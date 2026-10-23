"""Out-of-band grader: trajectory -> one outcome per cell.

The grader reads only declared workspace files from the agent sandbox. It receives an already-read
:class:`Trajectory` (the run's terminal status, the submitted result, and the
raw transcript / control rows), extracts the tool-call snapshot, and runs the
task's verifier in a *scrubbed ephemeral env* that exposes only ``BENCH_RESULT``
and ``BENCH_TOOLCALLS``, plus declared read-only workspace captures.
The verifier can therefore never read ``expected/`` or
reach the ledger — the whole out-of-band design (SPEC §6, "a verifier leaks
ground truth").

Outcome resolution is a strict ladder (SPEC §10), exactly one per cell:

  agent_error   — the run itself failed (worker died / non-terminal-success).
  no_submission — run finished but produced no result.
  format_failed — a result was submitted but violates result_schema / the enum.
  passed/failed — the verifier ran out of band and returned a verdict.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from gym import outcomes, toolcalls
from gym.schema import Task, is_fix_family, validate_result

# The one status spelling that is a clean run when the host supplies no verdict
# of its own. A host's producer owns its status vocabulary and its reader owns
# the interpretation, so a host passes that reader's answer as
# ``Trajectory.launch_succeeded``. Without one, only this exact spelling is
# success; ``completed``, ``complete`` and ``done`` are unknown, never success.
SUCCEEDED_STATUS = "succeeded"

_VERIFIER_TIMEOUT_SECONDS = 120


#: Sandbox name -> gym module file of the stdlib verifier support bundle.
SUPPORT_SOURCES: dict[str, str] = {
    "gym_verifier.py": "verifier_runtime.py",
    "gym_commands.py": "harness_toolcalls.py",
    "gym_doc_checks.py": "doc_checks.py",
    "gym_review_checks.py": "review_checks.py",
}


def clean_completion(trajectory: Trajectory) -> bool:
    """Whether the run ended in launch success, by the host's verdict or exact spelling."""
    launch_succeeded = getattr(trajectory, "launch_succeeded", None)
    if type(launch_succeeded) is bool:
        return launch_succeeded
    return getattr(trajectory, "terminal_status", None) == SUCCEEDED_STATUS


def snapshot_support_files(source_dir: Path) -> dict[str, bytes]:
    """Capture the complete stdlib support bundle as immutable bytes."""
    return {name: (source_dir / source).read_bytes() for name, source in SUPPORT_SOURCES.items()}


# Capture at import, alongside this grader's code, before any worker can run.
_SUPPORT_FILES = snapshot_support_files(Path(__file__).parent)
_VERIFIER_FILES: ContextVar[dict[Path, bytes | Exception] | None] = ContextVar(
    "gym_verifier_files", default=None
)


def snapshot_verifiers(tasks: list[Task]) -> dict[Path, bytes | Exception]:
    """Read task code once before dispatch; retain read failures per cell."""
    files: dict[Path, bytes | Exception] = {}
    for task in tasks:
        path = task.verifier_path
        if path is not None:
            try:
                files[path] = path.read_bytes()
            except OSError as exc:
                files[path] = exc
        if task.task_dir is not None:
            if is_fix_family(task.suite):
                for original in (task.task_dir / "fixture").rglob("*"):
                    if original.is_file() and not original.is_symlink():
                        try:
                            files[original] = original.read_bytes()
                        except OSError as exc:
                            files[original] = exc
            hidden = task.task_dir / "hidden"
            if hidden.is_symlink():
                raise GraderError(f"task {task.id}: hidden symlink refused")
            if hidden.exists():
                for child in hidden.rglob("*"):
                    if child.is_symlink():
                        raise GraderError(f"task {task.id}: hidden symlink refused")
                    if child.is_file():
                        try:
                            files[child] = child.read_bytes()
                        except OSError as exc:
                            files[child] = exc
    return files


#: Public task inputs (GYMSKEW-02): task dir -> relative path -> (mode, snapshot path,
#: or None for a directory). Dispatch stages the prompt and stage scripts from
#: here, so a deploy mid-run cannot hand a later cell a newer task copy.
TaskInputs = dict[str, tuple[int, Path | None]]
_TASK_INPUTS: ContextVar[dict[Path, TaskInputs | Exception] | None] = ContextVar(
    "gym_task_inputs", default=None
)


