"""MEG-02: per-cell ledger metrics — tokens, attempts, wall time, observed model.

The aggregation is pure stdlib (``gym.metrics``); the reader tests drive the
real ``LedgerReader.cost_for_launch`` through the session ledger's canonical
view, against SQLite, a faked bounded ``psql`` read, and (on the VDB lane) a
disposable Postgres ledger.
"""

from __future__ import annotations

import json
import os
import sqlite3
import types
import uuid
from unittest.mock import patch

import pytest
from conftest import FIXTURES_DIR

from gym import metrics, taint
from gym.grader import Trajectory
from gym.ledger import LedgerReader
from gym.suite import run_suite, discover_tasks

MINI_SUITE = FIXTURES_DIR / "mini-suite"
OPUS = "claude-opus-5"
HAIKU = "claude-haiku-4-5-20251001"


def _run(run_id, *, start, end, tokens=(100, 50, 10, 5, 165), by_model=None, **extra):
    names = metrics.TOKEN_COLUMNS
    row = {
        "run_id": run_id,
        "status": "succeeded",
        "started_at": start,
        "ended_at": end,
        "token_usage_cost_usd": 0.5,
        "token_usage_by_model_json": json.dumps(by_model or {OPUS: {"output": tokens[2]}}),
        **dict(zip(names, tokens)),
    }
    row.update(extra)
    return row


def test_single_run_launch():
    m = metrics.launch_metrics(
        [_run("run-1", start="2026-09-29T10:00:00Z", end="2026-09-29T10:02:30Z")],
        worker_class="claude-code",
    )
    assert m["run_ids"] == ["run-1"]
    assert m["attempts"] == 1
    assert (
        m["token_usage_input"],
        m["token_usage_cache_read"],
        m["token_usage_output"],
        m["token_usage_reasoning"],
        m["token_usage_running_total"],
    ) == (100, 50, 10, 5, 165)
    assert m["wall_seconds"] == 150.0
    assert m["worker_class"] == "claude-code"
    assert m["observed_models"] == {OPUS: 10}
    assert m["majority_model"] == OPUS
    assert m["side_models"] == []
    assert m["missing"] == []
    assert m["metrics_complete"] is True


def test_retried_launch_sums_tokens_and_spans_wall_time_across_runs():
    m = metrics.launch_metrics(
        [
            _run("run-1", start="2026-09-29T10:00:00Z", end="2026-09-29T10:01:00Z"),
            _run(
                "run-2",
                start="2026-09-29T10:05:00+00:00",
                end="2026-09-29T10:10:00+00:00",
                tokens=(200, 0, 30, 0, 230),
            ),
        ],
        worker_class="claude-code",
    )
    assert m["run_ids"] == ["run-1", "run-2"]
    assert m["attempts"] == 2
    assert m["token_usage_input"] == 300
    assert m["token_usage_cache_read"] == 50
    assert m["token_usage_output"] == 40
    assert m["token_usage_reasoning"] == 5
    assert m["token_usage_running_total"] == 395
    # First start to last end, including the gap between the attempts.
    assert m["wall_seconds"] == 600.0
    assert m["observed_models"] == {OPUS: 40}
    assert m["metrics_complete"] is True


def test_null_column_stays_null_and_marks_the_cell_incomplete():
    m = metrics.launch_metrics(
        [
            _run("run-1", start="2026-09-29T10:00:00Z", end="2026-09-29T10:01:00Z"),
            _run(
                "run-2",
                start="2026-09-29T10:02:00Z",
                end=None,
                token_usage_reasoning=None,
            ),
        ],
        worker_class="claude-code",
    )
    assert m["token_usage_reasoning"] is None
    assert m["token_usage_input"] == 200
    assert m["wall_seconds"] is None
    assert m["missing"] == ["ended_at", "token_usage_reasoning"]
    assert m["metrics_complete"] is False


def test_no_run_is_unmeasured_not_free():
    m = metrics.launch_metrics([], worker_class=None)
    assert m["attempts"] == 0
    assert all(m[column] is None for column in metrics.TOKEN_COLUMNS)
    assert m["wall_seconds"] is None
    assert m["metrics_complete"] is False
    assert "worker_class" in m["missing"]


