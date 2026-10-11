"""Worker-pool dispatch retry behavior."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import tomllib
import types

import pytest

pytest.importorskip("gym.dispatch", reason="host integration is not shipped")

import gym.dispatch as dispatch_mod
from gym.dispatch import WorkerPoolTrajectorySource
from gym.schema import Stage, Task


@pytest.fixture(autouse=True)
def isolate_dispatch_clock(monkeypatch):
    # Clock patches must not capture sleeps from unrelated ledger/lock helpers.
    clock = types.SimpleNamespace(**vars(dispatch_mod.time))
    monkeypatch.setattr(dispatch_mod, "time", clock)


def _task(**kwargs) -> Task:
    return Task(
        id="retry",
        title="Retry",
        suite="unit",
        persona="worker",
        result_schema=(),
        verifier_entrypoint="verifier.py",
        **kwargs,
    )


def _hq_root(tmp_path):
    hq_root = tmp_path / "hq"
    (hq_root / "workers").mkdir(parents=True)
    return hq_root


@pytest.mark.parametrize("transient_failures", [1, 2])
def test_dispatch_retries_transient_failure_with_same_staging(
    monkeypatch, tmp_path, transient_failures
):
    responses = iter(
        [
            *[
                subprocess.CompletedProcess([], 1, "", "service unavailable")
                for _ in range(transient_failures)
            ],
            subprocess.CompletedProcess([], 0, json.dumps({"launchRequestId": "lrq-1"}), ""),
        ]
    )
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return next(responses)

    monkeypatch.setattr(subprocess, "run", fake_run)
    source = WorkerPoolTrajectorySource(
        dispatch_retry_backoff_seconds=0,
        hq_bin="hq",
        hq_root=_hq_root(tmp_path),
    )
    launch_request_id, sandbox_root = source._dispatch(_task())
    assert launch_request_id == "lrq-1"
    # _dispatch also hands back the sandbox it staged into: worker_runs.
    # worktree_path is NULL for gym runs, so the ledger cannot supply it.
    assert sandbox_root.name == "workspace"
    assert len(calls) == transient_failures + 1
    worker_ids = []
    launch_keys = []
    for args, _kwargs in calls:
        cmd = args[0]
        worker_ids.append(cmd[cmd.index("--worker-id") + 1])
        launch_keys.append(cmd[cmd.index("--idempotency-key") + 1])
    # A transient failure may follow acceptance; retry the same launch identity.
    assert len(set(launch_keys)) == 1
    assert len(set(worker_ids)) == 1
    assert sandbox_root == source.hq_root / "workers" / worker_ids[-1] / "workspace"
    assert sandbox_root.is_dir()


def test_dispatch_acknowledgement_lost_keeps_accepted_result_and_workspace(monkeypatch, tmp_path):
    root = _hq_root(tmp_path)
    accepted = {}
    calls = []

    def fake_run(cmd, **kwargs):
        key = cmd[cmd.index("--idempotency-key") + 1]
        sandbox = root / "workers" / cmd[cmd.index("--worker-id") + 1] / "workspace"
        calls.append((key, sandbox))
        if key not in accepted:
            accepted[key] = sandbox
            (sandbox / "bench-result.json").write_text('{"accepted":true}')
            return subprocess.CompletedProcess(cmd, 1, "", "service unavailable after admission")
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"launchRequestId": "original"}), "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    source = WorkerPoolTrajectorySource(hq_bin="hq", hq_root=root, dispatch_retry_backoff_seconds=0)
    lrq, returned = source._dispatch(_task())
    assert lrq == "original"
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert returned == accepted[calls[0][0]]
    assert json.loads((returned / "bench-result.json").read_text()) == {"accepted": True}


@pytest.mark.parametrize("failure", [FileNotFoundError("hq missing"), OSError(35, "fork busy")])
def test_initial_spawn_failure_defers_without_retaining_unaccepted_workspace(
    monkeypatch, tmp_path, failure
):
    root = _hq_root(tmp_path)

    def cannot_spawn(*args, **kwargs):
        raise failure

    monkeypatch.setattr(subprocess, "run", cannot_spawn)
    source = WorkerPoolTrajectorySource(hq_bin="hq", hq_root=root)
    with pytest.raises(RuntimeError) as raised:
        source._dispatch(_task())
    assert not isinstance(raised.value, dispatch_mod.DispatchAcceptanceUnknown)
    assert list((root / "workers").iterdir()) == []


def test_retry_spawn_failure_preserves_prior_possible_acceptance(monkeypatch, tmp_path):
    root = _hq_root(tmp_path)
    accepted = []

    def accept_then_fail_spawn(cmd, **kwargs):
        if accepted:
            raise OSError(35, "fork busy")
        sandbox = root / "workers" / cmd[cmd.index("--worker-id") + 1] / "workspace"
        accepted.append(sandbox)
        (sandbox / "bench-result.json").write_text('{"accepted":true}')
        return subprocess.CompletedProcess(cmd, 1, "", "service unavailable after admission")

    monkeypatch.setattr(subprocess, "run", accept_then_fail_spawn)
    source = WorkerPoolTrajectorySource(hq_bin="hq", hq_root=root, dispatch_retry_backoff_seconds=0)
    with pytest.raises(dispatch_mod.DispatchAcceptanceUnknown) as raised:
        source._dispatch(_task())
    assert raised.value.sandbox == accepted[0]
    assert json.loads((accepted[0] / "bench-result.json").read_text()) == {"accepted": True}


@pytest.mark.parametrize("failure", ["timeout", "invalid-json", "missing-receipt", "permanent"])
def test_dispatch_uncertain_outcome_preserves_workspace_evidence(monkeypatch, tmp_path, failure):
    root = _hq_root(tmp_path)
    accepted = []
    launch_keys = []

    def fake_run(cmd, **kwargs):
        sandbox = root / "workers" / cmd[cmd.index("--worker-id") + 1] / "workspace"
        accepted.append(sandbox)
        launch_keys.append(cmd[cmd.index("--idempotency-key") + 1])
        (sandbox / "bench-result.json").write_text('{"accepted":true}')
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd, 1)
        if failure == "invalid-json":
            return subprocess.CompletedProcess(cmd, 0, "not-json", "")
        if failure == "missing-receipt":
            return subprocess.CompletedProcess(cmd, 0, "{}", "")
        return subprocess.CompletedProcess(cmd, 2, "", "invalid arguments")

    monkeypatch.setattr(subprocess, "run", fake_run)
    source = WorkerPoolTrajectorySource(hq_bin="hq", hq_root=root, dispatch_retry_backoff_seconds=0)
    with pytest.raises(dispatch_mod.DispatchAcceptanceUnknown) as raised:
        source._dispatch(_task())
    assert raised.value.sandbox == accepted[0]
    assert raised.value.launch_key == launch_keys[0]
    assert len(accepted) == 1
    assert json.loads((accepted[0] / "bench-result.json").read_text()) == {"accepted": True}


def test_dispatch_passes_reasoning_level_only_when_requested(monkeypatch, tmp_path):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args[0])
        return subprocess.CompletedProcess([], 0, json.dumps({"launchRequestId": "lrq-1"}), "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    root = _hq_root(tmp_path)
    WorkerPoolTrajectorySource(hq_bin="hq", hq_root=root)._dispatch(_task())
    assert "--reasoning-level" not in calls[-1]
    WorkerPoolTrajectorySource(hq_bin="hq", hq_root=root, reasoning_level="medium")._dispatch(
        _task()
    )
    assert calls[-1][calls[-1].index("--reasoning-level") + 1] == "medium"


def test_shared_task_cells_have_distinct_explicit_launch_keys(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        key = cmd[cmd.index("--idempotency-key") + 1]
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"launchRequestId": key}), "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    root = _hq_root(tmp_path)
    launches = []
    for model, level, compression in [
        ("model-a", "low", None),
        ("model-b", "low", None),
        ("model-b", "high", "spill"),
        ("model-b", "high", "spill"),  # Another repetition of the same axes.
    ]:
        launches.append(
            WorkerPoolTrajectorySource(
                hq_bin="hq",
                hq_root=root,
                model=model,
                reasoning_level=level,
                compression=compression,
            )._dispatch(_task())[0]
        )
    assert len(set(launches)) == 4
    assert all(key.startswith("gym-") and len(key) == 36 for key in launches)
    assert len({cmd[cmd.index("--ticket") + 1] for cmd in calls}) == 1


def test_dispatch_pins_the_compression_arm_only_when_requested(monkeypatch, tmp_path):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args[0])
        return subprocess.CompletedProcess([], 0, json.dumps({"launchRequestId": "lrq-1"}), "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    root = _hq_root(tmp_path)
    WorkerPoolTrajectorySource(hq_bin="hq", hq_root=root)._dispatch(_task())
    assert "--tool-output-compression" not in calls[-1]
    WorkerPoolTrajectorySource(hq_bin="hq", hq_root=root, compression="spill")._dispatch(_task())
    cmd = calls[-1]
    assert cmd[cmd.index("--tool-output-compression") + 1] == "spill"
    # The launch arm is honoured only for gym-project launches.
    assert cmd[cmd.index("--project") + 1] == "adversarial-eval-gym"


def test_dispatch_does_not_retry_permanent_failure(monkeypatch, tmp_path):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess([], 2, "", "invalid arguments")

    monkeypatch.setattr(subprocess, "run", fake_run)
    source = WorkerPoolTrajectorySource(
        dispatch_retry_backoff_seconds=0,
        hq_bin="hq",
        hq_root=_hq_root(tmp_path),
    )
    with pytest.raises(RuntimeError, match=r"after 1 attempt\(s\)"):
        source._dispatch(_task())
    assert len(calls) == 1


def test_dispatch_resolves_prompt_without_task_dir(monkeypatch, tmp_path):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess([], 0, json.dumps({"launchRequestId": "lrq-1"}), "")

    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompt.md").write_text("Original task prompt.\n", encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", fake_run)
    source = WorkerPoolTrajectorySource(
        dispatch_retry_backoff_seconds=0,
        hq_bin="hq",
        hq_root=_hq_root(tmp_path),
    )

    launch_request_id, _sandbox_root = source._dispatch(_task(dispatch={"prompt": "prompt.md"}))
    assert launch_request_id == "lrq-1"
    cmd = calls[0][0][0]
    guarded_prompt = Path(cmd[cmd.index("--prompt") + 1])
    assert guarded_prompt.name == ".gym-task-prompt.md"
    assert "Do not search outside" in guarded_prompt.read_text()
    assert "Reading anything under `modules/adversarial-eval-gym/tasks/`" in (
        guarded_prompt.read_text()
    )
    assert "Original task prompt." in guarded_prompt.read_text()
    assert cmd[cmd.index("--fleet-retry-policy") + 1] == "origin-owned"


def test_dispatch_cleans_worker_dir_when_staging_fails(tmp_path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    hq_root = _hq_root(tmp_path)
    source = WorkerPoolTrajectorySource(
        dispatch_retry_backoff_seconds=0,
        hq_bin="hq",
        hq_root=hq_root,
    )

    with pytest.raises(RuntimeError, match="stage script missing"):
        source._dispatch(_task(stages=(Stage(run="missing.sh"),), task_dir=task_dir))

    assert list((hq_root / "workers").iterdir()) == []


def test_dispatch_retains_worker_dir_when_success_output_is_malformed(monkeypatch, tmp_path):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess([], 0, "not json", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    hq_root = _hq_root(tmp_path)
    source = WorkerPoolTrajectorySource(
        dispatch_retry_backoff_seconds=0,
        hq_bin="hq",
        hq_root=hq_root,
    )

    with pytest.raises(RuntimeError, match="returned invalid JSON"):
        source._dispatch(_task())

    assert len(list((hq_root / "workers").iterdir())) == 1


def test_stage_task_inputs_refuses_foreign_workers_root(monkeypatch, tmp_path):
    workers_root = tmp_path / "hq" / "workers"
    workers_root.mkdir(parents=True)
    monkeypatch.setattr(dispatch_mod.os, "getuid", lambda: workers_root.stat().st_uid + 1)

    with pytest.raises(RuntimeError, match="owner mismatch"):
        dispatch_mod._stage_task_inputs(
            _task(),
            workers_root / "gym-retry-1234" / "workspace",
        )


def test_stage_task_inputs_bounds_stage_script_runtime(monkeypatch, tmp_path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    stage = task_dir / "stage.sh"
    stage.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    workers_root = tmp_path / "hq" / "workers"
    workers_root.mkdir(parents=True)
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    dispatch_mod._stage_task_inputs(
        _task(stages=(Stage(run="stage.sh"),), task_dir=task_dir),
        workers_root / "gym-retry-1234" / "workspace",
    )

    assert calls[0][1]["timeout"] == 300


def test_stage_failure_diagnostic_preserves_stdout_and_stderr(tmp_path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    stage = task_dir / "stage.sh"
    stage.write_text(
        "#!/usr/bin/env bash\necho harmless-warning >&2\necho fatal-context\nexit 7\n",
        encoding="utf-8",
    )
    workers_root = tmp_path / "hq" / "workers"
    workers_root.mkdir(parents=True)

    with pytest.raises(RuntimeError) as excinfo:
        dispatch_mod._stage_task_inputs(
            _task(stages=(Stage(run="stage.sh"),), task_dir=task_dir),
            workers_root / "gym-retry-1234" / "workspace",
        )

    message = str(excinfo.value)
    assert "harmless-warning" in message
    assert "fatal-context" in message


def test_missing_staged_fixture_fails_before_dispatch(monkeypatch, tmp_path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    stage = task_dir / "stage.sh"
    stage.write_text(
        '#!/usr/bin/env bash\nprintf fixture >"$1/input.txt"\n',
        encoding="utf-8",
    )
    hq_root = _hq_root(tmp_path)
    dispatched = False
    verify_calls = 0

    real_verify = dispatch_mod._verify_task_inputs

    def remove_then_verify(sandbox_root):
        nonlocal verify_calls
        verify_calls += 1
        if verify_calls == 2:
            (sandbox_root / "input.txt").unlink()
        return real_verify(sandbox_root)

    def fake_run(*args, **kwargs):
        nonlocal dispatched
        command = args[0]
        if command[0] == "bash":
            Path(command[2], "input.txt").write_text("fixture", encoding="utf-8")
            return subprocess.CompletedProcess([], 0, "", "")
        dispatched = True
        return subprocess.CompletedProcess([], 0, json.dumps({"launchRequestId": "lrq-1"}), "")

    monkeypatch.setattr(dispatch_mod, "_verify_task_inputs", remove_then_verify)
    monkeypatch.setattr(subprocess, "run", fake_run)
    source = WorkerPoolTrajectorySource(hq_bin="hq", hq_root=hq_root)

    trajectory = source.run(_task(stages=(Stage(run="stage.sh"),), task_dir=task_dir))

    assert trajectory.terminal_status == "input_fixtures_missing"
    assert "input.txt" in str(trajectory.error)
    assert dispatched is False


def test_staged_symlinks_fail_before_manifest_or_dispatch(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "linked.txt").symlink_to(tmp_path / "missing.txt")
    with pytest.raises(
        dispatch_mod.InputFixturesMissing, match="staged workspace contains symlink"
    ):
        dispatch_mod._workspace_files(sandbox)


def test_shipped_gym_tasks_have_real_prompts():
    tasks_root = Path(__file__).resolve().parents[1] / "tasks"
    task_paths = sorted(tasks_root.glob("*/task.toml"))
    assert task_paths
    for task_path in task_paths:
        task_data = tomllib.loads(task_path.read_text(encoding="utf-8"))
        prompt = task_data.get("dispatch", {}).get("prompt")
        assert isinstance(prompt, str) and prompt.strip(), task_path
        assert (task_path.parent / prompt).is_file(), task_path


def test_poll_to_terminal_accepts_completed_status():
    class FakeReader:
        def worker_run_for_launch(self, launch_request_id):
            assert launch_request_id == "lrq-completed"
            return {"status": "completed", "run_id": "run-1"}

    source = WorkerPoolTrajectorySource(poll_interval_seconds=0, poll_timeout_seconds=0)

    assert source._poll_to_terminal(FakeReader(), "lrq-completed") == {
        "status": "completed",
        "run_id": "run-1",
    }


def test_terminal_failed_worker_preserves_failure_detail(tmp_path):
    class FakeReader:
        def worker_run_for_launch(self, launch_request_id):
            assert launch_request_id == "lrq-failed"
            return {
                "status": "failed",
                "run_id": "run-1",
                "failure_class": "adapter_boot_crash",
                "failure_detail": "fallback_path_misconfigured: missing LiteLLM op_ref",
            }

        def worker_class_for_launch(self, launch_request_id):
            return None

        def transcript_rows(self, runtime_session_id):
            return []

        def control_rows(self, runtime_session_id):
            return []

    source = WorkerPoolTrajectorySource(poll_interval_seconds=0, poll_timeout_seconds=0)
    source._reader = FakeReader()
    source._dispatch = lambda task: ("lrq-failed", tmp_path)  # type: ignore[method-assign]

    trajectory = source.run(_task())

    assert trajectory.terminal_status == "failed"
    assert trajectory.submitted_result is None
    assert (
        trajectory.error
        == "adapter_boot_crash: fallback_path_misconfigured: missing LiteLLM op_ref"
    )


def test_run_grades_toolcalls_jsonl_by_the_ledger_worker_class(tmp_path, monkeypatch):
    # Review of #7339: the class that decides whether toolcalls.jsonl is
    # trusted comes from the ledger row, not the worker-writable dispatch dir.
    seen = {}

    class FakeReader:
        def worker_run_for_launch(self, launch_request_id):
            return {"status": "completed", "run_id": "run-1"}

        def worker_class_for_launch(self, launch_request_id):
            assert launch_request_id == "lrq-codex"
            return "codex"

    def fake_collect(**kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(dispatch_mod.harness_toolcalls, "collect", fake_collect)
    source = WorkerPoolTrajectorySource(
        hq_root=tmp_path, poll_interval_seconds=0, poll_timeout_seconds=0
    )
    source._reader = FakeReader()
    source._dispatch = lambda task: ("lrq-codex", tmp_path)  # type: ignore[method-assign]

    source.run(_task())

    assert seen == {"dispatch_dir": tmp_path / "dispatch" / "lrq-codex", "worker_class": "codex"}


def test_run_grades_without_the_export_when_the_ledger_class_read_fails(
    tmp_path, monkeypatch, caplog
):
    # Review of #7339: a ledger error on the class lookup must not lose the
    # trajectory; the run is graded as an unknown class instead.
    seen = {}

    class FakeReader:
        def worker_run_for_launch(self, launch_request_id):
            return {"status": "completed", "run_id": "run-1"}

        def worker_class_for_launch(self, launch_request_id):
            raise RuntimeError("ledger unavailable")

        def transcript_rows(self, runtime_session_id):
            return []

        def control_rows(self, runtime_session_id):
            return []

    def fake_collect(**kwargs):
        seen.update(kwargs)
        return [{"name": "read", "arguments": {}}]

    monkeypatch.setattr(dispatch_mod.harness_toolcalls, "collect", fake_collect)
    source = WorkerPoolTrajectorySource(
        hq_root=tmp_path, poll_interval_seconds=0, poll_timeout_seconds=0
    )
    source._reader = FakeReader()
    source._dispatch = lambda task: ("lrq-err", tmp_path)  # type: ignore[method-assign]

    with caplog.at_level("WARNING", logger="gym.dispatch"):
        trajectory = source.run(_task())

    assert trajectory.terminal_status == "completed"
    assert trajectory.sandbox_root == tmp_path
    assert trajectory.harness_calls == [{"name": "read", "arguments": {}}]
    assert seen == {"dispatch_dir": tmp_path / "dispatch" / "lrq-err", "worker_class": None}
    assert "ledger unavailable" in caplog.text


def test_ledger_reader_worker_class_comes_from_the_launch_request(monkeypatch):
    import sqlite3
    import sys
    import types

    from gym.ledger import LedgerReader

    seen = {}

    def fetch(conn, launch_request_id):
        seen["args"] = (conn, launch_request_id)
        return "oss-pi"

    api = types.ModuleType("session_ledger.api")
    api.fetch_launch_request_worker_class = fetch
    monkeypatch.setitem(sys.modules, "session_ledger.api", api)
    conn = sqlite3.connect(":memory:")

    class Db:
        pass

    db = Db()
    db.conn = conn

    assert LedgerReader(db).worker_class_for_launch("lrq-1") == "oss-pi"
    assert seen["args"] == (conn, "lrq-1")


def _sqlite_worker_runs_db(rows):
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE worker_runs (
            run_id TEXT, launch_request_id TEXT, session_id TEXT, status TEXT,
            phase TEXT, worktree_path TEXT, failure_class TEXT, failure_detail TEXT,
            metadata_json TEXT, started_at TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO worker_runs (run_id, launch_request_id, session_id, status, started_at)"
        " VALUES (?, ?, ?, ?, ?)",
        rows,
    )

    class Db:
        pass

    db = Db()
    db.conn = conn
    return db


def test_poll_to_terminal_reads_the_real_ledger_reader():
    # Review of #7391: the poll loop depends on LedgerReader.worker_run_for_launch;
    # drive it through the real reader over a SQLite worker_runs table so a
    # deleted or renamed method fails here instead of in production.
    from gym.ledger import LedgerReader

    db = _sqlite_worker_runs_db(
        [
            ("run-old", "lrq-1", "sess-old", "failed", "2026-09-29T00:00:00Z"),
            ("run-new", "lrq-1", "sess-new", "completed", "2026-09-29T01:00:00Z"),
        ]
    )
    source = WorkerPoolTrajectorySource(poll_interval_seconds=0, poll_timeout_seconds=0)

    run_row = source._poll_to_terminal(LedgerReader(db), "lrq-1")

    assert run_row is not None
    assert run_row["run_id"] == "run-new"
    assert run_row["session_id"] == "sess-new"
    assert run_row["status"] == "completed"
    assert LedgerReader(db).worker_run_for_launch("lrq-missing") is None


def test_ledger_reader_caches_the_target_only_after_a_successful_resolution(monkeypatch):
    from gym.ledger import LedgerReader

    seen = {}
    _fake_level_api(monkeypatch, seen)
    target = types.SimpleNamespace(backend="sqlite")
    calls = []

    def resolver():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("config unavailable")
        return target

    class Db:
        conn = object()

    reader = LedgerReader(Db(), target_resolver=resolver)
    with pytest.raises(RuntimeError):
        reader.resolved_reasoning_level_for_run("run-1")
    assert reader.resolved_reasoning_level_for_run("run-1") == "high"
    assert reader.resolved_reasoning_level_for_run("run-2") == "high"
    assert len(calls) == 2
    assert seen["target"] is target


def _fake_level_api(monkeypatch, seen):
    def fetch(conn, run_id, *, target=None):
        seen.update(conn=conn, run_id=run_id, target=target)
        return "high"

    api = types.ModuleType("session_ledger.api")
    api.fetch_worker_run_resolved_reasoning_level = fetch
    monkeypatch.setitem(sys.modules, "session_ledger.api", api)


def test_ledger_reader_reads_resolved_reasoning_level_from_the_sqlite_conn(monkeypatch):
    from gym.ledger import LedgerReader

    seen = {}
    _fake_level_api(monkeypatch, seen)

    class Db:
        conn = object()

    target = types.SimpleNamespace(backend="sqlite")
    reader = LedgerReader(Db(), target_resolver=lambda: target)
    assert reader.resolved_reasoning_level_for_run("run-1") == "high"
    assert seen == {"conn": Db.conn, "run_id": "run-1", "target": target}


def test_ledger_reader_reads_resolved_reasoning_level_through_the_postgres_target(monkeypatch):
    # Review of #7391: on the Postgres runtime the ledger-owned accessor gets the
    # target (bounded read-only psql), never the compatibility connection.
    from gym.ledger import LedgerReader

    seen = {}
    _fake_level_api(monkeypatch, seen)

    class NoConnDb:
        @property
        def conn(self):
            raise AssertionError("postgres read must not use the compatibility connection")

    target = types.SimpleNamespace(backend="postgres", dsn="postgresql://x")
    reader = LedgerReader(NoConnDb(), target_resolver=lambda: target)
    assert reader.resolved_reasoning_level_for_run("run-1") == "high"
    assert seen == {"conn": None, "run_id": "run-1", "target": target}


def _leveled_reader(level_read):
    class FakeReader:
        def worker_run_for_launch(self, launch_request_id):
            return {"run_id": "run-1", "status": "completed"}

        def worker_class_for_launch(self, launch_request_id):
            return "codex"

        def resolved_reasoning_level_for_run(self, run_id):
            return level_read()

    return FakeReader()


def test_trajectory_carries_resolved_reasoning_level(tmp_path):
    source = WorkerPoolTrajectorySource(
        hq_root=tmp_path, poll_interval_seconds=0, poll_timeout_seconds=0, reasoning_level="high"
    )
    source._reader = _leveled_reader(lambda: "high")
    source._dispatch = lambda task: ("lrq-1", tmp_path)  # type: ignore[method-assign]
    trajectory = source.run(_task())
    assert trajectory.launch_request_id == "lrq-1"
    assert trajectory.resolved_reasoning_level == "high"


def test_failed_level_read_leaves_the_level_unresolved(tmp_path, caplog):
    # A bounded read failure (e.g. psql timeout) must not fail the gym run; it
    # leaves the level unresolved so the suite invalidates the cell.
    def boom():
        raise subprocess.TimeoutExpired(["psql"], 15)

    source = WorkerPoolTrajectorySource(
        hq_root=tmp_path, poll_interval_seconds=0, poll_timeout_seconds=0, reasoning_level="high"
    )
    source._reader = _leveled_reader(boom)
    source._dispatch = lambda task: ("lrq-1", tmp_path)  # type: ignore[method-assign]
    with caplog.at_level("WARNING", logger="gym.dispatch"):
        trajectory = source.run(_task())
    assert trajectory.terminal_status == "completed"
    assert trajectory.resolved_reasoning_level is None
    assert "resolved reasoning level" in caplog.text


def test_unleveled_run_never_reads_the_level(tmp_path):
    def unexpected():
        raise AssertionError("unleveled run must not read the level")

    source = WorkerPoolTrajectorySource(
        hq_root=tmp_path, poll_interval_seconds=0, poll_timeout_seconds=0
    )
    source._reader = _leveled_reader(unexpected)
    source._dispatch = lambda task: ("lrq-1", tmp_path)  # type: ignore[method-assign]
    assert source.run(_task()).resolved_reasoning_level is None


def test_read_submitted_result_accepts_small_regular_json_file(tmp_path):
    (tmp_path / "bench-result.json").write_text('{"answer": 42}', encoding="utf-8")

    source = WorkerPoolTrajectorySource()

    assert source._read_submitted_result({"worktree_path": str(tmp_path)}) == {"answer": 42}


def test_read_submitted_result_rejects_symlink(tmp_path):
    ground_truth = tmp_path / "ground-truth.json"
    ground_truth.write_text('{"answer": "secret"}', encoding="utf-8")
    (tmp_path / "bench-result.json").symlink_to(ground_truth)

    source = WorkerPoolTrajectorySource()

    assert source._read_submitted_result({"worktree_path": str(tmp_path)}) is None


def test_read_submitted_result_rejects_fifo_without_blocking(tmp_path):
    os.mkfifo(tmp_path / "bench-result.json")

    source = WorkerPoolTrajectorySource()

    assert source._read_submitted_result({"worktree_path": str(tmp_path)}) is None


def test_read_submitted_result_rejects_oversized_file(tmp_path):
    (tmp_path / "bench-result.json").write_bytes(b"x" * (1024 * 1024 + 1))

    source = WorkerPoolTrajectorySource()

    assert source._read_submitted_result({"worktree_path": str(tmp_path)}) is None


# --- GYM-M1: sweep-level model / worker-class overrides ----------------------
#
# The corpus is only useful for comparing models if one suite can be graded
# against several of them. Before this, model and persona came only from each
# task.toml, so a sweep meant editing every task file.


def _dispatch_cmd(monkeypatch, tmp_path, source_kwargs, task_kwargs):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args[0])
        return subprocess.CompletedProcess([], 0, json.dumps({"launchRequestId": "lrq-1"}), "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    source = WorkerPoolTrajectorySource(
        dispatch_retry_backoff_seconds=0,
        hq_bin="hq",
        hq_root=_hq_root(tmp_path),
        **source_kwargs,
    )
    source._dispatch(_task(**task_kwargs))
    return calls[0]


def test_sweep_model_reaches_the_dispatch_command(monkeypatch, tmp_path):
    cmd = _dispatch_cmd(
        monkeypatch, tmp_path, {"model": "local/qwen2.5-coder-32b-q5"}, {"dispatch": {}}
    )
    assert cmd[cmd.index("--model") + 1] == "local/qwen2.5-coder-32b-q5"
    assert cmd[cmd.index("--worker-class") + 1] == "oss-local"


def test_sweep_worker_class_reaches_the_dispatch_command(monkeypatch, tmp_path):
    # Built directly rather than via _task, which pins persona="worker" -- with
    # that pin present this would assert the task's own persona and pass without
    # the sweep override existing at all.
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args[0])
        return subprocess.CompletedProcess([], 0, json.dumps({"launchRequestId": "lrq-1"}), "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    unpinned = Task(
        id="retry",
        title="Retry",
        suite="unit",
        persona=None,
        result_schema=(),
        verifier_entrypoint="verifier.py",
        dispatch={},
    )
    source = WorkerPoolTrajectorySource(
        dispatch_retry_backoff_seconds=0,
        hq_bin="hq",
        hq_root=_hq_root(tmp_path),
        worker_class="oss-local",
    )
    source._dispatch(unpinned)
    cmd = calls[0]
    assert cmd[cmd.index("--worker-class") + 1] == "oss-local"


def test_a_dispatch_persona_pin_keeps_it_under_a_sweep(monkeypatch, tmp_path):
    cmd = _dispatch_cmd(
        monkeypatch,
        tmp_path,
        {"model": "local/qwen2.5-coder-32b-q5", "worker_class": "oss-local"},
        {"dispatch": {"persona": "worker"}},
    )
    assert cmd[cmd.index("--worker-class") + 1] == "worker"


def test_explicit_worker_class_overrides_task_persona_metadata(monkeypatch, tmp_path):
    cmd = _dispatch_cmd(monkeypatch, tmp_path, {"worker_class": "oss-local"}, {"dispatch": {}})
    assert cmd[cmd.index("--worker-class") + 1] == "oss-local"


def test_a_task_that_pins_a_model_keeps_it_under_a_sweep(monkeypatch, tmp_path):
    # A task pinning a model exists to exercise THAT model. A sweep selects where
    # the task is indifferent; it must not silently regrade a pinned task.
    cmd = _dispatch_cmd(
        monkeypatch,
        tmp_path,
        {"model": "local/qwen2.5-coder-32b-q5"},
        {"dispatch": {"model": "claude-opus-5"}},
    )
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5"


def test_no_override_dispatches_without_a_model_flag(monkeypatch, tmp_path):
    cmd = _dispatch_cmd(monkeypatch, tmp_path, {}, {"dispatch": {}})
    assert "--model" not in cmd


def test_dispatch_forwards_priority(monkeypatch, tmp_path):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess([], 0, json.dumps({"launchRequestId": "lrq-1"}), "")

    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompt.md").write_text("Original task prompt.\n", encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", fake_run)
    source = WorkerPoolTrajectorySource(
        dispatch_retry_backoff_seconds=0,
        hq_bin="hq",
        hq_root=_hq_root(tmp_path),
        priority="low",
    )
    source._dispatch(_task(dispatch={"prompt": "prompt.md"}))
    cmd = calls[0][0][0]
    assert cmd[cmd.index("--priority") + 1] == "low"


def test_dispatch_defaults_to_low_priority(monkeypatch, tmp_path):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess([], 0, json.dumps({"launchRequestId": "lrq-1"}), "")

    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompt.md").write_text("Original task prompt.\n", encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", fake_run)
    source = WorkerPoolTrajectorySource(
        dispatch_retry_backoff_seconds=0, hq_bin="hq", hq_root=_hq_root(tmp_path)
    )
    source._dispatch(_task(dispatch={"prompt": "prompt.md"}))
    assert calls[0][0][0][calls[0][0][0].index("--priority") + 1] == "low"


def _metered_reader(cost_read):
    class FakeReader:
        def worker_run_for_launch(self, launch_request_id):
            return {"run_id": "run-1", "status": "completed"}

        def worker_class_for_launch(self, launch_request_id):
            return "codex"

        def cost_for_launch(self, launch_request_id, *, worker_class=None):
            return cost_read(launch_request_id)

    return FakeReader()


def test_trajectory_carries_the_launch_metrics(tmp_path):
    metrics = {"attempts": 2, "metrics_complete": True}
    source = WorkerPoolTrajectorySource(
        hq_root=tmp_path, poll_interval_seconds=0, poll_timeout_seconds=0
    )
    source._reader = _metered_reader(lambda lrq: {"runs": 2, "metrics": metrics})
    source._dispatch = lambda task: ("lrq-1", tmp_path)  # type: ignore[method-assign]
    assert source.run(_task()).metrics is metrics


def test_failed_metrics_read_records_the_cell_as_unmeasured(tmp_path, caplog):
    # A failed ledger read must neither drop the verdict nor read as zero spend.
    def boom(lrq):
        raise subprocess.TimeoutExpired(["psql"], 15)

    source = WorkerPoolTrajectorySource(
        hq_root=tmp_path, poll_interval_seconds=0, poll_timeout_seconds=0
    )
    source._reader = _metered_reader(boom)
    source._dispatch = lambda task: ("lrq-1", tmp_path)  # type: ignore[method-assign]
    with caplog.at_level("WARNING", logger="gym.dispatch"):
        trajectory = source.run(_task())
    assert trajectory.terminal_status == "completed"
    assert trajectory.metrics["metrics_complete"] is False
    assert trajectory.metrics["attempts"] == 0
    assert trajectory.metrics["token_usage_output"] is None
    assert trajectory.metrics["worker_class"] == "codex"
    assert "ledger metrics" in caplog.text


def test_launch_without_worker_runs_is_unmeasured(tmp_path):
    source = WorkerPoolTrajectorySource(
        hq_root=tmp_path, poll_interval_seconds=0, poll_timeout_seconds=0
    )
    source._reader = _metered_reader(lambda lrq: None)
    source._dispatch = lambda task: ("lrq-1", tmp_path)  # type: ignore[method-assign]
    assert source.run(_task()).metrics["metrics_complete"] is False


def test_timeout_keeps_best_effort_metrics(tmp_path):
    source = WorkerPoolTrajectorySource(
        hq_root=tmp_path, poll_interval_seconds=0, poll_timeout_seconds=0
    )
    source._reader = _metered_reader(
        lambda lrq: {"metrics": {"attempts": 1, "metrics_complete": False}}
    )
    source._dispatch = lambda task: ("lrq-1", tmp_path)  # type: ignore[method-assign]
    source._poll_to_terminal = lambda reader, lrq, sandbox=None: None  # type: ignore[method-assign]
    trajectory = source.run(_task())
    assert trajectory.terminal_status == "timeout"
    assert trajectory.metrics["attempts"] == 1
    assert trajectory.metrics["metrics_read_at"]


@pytest.mark.parametrize("arm", ["off", "spill"])
def test_gym_dispatch_receipt_is_readable_without_launch_payload_arm(monkeypatch, tmp_path, arm):
    """Exercise gym's command and dispatch's real arm payload builders together."""
    import sqlite3
    from cwp_dispatch import tool_output_compression_arm as toc_arm
    from gym.ledger import LedgerReader

    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE launch_requests (launch_request_id TEXT, request_payload_json TEXT);
        CREATE TABLE worker_runs (launch_request_id TEXT, metadata_json TEXT);
    """)

    def accept(cmd, **kwargs):
        # Parse the flags gym actually sends, then use the very functions
        # dispatch calls when constructing workerSpec and the run receipt.
        args = types.SimpleNamespace(
            tool_output_compression=cmd[cmd.index("--tool-output-compression") + 1]
        )
        project = cmd[cmd.index("--project") + 1]
        spec = toc_arm.apply_launch_arm(
            {"model": "test-model"}, toc_arm.launch_arm_from_args(args, project)
        )
        receipt = toc_arm.worker_run_metadata(spec)
        conn.execute(
            "INSERT INTO launch_requests VALUES (?, ?)",
            ("lrq-1", json.dumps({"workerId": "gym-test"})),
        )
        conn.execute("INSERT INTO worker_runs VALUES (?, ?)", ("lrq-1", json.dumps(receipt)))
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"launchRequestId": "lrq-1"}), "")

    monkeypatch.setattr(subprocess, "run", accept)
    source = WorkerPoolTrajectorySource(hq_bin="hq", hq_root=_hq_root(tmp_path), compression=arm)
    lrq, _ = source._dispatch(_task())
    result = LedgerReader(types.SimpleNamespace(conn=conn)).compression_arm_for_launch(lrq)
    assert result == {"requested": arm, "recorded": [arm], "effective": [None]}
    # Requested evidence must never stand in for adapter evidence.
    conn.close()


