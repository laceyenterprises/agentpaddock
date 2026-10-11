"""GYMWAIT-01: the poll deadline never throws away a launch that is running.

At the deadline the gym waits for a launch that is executing and cancels one
that never started. Fake clock, fake readers and a fake cancel only: no
dispatch, no ledger, no worker.
"""

from types import SimpleNamespace

import pytest

import gym.dispatch as dispatch_mod
import gym.provider_health as health_mod
from gym.dispatch import WorkerPoolTrajectorySource
from gym.ledger import LedgerReader
from gym.suite import discover_tasks
from conftest import FIXTURES_DIR

LRQ = "lrq-1"


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    fake = SimpleNamespace(
        monotonic=lambda: now[0], sleep=lambda delay: now.__setitem__(0, now[0] + delay)
    )
    monkeypatch.setattr(dispatch_mod, "time", fake)
    monkeypatch.setattr(health_mod, "time", fake)
    return now


def _source(tmp_path, monkeypatch, cancel=None):
    """Poll every 10 s, deadline at 30 s, then up to 100 s more for a running launch."""
    source = WorkerPoolTrajectorySource(
        hq_root=tmp_path,
        poll_interval_seconds=10,
        poll_timeout_seconds=30,
        running_wait_timeout_seconds=100,
    )
    calls = []

    def record(lrq):
        calls.append(lrq)
        return cancel(lrq) if cancel else pytest.fail("a launch must not be cancelled here")

    monkeypatch.setattr(source, "_cancel_provider_task", record)
    return source, calls


def _reader(clock, *, finishes_at=None, launch_status="running"):
    """A worker run that reads ``running`` until ``finishes_at`` on the fake clock."""

    def run(lrq):
        if finishes_at is not None and clock[0] >= finishes_at:
            return {"status": "succeeded", "run_id": "run-1"}
        return {"status": "running", "run_id": "run-1"}

    status = launch_status if callable(launch_status) else (lambda lrq: launch_status)
    return SimpleNamespace(worker_run_for_launch=run, launch_status=status)


def test_a_running_launch_is_waited_for_past_the_deadline(tmp_path, monkeypatch, clock):
    source, cancels = _source(tmp_path, monkeypatch)
    row = source._poll_to_terminal(_reader(clock, finishes_at=70), LRQ, tmp_path)
    assert row == {"status": "succeeded", "run_id": "run-1"}
    assert clock[0] == 70  # 40 s past the 30 s deadline, and graded
    assert cancels == []


@pytest.mark.parametrize("status", ["requested", "leased"])
def test_a_launch_that_never_started_is_cancelled(tmp_path, monkeypatch, clock, status):
    source, cancels = _source(tmp_path, monkeypatch, cancel=lambda lrq: (True, "cancel applied"))
    # The run row reads ``running`` before admission; the launch request is what says it never started.
    row = source._poll_to_terminal(_reader(clock, launch_status=status), LRQ, tmp_path)
    assert row is None
    assert cancels == [LRQ]
    assert clock[0] == 30  # decided at the deadline, no extra wait
    assert source._deadline_notes[LRQ] == "never started; launch cancelled (cancel applied)"


def test_a_launch_still_running_after_the_extended_wait_is_left_alone(tmp_path, monkeypatch, clock):
    source, cancels = _source(tmp_path, monkeypatch)
    row = source._poll_to_terminal(_reader(clock), LRQ, tmp_path)
    assert row is None
    assert clock[0] == 130  # the 30 s deadline plus the 100 s extension
    assert cancels == []  # never cancel work in progress
    assert "still running after a further 100s" in source._deadline_notes[LRQ]


def test_a_launch_that_finishes_while_being_cancelled_is_graded(tmp_path, monkeypatch, clock):
    finished = []

    def cancel(lrq):
        finished.append(lrq)
        return True, "worker already terminal"

    source, cancels = _source(tmp_path, monkeypatch, cancel=cancel)
    reader = SimpleNamespace(
        worker_run_for_launch=lambda lrq: {"status": "succeeded" if finished else "running"},
        launch_status=lambda lrq: "requested",
    )
    assert source._poll_to_terminal(reader, LRQ, tmp_path) == {"status": "succeeded"}
    assert cancels == [LRQ]
    assert LRQ not in source._deadline_notes