def test_claude_code_side_model_does_not_mismatch():
    m = metrics.launch_metrics(
        [
            _run(
                "run-1",
                start="2026-09-29T10:00:00Z",
                end="2026-09-29T10:01:00Z",
                by_model={OPUS: {"output": 900}, HAIKU: {"output": 40}},
            )
        ],
        worker_class="claude-code",
    )
    assert metrics.apply_requested(m, model=OPUS, worker_class="claude-code") is False
    assert m["majority_model"] == OPUS
    assert m["side_models"] == [HAIKU]


def test_different_majority_model_mismatches():
    m = metrics.launch_metrics(
        [
            _run(
                "run-1",
                start="2026-09-29T10:00:00Z",
                end="2026-09-29T10:01:00Z",
                by_model={OPUS: {"output": 40}, "claude-sonnet-5": {"output": 900}},
            )
        ],
        worker_class="claude-code",
    )
    assert metrics.apply_requested(m, model=OPUS, worker_class="claude-code") is True
    assert m["majority_model"] == "claude-sonnet-5"
    assert m["side_models"] == ["claude-sonnet-5"]


def test_a_tie_is_not_a_majority():
    m = metrics.launch_metrics(
        [
            _run(
                "run-1",
                start="2026-09-29T10:00:00Z",
                end="2026-09-29T10:01:00Z",
                by_model={OPUS: {"output": 50}, "claude-sonnet-5": {"output": 50}},
            )
        ],
        worker_class="claude-code",
    )
    assert metrics.apply_requested(m, model=OPUS, worker_class="claude-code") is True


def test_other_worker_class_mismatches():
    m = metrics.launch_metrics(
        [_run("run-1", start="2026-09-29T10:00:00Z", end="2026-09-29T10:01:00Z")],
        worker_class="codex",
    )
    assert metrics.apply_requested(m, model=OPUS, worker_class="claude-code") is True


def test_unverifiable_model_is_incomplete_not_mismatched():
    m = metrics.launch_metrics(
        [
            _run(
                "run-1",
                start="2026-09-29T10:00:00Z",
                end="2026-09-29T10:01:00Z",
                token_usage_by_model_json=None,
            )
        ],
        worker_class=None,
    )
    assert metrics.apply_requested(m, model=OPUS, worker_class="claude-code") is False
    assert m["metrics_complete"] is False
    assert {"token_usage_by_model_json", "worker_class"} <= set(m["missing"])


@pytest.mark.parametrize(
    "observed",
    [
        "claude-opus-5",
        "claude-opus-5-20260901",
        "litellm-local/anthropic/claude-opus-5",
        "CLAUDE-OPUS-5",
    ],
)
def test_model_matches_ignores_routing_prefix_and_snapshot(observed):
    assert metrics.model_matches(observed, OPUS)


def test_model_matches_rejects_another_model():
    assert not metrics.model_matches("claude-opus-5-5", OPUS)
    assert not metrics.model_matches(HAIKU, OPUS)


def test_provider_routes_remain_distinct():
    assert not metrics.model_matches(
        "litellm-local-openai/fireworks/kimi-k3", "litellm-local-openai/kimi-k3"
    )


@pytest.mark.parametrize(
    ("observed", "requested"),
    [
        ("gpt-6.1-sol", "litellm-local-openai/openai/gpt-6.1-sol"),
        ("gpt-6.1-sol-2026-09-30", "litellm-local-openai/openai/gpt-6.1-sol"),
        ("openai/gpt-6.1-sol", "gpt-6.1-sol"),
        ("claude-opus-5-5-20261001", "litellm-local-openai/anthropic/claude-opus-5-5"),
        ("anthropic/claude-sonnet-5-5", "litellm-local-openai/anthropic/claude-sonnet-5-5"),
    ],
)
def test_first_party_routes_match_the_native_model_id(observed, requested):
    assert metrics.model_matches(observed, requested)


def test_first_party_routes_still_distinguish_models():
    assert not metrics.model_matches("gpt-6-sol", "litellm-local-openai/openai/gpt-6.1-sol")
    assert not metrics.model_matches(
        "claude-sonnet-5-5", "litellm-local-openai/anthropic/claude-opus-5-5"
    )