@pytest.mark.parametrize("status", ["running", "succeeded"])
def test_completed_gym_grade_reuses_saved_launch_without_dispatch(monkeypatch, tmp_path, status):
    from agent_os_config import roots
    from gym import grader
    from gym.ledger import LedgerReader
    from session_ledger.canonical_views import tool_output_compression_ab as reads

    reader = LedgerReader(None)
    monkeypatch.setattr(reader, "worker_run_for_launch", lambda _: {"status": status})
    monkeypatch.setattr(reader, "_read_args", lambda: (None, None))
    monkeypatch.setattr(
        reads, "_rows", lambda *args: [{"request_payload_json": '{"workerId":"gym-saved"}'}]
    )
    monkeypatch.setattr(roots, "hq_root", lambda: tmp_path)
    sandbox = tmp_path / "workers" / "gym-saved" / "workspace"
    sandbox.mkdir(parents=True)
    collected = []

    def collect(source, lrq, path, row):
        assert source._reader is reader
        collected.append((lrq, path, row["status"]))
        return object()

    def no_dispatch(*args, **kwargs):
        raise AssertionError("recovery must never dispatch")

    monkeypatch.setattr(WorkerPoolTrajectorySource, "_dispatch", no_dispatch)
    monkeypatch.setattr(WorkerPoolTrajectorySource, "collect_terminal", collect)
    monkeypatch.setattr(
        grader,
        "grade",
        lambda task, trajectory: types.SimpleNamespace(outcome="passed", reason="fresh grade"),
    )
    result = reader.completed_gym_grade("lrq-saved", "coding-toc-commands")
    if status == "running":
        assert result is None and collected == []
    else:
        assert result["verdict"] == "passed" and result["regradedAt"]
        assert collected == [("lrq-saved", sandbox, "succeeded")]
        sandbox.rmdir()
        assert reader.completed_gym_grade("lrq-saved", "coding-toc-commands") is None


