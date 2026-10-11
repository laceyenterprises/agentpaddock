"""Trusted comparisons and strict untrusted response framing."""

import json
import os
import sys
from dataclasses import replace

import pytest

from gym_test_support import require_docker_backend

from gym.grader import GraderError, snapshot_verifiers
from gym.hidden_tests import run
from gym.schema import FieldSpec, Task, TaskSchemaError, parse_task_dict
from gym import verifier_runtime


def fixture_task(tmp_path, call="result=42", expected=None, target="", timeout=20):
    root = tmp_path / "task"
    (root / "hidden").mkdir(parents=True)
    (root / "hidden/cases.json").write_text(
        json.dumps(
            [{"call": call, "expected": {"ok": 42} if expected is None else expected}]
        )
    )
    (root / "verifier.py").write_text("")
    (root / "fixture").mkdir()
    (root / "fixture/staged-manifest.json").write_text(
        json.dumps({"editable": ["target.py"]})
    )
    (root / "fixture/target.py").write_text("")
    capture = tmp_path / "capture"
    capture.mkdir()
    (capture / "target.py").write_text(target)
    return Task(
        "fixture",
        (FieldSpec("ok", "boolean"),),
        "verifier.py",
        suite="fix",
        task_dir=root,
        capture_files=("target.py",),
        hidden_tests_entrypoint="hidden/cases.json",
        hidden_tests_expected_tests=1,
        hidden_tests_timeout_seconds=timeout,
    ), capture


@pytest.mark.parametrize(
    "target",
    [
        "import os; os._exit(0)",
        "import sys; sys.exit(0)",
        "print('GYM_RESULT {\"ok\":42}', flush=True)",
        "print('noise'*10000)",
        "import unittest; unittest.TestCase.assertEqual=lambda *a,**k:None",
    ],
)
def test_hostile_submission_fails(tmp_path, protocol_backend, target):
    task, capture = fixture_task(tmp_path, "import target\nresult=0", target=target)
    assert run(task, capture)["status"] == "failed"


@pytest.mark.parametrize(
    "kind",
    ["missing", "malformed", "nonce", "extra", "replay", "keys", "boolean", "nan"],
)
def test_invalid_responses(tmp_path, protocol_backend, monkeypatch, kind):
    import gym.hidden_tests as runner

    task, capture = fixture_task(tmp_path)

    def execute(command, work, timeout, *, input_data=b""):
        if not input_data:
            return 0, False, "gym-ready\n" + next(work.glob("gym-bind-*")).read_text()
        nonce = json.loads(input_data.splitlines()[0])["nonce"]
        record = {"nonce": nonce, "ok": 42}
        if kind == "missing":
            return 0, False, ""
        if kind == "malformed":
            return 0, False, "GYM_RESULT {"
        if kind == "nonce":
            record["nonce"] = "fake"
        if kind == "keys":
            record["passed"] = True
        if kind == "boolean":
            record["ok"] = True
        if kind == "nan":
            record["ok"] = float("nan")
        line = "GYM_RESULT " + json.dumps(record)
        if kind == "extra":
            line = "noise\n" + line
        if kind == "replay":
            line += "\n" + line
        return 0, False, line

    monkeypatch.setattr(runner, "_execute", execute)
    assert run(task, capture)["status"] == "failed"


@pytest.mark.parametrize(
    "call,exception",
    [
        ("raise ValueError()", "ValueError"),
        ("import sys; sys.exit(0)", "SystemExit"),
        ("import sys; sys.exit(7)", "SystemExit"),
        ("raise KeyboardInterrupt()", "KeyboardInterrupt"),
        ("pass", "KeyError"),
        ("result=42; del result", "KeyError"),
    ],
)
def test_expected_exception(tmp_path, protocol_backend, call, exception):
    task, capture = fixture_task(tmp_path, call, {"raised": f"builtins.{exception}"})
    result = run(task, capture)
    assert result["status"] == "passed"
    assert result["errors"] == 0
    assert result["exit_code"] == 0
    assert exception in result["output_tail"]
    assert "JSONDecodeError" not in result["output_tail"]