def test_cross_harness_cell_is_not_a_model_mismatch():
    # A foreign harness reports the upstream's bare id for most turns while the
    # proxy route names the rest; both are the requested model.
    m = {
        "observed_models": {"gpt-6.1-sol": 900, "litellm-local-openai/openai/gpt-6.1-sol": 100},
        "worker_class": "oss-pi",
    }
    requested = "litellm-local-openai/openai/gpt-6.1-sol"
    assert metrics.apply_requested(m, model=requested, worker_class="oss-pi") is False
    assert m["side_models"] == []


def test_majority_groups_router_and_snapshot_variants():
    m = metrics.launch_metrics(
        [
            _run(
                "run-1",
                start="2026-09-29T10:00:00Z",
                end="2026-09-29T10:01:00Z",
                by_model={
                    OPUS: {"output": 500},
                    "litellm-local-openai/claude-opus-5-20260901": {"output": 500},
                    "claude-sonnet-5": {"output": 600},
                },
            )
        ],
        worker_class="claude-code",
    )
    assert m["majority_model"] == OPUS
    assert metrics.apply_requested(m, model=OPUS, worker_class="claude-code") is False


def test_by_model_breakdown_decoded_by_postgres_is_accepted():
    run = _run("run-1", start="2026-09-29T10:00:00Z", end="2026-09-29T10:01:00Z")
    run["token_usage_by_model_json"] = {OPUS: {"output": 10}}
    assert metrics.launch_metrics([run], worker_class="claude-code")["observed_models"] == {
        OPUS: 10
    }


# --- LedgerReader.cost_for_launch over the canonical view -----------------


def _sqlite_ledger(runs, *, worker_class="claude-code"):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE launch_requests (launch_request_id TEXT, worker_class TEXT)")
    conn.execute(
        "CREATE TABLE worker_runs (run_id TEXT, launch_request_id TEXT, status TEXT, "
        "started_at TEXT, ended_at TEXT, token_usage_input INTEGER, "
        "token_usage_cache_read INTEGER, token_usage_output INTEGER, "
        "token_usage_reasoning INTEGER, token_usage_running_total INTEGER, "
        "token_usage_cost_usd REAL, token_usage_by_model_json TEXT, "
        "token_usage_source TEXT, resolved_reasoning_level TEXT)"
    )
    conn.execute("INSERT INTO launch_requests VALUES ('lrq-1', ?)", (worker_class,))
    for run in runs:
        row = {"launch_request_id": "lrq-1", **run}
        columns = ", ".join(row)
        conn.execute(
            f"INSERT INTO worker_runs ({columns}) VALUES ({', '.join('?' for _ in row)})",
            tuple(row.values()),
        )
    return types.SimpleNamespace(conn=conn)


def test_reader_sums_a_retried_launch_on_the_sqlite_ledger():
    db = _sqlite_ledger(
        [
            _run("run-2", start="2026-09-29T10:05:00Z", end="2026-09-29T10:10:00Z"),
            _run(
                "run-1",
                start="2026-09-29T10:00:00Z",
                end="2026-09-29T10:01:00Z",
                status="failed",
                token_usage_output=None,
            ),
        ]
    )
    reader = LedgerReader(db, target_resolver=lambda: types.SimpleNamespace(backend="sqlite"))
    cost = reader.cost_for_launch("lrq-1")
    assert (cost["runs"], cost["costUsd"], cost["inputTokens"]) == (2, 1.0, 200)
    # TOC-05's summary no longer reads a NULL column as zero either.
    assert cost["outputTokens"] is None
    m = cost["metrics"]
    assert m["run_ids"] == ["run-1", "run-2"]
    assert m["attempts"] == 2
    assert m["wall_seconds"] == 600.0
    assert m["token_usage_input"] == 200
    assert m["token_usage_output"] is None
    assert m["worker_class"] == "claude-code"
    assert m["metrics_complete"] is False
    assert reader.cost_for_launch("lrq-unknown") is None


