"""Import the published eval-bench evidence into ``results/`` (PDK-04).

The sources are the sanitized raw files published beside the eval-bench
reports, one results document per source:

* ``REPORT-rfd-calibration-2026-10-06.raw.json``: review, fix and docs across
  the frontier and open-weight combos;
* ``REPORT-hrf-calibration-2026-10-07.raw.json``: hard-review and hard-fix;
* ``REPORT-ossharn-04-gym-matrix-2026-09-29.raw.json``: coding, four
  open-weight harnesses x six hosted routes (the open-weight breadth), plus the
  post-KIMIEMPTY-01 rerun;
* ``REPORT-omt-08-local-80b-gym-rows-2026-09-30.raw.json``: coding on a local
  model in the same four harnesses.

Every comparability boundary is kept: a row a report excluded keeps its
``excludedBecause`` tag, infrastructure rows are tagged ``infra:<verdict>``,
and every legacy row stays at task_version 1, the version it was graded at (the
seven tasks PDK-03 scrubbed are at version 2 only from then on). The sources
carry no token measurements or wall times, so those are null and every cost is
``unknown``. The frontier coding and rca aggregates in the eval-bench report
have no published per-attempt rows in these four sources, so they are not imported.
The per-attempt eval-bench-evidence/v2 importer preserves recorded task versions,
tokens and wall times; its source will be registered when the export is published.

    python3 scripts/import_eval_bench.py --reports DIR            # write results/
    python3 scripts/import_eval_bench.py --reports DIR --check    # fail if results/ differs
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import leaderboard

PRICE_TABLE = "2026-10-10"
RUNNER = "agent-os-fleet"
PROXY_PREFIX = "litellm-local-openai/"

#: Fleet worker class -> (harness CLI, how it reaches the model).
WORKER_CLASSES: dict[str, tuple[str, str]] = {
    "claude-code": ("claude-code", "vendor"),
    "codex": ("codex", "vendor"),
    "gemini": ("antigravity", "vendor"),
    "oss-claude-code": ("claude-code", "openai-compatible-proxy"),
    "oss-codex": ("codex", "openai-compatible-proxy"),
    "oss-opencode": ("opencode", "openai-compatible-proxy"),
    "oss-pi": ("pi", "openai-compatible-proxy"),
}

#: Fleet model label (proxy prefix stripped) -> (provider id, reasoning level).
MODELS: dict[str, tuple[str, str | None]] = {
    "claude-opus-5-5": ("anthropic/claude-opus-5-5", None),
    "claude-sonnet-5-5": ("anthropic/claude-sonnet-5-5", None),
    "claude-sonnet-5": ("anthropic/claude-sonnet-5", None),
    "claude-haiku-4-5": ("anthropic/claude-haiku-4-5", None),
    "gpt-6.1-sol": ("openai/gpt-6.1-sol", None),
    "gpt-5.6-luna": ("openai/gpt-5.6-luna", None),
    "gpt-5.6-sol": ("openai/gpt-5.6-sol", None),
    "gpt-5.6-terra": ("openai/gpt-5.6-terra", None),
    "gpt-6-astra": ("openai/gpt-6-astra", None),
    "gpt-6-luna": ("openai/gpt-6-luna", None),
    "gpt-6-sol": ("openai/gpt-6-sol", None),
    # Frontier models run in another vendor's harness through the proxy.
    "anthropic/claude-opus-5-5": ("anthropic/claude-opus-5-5", None),
    "anthropic/claude-sonnet-5-5": ("anthropic/claude-sonnet-5-5", None),
    "openai/gpt-6.1-sol": ("openai/gpt-6.1-sol", None),
    "agy:gemini-3.1-pro-high": ("google/gemini-3.1-pro", "high"),
    "agy:gemini-3.8-flash-high": ("google/gemini-3.8-flash", "high"),
    "agy:gemini-4-argon-high": ("google/gemini-4-argon", "high"),
    "google/gemini-3.8-flash": ("google/gemini-3.8-flash", None),
    "google/gemini-3.8-flash-high": ("google/gemini-3.8-flash", "high"),
    "google/gemini-3.8-flash-low": ("google/gemini-3.8-flash", "low"),
    "fireworks/qwen3.8-max": ("fireworks/qwen3.8-max", None),
    "fireworks/qwen3.8-27b": ("fireworks/qwen3.8-27b", None),
    "fireworks/kimi-k3": ("fireworks/kimi-k3", None),
    "kimi-k3": ("moonshot/kimi-k3", None),
    "glm-5.2": ("zai/glm-5.2", None),
    "baseten/glm-5.3-flash": ("baseten/glm-5.3-flash", None),
    "together/deepseek-v4.1-flash": ("together/deepseek-v4.1-flash", None),
    "together/qwen3.8-flash": ("together/qwen3.8-flash", None),
    "local/qwen3-coder-next-80b-a3b-6bit": (
        "local/qwen3-coder-next-80b-a3b-6bit",
        None,
    ),
}

#: The RFD export's tag for an infrastructure row, mapped to the schema's.
RFD_INFRA_PREFIX = "infra_or_quarantine:"

#: HRF rows the report excluded through its infrastructure list but the raw
#: export left unmarked. The report's per-task table shows no valid run for
#: these (its hard-review totals give this combo 7/7), so they keep that
#: exclusion here.
HRF_SOURCE_EXCLUDED = frozenset(
    {
        "lrq_89008b02-2e0a-40c6-9877-cd1f92e3e5b5",  # hard-review-clean-long
        "lrq_b2b9724f-c9cb-43c5-9619-accc3539a653",  # hard-review-decoy-decision-replies
        "lrq_2cda1415-da4a-49f7-b6c4-dc5e3df67ebb",  # hard-review-decoy-job-leases
        "lrq_26d9718a-2760-4308-9695-a8357b3a78ba",  # hard-review-multi-defect-outbox
        "lrq_3c0e0b03-fc17-4ecc-ac05-99aa742e686e",  # hard-review-multi-defect-tool-calls
    }
)
SOURCE_EXCLUDED_TAG = "infra:source_infra_list"


class SourceError(ValueError):
    """A source row cannot be mapped onto the schema."""


def descriptor(worker_class: str, **extra: str) -> dict[str, str]:
    try:
        harness, transport = WORKER_CLASSES[worker_class]
    except KeyError:
        raise SourceError(f"unknown worker class {worker_class!r}") from None
    return {
        "harness": harness,
        "model_transport": transport,
        "runner": RUNNER,
        "worker_class": worker_class,
        **extra,
    }


def model_of(label: str, reasoning_level: str | None = None) -> dict[str, str | None]:
    name = label[len(PROXY_PREFIX) :] if label.startswith(PROXY_PREFIX) else label
    name, separator, effort = name.partition("@")
    try:
        provider_id, reasoning = MODELS[name]
    except KeyError:
        raise SourceError(f"unknown model label {label!r}") from None
    if separator:
        if not effort:
            raise SourceError(f"model label {label!r} has an empty reasoning suffix")
        reasoning = effort
    if reasoning_level is not None and reasoning_level != reasoning:
        raise SourceError(
            f"model label {label!r} contradicts reasoningLevel {reasoning_level!r}"
        )
    return {"provider_id": provider_id, "reasoning": reasoning}


def excluded_because(verdict: str, tag: str | None) -> str | None:
    if tag and tag.startswith(RFD_INFRA_PREFIX):
        return "infra:" + tag[len(RFD_INFRA_PREFIX) :]
    if tag:
        return tag
    return f"infra:{verdict}" if verdict in leaderboard.INFRA_VERDICTS else None


class Builder:
    """Collects one document's records and the harness configs they use."""

    def __init__(self, table: leaderboard.PriceTable) -> None:
        self.table = table
        self.configs: dict[str, dict[str, str]] = {}
        self.records: list[dict[str, Any]] = []

    def add(
        self,
        *,
        run_id: str,
        suite: str,
        task: str,
        attempt: int | None,
        config: dict[str, str],
        model_label: str,
        verdict: str,
        score: float | None,
        excluded: str | None,
        task_version: int = 1,
        tokens: dict[str, int | None] | None = None,
        wall_seconds: float | None = None,
        reasoning_level: str | None = None,
    ) -> None:
        if not run_id:
            raise SourceError(f"{task}: a source row has no run id")
        key = leaderboard.config_hash(config)
        self.configs[key] = config
        model = model_of(model_label, reasoning_level)
        if tokens is None:
            tokens = {"input": None, "cache": None, "output": None}
        self.records.append(
            {
                "run_id": run_id,
                "suite": suite,
                "task": task,
                "task_version": task_version,
                "attempt": attempt,
                "harness": {
                    "name": config["harness"],
                    "version": None,
                    "config_hash": key,
                },
                "model": model,
                "verdict": verdict,
                "score": score,
                "tokens": tokens,
                "wall_seconds": wall_seconds,
                "paddock_commit": None,
                "excludedBecause": excluded_because(verdict, excluded),
                "cost": leaderboard.price_cost(
                    tokens, str(model["provider_id"]), self.table
                ),
            }
        )

    def document(
        self, *, id: str, date: str, description: str, evidence: str
    ) -> dict[str, Any]:
        return {
            "schema": leaderboard.SCHEMA,
            "id": id,
            "date": date,
            "source": {
                "kind": "import",
                "description": description,
                "evidence": evidence,
            },
            "price_table": self.table.date,
            "harness_configs": dict(sorted(self.configs.items())),
            "records": sorted(self.records, key=lambda r: r["run_id"]),
        }


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def unlaunched_id(source: str, row: dict[str, Any], *fields: str) -> str:
    """A stable run id for an infrastructure row that never got a launch request."""
    return ":".join(["unlaunched", source, *(str(row[name]) for name in fields)])