def test_immutable_specs_never_mounted(tmp_path, protocol_backend, monkeypatch):
    import gym.hidden_tests as runner

    task, capture = fixture_task(tmp_path)
    files = snapshot_verifiers([task])
    (task.task_dir / task.hidden_tests_entrypoint).write_text("invalid")
    original = runner._execute

    def execute(command, work, timeout, *, input_data=b""):
        assert not (work / "hidden").exists()
        assert b"expected" not in input_data
        return original(command, work, timeout, input_data=input_data)

    monkeypatch.setattr(runner, "_execute", execute)
    assert run(task, capture, files)["status"] == "passed"


def test_count_mismatch_is_configuration_error(tmp_path, protocol_backend):
    task, capture = fixture_task(tmp_path)
    with pytest.raises(GraderError, match="count"):
        run(replace(task, hidden_tests_expected_tests=2), capture)


@pytest.mark.parametrize("usable", [False, True])
def test_docker_unavailable_is_error(tmp_path, monkeypatch, usable):
    import gym.hidden_tests as runner

    task, capture = fixture_task(tmp_path)
    monkeypatch.setattr(
        runner.shutil, "which", lambda name: "/usr/bin/false" if usable else None
    )
    monkeypatch.setattr(
        runner, "_execute", lambda *args: (1, False, "Docker unavailable")
    )
    with pytest.raises(
        GraderError,
        match="sandbox unusable: Docker unavailable"
        if usable
        else "require usable Docker",
    ):
        run(task, capture)


def test_hang_fails_closed(tmp_path, protocol_backend):
    task, capture = fixture_task(tmp_path, "while True: pass", timeout=1)
    try:
        result = run(task, capture)
    except GraderError as exc:
        # A contained worker can deny process-group signaling. The runner must
        # refuse grading explicitly rather than report a successful submission.
        assert str(exc) == "owned process-group cleanup denied"
    else:
        assert result["status"] == "failed"
        assert result["timed_out"] or result["exit_code"] != 0


def test_real_engine_hang(tmp_path, docker_backend):
    task, capture = fixture_task(tmp_path, "while True: pass", timeout=1)
    assert run(task, capture)["status"] == "failed"


def test_docker_resource_contract(tmp_path, monkeypatch):
    import gym.hidden_tests as runner

    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/docker")
    command = runner._command(tmp_path, [sys.executable, "-I", "-S"])
    for flag in (
        "--memory=256m",
        "--memory-swap=256m",
        "--pids-limit=32",
        "--read-only",
        "--network=none",
        "--user=65534:65534",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pull=never",
    ):
        assert flag in command
    assert f"type=bind,src={tmp_path},dst=/work,readonly" in command
    assert "/tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777" in command
    assert "--init" not in command
    assert not any("seccomp=" in flag for flag in command)
    assert runner.containment_image() in command


@pytest.mark.parametrize(
    "call,expected",
    [
        (
            "import os\nfrom pathlib import Path\nresult=[os.getuid(),Path('/var/run/docker.sock').exists(),Path('/work/hidden').exists(),*[Path('/sys/fs/cgroup/'+n).read_text().strip() for n in ('memory.max','memory.swap.max','pids.max')]]",
            [65534, False, False, "268435456", "0", "32"],
        ),
        (
            "import socket\ns=socket.socket()\ntry:\n s.connect(('1.1.1.1',443)); result=False\nexcept OSError:\n result=True\nfinally:\n s.close()",
            True,
        ),
        (
            "try:\n open('/work/target.py','w').write('overwrite'); result=False\nexcept OSError:\n result=True",
            True,
        ),
    ],
)
def test_real_engine_constraints(tmp_path, docker_backend, call, expected):
    task, capture = fixture_task(tmp_path, call, {"ok": expected})
    assert run(task, capture)["status"] == "passed"


@pytest.mark.parametrize("expected", [None, True, False, 0, -1, 1.5, "1"])
def test_expected_test_count_required(expected):
    with pytest.raises(TaskSchemaError, match="expected_tests"):
        parse_task_dict(
            {
                "id": "count",
                "suite": "fix",
                "result_schema": {"ok": {"type": "boolean"}},
                "verifier": {},
                "hidden_tests": {
                    "entrypoint": "hidden/cases.json",
                    "expected_tests": expected,
                },
            }
        )