def test_reader_keeps_run_metrics_when_worker_class_read_fails():
    db = _sqlite_ledger([_run("run-1", start="2026-09-29T10:00:00Z", end="2026-09-29T10:01:00Z")])
    reader = LedgerReader(db, target_resolver=lambda: types.SimpleNamespace(backend="sqlite"))
    reader.worker_class_for_launch = lambda _lrq: (_ for _ in ()).throw(TimeoutError())

    cost = reader.cost_for_launch("lrq-1")
    assert cost["metrics"]["run_ids"] == ["run-1"]
    assert cost["metrics"]["token_usage_output"] == 10
    assert cost["metrics"]["worker_class"] is None
    assert "worker_class" in cost["metrics"]["missing"]


def test_reader_uses_the_bounded_psql_path_on_postgres():
    seen = {}
    class_reads = []

    def fake_rows(target, select, *, variables=None):
        if "FROM launch_requests" in select:
            class_reads.append((target, select, variables))
            return [{"worker_class": "claude-code"}]
        seen.update(target=target, select=select, variables=variables)
        # row_to_json hands back ISO timestamps and a decoded jsonb breakdown.
        return [
            {
                **_run(
                    "run-1",
                    start="2026-09-29T10:00:00+00:00",
                    end="2026-09-29T10:00:30.5+00:00",
                ),
                "token_usage_by_model_json": {OPUS: {"output": 10}},
            }
        ]

    class RefusingCompatConnection:
        def execute(self, *_args):
            raise AssertionError("Postgres worker and class reads must not use compatibility SQL")

    target = types.SimpleNamespace(backend="postgres", dsn="postgresql://fixture")
    reader = LedgerReader(
        types.SimpleNamespace(conn=RefusingCompatConnection()), target_resolver=lambda: target
    )
    with patch("session_ledger.mcp_call_reads.psql_read_only_rows", fake_rows):
        cost = reader.cost_for_launch("lrq-'1")
    assert seen["target"] is target
    assert ":'launch_request_id'" in seen["select"]
    assert "lrq-" not in seen["select"]
    assert seen["variables"] == {"launch_request_id": "lrq-'1"}
    assert cost["metrics"]["wall_seconds"] == 30.5
    assert cost["metrics"]["observed_models"] == {OPUS: 10}
    assert cost["metrics"]["metrics_complete"] is True
    assert len(class_reads) == 1
    assert class_reads[0][0] is target
    assert class_reads[0][2] == {"launch_request_id": "lrq-'1"}
    assert cost["metrics"]["worker_class"] == "claude-code"


def test_reader_postgres_failure_raises_instead_of_falling_back():
    def failing_rows(target, select, *, variables=None):
        raise TimeoutError("psql timed out")

    class RefusingConn:
        def execute(self, *_args):
            raise AssertionError("a failed Postgres read must not fall back to SQLite")

    target = types.SimpleNamespace(backend="postgres", dsn="postgresql://fixture")
    reader = LedgerReader(
        types.SimpleNamespace(conn=RefusingConn()), target_resolver=lambda: target
    )
    with patch("session_ledger.mcp_call_reads.psql_read_only_rows", failing_rows):
        with pytest.raises(TimeoutError):
            reader.cost_for_launch("lrq-1")


#: A migrated, disposable Postgres ledger for the VDB-lane reader test. The
#: repo-root pytest plugin strips every ``AGENT_OS_SESSION_LEDGER_*`` variable,
#: so the lane names its database here instead; the test refuses any database
#: that is not a disposable test ledger by name.
_VDB_DSN_ENV = "GYM_LEDGER_TEST_DSN"


def _disposable_postgres_dsn() -> str | None:
    dsn = os.environ.get(_VDB_DSN_ENV)
    if not dsn or os.environ.get("AGENT_OS_TEST_ALLOW_PG") != "1":
        return None
    from session_ledger.db_postgres import require_disposable_postgres_test_database

    require_disposable_postgres_test_database(dsn)
    return dsn


