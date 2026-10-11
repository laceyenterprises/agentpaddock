"""TOC-05: the compression A/B matrix gates, aggregation, and CLI flags."""

from __future__ import annotations

import json
import types
from unittest.mock import patch

import pytest

from gym import cli, outcomes, toc_matrix


def _status(**overrides):
    payload = {
        "providerStatuses": [
            {"provider": "openai", "authPath": "oauth", "state": "ok"},
            {"provider": "anthropic", "authPath": "oauth", "state": "ok"},
        ],
        "suspendedLaunchRequests": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_quota_gate_is_healthy_only_on_a_clean_structured_status():
    assert toc_matrix.quota_gate(0, _status()).state == toc_matrix.QUOTA_HEALTHY


def test_quota_gate_suspends_on_an_exhausted_provider():
    rows = [{"provider": "openai", "authPath": "oauth", "state": "EXHAUSTED"}]
    gate = toc_matrix.quota_gate(0, _status(providerStatuses=rows))
    assert gate.state == toc_matrix.QUOTA_SUSPENDED
    assert "openai/oauth" in gate.reason


def test_quota_gate_suspends_on_an_operator_suspension_or_suspended_lrq():
    rows = [{"provider": "openai", "authPath": "oauth", "state": "ok", "operatorSuspendUntil": "x"}]
    assert (
        toc_matrix.quota_gate(0, _status(providerStatuses=rows)).state == toc_matrix.QUOTA_SUSPENDED
    )
    lrqs = [{"launchRequestId": "lrq-1"}]
    assert (
        toc_matrix.quota_gate(0, _status(suspendedLaunchRequests=lrqs)).state
        == toc_matrix.QUOTA_SUSPENDED
    )


def test_quota_gate_text_mentioning_suspended_is_not_a_suspension():
    # The text render always prints "Quota-suspended LRQs awaiting resume: 0";
    # only structured state decides.
    assert toc_matrix.quota_gate(0, _status()).state == toc_matrix.QUOTA_HEALTHY
    text = "  Quota-suspended LRQs awaiting resume: 0\n"
    assert toc_matrix.quota_gate(0, text).state == toc_matrix.QUOTA_UNKNOWN


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [(1, _status()), (0, ""), (0, "[]"), (0, json.dumps({"providerStatuses": []}))],
)
def test_quota_gate_unreadable_status_is_unknown(returncode, stdout):
    assert toc_matrix.quota_gate(returncode, stdout).state == toc_matrix.QUOTA_UNKNOWN


def test_arm_gate_runs_off_and_spill_and_refuses_rtk():
    assert toc_matrix.arm_omission_reason("off") is None
    assert toc_matrix.arm_omission_reason("spill", ("git", "pytest")) is None
    assert "RTK-ELIGIBILITY.md" in toc_matrix.arm_omission_reason("rtk", ("git", "pytest"))
    assert "compress nothing" in toc_matrix.arm_omission_reason("spill", ())
    assert "unknown arm" in toc_matrix.arm_omission_reason("zstd", ("git",))
    assert set(toc_matrix.LAUNCH_ARMS) | set(toc_matrix.OMITTED_ARM_REASONS) == set(toc_matrix.ARMS)


def test_fidelity_note_records_that_spill_runs_as_a_measurement_arm():
    note = toc_matrix.fidelity_note("spill", {"noop"})
    assert note["admitted"] is False and "measurement only" in note["note"]
    assert toc_matrix.fidelity_note("off", {"noop"}) is None


def _stub_ledger(monkeypatch, toc_eval, estimate=None):
    estimate = estimate or {
        "basis": "class",
        "sampleSize": 1,
        "totalTokens": 10,
        "inputTokens": 8,
        "cacheReadTokens": 0,
        "outputTokens": 2,
    }
    monkeypatch.setattr(
        toc_eval, "_estimates", lambda cells, _days: {c: dict(estimate) for c in cells}
    )
    monkeypatch.setattr(toc_eval, "_host_config", lambda: {"path": "p", "compressionSha256": "h"})
    monkeypatch.setattr(
        toc_eval,
        "_quota_once",
        lambda *_: toc_matrix.QuotaGate(toc_matrix.QUOTA_HEALTHY, "ok"),
    )


def _attempt(verdict, cost, arm="off", lrq=None):
    return {
        "workerClass": "codex",
        "model": "gpt-6-sol",
        "arm": arm,
        "verdict": verdict,
        "costUsd": cost,
        "launchRequestId": lrq,
        "armVerified": True,
    }


def test_cost_per_success_counts_every_attempt_but_only_graded_passes():
    # A completed-but-incorrect attempt and a failed worker run both cost money
    # and neither is a pass.
    arms = toc_matrix.aggregate(
        [
            _attempt(outcomes.PASSED, 1.0, lrq="a"),
            _attempt(outcomes.FAILED, 2.0, lrq="b"),
            _attempt(outcomes.AGENT_ERROR, 3.0, lrq="c"),
        ]
    )
    (arm,) = arms
    assert arm["sampleSize"] == 3
    assert arm["passes"] == 1
    assert arm["totalCostUsd"] == 6.0
    assert arm["costPerSuccessUsd"] == 6.0
    assert arm["successRate"] == pytest.approx(1 / 3, abs=1e-6)
    assert arm["attemptCostStdevUsd"] == 1.0
    assert arm["launchRequestIds"] == ["a", "b", "c"]
    assert arm["informative"] is True


def test_zero_passes_leaves_cost_per_success_undefined_not_zero():
    (arm,) = toc_matrix.aggregate([_attempt(outcomes.FAILED, 1.5), _attempt(outcomes.FAILED, 0)])
    assert arm["costPerSuccessUsd"] is None
    assert arm["costPerSuccessUndefinedReason"] == "no gym-graded passes"
    assert arm["totalCostUsd"] == 1.5


def test_missing_cost_leaves_cost_per_success_undefined():
    (arm,) = toc_matrix.aggregate([_attempt(outcomes.PASSED, 1.0), _attempt(outcomes.PASSED, None)])
    assert arm["costPerSuccessUsd"] is None
    assert "cost missing" in arm["costPerSuccessUndefinedReason"]


def test_compressed_arm_without_attributable_coverage_is_uninformative():
    (arm,) = toc_matrix.aggregate([_attempt(outcomes.PASSED, 1.0, arm="spill")])
    assert arm["informative"] is False
    assert arm["compressedInvocations"] == 0
    assert "no compressed invocation" in arm["uninformativeReason"]
    unverified = {**_attempt(outcomes.PASSED, 1.0), "armVerified": None}
    (arm,) = toc_matrix.aggregate([unverified])
    assert arm["informative"] is False
    assert "not verified" in arm["uninformativeReason"]


