"""PDK-04: the eval-bench import reproduces results/ and keeps every comparability boundary."""

from __future__ import annotations

import tomllib
from collections import Counter
from pathlib import Path

import import_eval_bench
import leaderboard as lb
import pytest

MODULE_ROOT = Path(__file__).resolve().parents[1]
# modules/adversarial-eval-gym -> repo root; the reports stay in agent-os.
REPORTS = MODULE_ROOT.parents[1] / "docs" / "reports"
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
    by_harness = {key[0]: (passed[key], totals[key]) for key in totals if key[0] != "codex"}
    assert by_harness == {"claude-code": (26, 40), "opencode": (27, 42), "pi": (28, 42)}
    # The original codex cells, and the post-KIMIEMPTY-01 rerun under its own config.
    assert sorted((passed[k], totals[k]) for k in totals if k[0] == "codex") == [(4, 7), (27, 38)]


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
        task = tomllib.loads((tasks_dir / record["task"] / "task.toml").read_text("utf-8"))
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
        ("litellm-local-openai/anthropic/claude-sonnet-5-5", "anthropic/claude-sonnet-5-5"),
        ("litellm-local-openai/openai/gpt-6.1-sol", "openai/gpt-6.1-sol"),
    ],
)
def test_cross_harness_proxy_labels_name_the_native_model(label, provider_id):
    # A frontier model in another vendor's harness is the same model as its
    # native cell, so both import under one provider id.
    assert import_eval_bench.model_of(label) == {"provider_id": provider_id, "reasoning": None}