@pytest.mark.parametrize(
    "payload", [None, "null", {}, "{}", [], "[]", True, "true", 7, "7", "", "{invalid"]
)
def test_completed_gym_grade_skips_payload_without_worker_id(monkeypatch, payload):
    from gym.ledger import LedgerReader
    from session_ledger.canonical_views import tool_output_compression_ab as reads

    reader = LedgerReader(None)
    monkeypatch.setattr(reader, "worker_run_for_launch", lambda _: {"status": "succeeded"})
    monkeypatch.setattr(reader, "_read_args", lambda: (None, None))
    monkeypatch.setattr(reads, "_rows", lambda *args: [{"request_payload_json": payload}])

    def no_collect(*args, **kwargs):
        raise AssertionError("a launch without a worker ID must not collect a trajectory")

    monkeypatch.setattr(WorkerPoolTrajectorySource, "collect_terminal", no_collect)
    assert reader.completed_gym_grade("lrq-saved", "coding-toc-commands") is None


@pytest.mark.parametrize("read_name", ["transcript_rows", "control_rows"])
@pytest.mark.parametrize("failures", [2, 3])
@pytest.mark.parametrize("backend", ["postgres", "sqlite"])
def test_terminal_evidence_read_retries_and_preserves_harness_error(
    tmp_path, monkeypatch, read_name, failures, backend
):
    from conftest import FIXTURES_DIR
    from gym.suite import discover_tasks, run_suite

    calls = {"transcript_rows": 0, "control_rows": 0}
    sleeps = []
    evidence = [{"tool_metadata_json": '{"name":"read","arguments":{}}'}]
    controls = [{"control_id": "test-control"}]
    stderr = "discarded prefix" + "x" * 600 + "canceling statement due to statement timeout"

    class FakeReader:
        def worker_run_for_launch(self, lrq):
            return {"status": "completed", "run_id": "run-1", "session_id": "ses-1"}

        def transcript_rows(self, session_id):
            return self.read("transcript_rows", session_id, evidence)

        def control_rows(self, session_id):
            return self.read("control_rows", session_id, controls)

        def read(self, name, session_id, rows):
            assert session_id == "ses-1"
            calls[name] += 1
            if name == read_name and calls[name] <= failures:
                if backend == "sqlite":
                    raise sqlite3.OperationalError("database is locked")
                raise subprocess.CalledProcessError(3, ["psql"], stderr=stderr)
            return rows

        def worker_class_for_launch(self, lrq):
            return None

        def cost_for_launch(self, lrq, **kwargs):
            return None

    monkeypatch.setattr(dispatch_mod.time, "sleep", sleeps.append)
    monkeypatch.setattr(dispatch_mod.harness_toolcalls, "collect", lambda **kwargs: [])
    (tmp_path / "bench-result.json").write_text('{"answer":"ok"}')
    source = WorkerPoolTrajectorySource(hq_root=tmp_path)
    source._reader = FakeReader()
    source._dispatch = lambda task: ("lrq-1", tmp_path)
    tasks = discover_tasks(FIXTURES_DIR / "mini-suite", task="mini-pass")
    seen = []
    from gym import grader

    real_grade = grader.grade

    def grade(task, trajectory, **kwargs):
        seen.append(trajectory)
        return real_grade(task, trajectory, **kwargs)

    monkeypatch.setattr(grader, "grade", grade)
    result = run_suite(tasks, source, suite="all")
    cell = result.graded[0].cell
    assert calls[read_name] == 3
    assert sleeps == [0.25, 0.5]
    if failures == 2:
        assert cell.outcome == "passed"
        assert seen[0].transcript_rows == evidence
        assert seen[0].control_rows == controls
    else:
        assert cell.outcome == "error"
        assert "3 attempts" in cell.reason
        assert read_name in cell.reason
        if backend == "postgres":
            assert stderr[-500:] in cell.reason
            assert "discarded prefix" not in cell.reason
        else:
            assert "database is locked" in cell.reason
        assert seen == []