def import_rfd(path: Path, table: leaderboard.PriceTable) -> dict[str, Any]:
    builder = Builder(table)
    for row in _load(path)["rows"]:
        fields = ("suite", "task", "harness", "model", "phase", "attempt")
        builder.add(
            run_id=row["launchRequestId"] or unlaunched_id("rfd", row, *fields),
            suite=row["suite"],
            task=row["task"],
            attempt=row["attempt"],
            config=descriptor(row["harness"]),
            model_label=row["model"],
            verdict=row["verdict"],
            score=row["score"],
            excluded=row["excludedBecause"],
        )
    return builder.document(
        id="rfd-calibration",
        date="2026-10-06",
        description=(
            "RFD calibration: review, fix and docs suites across frontier and open-weight "
            "harness x model combinations, run on the Agent OS fleet at low priority. "
            "Comparability gates: docs after GYMDOCS-01, Gemini review after GYMAGY-02, "
            "fix-corrupt-refresh-checkpoint after RFDFIX-01."
        ),
        evidence=f"{path.name} (sanitized per-attempt rows; no submissions or hidden cases)",
    )


def import_hrf(path: Path, table: leaderboard.PriceTable) -> dict[str, Any]:
    builder = Builder(table)
    for row in _load(path)["rows"]:
        fields = ("category", "task", "cls", "model", "phase", "attempt")
        builder.add(
            run_id=row["lrq"] or unlaunched_id("hrf", row, *fields),
            suite=row["category"],
            task=row["task"],
            attempt=row["attempt"],
            config=descriptor(row["cls"]),
            model_label=row["model"],
            verdict=row["verdict"],
            score=row["score"],
            excluded=SOURCE_EXCLUDED_TAG if row["lrq"] in HRF_SOURCE_EXCLUDED else None,
        )
    return builder.document(
        id="hrf-calibration",
        date="2026-10-07",
        description=(
            "HRF calibration: hard-review and hard-fix suites on five harness x model "
            "combinations (no Claude rows: provider quota was exhausted). Rows the report "
            "excluded through its infrastructure list carry infra:source_infra_list."
        ),
        evidence=f"{path.name} (sanitized per-attempt rows; no submissions or hidden cases)",
    )