def test_reader_on_the_disposable_postgres_vdb_lane(monkeypatch):
    dsn = _disposable_postgres_dsn()
    if dsn is None:
        pytest.skip(
            f"set {_VDB_DSN_ENV} to a disposable migrated ledger and AGENT_OS_TEST_ALLOW_PG=1"
        )
    from session_ledger.db_path import resolve_ledger_target
    from session_ledger.db_runtime import open_ledger_for_runtime

    monkeypatch.delenv("AGENT_OS_SESSION_LEDGER_BACKEND", raising=False)
    monkeypatch.setenv("AGENT_OS_SESSION_LEDGER_POSTGRES_RUNTIME", "on")
    monkeypatch.setenv("AGENT_OS_SESSION_LEDGER_DSN", dsn)
    monkeypatch.setenv("AGENT_OS_SESSION_LEDGER_DSN_DIRECT", dsn)
    assert resolve_ledger_target().backend == "postgres"

    lrq = f"lrq-meg02-{uuid.uuid4().hex[:12]}"
    db = open_ledger_for_runtime(dsn=dsn)
    try:
        conn = db.conn
        conn.execute(
            "INSERT INTO launch_requests (launch_request_id, adapter, adapter_request_key, "
            "idempotency_key, status, requested_at, auth_contract_json, "
            "request_payload_json, metadata_json, created_at, updated_at, worker_class) "
            "VALUES (?, 'gym-test', ?, ?, 'running', ?, '{}', '{}', '{}', ?, ?, 'claude-code')",
            (lrq, lrq, lrq, *(["2026-09-29T10:00:00Z"] * 3)),
        )
        for run_id, start, end, reasoning, by_model in (
            (
                f"{lrq}-b",
                "2026-09-29T10:05:00Z",
                "2026-09-29T10:10:00Z",
                7,
                {OPUS: {"output": 900}, HAIKU: {"output": 40}},
            ),
            (
                f"{lrq}-a",
                "2026-09-29T10:00:00Z",
                "2026-09-29T10:01:00Z",
                None,
                {OPUS: {"output": 10}},
            ),
        ):
            conn.execute(
                "INSERT INTO worker_runs (run_id, launch_request_id, worker_kind, "
                "runtime_substrate, status, started_at, ended_at, created_at, updated_at, "
                "token_usage_input, token_usage_cache_read, token_usage_output, "
                "token_usage_reasoning, token_usage_running_total, token_usage_cost_usd, "
                "token_usage_by_model_json) "
                "VALUES (?, ?, 'claude-code', 'local', 'succeeded', ?, ?, ?, ?, "
                "100, 50, 10, ?, 160, 0.5, ?)",
                (run_id, lrq, start, end, start, start, reasoning, json.dumps(by_model)),
            )
        conn.commit()

        reader = LedgerReader(db, target_resolver=resolve_ledger_target)
        cost = reader.cost_for_launch(lrq)
        m = cost["metrics"]
        assert m["run_ids"] == [f"{lrq}-a", f"{lrq}-b"]
        assert m["attempts"] == 2
        assert m["token_usage_input"] == 200
        assert m["token_usage_reasoning"] is None
        assert m["wall_seconds"] == 600.0
        assert m["observed_models"] == {HAIKU: 40, OPUS: 910}
        assert m["worker_class"] == "claude-code"
        assert m["metrics_complete"] is False
        assert metrics.apply_requested(m, model=OPUS, worker_class="claude-code") is False
        assert m["side_models"] == [HAIKU]
    finally:
        try:
            db.conn.execute("DELETE FROM worker_runs WHERE launch_request_id = ?", (lrq,))
            db.conn.execute("DELETE FROM launch_requests WHERE launch_request_id = ?", (lrq,))
            db.conn.commit()
        finally:
            db.close()


# --- the suite and its result JSON -----------------------------------------


class _MappingSource:
    def __init__(self, by_task):
        self.by_task = by_task

    def run(self, task):
        return self.by_task[task.id]


def _metered(by_model, *, worker_class="claude-code"):
    return metrics.launch_metrics(
        [
            _run(
                "run-1",
                start="2026-09-29T10:00:00Z",
                end="2026-09-29T10:01:00Z",
                by_model=by_model,
            )
        ],
        worker_class=worker_class,
    )


