"""PDK-04: the eval-bench import reproduces results/ and keeps every comparability boundary."""

from __future__ import annotations

import json
import tomllib
from collections import Counter
from pathlib import Path

import import_eval_bench
import leaderboard as lb
import pytest
from gym_test_support import checkout_root

MODULE_ROOT = Path(__file__).resolve().parents[1]
# Source reports are optional inputs, absent from the standalone distribution.
REPORTS = checkout_root(MODULE_ROOT) / "docs" / "reports"
needs_reports = pytest.mark.skipif(
    not all((REPORTS / name).is_file() for name, _ in import_eval_bench.SOURCES),
    reason="the eval-bench source reports are not in this checkout",
)
SCRUBBED = {
    "fix-corrupt-refresh-checkpoint",
    "fix-rescue-local-tip",
    "fix-stale-lock-recovery",
    "review-corrupt-refresh-checkpoint",
    "review-rescue-local-tip",
    "review-stale-lock-recovery",
    "hard-review-multi-defect-tool-calls",
}


@pytest.fixture(scope="module")
def records() -> list[dict]:
    return lb.load_results().records


@needs_reports
def test_import_reproduces_the_committed_results():
    files = import_eval_bench.build(REPORTS, lb.DEFAULT_RESULTS)
    for name, text in files.items():
        assert (lb.DEFAULT_RESULTS / name).read_text(encoding="utf-8") == text, name


def _cells(records, suite):
    out: dict[tuple[str, str, str], list[int]] = {}
    for record in records:
        if record["suite"] == suite and lb.included(record):
            key = (record["harness"]["name"], record["model"]["provider_id"])
            key += (record["harness"]["config_hash"],)
            cell = out.setdefault(key, [0, 0])
            cell[0] += record["verdict"] == lb.PASSED
            cell[1] += 1
    return out


def _by_model(cells):
    merged: Counter = Counter()
    totals: Counter = Counter()
    for (harness, model, _), (passed, n) in cells.items():
        merged[(harness, model)] += passed
        totals[(harness, model)] += n
    return {key: (merged[key], totals[key]) for key in totals}


def test_docs_board_matches_the_published_leaderboard(records):
    docs = _by_model(_cells(records, "docs"))
    assert docs == {
        ("claude-code", "fireworks/qwen3.8-max"): (7, 18),
        ("claude-code", "anthropic/claude-sonnet-5-5"): (4, 12),
        ("claude-code", "anthropic/claude-opus-5-5"): (3, 13),
        ("codex", "openai/gpt-6.1-sol"): (3, 15),
        ("claude-code", "zai/glm-5.2"): (2, 12),
        ("antigravity", "google/gemini-3.1-pro"): (3, 18),
        ("claude-code", "anthropic/claude-sonnet-5"): (0, 12),
        ("claude-code", "anthropic/claude-haiku-4-5"): (0, 6),
    }


def test_hard_suites_match_the_hrf_report(records):
    assert _by_model(_cells(records, "hard-review")) == {
        ("codex", "openai/gpt-6.1-sol"): (5, 6),
        ("antigravity", "google/gemini-3.1-pro"): (4, 6),
        ("claude-code", "fireworks/qwen3.8-max"): (7, 9),
        ("claude-code", "zai/glm-5.2"): (4, 4),
        ("opencode", "together/deepseek-v4.1-flash"): (7, 7),
    }
    assert _by_model(_cells(records, "hard-fix")) == {
        ("codex", "openai/gpt-6.1-sol"): (6, 6),
        ("antigravity", "google/gemini-3.1-pro"): (5, 6),
        ("claude-code", "fireworks/qwen3.8-max"): (6, 6),
        ("claude-code", "zai/glm-5.2"): (5, 6),
        ("opencode", "together/deepseek-v4.1-flash"): (9, 11),
    }


def test_open_weight_matrix_matches_the_ossharn_report():
    document = lb.load_results().documents["2026-09-29-ossharn-04-coding-matrix.json"]
    matrix = document["records"]
    totals: Counter = Counter()
    passed: Counter = Counter()
    for record in matrix:
        if lb.included(record):
            key = (record["harness"]["name"], record["harness"]["config_hash"])
            totals[key] += 1
            passed[key] += record["verdict"] == lb.PASSED
    by_harness = {
        key[0]: (passed[key], totals[key]) for key in totals if key[0] != "codex"
    }
    assert by_harness == {"claude-code": (26, 40), "opencode": (27, 42), "pi": (28, 42)}
    # The original codex cells, and the post-KIMIEMPTY-01 rerun under its own config.
    assert sorted((passed[k], totals[k]) for k in totals if k[0] == "codex") == [
        (4, 7),
        (27, 38),
    ]


