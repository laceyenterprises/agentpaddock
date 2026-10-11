"""HRF-06: the HRF activation file validates and its probes grade recorded samples."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytest.importorskip(
    "cwp_dispatch.activation_contract", reason="host activation contract is not shipped"
)

from cwp_dispatch.activation_contract import (
    evaluate_output,
    parse_activation_yaml_document,
    validate_pack,
    validate_schema_runtime_contract,
)
from gym.suite import discover_tasks

MODULE = Path(__file__).resolve().parents[1]
REPO = MODULE
ACTIVATION = REPO / "projects/gym-hard-review-fix/activation"
SPEC_REF = "gym-hard-review-fix@b3689f1ea261"
DOCUMENT = yaml.safe_load((ACTIVATION / "hrf.yaml").read_text())
ASSERTIONS = parse_activation_yaml_document(
    DOCUMENT, expected_spec_ref=SPEC_REF
).assertions
SUITES = ("hard-review", "hard-fix", "review", "fix")
# HRX-02 adds three hard-review -v2 tasks and HRX-03 adds five hard-fix -v2
# tasks beside the six HRF originals of each suite.
DISCOVERED = {"hard-review": 9, "hard-fix": 11, "review": 6, "fix": 6}


def probe_program(index):
    return ASSERTIONS[index].command.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


def execute_probe(index, monkeypatch):
    # Probe bootstrap edits belong to the probe process, not later pytest cases.
    original_path = sys.path[:]
    with monkeypatch.context() as context:
        context.setattr(sys, "path", original_path[:])
        exec(compile(probe_program(index), "hrf.yaml", "exec"), {})
    assert sys.path == original_path


def sample(name):
    return (ACTIVATION / f"samples/{name}.json").read_text()


@pytest.fixture
def probe_roots(monkeypatch, tmp_path):
    from agent_os_config import roots

    monkeypatch.setenv("AGENT_OS_ROOT", str(REPO))
    hq = tmp_path / "shared-hq"
    monkeypatch.setenv("AGENT_OS_ROOTS_HQ", str(hq))
    roots.reset_cache()
    assert roots.deploy_root() == str(REPO)
    return hq


@pytest.fixture
def docker_cli(monkeypatch):
    """`docker info` and `image inspect` succeed; the runner itself is the backend's."""
    real_run = subprocess.run

    def docker(command, *args, **kwargs):
        if command[0] == "docker":
            return subprocess.CompletedProcess(command, 0, "", "")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", docker)


# --- schema ----------------------------------------------------------------------


def test_activation_schema_and_pinned_interpreter():
    assert [assertion.id for assertion in ASSERTIONS] == [
        "hrf-task-discovery",
        "hrf-regression-test-containment",
    ]
    for index, assertion in enumerate(ASSERTIONS):
        assert assertion.kind == "probe"
        assert assertion.completed_spec_ref == SPEC_REF
        assert validate_schema_runtime_contract(assertion).ok
        compile(probe_program(index), "hrf.yaml", "exec")


def test_pack_validates_beside_plan():
    # The PMA reads every activation/*.yaml beside plan.json against its specRef.
    ids = [
        assertion.id
        for assertion in validate_pack(REPO, "projects/gym-hard-review-fix/plan.json")
    ]
    assert ids == ["hrf-task-discovery", "hrf-regression-test-containment"]


# --- discovery probe -------------------------------------------------------------


