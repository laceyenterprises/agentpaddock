"""PDK-01: the standalone runner yields gradeable Trajectories without a host.

A fake ``docker`` stands in for the engine and the harness CLI together: it
reads the bind-mounted workspace, prints the harness's own tool-call stream,
and writes ``bench-result.json``. The real grader then grades the Trajectory.
No container, model or network is involved.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from dataclasses import asdict
from pathlib import Path

import pytest

from gym import grader, outcomes, schema
from gym.standalone import HARNESSES, StandaloneConfigError, StandaloneTrajectorySource

SECRET = "sk-test-paddock-0123456789abcdef"

_FAKE_DOCKER = r"""
import json, os, sys, time
from pathlib import Path

argv = sys.argv[1:]
log = Path(os.environ["DOCKER_FAKE_LOG"])
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"argv": argv, "env": dict(os.environ)}) + "\n")
if argv[0] in {"kill", "inspect"}:
    name = argv[-1]
    pidfile = log.parent / name
    if argv[0] == "inspect":
        if pidfile.exists():
            print("true")
            sys.exit(0)
        print("Error: No such object: " + name, file=sys.stderr)
        sys.exit(1)
    if pidfile.exists():
        try:
            os.kill(int(pidfile.read_text()), 9)
        except ProcessLookupError:
            pass
        pidfile.unlink()
    sys.exit(0)
assert argv[0] == "run"
options = [a for a in argv[1:] if a.startswith("--")]
rest = [a for a in argv[1:] if not a.startswith("--")]
image, command = rest[0], rest[1:]
volume = next(a for a in options if a.startswith("--volume=")).split("=", 1)[1]
workspace = Path(volume.rsplit(":", 1)[0])
mode = os.environ.get("DOCKER_FAKE_MODE", "ok")
name = next(a for a in options if a.startswith("--name=")).split("=", 1)[1]
(log.parent / name).write_text(str(os.getpid()))
if mode in {"flood-stdout", "flood-stderr", "oversized-event"}:
    output = sys.stderr.buffer if mode == "flood-stderr" else sys.stdout.buffer
    while True:
        output.write(b"x" * 4096 + (b"\\n" if mode != "oversized-event" else b""))
        output.flush()
if mode == "sleep":
    time.sleep(30)
answer = (workspace / "input.txt").read_text(encoding="utf-8").strip()
key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
harness = command[0]
read_args = {"file_path": "input.txt", "note": "key=" + key}
if mode == "long-arguments":
    read_args["nested"] = [{"body": "x" * 1990 + key + "y" * 100}]
if harness == "claude":
    events = [{"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": read_args}]}}]
elif harness == "codex":
    events = [{"type": "item.completed", "item": {
        "id": "item_1", "type": "command_execution", "command": "cat input.txt # " + key}}]
elif harness == "pi":
    message = {"role": "assistant", "content": [
        {"type": "toolCall", "id": "call_1", "name": "read", "arguments": read_args}]}
    events = [{"type": "message_update", "message": message},
              {"type": "message_end", "message": message}]
elif harness == "opencode":
    events = [{"type": "tool_use", "part": {
        "type": "tool", "tool": "read", "callID": "call_1",
        "state": {"status": "completed", "input": read_args}}}]
else:
    sys.exit(f"unknown harness {harness}")
for event in events:
    print(json.dumps(event))
