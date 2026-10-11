"""Stage a task's public inputs into a sandbox and read the agent's submission.

Every :class:`~gym.grader.TrajectorySource` needs the same two edges around
the agent run: the task's stage scripts must populate the sandbox before the
agent starts, and the agent's ``bench-result.json`` must be read back without
following a link or trusting its size. The fleet source (``gym.dispatch``) and
the standalone source (``gym.standalone``) share them from here, so a staging
fix lands on both runners at once (PDK-01).

Stdlib and ``gym`` only: this module ships in the standalone package.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from gym import grader
from gym.schema import Task

# Where the agent leaves its result. The staged persona is instructed to write
# its submission here inside its worktree; absent means no_submission. Corpora
# (GYM-02/03) rely on this single, documented submission channel.
RESULT_FILENAME = "bench-result.json"
FIXTURE_MANIFEST_FILENAME = ".gym-expected-inputs.json"
GUARDED_PROMPT_FILENAME = ".gym-task-prompt.md"
STAGE_SCRIPT_TIMEOUT_SECONDS = 300
MAX_RESULT_BYTES = 1024 * 1024

GYM_PROMPT_GUARD = """# Gym workspace boundary (mandatory)

All task inputs exist only in your current workspace. Do not search outside the
workspace for inputs. Reading anything under `modules/adversarial-eval-gym/tasks/`
is a task failure. If an expected input is absent, stop and report
`input_fixtures_missing`; do not search the host or repository for a substitute.
"""


class InputFixturesMissing(RuntimeError):
    """A staged gym slot lost one or more declared input fixtures."""


def workspace_files(sandbox_root: Path) -> set[str]:
    files: set[str] = set()
    for path in sandbox_root.rglob("*"):
        relative = path.relative_to(sandbox_root).as_posix()
        if path.is_symlink():
            raise InputFixturesMissing(
                f"input_fixtures_missing: staged workspace contains symlink: {relative}"
            )
        if path.is_file():
            files.add(relative)
    return files


def write_fixture_manifest(sandbox_root: Path, expected_files: set[str]) -> None:
    manifest_path = sandbox_root / FIXTURE_MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps({"version": 1, "expectedFiles": sorted(expected_files)}, indent=2) + "\n",
        encoding="utf-8",
    )


def verify_task_inputs(sandbox_root: Path) -> None:
    manifest_path = sandbox_root / FIXTURE_MANIFEST_FILENAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_files = manifest["expectedFiles"]
        if not isinstance(expected_files, list) or not all(
            isinstance(item, str) and item for item in expected_files
        ):
            raise ValueError("expectedFiles must be a list of paths")
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise InputFixturesMissing(
            f"input_fixtures_missing: fixture manifest unavailable or invalid: {manifest_path}"
        ) from exc

    missing = [
        relative
        for relative in expected_files
        if not (sandbox_root / relative).is_file() or (sandbox_root / relative).is_symlink()
    ]
    if missing:
        raise InputFixturesMissing(
            "input_fixtures_missing: staged workspace is missing expected file(s): "
            + ", ".join(sorted(missing))
        )


def pinned_task_inputs(task: Task) -> grader.TaskInputs | None:
    """The run-start copy of this task's public files, when a run pinned one."""
    snapshot = grader._TASK_INPUTS.get()
    if snapshot is None or task.task_dir is None or task.task_dir not in snapshot:
        return None
    entries = snapshot[task.task_dir]
    if isinstance(entries, Exception):
        raise RuntimeError(str(entries)) from entries
    return entries


def guarded_prompt(task: Task, sandbox_root: Path) -> Path:
    prompt = task.dispatch.get("prompt")
    body = ""
    if prompt:
        source = task.task_dir / str(prompt) if task.task_dir is not None else Path(str(prompt))
        pinned = pinned_task_inputs(task)
        if pinned is not None and str(prompt) in pinned and pinned[str(prompt)][1] is not None:
            body = pinned[str(prompt)][1].read_text(encoding="utf-8")
        else:
            body = source.resolve().read_text(encoding="utf-8")
    prompt_path = sandbox_root / GUARDED_PROMPT_FILENAME
    prompt_path.write_text(f"{GYM_PROMPT_GUARD}\n{body}", encoding="utf-8")
    return prompt_path