def test_valid_hidden_test_schema():
    task = parse_task_dict(
        {
            "id": "count",
            "suite": "fix",
            "result_schema": {"ok": {"type": "boolean"}},
            "verifier": {},
            "hidden_tests": {"entrypoint": "hidden/cases.json", "expected_tests": 3},
        }
    )
    assert task.hidden_tests_expected_tests == 3


@pytest.mark.parametrize(
    "suite", [None, "coding", "rca", "injection", "docs", "review"]
)
def test_only_fix_can_declare_hidden(suite):
    with pytest.raises(TaskSchemaError, match="requires suite"):
        parse_task_dict(
            {
                "id": "bad",
                "suite": suite,
                "result_schema": {"ok": {"type": "boolean"}},
                "verifier": {},
                "hidden_tests": {"entrypoint": "hidden/cases.json"},
            }
        )


@pytest.mark.parametrize(
    "hidden",
    [
        {"entrypoint": "../test.py"},
        {"entrypoint": "hidden/../cases.json"},
        {"entrypoint": "hidden/test.py"},
        {"entrypoint": "hidden/cases.json", "timeout_seconds": 121},
        {"entrypoint": "hidden/cases.json", "timeout_seconds": True},
    ],
)
def test_hidden_schema_bounds(hidden):
    with pytest.raises(TaskSchemaError):
        parse_task_dict(
            {
                "id": "bad",
                "suite": "fix",
                "result_schema": {"ok": {"type": "boolean"}},
                "verifier": {},
                "hidden_tests": {"expected_tests": 1, **hidden},
            }
        )


def test_verifier_has_no_exec_helper():
    assert not any(
        name in vars(verifier_runtime)
        for name in ("exec", "execute", "run", "subprocess", "Popen")
    )


def test_backend_unusable_before_submission(tmp_path, monkeypatch):
    import gym.hidden_tests as runner

    task, capture = fixture_task(tmp_path, "raise AssertionError('must not run')")
    calls = []
    monkeypatch.setattr(runner, "_command", lambda work, args: args)

    def execute(command, work, timeout):
        calls.append(command)
        return 1, False, "backend refused"

    monkeypatch.setattr(runner, "_execute", execute)
    with pytest.raises(GraderError, match="unusable"):
        run(task, capture)
    assert len(calls) == 1
    assert "memory.swap.max" in calls[0][-1]


@pytest.mark.parametrize("platform", ["darwin", "linux", "freebsd"])
@pytest.mark.parametrize("backend_state", ["missing", "unusable"])
@pytest.mark.parametrize("switch", [None, "1"])
def test_trusted_fixture_env_never_bypasses_backend(
    tmp_path, monkeypatch, platform, backend_state, switch
):
    import gym.hidden_tests as runner

    task, capture = fixture_task(tmp_path, "raise AssertionError('must not execute')\n")
    monkeypatch.setattr(runner.sys, "platform", platform)
    monkeypatch.setattr(
        runner.shutil,
        "which",
        lambda name: None if backend_state == "missing" else "/fake/backend",
    )
    if switch is None:
        monkeypatch.delenv("GYM_HIDDEN_TESTS_TRUSTED_FIXTURE", raising=False)
    else:
        monkeypatch.setenv("GYM_HIDDEN_TESTS_TRUSTED_FIXTURE", switch)
    commands = []

    def execute(command, work, timeout, *, input_data=b""):
        commands.append(command)
        assert not input_data  # Only the readiness probe may run.
        return 1, False, "backend refused"

    monkeypatch.setattr(runner, "_execute", execute)
    with pytest.raises(GraderError, match="usable|supported"):
        run(task, capture)
    if commands:
        assert len(commands) == 1
        assert "memory.swap.max" in commands[0][-1]


def test_fix_activation_named_seam(monkeypatch):
    from gym.hidden_tests import require_fix_activation

    require_fix_activation()
    monkeypatch.setattr("gym.hidden_tests.FIX_ACTIVATION_ENABLED", False)
    with pytest.raises(GraderError, match="integrity acceptance suspended"):
        require_fix_activation()


