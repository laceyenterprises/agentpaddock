"""Hidden-test inputs reach each container by copy, never by a host bind mount.

On macOS the Docker engine runs in a VM. A bind-mounted host directory is served
to it over the shared host filesystem, and the VM keeps a host descriptor for
every file a container opened there long after the container and the file are
gone. The runner therefore copies each container's inputs into a fresh named
volume with `docker cp` and mounts that volume read-only at /work.

These tests drive the real runner through a stand-in `docker` executable that
emulates volumes, the never-started copy helper and `docker run` with plain
directories and a host interpreter. It proves the I/O contract and that the
graded result is unchanged; it proves no containment (see test_hidden_tests.py
for the real-engine tests).
"""

import json
import re
import sys
from dataclasses import replace

import pytest

from conftest import protocol_args
from gym import hidden_tests
from gym.grader import GraderError
from test_hidden_tests import fixture_task
from test_regression_tests import (
    ALTERNATIVE_RULES,
    GOOD_TEST,
    outcomes_of,
    probe,
    run_test,
)
from test_regression_tests import stage as stage_probe

FAKE_DOCKER = r"""#!{python}
import json, shutil, subprocess, sys
from pathlib import Path

STATE = Path({state!r})
args = sys.argv[1:]
with open(STATE / "log.jsonl", "a") as log:
    log.write(json.dumps(args) + "\n")
step = "-".join(args[:2]) if args[0] == "volume" else args[0]
if (STATE / ("fail-" + step)).exists():
    sys.stderr.write("injected failure: " + step + "\n")
    sys.exit(1)
volumes, containers = STATE / "volumes", STATE / "containers"


def mount_source(argv):
    for flag, value in zip(argv, argv[1:]):
        if flag == "--mount":
            fields = dict(part.partition("=")[::2] for part in value.split(","))
            assert fields["type"] == "volume", value
            return fields["src"]


def no_such(kind, name):
    sys.stderr.write("Error response from daemon: No such " + kind + ": " + name + "\n")
    sys.exit(1)


if args[:2] == ["volume", "create"]:
    (volumes / args[-1]).mkdir()
elif args[:2] == ["volume", "rm"]:
    if not (volumes / args[-1]).is_dir():
        no_such("volume", args[-1])
    shutil.rmtree(volumes / args[-1])
elif args[0] == "create":
    name = args[args.index("--name") + 1]
    assert (volumes / mount_source(args)).is_dir()
    (containers / name).write_text(mount_source(args))
elif args[0] == "cp":
    source, target = args[1], args[2]
    name, _, path = target.partition(":")
    assert source.endswith("/.") and path == "/work", args
    if not (containers / name).exists():
        no_such("container", name)
    volume = volumes / (containers / name).read_text()
    shutil.copytree(source[:-2], volume, dirs_exist_ok=True)
elif args[0] == "rm":
    if not (containers / args[-1]).exists():
        no_such("container", args[-1])
    (containers / args[-1]).unlink()
elif args[0] == "run":
    name = args[args.index("--name") + 1]
    volume = volumes / mount_source(args)
    image = args.index({image!r})
    (containers / name).write_text(volume.name)
    try:
        code = subprocess.run(
            [sys.executable, *args[image + 2 :]],
            cwd=volume,
            env={{"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}},
        ).returncode
    finally:
        (containers / name).unlink(missing_ok=True)  # --rm
    sys.exit(code)
else:
    sys.exit("unsupported: " + " ".join(args))
"""


class FakeDocker:
    def __init__(self, root):
        self.state = root / "docker-state"
        (self.state / "volumes").mkdir(parents=True)
        (self.state / "containers").mkdir()
        self.path = root / "docker"
        self.path.write_text(
            FAKE_DOCKER.format(
                python=sys.executable,
                state=str(self.state),
                image=hidden_tests.containment_image(),
            )
        )
        self.path.chmod(0o755)

    def calls(self):
        log = self.state / "log.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    def fail(self, step):
        (self.state / f"fail-{step}").touch()

    def leftovers(self):
        return sorted(
            path.name
            for kind in ("volumes", "containers")
            for path in (self.state / kind).iterdir()
        )


@pytest.fixture
def fake_docker(tmp_path, monkeypatch):
    docker = FakeDocker(tmp_path)
    which = hidden_tests.shutil.which
    monkeypatch.setattr(
        hidden_tests.shutil,
        "which",
        lambda name, *a, **k: (
            str(docker.path) if name == "docker" else which(name, *a, **k)
        ),
    )
    original = hidden_tests._command
    seen = []

    def command(work, args):
        # The real command builder; only container-only probe asserts are stripped.
        built = original(work, protocol_args(args, ""))
        seen.append((work, built))
        return built

    monkeypatch.setattr(hidden_tests, "_command", command)
    docker.commands = seen
    return docker


def _comparable(result, *, tail=True):
    """The graded artifacts, without timings and per-run nonces.

    unittest output carries its own run time and file paths, so regression
    results compare without their output tail.
    """
    timing = {"elapsed_seconds", "cleanup_seconds"}
    result = json.loads(json.dumps(result))
    for key in ("case_results", "audit_cases", "runs"):
        for record in result.get(key, []):
            for name in timing:
                record.pop(name, None)
    if tail:
        result["output_tail"] = re.sub(
            r"[0-9a-f]{64}", "<nonce>", result["output_tail"]
        )
    else:
        result.pop("output_tail")
    return result


