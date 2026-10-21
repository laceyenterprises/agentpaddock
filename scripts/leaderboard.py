"""Paddock leaderboard results: schema, validation, pricing and aggregation (PDK-04).

``results/`` is the leaderboard's only input. It holds:

* ``results/<date>-<id>.json``: one results document per submitted run bundle or
  import. Each record in it is one cell: one graded attempt of one task by one
  harness configuration and model.
* ``results/prices/<date>.json``: the committed, dated price tables. A record's
  cost is computed from its tokens at the table its document names, and every
  price carries a ``source``: ``provider-invoice-reconciled``, ``published-rate``
  or ``unknown``.
* ``results/meta/changelog.json``: the grading changelog. Each entry states its
  comparability boundary; an entry that voids earlier rows names the
  ``excludedBecause`` tag those rows carry.
* ``results/meta/fairness.json``: the harness-configuration fairness checklist
  and the per-harness audit status.

Nothing here is zero-filled: an unmeasured token count is null, and a cost
without a price source is ``unknown``, never a number.

    python3 scripts/leaderboard.py validate [--results DIR]
    python3 scripts/leaderboard.py from-run RUN.json --id ID --date DATE ...

Stdlib only, so it runs with nothing installed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

MODULE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = MODULE_ROOT / "results"

SCHEMA = "paddock-results/v1"
PRICE_SCHEMA = "paddock-price-table/v1"
CHANGELOG_SCHEMA = "paddock-changelog/v1"
FAIRNESS_SCHEMA = "paddock-fairness/v1"

UNKNOWN = "unknown"
RECONCILED = "provider-invoice-reconciled"
PUBLISHED = "published-rate"
PRICED_SOURCES = (RECONCILED, PUBLISHED)
PRICE_SOURCES = (*PRICED_SOURCES, UNKNOWN)

#: Verdicts a verifier graded. Only ``passed`` is a pass; the rest count in n.
GRADED_VERDICTS = frozenset({"passed", "failed", "format_failed", "no_submission"})
#: Infrastructure failures and quarantines: never scored, always excluded.
INFRA_VERDICTS = frozenset(
    {"agent_error", "error", "integrity_hold", "skipped_no_taint_gate", "xfail_shadow"}
)
VERDICTS = GRADED_VERDICTS | INFRA_VERDICTS
PASSED = "passed"

SOURCE_KINDS = ("import", "run")
CHANGE_KINDS = ("grading", "harness", "task", "infrastructure", "pricing")
AUDIT_STATUSES = ("pending", "audited", "passed", "failed")
CHECK_STATUSES = ("pass", "fail", "not-verifiable")

RESULT_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})-([a-z0-9][a-z0-9.-]*)\.json$")
PRICE_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CONFIG_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
PROVIDER_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._:-]*$")
INFRA_TAG = re.compile(r"^infra:[a-z0-9_]+$")

DOCUMENT_KEYS = {
    "schema",
    "id",
    "date",
    "source",
    "price_table",
    "harness_configs",
    "records",
}
SOURCE_KEYS = {"kind", "description", "evidence"}
RECORD_KEYS = {
    "run_id",
    "suite",
    "task",
    "task_version",
    "attempt",
    "harness",
    "model",
    "verdict",
    "score",
    "tokens",
    "wall_seconds",
    "paddock_commit",
    "excludedBecause",
    "cost",
}
HARNESS_KEYS = {"name", "version", "config_hash"}
MODEL_KEYS = {"provider_id", "reasoning"}
TOKEN_KEYS = ("input", "cache", "output")
COST_KEYS = {"usd", "source", "reason"}
PRICE_KEYS = {"schema", "date", "currency", "unit", "note", "models"}
PRICE_ENTRY_KEYS = {"input", "cache", "output", "source", "as_of", "reference", "note"}
CHANGE_KEYS = {"id", "date", "kind", "summary", "boundary", "excludes"}
FAIRNESS_KEYS = {"schema", "checklist", "audits"}
AUDIT_KEYS = {"harness", "status", "date", "notes"}

Z_95 = 1.959964


class ResultsError(ValueError):
    """``results/`` does not validate; the message lists every problem."""


# ---------------------------------------------------------------- helpers


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def config_hash(descriptor: Mapping[str, Any]) -> str:
    """The ``sha256:`` hash of a harness configuration descriptor's canonical JSON."""
    return "sha256:" + hashlib.sha256(canonical_json(descriptor).encode("utf-8")).hexdigest()


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _keys(where: str, value: Any, allowed: set[str], errors: list[str]) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{where}: must be an object")
        return False
    missing = sorted(allowed - value.keys())
    extra = sorted(value.keys() - allowed)
    if missing:
        errors.append(f"{where}: missing {', '.join(missing)}")
    if extra:
        errors.append(f"{where}: unknown {', '.join(extra)}")
    return not missing and not extra


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def wilson(passed: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """The Wilson score interval for ``passed`` of ``n``, as fractions."""
    if n <= 0:
        return 0.0, 0.0
    p = passed / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


# ---------------------------------------------------------------- price tables


@dataclass(frozen=True)
class PriceTable:
    date: str
    models: dict[str, dict[str, Any]]
    note: str


def validate_price_table(name: str, raw: Any) -> list[str]:
    errors: list[str] = []
    where = f"prices/{name}"
    if not _keys(where, raw, PRICE_KEYS, errors):
        return errors
    match = PRICE_NAME.match(name)
    if raw["schema"] != PRICE_SCHEMA:
        errors.append(f"{where}: schema must be {PRICE_SCHEMA}")
    if not match or raw["date"] != match.group(1):
        errors.append(f"{where}: file name must be <date>.json matching date {raw['date']!r}")
    if raw["currency"] != "USD" or raw["unit"] != "usd_per_million_tokens":
        errors.append(f"{where}: prices are USD per million tokens")
    if not isinstance(raw["models"], dict):
        errors.append(f"{where}: models must be an object")
        return errors
    for model, entry in sorted(raw["models"].items()):
        at = f"{where}: {model}"
        if not PROVIDER_ID.match(model):
            errors.append(f"{at}: model must be a provider id such as provider/model")
        if not _keys(at, entry, PRICE_ENTRY_KEYS, errors):
            continue
        if entry["source"] not in PRICE_SOURCES:
            errors.append(f"{at}: source must be one of {', '.join(PRICE_SOURCES)}")
        rates = [entry[key] for key in TOKEN_KEYS]
        if entry["source"] == UNKNOWN:
            if any(rate is not None for rate in rates):
                errors.append(f"{at}: an unknown source carries no rate")
        elif not all(_is_number(rate) and rate >= 0 for rate in rates):
            errors.append(f"{at}: a {entry['source']} price needs input, cache and output rates")
        if not (isinstance(entry["as_of"], str) and DATE.match(entry["as_of"])):
            errors.append(f"{at}: as_of must be an ISO date")
        elif entry["as_of"] > raw["date"]:
            errors.append(f"{at}: as_of is after the table's date")
        if not _text(entry["reference"]):
            errors.append(f"{at}: every price names its reference")
    return errors


def price_cost(
    tokens: Mapping[str, Any], provider_id: str, table: PriceTable | None
) -> dict[str, Any]:
    """A record's cost at ``table``'s rates; ``unknown`` whenever any input is missing."""

    def unknown(reason: str) -> dict[str, Any]:
        return {"usd": None, "source": UNKNOWN, "reason": reason}

    if table is None:
        return unknown("price_table_missing")
    counts = [tokens.get(key) for key in TOKEN_KEYS]
    if not all(_is_count(count) for count in counts):
        return unknown("tokens_not_measured")
    entry = table.models.get(provider_id)
    if entry is None:
        return unknown("model_not_in_price_table")
    if entry["source"] not in PRICED_SOURCES:
        return unknown("price_source_unknown")
    usd = sum(count * entry[key] for count, key in zip(counts, TOKEN_KEYS)) / 1_000_000
    return {"usd": round(usd, 6), "source": entry["source"], "reason": None}


def format_cost(cost: Mapping[str, Any] | None) -> str:
    """A cost for display: a dollar figure only when it carries a priced source."""
    if not isinstance(cost, Mapping):
        return UNKNOWN
    usd = cost.get("usd")
    if cost.get("source") not in PRICED_SOURCES or not _is_number(usd):
        return UNKNOWN
    return f"${float(usd):,.2f}" if usd >= 0.01 else f"${float(usd):.4f}"


# ---------------------------------------------------------------- changelog / fairness


def validate_changelog(raw: Any) -> list[str]:
    errors: list[str] = []
    where = "meta/changelog.json"
    if not _keys(where, raw, {"schema", "entries"}, errors):
        return errors
    if raw["schema"] != CHANGELOG_SCHEMA:
        errors.append(f"{where}: schema must be {CHANGELOG_SCHEMA}")
    if not isinstance(raw["entries"], list) or not raw["entries"]:
        errors.append(f"{where}: entries must be a non-empty list")
        return errors
    seen: set[str] = set()
    for index, entry in enumerate(raw["entries"]):
        at = f"{where}: entries[{index}]"
        if not _keys(at, entry, CHANGE_KEYS, errors):
            continue
        if not _text(entry["id"]) or entry["id"] in seen:
            errors.append(f"{at}: id must be a unique non-empty string")
        if _text(entry["id"]):
            seen.add(entry["id"])
        if not (isinstance(entry["date"], str) and DATE.match(entry["date"])):
            errors.append(f"{at}: date must be an ISO date")
        if entry["kind"] not in CHANGE_KINDS:
            errors.append(f"{at}: kind must be one of {', '.join(CHANGE_KINDS)}")
        for key in ("summary", "boundary"):
            if not _text(entry[key]):
                errors.append(f"{at}: {key} is required")
        excludes = entry["excludes"]
        if excludes is not None and excludes != f"pre-{entry['id']}":
            errors.append(f"{at}: excludes must be null or 'pre-{entry['id']}'")
    return errors


def fairness_repository_reference(source: str) -> tuple[str, str] | None:
    """Parse a normalized repository path and positive line, with optional context."""
    match = re.fullmatch(
        r"((?:results|lib|batteries|locks|scripts)/[\w./-]+):([1-9]\d*)(?: [^\r\n]+)?",
        source,
    )
    if match and all(part not in {"", ".", ".."} for part in match[1].split("/")):
        return match[1], match[2]
    return None


def valid_fairness_source(source: str) -> bool:
    """Accept HTTPS docs, repository lines, and known telemetry selector roots."""
    if source.startswith("https://"):
        try:
            url = urlsplit(source)
            return bool(url.hostname) and not any(c.isspace() for c in source)
        except ValueError:
            return False
    return fairness_repository_reference(source) is not None or bool(
        re.fullmatch(
            r"(?:run\.[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*"
            r"|(?:records|harness_configs)\[[^\r\n]+\](?:\.[A-Za-z_]\w*)*)",
            source,
        )
    )


def validate_fairness(raw: Any) -> list[str]:
    errors: list[str] = []
    where = "meta/fairness.json"
    if not _keys(where, raw, FAIRNESS_KEYS, errors):
        return errors
    if raw["schema"] != FAIRNESS_SCHEMA:
        errors.append(f"{where}: schema must be {FAIRNESS_SCHEMA}")
    checklist = raw["checklist"]
    check_ids: set[str] = set()
    if not isinstance(checklist, list) or not checklist:
        errors.append(f"{where}: checklist must be a non-empty list")
    else:
        for index, item in enumerate(checklist):
            if _keys(f"{where}: checklist[{index}]", item, {"id", "check"}, errors):
                if not (_text(item["id"]) and _text(item["check"])):
                    errors.append(f"{where}: checklist[{index}] needs an id and a check")
                elif item["id"] in check_ids:
                    errors.append(f"{where}: checklist ids must be unique")
                else:
                    check_ids.add(item["id"])
    audits = raw["audits"]
    if not isinstance(audits, list):
        errors.append(f"{where}: audits must be a list")
        return errors
    harnesses = set()
    for index, audit in enumerate(audits):
        at = f"{where}: audits[{index}]"
        # Legacy pending entries may omit the additive evidence and sign-off fields.
        optional = {"evidence", "sign_off"} & audit.keys() if isinstance(audit, dict) else set()
        if not _keys(at, audit, AUDIT_KEYS | optional, errors):
            continue
        if not _text(audit["harness"]):
            errors.append(f"{at}: harness must be a non-empty string")
        else:
            if audit["harness"] in harnesses:
                errors.append(f"{at}: one audit per harness")
            harnesses.add(audit["harness"])
        if audit["status"] not in AUDIT_STATUSES:
            errors.append(f"{at}: status must be one of {', '.join(AUDIT_STATUSES)}")
        if audit["status"] != "pending" and not (
            isinstance(audit["date"], str) and DATE.match(audit["date"])
        ):
            errors.append(f"{at}: a completed audit is dated")
        elif audit["date"] is not None and not (
            isinstance(audit["date"], str) and DATE.match(audit["date"])
        ):
            errors.append(f"{at}: date must be an ISO date or null")
        if not _text(audit["notes"]):
            errors.append(f"{at}: notes must explain the audit status")
        sign_off = audit.get("sign_off")
        if sign_off is not None and not _text(sign_off):
            errors.append(f"{at}: sign_off must be non-empty text or null")
        if sign_off is not None and audit["status"] != "passed":
            errors.append(f"{at}: only a passed audit carries maintainer sign-off")
        evidence = audit.get("evidence")
        legacy = not ({"evidence", "sign_off"} & audit.keys())
        if "evidence" not in audit and audit["status"] == "pending" and legacy:
            continue
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{at}: evidence must cover every checklist item")
            continue
        seen_checks: set[str] = set()
        all_pass = True
        for number, item in enumerate(evidence):
            check_at = f"{at}: evidence[{number}]"
            if not _keys(check_at, item, {"id", "status", "notes", "sources"}, errors):
                all_pass = False
                continue
            check_id = item["id"]
            if not _text(check_id) or check_id not in check_ids:
                errors.append(f"{check_at}: id must name a checklist item")
            elif check_id in seen_checks:
                errors.append(f"{check_at}: duplicate checklist evidence")
            else:
                seen_checks.add(check_id)
            if item["status"] not in CHECK_STATUSES:
                errors.append(f"{check_at}: status must be one of {', '.join(CHECK_STATUSES)}")
            all_pass = all_pass and item["status"] == "pass"
            if not _text(item["notes"]):
                errors.append(f"{check_at}: notes must explain the finding")
            sources = item["sources"]
            if not isinstance(sources, list) or not sources or not all(_text(s) for s in sources):
                errors.append(f"{check_at}: sources must contain evidence references")
            elif not all(valid_fairness_source(s) for s in sources):
                errors.append(
                    f"{check_at}: sources must use HTTPS URLs, repository path:line "
                    "references, or known telemetry selectors"
                )
        if seen_checks != check_ids:
            errors.append(f"{at}: evidence must cover every checklist item")
        if audit["status"] in {"audited", "passed"} and not all_pass:
            errors.append(f"{at}: completed audit requires every check to pass")
        if audit["status"] == "passed" and not _text(sign_off):
            errors.append(f"{at}: passed evidence requires maintainer sign_off")
    return errors


# ---------------------------------------------------------------- results documents


def validate_document(
    name: str,
    raw: Any,
    *,
    price_tables: Mapping[str, PriceTable],
    exclusion_tags: Iterable[str],
) -> list[str]:
    """Every schema problem in one results document; empty when it is valid."""
    errors: list[str] = []
    if not _keys(name, raw, DOCUMENT_KEYS, errors):
        return errors
    match = RESULT_NAME.match(name)
    if raw["schema"] != SCHEMA:
        errors.append(f"{name}: schema must be {SCHEMA}")
    if not match or (raw["date"], raw["id"]) != match.groups():
        errors.append(f"{name}: file name must be <date>-<id>.json from its date and id")
    source = raw["source"]
    if _keys(f"{name}: source", source, SOURCE_KEYS, errors):
        if source["kind"] not in SOURCE_KINDS:
            errors.append(f"{name}: source.kind must be one of {', '.join(SOURCE_KINDS)}")
        if not (_text(source["description"]) and _text(source["evidence"])):
            errors.append(f"{name}: source needs a description and its evidence")
    table = price_tables.get(raw["price_table"]) if isinstance(raw["price_table"], str) else None
    if table is None:
        errors.append(f"{name}: price_table {raw['price_table']!r} is not in results/prices/")
    configs = raw["harness_configs"]
    if not isinstance(configs, dict):
        errors.append(f"{name}: harness_configs must be an object")
        configs = {}
    for key, descriptor in sorted(configs.items()):
        if not isinstance(descriptor, dict) or config_hash(descriptor) != key:
            errors.append(f"{name}: harness_configs[{key}] does not hash to its key")
    records = raw["records"]
    if not isinstance(records, list) or not records:
        errors.append(f"{name}: records must be a non-empty list")
        return errors
    tags = set(exclusion_tags)
    used: set[str] = set()
    import_kind = isinstance(source, dict) and source.get("kind") == "import"
    for index, record in enumerate(records):
        at = f"{name}: records[{index}]"
        _validate_record(at, record, configs, table, tags, import_kind, errors)
        if isinstance(record, dict) and isinstance(record.get("harness"), dict):
            key = record["harness"].get("config_hash")
            if isinstance(key, str):
                used.add(key)
    for key in sorted(set(configs) - used):
        errors.append(f"{name}: harness_configs[{key}] is not used by any record")
    return errors


def _validate_record(
    at: str,
    record: Any,
    configs: Mapping[str, Any],
    table: PriceTable | None,
    tags: set[str],
    import_kind: bool,
    errors: list[str],
) -> None:
    if not _keys(at, record, RECORD_KEYS, errors):
        return
    for key in ("run_id", "suite", "task"):
        if not _text(record[key]):
            errors.append(f"{at}: {key} is required")
    version = record["task_version"]
    if not (_is_count(version) and version >= 1):
        errors.append(f"{at}: task_version must be a positive integer")
    if record["attempt"] is not None and not (_is_count(record["attempt"]) and record["attempt"]):
        errors.append(f"{at}: attempt must be a positive integer or null")
    harness = record["harness"]
    if _keys(f"{at}: harness", harness, HARNESS_KEYS, errors):
        if not _text(harness["name"]):
            errors.append(f"{at}: harness.name is required")
        if harness["version"] is not None and not _text(harness["version"]):
            errors.append(f"{at}: harness.version must be a string or null")
        if not (
            isinstance(harness["config_hash"], str) and CONFIG_HASH.match(harness["config_hash"])
        ):
            errors.append(f"{at}: harness.config_hash must be sha256:<64 hex>")
        elif harness["config_hash"] not in configs:
            errors.append(f"{at}: harness.config_hash has no descriptor in harness_configs")
    model = record["model"]
    provider_id = None
    if _keys(f"{at}: model", model, MODEL_KEYS, errors):
        provider_id = model["provider_id"]
        if not (isinstance(provider_id, str) and PROVIDER_ID.match(provider_id)):
            errors.append(f"{at}: model.provider_id must look like provider/model")
        if model["reasoning"] is not None and not _text(model["reasoning"]):
            errors.append(f"{at}: model.reasoning must be a string or null")
    verdict = record["verdict"]
    valid_verdict = isinstance(verdict, str) and verdict in VERDICTS
    if not valid_verdict:
        errors.append(f"{at}: verdict {verdict!r} is not a gym outcome")
    score = record["score"]
    if score is not None and not (_is_number(score) and 0 <= score <= 1):
        errors.append(f"{at}: score must be in [0, 1] or null")
    tokens = record["tokens"]
    if _keys(f"{at}: tokens", tokens, set(TOKEN_KEYS), errors):
        if any(tokens[key] is not None and not _is_count(tokens[key]) for key in TOKEN_KEYS):
            errors.append(f"{at}: token counts are non-negative integers or null, never guessed")
    wall = record["wall_seconds"]
    if wall is not None and not (_is_number(wall) and wall >= 0):
        errors.append(f"{at}: wall_seconds must be a non-negative number or null")
    commit = record["paddock_commit"]
    if commit is None:
        if not import_kind:
            errors.append(f"{at}: a run record names the paddock commit it ran at")
    elif not (isinstance(commit, str) and COMMIT.match(commit)):
        errors.append(f"{at}: paddock_commit must be a hex commit id")
    excluded = record["excludedBecause"]
    if (
        valid_verdict
        and verdict in INFRA_VERDICTS
        and not (isinstance(excluded, str) and INFRA_TAG.match(excluded))
    ):
        errors.append(f"{at}: an infrastructure row carries excludedBecause 'infra:<reason>'")
    elif excluded is not None and not (
        isinstance(excluded, str) and (INFRA_TAG.match(excluded) or excluded in tags)
    ):
        errors.append(
            f"{at}: excludedBecause {excluded!r} is neither infra:<reason> "
            "nor a changelog entry's tag"
        )
    cost = record["cost"]
    if (
        _keys(f"{at}: cost", cost, COST_KEYS, errors)
        and isinstance(tokens, dict)
        and isinstance(provider_id, str)
    ):
        if cost["source"] not in PRICE_SOURCES:
            errors.append(f"{at}: cost.source must be one of {', '.join(PRICE_SOURCES)}")
        elif cost["source"] == UNKNOWN and cost["usd"] is not None:
            errors.append(f"{at}: a cost with an unknown source has no dollar figure")
        expected = price_cost(tokens, provider_id, table)
        if table is not None and cost != expected:
            errors.append(
                f"{at}: cost {canonical_json(cost)} differs from the price table's "
                f"{canonical_json(expected)}"
            )


# ---------------------------------------------------------------- loading


@dataclass
class Results:
    documents: dict[str, dict[str, Any]]
    price_tables: dict[str, PriceTable]
    changelog: list[dict[str, Any]]
    fairness: dict[str, Any]
    records: list[dict[str, Any]] = field(default_factory=list)

    def configs(self) -> dict[str, dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for document in self.documents.values():
            merged.update(document["harness_configs"])
        return dict(sorted(merged.items()))


def _read_json(path: Path, errors: list[str], label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{label}: cannot read: {exc}")
        return None


def load_results(results_dir: Path = DEFAULT_RESULTS) -> Results:
    """Load and validate ``results_dir``; raise :class:`ResultsError` listing every problem."""
    errors: list[str] = []
    tables: dict[str, PriceTable] = {}
    for path in sorted((results_dir / "prices").glob("*.json")):
        raw = _read_json(path, errors, f"prices/{path.name}")
        problems = validate_price_table(path.name, raw) if raw is not None else []
        errors.extend(problems)
        if raw is not None and not problems:
            tables[raw["date"]] = PriceTable(raw["date"], raw["models"], raw["note"])
    if not tables:
        errors.append("results/prices/: no valid price table")
    changelog_raw = _read_json(
        results_dir / "meta" / "changelog.json", errors, "meta/changelog.json"
    )
    if changelog_raw is not None:
        errors.extend(validate_changelog(changelog_raw))
    changelog = changelog_raw.get("entries", []) if isinstance(changelog_raw, dict) else []
    if not isinstance(changelog, list):
        changelog = []
    tags = {
        entry["excludes"]
        for entry in changelog
        if isinstance(entry, dict) and _text(entry.get("excludes"))
    }
    fairness = _read_json(results_dir / "meta" / "fairness.json", errors, "meta/fairness.json")
    if fairness is not None:
        errors.extend(validate_fairness(fairness))
    documents: dict[str, dict[str, Any]] = {}
    run_ids: Counter[str] = Counter()
    for path in sorted(results_dir.glob("*.json")):
        raw = _read_json(path, errors, path.name)
        if raw is None:
            continue
        problems = validate_document(path.name, raw, price_tables=tables, exclusion_tags=tags)
        errors.extend(problems)
        if not problems:
            documents[path.name] = raw
            run_ids.update(record["run_id"] for record in raw["records"])
    if not documents and not errors:
        errors.append("results/: no results documents")
    errors.extend(
        f"run_id {run_id} appears in {count} records"
        for run_id, count in sorted(run_ids.items())
        if count > 1
    )
    if errors:
        raise ResultsError("\n".join(errors))
    records = [record for document in documents.values() for record in document["records"]]
    return Results(documents, tables, changelog, fairness or {}, records)


def write_document(path: Path, document: Mapping[str, Any]) -> None:
    path.write_text(render_document(document), encoding="utf-8")


def render_document(document: Mapping[str, Any]) -> str:
    """A results document as deterministic JSON text, one record per line."""
    head = {key: value for key, value in document.items() if key != "records"}
    text = json.dumps(head, indent=2, sort_keys=True, ensure_ascii=False)
    records = ",\n".join(
        "    " + json.dumps(record, sort_keys=True, ensure_ascii=False)
        for record in document["records"]
    )
    # Drop the head's closing "\n}" and append the records, one per line.
    return text[:-2] + ',\n  "records": [\n' + records + "\n  ]\n}\n"


# ---------------------------------------------------------------- aggregation


def included(record: Mapping[str, Any]) -> bool:
    """A record counts in n when no gate excludes it and a verifier graded it."""
    return record["excludedBecause"] is None and record["verdict"] in GRADED_VERDICTS


def row_key(record: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    harness, model = record["harness"], record["model"]
    return (
        harness["name"],
        harness["version"] or "",
        harness["config_hash"],
        model["provider_id"],
        model["reasoning"] or "",
    )


def _tokens_total(record: Mapping[str, Any]) -> int | None:
    counts = [record["tokens"][key] for key in TOKEN_KEYS]
    return sum(counts) if all(_is_count(count) for count in counts) else None


def summarize(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Pass rate, Wilson interval, mean score, tokens and cost per pass for ``records``."""
    graded = [record for record in records if included(record)]
    n = len(graded)
    passed = sum(record["verdict"] == PASSED for record in graded)
    low, high = wilson(passed, n)
    scores = [record["score"] for record in graded if record["score"] is not None]
    token_totals = [_tokens_total(record) for record in graded]
    measured = sum(total is not None for total in token_totals)
    tokens_per_pass = (
        sum(token_totals) / passed if passed and n and measured == n else None  # type: ignore[arg-type]
    )
    costs = [record["cost"] for record in graded]
    priced = all(cost["source"] in PRICED_SOURCES and _is_number(cost["usd"]) for cost in costs)
    if passed and n and priced:
        sources = {cost["source"] for cost in costs}
        cost_per_pass = {
            "usd": round(sum(cost["usd"] for cost in costs) / passed, 6),
            "source": RECONCILED if sources == {RECONCILED} else PUBLISHED,
            "reason": None,
        }
    else:
        cost_per_pass = {
            "usd": None,
            "source": UNKNOWN,
            "reason": "no_passes" if n and not passed else "not_every_attempt_priced",
        }
    excluded = Counter(
        record["excludedBecause"] or f"infra:{record['verdict']}"
        for record in records
        if not included(record)
    )
    return {
        "passed": passed,
        "n": n,
        "rate": passed / n if n else None,
        "ci": (low, high),
        "mean_score": sum(scores) / len(scores) if scores else None,
        "tasks": len({record["task"] for record in graded}),
        "tokens_measured": measured,
        "tokens_per_pass": tokens_per_pass,
        "cost_per_pass": cost_per_pass,
        "excluded": dict(sorted(excluded.items())),
    }


def boards(records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Per-suite boards. Results compare only within one task version, so a suite's
    records at different task versions form separate boards."""
    grouped: dict[tuple[str, int], list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[(record["suite"], record["task_version"])].append(record)
    versions_per_suite = Counter(suite for suite, _ in grouped)
    out = []
    for (suite, version), members in sorted(grouped.items()):
        rows: dict[tuple, list[Mapping[str, Any]]] = defaultdict(list)
        for record in members:
            rows[row_key(record)].append(record)
        board_rows = []
        for key, row_records in rows.items():
            summary = summarize(row_records)
            board_rows.append({"key": key, **summary})
        board_rows.sort(
            key=lambda row: (
                -row["rate"] if row["rate"] is not None else 1.0,
                -row["ci"][0],
                -row["n"],
                row["key"],
            )
        )
        out.append(
            {
                "suite": suite,
                "task_version": version,
                "split": versions_per_suite[suite] > 1,
                "tasks": sorted({record["task"] for record in members}),
                "rows": board_rows,
            }
        )
    return out


def matrix(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Harness configuration x model: per-suite pass counts in each cell."""
    cells: dict[tuple, dict[tuple[str, int], list[Mapping[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    harnesses: set[tuple[str, str, str]] = set()
    models: set[tuple[str, str]] = set()
    for record in records:
        key = row_key(record)
        harness, model = key[:3], key[3:]
        harnesses.add(harness)
        models.add(model)
        cells[(harness, model)][(record["suite"], record["task_version"])].append(record)
    table = {
        cell: {suite: summarize(members) for suite, members in sorted(by_suite.items())}
        for cell, by_suite in cells.items()
    }
    return {"harnesses": sorted(harnesses), "models": sorted(models), "cells": table}


def discrimination(records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Per task and version: which combos pass it, and whether it separates them.

    A task discriminates when at least one included attempt passed and at least
    one did not, the rule the calibration reports use.
    """
    grouped: dict[tuple[str, str, int], dict[tuple, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for record in records:
        if included(record):
            task = (record["suite"], record["task"], record["task_version"])
            grouped[task][row_key(record)].append(record["verdict"])
    out = []
    for (suite, task, version), combos in sorted(grouped.items()):
        verdicts = [verdict for runs in combos.values() for verdict in runs]
        passed = sum(verdict == PASSED for verdict in verdicts)
        out.append(
            {
                "suite": suite,
                "task": task,
                "task_version": version,
                "passed": passed,
                "n": len(verdicts),
                "combos": len(combos),
                "combos_passing": sum(PASSED in runs for runs in combos.values()),
                "discriminates": 0 < passed < len(verdicts),
                "runs": {
                    key: "".join("P" if verdict == PASSED else "F" for verdict in runs)
                    for key, runs in sorted(combos.items())
                },
            }
        )
    return out


def exclusion_counts(records: list[Mapping[str, Any]]) -> Counter[str]:
    return Counter(
        record["excludedBecause"] for record in records if record["excludedBecause"] is not None
    )


# ---------------------------------------------------------------- run bundles


def records_from_run(
    summary: Mapping[str, Any],
    *,
    bundle_id: str,
    harness: Mapping[str, Any],
    model: Mapping[str, Any],
    paddock_commit: str,
    table: PriceTable | None,
    worker_class: str | None = None,
) -> list[dict[str, Any]]:
    """Convert a single-suite summary, preserving attribution exclusions.

    Only verified cache-inclusive sources on known harnesses with single-model
    usage become disjoint, priceable tokens. Unsupported or unattributable
    aggregates stay null; standalone provenance can verify identity without
    token measurements. Fleet descriptors may name a worker_class distinct
    from their harness CLI name.
    """
    # Load the checkout package directly, as standalone repo scripts do, without
    # changing the interpreter's search path or requiring a package install.
    if "gym" not in sys.modules:
        package_dir = MODULE_ROOT / "lib" / "python" / "gym"
        spec = importlib.util.spec_from_file_location(
            "gym", package_dir / "__init__.py", submodule_search_locations=[str(package_dir)]
        )
        if spec is None or spec.loader is None:
            raise ResultsError("cannot load the gym telemetry contracts")
        package = importlib.util.module_from_spec(spec)
        sys.modules["gym"] = package
        try:
            spec.loader.exec_module(package)
        except Exception:
            sys.modules.pop("gym", None)
            raise
    from gym.cost import PRICEABLE_TOKEN_SOURCES, REASONING_INSIDE_OUTPUT
    from gym.metrics import model_matches

    suite = summary["suite"]
    if not _text(suite) or suite == "all":
        raise ResultsError("from-run requires a single suite; mixed-suite 'all' is unsupported")
    requested_model = model["provider_id"]
    requested_class = worker_class or harness["name"]

    def matches(observed: str) -> bool:
        # Native OpenAI telemetry uses bare IDs; other provider routes stay distinct.
        return model_matches(observed, requested_model) or (
            requested_model.startswith("openai/")
            and model_matches(observed, requested_model.removeprefix("openai/"))
        )

    records = []
    for index, cell in enumerate(summary["cells"]):
        metrics = cell.get("metrics") or {}
        provenance = cell.get("execution_provenance") or {}
        observed = metrics.get("observed_models") or {}
        if not isinstance(observed, dict) or any(not _is_count(n) for n in observed.values()):
            raise ResultsError(
                f"cells[{index}]: observed_models must map model IDs to token counts"
            )
        mismatch = bool(cell.get("model_mismatch"))
        model_verified = False
        harness_verified = False
        for identity in (metrics.get("majority_model"), provenance.get("model")):
            if _text(identity):
                model_verified = True
                mismatch |= not matches(identity)
        if observed:
            total = sum(observed.values())
            if total > 0:
                model_verified = True
                requested = sum(n for identity, n in observed.items() if matches(identity))
                mismatch |= requested * 2 <= total
        for identity, expected in (
            (metrics.get("worker_class"), requested_class),
            (provenance.get("harness"), harness["name"]),
        ):
            if _text(identity):
                harness_verified = True
                mismatch |= identity != expected
        reasoning_mismatch = bool(cell.get("level_mismatch")) or any(
            _text(level) and level != model["reasoning"]
            for level in (
                cell.get("reasoning_level"),
                cell.get("resolved_reasoning_level"),
                metrics.get("resolved_reasoning_level"),
            )
        )
        verdict = cell["verdict"]
        if not isinstance(verdict, str) or verdict not in VERDICTS:
            raise ResultsError(f"cells[{index}]: verdict is not a gym outcome")
        if mismatch:
            excluded = "infra:model_mismatch"
        elif reasoning_mismatch:
            excluded = "infra:reasoning_mismatch"
        elif verdict in INFRA_VERDICTS:
            excluded = f"infra:{verdict}"
        elif not (model_verified and harness_verified):
            excluded = "infra:attribution_unverified"
        else:
            excluded = None
        total_input = metrics.get("token_usage_input")
        cache = metrics.get("token_usage_cache_read")
        output = metrics.get("token_usage_output")
        sources = metrics.get("token_usage_sources")
        if (
            excluded is None
            and isinstance(sources, list)
            and sources
            and all(
                isinstance(source, str) and source in PRICEABLE_TOKEN_SOURCES for source in sources
            )
            and REASONING_INSIDE_OUTPUT.get(metrics.get("worker_class") or requested_class) is True
            and observed
            and all(matches(identity) for identity in observed)
            and _is_count(total_input)
            and _is_count(cache)
            and _is_count(output)
            and cache <= total_input
        ):
            tokens = {"input": total_input - cache, "cache": cache, "output": output}
        else:
            tokens = {"input": None, "cache": None, "output": None}
        wall = metrics.get("wall_seconds")
        run_id = cell.get("launchRequestId") or f"{bundle_id}:{suite}:{cell['task']}:{index}"
        records.append(
            {
                "run_id": run_id,
                "suite": suite,
                "task": cell["task"],
                "task_version": cell.get("task_version") or 1,
                "attempt": cell.get("attempt"),
                "harness": dict(harness),
                "model": dict(model),
                "verdict": verdict,
                "score": cell.get("score"),
                "tokens": tokens,
                "wall_seconds": wall if _is_number(wall) else None,
                "paddock_commit": paddock_commit,
                "excludedBecause": excluded,
                "cost": price_cost(tokens, model["provider_id"], table),
            }
        )
    return records


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        results = load_results(args.results)
    except ResultsError as exc:
        print(f"results/ does not validate:\n{exc}", file=sys.stderr)
        return 1
    print(
        f"results/ is valid: {len(results.documents)} documents, {len(results.records)} records, "
        f"{len(results.price_tables)} price tables"
    )
    return 0


def _cmd_from_run(args: argparse.Namespace) -> int:
    summary = json.loads(args.run.read_text(encoding="utf-8"))
    descriptor = json.loads(args.harness_config.read_text(encoding="utf-8"))
    if not isinstance(descriptor, dict) or descriptor.get("reasoning_level") != args.reasoning:
        raise ResultsError(
            "harness descriptor reasoning_level must match --reasoning (null for default)"
        )
    key = config_hash(descriptor)
    tables = {}
    for path in sorted((args.results / "prices").glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        tables[raw["date"]] = PriceTable(raw["date"], raw["models"], raw["note"])
    table_date = args.price_table or max(tables, default=None)
    records = records_from_run(
        summary,
        bundle_id=args.id,
        harness={"name": args.harness, "version": args.harness_version, "config_hash": key},
        model={"provider_id": args.model, "reasoning": args.reasoning},
        worker_class=descriptor.get("worker_class"),
        paddock_commit=args.paddock_commit,
        table=tables.get(table_date) if table_date else None,
    )
    document = {
        "schema": SCHEMA,
        "id": args.id,
        "date": args.date,
        "source": {"kind": "run", "description": args.description, "evidence": args.evidence},
        "price_table": table_date,
        "harness_configs": {key: descriptor},
        "records": records,
    }
    target = args.results / f"{args.date}-{args.id}.json"
    write_document(target, document)
    print(f"Wrote {target} ({len(records)} records)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="validate results/")
    run = sub.add_parser("from-run", help="write a results document from a run's --json summary")
    run.add_argument("run", type=Path, help="the paddock run --json output")
    run.add_argument("--id", required=True)
    run.add_argument("--date", required=True)
    run.add_argument("--harness", required=True, help="harness CLI name, e.g. claude-code")
    run.add_argument("--harness-version", required=True, help="the CLI's --version output")
    run.add_argument(
        "--harness-config", type=Path, required=True, help="JSON descriptor of the configuration"
    )
    run.add_argument("--model", required=True, help="provider id, e.g. anthropic/claude-opus-5-5")
    run.add_argument("--reasoning", default=None)
    run.add_argument("--paddock-commit", required=True)
    run.add_argument("--price-table", default=None, help="price table date (default: newest)")
    run.add_argument("--description", required=True)
    run.add_argument("--evidence", required=True, help="the reproduction command")
    args = parser.parse_args(argv)
    try:
        return _cmd_validate(args) if args.command == "validate" else _cmd_from_run(args)
    except ResultsError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