def test_probe_retries_only_timeout(tmp_path, protocol_backend, monkeypatch):
    import gym.hidden_tests as runner

    task, capture = fixture_task(tmp_path, "pass")
    calls = []

    def execute(command, work, timeout, *, input_data=b""):
        calls.append(timeout)
        return (0, True, "") if len(calls) == 1 else (1, False, "refused")

    monkeypatch.setattr(runner, "_execute", execute)
    with pytest.raises(GraderError, match="unusable"):
        run(task, capture)
    assert calls == [20, 20]


@pytest.mark.parametrize("backend", ["protocol_backend", "docker_backend"])
@pytest.mark.parametrize("forged", [0, 42])
@pytest.mark.parametrize(
    "writer",
    [
        "os.write(1, frame)",
        "sys.__stdout__.write(frame.decode()); sys.__stdout__.flush()",
        "fd=os.dup(1); os.dup2(fd,2); os.write(2,frame)",
    ],
)
def test_nonce_aware_forgery_fails_closed(tmp_path, request, backend, forged, writer):
    request.getfixturevalue(backend)
    target = f"""
import json, os, sys
main = sys.modules['__main__']
assert not hasattr(main, 'request')
assert sys.stdin.closed and sys.__stdin__.closed
try:
    os.read(0, 4096)
except OSError:
    pass
else:
    raise AssertionError('future requests exposed')
assert not hasattr(main, 'nonce')
frame = ('GYM_RESULT ' + json.dumps({{'nonce': getattr(main, 'nonce', 'guessed'), 'ok': {forged}}}) + '\\n').encode()
{writer}
os._exit(0)
"""
    task, capture = fixture_task(tmp_path, "import target\nresult=0", target=target)
    cases = [{"call": "import target\nresult=0", "expected": {"ok": 42}}] * 2
    (task.task_dir / task.hidden_tests_entrypoint).write_text(json.dumps(cases))
    from gym.hidden_tests import shared_work_root

    result = run(
        replace(task, hidden_tests_expected_tests=2),
        capture,
        work_root=shared_work_root() if backend == "docker_backend" else None,
    )
    assert result["status"] == "failed"
    assert result["errors"] == 2
    assert result["passed"] == 0


@pytest.mark.parametrize("value", ["set()", "b'bytes'", "object()", "float('nan')"])
def test_unserializable_observation_only_fails_its_case(
    tmp_path, protocol_backend, value
):
    task, capture = fixture_task(tmp_path)
    cases = [
        {"call": f"result={value}", "expected": {"ok": 42}},
        {"call": "result=42", "expected": {"ok": 42}},
    ]
    (task.task_dir / task.hidden_tests_entrypoint).write_text(json.dumps(cases))
    result = run(replace(task, hidden_tests_expected_tests=2), capture)
    assert result["passed"] == 1
    assert result["failed"] == 1
    assert result["errors"] == 0
    assert "UnserializableResult" in result["output_tail"]


def test_diagnostics_do_not_change_grading(tmp_path, protocol_backend):
    task, capture = fixture_task(
        tmp_path,
        "import target\nprint('debug')\nresult=42",
        target="import warnings\nwarnings.warn('diagnostic')\nprint('target debug')",
    )
    assert run(task, capture)["status"] == "passed"


def test_bounded_oom(tmp_path, docker_backend):
    task, capture = fixture_task(
        tmp_path, "chunks=[]\nwhile True: chunks.append(bytearray(8*1024*1024))"
    )
    result = run(task, capture)
    assert result["status"] == "failed"
    assert result["exit_code"] == 137
    assert not result["timed_out"]


@pytest.mark.parametrize(
    "call",
    [
        """import os, time
children=[]
try:
 for i in range(40):
  pid=os.fork()
  if pid==0:
   time.sleep(10); os._exit(0)
  children.append(pid)
 result=False
except OSError:
 result=True
finally:
 for pid in children:
  os.kill(pid,9); os.waitpid(pid,0)
""",
        """try:
 for i in range(40):
  with open('/tmp/f'+str(i),'wb') as f:
   f.write(b'x'*900000)
 result=False
except OSError:
 result=True
""",
        """result=[]
for path in ('/work/target.py', '/work/new.py', '/etc/new'):
 try:
  open(path, 'w'); result.append(False)
 except OSError:
  result.append(True)
result=all(result)
""",
    ],
    ids=["pids", "tmpfs", "readonly"],
)
def test_real_container_constraints(tmp_path, docker_backend, call):
    task, capture = fixture_task(tmp_path, call, {"ok": True})
    result = run(task, capture)
    assert result["status"] == "passed", result


