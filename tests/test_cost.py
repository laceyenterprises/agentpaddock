"""MEG-03: dollars and weekly-quota share per cell.

Prices go through the one SEW price table (``sew.cost_model.load_price_table``,
the real file), and the per-harness reasoning rule is pinned against the ledger
capture code that writes the token columns.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import types

import pytest
from conftest import FIXTURES_DIR

from gym import cost, metrics, taint
from gym.grader import Trajectory
from gym.ledger import LedgerReader
from gym.suite import discover_tasks, run_suite

MINI_SUITE = FIXTURES_DIR / "mini-suite"

#: The MEG pilot battery (projects/model-efficiency-gym/SPEC.md §3.1).
BATTERY = {
    "codex": [
        "gpt-6-sol",
        "gpt-6-astra",
        "gpt-6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
    ],
    "claude-code": ["claude-opus-5-5", "claude-sonnet-5", "claude-haiku-4-5"],
}
SOURCE = {"codex": "codex-exec-json", "claude-code": "session-ledger"}


@pytest.fixture(scope="module")
def table():
    from sew.cost_model import load_price_table

    return load_price_table()


def _metrics(
    model,
    *,
    worker_class="codex",
    tokens=(1000, 400, 100, 0),
    sources=None,
    observed=None,
):
    total_input, cache_read, output, reasoning = tokens
    return {
        "run_ids": ["run-1"],
        "attempts": 1,
        "token_usage_input": total_input,
        "token_usage_cache_read": cache_read,
        "token_usage_output": output,
        "token_usage_reasoning": reasoning,
        "token_usage_running_total": None,
        "token_usage_sources": sources if sources is not None else [SOURCE[worker_class]],
        "worker_class": worker_class,
        "observed_models": observed if observed is not None else {model: output},
        "majority_model": model,
    }


# --- dollars ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("worker_class", "model"),
    [(wc, model) for wc, models in BATTERY.items() for model in models],
)
def test_every_battery_model_prices_or_reports_unknown(table, worker_class, model):
    result = cost.price_cell(_metrics(model, worker_class=worker_class), table)
    if result["cost_status"] == "unknown":
        assert result["cost_usd"] is None
        assert result["cost_reason"]
    else:
        assert result["cost_usd"] > 0
        assert result["price_source"].startswith("https://")
        assert result["price_as_of"]
    # Every battery model has a published rate today, so none is unknown.
    assert result["cost_status"] in {"inferred", "estimated"}, result


def test_an_unpriced_model_is_unknown_never_zero(table):
    # The operator's pending Sonnet 5.5 has no row in the table.
    result = cost.price_cell(_metrics("claude-sonnet-5-5", worker_class="claude-code"), table)
    assert result["cost_usd"] is None
    assert result["cost_status"] == "unknown"
    assert result["cost_reason"] == "model:claude-sonnet-5-5:no_published_rate"


def test_input_splits_into_disjoint_fresh_and_cached_buckets(table):
    result = cost.price_cell(_metrics("gpt-5.6-terra", tokens=(1000, 400, 100, 0)), table)
    assert result["tokens"] == {"fresh_input": 600, "cache_read": 400, "output": 100}
    # 600 fresh at $2, 400 cached at $0.20, 100 output at $12, per million.
    assert result["cost_usd"] == pytest.approx((600 * 2.0 + 400 * 0.2 + 100 * 12.0) / 1e6)
    assert result["cost_status"] == "inferred"


def test_cache_reads_use_the_cached_rate(table):
    cached = cost.price_cell(_metrics("gpt-5.6-terra", tokens=(1000, 400, 100, 0)), table)
    uncached = cost.price_cell(_metrics("gpt-5.6-terra", tokens=(1000, 0, 100, 0)), table)
    rates = cached["assumed_rate"]
    assert rates["cached_input_usd_per_mtok"] == 0.2
    saving = 400 * (rates["input_usd_per_mtok"] - rates["cached_input_usd_per_mtok"]) / 1e6
    assert uncached["cost_usd"] - cached["cost_usd"] == pytest.approx(saving)


def test_cache_read_above_total_input_is_invalid_never_clamped(table):
    result = cost.price_cell(_metrics("gpt-5.6-terra", tokens=(400, 1000, 100, 0)), table)
    assert result["cost_usd"] is None
    assert result["cost_status"] == "unknown"
    assert result["cost_reason"] == "invalid_cache_read_exceeds_input"


@pytest.mark.parametrize("column", [0, 1, 2])
def test_a_null_token_column_stays_unpriced(table, column):
    tokens = [1000, 400, 100, 0]
    tokens[column] = None
    result = cost.price_cell(_metrics("gpt-5.6-terra", tokens=tuple(tokens)), table)
    assert (result["cost_usd"], result["cost_status"]) == (None, "unknown")
    assert result["cost_reason"] == "token_usage_incomplete"


@pytest.mark.parametrize(
    ("sources", "reason"),
    [
        # CONS-03: claude-code usage read from an exec artifact stores the
        # fresh-only Anthropic input_tokens, so input minus cache is meaningless.
        (["claude-code-exec-json"], "token_source_input_contract_unknown"),
        (["live-floor"], "token_source_input_contract_unknown"),
        (["session-ledger", None], "token_source_unrecorded"),
        ([], "token_source_unrecorded"),
    ],
)
def test_a_token_source_without_the_inclusive_input_contract_is_unknown(table, sources, reason):
    m = _metrics("claude-haiku-4-5", worker_class="claude-code", sources=sources)
    result = cost.price_cell(m, table)
    assert (result["cost_usd"], result["cost_status"], result["cost_reason"]) == (
        None,
        "unknown",
        reason,
    )


def test_an_unread_harness_is_unknown(table):
    m = _metrics("gpt-5.6-terra")
    m["worker_class"] = "gemini"
    result = cost.price_cell(m, table)
    assert result["cost_status"] == "unknown"
    assert result["cost_reason"] == "reasoning_rule_unknown_for_harness"


def test_codex_input_over_the_long_context_threshold_is_unknown(table):
    # Aggregate usage cannot say which request crossed 272K (SEW's rule).
    result = cost.price_cell(_metrics("gpt-6-sol", tokens=(300_000, 0, 100, 0)), table)
    assert result["cost_status"] == "unknown"
    assert result["cost_reason"].endswith(":long_context_rate_requires_per_request_usage")


def test_side_model_tokens_make_the_cost_an_estimate(table):
    m = _metrics(
        "claude-opus-5-5",
        worker_class="claude-code",
        observed={"claude-opus-5-5": 90, "claude-haiku-4-5-20251001": 10},
    )
    result = cost.price_cell(m, table)
    assert result["cost_status"] == "estimated"
    assert "side_model_tokens_priced_at_majority_model" in result["pricing_notes"]
    assert result["side_model_output_share"] == pytest.approx(0.1)


# --- reasoning is priced once ---------------------------------------------------


def test_codex_capture_keeps_reasoning_inside_output():
    """Pin: the codex capture stores OpenAI output_tokens, reasoning included."""
    from cwp_dispatch import teardown_rollup

    usage = teardown_rollup._usage_from_mapping(
        {
            "input_tokens": 24117,
            "cached_input_tokens": 16128,
            "output_tokens": 561,
            "reasoning_output_tokens": 512,
        }
    )
    assert (usage["output"], usage["reasoning"]) == (561, 512)
    assert cost.REASONING_INSIDE_OUTPUT["codex"] is True


def test_claude_code_capture_has_no_separate_reasoning():
    """Pin: the claude-code transcript capture stores Anthropic output_tokens,
    which already count thinking, and derives no reasoning breakout."""
    from session_ledger.adapters.claude_code import ClaudeCodeAdapter

    usage = ClaudeCodeAdapter()._claude_usage_payload(
        {
            "model": "claude-opus-5-5",
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 20,
                "cache_read_input_tokens": 400,
                "output_tokens": 900,
            },
        }
    )
    assert usage["output"] == 900
    assert "reasoning" not in usage
    # The stored input is total input, the contract the fresh split relies on.
    assert usage["input"] == 430
    assert cost.REASONING_INSIDE_OUTPUT["claude-code"] is True


@pytest.mark.parametrize(
    ("worker_class", "model", "rates"),
    [
        ("codex", "gpt-5.6-terra", (2.0, 0.2, 12.0)),
        ("claude-code", "claude-haiku-4-5", (1.0, 0.1, 5.0)),
    ],
)
def test_reasoning_tokens_are_not_double_counted(table, worker_class, model, rates):
    input_rate, cached_rate, output_rate = rates
    m = _metrics(model, worker_class=worker_class, tokens=(1000, 400, 561, 512))
    result = cost.price_cell(m, table)
    assert result["tokens"]["output"] == 561
    assert result["cost_usd"] == pytest.approx(
        (600 * input_rate + 400 * cached_rate + 561 * output_rate) / 1e6
    )


def test_a_harness_with_separate_reasoning_adds_it_once(table, monkeypatch):
    monkeypatch.setattr(cost, "REASONING_INSIDE_OUTPUT", {"codex": False})
    result = cost.price_cell(_metrics("gpt-5.6-terra", tokens=(1000, 0, 49, 512)), table)
    assert result["tokens"]["output"] == 561
    assert result["cost_usd"] == pytest.approx((1000 * 2.0 + 561 * 12.0) / 1e6)
    unmeasured = cost.price_cell(_metrics("gpt-5.6-terra", tokens=(1000, 0, 49, None)), table)
    assert unmeasured["cost_status"] == "unknown"


# --- quota share ------------------------------------------------------------------


def _proof(
    model="gpt-6-sol",
    *,
    reset,
    status="calibrating",
    tpp=1_000_000.0,
    limit="codex",
    window="secondary",
    provider="openai",
):
    return {
        "provider": provider,
        "limitId": limit,
        "model": model,
        "window": window,
        "resetsAt": reset,
        "tokensPerPercent": tpp,
        "status": status,
    }


WEEKLY = {("codex", "secondary")}


def test_weekly_windows_come_from_the_snapshot_window_length():
    snapshots = [
        {"limitId": "codex", "window": "primary", "windowMinutes": 300},
        {"limitId": "codex", "window": "secondary", "windowMinutes": 10080},
        {"window": "secondary", "windowMinutes": 10080},
        {"limitId": "other", "window": "secondary", "windowMinutes": 10080},
        {"limitId": "other", "window": "secondary", "windowMinutes": 300},
        {"limitId": "codex", "window": "tertiary"},
    ]
    assert cost.weekly_windows(snapshots) == {("codex", "secondary")}


def test_quota_share_uses_the_first_weekly_calibration():
    proofs = [
        _proof(reset="2026-09-29T00:00:00+00:00", status="ok", tpp=2_000_000.0),
        _proof(reset="2026-09-22T00:00:00+00:00", tpp=1_000_000.0),
        _proof(reset="2026-09-15T00:00:00+00:00", window="primary", tpp=10.0),
    ]
    m = _metrics("gpt-6-sol", tokens=(150_000, 100_000, 50_000, 0))
    result = cost.quota_share(m, proofs=proofs, weekly=WEEKLY)
    # (input + output) / tokens-per-percent, as a fraction of the window.
    assert result["quota_share"] == pytest.approx(200_000 / 1_000_000 / 100)
    assert result["quota_status"] == "calibrated"
    assert result["quota_auth_path"] == "codex"
    assert result["quota_calibration"]["tokens_per_percent"] == 1_000_000.0


def test_an_uncalibrated_model_has_unknown_quota_share():
    proofs = [_proof(reset="2026-09-22T00:00:00+00:00")]
    result = cost.quota_share(_metrics("gpt-6-luna"), proofs=proofs, weekly=WEEKLY)
    assert (result["quota_share"], result["quota_status"]) == (None, "unknown")
    assert result["quota_reason"] == "no_weekly_calibration"


def test_claude_quota_is_unknown_because_the_proof_is_unmeasured():
    proofs = [
        _proof(
            "all",
            reset="unknown",
            status="unmeasured",
            tpp=None,
            provider="anthropic",
            limit="subscription",
            window="subscription",
        ),
    ]
    m = _metrics("claude-opus-5-5", worker_class="claude-code")
    result = cost.quota_share(m, proofs=proofs, weekly=WEEKLY)
    assert (result["quota_share"], result["quota_status"]) == (None, "unknown")


def test_only_a_non_weekly_or_unmeasured_calibration_is_unknown():
    proofs = [
        _proof(reset="2026-09-22T00:00:00+00:00", window="primary"),
        _proof(reset="2026-09-23T00:00:00+00:00", status="unmeasured"),
    ]
    result = cost.quota_share(_metrics("gpt-6-sol"), proofs=proofs, weekly=WEEKLY)
    assert result["quota_status"] == "unknown"


def test_two_calibrated_auth_paths_are_ambiguous():
    proofs = [
        _proof(reset="2026-09-22T00:00:00+00:00"),
        _proof(reset="2026-09-22T00:00:00+00:00", limit="codex-team"),
    ]
    weekly = WEEKLY | {("codex-team", "secondary")}
    result = cost.quota_share(_metrics("gpt-6-sol"), proofs=proofs, weekly=weekly)
    assert (result["quota_share"], result["quota_reason"]) == (None, "auth_path_ambiguous")


def test_quota_share_with_null_tokens_is_unknown():
    proofs = [_proof(reset="2026-09-22T00:00:00+00:00")]
    m = _metrics("gpt-6-sol", tokens=(None, 0, 10, 0))
    result = cost.quota_share(m, proofs=proofs, weekly=WEEKLY)
    assert result["quota_reason"] == "token_usage_incomplete"


@pytest.mark.parametrize("side_calibrated", [True, False])
@pytest.mark.parametrize("side_output", [1000, 0])
def test_mixed_model_quota_is_unknown_without_per_model_input(side_calibrated, side_output):
    proofs = [_proof(reset="2026-09-22T00:00:00+00:00")]
    if side_calibrated:
        proofs.append(_proof("gpt-6-luna", reset="2026-09-22T00:00:00+00:00", tpp=100_000))
    # The metrics preserve output per model, but not input. Even a model with
    # zero output can have spent input tokens; no majority-model rate is safe.
    m = metrics.launch_metrics(
        [
            {
                "token_usage_input": 100_000,
                "token_usage_output": 10_000,
                "token_usage_by_model_json": {
                    "gpt-6-sol": {"input": 90_000, "output": 10_000 - side_output},
                    "gpt-6-luna": {"input": 10_000, "output": side_output},
                },
            }
        ],
        worker_class="codex",
    )
    result = cost.quota_share(m, proofs=proofs, weekly=WEEKLY)
    assert (result["quota_share"], result["quota_status"]) == (None, "unknown")
    assert result["quota_reason"] == "mixed_model_token_totals_unavailable"
    assert result["quota_calibration"] is None


def test_quota_model_aliases_share_one_calibration():
    proofs = [_proof(reset="2026-09-22T00:00:00+00:00")]
    m = _metrics(
        "gpt-6-sol",
        observed={"gpt-6-sol": 90, "litellm-local-openai/gpt-6-sol-20260929": 10},
    )
    result = cost.quota_share(m, proofs=proofs, weekly=WEEKLY)
    assert result["quota_status"] == "calibrated"
    assert result["quota_share"] == pytest.approx(1100 / 1_000_000 / 100)


def test_cell_economics_reports_unavailable_inputs_as_unknown():
    result = cost.cell_economics(_metrics("gpt-6-sol"))
    assert (result["cost_usd"], result["cost_status"]) == (None, "unknown")
    assert (result["quota_share"], result["quota_status"]) == (None, "unknown")


# --- the ledger read and the suite -------------------------------------------------


def test_reader_returns_the_stored_quota_proofs():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE worker_runs (run_id TEXT, worker_kind TEXT, updated_at TEXT, "
        "metadata_json TEXT)"
    )
    calibrating = _proof(reset="2026-01-01T00:00:00+00:00")
    conn.executemany(
        "INSERT INTO worker_runs VALUES (?, ?, ?, ?)",
        [
            # An old calibration stays readable: it is the fixed bar.
            (
                "p1",
                "subscription-quota-proof",
                "2026-01-01T00:00:00Z",
                json.dumps(calibrating, sort_keys=True).replace(",", ", "),
            ),
            ("p2", "codex", "2099-01-01T00:00:00Z", json.dumps({"status": "ok"})),
            ("p3", "subscription-quota-proof", "2099-01-01T00:00:00Z", "not json"),
        ],
    )
    reader = LedgerReader(types.SimpleNamespace(conn=conn))
    assert reader.subscription_quota_proofs() == [calibrating]


class _NoCompatibilityConnection:
    @property
    def conn(self):
        raise AssertionError("Postgres quota proofs must not touch the compatibility connection")


def test_quota_reader_uses_bounded_postgres_read_with_bound_values(monkeypatch):
    from cwp_dispatch import subscription_quota_reconcile
    from session_ledger import mcp_call_reads

    kind = "quota-'proof"
    monkeypatch.setattr(subscription_quota_reconcile, "PROOF_KIND", kind)
    calibrating = _proof(reset="2026-01-01T00:00:00+00:00")
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE worker_runs (worker_kind TEXT, updated_at TEXT, metadata_json TEXT)")
    conn.executemany(
        "INSERT INTO worker_runs VALUES (?, ?, ?)",
        [
            (kind, "2026-01-01T00:00:00Z", json.dumps(calibrating, separators=(",", ":"))),
            (kind, "2099-01-01T00:00:00Z", json.dumps({"status": "ok"})),
            (kind, "2026-01-01T00:00:00Z", json.dumps({"status": "ok"})),
            ("codex", "2099-01-01T00:00:00Z", json.dumps(calibrating)),
        ],
    )
    seen = {}
    target = types.SimpleNamespace(backend="postgres", dsn="postgresql://fixture")

    def fake_rows(active, select, *, variables=None):
        seen.update(target=active, select=select, variables=variables)
        # Execute the portable JSON predicate, including compact old proofs.
        query = select.replace(":'proof_kind'", ":proof_kind").replace(":'since'", ":since")
        rows = [{"metadata_json": row[0]} for row in conn.execute(query, variables)]
        return rows + [{"metadata_json": "not json"}, {"metadata_json": []}]

    monkeypatch.setattr(mcp_call_reads, "psql_read_only_rows", fake_rows)
    reader = LedgerReader(_NoCompatibilityConnection(), target_resolver=lambda: target)
    assert reader.subscription_quota_proofs() == [calibrating, {"status": "ok"}]
    assert seen["target"] is target
    assert kind not in seen["select"]
    assert seen["variables"]["proof_kind"] == kind
    assert seen["variables"]["since"].endswith("Z")


def test_blocked_postgres_quota_read_is_bounded_and_becomes_unknown(monkeypatch, tmp_path):
    from gym.dispatch import WorkerPoolTrajectorySource
    from session_ledger import mcp_call_reads

    target = types.SimpleNamespace(backend="postgres", dsn="postgresql://fixture")
    reader = LedgerReader(_NoCompatibilityConnection(), target_resolver=lambda: target)
    monkeypatch.setattr(mcp_call_reads, "_psql_env", lambda _dsn: {})
    monkeypatch.setattr(mcp_call_reads, "_psql_binary", lambda: "psql")
    calls = []

    def blocked_read(argv, **kwargs):
        calls.append((argv, kwargs))
        assert "BEGIN READ ONLY" in kwargs["input"]
        assert "SET LOCAL statement_timeout = '12s'" in kwargs["input"]
        assert kwargs["timeout"] == 15
        raise subprocess.CalledProcessError(
            1, argv, stderr="ERROR: canceling statement due to statement timeout"
        )

    monkeypatch.setattr(mcp_call_reads.subprocess, "run", blocked_read)
    source = WorkerPoolTrajectorySource(hq_root=tmp_path, _reader=reader)
    result = source.cell_economics(_metrics("gpt-6-sol"))
    assert len(calls) == 1
    assert (result["quota_share"], result["quota_reason"]) == (None, "calibration_unavailable")


def test_launch_metrics_carry_each_runs_token_source():
    runs = [
        {"run_id": "a", "token_usage_source": "codex-exec-json"},
        {"run_id": "b", "token_usage_source": None},
    ]
    m = metrics.launch_metrics(runs, worker_class="codex")
    assert m["token_usage_sources"] == ["codex-exec-json", None]


class _PricingSource:
    def __init__(self, trajectory, economics=None):
        self.trajectory = trajectory
        if economics is not None:
            self.cell_economics = economics

    def run(self, task):
        return self.trajectory


def _mini_pass():
    return [t for t in discover_tasks(MINI_SUITE) if t.id == "mini-pass"]


def test_run_suite_attaches_cost_and_quota_to_the_cell(table):
    m = _metrics("gpt-5.6-terra")
    source = _PricingSource(
        Trajectory(terminal_status="succeeded", submitted_result={"answer": "ok"}, metrics=m),
        economics=lambda metrics_: cost.cell_economics(
            metrics_, table=table, proofs=[], weekly=set()
        ),
    )
    result = run_suite(_mini_pass(), source, taint_mode=taint.ENFORCE)
    cell = json.loads(json.dumps(result.graded[0].cell.to_dict()))
    assert cell["cost_usd"] == pytest.approx(0.00248)
    assert cell["cost_status"] == "inferred"
    assert (cell["quota_share"], cell["quota_status"]) == (None, "unknown")
    assert cell["economics"]["price_source"].endswith("gpt-5.6-terra")
    view = result.summary_dict()["cells"][0]
    assert view["cost_usd"] == pytest.approx(0.00248)
    assert view["quota_status"] == "unknown"


def test_a_pricing_failure_keeps_the_verdict():
    def boom(_metrics):
        raise RuntimeError("table exploded")

    source = _PricingSource(
        Trajectory(
            terminal_status="succeeded",
            submitted_result={"answer": "ok"},
            metrics=_metrics("gpt-5.6-terra"),
        ),
        economics=boom,
    )
    cell = run_suite(_mini_pass(), source, taint_mode=taint.ENFORCE).graded[0].cell
    assert cell.outcome == "passed"
    assert (cell.economics["cost_usd"], cell.economics["cost_status"]) == (None, "unknown")


def test_a_source_that_cannot_price_leaves_the_cell_unchanged():
    source = _PricingSource(
        Trajectory(
            terminal_status="succeeded",
            submitted_result={"answer": "ok"},
            metrics=_metrics("gpt-5.6-terra"),
        )
    )
    cell = run_suite(_mini_pass(), source, taint_mode=taint.ENFORCE).graded[0].cell.to_dict()
    assert "cost_usd" not in cell
    assert "economics" not in cell


def test_worker_pool_source_prices_from_the_real_table_and_snapshots(tmp_path):
    from gym.dispatch import WorkerPoolTrajectorySource

    snapshots = tmp_path / "codex-quota-snapshots"
    snapshots.mkdir()
    (snapshots / "lrq_1.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2099-01-01T00:00:00+00:00",
                "limitId": "codex",
                "window": "secondary",
                "windowMinutes": 10080,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    reader = types.SimpleNamespace(
        subscription_quota_proofs=lambda: [_proof(reset="2026-09-22T00:00:00+00:00")]
    )
    source = WorkerPoolTrajectorySource(hq_root=tmp_path, _reader=reader)
    result = source.cell_economics(_metrics("gpt-6-sol", tokens=(150_000, 100_000, 50_000, 0)))
    assert result["cost_status"] in {"inferred", "estimated"}
    assert result["cost_usd"] > 0
    assert result["quota_share"] == pytest.approx(0.002)


def test_worker_pool_source_reports_an_unreadable_calibration_as_unknown(tmp_path):
    from gym.dispatch import WorkerPoolTrajectorySource

    def refuse():
        raise RuntimeError("ledger down")

    reader = types.SimpleNamespace(subscription_quota_proofs=refuse)
    source = WorkerPoolTrajectorySource(hq_root=tmp_path, _reader=reader)
    result = source.cell_economics(_metrics("gpt-6-sol"))
    assert result["cost_usd"] is not None
    assert (result["quota_share"], result["quota_reason"]) == (None, "calibration_unavailable")


def test_persisted_synthetic_codex_source_prices_identically(table):
    original = cost.price_cell(_metrics("gpt-5.6-terra"), table)
    synthetic = cost.price_cell(
        _metrics("gpt-5.6-terra", sources=["codex-exec-json+pricing-table-synthetic"]), table
    )
    assert synthetic == original


@pytest.mark.parametrize("output", [100, 0])
def test_input_consuming_zero_output_side_model_is_estimated(table, output):
    result = cost.price_cell(
        _metrics(
            "gpt-5.6-terra",
            tokens=(1000, 0, output, 0),
            observed={"gpt-5.6-terra": output, "gpt-5.6-luna": 0},
        ),
        table,
    )
    assert result["cost_usd"] > 0
    assert result["cost_status"] == "estimated"
    assert "side_model_tokens_priced_at_majority_model" in result["pricing_notes"]
    assert result["side_model_output_share"] == 0.0


@pytest.mark.parametrize(
    "value", [None, True, False, 0, 3, "4.5", "x", float("nan"), float("inf"), [], 10080.0]
)
def test_number_matches_the_snapshot_reader(value):
    # PDK-02 copied the helper so gym.cost imports no Agent OS package.
    from cwp_dispatch.codex_quota_snapshots import _number

    assert cost._number(value) == _number(value)


def test_previous_baseline_matches_the_reconcile():
    from cwp_dispatch.subscription_quota_reconcile import _previous_baseline

    proofs = [
        _proof(reset="2026-01-08T00:00:00Z", tpp=2_000_000.0),
        _proof(reset="2026-01-01T00:00:00Z", tpp=1_000_000.0),
        _proof(reset="2025-12-25T00:00:00Z", status="ok", tpp=5.0),
        _proof(reset="2025-12-18T00:00:00Z", tpp=0.0),
        _proof(reset="2025-12-11T00:00:00Z", tpp="nan"),
        _proof(reset="2025-12-04T00:00:00Z", provider="anthropic", tpp=7.0),
        _proof("other", reset="2025-11-27T00:00:00Z", tpp=9.0),
        _proof(reset="2025-11-20T00:00:00Z", window="primary", tpp=11.0),
    ]
    for limit, model, window, reset in [
        ("codex", "gpt-6-sol", "secondary", cost._ANY_RESET),
        ("codex", "gpt-6-sol", "secondary", "2026-01-05T00:00:00Z"),
        ("codex", "gpt-6-sol", "secondary", "2025-01-01T00:00:00Z"),
        ("codex", "gpt-6-sol", "primary", cost._ANY_RESET),
        ("codex", "other", "secondary", cost._ANY_RESET),
        ("other-limit", "gpt-6-sol", "secondary", cost._ANY_RESET),
    ]:
        assert cost._previous_baseline(proofs, limit, model, window, reset) == (
            _previous_baseline(proofs, limit, model, window, reset)
        )