def _matrix_rows(
    builder: Builder, runs: list[dict[str, Any]], suite: str, **extra: str
) -> None:
    for run in runs:
        builder.add(
            run_id=run["lrq_id"],
            suite=suite,
            task=run["task"],
            attempt=None,
            config=descriptor(run["worker_class"], **extra),
            model_label=run["route"],
            verdict=run["verdict"],
            score=None,
            excluded=None,
        )


def import_ossharn(path: Path, table: leaderboard.PriceTable) -> dict[str, Any]:
    raw = _load(path)
    builder = Builder(table)
    _matrix_rows(builder, raw["runs"], raw["suite"])
    for rerun in raw["reruns"]:
        # The rerun ran behind the KIMIEMPTY-01 proxy fix: a different harness
        # configuration, so it is never pooled with the original cell.
        _matrix_rows(builder, rerun["runs"], raw["suite"], proxy_fix="KIMIEMPTY-01")
    return builder.document(
        id="ossharn-04-coding-matrix",
        date="2026-09-29",
        description=(
            "Open-weight breadth: the coding suite (7 tasks, n=1 per cell) in four "
            "open-weight harness configurations x six hosted routes, plus the post-fix rerun "
            "of codex x moonshot/kimi-k3 under its own configuration hash. Verifiers stop at "
            "the first wrong field and record no partial-credit score."
        ),
        evidence=f"{path.name} (cell summaries and ledger-sourced per-run verdicts)",
    )