def test_env_scrubbed(tmp_path, docker_backend, monkeypatch):
    monkeypatch.setenv("GYM_PARENT_CANARY", "private-parent-canary")
    task, capture = fixture_task(
        tmp_path,
        "import os\nallowed={'PATH','HOME','TMPDIR','PYTHONDONTWRITEBYTECODE','LC_CTYPE','HOSTNAME','LANG','GPG_KEY','PYTHON_VERSION','PYTHON_SHA256'}\nresult=[sorted(set(os.environ)-allowed),os.getenv('GYM_PARENT_CANARY')]",
        {"ok": [[], None]},
    )
    result = run(task, capture)
    assert result["status"] == "passed"
    assert "private-parent-canary" not in result["output_tail"]


def test_home_read_contained(tmp_path, docker_backend):
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(dir=Path.home(), prefix="gym-canary-") as canary:
        canary.write(b"private-canary")
        canary.flush()
        task, capture = fixture_task(
            tmp_path,
            f"try:\n open({canary.name!r}).read(); result=False\nexcept OSError:\n result=True",
            {"ok": True},
        )
        assert run(task, capture)["status"] == "passed"


def test_write_contained(tmp_path, docker_backend):
    outside = tmp_path / "escaped"
    task, capture = fixture_task(
        tmp_path,
        f"try:\n open({str(outside)!r},'w').write('bad'); result=False\nexcept OSError:\n result=True",
        {"ok": True},
    )
    assert run(task, capture)["status"] == "passed"
    assert not outside.exists()


def test_fork_loop_contained(tmp_path, docker_backend, monkeypatch):
    import subprocess
    import gym.hidden_tests as runner

    original = runner._command
    containers = []

    def command(work, args):
        result = original(work, args)
        containers.append(result[result.index("--name") + 1])
        return result

    monkeypatch.setattr(runner, "_command", command)
    task, capture = fixture_task(
        tmp_path,
        "import os,time\nfor i in range(8):\n if os.fork()==0:\n  while True: time.sleep(.1)\nwhile True: time.sleep(.1)",
        timeout=2,
    )
    assert run(task, capture)["timed_out"]
    for name in containers:
        inspect = subprocess.run(
            [runner.shutil.which("docker"), "container", "inspect", name],
            env=runner._client_env(),
            capture_output=True,
            timeout=15,
        )
        assert inspect.returncode != 0
        assert b"No such" in inspect.stderr


def test_output_bounded(tmp_path, docker_backend):
    task, capture = fixture_task(
        tmp_path, "import os\nos.write(1,b'x'*100000)\nresult=42"
    )
    result = run(task, capture)
    assert result["status"] == "failed"
    assert len(result["output_tail"].encode()) <= 16384
    assert "OUTPUT_LIMIT_EXCEEDED" in result["output_tail"]


def test_stderr_cannot_supply_completion(tmp_path, protocol_backend):
    task, capture = fixture_task(
        tmp_path,
        "import target\nresult=42",
        target="""import json, os, sys
nonce=getattr(sys.modules['__main__'], 'nonce', 'guessed')
os.write(2, ('GYM_RESULT '+json.dumps({'nonce':nonce,'ok':42})+'\\n').encode())
os._exit(0)
""",
    )
    result = run(task, capture)
    assert result["status"] == "failed"
    assert result["errors"] == 1