@pytest.mark.parametrize("prior_unknown", [False, True])
def test_drain_wait_timeout_is_harness_error(monkeypatch, tmp_path, prior_unknown):
    from conftest import FIXTURES_DIR

    clock = [0.0]
    monkeypatch.setattr(dispatch_mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        dispatch_mod.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    launches = []

    def fake_run(cmd, **kwargs):
        if "drain-status" in cmd:
            assert "--no-reap" in cmd
            return subprocess.CompletedProcess(cmd, 0, '{"isDraining": true, "epoch": 622}', "")
        launches.append(cmd)
        if prior_unknown and len(launches) == 1:
            return subprocess.CompletedProcess(cmd, 75, "", "service unavailable")
        return subprocess.CompletedProcess(
            cmd, 6, "", "dispatch refused: drain in effect (epoch=622)"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    source = WorkerPoolTrajectorySource(
        hq_bin="hq", hq_root=_hq_root(tmp_path), drain_wait_timeout_seconds=10
    )
    if prior_unknown:
        with pytest.raises(dispatch_mod.DispatchAcceptanceUnknown, match="epoch=622"):
            source._dispatch(_task())
    else:
        from gym.suite import discover_tasks, run_suite

        tasks = discover_tasks(FIXTURES_DIR / "mini-suite", task="mini-pass")
        result = run_suite(tasks, source, suite="all")
        assert result.graded[0].cell.outcome == "error"
        assert "epoch=622" in result.graded[0].cell.reason


def test_drain_refusals_retry_same_launch_without_using_attempt_budget(
    monkeypatch, tmp_path, caplog
):
    launches = []
    statuses = iter([True, False, False])
    monkeypatch.setattr(dispatch_mod.time, "sleep", lambda _: None)

    def fake_run(cmd, **kwargs):
        if "drain-status" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, json.dumps({"isDraining": next(statuses), "epoch": 622}), ""
            )
        launches.append(cmd)
        if len(launches) <= 2:
            return subprocess.CompletedProcess(
                cmd, 6, "", "dispatch refused: drain in effect (epoch=622)"
            )
        return subprocess.CompletedProcess(cmd, 0, '{"launchRequestId": "lrq-drain"}', "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    source = WorkerPoolTrajectorySource(
        hq_bin="hq", hq_root=_hq_root(tmp_path), dispatch_max_attempts=1
    )
    launch, sandbox = source._dispatch(_task())
    assert launch == "lrq-drain"
    assert launches[0] == launches[1] == launches[2]
    assert sandbox.is_dir()
    assert "waiting for dispatch drain epoch=622" in caplog.text
    assert "wait ended: cleared" in caplog.text


@pytest.mark.parametrize("wait_budget, expected_sleeps", [(0.25, [0.25]), (2.5, [1.0, 1.0, 0.5])])
@pytest.mark.parametrize("prior_unknown", [False, True])
def test_drain_status_mismatch_backs_off_within_wait_budget(
    monkeypatch, tmp_path, wait_budget, expected_sleeps, prior_unknown
):
    clock = [0.0]
    sleeps = []
    launches = []

    def fake_sleep(seconds):
        if seconds:
            sleeps.append(seconds)
        clock[0] += seconds

    def fake_run(cmd, **kwargs):
        if "drain-status" in cmd:
            return subprocess.CompletedProcess(cmd, 0, '{"isDraining": false}', "")
        launches.append(cmd)
        if prior_unknown and len(launches) == 1:
            return subprocess.CompletedProcess(cmd, 75, "", "service unavailable")
        # Fail deterministically on broken code rather than spinning forever
        # with a fake clock that only advances when dispatch sleeps.
        assert len(launches) <= len(expected_sleeps) + 1 + int(prior_unknown)
        return subprocess.CompletedProcess(
            cmd, 6, "", "dispatch refused: drain in effect (epoch=622)"
        )

    monkeypatch.setattr(dispatch_mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(dispatch_mod.time, "sleep", fake_sleep)
    monkeypatch.setattr(subprocess, "run", fake_run)
    source = WorkerPoolTrajectorySource(
        hq_bin="hq",
        hq_root=_hq_root(tmp_path),
        drain_wait_timeout_seconds=wait_budget,
        dispatch_retry_backoff_seconds=0,
    )
    error = (
        dispatch_mod.DispatchAcceptanceUnknown
        if prior_unknown
        else dispatch_mod.DispatchDrainTimeout
    )
    with pytest.raises(error, match="epoch=622"):
        source._dispatch(_task())
    assert sleeps == expected_sleeps
    assert clock[0] == wait_budget
    assert all(cmd == launches[0] for cmd in launches)


def test_non_drain_exit_six_still_has_unknown_acceptance(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 6, "", "another refusal")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(dispatch_mod.DispatchAcceptanceUnknown, match="exit 6"):
        WorkerPoolTrajectorySource(hq_bin="hq", hq_root=_hq_root(tmp_path))._dispatch(_task())
    assert len(calls) == 1


def test_fix_activation_dispatches(monkeypatch, tmp_path):
    from dataclasses import replace

    source = WorkerPoolTrajectorySource(hq_bin="hq", hq_root=_hq_root(tmp_path))
    seen = []

    def dispatch(task):
        seen.append(task.suite)
        raise RuntimeError("synthetic dispatch sentinel")

    monkeypatch.setattr(source, "_dispatch", dispatch)
    result = source.run(replace(_task(), suite="fix"))
    assert seen == ["fix"]
    assert result.error == "synthetic dispatch sentinel"


_SCHEMA_REFUSAL = (
    "PostgresSchemaVersionError: Postgres session-ledger schema is behind Alembic head "
    "operator_merge_policy_268; found operator_merge_pending_267. "
    "A migration may be in progress; retry briefly."
)


@pytest.mark.parametrize(
    "diagnostic",
    [
        _SCHEMA_REFUSAL,
        "dispatch refused:\nSCHEMA IS BEHIND ALEMBIC HEAD operator_merge_policy_268; "
        "found operator_merge_pending_267",
        "schema is behind Alembic head operator_merge_policy_268; found operator_merge_pending_267",
    ],
)
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_schema_refusals_retry_same_launch(monkeypatch, tmp_path, diagnostic, stream):
    clock = [0.0]
    launches = []
    monkeypatch.setattr(dispatch_mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        dispatch_mod.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )

    def fake_run(cmd, **kwargs):
        launches.append(cmd)
        if len(launches) <= 2:
            return subprocess.CompletedProcess(
                cmd,
                1,
                diagnostic if stream == "stdout" else "",
                diagnostic if stream == "stderr" else "",
            )
        return subprocess.CompletedProcess(cmd, 0, '{"launchRequestId": "lrq-schema"}', "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    source = WorkerPoolTrajectorySource(
        hq_bin="hq", hq_root=_hq_root(tmp_path), dispatch_max_attempts=1
    )
    launch, sandbox = source._dispatch(_task())
    assert launch == "lrq-schema"
    assert len(launches) == 3
    assert all(cmd == launches[0] for cmd in launches)
    assert sandbox.is_dir()
    assert clock[0] == 3.0


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_backend_exception_name_alone_does_not_trigger_schema_wait(monkeypatch, tmp_path, stream):
    launches = []

    def fake_run(cmd, **kwargs):
        launches.append(cmd)
        diagnostic = "PostgresSchemaVersionError"
        return subprocess.CompletedProcess(
            cmd,
            1,
            diagnostic if stream == "stdout" else "",
            diagnostic if stream == "stderr" else "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        dispatch_mod.time, "sleep", lambda seconds: pytest.fail("unexpected schema wait")
    )
    source = WorkerPoolTrajectorySource(hq_bin="hq", hq_root=_hq_root(tmp_path))
    with pytest.raises(dispatch_mod.DispatchAcceptanceUnknown, match="PostgresSchemaVersionError"):
        source._dispatch(_task())
    assert len(launches) == 1


@pytest.mark.parametrize("prior_unknown", [False, True])
def test_schema_wait_timeout_is_harness_error(monkeypatch, tmp_path, prior_unknown):
    from conftest import FIXTURES_DIR
    from gym.suite import discover_tasks, run_suite

    clock = [0.0]
    sleeps = []
    launches = []
    monkeypatch.setattr(dispatch_mod.time, "monotonic", lambda: clock[0])

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    def fake_run(cmd, **kwargs):
        launches.append(cmd)
        assert len(launches) <= 6
        if prior_unknown and len(launches) == 1:
            return subprocess.CompletedProcess(cmd, 75, "", "service unavailable")
        return subprocess.CompletedProcess(cmd, 1, "", _SCHEMA_REFUSAL)

    monkeypatch.setattr(dispatch_mod.time, "sleep", sleep)
    monkeypatch.setattr(subprocess, "run", fake_run)
    source = WorkerPoolTrajectorySource(
        hq_bin="hq",
        hq_root=_hq_root(tmp_path),
        schema_wait_timeout_seconds=10,
        dispatch_retry_backoff_seconds=0,
    )
    tasks = discover_tasks(FIXTURES_DIR / "mini-suite", task="mini-pass")
    result = run_suite(tasks, source, suite="all")
    cell = result.graded[0].cell
    assert cell.outcome == "error"
    assert "operator_merge_policy_268" in cell.reason
    assert "operator_merge_pending_267" in cell.reason
    assert "wait bound exceeded" in cell.reason
    assert clock[0] == 10
    assert [s for s in sleeps if s] == [1, 2, 4, 3]
    assert all(cmd == launches[0] for cmd in launches)
    # The schema message itself could follow enqueue, even with no earlier
    # generic failure. Preserve the original intent for acceptance recovery.
    assert "workspace retained" in cell.reason
    sandbox = Path(launches[0][launches[0].index("--prompt") + 1]).parent
    assert sandbox.is_dir()


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_schema_timeout_bounds_diagnostic_and_preserves_unknown_acceptance(
    monkeypatch, tmp_path, caplog, stream
):
    diagnostic = "unrelated traceback detail\n" * 1000 + _SCHEMA_REFUSAL
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd,
            1,
            diagnostic if stream == "stdout" else "",
            diagnostic if stream == "stderr" else "",
        )

    clock = [0.0]
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(dispatch_mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        dispatch_mod.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    source = WorkerPoolTrajectorySource(
        hq_bin="hq", hq_root=_hq_root(tmp_path), schema_wait_timeout_seconds=1
    )
    with pytest.raises(dispatch_mod.DispatchSchemaAcceptanceUnknown) as caught:
        source._dispatch(_task())
    assert len(calls) == 2
    assert caught.value.sandbox.is_dir()
    assert caught.value.launch_key == calls[0][calls[0].index("--idempotency-key") + 1]
    assert "operator_merge_policy_268" in str(caught.value)
    assert "unrelated traceback detail" not in str(caught.value)
    assert "unrelated traceback detail" not in caplog.text
    assert "waiting for dispatch schema" in caplog.text
    assert len(str(caught.value)) < 1000


def test_drain_timeout_after_schema_failure_preserves_unknown_acceptance(monkeypatch, tmp_path):
    responses = iter(
        [
            subprocess.CompletedProcess([], 1, "", _SCHEMA_REFUSAL),
            subprocess.CompletedProcess([], 6, "", "dispatch refused: drain in effect (epoch=622)"),
        ]
    )
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return next(responses)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(dispatch_mod.time, "sleep", lambda seconds: None)
    source = WorkerPoolTrajectorySource(
        hq_bin="hq", hq_root=_hq_root(tmp_path), drain_wait_timeout_seconds=0
    )
    with pytest.raises(dispatch_mod.DispatchAcceptanceUnknown) as caught:
        source._dispatch(_task())
    assert "epoch=622" in str(caught.value)
    assert caught.value.sandbox.is_dir()
    assert len(calls) == 2 and calls[0] == calls[1]


def test_run_preserves_acceptance_unknown_recovery_evidence(monkeypatch, tmp_path):
    source = WorkerPoolTrajectorySource(hq_bin="hq", hq_root=_hq_root(tmp_path))
    failure = dispatch_mod.DispatchAcceptanceUnknown(
        "dispatch drain wait bound exceeded", sandbox=tmp_path, launch_key="original-launch"
    )

    def refuse(task):
        raise failure

    monkeypatch.setattr(source, "_dispatch", refuse)
    with pytest.raises(dispatch_mod.DispatchAcceptanceUnknown) as caught:
        source.run(_task())
    assert caught.value is failure
    assert caught.value.sandbox == tmp_path
    assert caught.value.launch_key == "original-launch"


@pytest.mark.parametrize("failure", ["drain", "transient"])
def test_failure_after_schema_refusal_is_suite_harness_error(monkeypatch, tmp_path, failure):
    from conftest import FIXTURES_DIR
    from gym.suite import discover_tasks, run_suite

    clock = [0.0]
    launches = []
    monkeypatch.setattr(dispatch_mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        dispatch_mod.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )

    def fake_run(cmd, **kwargs):
        launches.append(cmd)
        if len(launches) == 1:
            return subprocess.CompletedProcess(cmd, 1, "", _SCHEMA_REFUSAL)
        if failure == "drain":
            return subprocess.CompletedProcess(
                cmd, 6, "", "dispatch refused: drain in effect (epoch=622)"
            )
        return subprocess.CompletedProcess(cmd, 75, "", "service unavailable")

    monkeypatch.setattr(subprocess, "run", fake_run)
    source = WorkerPoolTrajectorySource(
        hq_bin="hq", hq_root=_hq_root(tmp_path), drain_wait_timeout_seconds=0
    )
    tasks = discover_tasks(FIXTURES_DIR / "mini-suite", task="mini-pass")
    result = run_suite(tasks, source, suite="all")
    cell = result.graded[0].cell
    assert cell.outcome == "error"
    assert ("epoch=622" if failure == "drain" else "service unavailable") in cell.reason
    assert "workspace retained" in cell.reason
    assert len(launches) == (2 if failure == "drain" else 4)
    assert all(cmd == launches[0] for cmd in launches)
    sandbox = Path(launches[0][launches[0].index("--prompt") + 1]).parent
    assert sandbox.is_dir()


@pytest.mark.parametrize("schema_clears", [False, True])
def test_drain_wait_does_not_consume_schema_budget(monkeypatch, tmp_path, schema_clears):
    clock = [0.0]
    sleeps = []
    launches = []
    statuses = iter([True, False])
    monkeypatch.setattr(dispatch_mod.time, "monotonic", lambda: clock[0])

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    def fake_run(cmd, **kwargs):
        if "drain-status" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, json.dumps({"isDraining": next(statuses), "epoch": 622}), ""
            )
        launches.append(cmd)
        assert len(launches) <= 4
        if len(launches) == 2:
            return subprocess.CompletedProcess(
                cmd, 6, "", "dispatch refused: drain in effect (epoch=622)"
            )
        if schema_clears and len(launches) == 4:
            return subprocess.CompletedProcess(cmd, 0, '{"launchRequestId": "lrq-schema"}', "")
        return subprocess.CompletedProcess(cmd, 1, "", _SCHEMA_REFUSAL)

    monkeypatch.setattr(dispatch_mod.time, "sleep", sleep)
    monkeypatch.setattr(subprocess, "run", fake_run)
    source = WorkerPoolTrajectorySource(
        hq_bin="hq",
        hq_root=_hq_root(tmp_path),
        schema_wait_timeout_seconds=3,
        drain_wait_timeout_seconds=20,
    )
    if schema_clears:
        launch, sandbox = source._dispatch(_task())
        assert launch == "lrq-schema" and sandbox.is_dir()
    else:
        with pytest.raises(dispatch_mod.DispatchSchemaAcceptanceUnknown) as caught:
            source._dispatch(_task())
        assert caught.value.sandbox.is_dir()
    assert len(launches) == 4
    assert all(cmd == launches[0] for cmd in launches)
    # Three seconds of schema sleep plus five seconds of drain polling and
    # one second of drain retry pacing; the schema still gets its full budget.
    assert sleeps == [1, 5, 1, 2]
    assert clock[0] == 9


@pytest.mark.parametrize("budget", [-1, float("nan"), float("inf")])
def test_schema_wait_budget_validation(tmp_path, budget):
    source = WorkerPoolTrajectorySource(
        hq_root=_hq_root(tmp_path), schema_wait_timeout_seconds=budget
    )
    with pytest.raises(ValueError, match="GYM_SCHEMA_WAIT_TIMEOUT_SECONDS"):
        source._dispatch(_task())


def test_memory_crit_refuses_before_dispatch_subprocess(monkeypatch):
    monkeypatch.setattr(
        "cwp_dispatch.resource_admission.sample_headroom_pause", lambda: "memory pressure CRIT"
    )
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_kw: pytest.fail("must not enqueue under CRIT")
    )
    with pytest.raises(
        dispatch_mod.AdmissionRefused, match="admission_refused: memory pressure CRIT"
    ):
        WorkerPoolTrajectorySource(drain_wait_timeout_seconds=0)._dispatch(_task())