def test_every_exclusion_survives_the_import(records):
    tags = Counter(r["excludedBecause"] for r in records if r["excludedBecause"])
    assert tags["pre-GYMDOCS-01"] == 35
    assert tags["pre-GYMAGY-02"] == 11
    assert tags["pre-RFDFIX-01"] == 6
    assert tags["infra:source_infra_list"] == 5
    assert all(
        r["excludedBecause"].startswith("infra:")
        for r in records
        if r["verdict"] in lb.INFRA_VERDICTS
    )


def test_imported_rows_are_graded_task_versions_and_unmeasured(records):
    tasks_dir = MODULE_ROOT / "tasks"
    for record in records:
        task = tomllib.loads(
            (tasks_dir / record["task"] / "task.toml").read_text("utf-8")
        )
        assert task["suite"] == record["suite"]
        assert record["task_version"] == 1
        assert record["task_version"] <= task["task_version"]
        assert record["tokens"] == {"input": None, "cache": None, "output": None}
        assert record["cost"]["source"] == lb.UNKNOWN and record["cost"]["usd"] is None
    assert {r["task"] for r in records} & SCRUBBED == SCRUBBED


@pytest.mark.parametrize(
    ("label", "provider_id"),
    [
        ("litellm-local-openai/anthropic/claude-opus-5-5", "anthropic/claude-opus-5-5"),
        (
            "litellm-local-openai/anthropic/claude-sonnet-5-5",
            "anthropic/claude-sonnet-5-5",
        ),
        ("litellm-local-openai/openai/gpt-6.1-sol", "openai/gpt-6.1-sol"),
    ],
)
def test_cross_harness_proxy_labels_name_the_native_model(label, provider_id):
    # A frontier model in another vendor's harness is the same model as its
    # native cell, so both import under one provider id.
    assert import_eval_bench.model_of(label) == {
        "provider_id": provider_id,
        "reasoning": None,
    }


V2_SAMPLE = MODULE_ROOT / "tests" / "fixtures" / "eval-bench-v2-sample.raw.json"


def _synthetic_id(number):
    return f"lrq_00000000-0000-4000-8000-{number:012d}"


@pytest.fixture(scope="module")
def v2_import():
    table = lb.load_results().price_tables["2026-10-18"]
    return import_eval_bench.import_eval_bench(V2_SAMPLE, table, {_synthetic_id(3)})


def _v2_record(document, number):
    return next(r for r in document["records"] if r["run_id"] == _synthetic_id(number))


def test_per_attempt_tokens_wall_time_and_price(v2_import):
    document, _, _ = v2_import
    record = _v2_record(document, 1)
    assert document["id"] == "eval-bench"
    assert document["date"] == "2026-10-18"
    assert document["price_table"] == "2026-10-18"
    assert document["source"]["kind"] == "import"
    assert V2_SAMPLE.name in document["source"]["evidence"]
    assert record["suite"] == "review"
    assert record["task"] == "review-clean-retention-plan"
    assert record["attempt"] == 1
    assert record["verdict"] == "passed"
    assert record["score"] == 1.0
    assert record["tokens"] == {"input": 1000, "cache": 2000, "output": 300}
    assert record["wall_seconds"] == 12.5
    assert record["paddock_commit"] is None
    assert record["cost"] == {"usd": 0.0052, "source": "published-rate", "reason": None}
    assert record["excludedBecause"] is None
    config = document["harness_configs"][record["harness"]["config_hash"]]
    assert config == import_eval_bench.descriptor("claude-code")


def test_per_attempt_regrades_keep_both_versions(v2_import):
    document, _, _ = v2_import
    original = _v2_record(document, 2)
    regrade = next(
        r for r in document["records"] if r["run_id"] == _synthetic_id(2) + ":v2"
    )
    assert (original["task_version"], regrade["task_version"]) == (1, 2)
    assert original["task"] == regrade["task"]
    assert original["verdict"] == "failed"
    assert regrade["verdict"] == "passed"
    assert lb.included(original) and lb.included(regrade)
    boards = lb.boards([original, regrade])
    assert {board["task_version"] for board in boards} == {1, 2}


def test_per_attempt_skips_published_and_unrecorded_versions(v2_import):
    document, published, unrecorded = v2_import
    assert (published, unrecorded) == (1, 1)
    assert len(document["records"]) == 10
    ids = {r["run_id"] for r in document["records"]}
    assert _synthetic_id(3) not in ids
    assert _synthetic_id(4) not in ids
    # Inference is informational: the supplied version is retained.
    assert _v2_record(document, 9)["task_version"] == 1