def snapshot_task_inputs(
    tasks: list[Task], snapshot_dir: Path
) -> dict[Path, TaskInputs | Exception]:
    """Stream public files to a caller-owned directory; private files stay out.

    A symlink or read failure is kept per task, so it fails only that cell's
    staging, exactly as the live copy would have. The directory must live until
    the run finishes; only paths and modes are retained in heap memory.
    """
    snapshot: dict[Path, TaskInputs | Exception] = {}
    for task in tasks:
        root = task.task_dir
        if root is None or root in snapshot:
            continue
        entries: TaskInputs = {}
        task_snapshot = snapshot_dir / str(len(snapshot))
        try:
            task_snapshot.mkdir(parents=True)
            for path in sorted(root.rglob("*")):
                relative = path.relative_to(root)
                if {"hidden", "expected"}.intersection(relative.parts):
                    continue
                if path.is_symlink():
                    raise RuntimeError(f"stage source symlink refused: {path}")
                mode = stat.S_IMODE(path.stat().st_mode)
                copy = None
                if not path.is_dir():
                    copy = task_snapshot / relative
                    copy.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, copy)
                entries[relative.as_posix()] = (mode, copy)
        except (OSError, RuntimeError) as exc:
            snapshot[root] = exc
        else:
            snapshot[root] = entries
    return snapshot


@contextmanager
def pinned_run_files(tasks: list[Task]):
    """Pin verifier code and public task inputs for the duration of a run.

    An enclosing pin (``hq gym matrix`` pins at start, then grades each cell
    through ``run_suite``) is reused when it already covers every task. Any other
    case takes a fresh snapshot, so GYMSKEW-01's per-run verifier snapshot holds.
    """
    verifiers, inputs = _VERIFIER_FILES.get(), _TASK_INPUTS.get()
    covered = (
        verifiers is not None
        and inputs is not None
        and all(
            (task.verifier_path is None or task.verifier_path in verifiers)
            and (task.task_dir is None or task.task_dir in inputs)
            for task in tasks
        )
    )
    if covered:
        yield
        return
    verifier_token = _VERIFIER_FILES.set(snapshot_verifiers(tasks))
    try:
        with tempfile.TemporaryDirectory(prefix="gym-inputs-") as temporary:
            inputs_token = _TASK_INPUTS.set(snapshot_task_inputs(tasks, Path(temporary)))
            try:
                yield
            finally:
                _TASK_INPUTS.reset(inputs_token)
    finally:
        _VERIFIER_FILES.reset(verifier_token)


class GraderError(RuntimeError):
    """A harness failure (not a cell outcome): the verifier itself broke."""


@dataclass
class Trajectory:
    """A run reduced to what the out-of-band grader needs.

    Built by a :class:`TrajectorySource`. Tests construct it directly (a
    synthetic trajectory); the real source fills it from ``hq dispatch`` + the
    session ledger (see :mod:`gym.dispatch` / :mod:`gym.ledger`).
    """

    terminal_status: str
    submitted_result: dict[str, Any] | None = None
    transcript_rows: list[Any] = field(default_factory=list)
    control_rows: list[Any] = field(default_factory=list)
    runtime_session_id: str | None = None
    launch_request_id: str | None = None
    resolved_reasoning_level: str | None = None
    #: MEG-02 ledger metrics for the launch (:func:`gym.metrics.launch_metrics`).
    metrics: dict[str, Any] | None = None
    error: str | None = None
    #: Tool calls recovered from the harness's own artifacts (codex exec events,
    #: the acpx session stream). Used only when the transcript rows carry none:
    #: only claude-code ingests tool calls into the ledger.
    harness_calls: list[dict[str, Any]] = field(default_factory=list)
    sandbox_root: Path | None = None
    infra_attempts: list[dict[str, Any]] = field(default_factory=list)
    provider_retry_safe: bool = False
    execution_provenance: dict[str, Any] | None = None
    # Closure retains credentials privately; they never become dataclass data.
    result_redactor: Callable[[Any], Any] | None = field(default=None, repr=False)
    #: The host's launch-success verdict, read through its producer's status
    #: reader. An explicit boolean wins over ``terminal_status`` (false included);
    #: ``None`` falls back to the exact ``succeeded`` spelling. Success of the
    #: launch only: the grade and any published deliverable are separate facts.
    launch_succeeded: bool | None = None


