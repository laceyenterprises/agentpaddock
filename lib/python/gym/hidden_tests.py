"""Fail-closed OS containment for stdlib-only fix fixtures.

Hidden JSON specs stay in the host process. The sandbox executes calls and
returns observations; only the trusted controller compares expectations.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import resource
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from gym.schema import REGRESSION_VARIANTS

_monotonic = time.monotonic
_MAX_EXPECTATION_BYTES = 8192

# Spawn a fresh interpreter: no nonce-bearing Python state is inherited.
_CHILD = r"""
import contextlib, json, os, sys, traceback
result_fd = int(sys.argv[1])
sys.path.insert(0, '.')
call = sys.stdin.read()
sys.stdin.close()
os.close(0)
scope = {'__name__': '__gym_call__'}
try:
    # Ordinary diagnostics must not contaminate the response channel.
    with contextlib.redirect_stdout(sys.stderr):
        exec(call, scope)
    response = {'ok': scope['result']}
except BaseException as exc:
    traceback.print_exc()
    response = {'raised': type(exc).__module__ + '.' + type(exc).__qualname__}
try:
    frame = json.dumps(response, allow_nan=False)
except (ValueError, TypeError, RecursionError):
    frame = json.dumps({'raised': 'UnserializableResult'})
os.write(result_fd, frame.encode())
os.close(result_fd)
"""

_WRAPPER_BODY = r"""
import ctypes, json, os, selectors, subprocess, sys
# Protect nonce-bearing memory and /proc descriptors before a child exists.
def protect_parent():
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(4, 0, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'PR_SET_DUMPABLE')
protect_parent()
request = json.loads(sys.stdin.readline())
call, nonce = request['call'], request['nonce']
del request
sys.stdin.close()
os.close(0)
read_fd, write_fd = os.pipe()
child = subprocess.Popen(
    [sys.executable, '-I', '-S', '-c', CHILD, str(write_fd)],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    close_fds=True, pass_fds=(write_fd,),
)
os.close(write_fd)
child.stdin.write(call.encode())
child.stdin.close()
del call
selector = selectors.DefaultSelector()
selector.register(read_fd, selectors.EVENT_READ, 'result')
selector.register(child.stdout, selectors.EVENT_READ, 'diagnostics')
payload = bytearray()
diagnostic_bytes = 0
while selector.get_map():
    for key, _ in selector.select():
        chunk = os.read(key.fd, 4096)
        if not chunk:
            selector.unregister(key.fileobj)
            continue
        if key.data == 'result':
            payload.extend(chunk)
            if len(payload) > 8192:
                raise ValueError('child result limit exceeded')
        else:
            diagnostic_bytes += len(chunk)
            if diagnostic_bytes > 16384:
                sys.stderr.write('OUTPUT_LIMIT_EXCEEDED\n')
                sys.stderr.flush()
                raise ValueError('child diagnostic limit exceeded')
            os.write(2, chunk)
os.close(read_fd)
returncode = child.wait()
if returncode != 0:
    sys.exit(128 - returncode if returncode < 0 else returncode)
def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate child response key')
        value[key] = item
    return value
def reject_constant(value):
    raise ValueError(value)
response = json.loads(payload, object_pairs_hook=unique_object, parse_constant=reject_constant)
if not isinstance(response, dict) or set(response) not in ({'ok'}, {'raised'}):
    raise ValueError('invalid child response')
if 'raised' in response and not isinstance(response['raised'], str):
    raise ValueError('invalid child exception')
print('GYM_RESULT ' + json.dumps({'nonce': nonce, **response}, allow_nan=False), flush=True)
"""
_WRAPPER = "CHILD = " + repr(_CHILD) + "\n" + _WRAPPER_BODY

# HRF-03: the agent's unittest file runs in the same child seat as a hidden
# call. Counts come from the unittest result object, never from printed text.
_REGRESSION_CHILD = r"""
import contextlib, json, os, sys, traceback, unittest
result_fd = int(sys.argv[1])
sys.path.insert(0, '.')
module = sys.stdin.read()
sys.stdin.close()
os.close(0)
class Report:
    # Test prints and the unittest report stay under the wrapper's limit.
    def __init__(self, limit):
        self.left = limit
    def write(self, text):
        data = str(text).encode('utf-8', 'replace')[: max(self.left, 0)]
        self.left -= len(data)
        if data:
            os.write(2, data)
        return len(text)
    def flush(self):
        pass