def _assert_copy_only(docker):
    """Every container got its inputs by copy into its own volume, and nothing is left."""
    calls = docker.calls()
    runs = [call for call in calls if call[0] == "run"]
    assert runs and len(runs) == len(docker.commands)
    copies = [call for call in calls if call[0] == "cp"]
    assert len(copies) == len(runs)
    volumes = set()
    for (work, command), run_call, copy in zip(docker.commands, runs, copies):
        assert run_call == command[1:]
        name = command[command.index("--name") + 1]
        mounts = [
            value for flag, value in zip(command, command[1:]) if flag == "--mount"
        ]
        assert mounts == [f"type=volume,src={name}-work,dst=/work,readonly"]
        assert not any("type=bind" in arg or str(work) in arg for arg in command)
        assert copy == ["cp", f"{work}/.", f"{name}-work-copy:/work"]
        volumes.add(name + "-work")
    assert len(volumes) == len(runs)
    removed = {call[-1] for call in calls if call[:2] == ["volume", "rm"]}
    assert removed == volumes
    assert docker.leftovers() == []


def test_hidden_cases_copy_inputs_in_and_grade_the_same(
    tmp_path, fake_docker, monkeypatch
):
    task, capture = fixture_task(
        tmp_path, "import target\nresult=target.value", target="value = 42\n"
    )
    cases = [
        {"call": "import target\nresult=target.value", "expected": {"ok": 42}},
        {"call": "import target\nresult=target.value + 1", "expected": {"ok": 42}},
        {
            "call": "raise ValueError('x')",
            "expected": {"raised": "builtins.ValueError"},
        },
    ]
    (task.task_dir / task.hidden_tests_entrypoint).write_text(json.dumps(cases))
    task = replace(task, hidden_tests_expected_tests=3)
    work_root = tmp_path / "scratch"
    work_root.mkdir()

    copied = hidden_tests.run(task, capture, work_root=work_root)

    assert copied["passed"] == 2 and copied["failed"] == 1 and copied["errors"] == 0
    _assert_copy_only(fake_docker)
    # The captured file reached /work through the copy, and every work dir is gone.
    assert not list(work_root.iterdir())

    # The same task through the direct protocol seam grades identically.
    monkeypatch.setattr(
        hidden_tests,
        "_command",
        lambda work, args: protocol_args(args, str(work) + "/"),
    )
    direct = hidden_tests.run(task, capture, work_root=work_root)
    assert _comparable(copied) == _comparable(direct)


def test_regression_variants_each_get_a_fresh_copy(tmp_path, fake_docker, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    stage_probe(workspace, GOOD_TEST, rules=ALTERNATIVE_RULES)

    copied = run_test(probe(), workspace)

    assert outcomes_of(copied) == {
        "original": "failed",
        "insufficient": "failed",
        "reference": "passed",
        "submission": "passed",
    }
    # Readiness probe plus one container per variant, each with its own volume.
    assert len(fake_docker.commands) == 5
    _assert_copy_only(fake_docker)

    monkeypatch.setattr(
        hidden_tests,
        "_command",
        lambda work, args: protocol_args(args, str(work) + "/"),
    )
    direct = run_test(probe(), workspace)
    assert _comparable(copied, tail=False) == _comparable(direct, tail=False)


@pytest.mark.parametrize("step", ["volume-create", "create", "cp"])
def test_copy_failure_refuses_before_any_container_runs(tmp_path, fake_docker, step):
    task, capture = fixture_task(tmp_path, target="raise RuntimeError('must not run')")
    fake_docker.fail(step)
    with pytest.raises(GraderError, match="sandbox unusable: work copy failed"):
        hidden_tests.run(task, capture)
    calls = fake_docker.calls()
    assert not any(call[0] == "run" for call in calls)
    assert fake_docker.leftovers() == []


def test_work_volume_cleanup_failure_is_a_grader_error(tmp_path, fake_docker):
    task, capture = fixture_task(tmp_path)
    fake_docker.fail("volume-rm")
    with pytest.raises(GraderError, match="owned work volume cleanup failed"):
        hidden_tests.run(task, capture)


def test_setup_time_is_excluded_from_the_shared_budget(tmp_path, monkeypatch):
    """Copy-in is harness overhead: it extends the shared deadline like cleanup."""
    task, capture = fixture_task(tmp_path, timeout=2)
    cases = [{"call": "result=42", "expected": {"ok": 42}}] * 2
    (task.task_dir / task.hidden_tests_entrypoint).write_text(json.dumps(cases))
    task = replace(task, hidden_tests_expected_tests=2)
    clock = [0.0]
    monkeypatch.setattr(hidden_tests, "_monotonic", lambda: clock[0])
    monkeypatch.setattr(hidden_tests, "_command", lambda work, args: args)
    timeouts = []

    def execute(command, work, timeout, *, input_data=b""):
        if not input_data:
            nonce = next(work.glob("gym-ready-*")).read_text()
            return 0, False, "gym-ready\n" + nonce
        timeouts.append(timeout)
        clock[0] += 10.1  # 10 s of copy-in, 0.1 s of execution
        nonce = json.loads(input_data)["nonce"]
        line = "GYM_RESULT " + json.dumps({"nonce": nonce, "ok": 42})
        return 0, False, hidden_tests._ExecutionOutput(line, line, 0, 10.0)

    monkeypatch.setattr(hidden_tests, "_execute", execute)
    result = hidden_tests.run(task, capture)
    assert result["passed"] == 2
    assert not result["timed_out"]
    assert timeouts == [2, 2]


def test_work_volume_is_read_from_the_mount_flag():
    assert (
        hidden_tests._work_volume(
            ["docker", "run", "--mount", "type=volume,src=v-work,dst=/work,readonly"]
        )
        == "v-work"
    )
    assert (
        hidden_tests._work_volume(
            ["docker", "run", "--mount", "type=bind,src=/host/dir,dst=/work,readonly"]
        )
        is None
    )
    assert hidden_tests._work_volume([sys.executable, "-c", "pass"]) is None