if mode in {"fail-tail-stderr", "fail-tail-stdout"}:
    output = sys.stderr if mode == "fail-tail-stderr" else sys.stdout
    print(key + "x" * (2000 - len(key) // 2 - 1), file=output)
    sys.exit(3)
if mode == "fail":
    print("provider rejected key " + key, file=sys.stderr)
    sys.exit(3)
(workspace / "bench-result.json").write_text(json.dumps({"answer": answer}), encoding="utf-8")
"""

_VERIFIER = """
import json

import gym_verifier as gv

result = gv.result()
read = any("input.txt" in json.dumps(call.get("arguments")) for call in gv.toolcalls())
gv.check("read_input", read, "never read input.txt")
gv.check("answer", result.get("answer") == "ok", "answer was not ok")
gv.passed()
"""


def _task(root: Path) -> schema.Task:
    task_dir = root / "standalone-probe"
    (task_dir / "stages").mkdir(parents=True)
    (task_dir / "task.toml").write_text(
        textwrap.dedent(
            """
            id = "standalone-probe"
            suite = "rca"
            persona = "claude-code"

            [dispatch]
            prompt = "prompt.md"

            [[stages]]
            run = "stages/stage.sh"

            [result_schema]
            answer = { type = "enum", enum = ["ok", "bad"], required = true }

            [verifier]
            entrypoint = "verifier.py"
            description = "read input.txt and answered ok"
            """
        ),
        encoding="utf-8",
    )
    (task_dir / "prompt.md").write_text("Read input.txt and submit its word.\n", encoding="utf-8")
    (task_dir / "stages" / "stage.sh").write_text(
        'printf "ok\\n" > "$1/input.txt"\n', encoding="utf-8"
    )
    (task_dir / "verifier.py").write_text(_VERIFIER, encoding="utf-8")
    return schema.load_task(task_dir)


@pytest.fixture
def fake_docker(tmp_path):
    script = tmp_path / "docker"
    script.write_text(f"#!{sys.executable}\n{_FAKE_DOCKER}", encoding="utf-8")
    script.chmod(0o755)
    log = tmp_path / "docker.log"
    log.touch()
    return script, log


def _source(fake_docker, tmp_path, harness, *, mode="ok", **kwargs):
    script, log = fake_docker
    env = {
        "PATH": os.environ.get("PATH", ""),
        "DOCKER_FAKE_LOG": str(log),
        "DOCKER_FAKE_MODE": mode,
        "ANTHROPIC_API_KEY": SECRET,
        "OPENAI_API_KEY": SECRET,
        "UNRELATED_SECRET": "must-not-reach-docker",
    }
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return StandaloneTrajectorySource(
        harness=harness,
        image="paddock/test:local",
        model="test-model",
        docker_bin=str(script),
        work_dir=work,
        env=env,
        **kwargs,
    )


def _invocations(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("harness", sorted(HARNESSES))
def test_fake_harness_cli_yields_a_gradeable_trajectory(fake_docker, tmp_path, harness):
    source = _source(fake_docker, tmp_path, harness)
    task = _task(tmp_path)

    trajectory = source.run(task)
    cell = grader.grade(task, trajectory)

    assert trajectory.terminal_status == "succeeded", trajectory.error
    assert trajectory.submitted_result == {"answer": "ok"}
    assert len(trajectory.harness_calls) == 1
    assert set(trajectory.harness_calls[0]) == {"name", "arguments", "call_id"}
    assert cell.outcome == outcomes.PASSED, cell.reason

    # Credentials ride as names in argv and as values only in docker's env.
    invocation = next(i for i in _invocations(fake_docker[1]) if i["argv"][0] == "run")
    assert SECRET not in json.dumps(invocation["argv"])
    assert (
        "--env=ANTHROPIC_API_KEY" in invocation["argv"]
        or "--env=OPENAI_API_KEY" in (invocation["argv"])
    )
    assert "UNRELATED_SECRET" not in invocation["env"]
    assert "--cap-drop=ALL" in invocation["argv"]
    assert "--pull=never" in invocation["argv"]
    # ...and never in what a result could carry.
    assert SECRET not in json.dumps(asdict(trajectory), default=str)
    assert SECRET not in json.dumps(cell.to_dict(), default=str)
    source.close()
    assert not trajectory.sandbox_root.exists()


def test_failed_harness_redacts_credentials_from_the_error(fake_docker, tmp_path):
    source = _source(fake_docker, tmp_path, "claude-code", mode="fail")
    task = _task(tmp_path)

    trajectory = source.run(task)
    cell = grader.grade(task, trajectory)

    assert trajectory.terminal_status == "failed"
    assert "exited 3" in trajectory.error and "<redacted>" in trajectory.error
    assert SECRET not in json.dumps(asdict(trajectory), default=str)
    assert SECRET not in json.dumps(cell.to_dict(), default=str)
    assert cell.outcome != outcomes.PASSED


@pytest.mark.parametrize("mode", ["fail-tail-stderr", "fail-tail-stdout"])
def test_error_tail_redacts_secrets_crossing_the_truncation_boundary(fake_docker, tmp_path, mode):
    source = _source(fake_docker, tmp_path, "pi", mode=mode)
    task = _task(tmp_path)
    trajectory = source.run(task)
    cell = grader.grade(task, trajectory)
    assert trajectory.terminal_status == "failed"
    assert len(trajectory.error) <= 2000
    for result in (asdict(trajectory), cell.to_dict()):
        serialized = json.dumps(result, default=str)
        assert SECRET not in serialized
        assert SECRET[len(SECRET) // 2 :] not in serialized
    source.close()


@pytest.mark.parametrize("harness", ["pi", "opencode"])
def test_nested_arguments_redact_secrets_before_capping(fake_docker, tmp_path, harness):
    source = _source(fake_docker, tmp_path, harness, mode="long-arguments")
    task = _task(tmp_path)
    trajectory = source.run(task)
    cell = grader.grade(task, trajectory)
    body = trajectory.harness_calls[0]["arguments"]["nested"][0]["body"]
    assert body.startswith("x" * 1990 + "<redacted>")
    assert "[truncated " in body
    assert cell.outcome == outcomes.PASSED
    for result in (asdict(trajectory), cell.to_dict()):
        serialized = json.dumps(result, default=str)
        assert SECRET not in serialized
        assert SECRET[:10] not in serialized
    source.close()


def test_timeout_kills_the_container(fake_docker, tmp_path):
    source = _source(fake_docker, tmp_path, "codex", mode="sleep", timeout_seconds=0.5)

    trajectory = source.run(_task(tmp_path))

    assert trajectory.terminal_status == "timeout"
    invocations = _invocations(fake_docker[1])
    run = invocations[0]
    kill = next(i for i in invocations if i["argv"][0] == "kill")
    name = next(a for a in run["argv"] if a.startswith("--name=")).split("=", 1)[1]
    assert kill["argv"] == ["kill", name]


def test_missing_docker_client_is_a_failed_run_not_a_crash(tmp_path):
    source = StandaloneTrajectorySource(
        harness="pi", image="img", docker_bin=str(tmp_path / "no-docker"), env={}
    )
    trajectory = source.run(_task(tmp_path))
    assert trajectory.terminal_status == "failed"
    assert "docker client not found" in trajectory.error
    source.close()


def test_from_config_reads_the_user_config_and_explicit_flags_win():
    config = {
        "standalone": {"harness": "pi", "image": "base", "codex": {"image": "codex-img"}},
        "credentials": {"OPENAI_API_KEY": SECRET},
    }
    source = StandaloneTrajectorySource.from_config(config, env={}, harness="codex", model="m")
    assert (source.harness, source.image, source.model) == ("codex", "codex-img", "m")
    assert source._credentials() == {"OPENAI_API_KEY": SECRET}
    assert SECRET not in repr(source)
    # The environment wins over the config for the same variable.
    env_source = StandaloneTrajectorySource.from_config(
        config, env={"OPENAI_API_KEY": "from-env"}, harness="codex"
    )
    assert env_source._credentials()["OPENAI_API_KEY"] == "from-env"


@pytest.mark.parametrize(
    ("config", "options", "message"),
    [
        ({}, {}, "needs a harness"),
        ({"standalone": {"harness": "gemini"}}, {"image": "i"}, "unknown harness"),
        ({"standalone": {"harness": "pi"}}, {}, "needs a Docker image"),
        ({"standalone": {"harness": "pi", "image": "i"}}, {"priority": "low"}, "--priority"),
    ],
)
def test_from_config_refuses_incomplete_or_fleet_only_settings(config, options, message):
    with pytest.raises(StandaloneConfigError, match=message):
        StandaloneTrajectorySource.from_config(config, env={}, **options)


@pytest.mark.parametrize("from_config", [False, True])
def test_opencode_reasoning_override_is_rejected(from_config):
    with pytest.raises(StandaloneConfigError, match="opencode.*reasoning"):
        if from_config:
            StandaloneTrajectorySource.from_config(
                {"standalone": {"harness": "opencode", "image": "img", "reasoning_level": "high"}}
            )
        else:
            StandaloneTrajectorySource(harness="opencode", image="img", reasoning_level="high")


def test_opencode_without_override_cannot_enter_high_effort_cohort(fake_docker, tmp_path):
    from gym import suite

    source = _source(fake_docker, tmp_path, "opencode")
    result = suite.run_suite([_task(tmp_path)], source, suite="rca", reasoning_level="high")
    assert result.graded[0].cell.resolved_reasoning_level is None
    assert result.graded[0].cell.outcome != outcomes.PASSED


@pytest.mark.parametrize(
    "options",
    [
        {"memory_mb": 0},
        {"memory_mb": -1},
        {"memory_mb": True},
        {"pids_limit": 0},
        {"pids_limit": "unlimited"},
        {"cpus": 0},
        {"cpus": float("inf")},
        {"cpus": float("nan")},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("nan")},
    ],
)
def test_resource_limits_and_deadline_must_be_finite_positive(options):
    with pytest.raises(StandaloneConfigError):
        StandaloneTrajectorySource(harness="pi", image="img", **options)


def test_resource_limits_are_configurable_and_swap_is_disabled(tmp_path):
    source = StandaloneTrajectorySource.from_config(
        {
            "standalone": {
                "harness": "pi",
                "image": "img",
                "memory_mb": 512,
                "cpus": 0.5,
                "pids_limit": 32,
            }
        }
    )
    argv = source._docker_argv("probe", tmp_path, [], ["pi"])
    assert {"--memory=512m", "--memory-swap=512m", "--cpus=0.5", "--pids-limit=32"} <= set(argv)
    defaults = StandaloneTrajectorySource(harness="pi", image="img")._docker_argv(
        "probe", tmp_path, [], ["pi"]
    )
    assert {"--memory=2048m", "--memory-swap=2048m", "--cpus=2.0", "--pids-limit=256"} <= set(
        defaults
    )


@pytest.mark.parametrize("failure", ["nonzero", "timeout", "oserror", "inspect-error"])
def test_kill_retries_and_requires_engine_confirmation(monkeypatch, failure):
    from gym import standalone

    source = StandaloneTrajectorySource(harness="pi", image="img")
    calls = []

    def control(argv, **kwargs):
        calls.append(argv[1])
        if len(calls) == 1:
            if failure == "timeout":
                raise subprocess.TimeoutExpired(argv, 5)
            if failure == "oserror":
                raise OSError("daemon unavailable")
        if argv[1] == "kill":
            return subprocess.CompletedProcess(
                argv, 1 if len(calls) == 1 else 0, "", "daemon unavailable"
            )
        if failure == "inspect-error" and len(calls) == 2:
            return subprocess.CompletedProcess(argv, 1, "", "daemon unavailable")
        running = len(calls) == 2
        return subprocess.CompletedProcess(argv, 0, "true" if running else "false", "")

    monkeypatch.setattr(standalone.subprocess, "run", control)
    monkeypatch.setattr(standalone.time, "sleep", lambda _: None)
    assert source._kill("probe", {}) is True
    assert calls.count("kill") == 2


def test_unconfirmed_containment_retains_supervision_and_halts_admission(
    fake_docker, tmp_path, monkeypatch
):
    from gym import standalone, suite
    from dataclasses import replace

    source = _source(fake_docker, tmp_path, "pi", mode="sleep", timeout_seconds=0.1)
    task = _task(tmp_path)
    kill = source._kill
    attempts = []
    read = standalone.staging.read_submitted_result

    def fail_then_stop(name, env):
        attempts.append(name)
        if len(attempts) == 1:
            assert source._active
            with pytest.raises(RuntimeError, match="remain supervised"):
                source.close()
            return False
        assert source.admission_halted.is_set()
        return kill(name, env)

    def guarded_read(sandbox):
        assert not source._active
        return read(sandbox)

    monkeypatch.setattr(source, "_kill", fail_then_stop)
    monkeypatch.setattr(standalone.staging, "read_submitted_result", guarded_read)
    result = suite.run_suite([task, replace(task, id="not-admitted")], source, suite="rca")
    assert source.admission_halted.is_set()
    assert not source._active
    assert result.graded[0].cell.terminal_status == "timeout"
    assert "admission halted" in result.graded[1].cell.reason
    assert len([i for i in _invocations(fake_docker[1]) if i["argv"][0] == "run"]) == 1
    source.close()


def test_sigint_to_gym_process_cancels_both_concurrent_containers(fake_docker, tmp_path):
    _task(tmp_path)
    script, log = fake_docker
    driver = r"""
import os, signal, threading, time
from pathlib import Path
from dataclasses import replace
from gym import schema, suite
from gym.standalone import StandaloneTrajectorySource
root = Path(os.environ['PROBE_ROOT'])
log = root / 'docker.log'
source = StandaloneTrajectorySource(
    harness='pi', image='img', docker_bin=str(root / 'docker'), timeout_seconds=3,
    env={'DOCKER_FAKE_LOG': str(log), 'DOCKER_FAKE_MODE': 'sleep'},
)
task = schema.load_task(root / 'standalone-probe')
def interrupt():
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if len(list(root.glob('paddock-*'))) == 2:
            os.kill(os.getpid(), signal.SIGINT)
            return
        time.sleep(0.01)
thread = threading.Thread(target=interrupt)
thread.start()
try:
    suite.run_suite([task, replace(task, id='second'), replace(task, id='third')], source, suite='rca', concurrency=2)
    raise AssertionError('SIGINT was not delivered')
except KeyboardInterrupt:
    assert not list(root.glob('paddock-*')), 'containers were not stopped'
finally:
    thread.join()
    source.close()
"""
    env = dict(
        os.environ, PROBE_ROOT=str(tmp_path), PYTHONPATH=str(Path(schema.__file__).parents[1])
    )
    result = subprocess.run(
        [sys.executable, "-c", driver], env=env, capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    invocations = _invocations(log)
    runs = [i for i in invocations if i["argv"][0] == "run"]
    killed = {i["argv"][-1] for i in invocations if i["argv"][0] == "kill"}
    assert len(runs) == 2
    assert {
        next(a.split("=", 1)[1] for a in i["argv"] if a.startswith("--name=")) for i in runs
    } <= killed


@pytest.mark.parametrize(
    "diagnostic, stopped",
    [
        ("Error: No such object: probe", True),
        ("error: no such object: probe", True),
        ("Error response from daemon: No such container: probe", True),
        ("Error: No such object: somebody-else", False),
        ("daemon unavailable", False),
        ("error: no such object: probe\nconnection reset", True),
        ("WARNING: deprecated API\nError: No such object: probe\nTLS warning", True),
        ("Error: No such object: probe-extra", False),
        ("warning mentions Error: No such object: probe", False),
        ("WARNING: deprecated API\nError: No such object: somebody-else", False),
    ],
)
def test_only_confirmed_named_container_absence_counts_as_stopped(monkeypatch, diagnostic, stopped):
    from gym import standalone

    source = StandaloneTrajectorySource(harness="pi", image="img")
    monkeypatch.setattr(
        standalone.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, "", diagnostic),
    )
    monkeypatch.setattr(standalone.time, "sleep", lambda _: None)
    assert source._kill("probe", {}) is stopped


@pytest.mark.parametrize("mode", ["flood-stdout", "flood-stderr", "oversized-event"])
def test_output_budget_contains_flood_without_grading(fake_docker, tmp_path, monkeypatch, mode):
    from gym import standalone

    monkeypatch.setattr(standalone, "_MAX_OUTPUT_BYTES", 32768)
    monkeypatch.setattr(standalone, "_MAX_EVENT_BYTES", 16384)
    source = _source(fake_docker, tmp_path, "codex", mode=mode, timeout_seconds=5)
    trajectory = source.run(_task(tmp_path))
    assert trajectory.terminal_status == "output_limit_exceeded"
    assert trajectory.submitted_result is None
    assert trajectory.harness_calls == []
    assert any(i["argv"][0] == "kill" for i in _invocations(fake_docker[1]))
    assert not list(tmp_path.glob("paddock-*"))
    assert not source._active
    source.close()


def test_workspace_diagnostics_redacted_after_grading(fake_docker, tmp_path):
    from gym import suite

    source = _source(fake_docker, tmp_path, "codex")
    task = schema.load_task(suite.default_tasks_dir() / "docs-gym-run-flags")
    # This is the exact shipped workspace-capture/verifier boundary.
    trajectory = source.run(_task(tmp_path))
    (trajectory.sandbox_root / "guide.md").write_text("## Run\n--" + SECRET)
    trajectory.submitted_result = {"done": True}
    cell = grader.grade(task, trajectory)
    assert cell.outcome == outcomes.FAILED
    assert "<redacted>" in json.dumps(cell.subchecks)
    assert SECRET in (trajectory.sandbox_root / "guide.md").read_text()
    cell.held_verdict = {"reason": SECRET, "subchecks": [{"reason": SECRET}]}
    result = suite.SuiteResult("docs", [suite.GradedCell(cell, None)])
    assert SECRET not in json.dumps(cell.to_dict())
    assert SECRET not in json.dumps(result.summary_dict())
    source.close()


def test_grading_exception_redacts_workspace_credential(fake_docker, tmp_path, monkeypatch):
    source = _source(fake_docker, tmp_path, "codex")
    task = _task(tmp_path)
    trajectory = source.run(task)

    def broken(*args, **kwargs):
        raise grader.GraderError("workspace diagnostic: " + SECRET)

    monkeypatch.setattr(grader, "_grade", broken)
    with pytest.raises(grader.GraderError) as error:
        grader.grade(task, trajectory)
    assert SECRET not in str(error.value)
    assert "<redacted>" in str(error.value)
    source.close()


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
def test_config_execution_provenance_persisted(fake_docker, tmp_path, harness):
    from gym import cli, suite

    source = _source(fake_docker, tmp_path, harness)
    source.model = "config-selected-model"
    result = suite.run_suite([_task(tmp_path)], source, suite="rca")
    path = cli._store_model_result(result, None, None, tmp_path / "results")
    document = json.loads(path.read_text())
    assert document["model"] == "config-selected-model"
    provenance = document["summary"]["cells"][0]["execution_provenance"]
    assert provenance == {
        "runner": "standalone",
        "harness": harness,
        "image": "paddock/test:local",
        "model": "config-selected-model",
    }
    source.close()


def test_output_collector_retention_is_bounded(monkeypatch):
    from gym import standalone
    from io import BytesIO
    from types import SimpleNamespace

    monkeypatch.setattr(standalone, "_MAX_OUTPUT_BYTES", 131072)
    monkeypatch.setattr(standalone, "_MAX_EVENT_BYTES", 65536)
    proc = SimpleNamespace(stdout=BytesIO(b"x\n" * 1000000), stderr=BytesIO(b"y\n" * 1000000))
    collector = standalone._OutputCollector(proc)
    collector.finish()
    assert collector.exceeded.is_set()
    assert sum(len(b) for b in collector._buffers) <= 131072