def test_failed_execute_preserves_stderr(tmp_path):
    from gym.hidden_tests import _execute

    code, timed_out, output = _execute(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            "import sys; sys.stderr.write('mount refused'); sys.exit(2)",
        ],
        tmp_path,
        2,
    )
    assert code == 2
    assert not timed_out
    assert "mount refused" in output


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("state", ["missing", "unreachable", "timeout", "error"])
def test_backend_readiness_requires_real_engine_when_requested(
    tmp_path, monkeypatch, required, state
):
    import shutil
    import subprocess

    if required:
        monkeypatch.setenv("GYM_REQUIRE_REAL_ENGINE", "1")
    else:
        monkeypatch.delenv("GYM_REQUIRE_REAL_ENGINE", raising=False)
    monkeypatch.setattr(
        shutil, "which", lambda _: None if state == "missing" else "/docker"
    )

    def info(*args, **kwargs):
        if state == "timeout":
            raise subprocess.TimeoutExpired("docker info", 20)
        if state == "error":
            raise OSError("cannot execute docker")
        return subprocess.CompletedProcess(args[0], 1, b"", b"engine unreachable")

    monkeypatch.setattr(subprocess, "run", info)
    with pytest.raises(pytest.fail.Exception if required else pytest.skip.Exception):
        require_docker_backend()


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_invalid_work_root_raises_grader_error(tmp_path, kind):
    from gym.grader import GraderError

    task, capture = fixture_task(tmp_path, "result=42")
    root = tmp_path / "invalid-root"
    if kind == "file":
        root.write_text("not a directory")
    with pytest.raises(
        GraderError, match="work_root must be an existing VM-shared directory"
    ):
        run(task, capture, work_root=root)


def test_readiness_timeout_keeps_retry_evidence_and_cleans_work_root(
    tmp_path, monkeypatch
):
    import gym.hidden_tests as runner

    task, capture = fixture_task(tmp_path, "raise AssertionError('must not run')")
    work_root = tmp_path / "shared"
    work_root.mkdir()
    calls = []
    monkeypatch.setattr(runner, "_command", lambda work, args: args)

    def execute(command, work, timeout):
        calls.append(work)
        return 1, True, ""

    monkeypatch.setattr(runner, "_execute", execute)
    with pytest.raises(GraderError, match="sandbox unusable: timed out"):
        run(task, capture, work_root=work_root)
    assert len(calls) == 2
    assert all(path.parent == work_root for path in calls)
    assert not list(work_root.iterdir())


@pytest.mark.parametrize(
    "call,expected_status",
    [
        ("print('debug'); result=0", "failed"),
        ("print('debug'); result=42", "passed"),
        ("raise ValueError('trace detail')", "failed"),
    ],
)
def test_diagnostics_preserved(tmp_path, protocol_backend, call, expected_status):
    task, capture = fixture_task(tmp_path, call)
    result = run(task, capture)
    assert result["status"] == expected_status
    assert ("trace detail" if "raise" in call else "debug") in result["output_tail"]
    if "raise" in call:
        assert "Traceback" in result["output_tail"]


def test_mixed_case_counts(tmp_path, protocol_backend):
    task, capture = fixture_task(tmp_path)
    cases = [
        {"call": call, "expected": {"ok": 42}}
        for call in [
            "result=42",
            "result=0",
            "import os; os._exit(0)",
            "import os; os._exit(0)",
        ]
    ]
    (task.task_dir / task.hidden_tests_entrypoint).write_text(json.dumps(cases))
    result = run(replace(task, hidden_tests_expected_tests=4), capture)
    assert (result["passed"], result["failed"], result["errors"]) == (1, 1, 2)