def test_an_unconfirmed_cancel_keeps_the_launch_supervised(tmp_path, monkeypatch, clock):
    source, cancels = _source(tmp_path, monkeypatch, cancel=lambda lrq: (False, "unconfirmed"))
    reader = _reader(clock, finishes_at=80, launch_status="requested")
    row = source._poll_to_terminal(reader, LRQ, tmp_path)
    assert row == {"status": "succeeded", "run_id": "run-1"}  # it started after all: graded
    assert cancels == [LRQ]  # one cancel attempt, never repeated


def test_an_unreadable_launch_status_is_supervised_as_running(tmp_path, monkeypatch, clock):
    def unreadable(lrq):
        raise OSError("ledger read failed")

    source, cancels = _source(tmp_path, monkeypatch)
    row = source._poll_to_terminal(
        _reader(clock, finishes_at=50, launch_status=unreadable), LRQ, tmp_path
    )
    assert row == {"status": "succeeded", "run_id": "run-1"}
    assert cancels == []


@pytest.mark.parametrize("status", [None, "blocked_needs_decision", "operator_triage_required"])
def test_other_launch_states_keep_the_plain_deadline(tmp_path, monkeypatch, clock, status):
    source, cancels = _source(tmp_path, monkeypatch)
    assert source._poll_to_terminal(_reader(clock, launch_status=status), LRQ, tmp_path) is None
    assert clock[0] == 30
    assert cancels == []
    assert LRQ not in source._deadline_notes


def test_a_reader_without_launch_status_keeps_the_plain_deadline(tmp_path, monkeypatch, clock):
    source, cancels = _source(tmp_path, monkeypatch)
    reader = SimpleNamespace(worker_run_for_launch=lambda lrq: {"status": "running"})
    assert source._poll_to_terminal(reader, LRQ, tmp_path) is None
    assert clock[0] == 30
    assert cancels == []


def test_the_timeout_reason_says_what_happened_at_the_deadline(tmp_path, monkeypatch, clock):
    source, _ = _source(tmp_path, monkeypatch, cancel=lambda lrq: (True, "cancel applied"))
    reader = _reader(clock, launch_status="requested")
    reader.cost_for_launch = lambda lrq, **kwargs: None
    source._reader = reader
    source._dispatch = lambda task: (LRQ, tmp_path)  # type: ignore[method-assign]
    task = discover_tasks(FIXTURES_DIR / "mini-suite", task="mini-pass")[0]
    trajectory = source.run(task)
    assert trajectory.terminal_status == "timeout"
    assert trajectory.error == (
        f"launch {LRQ} did not reach terminal state in 30s; "
        "never started; launch cancelled (cancel applied)"
    )
    assert source._deadline_notes == {}


def test_a_fork_does_not_share_deadline_notes(tmp_path):
    source = WorkerPoolTrajectorySource(hq_root=tmp_path)
    source._deadline_notes[LRQ] = "never started; launch cancelled (x)"
    assert source.fork()._deadline_notes == {}


@pytest.mark.parametrize(
    "rows, expected",
    [
        ([{"request_payload_json": "{}", "status": "requested"}], "requested"),
        ([{"request_payload_json": "{}", "status": "running"}], "running"),
        ([{"request_payload_json": "{}"}], None),  # an older view without the column
        ([], None),
    ],
)
def test_ledger_reader_reports_the_launch_request_status(monkeypatch, rows, expected):
    from session_ledger.canonical_views import tool_output_compression_ab as toc

    target = SimpleNamespace(backend="postgres")
    calls = []

    def launches(conn, lrq, *, target):
        calls.append((conn, lrq, target))
        return rows

    monkeypatch.setattr(toc, "fetch_gym_recovery_launch", launches)
    reader = LedgerReader(None, target_resolver=lambda: target)
    assert reader.launch_status("lrq_x") == expected
    assert calls == [(None, "lrq_x", target)]