def test_run_suite_flags_the_mismatched_cell_and_keeps_its_verdict():
    tasks = discover_tasks(MINI_SUITE)
    source = _MappingSource(
        {
            "mini-pass": Trajectory(
                terminal_status="succeeded",
                submitted_result={"answer": "ok"},
                metrics=_metered({OPUS: {"output": 900}, HAIKU: {"output": 40}}),
            ),
            "mini-fail": Trajectory(
                terminal_status="succeeded",
                submitted_result={"answer": "ok"},
                metrics=_metered({"claude-sonnet-5": {"output": 900}}),
            ),
        }
    )
    result = run_suite(tasks, source, taint_mode=taint.ENFORCE, model=OPUS)
    by_task = {g.cell.task_id: g.cell for g in result.graded}
    assert by_task["mini-pass"].model_mismatch is False
    assert by_task["mini-fail"].model_mismatch is True
    assert by_task["mini-fail"].outcome == "passed"
    summary = json.loads(json.dumps(result.summary_dict()))
    assert summary["model_mismatches"] == 1
    assert summary["metrics_incomplete"] == 0
    cells = {cell["task"]: cell for cell in summary["cells"]}
    assert cells["mini-pass"]["model_mismatch"] is False
    assert cells["mini-pass"]["metrics"]["side_models"] == [HAIKU]
    assert cells["mini-pass"]["metrics"]["attempts"] == 1
    assert cells["mini-fail"]["model_mismatch"] is True


def test_run_suite_counts_unmeasured_cells():
    tasks = [t for t in discover_tasks(MINI_SUITE) if t.id == "mini-pass"]
    source = _MappingSource(
        {
            "mini-pass": Trajectory(
                terminal_status="succeeded",
                submitted_result={"answer": "ok"},
                metrics=metrics.launch_metrics([], worker_class="claude-code"),
            )
        }
    )
    result = run_suite(tasks, source, taint_mode=taint.ENFORCE, model=OPUS)
    summary = result.summary_dict()
    assert summary["metrics_incomplete"] == 1
    assert summary["model_mismatches"] == 0
    assert summary["cells"][0]["metrics"]["metrics_complete"] is False


def test_cell_without_metrics_keeps_its_old_shape():
    tasks = [t for t in discover_tasks(MINI_SUITE) if t.id == "mini-pass"]
    source = _MappingSource(
        {"mini-pass": Trajectory(terminal_status="succeeded", submitted_result={"answer": "ok"})}
    )
    cell = run_suite(tasks, source, taint_mode=taint.ENFORCE, model=OPUS).graded[0].cell
    assert "metrics" not in cell.to_dict()
    assert "model_mismatch" not in cell.to_dict()


@pytest.mark.parametrize("bucket", ["live-floor", "unknown", "<synthetic>"])
def test_accounting_bucket_does_not_outvote_captured_model(bucket):
    row = _run(
        "accounting-floor",
        start="2026-09-29T10:00:00Z",
        end="2026-09-29T10:01:00Z",
        by_model={OPUS: {"output": 10}, bucket: {"output": 999}},
    )
    m = metrics.launch_metrics([row], worker_class="claude-code")
    assert m["majority_model"] == OPUS
    assert m["observed_models"] == {OPUS: 10}
    assert m["side_models"] == []
    assert m["token_usage_running_total"] == row["token_usage_running_total"]
    assert m["metrics_complete"] is True
    assert metrics.apply_requested(m, model=OPUS, worker_class="claude-code") is False


def test_accounting_only_usage_remains_unverifiable():
    row = _run(
        "floor-only",
        start="2026-09-29T10:00:00Z",
        end="2026-09-29T10:01:00Z",
        by_model={"live-floor": {"output": 10}, "unknown": {"output": 3}},
    )
    m = metrics.launch_metrics([row], worker_class="claude-code")
    assert m["majority_model"] is None
    assert m["observed_models"] == {}
    assert m["metrics_complete"] is False
    assert "token_usage_by_model_json" in m["missing"]
    assert m["token_usage_running_total"] == row["token_usage_running_total"]


@pytest.mark.parametrize(
    "key",
    [
        "unknown",
        "live-floor",
        "<synthetic>",
        "",
        " ",
        OPUS,
        HAIKU,
        "fireworks/model",
        "other-model",
    ],
)
def test_accounting_key_contract_matches_shared_spend_analytics(key):
    analytics = pytest.importorskip("cwp_dispatch.budget_analytics_core")

    assert metrics._is_model_key(key) == analytics.is_model_key(key)