@pytest.mark.parametrize("pause", ["memory pressure CRIT", "CPU headroom exhausted"])
def test_run_headroom_refusal_is_suite_harness_error(monkeypatch, tmp_path, pause):
    from conftest import FIXTURES_DIR
    from gym.suite import discover_tasks, run_suite

    monkeypatch.setattr("cwp_dispatch.resource_admission.sample_headroom_pause", lambda: pause)
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: pytest.fail("refused task must not dispatch")
    )
    source = WorkerPoolTrajectorySource(hq_root=_hq_root(tmp_path), drain_wait_timeout_seconds=0)
    tasks = discover_tasks(FIXTURES_DIR / "mini-suite", task="mini-pass")
    result = run_suite(tasks, source, suite="all")
    cell = result.graded[0].cell
    assert cell.outcome == "error"
    assert pause in cell.reason
    assert result.summary_dict()["refused"] == 1
    assert not list((source.hq_root / "workers").iterdir())


def test_headroom_wait_resamples_before_dispatch(monkeypatch):
    samples = iter(["CPU headroom exhausted", None])
    sleeps = []
    monkeypatch.setattr(
        "cwp_dispatch.resource_admission.sample_headroom_pause", lambda: next(samples)
    )
    monkeypatch.setattr(dispatch_mod.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("reached dispatch")),
    )
    with pytest.raises(RuntimeError, match="reached dispatch"):
        WorkerPoolTrajectorySource(drain_wait_timeout_seconds=2)._dispatch(_task())
    assert len(sleeps) == 1