def import_omt(path: Path, table: leaderboard.PriceTable) -> dict[str, Any]:
    raw = _load(path)
    builder = Builder(table)
    _matrix_rows(builder, raw["runs"], raw["suite"])
    return builder.document(
        id="omt-08-local-coding",
        date="2026-09-30",
        description=(
            "Local model: the coding suite (12 tasks, n=1) on a self-hosted "
            "Qwen3-Coder-Next 80B (6-bit) in the four open-weight harness configurations, "
            "after the local backend's tool-call fixes (OMBPARSE-01)."
        ),
        evidence=f"{path.name} (per-run verdicts; the superseded first run is not included)",
    )


def import_eval_bench(
    path: Path, table: leaderboard.PriceTable, existing_run_ids: set[str]
) -> tuple[dict[str, Any], int, int]:
    """Return the document, already-published count and unrecorded-version count."""
    raw = _load(path)
    if raw["schema"] != "eval-bench-evidence/v2":
        raise SourceError("expected eval-bench-evidence/v2")
    builder = Builder(table)
    already_published = version_unrecorded = 0
    for row in raw["rows"]:
        if row["runId"] in existing_run_ids:
            already_published += 1
            continue
        if row["excludedBecause"] == "version-unrecorded":
            version_unrecorded += 1
            continue
        tokens = row["tokens"] or {}
        builder.add(
            run_id=row["runId"],
            suite=row["category"],
            task=row["task"],
            task_version=row["taskVersion"],
            attempt=row["attempt"],
            config=descriptor(row["cls"]),
            model_label=row["model"],
            reasoning_level=row["reasoningLevel"],
            verdict=row["verdict"],
            score=row["score"],
            tokens={
                "input": tokens.get("freshInput"),
                "cache": tokens.get("cacheRead"),
                "output": tokens.get("output"),
            },
            wall_seconds=row["wallSeconds"],
            excluded=row["excludedBecause"],
        )
    document = builder.document(
        id="eval-bench",
        date=raw["generatedAt"],
        description="Per-attempt eval-bench gradings with task versions, measured tokens and wall time.",
        evidence=f"{path.name} (sanitized per-attempt rows; no submissions or hidden cases)",
    )
    return document, already_published, version_unrecorded


# Add the per-attempt export to SOURCES when it is published.
SOURCES = (
    ("REPORT-rfd-calibration-2026-10-06.raw.json", import_rfd),
    ("REPORT-hrf-calibration-2026-10-07.raw.json", import_hrf),
    ("REPORT-ossharn-04-gym-matrix-2026-09-29.raw.json", import_ossharn),
    ("REPORT-omt-08-local-80b-gym-rows-2026-09-30.raw.json", import_omt),
)


def build(reports: Path, results: Path) -> dict[str, str]:
    """``{file name: text}`` for every imported document."""
    raw = json.loads(
        (results / "prices" / f"{PRICE_TABLE}.json").read_text(encoding="utf-8")
    )
    table = leaderboard.PriceTable(raw["date"], raw["models"], raw["note"])
    out = {}
    for name, importer in SOURCES:
        document = importer(reports / name, table)
        out[f"{document['date']}-{document['id']}.json"] = leaderboard.render_document(
            document
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--reports", type=Path, required=True, help="the reports directory"
    )
    parser.add_argument("--results", type=Path, default=leaderboard.DEFAULT_RESULTS)
    parser.add_argument("--check", action="store_true", help="fail if results/ differs")
    args = parser.parse_args(argv)
    files = build(args.reports, args.results)
    if args.check:
        stale = [
            name
            for name, text in files.items()
            if not (args.results / name).is_file()
            or (args.results / name).read_text(encoding="utf-8") != text
        ]
        if stale:
            print(
                "results/ differs from the import: " + ", ".join(stale), file=sys.stderr
            )
            return 1
        print("results/ matches the import")
        return 0
    for name, text in files.items():
        (args.results / name).write_text(text, encoding="utf-8")
    print("Imported: " + ", ".join(sorted(files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
