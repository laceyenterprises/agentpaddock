"""PDK-04: the leaderboard results schema, pricing and aggregation (scripts/leaderboard.py)."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import leaderboard as lb
import pytest

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "leaderboard"
FIXTURE_DOC = "2026-01-03-fixture-run.json"


@pytest.fixture
def results(tmp_path: Path) -> Path:
    target = tmp_path / "results"
    shutil.copytree(FIXTURE, target)
    return target


def _edit(results: Path, mutate) -> None:
    path = results / FIXTURE_DOC
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    lb.write_document(path, document)


def _errors(results: Path) -> str:
    with pytest.raises(lb.ResultsError) as caught:
        lb.load_results(results)
    return str(caught.value)


def test_committed_results_validate():
    loaded = lb.load_results()
    assert loaded.documents and loaded.records
    assert all(record["cost"]["source"] in lb.PRICE_SOURCES for record in loaded.records)


def test_fixture_results_validate():
    loaded = lb.load_results(FIXTURE)
    assert len(loaded.records) == 14
    assert set(loaded.price_tables) == {"2026-01-02"}


def _evidenced_fairness():
    raw = json.loads((FIXTURE / "meta" / "fairness.json").read_text(encoding="utf-8"))
    raw["audits"] = [raw["audits"][0]]
    raw["audits"][0].update(
        status="audited",
        sign_off=None,
        evidence=[
            {
                "id": "tools",
                "status": "pass",
                "notes": "Native tools match the documented default.",
                "sources": ["https://vendor.example/defaults", "run.tools"],
            }
        ],
    )
    return raw


def test_fairness_review_and_maintainer_sign_off_are_separate():
    raw = _evidenced_fairness()
    assert lb.validate_fairness(raw) == []
    audit = raw["audits"][0]
    audit.update(status="passed", sign_off="Fixture maintainer sign-off")
    assert lb.validate_fairness(raw) == []
    audit.update(status="pending", sign_off=None)
    audit["evidence"][0]["status"] = "not-verifiable"
    assert lb.validate_fairness(raw) == []


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda a: a.pop("evidence"), "evidence must cover every checklist item"),
        (lambda a: a.update(evidence=None), "evidence must cover every checklist item"),
        (lambda a: a.update(evidence=[]), "evidence must cover every checklist item"),
        (
            lambda a: a["evidence"].append(dict(a["evidence"][0])),
            "duplicate checklist evidence",
        ),
        (
            lambda a: a["evidence"][0].update(id="unknown"),
            "id must name a checklist item",
        ),
        (lambda a: a["evidence"][0].update(status="unknown"), "status must be one of"),
        (
            lambda a: a["evidence"][0].update(status="fail"),
            "requires every check to pass",
        ),
        (
            lambda a: a["evidence"][0].update(status="not-verifiable"),
            "requires every check to pass",
        ),
        (
            lambda a: a["evidence"][0].update(sources=[]),
            "sources must contain evidence",
        ),
        (
            lambda a: a["evidence"][0].update(sources=[""]),
            "sources must contain evidence",
        ),
        (lambda a: a["evidence"][0].update(notes=""), "notes must explain the finding"),
        (lambda a: a["evidence"][0].pop("sources"), "missing sources"),
        (lambda a: a.update(sign_off="Fixture sign-off"), "only a passed audit"),
        (lambda a: a.update(status="passed"), "requires maintainer sign_off"),
        (lambda a: a.update(sign_off=42), "sign_off must be non-empty text or null"),
    ],
)
def test_fairness_evidence_rejects_incomplete_or_contradictory_audits(mutation, message):
    raw = _evidenced_fairness()
    mutation(raw["audits"][0])
    assert message in "\n".join(lb.validate_fairness(raw))


@pytest.mark.parametrize("status", ["pending", "audited", "passed", "failed"])
@pytest.mark.parametrize("date", [None, "", "unknown", 42, "2026-01-03"])
def test_fairness_audit_dates(status, date):
    raw = _evidenced_fairness()
    raw["audits"][0].update(
        status=status,
        date=date,
        sign_off="Fixture maintainer sign-off" if status == "passed" else None,
    )
    if date == "2026-01-03" or (status == "pending" and date is None):
        expected = []
    elif status == "pending":
        expected = ["meta/fairness.json: audits[0]: date must be an ISO date or null"]
    else:
        expected = ["meta/fairness.json: audits[0]: a completed audit is dated"]
    assert lb.validate_fairness(raw) == expected


def test_fairness_evidence_covers_all_unique_checklist_ids():
    raw = _evidenced_fairness()
    raw["checklist"].append({"id": "model", "check": "Native model identity."})
    assert "evidence must cover every checklist item" in "\n".join(lb.validate_fairness(raw))
    raw["checklist"][-1]["id"] = "tools"
    assert "checklist ids must be unique" in "\n".join(lb.validate_fairness(raw))


def test_sign_off_cannot_bypass_evidence_and_pending_review_dates_are_validated():
    raw = _evidenced_fairness()
    audit = raw["audits"][0]
    audit.update(status="passed", sign_off="Fixture sign-off")
    audit.pop("evidence")
    assert "evidence must cover every checklist item" in "\n".join(lb.validate_fairness(raw))
    audit.update(status="pending", sign_off=None, date="unknown")
    assert "date must be an ISO date or null" in "\n".join(lb.validate_fairness(raw))


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["records"][0].update(extra=1), "unknown extra"),
        (lambda d: d["records"][0].pop("task_version"), "missing task_version"),
        (lambda d: d["records"][0].update(task_version=0), "task_version must be a positive"),
        (lambda d: d["records"][5].update(excludedBecause=None), "infrastructure row carries"),
        (lambda d: d["records"][0].update(excludedBecause="pre-NOPE-01"), "nor a changelog"),
        (lambda d: d["records"][0].update(verdict="maybe"), "is not a gym outcome"),
        (lambda d: d["records"][0].update(score=1.5), "score must be in [0, 1]"),
        (lambda d: d["records"][0]["tokens"].update(input=-1), "token counts are non-negative"),
        (lambda d: d["records"][0]["tokens"].update(input=1.5), "token counts are non-negative"),
        (lambda d: d["records"][0].update(paddock_commit=None), "names the paddock commit"),
        (lambda d: d["records"][0]["model"].update(provider_id="nope"), "provider/model"),
        (
            lambda d: d["records"][0]["harness"].update(config_hash="sha256:" + "0" * 64),
            "no descriptor in harness_configs",
        ),
        (
            lambda d: d["harness_configs"].update({"sha256:" + "1" * 64: {"harness": "x"}}),
            "does not hash to its key",
        ),
        (lambda d: d["records"][0]["cost"].update(usd=0.01), "differs from the price table"),
        (
            lambda d: d["records"][6].update(
                cost={"usd": 0.5, "source": "unknown", "reason": None}
            ),
            "unknown source has no dollar figure",
        ),
        (lambda d: d.update(price_table="1999-01-01"), "is not in results/prices/"),
        (lambda d: d.update(id="renamed"), "file name must be <date>-<id>.json"),
        (lambda d: d["source"].update(kind="guess"), "source.kind must be one of"),
    ],
)
def test_schema_rejects(results: Path, mutate, message: str):
    _edit(results, mutate)
    assert message in _errors(results)


def test_duplicate_run_ids_across_documents_are_rejected(results: Path):
    document = json.loads((results / FIXTURE_DOC).read_text(encoding="utf-8"))
    document["id"] = "fixture-copy"
    lb.write_document(results / "2026-01-03-fixture-copy.json", document)
    assert "run_id fixture-001 appears in 2 records" in _errors(results)


def test_import_documents_may_omit_the_paddock_commit(results: Path):
    def mutate(document):
        document["source"]["kind"] = "import"
        document["records"][0]["paddock_commit"] = None

    _edit(results, mutate)
    assert lb.load_results(results).records[0]["paddock_commit"] is None


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({"input": 1.0, "cache": None, "output": 1.0, "source": "unknown"}, "carries no rate"),
        ({"input": None, "cache": None, "output": None, "source": "published-rate"}, "needs input"),
        ({"source": "estimate"}, "source must be one of"),
        ({"as_of": "2026-02-01"}, "as_of is after the table's date"),
        ({"reference": ""}, "names its reference"),
    ],
)
def test_price_table_rejects(results: Path, entry: dict, message: str):
    path = results / "prices" / "2026-01-02.json"
    table = json.loads(path.read_text(encoding="utf-8"))
    table["models"]["acme/coder-large"].update(entry)
    path.write_text(json.dumps(table), encoding="utf-8")
    assert message in _errors(results)


def test_changelog_exclusion_tag_must_name_its_entry(results: Path):
    path = results / "meta" / "changelog.json"
    changelog = json.loads(path.read_text(encoding="utf-8"))
    changelog["entries"][0]["excludes"] = "pre-SOMETHING-ELSE"
    path.write_text(json.dumps(changelog), encoding="utf-8")
    assert "excludes must be null or 'pre-FIXGRADE-01'" in _errors(results)


def _table() -> lb.PriceTable:
    return lb.load_results(FIXTURE).price_tables["2026-01-02"]


MEASURED = {"input": 100_000, "cache": 400_000, "output": 20_000}


def test_price_cost_is_a_number_only_with_a_priced_source():
    table = _table()
    assert lb.price_cost(MEASURED, "acme/coder-large", table) == {
        "usd": 0.48,
        "source": "published-rate",
        "reason": None,
    }
    assert lb.price_cost(MEASURED, "bolt/coder-small", table)["source"] == lb.RECONCILED
    unmeasured = dict(MEASURED, cache=None)
    assert lb.price_cost(unmeasured, "acme/coder-large", table)["reason"] == "tokens_not_measured"
    assert lb.price_cost(MEASURED, "nova/coder-unpriced", table)["reason"] == (
        "model_not_in_price_table"
    )
    assert lb.price_cost(MEASURED, "local/coder-tiny", table)["reason"] == "price_source_unknown"
    assert lb.price_cost(MEASURED, "acme/coder-large", None)["reason"] == "price_table_missing"
    for model in ("nova/coder-unpriced", "local/coder-tiny"):
        assert lb.price_cost(MEASURED, model, table)["usd"] is None


@pytest.mark.parametrize(
    "cost",
    [
        {"usd": 0.48},
        {"usd": 0.48, "source": None},
        {"usd": 0.48, "source": "unknown"},
        {"usd": 0.48, "source": "estimate"},
        {"usd": None, "source": "published-rate"},
        None,
    ],
)
def test_a_cost_without_a_price_source_formats_as_unknown(cost):
    assert lb.format_cost(cost) == "unknown"


def test_a_priced_cost_formats_as_dollars():
    assert lb.format_cost({"usd": 0.48, "source": "published-rate"}) == "$0.48"
    assert lb.format_cost({"usd": 0.004, "source": lb.RECONCILED}) == "$0.0040"


def test_wilson_matches_the_published_intervals():
    low, high = lb.wilson(7, 18)
    assert (round(low * 100), round(high * 100)) == (20, 61)
    assert lb.wilson(0, 12)[0] == 0.0
    assert lb.wilson(0, 0) == (0.0, 0.0)


def _rows(records, suite="coding", version=1):
    board = next(
        b for b in lb.boards(records) if b["suite"] == suite and b["task_version"] == version
    )
    return {(row["key"][0], row["key"][3]): row for row in board["rows"]}


def test_boards_score_only_included_rows_and_price_per_pass():
    rows = _rows(lb.load_results(FIXTURE).records)
    acme = rows[("alpha-cli", "acme/coder-large")]
    assert (acme["passed"], acme["n"]) == (2, 3)
    assert acme["tokens_per_pass"] == 3 * 520_000 / 2
    assert acme["cost_per_pass"] == {"usd": 0.72, "source": "published-rate", "reason": None}
    bolt = rows[("beta-cli", "bolt/coder-small")]
    assert (bolt["passed"], bolt["n"]) == (1, 2)
    assert bolt["excluded"] == {"infra:agent_error": 1}
    assert bolt["cost_per_pass"]["source"] == lb.RECONCILED
    unpriced = rows[("beta-cli", "nova/coder-unpriced")]
    assert unpriced["cost_per_pass"]["usd"] is None
    assert unpriced["cost_per_pass"]["source"] == lb.UNKNOWN
    assert rows[("alpha-cli", "local/coder-tiny")]["cost_per_pass"]["source"] == lb.UNKNOWN


def test_mixed_sources_report_the_weaker_one():
    records = copy.deepcopy(lb.load_results(FIXTURE).records[:5])
    for record in records:
        record["harness"] = records[0]["harness"]
        record["model"] = records[0]["model"]
    records[3]["cost"]["source"] = lb.RECONCILED
    summary = lb.summarize(records)
    assert summary["cost_per_pass"]["source"] == lb.PUBLISHED


def test_tokens_per_pass_needs_every_attempt_measured():
    review_v1 = _rows(lb.load_results(FIXTURE).records, "review", 1)
    row = review_v1[("alpha-cli", "acme/coder-large")]
    assert (row["passed"], row["n"]) == (1, 1)
    assert row["excluded"] == {"pre-FIXGRADE-01": 1}
    assert row["tokens_per_pass"] is None and row["tokens_measured"] == 0
    assert row["cost_per_pass"]["source"] == lb.UNKNOWN


def test_a_row_with_no_scored_attempts_ranks_last():
    records = copy.deepcopy(lb.load_results(FIXTURE).records)
    only_infra = copy.deepcopy(records[5])
    only_infra["run_id"] = "fixture-infra-only"
    only_infra["model"]["provider_id"] = "acme/coder-flaky"
    records.append(only_infra)
    board = next(b for b in lb.boards(records) if b["suite"] == "coding")
    assert board["rows"][-1]["key"][3] == "acme/coder-flaky"
    assert board["rows"][-1]["n"] == 0 and board["rows"][-1]["rate"] is None


def test_task_versions_form_separate_boards():
    boards = lb.boards(lb.load_results(FIXTURE).records)
    review = [b for b in boards if b["suite"] == "review"]
    assert [b["task_version"] for b in review] == [1, 2]
    assert all(b["split"] for b in review)
    assert not next(b for b in boards if b["suite"] == "coding")["split"]


def test_discrimination_rule():
    tasks = {
        (t["task"], t["task_version"]): t
        for t in lb.discrimination(lb.load_results(FIXTURE).records)
    }
    assert tasks[("coding-alpha", 1)]["discriminates"] is True
    assert tasks[("coding-alpha", 1)]["combos_passing"] == 3
    assert tasks[("review-delta", 1)] == {
        **tasks[("review-delta", 1)],
        "passed": 1,
        "n": 1,
        "discriminates": False,
    }
    assert tasks[("review-delta", 2)]["discriminates"] is True


def test_records_from_run_maps_a_suite_summary():
    summary = {
        "suite": "coding",
        "cells": [
            {
                "task": "coding-alpha",
                "task_version": 2,
                "verdict": "passed",
                "score": 1.0,
                "launchRequestId": "lrq_a",
                "attempt": 1,
                "metrics": {
                    "worker_class": "codex",
                    "majority_model": "acme/coder-large",
                    "observed_models": {"acme/coder-large": 20_000},
                    "token_usage_sources": ["codex-exec-json"],
                    "token_usage_input": 500_000,
                    "token_usage_cache_read": 400_000,
                    "token_usage_output": 20_000,
                    "wall_seconds": 61.5,
                },
            },
            {"task": "coding-beta", "verdict": "agent_error", "score": None},
        ],
    }
    harness = {"name": "codex", "version": "1.2.3", "config_hash": "sha256:" + "a" * 64}
    model = {"provider_id": "acme/coder-large", "reasoning": "high"}
    first, second = lb.records_from_run(
        summary,
        bundle_id="test-bundle",
        harness=harness,
        model=model,
        paddock_commit="abcdef1",
        table=_table(),
    )
    assert first["tokens"] == MEASURED
    assert first["task_version"] == 2 and first["wall_seconds"] == 61.5
    assert first["cost"] == {"usd": 0.48, "source": "published-rate", "reason": None}
    assert first["excludedBecause"] is None
    assert second["run_id"] == "test-bundle:coding:coding-beta:1"
    assert second["excludedBecause"] == "infra:agent_error"
    assert second["tokens"] == {"input": None, "cache": None, "output": None}
    assert second["cost"]["source"] == lb.UNKNOWN


def test_render_document_is_stable_json():
    document = json.loads((FIXTURE / FIXTURE_DOC).read_text(encoding="utf-8"))
    text = lb.render_document(document)
    assert json.loads(text) == document
    assert text == (FIXTURE / FIXTURE_DOC).read_text(encoding="utf-8")
    assert len(text.splitlines()) < 40 + len(document["records"])


def _convert(cell, suite="coding", **kwargs):
    return lb.records_from_run(
        {"suite": suite, "cells": [dict(task="coding-alpha", verdict="passed", **cell)]},
        bundle_id="test-bundle",
        harness={"name": "codex", "version": "1", "config_hash": "sha256:" + "a" * 64},
        model={"provider_id": "acme/coder-large", "reasoning": "high"},
        paddock_commit="abcdef1",
        table=_table(),
        **kwargs,
    )[0]


def _metrics(**updates):
    return dict(
        worker_class="codex",
        majority_model="acme/coder-large",
        observed_models={"acme/coder-large": 100},
        token_usage_sources=["codex-exec-json"],
        token_usage_input=1000,
        token_usage_cache_read=400,
        token_usage_output=100,
        **updates,
    )


@pytest.mark.parametrize(
    "cell,tag",
    [
        ({"model_mismatch": True}, "infra:model_mismatch"),
        (
            {"metrics": {"worker_class": "claude-code", "majority_model": "acme/coder-large"}},
            "infra:model_mismatch",
        ),
        (
            {"metrics": {"worker_class": "codex", "majority_model": "bolt/coder-small"}},
            "infra:model_mismatch",
        ),
        (
            {
                "metrics": {
                    "worker_class": "codex",
                    "observed_models": {"acme/coder-large": 50, "bolt/coder-small": 50},
                }
            },
            "infra:model_mismatch",
        ),
        (
            {"execution_provenance": {"harness": "claude-code", "model": "acme/coder-large"}},
            "infra:model_mismatch",
        ),
        (
            {"execution_provenance": {"harness": "codex", "model": "bolt/coder-small"}},
            "infra:model_mismatch",
        ),
        ({}, "infra:attribution_unverified"),
    ],
)
def test_conversion_excludes_attribution_failures(cell, tag):
    record = _convert(cell)
    assert record["verdict"] == "passed"
    assert record["excludedBecause"] == tag
    assert not lb.included(record)
    assert lb.summarize([record])["n"] == 0


def test_standalone_provenance_verifies_identity_without_pricing():
    record = _convert({"execution_provenance": {"harness": "codex", "model": "acme/coder-large"}})
    assert lb.included(record)
    assert record["cost"]["source"] == lb.UNKNOWN


@pytest.mark.parametrize(
    "updates",
    [
        {"token_usage_sources": ["claude-code-exec-json"]},
        {"token_usage_sources": ["codex-exec-json", "claude-code-exec-json"]},
        {"token_usage_sources": []},
        {"token_usage_sources": [None]},
        {"observed_models": {"acme/coder-large": 100, "bolt/coder-small": 0}},
        {"token_usage_cache_read": 1001},
        {"worker_class": "unverified-harness"},
    ],
)
def test_conversion_does_not_price_unsupported_or_mixed_usage(updates):
    metrics = _metrics()
    metrics.update(updates)
    record = _convert({"metrics": metrics})
    assert record["cost"]["usd"] is None
    assert record["cost"]["source"] == lb.UNKNOWN
    assert all(value is None for value in record["tokens"].values())


def test_conversion_rejects_mixed_suite_selector():
    with pytest.raises(lb.ResultsError, match="single suite"):
        _convert({}, suite="all")


@pytest.mark.parametrize("value", [[], {}])
@pytest.mark.parametrize(
    "field", ["verdict", "model", "harness", "changelog_id", "changelog_excludes"]
)
def test_unhashable_values_report_validation_errors(results, field, value):
    if field.startswith("changelog"):
        path = results / "meta" / "changelog.json"
        raw = json.loads(path.read_text())
        raw["entries"][0]["id" if field == "changelog_id" else "excludes"] = value
        path.write_text(json.dumps(raw))
    else:

        def mutate(raw):
            record = raw["records"][0]
            if field == "verdict":
                record["verdict"] = value
            elif field == "model":
                record["model"]["provider_id"] = value
            else:
                record["harness"]["config_hash"] = value

        _edit(results, mutate)
    assert _errors(results)


@pytest.mark.parametrize(
    "cell",
    [
        {"level_mismatch": True},
        {"reasoning_level": "low"},
        {"resolved_reasoning_level": "low"},
        {"metrics": {"resolved_reasoning_level": "low"}},
    ],
)
def test_conversion_excludes_conflicting_reasoning(cell):
    cell = {"execution_provenance": {"harness": "codex", "model": "acme/coder-large"}, **cell}
    assert _convert(cell)["excludedBecause"] == "infra:reasoning_mismatch"


@pytest.mark.parametrize(
    "source,observed,excluded,priced",
    [
        ("codex-exec-json", {"acme/coder-large": 100}, None, True),
        ("claude-code-exec-json", {"acme/coder-large": 100}, None, False),
        ("codex-exec-json", {"acme/coder-large": 100, "bolt/coder-small": 0}, None, False),
        ("codex-exec-json", {"bolt/coder-small": 100}, "infra:model_mismatch", False),
    ],
)
def test_cli_conversion_round_trips_actual_ledger_metrics(
    results, tmp_path, source, observed, excluded, priced
):
    from gym.metrics import launch_metrics

    metrics = launch_metrics(
        [
            {
                "run_id": "worker-1",
                "started_at": "2026-01-03T00:00:00Z",
                "ended_at": "2026-01-03T00:01:00Z",
                "token_usage_source": source,
                "token_usage_input": 1000,
                "token_usage_cache_read": 400,
                "token_usage_output": 100,
                "token_usage_reasoning": 0,
                "token_usage_running_total": 1100,
                "token_usage_by_model_json": {m: {"output": n} for m, n in observed.items()},
                "resolved_reasoning_level": "high",
            }
        ],
        worker_class="codex",
    )
    run = tmp_path / "run.json"
    run.write_text(
        json.dumps(
            {
                "suite": "coding",
                "cells": [
                    {
                        "task": "coding-alpha",
                        "task_version": 2,
                        "verdict": "passed",
                        "model_mismatch": False,
                        "metrics": metrics,
                        "launchRequestId": "lrq-converted",
                    }
                ],
            }
        )
    )
    descriptor = tmp_path / "harness.json"
    descriptor.write_text(json.dumps({"harness": "codex", "reasoning_level": "high"}))
    argv = [
        "--results",
        str(results),
        "from-run",
        str(run),
        "--id",
        "converted",
        "--date",
        "2026-01-03",
        "--harness",
        "codex",
        "--harness-version",
        "1",
        "--harness-config",
        str(descriptor),
        "--model",
        "acme/coder-large",
        "--reasoning",
        "high",
        "--paddock-commit",
        "abcdef1",
        "--description",
        "conversion regression",
        "--evidence",
        "synthetic ledger run",
    ]
    # Test the documented standalone script in a fresh, isolated interpreter:
    # pytest's gym bootstrap must not hide checkout-import or path-mutation bugs.
    converted = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import runpy, sys; "
            "script = runpy.run_path(sys.argv[1]); "
            "original_path = sys.path.copy(); "
            "status = script['main'](sys.argv[2:]); "
            "assert sys.path == original_path, 'conversion changed sys.path'; "
            "sys.exit(status)",
            lb.__file__,
            *argv,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert converted.returncode == 0, converted.stdout + converted.stderr
    record = next(r for r in lb.load_results(results).records if r["run_id"] == "lrq-converted")
    assert record["excludedBecause"] == excluded
    assert (record["cost"]["usd"] is not None) == priced
    if priced:
        assert record["tokens"] == {"input": 600, "cache": 400, "output": 100}
    # A contradictory descriptor must fail before replacing the stored document.
    target = results / "2026-01-03-converted.json"
    before = target.read_bytes()
    descriptor.write_text(json.dumps({"harness": "codex", "reasoning_level": "low"}))
    assert lb.main(argv) == 1
    assert target.read_bytes() == before


@pytest.mark.parametrize(
    "provider_id,observed",
    [
        ("anthropic/claude-sonnet-5-5", "claude-sonnet-5-5-20261001"),
        ("openai/gpt-6.1-sol", "gpt-6.1-sol"),
    ],
)
def test_conversion_accepts_native_provider_model_names(provider_id, observed):
    records = lb.records_from_run(
        {
            "suite": "review",
            "cells": [
                {
                    "task": "review-one",
                    "verdict": "passed",
                    "execution_provenance": {"harness": "codex", "model": observed},
                }
            ],
        },
        bundle_id="test-bundle",
        harness={"name": "codex", "version": "1", "config_hash": "sha256:" + "a" * 64},
        model={"provider_id": provider_id, "reasoning": None},
        paddock_commit="abcdef1",
        table=None,
    )
    assert lb.included(records[0])


def test_conversion_verifies_fleet_worker_class_distinct_from_harness():
    metrics = _metrics()
    metrics["worker_class"] = "oss-codex"
    record = _convert({"metrics": metrics}, worker_class="oss-codex")
    assert lb.included(record)
    assert record["cost"]["source"] == lb.UNKNOWN  # output contract not registered for this class


@pytest.mark.parametrize("worker_class", [None, ""])
def test_conversion_keeps_tokens_without_worker_class(worker_class):
    metrics = _metrics()
    metrics["worker_class"] = worker_class
    record = _convert({"metrics": metrics, "execution_provenance": {"harness": "codex"}})
    assert record["tokens"] == {"input": 600, "cache": 400, "output": 100}


def test_fallback_run_ids_are_namespaced_by_bundle():
    summary = {"suite": "coding", "cells": [{"task": "coding-alpha", "verdict": "passed"}]}
    kwargs = dict(
        harness={"name": "codex"},
        model={"provider_id": "acme/coder-large", "reasoning": None},
        paddock_commit="abcdef1",
        table=None,
    )
    first = lb.records_from_run(summary, bundle_id="first", **kwargs)[0]
    second = lb.records_from_run(summary, bundle_id="second", **kwargs)[0]
    assert first["run_id"] == "first:coding:coding-alpha:0"
    assert second["run_id"] == "second:coding:coding-alpha:0"


@pytest.mark.parametrize("sign_off", [None, "Fixture maintainer sign-off"])
def test_legacy_passed_audit_requires_evidence_with_or_without_sign_off(sign_off):
    raw = _evidenced_fairness()
    audit = raw["audits"][0]
    audit.update(status="passed")
    audit.pop("evidence")
    audit.pop("sign_off")
    if sign_off is not None:
        audit["sign_off"] = sign_off
    assert "evidence must cover every checklist item" in "\n".join(lb.validate_fairness(raw))


def test_legacy_pending_audit_remains_readable():
    raw = _evidenced_fairness()
    audit = raw["audits"][0]
    audit.update(status="pending")
    audit.pop("evidence")
    audit.pop("sign_off")
    assert lb.validate_fairness(raw) == []


@pytest.mark.parametrize(
    "source",
    [
        "not a reference", "http://vendor.example/docs", "https://",
        "https://vendor.example/bad path", "https://[invalid",
        "scripts/../leaderboard.py:1", "scripts/./leaderboard.py:1",
        "scripts//leaderboard.py:1", "scripts/leaderboard.py:0",
        "scripts/leaderboard.py:1junk", "unknown.field",
    ],
)
def test_fairness_rejects_malformed_source_references(source):
    raw = _evidenced_fairness()
    raw["audits"][0]["evidence"][0]["sources"] = [source]
    assert "sources must use HTTPS URLs" in "\n".join(lb.validate_fairness(raw))


@pytest.mark.parametrize(
    "source",
    [
        "https://vendor.example/docs?a=1&b=2", "scripts/leaderboard.py:1",
        "results/fixture.json:45 records[harness.name=codex].model.provider_id (49 records)",
        "run.tools", "records[model.reasoning=<unknown>]", "harness_configs[sha256:abc]",
    ],
)
def test_fairness_accepts_supported_source_references(source):
    raw = _evidenced_fairness()
    raw["audits"][0]["evidence"][0]["sources"] = [source]
    assert lb.validate_fairness(raw) == []