def materialize_task_inputs(entries: grader.TaskInputs, staging_dir: Path) -> None:
    staging_dir.mkdir(parents=True)
    for relative, (mode, source) in sorted(entries.items()):
        target = staging_dir / relative
        if source is None:
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(mode)
    # Directory modes last, deepest first, as copytree does.
    for relative, (mode, source) in sorted(entries.items(), reverse=True):
        if source is None:
            (staging_dir / relative).chmod(mode)


def run_stage_scripts(task: Task, sandbox_root: Path) -> None:
    """Run the task's stage scripts into the sandbox and write its manifest.

    Nothing in the gym executed `task.stages` before GYM staging landed. The
    schema parsed them, `Task.stages` existed, every shipped task declared a
    stage script taking `sandbox_root` as $1 -- and no code called it, so every
    cell ran against an empty sandbox. The first live dispatch proved it: the
    agent wrote `{"error": "input_fixtures_missing", "detail": "query.sql and
    schema.sql were never staged"}` and refused to fabricate an answer.

    Staging happens BEFORE the agent starts, so it cannot race the agent. The
    caller verifies the manifest afterwards (and again right before launch).
    """
    sandbox_root.mkdir(parents=True, exist_ok=True)
    before = workspace_files(sandbox_root)
    with tempfile.TemporaryDirectory(prefix="gym-stage-") as temporary:
        staging_dir = (Path(temporary) / "task").resolve()
        pinned = pinned_task_inputs(task)
        if pinned is not None:
            # GYMSKEW-02: stage the copy read at run start, not today's disk.
            materialize_task_inputs(pinned, staging_dir)
        elif task.task_dir is not None:
            # Task staging scripts are trusted corpus code. Give their relative
            # reads only public fixtures, never the grading-only directories.
            for source in task.task_dir.rglob("*"):
                if (
                    not {"hidden", "expected"}.intersection(source.relative_to(task.task_dir).parts)
                    and source.is_symlink()
                ):
                    raise RuntimeError(f"stage source symlink refused: {source}")
            shutil.copytree(
                task.task_dir, staging_dir, ignore=shutil.ignore_patterns("hidden", "expected")
            )
        for stage in task.stages:
            script = (staging_dir / stage.run).resolve() if task.task_dir else Path(stage.run)
            if task.task_dir is not None and not script.is_relative_to(staging_dir):
                raise RuntimeError(f"stage script escapes public task copy: {stage.run}")
            if not script.is_file():
                raise RuntimeError(f"stage script missing for {task.id}: {script}")
            proc = subprocess.run(
                ["bash", str(script), str(sandbox_root)],
                cwd=str(staging_dir) if task.task_dir else None,
                capture_output=True,
                text=True,
                timeout=STAGE_SCRIPT_TIMEOUT_SECONDS,
            )
            if proc.returncode != 0:
                detail = (
                    "\n".join(part for part in (proc.stderr.strip(), proc.stdout.strip()) if part)
                    or "<no output>"
                )
                raise RuntimeError(
                    f"stage {script.name} failed for {task.id} (exit {proc.returncode}): {detail}"
                )
    produced = workspace_files(sandbox_root) - before
    write_fixture_manifest(sandbox_root, produced)


def read_submitted_result(sandbox_root: Path) -> dict[str, Any] | None:
    """The agent's ``bench-result.json``, or None when absent or unsafe to read.

    The read refuses a symlink, a non-regular file, and anything over
    ``MAX_RESULT_BYTES``: the agent controls this file, the grader does not.
    """
    result_path = Path(sandbox_root) / RESULT_FILENAME
    try:
        flags = os.O_RDONLY | os.O_NONBLOCK
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(result_path, flags)
        with os.fdopen(fd, "rb") as handle:
            file_stat = os.fstat(handle.fileno())
            if not stat.S_ISREG(file_stat.st_mode):
                return None
            if file_stat.st_size > MAX_RESULT_BYTES:
                return None
            raw = handle.read(MAX_RESULT_BYTES + 1)
        if len(raw) > MAX_RESULT_BYTES:
            return None
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