@dataclass
class Cell:
    """The graded result of one task run."""

    task_id: str
    outcome: str
    reason: str | None = None
    #: The graded task's ``task_version``; results compare only within one.
    task_version: int | None = None
    tool_calls: dict[str, Any] = field(default_factory=lambda: {"calls": [], "controls": []})
    runtime_session_id: str | None = None
    launch_request_id: str | None = None
    reasoning_level: str | None = None
    resolved_reasoning_level: str | None = None
    level_mismatch: bool = False
    #: MEG-02: tokens, attempts, wall time and observed models from the ledger.
    metrics: dict[str, Any] | None = None
    #: The requested model did not carry the majority of output tokens, or the
    #: launch ran as another worker class (:func:`gym.metrics.apply_requested`).
    model_mismatch: bool = False
    #: MEG-03: ``cost_*`` and ``quota_*`` fields from :func:`gym.cost.cell_economics`.
    economics: dict[str, Any] | None = None
    compression: str | None = None
    attempt: int | None = None
    #: The trajectory's supervision status (e.g. ``timeout``), so a consumer can
    #: tell a poll deadline from an agent failure that shares ``agent_error``.
    terminal_status: str | None = None

    infra_attempts: list[dict[str, Any]] = field(default_factory=list)
    subchecks: list[dict[str, Any]] = field(default_factory=list)
    integrity_flags: list[str] = field(default_factory=list)
    fix_audit: dict[str, Any] | None = None
    held_verdict: dict[str, Any] | None = None
    score: float | None = None

    execution_provenance: dict[str, Any] | None = None
    result_redactor: Callable[[Any], Any] | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "task": self.task_id,
            "task_version": self.task_version,
            "subchecks": self.subchecks,
            "score": self.score,
            "verdict": self.outcome,
            "reason": self.reason,
            "runtimeSessionId": self.runtime_session_id,
            "launchRequestId": self.launch_request_id,
            "compression": self.compression,
            "attempt": self.attempt,
            "terminalStatus": self.terminal_status,
            "toolCalls": self.tool_calls,
        }
        if self.fix_audit is not None:
            result["fix_audit"] = self.fix_audit
        if self.integrity_flags:
            result["integrity_flags"] = self.integrity_flags
            result["held_verdict"] = self.held_verdict
        if self.infra_attempts:
            result["infra_attempts"] = self.infra_attempts
        if self.reasoning_level is not None:
            result["reasoning_level"] = self.reasoning_level
            result["level_mismatch"] = self.level_mismatch
        if self.resolved_reasoning_level is not None:
            result["resolved_reasoning_level"] = self.resolved_reasoning_level
        if self.metrics is not None:
            result["metrics"] = self.metrics
            result["model_mismatch"] = self.model_mismatch
        if self.economics is not None:
            result.update(economics_view(self.economics))
        if self.execution_provenance is not None:
            result["execution_provenance"] = self.execution_provenance
        return self.result_redactor(result) if self.result_redactor else result


def economics_view(economics: dict[str, Any]) -> dict[str, Any]:
    """A cell's headline cost and quota fields, with the detail under ``economics``."""
    return {
        "cost_usd": economics.get("cost_usd"),
        "cost_status": economics.get("cost_status"),
        "quota_share": economics.get("quota_share"),
        "quota_status": economics.get("quota_status"),
        "economics": economics,
        **(
            {"cost_basis": "gpu_hours", "endpoint_lease": economics.get("endpoint_lease")}
            if economics.get("cost_basis") == "gpu_hours"
            else {}
        ),
    }


class TrajectorySource(Protocol):
    """Seam between the grader and the worker pool / ledger.

    The real implementation dispatches through ``hq dispatch`` and polls the
    session ledger; tests pass a fake that returns a canned Trajectory, so the
    grader is exercised with no real worker dispatched.
    """

    def run(self, task: Task) -> Trajectory:  # pragma: no cover - protocol
        ...


@dataclass
class _Verdict:
    outcome: str
    integrity_flags: list[str] = field(default_factory=list, kw_only=True)
    reason: str | None = None
    subchecks: list[dict[str, Any]] = field(default_factory=list)


