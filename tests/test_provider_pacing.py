"""Dedicated endpoint execution uses fake streams and dispatch only."""

import json
import subprocess
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

pytest.importorskip("gym.dispatch", reason="host integration is not shipped")

from gym.cli import build_parser
from gym.dispatch import WorkerPoolTrajectorySource
from gym.grader import Trajectory, grade
from gym.provider_health import ProviderHealth
from gym.suite import discover_tasks, run_suite
from conftest import FIXTURES_DIR


def retry(status=408):
    return {
        "type": "system",
        "subtype": "api_retry",
        "error_status": status,
        "error": "server_error",
    }


def write_events(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        for event in events:
            stream.write(json.dumps(event) + "\n")


@pytest.mark.parametrize("transient_timeout", [False, True, "cancelled", "canceled"])
@pytest.mark.parametrize("status", [408, 500, 503])
def test_repeated_provider_errors_cancel_and_retry_once(
    tmp_path, monkeypatch, status, transient_timeout
):
    tasks = discover_tasks(FIXTURES_DIR / "mini-suite", task="mini-pass")
    source = WorkerPoolTrajectorySource(hq_root=tmp_path, hq_bin="fake-hq", poll_interval_seconds=0)
    launches = []
    canceled = []
    statuses = {}

    def dispatch(task):
        launch = f"lrq-{len(launches)}"
        sandbox = tmp_path / "workers" / launch / "workspace"
        sandbox.mkdir(parents=True)
        launches.append((launch, sandbox))
        write_events(tmp_path / "dispatch" / launch / "stdout.log", [retry(status), retry(status)])
        return launch, sandbox

    monkeypatch.setattr("gym.dispatch.time.sleep", lambda delay: None)

    def cancel(cmd, **kwargs):
        assert cmd[:3] == ["fake-hq", "dispatch", "cancel"]
        assert kwargs["timeout"] == 30
        canceled.append(cmd[3])
        if transient_timeout and canceled.count(cmd[3]) == 1:
            if isinstance(transient_timeout, str):
                # The first cancel committed before its subprocess timed out.
                statuses[cmd[3]] = transient_timeout
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        if isinstance(transient_timeout, str):
            status = statuses[cmd[3]]
            return subprocess.CompletedProcess(
                cmd,
                1,
                json.dumps(
                    {
                        "ok": False,
                        "currentStatus": status,
                        "reason": f"already terminal (status={status})",
                    }
                ),
                "",
            )
        return subprocess.CompletedProcess(cmd, 0, '{"ok":true,"cancelApplied":true}', "")

    source._reader = SimpleNamespace(
        worker_run_for_launch=lambda lrq: {"status": statuses.get(lrq, "running")},
        cost_for_launch=lambda lrq: None,
    )
    monkeypatch.setattr(source, "_dispatch", dispatch)
    monkeypatch.setattr(subprocess, "run", cancel)
    cell = run_suite(tasks, source, taint_mode="enforce").cells[0]
    assert cell.outcome == "agent_error"
    assert cell.terminal_status == "provider_unavailable"
    assert "provider_unavailable" in cell.reason and "HTTP" in cell.reason
    assert len(cell.infra_attempts) == 2
    assert canceled == [
        launch for launch, _ in launches for _ in range(2 if transient_timeout else 1)
    ]
    for attempt in cell.infra_attempts:
        assert f"cancel attempts={2 if transient_timeout else 1}" in attempt["reason"]
        assert "worker stopped" in attempt["reason"]
        if isinstance(transient_timeout, str):
            assert "worker stopped (confirmed after retry)" in attempt["reason"]
            assert f"already terminal (status={transient_timeout})" in attempt["reason"]
    assert launches[0][1] != launches[1][1]


def test_provider_blip_recovers_on_only_retry(tmp_path, monkeypatch):
    task = discover_tasks(FIXTURES_DIR / "mini-suite", task="mini-pass")[0]
    source = WorkerPoolTrajectorySource()
    calls = []

    def run_once(t):
        calls.append(t.id)
        if len(calls) == 1:
            return Trajectory(
                terminal_status="provider_unavailable",
                error="litellm.Timeout",
                launch_request_id="old",
                provider_retry_safe=True,
            )
        return Trajectory(
            terminal_status="succeeded", submitted_result={"answer": "ok"}, launch_request_id="new"
        )

    monkeypatch.setattr(source, "_run_once", run_once)
    cell = grade(task, source.run(task))
    assert cell.outcome == "passed"
    assert len(cell.infra_attempts) == 2
    assert cell.infra_attempts[0]["launchRequestId"] == "old"


@pytest.mark.parametrize(
    "payload",
    [
        '{"ok":true,"cancelApplied":false,"newStatus":"cancel_requested"}',
        '{"ok":false}',
        "invalid",
        "timeout",
    ],
)
def test_unconfirmed_cancellation_suppresses_retry(tmp_path, monkeypatch, payload):
    task = discover_tasks(FIXTURES_DIR / "mini-suite", task="mini-pass")[0]
    source = WorkerPoolTrajectorySource(hq_root=tmp_path, hq_bin="fake-hq", poll_timeout_seconds=0)
    launches = []

    def dispatch(t):
        launches.append(t.id)
        write_events(tmp_path / "dispatch" / "lrq-1" / "stdout.log", [retry(), retry()])
        return "lrq-1", tmp_path

    source._reader = SimpleNamespace(
        worker_run_for_launch=lambda lrq: {"status": "running"}, cost_for_launch=lambda lrq: None
    )
    monkeypatch.setattr(source, "_dispatch", dispatch)
    calls = []
    monkeypatch.setattr("gym.dispatch.time.sleep", lambda delay: None)

    def cancel(cmd, **kw):
        calls.append(cmd)
        if payload == "timeout":
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        return subprocess.CompletedProcess(cmd, 0, payload, "")

    monkeypatch.setattr(subprocess, "run", cancel)
    trajectory = source.run(task)
    assert len(launches) == 1
    assert "retry suppressed" in trajectory.error

    expected = 3 if payload == "timeout" else 1
    assert len(calls) == expected
    assert f"cancel attempts={expected}" in trajectory.infra_attempts[0]["reason"]
    assert "final reason=" in trajectory.infra_attempts[0]["reason"]


def test_one_error_expires_after_bounded_budget(tmp_path, monkeypatch):
    import gym.provider_health as health_mod

    clock = [0.0]
    monkeypatch.setattr(health_mod, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    path = tmp_path / "stdout.log"
    health = ProviderHealth(path, timeout_seconds=600)
    write_events(path, [retry()])
    assert health.check() is None
    clock[0] = 599
    assert health.check() is None
    clock[0] = 600
    assert "provider_unavailable" in health.check()


def test_success_resets_errors_and_partial_lines_are_counted_once(tmp_path):
    path = tmp_path / "stdout.log"
    health = ProviderHealth(path)
    path.write_text(json.dumps(retry())[:20])
    assert health.check() is None
    with path.open("a") as stream:
        stream.write(json.dumps(retry())[20:] + "\n")
    assert health.check() is None
    assert health.consecutive == 1
    assert health.check() is None
    assert health.consecutive == 1
    write_events(path, [{"type": "stream_event", "event": {"type": "message_start"}}, retry()])
    assert health.check() is None
    assert health.consecutive == 1


def test_quoted_error_is_not_provider_evidence(tmp_path):
    path = tmp_path / "stdout.log"
    write_events(
        path,
        [
            {
                "type": "assistant",
                "message": {
                    "model": "qwen",
                    "content": [{"type": "text", "text": "API Error: 503 litellm.Timeout"}],
                },
            },
            {"type": "user", "content": "litellm.Timeout"},
        ],
    )
    assert ProviderHealth(path).check() is None


@pytest.mark.parametrize(
    "error",
    [
        "litellm.Timeout: Timeout passed=60.0",
        "litellm.ServiceUnavailable",
        "litellm.APIConnectionError",
    ],
)
def test_terminal_synthetic_error_is_provider_unavailable(tmp_path, error):
    path = tmp_path / "stdout.log"
    write_events(
        path, [{"type": "result", "subtype": "success", "is_error": True, "result": error}]
    )
    assert error.split(":")[0] in ProviderHealth(path).check()


@pytest.mark.parametrize("concurrency", [1, 3])
def test_concurrency_preserves_grades_and_summary(concurrency):
    tasks = discover_tasks(FIXTURES_DIR / "mini-suite")
    # Six independent cells reuse immutable verifier fixtures with distinct ids.
    tasks = [replace(tasks[0], id=f"task-{i}") for i in range(6)]

    class Source:
        active = 0
        maximum = 0
        lock = threading.Lock()
        barrier = threading.Barrier(concurrency)

        def run(self, task):
            with self.lock:
                self.active += 1
                self.maximum = max(self.maximum, self.active)
            self.barrier.wait(timeout=5)
            with self.lock:
                self.active -= 1
            return Trajectory(terminal_status="succeeded", submitted_result={"answer": "ok"})

    source = Source()
    concurrent = run_suite(tasks, source, taint_mode="enforce", concurrency=concurrency)
    serial = run_suite(
        tasks,
        SimpleNamespace(
            run=lambda t: Trajectory(terminal_status="succeeded", submitted_result={"answer": "ok"})
        ),
        taint_mode="enforce",
    )
    assert source.maximum == concurrency
    assert concurrent.summary_dict() == serial.summary_dict()


def test_default_cli_is_serial():
    assert build_parser().parse_args(["run"]).concurrency == 1
    assert build_parser().parse_args(["run", "--concurrency", "3"]).concurrency == 3
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--concurrency", "0"])


def test_production_source_fork_does_not_share_reader():
    source = WorkerPoolTrajectorySource(_reader=object())
    assert source.fork()._reader is None


def test_incremental_fake_provider_stops_within_budget(tmp_path, monkeypatch):
    import gym.dispatch as dispatch_mod
    import gym.provider_health as health_mod

    clock = [0.0]
    fake_time = SimpleNamespace(
        monotonic=lambda: clock[0], sleep=lambda delay: clock.__setitem__(0, clock[0] + delay)
    )
    monkeypatch.setattr(dispatch_mod, "time", fake_time)
    monkeypatch.setattr(health_mod, "time", fake_time)
    source = WorkerPoolTrajectorySource(hq_root=tmp_path, poll_interval_seconds=60)
    path = tmp_path / "dispatch" / "lrq-1" / "stdout.log"

    def read(launch):
        write_events(path, [retry()])
        return {"status": "running"}

    monkeypatch.setattr(source, "_cancel_provider_task", lambda lrq: (True, "worker stopped"))

    from gym.dispatch import ProviderUnavailable

    with pytest.raises(ProviderUnavailable):
        source._poll_to_terminal(SimpleNamespace(worker_run_for_launch=read), "lrq-1")
    assert clock[0] == 60  # One poll interval between observed failures, not an hour.


def test_unconfirmed_stop_halts_later_suite_admissions():
    tasks = discover_tasks(FIXTURES_DIR / "mini-suite")
    calls = []

    def run(task):
        calls.append(task.id)
        return Trajectory(
            terminal_status="provider_unavailable",
            error="provider_unavailable",
            infra_attempts=[{"retry_safe": False}],
        )

    result = run_suite(tasks, SimpleNamespace(run=run), taint_mode="enforce")
    assert len(calls) == 1
    assert all(cell.outcome == "agent_error" for cell in result.cells)
    assert "admission halted" in result.cells[-1].reason


def test_retry_metrics_include_both_launches_and_preserve_unknowns():
    from gym.metrics import TOKEN_COLUMNS, combine_launch_metrics, launch_metrics

    def measured(run_id, count):
        return launch_metrics(
            [
                {
                    "run_id": run_id,
                    **{column: count for column in TOKEN_COLUMNS},
                    "started_at": "2026-10-05T01:00:00Z",
                    "ended_at": "2026-10-05T01:01:00Z",
                    "token_usage_source": "claude-code",
                    "token_usage_by_model_json": {"qwen": {"output": count}},
                }
            ],
            worker_class="oss-claude-code",
        )

    combined = combine_launch_metrics([measured("old", 10), measured("new", 20)])
    assert combined["run_ids"] == ["old", "new"]
    assert combined["token_usage_input"] == 30
    assert combined["wall_seconds"] == 120
    assert combined["observed_models"] == {"qwen": 30}
    assert combined["metrics_complete"]
    unknown = combine_launch_metrics([None, measured("new", 20)])
    assert unknown["token_usage_input"] is None
    assert not unknown["metrics_complete"]
    other = measured("new", 20)
    other["worker_class"] = "other-harness"
    mixed = combine_launch_metrics([measured("old", 10), other])
    assert not mixed["metrics_complete"]


def test_parallel_slot_refilled_before_slow_first_task_finishes():
    tasks = discover_tasks(FIXTURES_DIR / "mini-suite")
    tasks = [replace(tasks[0], id=f"task-{i}") for i in range(4)]
    replacement_started = threading.Event()

    def run(task):
        if task.id == "task-0":
            assert replacement_started.wait(timeout=5), "fast slot was blocked by first task"
        if task.id == "task-2":
            replacement_started.set()
        return Trajectory(terminal_status="succeeded", submitted_result={"answer": "ok"})

    result = run_suite(tasks, SimpleNamespace(run=run), taint_mode="enforce", concurrency=2)
    assert result.passed == 4
    assert [cell.task_id for cell in result.cells] == [t.id for t in tasks]


def test_provider_error_preserves_a_valid_submission():
    task = discover_tasks(FIXTURES_DIR / "mini-suite", task="mini-pass")[0]
    cell = grade(
        task,
        Trajectory(
            terminal_status="provider_unavailable",
            submitted_result={"answer": "ok"},
            error="provider_unavailable: litellm.Timeout",
        ),
    )
    assert cell.outcome == "passed"


def test_saved_run_records_concurrency_separately_from_summary(tmp_path):
    from gym.cli import _store_model_result

    tasks = discover_tasks(FIXTURES_DIR / "mini-suite", task="mini-pass")
    result = run_suite(
        tasks,
        SimpleNamespace(
            run=lambda t: Trajectory(terminal_status="succeeded", submitted_result={"answer": "ok"})
        ),
        taint_mode="enforce",
    )
    path = _store_model_result(result, None, None, tmp_path, concurrency=3)
    document = json.loads(path.read_text())
    assert document["concurrency"] == 3
    assert document["summary"] == result.summary_dict()


def test_terminal_cancel_acknowledgement_allows_retry(monkeypatch):
    source = WorkerPoolTrajectorySource(hq_bin="fake-hq")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(
            cmd,
            1,
            '{"ok":false,"currentStatus":"failed","reason":"already terminal (status=failed)"}',
            "",
        ),
    )
    assert source._cancel_provider_task("lrq-1")[0]


def test_combined_metrics_keep_normalized_majority():
    from gym.metrics import combine_launch_metrics, launch_metrics

    parts = [launch_metrics([]), launch_metrics([])]
    parts[0]["observed_models"] = {"litellm-local-openai/fireworks/qwen": 3}
    parts[1]["observed_models"] = {"fireworks/qwen": 3, "other": 4}
    assert combine_launch_metrics(parts)["majority_model"] == "fireworks/qwen"


def test_rate_limit_retry_does_not_reset_elapsed_provider_failure_budget(tmp_path, monkeypatch):
    import gym.provider_health as health_mod

    clock = [0.0]
    monkeypatch.setattr(health_mod, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    path = tmp_path / "stdout.log"
    health = ProviderHealth(path)
    write_events(path, [retry()])
    assert health.check() is None
    clock[0] = 300
    write_events(path, [retry(429)])
    assert health.check() is None
    assert health.consecutive == 0
    clock[0] = 600
    assert "provider_unavailable" in health.check()


@pytest.mark.parametrize("status", ["failed", "succeeded"])
@pytest.mark.parametrize("submitted", [False, True])
def test_terminal_provider_error_is_collected_without_retry(
    tmp_path, monkeypatch, status, submitted
):
    task = discover_tasks(FIXTURES_DIR / "mini-suite", task="mini-pass")[0]
    source = WorkerPoolTrajectorySource(hq_root=tmp_path, poll_interval_seconds=0)
    if submitted:
        (tmp_path / "bench-result.json").write_text('{"answer":"ok"}')
    write_events(
        tmp_path / "dispatch" / "lrq-1" / "stdout.log",
        [{"type": "result", "is_error": True, "result": "litellm.Timeout"}],
    )
    row = {"status": status, "failure_detail": "litellm.Timeout"}
    source._reader = SimpleNamespace(
        worker_run_for_launch=lambda lrq: row,
        worker_class_for_launch=lambda lrq: None,
        cost_for_launch=lambda lrq, **kw: None,
    )
    launches = []

    def dispatch(t):
        launches.append(t.id)
        return "lrq-1", tmp_path

    monkeypatch.setattr(source, "_dispatch", dispatch)
    monkeypatch.setattr(
        source, "_cancel_provider_task", lambda lrq: pytest.fail("terminal cancellation")
    )
    trajectory = source.run(task)
    assert len(launches) == 1
    assert trajectory.terminal_status == status
    assert "litellm.Timeout" in trajectory.infra_attempts[0]["reason"]
    assert grade(task, trajectory).outcome == ("passed" if submitted else "agent_error")


def test_submitted_running_task_is_watched_without_cancellation(tmp_path, monkeypatch):
    (tmp_path / "bench-result.json").write_text('{"answer":"ok"}')
    write_events(tmp_path / "dispatch" / "lrq-1" / "stdout.log", [retry(), retry()])
    rows = iter([{"status": "running"}, {"status": "failed", "failure_detail": "litellm.Timeout"}])
    source = WorkerPoolTrajectorySource(hq_root=tmp_path, poll_interval_seconds=0)
    monkeypatch.setattr(
        source, "_cancel_provider_task", lambda lrq: pytest.fail("answered cancellation")
    )
    row = source._poll_to_terminal(
        SimpleNamespace(worker_run_for_launch=lambda lrq: next(rows)), "lrq-1", tmp_path
    )
    assert row["status"] == "failed"
    assert source._read_submitted_result(row, tmp_path) == {"answer": "ok"}


@pytest.mark.parametrize("existing_actor", [None, "operator:test", "session:parent"])
def test_cancel_subprocess_receives_authenticated_actor(tmp_path, monkeypatch, existing_actor):
    import pwd
    import os
    from cwp_dispatch.cli_watch import _resolve_cancel_actor

    keys = (
        "HQ_OPERATOR_PRINCIPAL",
        "HQ_PARENT_SESSION",
        "OPENCLAW_SESSION_ID",
        "CODEX_SESSION_ID",
        "CLAUDE_SESSION_ID",
    )
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    if existing_actor:
        monkeypatch.setenv(
            "HQ_PARENT_SESSION"
            if existing_actor.startswith("session")
            else "HQ_OPERATOR_PRINCIPAL",
            existing_actor,
        )

    def cancel(cmd, **kw):
        with monkeypatch.context() as env_patch:
            for key, value in kw["env"].items():
                env_patch.setenv(key, value)
            assert _resolve_cancel_actor() == (
                existing_actor or f"service:gym:{pwd.getpwuid(os.geteuid()).pw_name}"
            )
        return subprocess.CompletedProcess(cmd, 0, '{"ok":true,"cancelApplied":true}', "")

    monkeypatch.setattr(subprocess, "run", cancel)
    assert WorkerPoolTrajectorySource(hq_root=tmp_path, hq_bin="fake-hq")._cancel_provider_task(
        "lrq-1"
    )[0]


def test_unconfirmed_cancel_watches_to_terminal_and_allows_later_tasks(tmp_path, monkeypatch):
    tasks = discover_tasks(FIXTURES_DIR / "mini-suite")
    source = WorkerPoolTrajectorySource(hq_root=tmp_path, hq_bin="fake-hq", poll_interval_seconds=0)
    launches = []
    reads = {}

    def dispatch(task):
        launch = f"lrq-{len(launches)}"
        launches.append(launch)
        write_events(tmp_path / "dispatch" / launch / "stdout.log", [retry(), retry()])
        return launch, tmp_path

    def read(launch):
        reads[launch] = reads.get(launch, 0) + 1
        return {
            "status": "running" if reads[launch] < 4 else "failed",
            "failure_detail": "litellm.Timeout",
        }

    source._reader = SimpleNamespace(
        worker_run_for_launch=read,
        worker_class_for_launch=lambda lrq: None,
        cost_for_launch=lambda lrq, **kw: None,
    )
    monkeypatch.setattr(source, "_dispatch", dispatch)
    cancellations = []

    def cancel(launch):
        cancellations.append(launch)
        return False, "cancellation unconfirmed; retry suppressed"

    monkeypatch.setattr(source, "_cancel_provider_task", cancel)
    result = run_suite(tasks, source, taint_mode="enforce")
    assert len(launches) == len(tasks)
    assert cancellations == launches
    assert all(reads[launch] == 4 for launch in launches)
    assert all("admission halted" not in cell.reason for cell in result.cells)


def test_unconfirmed_cancel_keeps_original_poll_deadline(tmp_path, monkeypatch):
    import gym.dispatch as dispatch_mod

    clock = [0.0]
    monkeypatch.setattr(
        dispatch_mod,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock[0], sleep=lambda delay: clock.__setitem__(0, clock[0] + delay)
        ),
    )
    write_events(tmp_path / "dispatch" / "lrq-1" / "stdout.log", [retry(), retry()])
    source = WorkerPoolTrajectorySource(
        hq_root=tmp_path, poll_interval_seconds=1, poll_timeout_seconds=3
    )
    cancel_calls = []
    monkeypatch.setattr(
        source,
        "_cancel_provider_task",
        lambda lrq: (cancel_calls.append(lrq) or False, "unconfirmed"),
    )
    row = source._poll_to_terminal(
        SimpleNamespace(worker_run_for_launch=lambda lrq: {"status": "running"}), "lrq-1", tmp_path
    )
    assert row["status"] == "provider_unavailable"
    assert clock[0] == 3
    assert cancel_calls == ["lrq-1"]


def test_provider_status_survives_suite_summary():
    tasks = discover_tasks(FIXTURES_DIR / "mini-suite", task="mini-pass")
    result = run_suite(
        tasks,
        SimpleNamespace(
            run=lambda t: Trajectory(
                terminal_status="provider_unavailable",
                error="provider_unavailable",
                launch_request_id="lrq-1",
                provider_retry_safe=True,
            )
        ),
        taint_mode="enforce",
    )
    assert result.summary_dict()["cells"][0]["terminalStatus"] == "provider_unavailable"


def test_submission_written_during_cancel_is_preserved(tmp_path, monkeypatch):
    task = discover_tasks(FIXTURES_DIR / "mini-suite", task="mini-pass")[0]
    write_events(tmp_path / "dispatch" / "lrq-1" / "stdout.log", [retry(), retry()])
    source = WorkerPoolTrajectorySource(hq_root=tmp_path, poll_interval_seconds=0)
    status = ["running"]

    def cancel(lrq):
        (tmp_path / "bench-result.json").write_text('{"answer":"ok"}')
        status[0] = "canceled"
        return True, "worker stopped"

    source._reader = SimpleNamespace(
        worker_run_for_launch=lambda lrq: {"status": status[0]},
        worker_class_for_launch=lambda lrq: None,
        cost_for_launch=lambda lrq, **kw: None,
    )
    launches = []
    monkeypatch.setattr(source, "_dispatch", lambda t: (launches.append(t.id) or "lrq-1", tmp_path))
    monkeypatch.setattr(source, "_cancel_provider_task", cancel)
    assert grade(task, source.run(task)).outcome == "passed"
    assert len(launches) == 1


def test_completion_during_cancel_does_not_retry(tmp_path, monkeypatch):
    task = discover_tasks(FIXTURES_DIR / "mini-suite", task="mini-pass")[0]
    write_events(tmp_path / "dispatch" / "lrq-1" / "stdout.log", [retry(), retry()])
    source = WorkerPoolTrajectorySource(hq_root=tmp_path, poll_interval_seconds=0)
    status = ["running"]

    def cancel(lrq):
        status[0] = "failed"
        return True, "worker already terminal"

    source._reader = SimpleNamespace(
        worker_run_for_launch=lambda lrq: {
            "status": status[0],
            "failure_detail": "litellm.Timeout",
        },
        worker_class_for_launch=lambda lrq: None,
        cost_for_launch=lambda lrq, **kw: None,
    )
    launches = []
    monkeypatch.setattr(source, "_dispatch", lambda t: (launches.append(t.id) or "lrq-1", tmp_path))
    monkeypatch.setattr(source, "_cancel_provider_task", cancel)
    assert source.run(task).terminal_status == "failed"
    assert len(launches) == 1


def test_provider_monitor_drains_large_snapshot_before_terminal_error(tmp_path):
    path = tmp_path / "stdout.log"
    write_events(
        path,
        [
            {"type": "tool_result", "content": "x" * (5 * 1024 * 1024)},
            {"type": "result", "is_error": True, "result": "litellm.Timeout"},
        ],
    )
    health = ProviderHealth(path)
    assert "litellm.Timeout" in health.check()
    assert health.offset == path.stat().st_size
    assert len(health.pending) <= 1024 * 1024


def test_provider_monitor_warns_once_when_existing_stream_is_unreadable(
    tmp_path, monkeypatch, caplog
):
    from pathlib import Path

    path = tmp_path / "stdout.log"
    path.write_text("")

    def denied(*args, **kwargs):
        raise PermissionError("cross-owner fixture")

    monkeypatch.setattr(Path, "open", denied)
    health = ProviderHealth(path)
    assert health.check() is None
    assert health.check() is None
    assert (
        sum("Provider monitoring cannot read" in record.message for record in caplog.records) == 1
    )


def test_concurrent_sources_reuse_one_economics_snapshot(monkeypatch):
    source = WorkerPoolTrajectorySource()
    inputs = (object(), object(), object())
    loads = []
    monkeypatch.setattr(source, "_load_economics_inputs", lambda: loads.append(True) or inputs)
    stop = threading.Event()
    source.prepare_concurrent(stop)
    forks = [source.fork() for _ in range(3)]
    assert len(loads) == 1
    assert all(
        fork._economics_inputs is inputs and fork.stop_event is stop and fork._reader is None
        for fork in forks
    )


def test_concurrent_interrupt_signals_sources_without_waiting_for_poll_deadline(monkeypatch):
    import gym.suite as suite_module

    started = threading.Event()

    class Source:
        def __init__(self):
            self.stop = threading.Event()

        def prepare_concurrent(self, stop):
            self.stop = stop

        def run(self, task):
            started.set()
            assert self.stop.wait(2)
            return Trajectory(terminal_status="cancelled")

    source = Source()

    def interrupt(*args, **kwargs):
        assert started.wait(1)
        raise KeyboardInterrupt

    monkeypatch.setattr(suite_module, "wait", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_suite(
            discover_tasks(FIXTURES_DIR / "mini-suite"), source, taint_mode="enforce", concurrency=2
        )
    assert source.stop.is_set()


@pytest.mark.parametrize("confirmed", [True, False])
def test_interrupt_cancels_once_and_retains_unconfirmed_supervision(
    tmp_path, monkeypatch, confirmed
):
    stop = threading.Event()
    stop.set()
    source = WorkerPoolTrajectorySource(hq_root=tmp_path, stop_event=stop, poll_timeout_seconds=0)
    cancellations = []
    monkeypatch.setattr(
        source,
        "_cancel_provider_task",
        lambda launch, **kw: cancellations.append((launch, kw)) or (confirmed, "fixture stop"),
    )
    reader = SimpleNamespace(worker_run_for_launch=lambda launch: {"status": "running"})
    if confirmed:
        with pytest.raises(InterruptedError, match="suite interrupted"):
            source._poll_to_terminal(reader, "launch")
    else:
        assert source._poll_to_terminal(reader, "launch") is None
    assert cancellations == [("launch", {"max_attempts": 1})]


@pytest.mark.parametrize(
    "failure, expected_attempts",
    [
        ("timeout", 3),
        ("eio", 3),
        ("enoent", 1),
        ("invalid", 1),
        ("invalid-transient", 3),
        ("invalid-traceback", 1),
        ("ledger", 3),
        ("connection", 3),
        ("refused", 1),
        ("terminal", 1),
    ],
)
def test_cancel_transient_attempt_bound(monkeypatch, failure, expected_attempts):
    import errno
    import gym.dispatch as dispatch_mod

    elapsed = [0.0]
    calls = []
    monkeypatch.setattr(
        dispatch_mod.time, "sleep", lambda delay: elapsed.__setitem__(0, elapsed[0] + delay)
    )

    def cancel(cmd, **kw):
        calls.append(cmd)
        assert kw["timeout"] == 30
        if failure == "timeout":
            elapsed[0] += kw["timeout"]
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        if failure in {"eio", "enoent"}:
            raise OSError(errno.EIO if failure == "eio" else errno.ENOENT, "test")
        stdout, stderr = {
            "invalid": ("invalid", ""),
            "invalid-transient": ("", "resource temporarily unavailable"),
            "invalid-traceback": (
                "",
                "Traceback (most recent call last):\n"
                '  File "session_ledger/db.py", line 1, in connect\n'
                "    connect(timeout=30)\n"
                "TypeError: unexpected keyword argument timeout\n\n",
            ),
            "ledger": ('{"ok":false,"reason":"dispatch ledger temporarily unavailable"}', ""),
            "connection": ('{"ok":false,"reason":"dispatch ledger connection refused"}', ""),
            "refused": (
                '{"ok":false,"reason":"cancel refused: ledger temporarily unavailable"}',
                "",
            ),
            "terminal": (
                '{"ok":false,"currentStatus":"failed","reason":"already terminal (status=failed)"}',
                "",
            ),
        }[failure]
        return subprocess.CompletedProcess(cmd, 1, stdout, stderr)

    monkeypatch.setattr(subprocess, "run", cancel)
    safe, detail = WorkerPoolTrajectorySource(hq_bin="fake-hq")._cancel_provider_task("lrq-1")
    assert safe is (failure == "terminal")
    assert len(calls) == expected_attempts
    assert f"cancel attempts={expected_attempts}" in detail
    assert "final reason=" in detail
    if failure == "timeout":
        assert detail.startswith("cancellation unavailable; retry suppressed")
        assert elapsed[0] == 97
    if failure == "terminal":
        assert detail.startswith("worker already terminal")

    if failure in {"ledger", "connection"}:
        assert detail.startswith("cancellation unconfirmed (transient); retry suppressed")


def test_interrupt_timeout_uses_one_cancel_attempt(tmp_path, monkeypatch):
    stop = threading.Event()
    stop.set()
    source = WorkerPoolTrajectorySource(
        hq_root=tmp_path, hq_bin="fake-hq", stop_event=stop, poll_timeout_seconds=0
    )
    calls = []

    def cancel(cmd, **kw):
        calls.append(cmd)
        assert kw["timeout"] == 30
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(subprocess, "run", cancel)
    monkeypatch.setattr("gym.dispatch.time.sleep", lambda delay: pytest.fail("interrupt backoff"))
    reader = SimpleNamespace(worker_run_for_launch=lambda launch: {"status": "running"})
    assert source._poll_to_terminal(reader, "lrq-1") is None
    assert len(calls) == 1