def test_expired_budget_counts_unexecuted_cases(
    tmp_path, protocol_backend, monkeypatch
):
    import gym.hidden_tests as runner

    task, capture = fixture_task(tmp_path)
    cases = [{"call": "result=42", "expected": {"ok": 42}}] * 3
    (task.task_dir / task.hidden_tests_entrypoint).write_text(json.dumps(cases))
    original = runner._execute

    def execute(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs.get("input_data"):
            monkeypatch.setattr(runner, "_monotonic", lambda: float("inf"))
        return result

    monkeypatch.setattr(runner, "_execute", execute)
    result = run(replace(task, hidden_tests_expected_tests=3), capture)
    assert (result["passed"], result["failed"], result["errors"]) == (1, 0, 2)
    assert result["timed_out"]


@pytest.mark.parametrize(
    "expected",
    [
        {"raised": 3},
        {"raised": "ValueError"},
        {"raised": "builtins..ValueError"},
        {"ok": "x" * 8193},
    ],
)
def test_invalid_expectation_rejected(tmp_path, protocol_backend, expected):
    task, capture = fixture_task(tmp_path)
    (task.task_dir / task.hidden_tests_entrypoint).write_text(
        json.dumps([{"call": "result=42", "expected": expected}])
    )
    with pytest.raises(GraderError, match="invalid hidden case spec"):
        run(task, capture)


def test_exception_name_spoof_rejected(tmp_path, protocol_backend):
    task, capture = fixture_task(
        tmp_path,
        "class ValueError(Exception): pass\nraise ValueError()",
        {"raised": "builtins.ValueError"},
    )
    assert run(task, capture)["status"] == "failed"


def test_first_nonzero_exit_survives_later_success(tmp_path, protocol_backend):
    task, capture = fixture_task(tmp_path)
    cases = [
        {"call": "import os; os._exit(7)", "expected": {"ok": 42}},
        {"call": "result=42", "expected": {"ok": 42}},
    ]
    (task.task_dir / task.hidden_tests_entrypoint).write_text(json.dumps(cases))
    result = run(replace(task, hidden_tests_expected_tests=2), capture)
    assert result["exit_code"] == 7
    assert [case["exit_code"] for case in result["case_results"]] == [7, 0]
    assert result["errors"] == 1


def test_cleanup_excluded_from_shared_budget(tmp_path, protocol_backend, monkeypatch):
    import gym.hidden_tests as runner

    task, capture = fixture_task(tmp_path, timeout=2)
    cases = [{"call": "result=42", "expected": {"ok": 42}}] * 2
    (task.task_dir / task.hidden_tests_entrypoint).write_text(json.dumps(cases))
    clock = [0.0]
    monkeypatch.setattr(runner, "_monotonic", lambda: clock[0])
    original = runner._execute

    def execute(*args, **kwargs):
        code, timeout, output = original(*args, **kwargs)
        if kwargs.get("input_data"):
            clock[0] += 10.1
            output.cleanup_seconds = 10.0
        return code, timeout, output

    monkeypatch.setattr(runner, "_execute", execute)
    result = run(replace(task, hidden_tests_expected_tests=2), capture)
    assert result["passed"] == 2
    assert not result["timed_out"]
    assert all(case["cleanup_seconds"] == 10 for case in result["case_results"])


def test_numeric_representation_is_contract(tmp_path, protocol_backend):
    task, capture = fixture_task(tmp_path, "result=1.0", {"ok": 1})
    assert run(task, capture)["failed"] == 1


@pytest.mark.parametrize(
    "code,protocol,diagnostics",
    [
        (0, "gym-ready\n", ""),
        (0, "gym-ready\nwrong", ""),
        (
            1,
            "gym-ready\n",
            'Traceback (most recent call last):\n  File "<string>", line 1, in <module>\n'
            "PermissionError: [Errno 13] Permission denied: '/work/gym-bind-test'\n",
        ),
    ],
)
def test_bind_preflight_refuses_before_copy_and_cleans(
    tmp_path, monkeypatch, code, protocol, diagnostics
):
    from gym import hidden_tests

    task, capture = fixture_task(
        tmp_path, target="raise RuntimeError('must not execute')"
    )
    work_root = tmp_path / "shared"
    work_root.mkdir()
    seen = []
    reported = hidden_tests._ExecutionOutput(protocol + diagnostics, protocol)
    monkeypatch.setattr(hidden_tests.shutil, "which", lambda _: "/docker")

    def execute(command, work, timeout, *, input_data=b""):
        seen.append(work)
        assert not input_data
        assert not (work / "target.py").exists()
        assert not (work / "hidden").exists()
        assert len(list(work.iterdir())) == 1
        assert "Path('/work/gym-bind-" in command[-1]
        return code, False, reported

    monkeypatch.setattr(hidden_tests, "_execute", execute)
    with pytest.raises(
        GraderError, match="work root is not shared with the Docker VM"
    ) as error:
        run(task, capture, work_root=work_root)
    assert str(reported) in str(error.value)
    assert len(seen) == 1
    assert not seen[0].exists()
    assert not list(work_root.iterdir())


@pytest.mark.parametrize("umask", [0o002, 0o022, 0o077])
def test_shared_work_root_creates_group_writable_parent(tmp_path, monkeypatch, umask):
    from gym import hidden_tests
    from gym import host_services

    (tmp_path / "hq").mkdir(mode=0o775)
    monkeypatch.setattr(
        host_services.current(), "scratch_dir", lambda: tmp_path / "hq/tmp"
    )
    previous_umask = os.umask(umask)
    try:
        root = hidden_tests.shared_work_root()
    finally:
        os.umask(previous_umask)
    assert root == tmp_path / "hq/tmp"
    assert root.stat().st_mode & 0o770 == 0o770
    assert root.stat().st_mode & 0o002 == 0
    assert hidden_tests.shared_work_root() == root


@pytest.mark.parametrize("mode", [0o700, 0o755, 0o1770])
def test_shared_work_root_repairs_owned_parent(tmp_path, monkeypatch, mode):
    from gym import hidden_tests
    from gym import host_services

    root = tmp_path / "tmp"
    root.mkdir()
    root.chmod(mode)
    monkeypatch.setattr(
        host_services.current(), "scratch_dir", lambda: tmp_path / "tmp"
    )
    assert hidden_tests.shared_work_root() == root
    assert root.stat().st_mode & 0o7777 == mode | 0o070


def test_shared_work_root_preserves_other_owner_permissions(tmp_path, monkeypatch):
    from gym import hidden_tests
    from gym import host_services

    root = tmp_path / "tmp"
    root.mkdir()
    root.chmod(0o775)
    monkeypatch.setattr(
        host_services.current(), "scratch_dir", lambda: tmp_path / "tmp"
    )
    monkeypatch.setattr(hidden_tests.os, "getuid", lambda: root.stat().st_uid + 1)

    def deny_chmod(*args, **kwargs):
        pytest.fail("shared parent owned by another account must not be chmodded")

    monkeypatch.setattr(type(root), "chmod", deny_chmod)
    assert hidden_tests.shared_work_root() == root
    assert root.stat().st_mode & 0o777 == 0o775


def test_real_engine_parent_memory_and_descriptors_inaccessible(
    tmp_path, docker_backend
):
    target = """
import ctypes, gc, inspect, os, sys
assert not hasattr(sys.modules['__main__'], 'nonce')
assert not any(isinstance(obj, dict) and 'nonce' in obj for obj in gc.get_objects())
frame = inspect.currentframe()
while frame:
    assert 'nonce' not in frame.f_globals and 'nonce' not in frame.f_locals
    frame = frame.f_back
for pid in {1, os.getppid()}:
    for path in ('mem', 'fd/1', 'fd/2'):
        try:
            fd = os.open('/proc/' + str(pid) + '/' + path, os.O_WRONLY if path.startswith('fd/') else os.O_RDONLY)
        except OSError:
            pass
        else:
            os.close(fd)
            raise AssertionError('parent accessible')
libc = ctypes.CDLL(None, use_errno=True)
assert libc.ptrace(16, os.getppid(), 0, 0) == -1
"""
    task, capture = fixture_task(tmp_path, "import target\nresult=42", target=target)
    from gym.hidden_tests import shared_work_root

    assert run(task, capture, work_root=shared_work_root())["status"] == "passed"


@pytest.mark.parametrize("backend", ["protocol_backend", "docker_backend"])
def test_submitted_main_has_no_nonce(tmp_path, request, backend):
    request.getfixturevalue(backend)
    task, capture = fixture_task(
        tmp_path,
        "import target\nresult=target.isolated",
        {"ok": True},
        target="import sys\nisolated = not hasattr(sys.modules['__main__'], 'nonce')",
    )
    from gym.hidden_tests import shared_work_root

    result = run(
        task,
        capture,
        work_root=shared_work_root() if backend == "docker_backend" else None,
    )
    assert result["status"] == "passed"


def test_parent_protection_failure_prevents_target_execution(
    tmp_path, protocol_backend, monkeypatch
):
    import gym.hidden_tests as runner

    monkeypatch.setattr(
        runner,
        "_WRAPPER",
        runner._WRAPPER.replace(
            "protect_parent()\nrequest", "raise OSError('protection denied')\nrequest"
        ),
    )
    task, capture = fixture_task(
        tmp_path, "import target\nresult=42", target="raise AssertionError('executed')"
    )
    result = run(task, capture)
    assert result["errors"] == 1
    assert "protection denied" in result["output_tail"]