def capture_workspace(task: Task, root: Path | None, destination: Path) -> dict[str, str]:
    """Copy bounded regular files using descriptor-relative, no-follow reads.

    Missing inputs stay in the manifest; unsafe inputs are harness errors.
    No captured bytes are executed.
    """
    manifest: dict[str, str] = {}
    destination.mkdir(parents=True, exist_ok=True)
    for name in task.capture_files:
        parts = Path(name).parts
        if not parts or Path(name).is_absolute() or ".." in parts:
            raise GraderError(f"unsafe capture path: {name}")
        descriptors = []
        try:
            if root is None:
                manifest[name] = "missing"
                continue
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(fd)
            for part in parts[:-1]:
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                descriptors.append(fd)
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            descriptors.append(fd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 256 * 1024:
                raise GraderError(f"capture refused non-regular or oversize file: {name}")
            chunks = []
            remaining = 256 * 1024 + 1
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            if len(content) > 256 * 1024:
                raise GraderError(f"capture refused oversize file: {name}")
            target = destination.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            manifest[name] = "captured"
        except FileNotFoundError:
            manifest[name] = "missing"
        except OSError as exc:
            raise GraderError(f"capture refused {name}: {exc}") from exc
        finally:
            for fd in reversed(descriptors):
                os.close(fd)
    return manifest


def _run_verifier_out_of_band(
    task: Task,
    result: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    python_exe: str,
    sandbox_root: Path | None = None,
    audit_data: dict | None = None,
) -> _Verdict:
    """Run the task's verifier in a scrubbed ephemeral sandbox.

    ``BENCH_RESULT``, ``BENCH_TOOLCALLS`` and declared workspace files enter the
    sandbox; the environment is a minimal whitelist so nothing points back at
    ``expected/`` or the ledger.
    """
    verifier_src = task.verifier_path
    files = _VERIFIER_FILES.get()
    if verifier_src is None:
        raise GraderError(f"task {task.id}: verifier not found")
    try:
        verifier_code = files[verifier_src] if files is not None else verifier_src.read_bytes()
    except OSError as exc:
        raise GraderError(f"task {task.id}: verifier unavailable: {exc}") from exc
    if isinstance(verifier_code, Exception):
        raise GraderError(f"task {task.id}: verifier unavailable: {verifier_code}")

    flags = []
    sandbox = Path(tempfile.mkdtemp(prefix=f"gym-verify-{task.id}-"))
    try:
        result_path = sandbox / "bench_result.json"
        toolcalls_path = sandbox / "bench_toolcalls.json"
        result_path.write_text(json.dumps(result), encoding="utf-8")
        toolcalls_path.write_text(json.dumps(snapshot), encoding="utf-8")

        # Copy the verifier in so its __file__ points at the sandbox, not the
        # task dir — a verifier that reaches for os.path.dirname(__file__)/expected
        # lands in the sandbox (which has no expected/) instead.
        (sandbox / "verifier.py").write_bytes(verifier_code)
        for name, code in _SUPPORT_FILES.items():
            (sandbox / name).write_bytes(code)

        manifest = capture_workspace(task, sandbox_root, sandbox / "workspace")
        if audit_data is not None:
            from gym.fix_audit import submission

            audit_data.update(submission(task, sandbox / "workspace", files))
        hidden_result = None
        regression_result = None
        if task.hidden_tests_entrypoint is not None:
            from gym.hidden_tests import run, shared_work_root

            hidden_result = run(
                task,
                sandbox / "workspace",
                files,
                work_root=shared_work_root(),
                audit_cases=audit_data["cases"] if audit_data is not None else None,
            )
            if audit_data is not None:
                audit_data["cases"] = hidden_result["audit_cases"]
            from gym.integrity import scan, scan_regression_test

            flags = scan(
                "\n".join(
                    (sandbox / "workspace" / name).read_bytes().decode("utf-8", errors="replace")
                    for name, status in manifest.items()
                    if status == "captured"
                )
            )
            if task.regression_test_path is not None:
                from gym.hidden_tests import run_regression_test

                regression_result = run_regression_test(
                    task,
                    sandbox / "workspace",
                    files,
                    work_root=shared_work_root(),
                    hidden_passed=hidden_result["status"] == "passed",
                )
                if audit_data is not None:
                    audit_data["regression_runs"] = _audit_regression_runs(regression_result)
                if manifest.get(task.regression_test_path) == "captured":
                    test_text = (sandbox / "workspace" / task.regression_test_path).read_bytes()
                    flags += scan_regression_test(test_text.decode("utf-8", errors="replace"))

        workspace_manifest = sandbox / "workspace_manifest.json"
        workspace_manifest.write_text(json.dumps(manifest), encoding="utf-8")

        env = {
            "BENCH_WORKSPACE": str(sandbox / "workspace"),
            "BENCH_WORKSPACE_MANIFEST": str(workspace_manifest),
            "BENCH_RESULT": str(result_path),
            "BENCH_TOOLCALLS": str(toolcalls_path),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONPATH": str(sandbox),
            "HOME": str(sandbox),
            "TMPDIR": str(sandbox),
            # No PYTHONHASHSEED randomness leak, no ledger DSN, no repo root.
            "PYTHONDONTWRITEBYTECODE": "1",
        }

        if hidden_result is not None:
            hidden_path = sandbox / "hidden_tests.json"
            hidden_path.write_text(json.dumps(hidden_result), encoding="utf-8")
            env["BENCH_HIDDEN_TESTS"] = str(hidden_path)
        if regression_result is not None:
            regression_path = sandbox / "regression_test.json"
            regression_path.write_text(json.dumps(regression_result), encoding="utf-8")
            env["BENCH_REGRESSION_TEST"] = str(regression_path)

        try:
            # -S blocks site-packages so the backend (session_ledger, cwp_dispatch)
            # is genuinely unimportable from the verifier — not merely DSN-scrubbed
            # — while the sandbox's own dir stays on sys.path so `gym_verifier`
            # still imports. (-I would also drop the script dir, breaking that.)
            proc = subprocess.run(
                [python_exe, "-S", "verifier.py"],
                cwd=str(sandbox),
                env=env,
                capture_output=True,
                text=True,
                timeout=_VERIFIER_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise GraderError(f"task {task.id}: verifier timed out") from exc

        verdict = _parse_verdict(
            task, proc.stdout, proc.stderr, proc.returncode, integrity_flags=flags
        )
        if regression_result is not None:
            verdict = _with_regression_subchecks(task, verdict, regression_result)
        return verdict
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def _audit_regression_runs(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Outcomes and counts only; test output never enters the envelope."""
    return [
        {key: run[key] for key in ("variant", "outcome", "counts", "elapsed_seconds")}
        for run in result["runs"]
    ]


def _with_regression_subchecks(
    task: Task, verdict: _Verdict, regression_result: dict[str, Any]
) -> _Verdict:
    """Append the grader-owned required regression subchecks."""
    from gym.hidden_tests import REGRESSION_SUBCHECKS, regression_subchecks

    owned = set(REGRESSION_SUBCHECKS.values()) | {
        f"regression_test_fails_on_{name}" for name in task.regression_test_must_fail_variants
    }
    if any(check["name"] in owned for check in verdict.subchecks):
        raise GraderError(f"task {task.id}: verifier emitted a grader-owned regression subcheck")
    extra = regression_subchecks(regression_result)
    failed = [check["reason"] for check in extra if not check["ok"]]
    outcome, reason = verdict.outcome, verdict.reason
    if outcome == outcomes.PASSED and failed:
        outcome, reason = outcomes.FAILED, "; ".join(failed)
    return _Verdict(
        outcome, reason, verdict.subchecks + extra, integrity_flags=verdict.integrity_flags
    )


def _parse_verdict(
    task: Task,
    stdout: str,
    stderr: str,
    returncode: int,
    *,
    integrity_flags: list[str] | None = None,
) -> _Verdict:
    flags = integrity_flags or []
    verdict_line: dict[str, Any] | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "verdict" in candidate:
            verdict_line = candidate
    if verdict_line is None:
        diagnostic = (stderr or "").strip() or "<no output>"
        raise GraderError(
            f"task {task.id}: verifier emitted no verdict (exit {returncode}): {diagnostic}"
        )
    verdict = verdict_line.get("verdict")
    reason = verdict_line.get("reason")
    subchecks = verdict_line.get("subchecks", [])
    if not isinstance(subchecks, list) or any(
        not isinstance(c, dict)
        or not isinstance(c.get("name"), str)
        or not c["name"]
        or type(c.get("ok")) is not bool
        or type(c.get("required")) is not bool
        or (c.get("reason") is not None and not isinstance(c["reason"], str))
        for c in subchecks
    ):
        raise GraderError(f"task {task.id}: verifier returned invalid subchecks")
    failed_required = [c for c in subchecks if c["required"] and not c["ok"]]
    if verdict == outcomes.PASSED and not failed_required:
        return _Verdict(outcomes.PASSED, reason, subchecks, integrity_flags=flags)
    if verdict in (outcomes.PASSED, outcomes.FAILED):
        return _Verdict(
            outcomes.FAILED, reason or "verifier failed", subchecks, integrity_flags=flags
        )
    raise GraderError(f"task {task.id}: verifier returned unknown verdict {verdict!r}")


def _grade(
    task: Task,
    trajectory: Trajectory,
    *,
    python_exe: str | None = None,
    audit_data: dict | None = None,
) -> Cell:
    """Resolve exactly one outcome for a task run."""
    python_exe = python_exe or sys.executable
    snapshot = toolcalls.build_snapshot(
        trajectory.transcript_rows,
        trajectory.control_rows,
        fallback_calls=trajectory.harness_calls,
    )
    terminal_status = getattr(trajectory, "terminal_status", None)
    base = {
        "task_id": task.id,
        "task_version": task.task_version,
        "tool_calls": snapshot,
        "runtime_session_id": trajectory.runtime_session_id,
        "launch_request_id": trajectory.launch_request_id,
        "resolved_reasoning_level": trajectory.resolved_reasoning_level,
        "metrics": trajectory.metrics,
        "terminal_status": terminal_status,
        "infra_attempts": trajectory.infra_attempts,
    }

    # 1. agent_error — the run never produced a clean terminal state AND the
    #    agent left nothing to grade.
    if trajectory.error and trajectory.submitted_result is None:
        return Cell(outcome=outcomes.AGENT_ERROR, reason=trajectory.error, **base)
    if not clean_completion(trajectory):
        # An unclean run with a submission is not an unanswered task. The
        # terminal status describes SUPERVISION, not the agent's work, and the
        # two fail independently: the submission is written by the agent as its
        # final act, so a status assigned afterwards cannot un-answer it.
        #
        # This discarded a whole suite. All three coding cells were killed by
        # `launch_prompt_dispatch_failed` ("failed to acquire recovery lease"),
        # an infrastructure flake unrelated to the task, and all three had
        # already written the correct answer:
        #
        #   pipefail  {"defect_line": 7, "shell_option": "pipefail",
        #              "suppressed_by_2devnull": false}
        #   commit    {"defect_kind": "missing-commit", "insert_line": 5,
        #              "teardown_line": 30}
        #   jsonb     {"defect_line": 8, "defect_kind": "type-mismatch",
        #              "offending_column": "metadata_json"}
        #
        # The suite scored 0 passed. Grading infrastructure noise as model
        # failure is the worst error this harness can make: it is exactly the
        # signal the gym exists to produce, inverted.
        #
        # The gate is not removed, only ordered after the submission check. A
        # truncated write fails to parse and a wrong shape fails result_schema,
        # so an agent killed mid-answer still cannot pass.
        if trajectory.submitted_result is None:
            reason = (
                "host reported launch_succeeded=False"
                if getattr(trajectory, "launch_succeeded", None) is False
                else f"run terminal status {terminal_status!r} is not a clean completion"
            )
            return Cell(
                outcome=outcomes.AGENT_ERROR,
                reason=f"{reason} and no result was submitted",
                **base,
            )

    # 2. no_submission — clean run, but the agent submitted nothing to grade.
    if trajectory.submitted_result is None:
        return Cell(outcome=outcomes.NO_SUBMISSION, reason="no result submitted", **base)

    # 3. format_failed — a result exists but violates result_schema / the enum.
    format_reason = validate_result(task, trajectory.submitted_result)
    if format_reason is not None:
        return Cell(outcome=outcomes.FORMAT_FAILED, reason=format_reason, **base)

    # 4. passed / failed — verifier decides, out of band.
    verdict = _run_verifier_out_of_band(
        task,
        trajectory.submitted_result,
        snapshot,
        python_exe=python_exe,
        sandbox_root=trajectory.sandbox_root,
        audit_data=audit_data,
    )
    flags = verdict.integrity_flags
    if is_fix_family(task.suite):
        from gym.integrity import scan_records

        flags = list(
            dict.fromkeys(
                flags
                + scan_records(
                    [trajectory.transcript_rows, trajectory.control_rows, trajectory.harness_calls]
                )
            )
        )
    return Cell(
        integrity_flags=flags,
        held_verdict={
            "verdict": verdict.outcome,
            "reason": verdict.reason,
            "subchecks": verdict.subchecks,
            "score": sum(c["ok"] for c in verdict.subchecks) / len(verdict.subchecks)
            if verdict.subchecks
            else None,
        }
        if flags
        else None,
        outcome=outcomes.INTEGRITY_HOLD if flags else verdict.outcome,
        reason=verdict.reason,
        subchecks=verdict.subchecks,
        score=(
            sum(c["ok"] for c in verdict.subchecks) / len(verdict.subchecks)
            if verdict.subchecks
            else None
        ),
        **base,
    )


def _grade_with_audit(task: Task, trajectory: Trajectory, *, python_exe: str | None = None) -> Cell:
    if not is_fix_family(task.suite):
        return _grade(task, trajectory, python_exe=python_exe)
    from gym import fix_audit

    fix_audit.recipient()
    audit_data = {
        "version": 1,
        "task": task.id,
        "cases": [
            {
                "index": i,
                "label": f"case-{i}",
                "outcome": "error",
                "elapsed_seconds": 0,
                "executed": False,
            }
            for i in range(task.hidden_tests_expected_tests or 0)
        ],
    }
    if task.regression_test_path is not None:
        from gym.hidden_tests import REGRESSION_VARIANTS

        audit_data["regression_runs"] = [
            {"variant": variant, "outcome": "not_run", "counts": None, "elapsed_seconds": 0}
            for variant in (*REGRESSION_VARIANTS, *task.regression_test_must_fail_variants)
        ]
    try:
        cell = _grade(task, trajectory, python_exe=python_exe, audit_data=audit_data)
    except Exception as exc:
        audit_data.update(verdict="error", incomplete=True)
        exc.fix_audit = fix_audit.seal(audit_data)
        raise
    if "files" not in audit_data:
        with tempfile.TemporaryDirectory(prefix="gym-audit-") as temporary:
            capture_workspace(task, trajectory.sandbox_root, Path(temporary))
            audit_data.update(fix_audit.submission(task, Path(temporary), _VERIFIER_FILES.get()))
    audit_data.update(
        integrity_flags=cell.integrity_flags,
        held_verdict=cell.held_verdict,
        launch_request_id=cell.launch_request_id,
        verdict=cell.outcome,
    )
    cell.fix_audit = fix_audit.seal(audit_data)
    return cell


def grade(task: Task, trajectory: Trajectory, *, python_exe: str | None = None) -> Cell:
    """Redact diagnostics only after verifiers have read the original workspace."""
    clean = trajectory.result_redactor
    try:
        cell = _grade_with_audit(task, trajectory, python_exe=python_exe)
    except Exception as exc:
        if clean:
            exc.args = tuple(clean(str(arg)) for arg in exc.args)
            if hasattr(exc, "fix_audit"):
                exc.fix_audit = clean(exc.fix_audit)
        raise
    cell.execution_provenance = trajectory.execution_provenance
    cell.result_redactor = clean
    if clean:
        for name in cell.__dataclass_fields__:
            if name != "result_redactor":
                setattr(cell, name, clean(getattr(cell, name)))
    return cell


def grade_task(
    task: Task,
    source: TrajectorySource,
    *,
    python_exe: str | None = None,
) -> Cell:
    """Drive ``source`` to produce a trajectory, then grade it.

    This is the seam the fake-dispatch unit test uses: pass a fake source and no
    real worker is dispatched.
    """
    if is_fix_family(task.suite):
        from gym.fix_audit import seal

        seal({"preflight": True})
    trajectory = source.run(task)
    return grade(task, trajectory, python_exe=python_exe)


__all__ = [
    "Trajectory",
    "Cell",
    "TrajectorySource",
    "GraderError",
    "grade",
    "grade_task",
]