report = Report(8192)
try:
    with contextlib.redirect_stdout(report), contextlib.redirect_stderr(report):
        try:
            suite = unittest.TestLoader().loadTestsFromName(module)
        except Exception as exc:
            # A test module that cannot load is the test's error, as in unittest.
            failure = exc
            class ModuleLoadFailure(unittest.TestCase):
                def runTest(self):
                    raise failure
            suite = unittest.TestSuite([ModuleLoadFailure()])
        outcome = unittest.TextTestRunner(stream=report).run(suite)
    response = {'ok': {
        'run': outcome.testsRun,
        'failures': len(outcome.failures),
        'errors': len(outcome.errors),
        'skipped': len(outcome.skipped),
        'expected_failures': len(outcome.expectedFailures),
        'unexpected_successes': len(outcome.unexpectedSuccesses),
    }}
except BaseException as exc:
    traceback.print_exc()
    response = {'raised': type(exc).__module__ + '.' + type(exc).__qualname__}
try:
    frame = json.dumps(response, allow_nan=False)
except (ValueError, TypeError, RecursionError):
    frame = json.dumps({'raised': 'UnserializableResult'})
os.write(result_fd, frame.encode())
os.close(result_fd)
"""
_REGRESSION_WRAPPER = "CHILD = " + repr(_REGRESSION_CHILD) + "\n" + _WRAPPER_BODY

# The test must fail on code that still has the defect and pass on fixed code.
REGRESSION_EXPECTED = {
    "original": "failed",
    "insufficient": "failed",
    "reference": "passed",
    "submission": "passed",
}
REGRESSION_SUBCHECKS = {
    "original": "regression_test_fails_on_original",
    "insufficient": "regression_test_fails_on_insufficient",
    "reference": "regression_test_passes_on_reference",
    "submission": "regression_test_passes_on_submission",
}
_REGRESSION_COUNTS = (
    "run",
    "failures",
    "errors",
    "skipped",
    "expected_failures",
    "unexpected_successes",
)


class _ExecutionOutput(str):
    """Bounded operator output with a separate stdout protocol channel."""

    def __new__(cls, tail: str, protocol: str, cleanup_seconds: float = 0):
        value = super().__new__(cls, tail)
        value.protocol = protocol
        value.cleanup_seconds = cleanup_seconds
        return value


def _reject_constant(value):
    raise ValueError(value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate response key")
        result[key] = value
    return result


def _limits(timeout: int) -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


# Pinned stdlib runtime; provision explicitly with docker pull, never at grade time.
_IMAGE = "python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea"


def containment_image() -> str:
    """Return the pinned Docker image used by the hidden-test runner."""
    return _IMAGE


def shared_work_root() -> Path:
    """Resolve group-writable host scratch shared by HQ users and the Docker VM."""
    from gym import host_services

    root = host_services.current().scratch_dir()
    root.mkdir(mode=0o775, parents=True, exist_ok=True)
    info = root.stat()
    # mkdir honors umask and leaves existing permissions untouched. The owner
    # repairs group access, including roots previously created with mode 0700.
    # A second account can use a shared root but cannot chmod the owner's path.
    if info.st_uid == os.getuid() and info.st_mode & 0o070 != 0o070:
        root.chmod((info.st_mode & 0o7777) | 0o070)
    return root


FIX_ACTIVATION_ENABLED = True


def require_fix_activation() -> None:
    """Named emergency closure seam; operator accepted lift in RFDLIFT-01."""
    if not FIX_ACTIVATION_ENABLED:
        from gym.grader import GraderError

        raise GraderError("fix activation blocked: integrity acceptance suspended")


def _command(work: Path, args: list[str]) -> list[str]:
    from gym.grader import GraderError

    if sys.platform != "darwin" and not sys.platform.startswith("linux"):
        raise GraderError("hidden tests: no supported sandbox backend")
    backend = shutil.which("docker")
    if not backend:
        raise GraderError("hidden tests require usable Docker Linux containment")
    # Caller context/host is used unchanged. No socket, credentials or host
    # environment are mounted/passed to the container. Only copied input bytes.
    # No --init: a same-UID init would retain reopenable protocol descriptors
    # under /proc/1/fd. The non-dumpable trusted wrapper is container PID 1.
    work.chmod(0o755)
    for path in work.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    return [
        backend,
        "run",
        "--name",
        "gym-hidden-" + secrets.token_hex(16),
        "--pull=never",
        "--rm",
        "--interactive",
        "--memory=256m",
        "--memory-swap=256m",
        "--pids-limit=32",
        "--cpus=1",
        "--ulimit",
        "cpu=120:121",
        "--ulimit",
        "fsize=1048576:1048576",
        "--ulimit",
        "core=0:0",
        "--read-only",
        "--network=none",
        "--user=65534:65534",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777",
        "--mount",
        f"type=bind,src={work},dst=/work,readonly",
        "--workdir=/work",
        "--env",
        "HOME=/tmp",
        "--env",
        "TMPDIR=/tmp",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        _IMAGE,
        "python",
        *args[1:],
    ]


def _client_env() -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(Path.home())}
    for key in (
        "DOCKER_CONTEXT",
        "DOCKER_HOST",
        "DOCKER_CONFIG",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    ):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def _execute(
    command: list[str], work: Path, timeout: int, *, input_data: bytes = b""
) -> tuple[int, bool, str]:
    from gym.grader import GraderError

    container = command[command.index("--name") + 1] if "--name" in command else None
    env = (
        _client_env()
        if container
        else {
            "PATH": "/usr/bin:/bin",
            "HOME": str(work),
            "TMPDIR": str(work),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    with (
        tempfile.TemporaryFile(dir=work) as output,
        tempfile.TemporaryFile(dir=work) as diagnostics,
        tempfile.TemporaryFile(dir=work) as input_file,
    ):
        input_file.write(input_data)
        input_file.seek(0)
        try:
            proc = subprocess.Popen(
                command,
                cwd=work,
                env=env,
                stdin=input_file,
                stdout=output,
                stderr=diagnostics,
                start_new_session=True,
                preexec_fn=lambda: _limits(timeout),
            )
        except OSError as exc:
            raise GraderError(f"hidden-test sandbox unavailable: {exc}") from exc
        timed_out = False
        cleanup_error = None
        try:
            # WNOWAIT preserves ownership of the leader PID until cleanup; poll()
            # and wait() both reap and would allow a recycled process group.
            deadline = time.monotonic() + timeout
            while os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                time.sleep(0.02)
        finally:
            cleanup_started = time.monotonic()
            if container:
                try:
                    removed = subprocess.run(
                        [command[0], "rm", "--force", container],
                        env=env,
                        capture_output=True,
                        timeout=15,
                    )
                    if removed.returncode and b"No such container" not in removed.stderr:
                        cleanup_error = "owned container cleanup failed"
                except (OSError, subprocess.TimeoutExpired):
                    cleanup_error = "owned container cleanup unavailable"
            if container:
                # Container ownership, including every descendant, is enforced
                # by Docker's unique name. The CLI may need time to observe rm.
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()  # Unreaped, owned leader only; no PID reuse.
                    proc.wait(timeout=5)
            else:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    if timed_out:
                        cleanup_error = "owned process-group cleanup denied"
                proc.wait(timeout=5)
        cleanup_seconds = time.monotonic() - cleanup_started
        if cleanup_error:
            raise GraderError(cleanup_error)
        diagnostics.seek(0, os.SEEK_END)
        diagnostics.seek(max(0, diagnostics.tell() - 16384))
        diagnostics_tail = diagnostics.read().decode("utf-8", errors="replace")
        output.seek(0, os.SEEK_END)
        oversized = output.tell() > 16384
        output.seek(max(0, output.tell() - 16384))
        tail = output.read().decode("utf-8", errors="replace")
        tail = tail.encode("utf-8")[-16384:].decode("utf-8", errors="ignore")
        if oversized:
            tail = "OUTPUT_LIMIT_EXCEEDED\n" + tail.encode("utf-8")[-16362:].decode(
                "utf-8", errors="ignore"
            )
        protocol = tail
        if diagnostics_tail:
            tail = (
                (tail + "\n" + diagnostics_tail)
                .encode("utf-8")[-16384:]
                .decode("utf-8", errors="ignore")
            )
        return proc.returncode, timed_out, _ExecutionOutput(tail, protocol, cleanup_seconds)


def _ready(work: Path, python: str) -> None:
    """Probe the exact sandbox/interpreter before copying or executing code."""
    from gym.grader import GraderError

    bind_nonce = secrets.token_hex(32)
    nonce_file = "gym-bind-" + secrets.token_hex(16)
    (work / nonce_file).write_text(bind_nonce, encoding="utf-8")
    probe_code = (
        "from pathlib import Path; import os; "
        "c=Path('/sys/fs/cgroup'); "
        "assert (c/'memory.max').read_text().strip() == '268435456'; "
        "assert (c/'memory.swap.max').read_text().strip() == '0'; "
        "assert (c/'pids.max').read_text().strip() == '32'; "
        "assert os.getuid() == 65534; print('gym-ready', flush=True); "
        f"print(Path('/work/{nonce_file}').read_text(encoding='utf-8'), flush=True)"
    )
    probe = _command(work, [python, "-I", "-S", "-c", probe_code])
    for attempt in range(2):
        code, timeout, output = _execute(probe, work, 20)
        if not timeout:
            break
    protocol = getattr(output, "protocol", output).splitlines()
    if protocol and protocol[0] == "gym-ready" and protocol[1:] != [bind_nonce]:
        raise GraderError(
            f"hidden tests: work root is not shared with the Docker VM: {work}\n{output}"
        )
    if code or timeout or protocol != ["gym-ready", bind_nonce]:
        detail = "timed out: " + output if timeout else output
        raise GraderError(f"hidden-test sandbox unusable: {detail}")
    (work / nonce_file).unlink()


def _response(output: str, code: int, timeout: bool, nonce: str) -> dict:
    """Parse the one nonce-framed wrapper line; anything else raises ValueError."""
    lines = getattr(output, "protocol", output).splitlines()
    if len(lines) != 1 or code != 0 or timeout:
        raise ValueError("incomplete or extra output")
    line = lines[0]
    if not line.startswith("GYM_RESULT "):
        raise ValueError("unframed output")
    response = json.loads(
        line.removeprefix("GYM_RESULT "),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(response, dict) or response.pop("nonce", None) != nonce:
        raise ValueError("nonce mismatch")
    if set(response) not in ({"ok"}, {"raised"}):
        raise ValueError("invalid response")
    json.dumps(response, sort_keys=True, allow_nan=False)
    return response


def run(
    task,
    captured: Path,
    files: dict | None = None,
    *,
    work_root: Path | None = None,
    audit_cases: list | None = None,
) -> dict:
    """Run immutable hidden tests; work_root must be shared with the Docker VM."""
    from gym.grader import GraderError, snapshot_verifiers
    from gym.schema import is_fix_family

    if not is_fix_family(task.suite) or not task.hidden_tests_entrypoint:
        raise GraderError("hidden tests require a fix-family task and entrypoint")
    if not 1 <= task.hidden_tests_timeout_seconds <= 120:
        raise GraderError("invalid hidden-test timeout")
    if type(task.hidden_tests_expected_tests) is not int or task.hidden_tests_expected_tests < 1:
        raise GraderError("hidden tests require a positive expected_tests count")
    if work_root is not None and not work_root.is_dir():
        raise GraderError("work_root must be an existing VM-shared directory")
    files = snapshot_verifiers([task]) if files is None else files
    with tempfile.TemporaryDirectory(prefix="gym-hidden-", dir=work_root) as temporary:
        work = Path(temporary).resolve()
        python = str(Path(sys.executable).resolve())
        _ready(work, python)
        for name in task.capture_files:
            if Path(name).parts[0] == "hidden" or name == "sandbox.sb":
                raise GraderError("capture collides with hidden-test infrastructure")
            source = captured / name
            if source.exists():
                target = work / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
        source = task.task_dir / task.hidden_tests_entrypoint
        content = files.get(source)
        if not isinstance(content, bytes):
            raise GraderError("hidden case spec missing from snapshot")
        try:
            cases = json.loads(content)
            if not isinstance(cases, list) or len(cases) != task.hidden_tests_expected_tests:
                raise ValueError("case count mismatch")
            for case in cases:
                if set(case) != {"call", "expected"} or not isinstance(case["call"], str):
                    raise ValueError("invalid case")
                expected = case["expected"]
                serialized = json.dumps(expected, allow_nan=False)
                if len(serialized.encode("utf-8")) > _MAX_EXPECTATION_BYTES:
                    raise ValueError("expectation exceeds 8192-byte frame allowance")
                if not isinstance(expected, dict) or set(expected) not in ({"ok"}, {"raised"}):
                    raise ValueError("invalid expectation")
                if "raised" in expected and (
                    not isinstance(expected["raised"], str)
                    or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", expected["raised"])
                ):
                    raise ValueError("raised expectation must be module-qualified")
        except (ValueError, TypeError, RecursionError) as exc:
            raise GraderError(f"invalid hidden case spec: {exc}") from exc
        responses = []
        outputs = []
        timed_out = False
        code = 0
        errors = 0
        first_nonzero_code = 0
        case_results = []
        deadline = _monotonic() + task.hidden_tests_timeout_seconds
        if audit_cases is None:
            audit_cases = []
        audit_cases.clear()
        audit_cases.extend(
            {
                "index": i,
                "label": f"case-{i}",
                "outcome": "error",
                "elapsed_seconds": 0,
                "executed": False,
            }
            for i in range(len(cases))
        )
        for index, case in enumerate(cases):
            # A fresh interpreter gets exactly one request. Submitted code cannot
            # consume later nonces/calls, and stdin is closed before it executes.
            remaining = deadline - _monotonic()
            if remaining <= 0:
                timed_out = True
                errors += len(cases) - len(responses)
                for i in range(index, len(cases)):
                    audit_cases[i]["outcome"] = "timed_out"
                break
            nonce = secrets.token_hex(32)
            request = json.dumps({"nonce": nonce, "call": case["call"]}) + "\n"
            command = _command(work, [python, "-I", "-S", "-c", _WRAPPER])
            started = _monotonic()
            code, timeout, output = _execute(
                command, work, math.ceil(remaining), input_data=request.encode()
            )
            elapsed = _monotonic() - started
            cleanup_seconds = getattr(output, "cleanup_seconds", 0)
            deadline += cleanup_seconds
            if code and not first_nonzero_code:
                first_nonzero_code = code
            timed_out |= timeout
            outputs.append(output)
            response = None
            try:
                response = _response(output, code, timeout, nonce)
            except (ValueError, TypeError, RecursionError):
                response = None
                errors += 1
            case_results.append(
                {
                    "exit_code": code,
                    "timed_out": timeout,
                    "elapsed_seconds": elapsed,
                    "cleanup_seconds": cleanup_seconds,
                    "error": response is None,
                }
            )
            matches = response is not None and json.dumps(
                response, sort_keys=True, allow_nan=False
            ) == json.dumps(case["expected"], sort_keys=True, allow_nan=False)
            outcome = (
                "timed_out"
                if timeout
                else "error"
                if response is None
                else "passed"
                if matches
                else "failed"
            )
            record = {
                "index": index,
                "label": f"case-{index}",
                "outcome": outcome,
                "elapsed_seconds": elapsed,
            }
            if response is not None and "raised" in response:
                # Exception identifiers are untrusted observation data. Only builtin
                # types can be retained verbatim; custom names may encode secrets.
                import builtins

                raised = response["raised"]
                name = raised.removeprefix("builtins.") if isinstance(raised, str) else ""
                kind = getattr(builtins, name, None)
                record["raised_type"] = (
                    raised
                    if isinstance(kind, type)
                    and issubclass(kind, BaseException)
                    and raised == "builtins." + name
                    else "custom"
                )
                if not matches:
                    record["outcome"] = "raised"
            record["executed"] = True
            audit_cases[index] = record
            responses.append(response)
        # Compare JSON types, including bool versus number. Tuples become arrays
        # and object keys become strings; type-sensitive calls must encode types.
        passed = sum(
            response is not None
            and json.dumps(response, sort_keys=True, allow_nan=False)
            == json.dumps(case["expected"], sort_keys=True, allow_nan=False)
            for response, case in zip(responses, cases)
        )
        output = "\n".join(outputs).encode("utf-8")[-16384:].decode("utf-8", errors="ignore")
        return {
            "status": "passed" if passed == len(cases) and not errors else "failed",
            "passed": passed,
            "failed": len(cases) - passed - errors,
            "errors": errors,
            "timed_out": timed_out,
            "exit_code": first_nonzero_code,
            "case_results": case_results,
            "audit_cases": audit_cases,
            "output_tail": output,
        }


def _variant_files(task, files: dict, editable: list[str]) -> dict[str, dict[str, bytes]]:
    """Trusted bytes for every non-test editable file in each graded variant."""
    from gym.grader import GraderError

    targets = [name for name in editable if name != task.regression_test_path]
    variants = {}
    for variant in (*REGRESSION_VARIANTS[:3], *task.regression_test_must_fail_variants):
        root = task.task_dir / (
            "fixture" if variant == "original" else f"hidden/variants/{variant}"
        )
        if variant != "original":
            shipped = {
                path.relative_to(root).as_posix()
                for path in files
                if isinstance(path, Path) and root in path.parents
            }
            if shipped != set(targets):
                raise GraderError(
                    f"task {task.id}: hidden/variants/{variant} must ship exactly {targets}"
                )
        variants[variant] = {}
        for name in targets:
            content = files.get(root / name)
            if not isinstance(content, bytes):
                raise GraderError(
                    f"task {task.id}: {variant} variant of {name} missing from snapshot"
                )
            variants[variant][name] = content
    return variants


def _stage_variant(task, work: Path, captured: Path, files: dict, replaced: dict | None) -> None:
    """Rebuild work: captured files, with every non-test file trusted unless submitted."""
    from gym.grader import GraderError

    for child in work.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    for name in task.capture_files:
        if Path(name).parts[0] == "hidden" or name == "sandbox.sb":
            raise GraderError("capture collides with hidden-test infrastructure")
        content = None
        if replaced is not None and name != task.regression_test_path:
            # Original/insufficient/reference runs keep only the agent's test.
            content = replaced.get(name, files.get(task.task_dir / "fixture" / name))
        if not isinstance(content, bytes) and (captured / name).is_file():
            content = (captured / name).read_bytes()
        if isinstance(content, bytes):
            target = work / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)


def _regression_outcome(response: dict | None, timeout: bool) -> tuple[str, dict | None]:
    if timeout:
        return "timed_out", None
    counts = response.get("ok") if response else None
    if (
        not isinstance(counts, dict)
        or set(counts) != set(_REGRESSION_COUNTS)
        or any(type(value) is not int or value < 0 for value in counts.values())
    ):
        return "error", None
    if counts["failures"] + counts["errors"]:
        return "failed", counts
    if counts["run"] and not any(counts[key] for key in _REGRESSION_COUNTS[3:]):
        return "passed", counts
    # No test ran, or skips and expected failures stand in for assertions.
    return "inconclusive", counts


def run_regression_test(
    task,
    captured: Path,
    files: dict | None = None,
    *,
    work_root: Path | None = None,
    hidden_passed: bool,
) -> dict:
    """Run the agent's unittest file against built-in and named variants, one container each.

    Same containment, limits and nonce isolation as a hidden case. The test
    sees only the original code and the agent's own fix; the insufficient and
    reference variants come from the task's snapshotted hidden/variants/.
    """
    from gym.fix_audit import editable_files
    from gym.grader import GraderError, snapshot_verifiers
    from gym.schema import is_fix_family

    path = task.regression_test_path
    if not is_fix_family(task.suite) or not task.hidden_tests_entrypoint or not path:
        raise GraderError("regression tests require a fix-family task with hidden tests")
    if not 1 <= task.regression_test_timeout_seconds <= 60:
        raise GraderError("invalid regression-test timeout")
    if work_root is not None and not work_root.is_dir():
        raise GraderError("work_root must be an existing VM-shared directory")
    files = snapshot_verifiers([task]) if files is None else files
    variants = _variant_files(task, files, editable_files(task, files))
    runs = [
        {
            "variant": variant,
            "outcome": "not_run",
            "counts": None,
            "exit_code": 0,
            "timed_out": False,
            "elapsed_seconds": 0,
        }
        for variant in (*REGRESSION_VARIANTS, *task.regression_test_must_fail_variants)
    ]
    if not hidden_passed or not (captured / path).is_file():
        reason = "hidden tests did not pass" if not hidden_passed else "test file missing"
        return {
            "status": "not_run",
            "reason": reason,
            "path": path,
            "runs": runs,
            "output_tail": "",
        }
    outputs = []
    with tempfile.TemporaryDirectory(prefix="gym-regression-", dir=work_root) as temporary:
        work = Path(temporary).resolve()
        python = str(Path(sys.executable).resolve())
        _ready(work, python)
        for record in runs:
            variant = record["variant"]
            _stage_variant(task, work, captured, files, variants.get(variant))
            # A fresh container and nonce per variant; the child only learns the module.
            nonce = secrets.token_hex(32)
            request = json.dumps({"nonce": nonce, "call": path.removesuffix(".py")}) + "\n"
            command = _command(work, [python, "-I", "-S", "-c", _REGRESSION_WRAPPER])
            started = _monotonic()
            code, timeout, output = _execute(
                command, work, task.regression_test_timeout_seconds, input_data=request.encode()
            )
            outputs.append(f"[{variant}]\n{output}")
            try:
                response = _response(output, code, timeout, nonce)
            except (ValueError, TypeError, RecursionError):
                response = None
            record["outcome"], record["counts"] = _regression_outcome(response, timeout)
            record.update(
                exit_code=code,
                timed_out=timeout,
                elapsed_seconds=_monotonic() - started,
            )
    output = "\n".join(outputs).encode("utf-8")[-16384:].decode("utf-8", errors="ignore")
    return {"status": "completed", "path": path, "runs": runs, "output_tail": output}


def regression_subchecks(result: dict) -> list[dict]:
    """The required subchecks; reasons carry outcomes and counts only."""
    checks = []
    for record in result["runs"]:
        variant = record["variant"]
        expected = REGRESSION_EXPECTED.get(variant, "failed")
        ok = record["outcome"] == expected
        reason = None
        if not ok:
            counts = record["counts"]
            detail = (
                " (" + ", ".join(f"{key}={counts[key]}" for key in _REGRESSION_COUNTS) + ")"
                if counts
                else ""
            )
            reason = f"regression test on {variant}: expected {expected}, got {record['outcome']}{detail}"
        checks.append(
            {
                "name": REGRESSION_SUBCHECKS.get(variant, f"regression_test_fails_on_{variant}"),
                "ok": ok,
                "reason": reason,
                "required": True,
            }
        )
    return checks