def test_published_first_grading_does_not_skip_its_regrade():
    table = lb.load_results().price_tables["2026-10-18"]
    document, published, unrecorded = import_eval_bench.import_eval_bench(
        V2_SAMPLE, table, {_synthetic_id(2)}
    )
    assert (published, unrecorded) == (1, 1)
    ids = {r["run_id"] for r in document["records"]}
    assert _synthetic_id(2) not in ids
    assert _synthetic_id(2) + ":v2" in ids


def test_per_attempt_exclusions_survive(v2_import):
    document, _, _ = v2_import
    infra = next(
        r for r in document["records"] if r["run_id"].startswith("unlaunched:")
    )
    assert infra["verdict"] == "agent_error"
    assert infra["excludedBecause"] == "infra:launch_failed"
    assert infra["wall_seconds"] is None
    assert _v2_record(document, 6)["excludedBecause"] == "infra:qwensys_02"
    assert _v2_record(document, 7)["excludedBecause"] == "pre-CCMODEL-01"


def test_per_attempt_infra_verdict_gets_default_exclusion(tmp_path):
    raw = json.loads(V2_SAMPLE.read_text())
    row = next(r for r in raw["rows"] if r["verdict"] == "agent_error")
    row["excludedBecause"] = None
    raw["rows"] = [row]
    path = tmp_path / V2_SAMPLE.name
    path.write_text(json.dumps(raw))
    table = lb.load_results().price_tables["2026-10-18"]
    document, _, _ = import_eval_bench.import_eval_bench(path, table, set())
    assert document["records"][0]["excludedBecause"] == "infra:agent_error"


def test_per_attempt_reasoning_and_unknown_cost(v2_import):
    document, _, _ = v2_import
    assert _v2_record(document, 8)["model"] == {
        "provider_id": "openai/gpt-6.1-sol",
        "reasoning": "xhigh",
    }
    unmeasured = _v2_record(document, 9)
    assert unmeasured["tokens"] == {"input": None, "cache": None, "output": None}
    assert unmeasured["model"]["reasoning"] is None
    assert unmeasured["cost"] == {
        "usd": None,
        "source": "unknown",
        "reason": "tokens_not_measured",
    }
    assert _v2_record(document, 11)["cost"] == {
        "usd": None,
        "source": "unknown",
        "reason": "price_source_unknown",
    }


def test_per_attempt_rejects_contradictory_reasoning(tmp_path):
    raw = json.loads(V2_SAMPLE.read_text())
    row = next(r for r in raw["rows"] if r["model"].endswith("@xhigh"))
    row["reasoningLevel"] = "high"
    raw["rows"] = [row]
    path = tmp_path / V2_SAMPLE.name
    path.write_text(json.dumps(raw))
    table = lb.load_results().price_tables["2026-10-18"]
    with pytest.raises(
        import_eval_bench.SourceError, match="contradicts reasoningLevel"
    ):
        import_eval_bench.import_eval_bench(path, table, set())


@pytest.mark.parametrize(
    ("label", "provider_id", "reasoning"),
    [
        ("agy:gemini-3.8-flash-high", "google/gemini-3.8-flash", "high"),
        ("agy:gemini-4-argon-high", "google/gemini-4-argon", "high"),
        ("google/gemini-3.8-flash", "google/gemini-3.8-flash", None),
        ("google/gemini-3.8-flash-high", "google/gemini-3.8-flash", "high"),
        ("google/gemini-3.8-flash-low", "google/gemini-3.8-flash", "low"),
        ("together/qwen3.8-flash", "together/qwen3.8-flash", None),
        *[
            (name, "openai/" + name, None)
            for name in (
                "gpt-5.6-luna",
                "gpt-5.6-sol",
                "gpt-5.6-terra",
                "gpt-6-astra",
                "gpt-6-luna",
                "gpt-6-sol",
            )
        ],
        ("gpt-6.1-sol@xhigh", "openai/gpt-6.1-sol", "xhigh"),
        ("litellm-local-openai/openai/gpt-6.1-sol@high", "openai/gpt-6.1-sol", "high"),
    ],
)
def test_per_attempt_model_labels(label, provider_id, reasoning):
    assert import_eval_bench.model_of(label) == {
        "provider_id": provider_id,
        "reasoning": reasoning,
    }


def test_per_attempt_document_validates(v2_import):
    document, _, _ = v2_import
    loaded = lb.load_results()
    assert (
        lb.validate_document(
            "2026-10-18-eval-bench.json",
            document,
            price_tables=loaded.price_tables,
            exclusion_tags={e["excludes"] for e in loaded.changelog if e["excludes"]},
        )
        == []
    )


def test_new_price_table_preserves_previous_entries_and_note():
    tables = lb.load_results().price_tables
    previous, current = tables["2026-10-10"], tables["2026-10-18"]
    assert current.note == previous.note
    assert {name: current.models[name] for name in previous.models} == previous.models