def test_headroom_sensor_error_fails_open(monkeypatch, caplog):
    def broken_sensor():
        raise ImportError("sensor dependency unavailable")

    monkeypatch.setattr("cwp_dispatch.resource_admission.sample_headroom_pause", broken_sensor)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("reached dispatch")),
    )
    with pytest.raises(RuntimeError, match="reached dispatch"):
        WorkerPoolTrajectorySource()._dispatch(_task())
    assert "headroom unavailable" in caplog.text


@pytest.mark.parametrize("receipt", [True, False])
def test_endpoint_admission_not_repeated_after_schema_backoff(monkeypatch, tmp_path, receipt):
    from gym.endpoint_api import EndpointError
    from gym.endpoints import Manager

    clock = [0.0]
    admissions, launches = [], []

    def guard(self, model, seconds, key):
        admissions.append(key)
        if clock[0] >= 1:
            raise EndpointError("endpoint cap or TTL would be exceeded")

    def invoke(cmd, **kwargs):
        launches.append(cmd)
        if len(launches) == 1:
            return subprocess.CompletedProcess(cmd, 1, "", "schema is behind alembic head")
        if receipt:
            return subprocess.CompletedProcess(cmd, 0, '{"launchRequestId":"lrq-original"}', "")
        return subprocess.CompletedProcess(cmd, 1, "", "permanent failure")

    monkeypatch.setattr(Manager, "guard", guard)
    monkeypatch.setattr(Manager, "bind_cell", lambda *args: None)
    monkeypatch.setattr(subprocess, "run", invoke)
    monkeypatch.setattr(dispatch_mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        dispatch_mod.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    source = WorkerPoolTrajectorySource(hq_bin="hq", hq_root=_hq_root(tmp_path))
    if receipt:
        launch, sandbox = source._dispatch(_task())
        assert launch == "lrq-original"
    else:
        with pytest.raises(dispatch_mod.DispatchAcceptanceUnknown) as raised:
            source._dispatch(_task())
        sandbox = raised.value.sandbox
        assert raised.value.launch_key == admissions[0]
    assert sandbox.is_dir()
    assert len(admissions) == 1
    assert len(launches) == 2
    assert launches[0] == launches[1]


def test_endpoint_initial_refusal_cleans_staged_workspace(monkeypatch, tmp_path):
    from gym.endpoint_api import EndpointError
    from gym.endpoints import Manager
    from unittest.mock import Mock

    monkeypatch.setattr(Manager, "guard", Mock(side_effect=EndpointError("cap refused")))
    invoke = Mock()
    monkeypatch.setattr(subprocess, "run", invoke)
    root = _hq_root(tmp_path)
    with pytest.raises(EndpointError):
        WorkerPoolTrajectorySource(hq_bin="hq", hq_root=root)._dispatch(_task())
    invoke.assert_not_called()
    assert list((root / "workers").iterdir()) == []


def test_endpoint_binding_failure_retains_accepted_intent(monkeypatch, tmp_path):
    from gym.endpoints import Manager
    from unittest.mock import Mock

    monkeypatch.setattr(
        Manager, "bind_cell", Mock(side_effect=PermissionError("lease unavailable"))
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            cmd, 0, '{"launchRequestId":"accepted"}', ""
        ),
    )
    source = WorkerPoolTrajectorySource(hq_bin="hq", hq_root=_hq_root(tmp_path))
    with pytest.raises(dispatch_mod.DispatchAcceptanceUnknown) as raised:
        source._dispatch(_task())
    assert raised.value.sandbox.is_dir()
    assert raised.value.launch_key.startswith("gym-")