def test_run_parser_validates_the_toc_flags():
    parser = cli.build_parser()
    args = parser.parse_args(["run", "--priority", "low", "--compression", "off", "--attempt", "2"])
    assert (args.priority, args.compression, args.attempt) == ("low", "off", 2)
    for bad in (["--priority", "urgent"], ["--compression", "zstd"], ["--attempt", "0"]):
        with pytest.raises(SystemExit):
            parser.parse_args(["run", *bad])


def test_main_forwards_priority_to_the_default_source(monkeypatch):
    seen = {}

    def fake_source(**kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop after source construction")

    monkeypatch.setattr(cli, "_default_source", fake_source)
    with pytest.raises(RuntimeError, match="stop after source construction"):
        cli.main(["run", "--priority", "low"])
    assert seen["priority"] == "low"


class _FakeReader:
    def __init__(self, costs, arms=None, counts=None, usage=None):
        self.costs = costs
        self.arms = arms
        self.counts = counts or {}
        self.usage = usage or {}

    def cost_for_launch(self, launch_request_id):
        return self.costs.get(launch_request_id)

    def compression_arm_for_launch(self, launch_request_id):
        if self.arms is None:
            return {"requested": "off", "recorded": ["off"], "effective": ["off"]}
        return self.arms.get(launch_request_id)

    def toc_counts_for_launch(self, launch_request_id):
        return self.counts.get(launch_request_id, {"rows": 0, "okRows": 0})

    def turn_usage_for_launch(self, launch_request_id):
        return self.usage.get(launch_request_id)


def test_uncaptured_or_in_flight_cost_stays_pending_and_rejoins_later():
    toc_eval = pytest.importorskip("gym.toc_eval")
    # A timed-out cell whose worker is still running, and one whose run has no
    # captured cost: the ledger reports costUsd None, which must not freeze as $0.
    attempts = [
        {**_attempt(outcomes.AGENT_ERROR, None, lrq="open"), "costUsd": None},
        {**_attempt(outcomes.PASSED, None, lrq="uncosted"), "costUsd": None},
    ]
    pending = {
        "open": {"runs": 1, "uncostedRuns": 0, "openRuns": 1, "costUsd": None},
        "uncosted": {"runs": 1, "uncostedRuns": 1, "openRuns": 0, "costUsd": None},
    }
    assert toc_eval._join(attempts, _FakeReader(pending)) == 2
    assert [a["costUsd"] for a in attempts] == [None, None]
    (arm,) = toc_matrix.aggregate(attempts)
    assert arm["costPerSuccessUsd"] is None
    assert "cost missing for 2" in arm["costPerSuccessUndefinedReason"]

    captured = {
        "open": {"runs": 1, "uncostedRuns": 0, "openRuns": 0, "costUsd": 1.5},
        "uncosted": {"runs": 1, "uncostedRuns": 0, "openRuns": 0, "costUsd": 0.5},
    }
    assert toc_eval._join(attempts, _FakeReader(captured)) == 0
    (arm,) = toc_matrix.aggregate(attempts)
    assert arm["totalCostUsd"] == 2.0
    assert arm["costPerSuccessUsd"] == 2.0


def test_excluded_attempts_leave_the_sample_passes_and_cost():
    attempts = [
        _attempt(outcomes.PASSED, 1.0, lrq="a"),
        {**_attempt(outcomes.AGENT_ERROR, None, lrq="slow"), "excluded": True},
        {**_attempt("error", None), "excluded": True},
    ]
    (arm,) = toc_matrix.aggregate(attempts)
    assert (arm["sampleSize"], arm["passes"], arm["totalCostUsd"]) == (1, 1, 1.0)
    assert arm["costPerSuccessUsd"] == 1.0
    assert arm["excludedAttempts"] == 2
    assert arm["excludedLaunchRequestIds"] == ["slow"]

    (only_excluded,) = toc_matrix.aggregate(attempts[1:])
    assert only_excluded["sampleSize"] == 0
    assert only_excluded["costPerSuccessUndefinedReason"] == "no counted attempts"


class _Proc:
    def __init__(self, stdout, returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def test_gym_run_without_a_cell_is_excluded_not_pending_forever(monkeypatch):
    toc_eval = pytest.importorskip("gym.toc_eval")
    monkeypatch.setattr(
        toc_eval.subprocess, "run", lambda *a, **k: _Proc("", 1, "LockDriftError: codex")
    )
    (record,) = toc_eval._run_attempt("codex", "gpt-6-sol", "off", "coding-toc-commands", 1, "low")
    assert record["excluded"] is True
    assert record["launchRequestId"] is None
    assert toc_eval._join([record], _FakeReader({})) == 0


def test_poll_deadline_cell_is_excluded_from_the_arm(monkeypatch):
    toc_eval = pytest.importorskip("gym.toc_eval")
    cell = {
        "verdict": outcomes.AGENT_ERROR,
        "reason": "launch lrq-q did not reach terminal state in 3600.0s",
        "launchRequestId": "lrq-q",
        "terminalStatus": "timeout",
    }
    payload = json.dumps({"cells": [cell]})
    monkeypatch.setattr(toc_eval.subprocess, "run", lambda *a, **k: _Proc(payload))
    (record,) = toc_eval._run_attempt("codex", "gpt-6-sol", "off", "coding-toc-commands", 1, "low")
    assert record["excluded"] is True
    assert "queue time" in record["excludedReason"]
    # Not rejoined: an excluded launch never holds the arm's cost pending.
    assert toc_eval._join([record], _FakeReader({})) == 0


def test_quota_gate_retries_an_unreadable_status_a_bounded_number_of_times():
    toc_eval = pytest.importorskip("gym.toc_eval")
    unknown = toc_matrix.QuotaGate(toc_matrix.QUOTA_UNKNOWN, "x")
    healthy = toc_matrix.QuotaGate(toc_matrix.QUOTA_HEALTHY, "ok")
    reads = iter([unknown, healthy])
    assert toc_eval._quota(read=lambda: next(reads), sleep=lambda _: None) == healthy

    calls = []

    def always_unknown():
        calls.append(1)
        return unknown

    assert toc_eval._quota(read=always_unknown, sleep=lambda _: None) == unknown
    assert len(calls) == toc_eval.QUOTA_READ_ATTEMPTS


def test_rejoin_only_joins_and_writes_without_dispatching(monkeypatch, tmp_path):
    toc_eval = pytest.importorskip("gym.toc_eval")
    out = tmp_path / "matrix.json"
    out.write_text(
        json.dumps(
            {
                "schema": toc_eval.SCHEMA,
                "attempts": [_attempt(outcomes.PASSED, None, lrq="lrq-1")],
                "omittedArms": [],
            }
        )
    )

    def no_dispatch(*_a, **_k):
        raise AssertionError("--rejoin-only must not dispatch or read quota")

    monkeypatch.setattr(toc_eval, "_run_attempt", no_dispatch)
    monkeypatch.setattr(toc_eval, "_quota", no_dispatch)
    monkeypatch.setattr(toc_eval, "_host_config", no_dispatch)
    real_join = toc_eval._join
    reader = _FakeReader({"lrq-1": {"runs": 1, "uncostedRuns": 0, "openRuns": 0, "costUsd": 2.5}})
    monkeypatch.setattr(toc_eval, "_join", lambda attempts: real_join(attempts, reader))

    assert toc_eval.main(["--out", str(out), "--rejoin-only"]) == 1
    assert json.loads(out.read_text())["result"]["tokenAccountingStatus"] == "unresolved"
    (arm,) = json.loads(out.read_text())["arms"]
    assert arm["costPerSuccessUsd"] == 2.5

    with pytest.raises(SystemExit):
        toc_eval.main(["--out", str(out), "--rejoin-only", "--cell", "codex=m"])


def test_quota_is_rechecked_before_every_attempt(monkeypatch, tmp_path):
    toc_eval = pytest.importorskip("gym.toc_eval")
    _stub_ledger(monkeypatch, toc_eval)
    monkeypatch.setattr(toc_eval, "_lock_omission_reason", lambda _cls: None)
    gates = iter(
        [
            toc_matrix.QuotaGate(toc_matrix.QUOTA_HEALTHY, "ok"),
            toc_matrix.QuotaGate(toc_matrix.QUOTA_SUSPENDED, "openai/oauth exhausted"),
        ]
    )
    monkeypatch.setattr(toc_eval, "_quota", lambda *_: next(gates))
    ran = []

    def fake_attempt(worker_class, model, arm, task, attempt, priority):
        ran.append(attempt)
        return [{**_attempt(outcomes.PASSED, 0.0), "arm": arm, "task": task, "attempt": attempt}]

    monkeypatch.setattr(toc_eval, "_run_attempt", fake_attempt)
    monkeypatch.setattr(toc_eval, "_join", lambda attempts, reader=None: 0)
    out = tmp_path / "matrix.json"
    rc = toc_eval.main(
        ["--out", str(out), "--cell", "codex=gpt-6-sol", "--arm", "off", "--attempts", "3"]
    )
    assert rc == 0
    assert ran == [1]
    document = json.loads(out.read_text())
    assert document["lastInvocation"]["quota"]["state"] == toc_matrix.QUOTA_SUSPENDED
    assert document["lastInvocation"]["hostConfig"]["path"] == "p"
    assert len(document["attempts"]) == 1
    assert document["result"]["status"] == "partial"
    assert "quota suspended" in document["result"]["reason"]


def test_a_class_without_a_committed_lock_is_omitted_not_dispatched(monkeypatch, tmp_path):
    toc_eval = pytest.importorskip("gym.toc_eval")
    _stub_ledger(monkeypatch, toc_eval)
    monkeypatch.setattr(toc_eval.locks, "default_locks_dir", lambda: tmp_path / "no-locks")
    assert "lock" in toc_eval._lock_omission_reason("codex")
    rc = toc_eval.main(["--out", str(tmp_path / "m.json"), "--cell", "codex=m", "--dry-run"])
    assert rc == 0


def test_committed_codex_lock_matches_the_worker_class_registry():
    toc_eval = pytest.importorskip("gym.toc_eval")
    assert toc_eval._lock_omission_reason("codex") is None


def test_run_refuses_the_rtk_arm_before_building_a_source(monkeypatch, capsys):
    def no_source(**_kwargs):
        raise AssertionError("rtk must be refused before any dispatch")

    monkeypatch.setattr(cli, "_default_source", no_source)
    assert cli.main(["run", "--compression", "rtk"]) == cli.EXIT_ERROR
    assert "RTK-ELIGIBILITY.md" in capsys.readouterr().err


def test_main_forwards_the_compression_arm_to_the_default_source(monkeypatch):
    seen = {}

    def fake_source(**kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop after source construction")

    monkeypatch.setattr(cli, "_default_source", fake_source)
    with pytest.raises(RuntimeError, match="stop after source construction"):
        cli.main(["run", "--compression", "spill", "--priority", "low"])
    assert seen["compression"] == "spill"


def test_arm_mismatch_needs_request_run_adapter_and_rows_to_agree():
    spill = {"requested": "spill", "recorded": ["spill"], "effective": ["spill"]}
    rows = {"rows": 2, "okRows": 1, "backends": ["spill"], "armSources": ["launch"]}
    assert toc_matrix.arm_mismatch_reason("spill", spill, rows) is None
    assert toc_matrix.arm_mismatch_reason("spill", spill, {"rows": 0}) is None
    assert "dispatch receipts" in toc_matrix.arm_mismatch_reason(
        "spill", {"requested": None, "recorded": ["spill"]}, rows
    )
    assert "worker runs" in toc_matrix.arm_mismatch_reason(
        "spill", {"requested": "spill", "recorded": [None]}, rows
    )
    # A pre-TOCMEASURE-01 adapter ignores the arm and reports none: not verified.
    unapplied = {"requested": "spill", "recorded": ["spill"], "effective": [None]}
    assert "adapters report" in toc_matrix.arm_mismatch_reason("spill", unapplied, rows)
    assert "adapters report" in toc_matrix.arm_mismatch_reason(
        "spill", {"requested": "spill", "recorded": ["spill"]}, {"rows": 0}
    )
    crossed = {**spill, "effective": ["spill", "off"]}
    assert "adapters report" in toc_matrix.arm_mismatch_reason("spill", crossed, rows)
    host_rows = {**rows, "armSources": ["host"]}
    assert "arm source" in toc_matrix.arm_mismatch_reason("spill", spill, host_rows)
    noop_rows = {**rows, "backends": ["noop"]}
    assert "backend" in toc_matrix.arm_mismatch_reason("spill", spill, noop_rows)
    off = {"requested": "off", "recorded": ["off"], "effective": ["off"]}
    assert toc_matrix.arm_mismatch_reason("off", off, {"rows": 0}) is None
    assert "off launch wrote 3" in toc_matrix.arm_mismatch_reason("off", off, {"rows": 3})


def test_join_verifies_the_arm_and_counts_attributed_invocations():
    toc_eval = pytest.importorskip("gym.toc_eval")
    base = {"workerClass": "codex", "model": "gpt-6-sol", "verdict": outcomes.PASSED}
    attempts = [
        {**base, "arm": "spill", "launchRequestId": "s"},
        {**base, "arm": "off", "launchRequestId": "o"},
        {**base, "arm": "spill", "launchRequestId": "wrong"},
        {**base, "arm": "spill", "launchRequestId": "unknown"},
    ]
    reader = _FakeReader(
        {lrq: {"costUsd": 1.0} for lrq in ("s", "o", "wrong")},
        arms={
            "s": {"requested": "spill", "recorded": ["spill"], "effective": ["spill"]},
            "o": {"requested": "off", "recorded": ["off"], "effective": ["off"]},
            "wrong": {"requested": "spill", "recorded": [None]},
        },
        counts={
            "s": {
                "rows": 3,
                "okRows": 2,
                "backends": ["spill"],
                "armSources": ["launch"],
                "bytesIn": 1000,
                "bytesOut": 100,
            }
        },
        usage={
            "s": {"turns": 7, "inputTokens": 5, "cacheReadTokens": 50, "outputTokens": 9},
        },
    )
    assert toc_eval._join(attempts, reader) == 1  # the unknown launch stays pending
    spill, off, wrong, unknown = attempts
    assert (spill["armVerified"], spill["eligibleInvocations"]) == (True, 3)
    assert (spill["compressedInvocations"], spill["turns"]) == (2, 7)
    assert off["armVerified"] is True and off["compressedInvocations"] == 0
    assert off["eligibleInvocations"] is None and off["eligibleReason"]
    assert wrong["excluded"] is True and "arm mismatch" in wrong["excludedReason"]
    assert unknown["armVerified"] is None and not unknown.get("excluded")


def test_aggregate_reports_variance_turns_and_bytes_not_as_the_decision():
    rows = [
        {**_attempt(outcomes.PASSED, 1.0, arm="spill", lrq="a"), "turns": 10},
        {**_attempt(outcomes.PASSED, 3.0, arm="spill", lrq="b"), "turns": 14},
        {**_attempt(outcomes.FAILED, 2.0, arm="spill", lrq="c"), "turns": 30},
    ]
    for row in rows:
        row.update(eligibleInvocations=2, compressedInvocations=1, bytesIn=1000, bytesOut=250)
    (arm,) = toc_matrix.aggregate(rows)
    assert arm["informative"] is True
    assert arm["costPerSuccessUsd"] == 3.0
    assert arm["successRateVariance"] == pytest.approx(1 / 3, abs=1e-6)
    assert arm["attemptCostVarianceUsd"] == 1.0
    assert arm["costPerSuccessVarianceUsd"] > 0
    assert arm["turnsToCompletion"] == {"n": 2, "mean": 12.0, "variance": 8.0}
    assert arm["turnsAllAttempts"]["n"] == 3
    assert (arm["eligibleInvocations"], arm["compressedInvocations"]) == (6, 3)
    assert arm["byteReduction"] == 0.75
    assert arm["byteReductionIsDecisionMetric"] is False


def test_budget_counts_cached_input_once_per_class():
    assert toc_matrix.attempt_tokens("codex", 1000, 900, 10) == 1010
    assert toc_matrix.attempt_tokens("claude-code", 1000, 900, 10) == 1010
    assert toc_matrix.format_budget(1234.4, 5, 67) == "1,234(5):67"


def test_budget_estimate_prefers_the_model_then_the_class():
    samples = [
        {"model": "gpt-5.5", "inputTokens": 100, "cacheReadTokens": 80, "outputTokens": 10},
        {"model": "gpt-5.5", "inputTokens": 300, "cacheReadTokens": 80, "outputTokens": 30},
        {"model": "gpt-6-sol", "inputTokens": 50, "cacheReadTokens": 0, "outputTokens": 5},
    ]
    same = toc_matrix.estimate_attempt_budget("codex", "gpt-6-sol", samples)
    assert (same["basis"], same["sampleSize"], same["totalTokens"]) == ("model", 1, 55)
    fallback = toc_matrix.estimate_attempt_budget("codex", "gpt-7", samples[:2])
    assert (fallback["basis"], fallback["inputTokens"], fallback["totalTokens"]) == (
        "class",
        200,
        220,
    )
    none = toc_matrix.estimate_attempt_budget("codex", "gpt-7", [])
    assert (none["basis"], none["totalTokens"]) == ("none", None)


def test_toc_matrix_accepts_only_low_priority_and_needs_out_unless_dry_run(tmp_path):
    toc_eval = pytest.importorskip("gym.toc_eval")
    out = str(tmp_path / "ab-result.json")
    with pytest.raises(SystemExit):
        toc_eval._parse_args(["--cell", "codex=m", "--out", out, "--priority", "normal"])
    with pytest.raises(SystemExit):
        toc_eval._parse_args(["--cell", "codex=m"])
    assert toc_eval._parse_args(["--cell", "codex=m", "--out", out]).priority == "low"
    assert toc_eval._parse_args(["--cell", "codex=m", "--dry-run"]).out is None


def test_dry_run_prints_cells_budget_and_caps_without_dispatching(monkeypatch, tmp_path, capsys):
    toc_eval = pytest.importorskip("gym.toc_eval")
    estimate = {
        "basis": "class",
        "sampleSize": 4,
        "inputTokens": 1000,
        "cacheReadTokens": 900,
        "outputTokens": 10,
        "totalTokens": 1010,
    }
    _stub_ledger(monkeypatch, toc_eval, estimate)
    monkeypatch.setattr(toc_eval, "_lock_omission_reason", lambda _cls: None)

    def no_dispatch(*_a, **_k):
        raise AssertionError("--dry-run must not dispatch")

    monkeypatch.setattr(toc_eval, "_run_attempt", no_dispatch)
    out = tmp_path / "ab.json"
    argv = ["--out", str(out), "--cell", "codex=gpt-6-sol", "--dry-run"]
    assert toc_eval.main([*argv, "--arm", "off", "--arm", "spill", "--arm", "rtk"]) == 0
    text = capsys.readouterr().out
    assert "codex/gpt-6-sol/off coding-toc-commands: per attempt 1,000(900):10 = 1,010" in text
    assert "per cell 3,000(2,700):30 = 3,030 (basis: class, n=4)" in text
    assert "omitted codex/gpt-6-sol/rtk" in text
    assert "Estimated total: 6,060 tokens" in text
    assert "max total tokens: 12,000,000" in text
    assert "priority: low" in text and "per-attempt poll deadline: 3600s" in text
    assert "Quota now: healthy" in text
    assert not out.exists()


def test_token_budget_stops_the_run_and_reports_it_partial(monkeypatch, tmp_path):
    toc_eval = pytest.importorskip("gym.toc_eval")
    estimate = {
        "basis": "class",
        "sampleSize": 1,
        "inputTokens": 350,
        "cacheReadTokens": 300,
        "outputTokens": 50,
        "totalTokens": 400,
    }
    _stub_ledger(monkeypatch, toc_eval, estimate)
    monkeypatch.setattr(toc_eval, "_lock_omission_reason", lambda _cls: None)
    monkeypatch.setattr(
        toc_eval, "_quota", lambda *_: toc_matrix.QuotaGate(toc_matrix.QUOTA_HEALTHY, "ok")
    )
    ran = []

    def fake_attempt(worker_class, model, arm, task, attempt, priority):
        assert priority == "low"
        ran.append((arm, attempt))
        record = _attempt(outcomes.PASSED, 1.0, arm=arm, lrq=f"{arm}-{attempt}")
        # The ledger's usage is what counts once it is joined: 500 > the estimate.
        record.update(
            inputTokens=450,
            cacheReadTokens=400,
            outputTokens=50,
            turns=3,
            totalTokens=500,
            usageComplete=True,
        )
        return [record]

    monkeypatch.setattr(toc_eval, "_run_attempt", fake_attempt)
    monkeypatch.setattr(toc_eval, "_open_reader", lambda: _FakeReader({}))
    monkeypatch.setattr(toc_eval, "_join", lambda attempts, reader=None: 0)
    out = tmp_path / "ab.json"
    argv = ["--out", str(out), "--cell", "codex=gpt-6-sol", "--arm", "off", "--arm", "spill"]
    assert toc_eval.main([*argv, "--max-total-tokens", "1200"]) == 0
    # 500 + 500 spent; the third attempt's 400 estimate would reach 1400 > 1200.
    assert ran == [("off", 1), ("off", 2)]
    document = json.loads(out.read_text())
    result = document["result"]
    assert result["status"] == "partial"
    assert "token budget" in result["reason"]
    assert (result["plannedAttempts"], result["dispatchedAttempts"]) == (6, 2)
    assert result["tokensSpent"] == 1000
    assert document["attempts"][0]["budgetTokens"] == {"tokens": 500, "source": "ledger"}
    assert document["armNotes"]["spill"]["admitted"] is False


def test_reader_ab_reads_use_the_bounded_psql_path_on_postgres():
    """The A/B reads never touch the compatibility connection on Postgres."""
    from gym.ledger import LedgerReader

    sent = []

    def fake_rows(target, select, *, variables=None):
        sent.append(variables)
        return []

    class RefusingConn:
        def execute(self, *_args):
            raise AssertionError("an A/B read used the compatibility connection")

    target = types.SimpleNamespace(backend="postgres", dsn="postgresql://fixture")
    reader = LedgerReader(
        types.SimpleNamespace(conn=RefusingConn()), target_resolver=lambda: target
    )
    with patch("session_ledger.mcp_call_reads.psql_read_only_rows", fake_rows):
        assert reader.compression_arm_for_launch("lrq-1") is None
        assert reader.toc_counts_for_launch("lrq-1")["rows"] == 0
        assert reader.turn_usage_for_launch("lrq-1") is None
        assert reader.gym_token_samples("codex", "2026-09-01") == []
    assert sent[0] == {"launch_request_id": "lrq-1"}
    assert sent[-1]["project_ref"] == "adversarial-eval-gym"
    assert sent[-1]["worker_class"] == "codex"


@pytest.mark.parametrize("error", [None, "ledger unavailable: read failed"])
def test_unknown_estimate_never_dispatches(monkeypatch, tmp_path, error):
    toc_eval = pytest.importorskip("gym.toc_eval")
    estimate = {"basis": "none", "sampleSize": 0, "totalTokens": None}
    if error:
        estimate["error"] = error
    _stub_ledger(monkeypatch, toc_eval, estimate)
    monkeypatch.setattr(toc_eval, "_lock_omission_reason", lambda _: None)
    monkeypatch.setattr(toc_eval, "_quota", lambda *_: toc_matrix.quota_gate(0, _status()))

    def refuse(*args):
        pytest.fail("missing estimate must stop dispatch")

    monkeypatch.setattr(toc_eval, "_run_attempt", refuse)
    out = tmp_path / "matrix.json"
    assert (
        toc_eval.main(
            ["--out", str(out), "--cell", "codex=m", "--arm", "off", "--max-total-tokens", "1"]
        )
        == 1
    )
    result = json.loads(out.read_text())["result"]
    assert result["status"] == "partial" and result["dispatchedAttempts"] == 0


def test_failed_estimate_read_reports_unknown(monkeypatch):
    toc_eval = pytest.importorskip("gym.toc_eval")

    def fail():
        raise OSError("ledger offline")

    monkeypatch.setattr(toc_eval, "_open_reader", fail)
    estimate = toc_eval._estimates([("codex", "m")], 30)[("codex", "m")]
    assert estimate["totalTokens"] is None and "ledger offline" in estimate["error"]


def test_reservation_survives_incomplete_usage_and_rejoins():
    toc_eval = pytest.importorskip("gym.toc_eval")
    record = {
        **_attempt(outcomes.PASSED, 1, lrq="a"),
        "reservationTokens": 500,
        "inputTokens": None,
        "cacheReadTokens": None,
        "outputTokens": 0,
        "turns": 3,
        "usageComplete": False,
        "totalTokens": None,
    }
    assert toc_eval._attempt_tokens([record], {}) == (500, "estimate")
    reader = _FakeReader(
        {},
        usage={
            "a": {
                "turns": 3,
                "inputTokens": 100,
                "cacheReadTokens": 50,
                "outputTokens": 10,
                "totalTokens": 110,
                "usageComplete": True,
            }
        },
    )
    toc_eval._join([record], reader)
    assert toc_eval._attempt_tokens([record], {}) == (110, "ledger")


@pytest.mark.parametrize(
    ("arm", "counts"),
    [
        ("off", {"rows": 1, "okRows": 1, "backends": ["spill"], "armSources": ["launch"]}),
        ("spill", {"rows": 1, "okRows": 1, "backends": ["spill"], "armSources": ["host"]}),
        ("spill", {"rows": 1, "okRows": 1, "backends": ["rtk"], "armSources": ["launch"]}),
    ],
)
def test_late_journals_invalidate_a_previously_verified_arm(arm, counts):
    toc_eval = pytest.importorskip("gym.toc_eval")
    record = _attempt(outcomes.PASSED, 1, arm=arm, lrq="a")
    reader = _FakeReader({}, arms={"a": {"requested": arm, "recorded": [arm], "effective": [arm]}})
    toc_eval._join([record], reader)
    assert record["armVerified"] is True
    reader.counts["a"] = counts
    toc_eval._join([record], reader)
    assert record["armVerified"] is False and record["excluded"] is True
    assert toc_matrix.aggregate([record])[0]["informative"] is False


@pytest.mark.parametrize("failure", ["open", "read"])
def test_accepted_launch_is_persisted_before_enrichment_failure(monkeypatch, tmp_path, failure):
    toc_eval = pytest.importorskip("gym.toc_eval")
    _stub_ledger(monkeypatch, toc_eval)
    monkeypatch.setattr(toc_eval, "_lock_omission_reason", lambda _: None)
    monkeypatch.setattr(toc_eval, "_quota", lambda *_: toc_matrix.quota_gate(0, _status()))
    out = tmp_path / "matrix.json"
    calls = []

    def run(*args):
        calls.append(args)
        return [_attempt(outcomes.PASSED, None, lrq="accepted")]

    def fail(*args):
        saved = json.loads(out.read_text())["attempts"][0]
        assert saved["launchRequestId"] == "accepted"
        assert saved["reservationTokens"] == 10
        raise OSError("ledger offline")

    reader = _FakeReader({"accepted": {"costUsd": 1}})
    if failure == "read":
        monkeypatch.setattr(reader, "cost_for_launch", fail)
    monkeypatch.setattr(toc_eval, "_run_attempt", run)
    monkeypatch.setattr(toc_eval, "_open_reader", fail if failure == "open" else lambda: reader)
    assert toc_eval.main(["--out", str(out), "--cell", "codex=m", "--arm", "off"]) == 1
    saved = json.loads(out.read_text())
    assert len(calls) == 1 and saved["result"]["status"] == "partial"
    assert "ledger offline" in saved["result"]["reason"]
    reader = _FakeReader({"accepted": {"costUsd": 1}})
    monkeypatch.setattr(toc_eval, "_open_reader", lambda: reader)
    assert toc_eval.main(["--out", str(out), "--rejoin-only"]) == 1
    assert json.loads(out.read_text())["result"]["tokenAccountingStatus"] == "unresolved"
    assert len(calls) == 1


def test_join_validates_and_reports_one_journal_snapshot():
    toc_eval = pytest.importorskip("gym.toc_eval")

    class Reader(_FakeReader):
        reads = 0

        def toc_counts_for_launch(self, launch_request_id):
            self.reads += 1
            assert self.reads == 1
            return {"rows": 1, "okRows": 1, "backends": ["spill"], "armSources": ["launch"]}

    reader = Reader(
        {}, arms={"a": {"requested": "spill", "recorded": ["spill"], "effective": ["spill"]}}
    )
    record = _attempt(outcomes.PASSED, 1, arm="spill", lrq="a")
    toc_eval._join([record], reader)
    assert record["armVerified"] and record["compressedInvocations"] == 1


def test_later_worker_run_invalidates_verified_arm():
    toc_eval = pytest.importorskip("gym.toc_eval")
    record = _attempt(outcomes.PASSED, 1, lrq="a")
    reader = _FakeReader({})
    toc_eval._join([record], reader)
    assert record["armVerified"] is True
    reader.arms = {
        "a": {"requested": "off", "recorded": ["off", "spill"], "effective": ["off", "spill"]}
    }
    toc_eval._join([record], reader)
    assert record["excluded"] and not record["armVerified"]


@pytest.mark.parametrize("field", ["recorded", "effective"])
def test_missing_sibling_arm_excludes_attempt(field):
    launch = {"requested": "spill", "recorded": ["spill", "spill"], "effective": ["spill", "spill"]}
    launch[field][1] = None
    assert toc_matrix.arm_mismatch_reason("spill", launch, {}) is not None


def test_read_only_reader_resolves_postgres_without_opening_database():
    from gym.ledger import LedgerReader

    target = types.SimpleNamespace(backend="postgres")
    with (
        patch("gym.ledger._ledger_target", return_value=target),
        patch("gym.ledger._open_ledger", side_effect=AssertionError("must not open")),
    ):
        assert LedgerReader.open_read_only()._read_args() == (None, target)


def test_read_only_reader_does_not_create_missing_sqlite(tmp_path):
    from gym.ledger import LedgerReader

    path = tmp_path / "absent" / "ledger.sqlite"
    target = types.SimpleNamespace(backend="sqlite", db_path=path)
    with patch("gym.ledger._ledger_target", return_value=target):
        with pytest.raises(FileNotFoundError):
            LedgerReader.open_read_only()
    assert not path.parent.exists()


def test_read_only_reader_cannot_write_sqlite(tmp_path):
    import sqlite3
    from gym.ledger import LedgerReader

    path = tmp_path / "ledger.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE history (id)")
    target = types.SimpleNamespace(backend="sqlite", db_path=path)
    with patch("gym.ledger._ledger_target", return_value=target):
        reader = LedgerReader.open_read_only()
    conn, _ = reader._read_args()
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("INSERT INTO history VALUES (1)")
    conn.close()


@pytest.mark.parametrize(
    "worker_class, state, expected",
    [
        ("codex", "ok", toc_matrix.QUOTA_HEALTHY),
        ("claude-code", "ok", toc_matrix.QUOTA_SUSPENDED),
        ("codex", "unknown", toc_matrix.QUOTA_UNKNOWN),
    ],
)
def test_cell_scoped_quota(monkeypatch, worker_class, state, expected):
    from gym import toc_eval

    payload = _status(
        providerStatuses=[
            {"provider": "openai", "authPath": "oauth", "state": state},
            {
                "provider": "anthropic",
                "authPath": "oauth",
                "state": "ok",
                "operatorSuspendUntil": "2099-01-01",
            },
        ],
        suspendedLaunchRequests=[{"workerClass": "claude-code"}],
    )
    monkeypatch.setattr(
        toc_eval.subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=payload),
    )
    assert toc_eval._quota_once(worker_class).state == expected


def test_allow_unestimated_stops_on_actual_cap(monkeypatch, tmp_path):
    from gym import toc_eval

    _stub_ledger(monkeypatch, toc_eval, {"basis": "none", "sampleSize": 0, "totalTokens": None})
    monkeypatch.setattr(toc_eval, "_lock_omission_reason", lambda _: None)
    monkeypatch.setattr(toc_eval, "_quota", lambda *_: toc_matrix.QuotaGate("healthy", "ok"))
    ran = []

    def run(*args):
        ran.append(args)
        return [
            {**_attempt(outcomes.PASSED, 0, lrq="test"), "usageComplete": True, "totalTokens": 100}
        ]

    monkeypatch.setattr(toc_eval, "_run_attempt", run)
    monkeypatch.setattr(toc_eval, "_join", lambda *a, **k: 0)
    out = tmp_path / "matrix.json"
    assert (
        toc_eval.main(
            [
                "--out",
                str(out),
                "--cell",
                "codex=m",
                "--arm",
                "off",
                "--allow-unestimated",
                "--max-total-tokens",
                "50",
            ]
        )
        == 0
    )
    assert len(ran) == 1
    result = json.loads(out.read_text())["result"]
    assert result["status"] == "partial" and result["tokensSpent"] == 100


def test_mixed_matrix_stops_before_suspended_claude_cell(monkeypatch, tmp_path):
    from gym import toc_eval

    _stub_ledger(monkeypatch, toc_eval)
    monkeypatch.setattr(
        toc_eval,
        "_plan",
        lambda _: ([("codex", "m", "off", "task"), ("claude-code", "m", "off", "task")], []),
    )

    def quota(worker_class):
        from cwp_dispatch.quota_accounts import class_quota_requirements

        return toc_matrix.quota_gate(
            0,
            _status(
                providerStatuses=[
                    {"provider": "openai", "authPath": "oauth", "state": "ok"},
                    {"provider": "anthropic", "authPath": "oauth", "state": "exhausted"},
                ]
            ),
            requirements=class_quota_requirements(worker_class),
            worker_class=worker_class,
        )

    monkeypatch.setattr(toc_eval, "_quota", quota)
    ran = []

    def run(worker_class, *args):
        ran.append(worker_class)
        return [_attempt(outcomes.PASSED, 0)]

    monkeypatch.setattr(toc_eval, "_run_attempt", run)
    monkeypatch.setattr(toc_eval, "_join", lambda *a, **k: 0)
    out = tmp_path / "mixed.json"
    assert (
        toc_eval.main(
            ["--out", str(out), "--cell", "codex=m", "--cell", "claude-code=m", "--attempts", "1"]
        )
        == 0
    )
    assert ran == ["codex"]
    assert "anthropic/oauth" in json.loads(out.read_text())["result"]["reason"]


@pytest.mark.parametrize("effective", [["spill"], [None]])
def test_rejoin_rechecks_arm_exclusions_and_names_missing_effective(effective):
    from gym import toc_eval

    record = _attempt(outcomes.PASSED, 2, arm="spill", lrq="a")
    record.update(excluded=True, excludedReason="arm mismatch: launch request records arm None")
    reader = _FakeReader(
        {}, arms={"a": {"requested": "spill", "recorded": ["spill"], "effective": effective}}
    )
    toc_eval._join([record], reader)
    if effective == ["spill"]:
        assert record["armVerified"] is True
        assert not record.get("excluded")
        assert toc_matrix.aggregate([record])[0]["sampleSize"] == 1
    else:
        assert record["armVerified"] is None
        assert record["armVerificationReason"] == "effective_arm_not_reported"
        assert record["excluded"] is True


@pytest.mark.parametrize("completed", [False, True])
def test_deadline_recovery_requires_a_fresh_grade(completed):
    from gym import toc_eval

    class Reader(_FakeReader):
        def completed_gym_grade(self, lrq, task):
            assert (lrq, task) == ("a", "coding-toc-commands")
            return (
                {"verdict": outcomes.PASSED, "reason": "regraded", "terminalStatus": "succeeded"}
                if completed
                else None
            )

    record = _attempt(outcomes.AGENT_ERROR, None, lrq="a")
    record.update(
        task="coding-toc-commands",
        excluded=True,
        excludedReason="poll deadline passed (includes queue time)",
    )
    reader = Reader(
        {"a": {"costUsd": 2}},
        arms={"a": {"requested": "off", "recorded": ["off"], "effective": ["off"]}},
    )
    toc_eval._join([record], reader)
    assert bool(record.get("excluded")) is not completed
    assert record["verdict"] == (outcomes.PASSED if completed else outcomes.AGENT_ERROR)


@pytest.mark.parametrize(
    ("requested", "effective", "reason"),
    [("spill", ["spill"], "arm mismatch"), ("off", [None], "effective_arm_not_reported")],
)
def test_first_arm_failure_stops_the_matrix(monkeypatch, tmp_path, requested, effective, reason):
    from gym import toc_eval

    _stub_ledger(monkeypatch, toc_eval)
    monkeypatch.setattr(toc_eval, "_lock_omission_reason", lambda _: None)
    reader = _FakeReader(
        {}, arms={"a": {"requested": requested, "recorded": [requested], "effective": effective}}
    )
    monkeypatch.setattr(toc_eval, "_open_reader", lambda: reader)
    calls = []

    def run(*args):
        calls.append(args)
        return [_attempt(outcomes.PASSED, 1, lrq="a")]

    monkeypatch.setattr(toc_eval, "_run_attempt", run)
    out = tmp_path / "result.json"
    assert (
        toc_eval.main(["--out", str(out), "--cell", "codex=m", "--arm", "off", "--arm", "spill"])
        == 1
    )
    doc = json.loads(out.read_text())
    assert len(calls) == 1
    assert doc["result"]["status"] == "partial"
    assert reason in doc["result"]["reason"]


@pytest.mark.parametrize("initial_cost", [None, 1.0])
@pytest.mark.parametrize("calls", [{}, {"git": 9}, {"git": 1, "pytest": 2}])
def test_required_seam_coverage_stays_pending_until_journals_arrive(initial_cost, calls):
    from gym import toc_eval

    record = {
        "launchRequestId": "lrq",
        "arm": "spill",
        "task": "coding-toc-large-output",
        "requiredLargeOutputCommands": ["git", "pytest"],
        "verdict": "passed",
        "workerClass": "codex",
        "model": "gpt-6.1-sol",
    }
    reader = _FakeReader(
        {"lrq": {"costUsd": initial_cost}},
        arms={"lrq": {"requested": "spill", "recorded": ["spill"], "effective": ["spill"]}},
        counts={
            "lrq": {
                "rows": sum(calls.values()),
                "okRows": sum(calls.values()),
                "callsByCommand": calls,
                "backends": ["spill"],
                "armSources": ["launch"],
            }
        },
    )
    missing = not all(calls.get(c) for c in ("git", "pytest"))
    assert toc_eval._join([record], reader) == int(initial_cost is None or missing)
    assert record["seamCoverage"] == ("pending" if missing else "observed")
    assert not record.get("excluded")
    summary = toc_matrix.aggregate([record])[0]
    assert summary["sampleSize"] == 1
    assert summary["totalCostUsd"] == (initial_cost or 0)
    assert summary["informative"] is not missing
    reader.costs["lrq"]["costUsd"] = 1.0
    # Cost/usage completion cannot establish journal ingestion completion.
    assert toc_eval._join([record], reader) == int(missing)
    reader.counts["lrq"].update(callsByCommand={"git": 1, "pytest": 2}, rows=3, okRows=3)
    assert toc_eval._join([record], reader) == 0
    assert record["seamCoverage"] == "observed"
    assert not record.get("seamMissingCommands")


def test_finish_rejoins_legacy_seam_exclusion_even_with_complete_usage(monkeypatch, tmp_path):
    from gym import toc_eval

    record = {
        **_attempt(outcomes.PASSED, None, arm="spill", lrq="lrq"),
        "requiredLargeOutputCommands": ["git", "pytest"],
        "excluded": True,
        "excludedReason": "seam_bypassed",
        "seamBypassedCommands": ["git", "pytest"],
        "invocationAt": "test-invocation",
    }
    reader = _FakeReader(
        {"lrq": {"costUsd": None}},
        arms={"lrq": {"requested": "spill", "recorded": ["spill"], "effective": ["spill"]}},
        usage={"lrq": {"usageComplete": True, "totalTokens": 10}},
    )
    monkeypatch.setattr(toc_eval, "_open_reader", lambda: reader)
    document = {"attempts": [record], "lastInvocation": {"at": "test-invocation"}}
    out = tmp_path / "result.json"
    assert toc_eval._finish(out, document) == 1
    assert not record.get("excluded")
    assert record["seamCoverage"] == "pending"
    assert document["arms"][0]["sampleSize"] == 1
    reader.costs["lrq"]["costUsd"] = 1
    assert toc_eval._finish(out, document) == 1
    reader.counts["lrq"] = {
        "rows": 3,
        "okRows": 3,
        "callsByCommand": {"git": 1, "pytest": 2},
        "backends": ["spill"],
        "armSources": ["launch"],
    }
    assert toc_eval._finish(out, document) == 0


@pytest.mark.parametrize("arm", ["off", "rtk"])
def test_required_commands_do_not_change_other_arm_accounting(arm):
    from gym import toc_eval

    record = {
        "launchRequestId": "lrq",
        "arm": arm,
        "task": "coding-toc-large-output",
        "verdict": "passed",
    }
    reader = _FakeReader(
        {"lrq": {"costUsd": 1}},
        arms={"lrq": {"requested": arm, "recorded": [arm], "effective": [arm]}},
    )
    toc_eval._join_record(record, reader)
    assert not record.get("excluded")


@pytest.mark.parametrize("commands", ["git", ["unknown"], [None]])
def test_required_large_output_commands_schema_rejects_invalid_labels(commands):
    from gym.schema import TaskSchemaError, parse_task_dict

    with pytest.raises(TaskSchemaError, match="required_large_output_commands"):
        parse_task_dict(
            {
                "id": "test",
                "result_schema": {"status": {"type": "string"}},
                "verifier": {"entrypoint": "verifier.py"},
                "required_large_output_commands": commands,
            }
        )


@pytest.mark.parametrize("task", ["../escape", "coding-toc-large-output"])
def test_dispatch_freezes_required_commands_from_configured_corpus(tmp_path, monkeypatch, task):
    from gym import toc_eval
    from gym.schema import TaskSchemaError

    task_dir = tmp_path / "coding-toc-large-output"
    task_dir.mkdir()
    (task_dir / "task.toml").write_text(
        'id = "coding-toc-large-output"\nrequired_large_output_commands = ["git"]\n'
        '[result_schema.status]\ntype = "string"\n[verifier]\nentrypoint = "verifier.py"\n'
    )
    (task_dir / "verifier.py").write_text("# fixture verifier\n")
    monkeypatch.setattr(toc_eval.suite, "default_tasks_dir", lambda: tmp_path)
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        return types.SimpleNamespace(stdout=json.dumps({"cells": [{"launchRequestId": "lrq"}]}))

    monkeypatch.setattr(toc_eval.subprocess, "run", run)
    if task.startswith(".."):
        with pytest.raises(TaskSchemaError):
            toc_eval._run_attempt("codex", "m", "spill", task, 1, "low")
        assert not calls
    else:
        records = toc_eval._run_attempt("codex", "m", "spill", task, 1, "low")
        assert records[0]["requiredLargeOutputCommands"] == ["git"]
        # The saved contract is unaffected by later task edits or removal.
        (task_dir / "task.toml").write_text("invalid task")
        reader = _FakeReader(
            {"lrq": {"costUsd": 1}},
            arms={"lrq": {"requested": "spill", "recorded": ["spill"], "effective": ["spill"]}},
            counts={
                "lrq": {
                    "rows": 1,
                    "callsByCommand": {"git": 1},
                    "backends": ["spill"],
                    "armSources": ["launch"],
                }
            },
        )
        assert toc_eval._join(records, reader) == 0
        assert records[0]["seamCoverage"] == "observed"


def test_legacy_join_does_not_read_untrusted_or_current_task_files():
    from gym import toc_eval

    record = {**_attempt(outcomes.PASSED, 1, lrq="lrq"), "task": "../escape"}
    assert toc_eval._join([record], _FakeReader({"lrq": {"costUsd": 1}})) == 0
    assert "requiredLargeOutputCommands" not in record