def test_discovery_probe_executes_real_root_resolver(probe_roots, tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", probe_program(0)],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(REPO / "modules/worker-pool/lib/python")},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == json.loads(sample("discovery"))
    assert evaluate_output(ASSERTIONS[0], result.stdout).passed


@pytest.mark.parametrize("index", [0, 1])
def test_probe_bootstrap_without_config_on_pythonpath(index, probe_roots):
    # PMA exports the worker-pool package; the helper must locate config itself.
    # Disable site hooks so an editable config install cannot mask a broken bootstrap.
    # Stop before discovery/containment to verify both bootstraps without Docker.
    bootstrap = (
        probe_program(index).split("\nfrom gym.suite", 1)[0].split("\nimport re", 1)[0]
    )
    result = subprocess.run(
        [sys.executable, "-S", "-c", bootstrap + "\nprint(module)"],
        cwd="/",
        env={**os.environ, "PYTHONPATH": str(REPO / "modules/worker-pool/lib/python")},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(MODULE)


@pytest.mark.parametrize("suite", SUITES)
def test_actual_discovery_matches_recorded_sample(suite):
    assert (
        len(discover_tasks(MODULE / "tasks", suite=suite))
        == json.loads(sample("discovery"))[suite]
        == DISCOVERED[suite]
    )


@pytest.mark.parametrize("suite", SUITES)
@pytest.mark.parametrize("delta", [-1, 1])
def test_discovery_count_drift_fails(suite, delta):
    drifted = json.loads(sample("discovery"))
    drifted[suite] += delta
    assert not evaluate_output(ASSERTIONS[0], json.dumps(drifted)).passed


# --- regression-test containment probe ---------------------------------------------


def test_recorded_expectations_hold():
    assert evaluate_output(ASSERTIONS[0], sample("discovery")).passed
    assert evaluate_output(ASSERTIONS[1], sample("prospective-activation")).passed


@pytest.mark.parametrize(
    "field,value",
    [
        ("engine_reachable", False),
        ("image_present", False),
        ("trivial", "failed"),
        ("trivial", None),
        ("socket", "not_contained"),
        ("socket", None),
        ("error", "engine unavailable"),
        ("fix_activation", False),
        ("gate_reason", "fix activation blocked: integrity acceptance suspended"),
        ("sniffing_flagged", False),
    ],
)
def test_incomplete_activation_fails(field, value):
    report = json.loads(sample("prospective-activation"))
    report[field] = value
    assert not evaluate_output(ASSERTIONS[1], json.dumps(report)).passed


def test_probe_runs_the_hrf03_runner_and_grades_four_outcomes(
    monkeypatch, capsys, probe_roots, docker_cli, protocol_backend
):
    """The real runner on the protocol backend: the trivial test grades all four."""
    from gym import hidden_tests

    graded = []
    real = hidden_tests.run_regression_test

    def runner(task, captured, *args, **kwargs):
        assert task.suite == "hard-fix" and kwargs["hidden_passed"] is True
        assert kwargs["work_root"].parent == hidden_tests.shared_work_root()
        result = real(task, captured, *args, **kwargs)
        graded.append(result)
        return result

    monkeypatch.setattr(hidden_tests, "run_regression_test", runner)
    execute_probe(1, monkeypatch)
    report = json.loads(capsys.readouterr().out)
    assert len(graded) == 2
    assert (
        report["trivial_outcomes"]
        == json.loads(sample("prospective-activation"))["trivial_outcomes"]
    )
    assert report["trivial"] == "passed"
    assert report["sniffing_flagged"] is True
    assert report["fix_activation"] is True
    assert report["error"] is None
    # The protocol backend proves no network containment, so the socket verdict
    # is left to the real engine (below); only its shape is checked here.
    assert set(report["socket_outcomes"]) == {
        "original",
        "insufficient",
        "reference",
        "submission",
    }
    assert not list(hidden_tests.shared_work_root().iterdir())


def _socket_result(outcome, marker):
    runs = [
        {"variant": variant, "outcome": outcome, "timed_out": False}
        for variant in ("original", "insufficient", "reference", "submission")
    ]
    tail = "\n".join(f"[{run['variant']}]\n{marker}\n." for run in runs)
    return {"status": "completed", "runs": runs, "output_tail": tail}


@pytest.mark.parametrize(
    "outcome,marker,contained",
    [
        ("passed", "GYM_NETWORK_DENIED errno=101", True),
        ("passed", "GYM_NETWORK_DENIED errno=113", True),
        # macOS errnos mean the test ran outside the Linux container.
        ("passed", "GYM_NETWORK_DENIED errno=51", False),
        ("passed", "", False),
        ("failed", "GYM_NETWORK_DENIED errno=101", False),
        ("error", "", False),
    ],
)
def test_socket_verdict_requires_network_denial_on_every_variant(
    monkeypatch, capsys, probe_roots, docker_cli, outcome, marker, contained
):
    from gym import hidden_tests

    reference = json.loads(sample("prospective-activation"))["trivial_outcomes"]

    def runner(task, captured, **kwargs):
        if "socket" in (captured / "test_regression.py").read_text():
            return _socket_result(outcome, marker)
        runs = [
            {"variant": variant, "outcome": result, "counts": None, "timed_out": False}
            for variant, result in reference.items()
        ]
        return {"status": "completed", "runs": runs, "output_tail": ""}

    monkeypatch.setattr(hidden_tests, "run_regression_test", runner)
    execute_probe(1, monkeypatch)
    output = capsys.readouterr().out
    assert (json.loads(output)["socket"] == "contained") is contained
    assert evaluate_output(ASSERTIONS[1], output).passed is contained


def test_engine_down_is_transient(monkeypatch, capsys, probe_roots):
    real_run = subprocess.run

    def docker(command, *args, **kwargs):
        if command[0] == "docker":
            return subprocess.CompletedProcess(
                command, 1, "", "Cannot connect to the Docker daemon"
            )
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", docker)
    with pytest.raises(SystemExit) as exit_info:
        execute_probe(1, monkeypatch)
    assert exit_info.value.code == 1
    output = capsys.readouterr()
    assert "service unavailable" in output.err
    assert not evaluate_output(ASSERTIONS[1], output.out).passed


@pytest.mark.parametrize("timed_out_command", ["info", "image"])
def test_docker_probe_timeout_is_transient(
    monkeypatch, capsys, probe_roots, timed_out_command
):
    calls = []

    def docker(command, *args, **kwargs):
        assert command[0] == "docker"
        calls.append(command[1])
        if command[1] == timed_out_command:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", docker)
    with pytest.raises(SystemExit) as exit_info:
        execute_probe(1, monkeypatch)
    assert exit_info.value.code == 1
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert calls == (["info"] if timed_out_command == "info" else ["info", "image"])
    assert report["engine_reachable"] is (timed_out_command == "image")
    assert report["image_present"] is False
    assert "timed out" in report["error"]
    assert "service unavailable" in output.err
    assert not evaluate_output(ASSERTIONS[1], output.out).passed


def test_missing_image_is_subject_failure(monkeypatch, capsys, probe_roots):
    def docker(command, *args, **kwargs):
        assert command[0] == "docker"
        if command[1] == "info":
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 1, "", "No such image")

    monkeypatch.setattr(subprocess, "run", docker)
    execute_probe(1, monkeypatch)
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["engine_reachable"] is True
    assert report["image_present"] is False
    assert report["error"] == "pinned image unavailable: No such image"
    assert output.err == ""
    assert not evaluate_output(ASSERTIONS[1], output.out).passed


def test_real_engine_probe_passes(monkeypatch, capsys, docker_backend):
    """On the host's real engine the probe reproduces the recorded sample."""
    from agent_os_config import roots

    monkeypatch.setattr(roots, "deploy_root", lambda: str(REPO))
    execute_probe(1, monkeypatch)
    output = capsys.readouterr().out
    report = json.loads(output)
    expected = json.loads(sample("prospective-activation"))
    assert report["trivial_outcomes"] == expected["trivial_outcomes"], report
    assert report["socket_outcomes"] == expected["socket_outcomes"], report
    assert evaluate_output(ASSERTIONS[1], output).passed, report


# --- battery ---------------------------------------------------------------------


def test_battery_axes():
    from gym.matrix import load_battery

    battery, _, identities = load_battery(
        MODULE / "batteries/hrf-pilot.toml",
        discover_tasks(MODULE / "tasks"),
    )
    assert len(identities) * battery["repetitions"] == battery["budget"]["max_cells"]
    assert battery["suites"] == ["hard-review", "hard-fix"]
    assert battery["levels"] == ["high"]
    assert {family["worker_class"] for family in battery["families"].values()} == {
        "codex",
        "claude-code",
    }


def test_cleanup_permission_failure_preserves_runner_error(
    monkeypatch, capsys, probe_roots, docker_cli
):
    import tempfile
    from gym import hidden_tests
    from gym.grader import GraderError

    cleanup_modes = []
    real_cleanup = tempfile.TemporaryDirectory._rmtree

    def permission_cleanup(cls, name, ignore_errors=False, repeated=False):
        cleanup_modes.append(ignore_errors)
        if not ignore_errors:
            raise PermissionError("root-owned container output")
        real_cleanup(name, ignore_errors=ignore_errors, repeated=repeated)

    def failed_runner(*args, **kwargs):
        raise GraderError("original runner failure")

    monkeypatch.setattr(
        tempfile.TemporaryDirectory, "_rmtree", classmethod(permission_cleanup)
    )
    monkeypatch.setattr(hidden_tests, "run_regression_test", failed_runner)
    execute_probe(1, monkeypatch)
    report = json.loads(capsys.readouterr().out)
    assert cleanup_modes == [True]
    assert report["error"] == "original runner failure"
    assert not evaluate_output(ASSERTIONS[1], json.dumps(report)).passed