@pytest.mark.parametrize("timeout", [False, True])
@pytest.mark.parametrize("model", ["codex", "litellm-local-openai/evalbench-gym-test"])
@pytest.mark.parametrize("failure", [PermissionError("denied"), ValueError("bad lease")])
def test_endpoint_cost_failure_preserves_trajectory(monkeypatch, tmp_path, timeout, model, failure):
    from gym.endpoints import Manager
    from unittest.mock import Mock

    read = Mock(side_effect=failure)
    monkeypatch.setattr(Manager, "cell_cost", read)
    source = WorkerPoolTrajectorySource(hq_root=tmp_path, model=model)
    source._reader = _metered_reader(
        lambda lrq: {"metrics": {"attempts": 1, "metrics_complete": True}}
    )
    source._dispatch = lambda task: ("lrq-1", tmp_path)
    source._poll_to_terminal = lambda reader, lrq, sandbox=None: (
        None if timeout else {"status": "completed", "run_id": "run-1"}
    )
    trajectory = source.run(_task())
    assert trajectory.terminal_status == ("timeout" if timeout else "completed")
    assert trajectory.metrics["attempts"] == 1
    if model == "codex":
        read.assert_not_called()
    else:
        read.assert_called_once()
        economics = source.cell_economics(trajectory.metrics)
        assert economics["cost_basis"] == "gpu_hours"
        assert economics["cost_status"] == "unknown"
        assert economics["cost_usd"] is None
        assert economics["gpu_hours"] is None


@pytest.mark.parametrize("timeout", [False, True])
def test_endpoint_cost_uses_task_pinned_model(monkeypatch, tmp_path, timeout):
    from gym.endpoints import Manager
    from unittest.mock import Mock

    route = "provider/evalbench-gym-pinned"
    read = Mock(
        return_value={
            "cost_basis": "gpu_hours",
            "cost_usd": 2,
            "cost_status": "estimated",
            "endpoint_lease": "evalbench-gym-pinned",
            "gpu_hours": 0.25,
        }
    )
    monkeypatch.setattr(Manager, "cell_cost", read)
    source = WorkerPoolTrajectorySource(hq_root=tmp_path, model="codex")
    source._reader = _metered_reader(lambda lrq: None)
    source._dispatch = lambda task: ("lrq-1", tmp_path)
    source._poll_to_terminal = lambda reader, lrq, sandbox=None: (
        None if timeout else {"status": "completed", "run_id": "run-1"}
    )
    trajectory = source.run(_task(dispatch={"model": route}))
    read.assert_called_once_with(route, "lrq-1")
    assert trajectory.metrics["cost_usd"] == 2
