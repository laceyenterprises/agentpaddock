"""Recorded activation expectations never waive the production fix gate."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

pytest.importorskip(
    "cwp_dispatch.activation_contract", reason="host activation contract is not shipped"
)

from cwp_dispatch.activation_contract import (
    _execute_assertion,
    evaluate_output,
    parse_activation_yaml_document,
    validate_schema_runtime_contract,
)
from gym.grader import GraderError
from gym.hidden_tests import require_fix_activation
from gym.suite import discover_tasks

MODULE = Path(__file__).resolve().parents[1]
ACTIVATION = MODULE / "projects/gym-review-fix-docs/activation"
DOCUMENT = yaml.safe_load((ACTIVATION / "rfd.yaml").read_text())
ASSERTIONS = parse_activation_yaml_document(
    DOCUMENT,
    expected_spec_ref="gym-review-fix-docs@66b1b284175d",
).assertions


def probe_program(index):
    return ASSERTIONS[index].command.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


def execute_probe(index, monkeypatch):
    # Probe bootstrap edits belong to the probe process, not later pytest cases.
    original_path = sys.path[:]
    with monkeypatch.context() as context:
        context.setattr(sys, "path", original_path[:])
        exec(compile(probe_program(index), "rfd.yaml", "exec"), {})
    assert sys.path == original_path


@pytest.fixture
def probe_roots(monkeypatch, tmp_path):
    from agent_os_config import roots

    monkeypatch.setenv("AGENT_OS_ROOT", str(MODULE))
    hq = tmp_path / "shared-hq"
    monkeypatch.setenv("AGENT_OS_ROOTS_HQ", str(hq))
    roots.reset_cache()
    assert roots.deploy_root() == str(MODULE)
    assert roots.hq_root() == str(hq)
    return hq


def test_discovery_probe_executes_real_root_resolver(probe_roots):
    result = subprocess.run(
        [sys.executable, "-c", probe_program(0)],
        cwd=MODULE,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"review": 6, "fix": 6, "docs": 6}
    assert evaluate_output(ASSERTIONS[0], result.stdout).passed


def test_activation_schema_and_pinned_interpreter():
    assert len(ASSERTIONS) == 2
    for assertion in ASSERTIONS:
        assert validate_schema_runtime_contract(assertion).ok
        program = assertion.command.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        compile(program, "rfd.yaml", "exec")


@pytest.mark.parametrize(
    "index,sample", [(0, "discovery"), (1, "prospective-activation")]
)
def test_recorded_expectation(index, sample):
    assert evaluate_output(
        ASSERTIONS[index], (ACTIVATION / f"samples/{sample}.json").read_text()
    ).passed


def test_activation_matches_operator_lift():
    blocked = (ACTIVATION / "samples/blocked-activation.json").read_text()
    assert not evaluate_output(ASSERTIONS[1], blocked).passed
    activated = (ACTIVATION / "samples/prospective-activation.json").read_text()
    assert evaluate_output(ASSERTIONS[1], activated).passed
    require_fix_activation()
    assert "hidden_tests.require_fix_activation()" in ASSERTIONS[1].command


@pytest.mark.parametrize(
    "field,value",
    [
        ("engine_reachable", False),
        ("image_present", False),
        ("passing", "failed"),
        ("socket", "not_contained"),
        ("error", "engine unavailable"),
        ("fix_activation", False),
        ("integrity_audit", False),
        ("gate_reason", "unrelated rejection"),
    ],
)
def test_incomplete_activation_fails(field, value):
    sample = json.loads(
        (ACTIVATION / "samples/prospective-activation.json").read_text()
    )
    sample[field] = value
    assert not evaluate_output(ASSERTIONS[1], json.dumps(sample)).passed


@pytest.mark.parametrize("suite", ["review", "fix", "docs"])
def test_actual_discovery_matches_recorded_sample(suite):
    sample = json.loads((ACTIVATION / "samples/discovery.json").read_text())
    assert len(discover_tasks(MODULE / "tasks", suite=suite)) == sample[suite] == 6


def test_battery_axes():
    from gym.matrix import load_battery

    battery, _, identities = load_battery(
        MODULE / "batteries/rfd-pilot.toml",
        discover_tasks(MODULE / "tasks"),
    )
    assert len(identities) * battery["repetitions"] == battery["budget"]["max_cells"]
    assert battery["suites"] == ["review", "fix", "docs"]
    assert battery["levels"] == ["high"]
    assert {family["worker_class"] for family in battery["families"].values()} == {
        "codex",
        "claude-code",
    }


@pytest.mark.parametrize(
    "socket_output,contained",
    [
        (
            "GYM_NETWORK_DENIED errno=101\nOSError: [Errno 101] Network is unreachable",
            True,
        ),
        ("GYM_NETWORK_DENIED errno=113\nOSError: [Errno 113] No route to host", True),
        ("PermissionError: [Errno 13] Permission denied", False),
        ("OSError: [Errno 1] Operation not permitted", False),
        ("OSError: [Errno 101] Network is unreachable", False),
        ("GYM_NETWORK_DENIED errno=13", False),
        ("AssertionError: unrelated failure", False),
    ],
)
def test_probe_reports_real_gate_and_only_network_denial(
    monkeypatch, capsys, probe_roots, socket_output, contained
):
    from agent_os_config import roots
    from gym import hidden_tests

    monkeypatch.setattr(roots, "deploy_root", lambda: str(MODULE))
    real_run = subprocess.run

    def inspect_image(command, *args, **kwargs):
        if command == ["docker", "info"] or command[:3] == [
            "docker",
            "image",
            "inspect",
        ]:
            return subprocess.CompletedProcess(command, 0, "", "")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", inspect_image)
    outcomes = iter(
        [
            {"status": "passed"},
            {"status": "failed", "failed": 1, "output_tail": socket_output},
        ]
    )

    def run(task, captured, *, work_root):
        cases = json.loads((task.task_dir / "hidden/cases.json").read_text())
        compile(cases[0]["call"], "case", "exec")
        assert task.hidden_tests_expected_tests == 1
        assert work_root.parent == probe_roots / "tmp"
        assert work_root.stat().st_mode & 0o777 == 0o700
        return next(outcomes)

    monkeypatch.setattr(hidden_tests, "run", run)
    execute_probe(1, monkeypatch)
    report = json.loads(capsys.readouterr().out)
    assert report["image_present"] is True
    assert report["passing"] == "passed"
    assert (report["socket"] == "contained") is contained
    assert report["fix_activation"] is True
    assert report["gate_reason"] is None
    assert evaluate_output(ASSERTIONS[1], json.dumps(report)).passed is contained


def test_probe_real_runner_mounts_shared_root_despite_default_tmp(
    monkeypatch, capsys, probe_roots, tmp_path
):
    from gym import hidden_tests

    unshared = tmp_path / "unshared-temp"
    unshared.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(unshared))
    monkeypatch.setattr(hidden_tests.shutil, "which", lambda _: "/docker")
    real_run = subprocess.run

    def docker(command, *args, **kwargs):
        if command[0] == "docker":
            return subprocess.CompletedProcess(command, 0, "", "")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", docker)
    mounts = []

    def execute(command, work, timeout, *, input_data=b""):
        # Exercise real run(), snapshotting and Docker command construction.
        assert work.is_relative_to(probe_roots / "tmp")
        assert work.parent.stat().st_mode & 0o777 == 0o700
        assert command[command.index("--mount") + 1] == (
            f"type=bind,src={work},dst=/work,readonly"
        )
        mounts.append(work)
        if not input_data:
            return 0, False, "gym-ready\n" + next(work.glob("gym-bind-*")).read_text()
        request = json.loads(input_data)
        assert not (work / "hidden").exists()
        socket_fixture = "socket.create_connection" in request["call"]
        record = {
            "nonce": request["nonce"],
            **({"raised": "OSError"} if socket_fixture else {"ok": 42}),
        }
        protocol = "GYM_RESULT " + json.dumps(record) + "\n"
        output = protocol + ("GYM_NETWORK_DENIED errno=101\n" if socket_fixture else "")
        return 0, False, hidden_tests._ExecutionOutput(output, protocol)

    monkeypatch.setattr(hidden_tests, "_execute", execute)
    execute_probe(1, monkeypatch)
    output = capsys.readouterr().out
    assert evaluate_output(ASSERTIONS[1], output).passed
    assert len(mounts) == 4
    assert all(not path.exists() for path in mounts)
    assert not list((probe_roots / "tmp").iterdir())
    assert not list(unshared.iterdir())


@pytest.mark.parametrize(
    "failure,transient,engine_reachable,image_present",
    [
        ("engine", True, False, False),
        ("info-timeout", True, False, False),
        ("inspect-timeout", False, True, False),
        ("missing-image", False, True, False),
        ("sandbox-engine", True, True, True),
        ("sandbox-timeout", False, True, True),
        ("fixture-timeout", False, True, True),
        ("mount", False, True, True),
    ],
)
def test_probe_infrastructure_failures_enable_bounded_pma_retry(
    monkeypatch,
    capsys,
    probe_roots,
    failure,
    transient,
    engine_reachable,
    image_present,
):
    from gym import hidden_tests

    calls = []
    real_run = subprocess.run

    def docker(command, *args, **kwargs):
        if command[0] != "docker":
            return real_run(command, *args, **kwargs)
        calls.append(command)
        if (failure == "info-timeout" and command[1] == "info") or (
            failure == "inspect-timeout" and command[1] == "image"
        ):
            raise subprocess.TimeoutExpired(command, 20)
        if failure == "engine" and command[1] == "info":
            return subprocess.CompletedProcess(
                command, 1, "", "Cannot connect to the Docker daemon"
            )
        if failure == "missing-image" and command[1] == "image":
            return subprocess.CompletedProcess(command, 1, "", "No such image")
        return subprocess.CompletedProcess(command, 0, "", "")

    def run(*args, **kwargs):
        if failure == "fixture-timeout":
            return {"timed_out": True}
        message = {
            "sandbox-engine": "Cannot connect to the Docker daemon",
            "sandbox-timeout": "timed out",
            "mount": "bind source path does not exist",
        }[failure]
        raise GraderError("hidden-test sandbox unusable: " + message)

    monkeypatch.setattr(subprocess, "run", docker)
    monkeypatch.setattr(hidden_tests, "run", run)
    if transient:
        with pytest.raises(SystemExit) as exit_info:
            execute_probe(1, monkeypatch)
        assert exit_info.value.code == 1
    else:
        execute_probe(1, monkeypatch)
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["engine_reachable"] is engine_reachable
    assert report["image_present"] is image_present
    assert report["error"]
    assert report["fix_activation"] is True
    assert report["gate_reason"] is None
    assert not evaluate_output(ASSERTIONS[1], output.out).passed
    if not engine_reachable:
        assert calls == [["docker", "info"]]
        assert "pinned image unavailable" not in report["error"]
    retries = []
    naps = []

    def runner(command, timeout):
        retries.append(command)
        return subprocess.CompletedProcess(
            command, int(transient), output.out, output.err
        )

    execution = _execute_assertion(
        ASSERTIONS[1], runner=runner, retry_transient=True, sleep=naps.append
    )
    assert len(retries) == (3 if transient else 1)
    assert naps == ([1.0, 2.0] if transient else [])
    assert execution.check_error_cause == ("probe_error" if transient else None)


def test_grader_real_runner_mounts_shared_root_despite_default_tmp(
    monkeypatch, probe_roots, tmp_path
):
    from gym import hidden_tests
    from gym.grader import _run_verifier_out_of_band
    from test_hidden_tests import fixture_task

    unshared = tmp_path / "unshared-temp"
    unshared.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(unshared))
    monkeypatch.setattr(hidden_tests.shutil, "which", lambda _: "/docker")
    task, capture = fixture_task(tmp_path)
    (task.task_dir / "verifier.py").write_text(
        "import gym_verifier as gv\nassert gv.hidden_tests()['status'] == 'passed'\ngv.passed()\n"
    )
    mounts = []

    def execute(command, work, timeout, *, input_data=b""):
        assert work.parent == hidden_tests.shared_work_root() == probe_roots / "tmp"
        assert (
            command[command.index("--mount") + 1]
            == f"type=bind,src={work},dst=/work,readonly"
        )
        mounts.append(work)
        if not input_data:
            return 0, False, "gym-ready\n" + next(work.glob("gym-bind-*")).read_text()
        request = json.loads(input_data)
        return (
            0,
            False,
            "GYM_RESULT " + json.dumps({"nonce": request["nonce"], "ok": 42}),
        )

    monkeypatch.setattr(hidden_tests, "_execute", execute)
    _run_verifier_out_of_band(
        task, {}, {}, python_exe=sys.executable, sandbox_root=capture
    )
    assert len(mounts) == 2
    assert all(not path.exists() for path in mounts)
    assert not list(hidden_tests.shared_work_root().iterdir())
    assert not list(unshared.iterdir())


def test_probe_uses_shared_work_root_helper(monkeypatch, capsys, probe_roots):
    from gym import hidden_tests

    calls = []
    original = hidden_tests.shared_work_root

    def resolve():
        calls.append(original())
        return calls[-1]

    monkeypatch.setattr(hidden_tests, "shared_work_root", resolve)
    real_run = subprocess.run

    def docker(command, *args, **kwargs):
        if command[0] == "docker":
            return subprocess.CompletedProcess(command, 0, "", "")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", docker)
    monkeypatch.setattr(
        hidden_tests,
        "run",
        lambda task, captured, **kwargs: {
            "status": "passed" if "passing" in task.id else "failed",
            "failed": 1,
            "output_tail": "GYM_NETWORK_DENIED errno=101",
        },
    )
    execute_probe(1, monkeypatch)
    assert calls == [probe_roots / "tmp"]
    assert evaluate_output(ASSERTIONS[1], capsys.readouterr().out).passed
