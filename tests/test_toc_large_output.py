"""Local fixture/verifier checks; never dispatch gym attempts."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from gym import grader, outcomes, toolcalls
from gym.schema import TaskSchemaError, load_task
from test_coding_corpus import _toc_trajectory

TASK = Path(__file__).resolve().parents[1] / "tasks" / "coding-toc-large-output"


@pytest.fixture
def toc_eval():
    return pytest.importorskip("gym.toc_eval", reason="host integration is not shipped")


@pytest.mark.parametrize("symlink", [False, True])
def test_attempt_snapshots_direct_and_symlinked_task_contracts(
    monkeypatch, tmp_path, symlink, toc_eval
):
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    target = tmp_path / "shared" / TASK.name if symlink else tasks / TASK.name
    shutil.copytree(TASK, target)
    if symlink:
        (tasks / TASK.name).symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(toc_eval.suite, "default_tasks_dir", lambda: tasks)
    commands = []

    def dispatch(cmd, **kwargs):
        commands.append(cmd)
        return SimpleNamespace(
            stdout=json.dumps(
                {"cells": [{"launchRequestId": "a", "verdict": "passed"}]}
            ),
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr(toc_eval.subprocess, "run", dispatch)
    [record] = toc_eval._run_attempt("codex", "m", "spill", TASK.name, 1, "low")
    assert record["requiredLargeOutputCommands"] == ["git", "pytest"]
    assert commands[0][commands[0].index("--task") + 1] == TASK.name


@pytest.mark.parametrize(
    "task", ["", ".", "..", "../shared", "nested/task", "/tmp/task"]
)
def test_attempt_rejects_non_task_names_before_read_or_dispatch(
    monkeypatch, tmp_path, task, toc_eval
):
    import gym.schema

    monkeypatch.setattr(toc_eval.suite, "default_tasks_dir", lambda: tmp_path / "tasks")
    monkeypatch.setattr(gym.schema, "load_task", lambda *_: pytest.fail("task read"))
    monkeypatch.setattr(
        toc_eval.subprocess, "run", lambda *_a, **_kw: pytest.fail("dispatched")
    )
    with pytest.raises(TaskSchemaError, match="directly under the tasks root"):
        toc_eval._run_attempt("codex", "m", "spill", task, 1, "low")


def test_large_outputs_hide_required_evidence_and_fix_preserves_tests(tmp_path):
    subprocess.run(
        ["bash", str(TASK / "stages/stage-input.sh"), str(tmp_path)],
        check=True,
        timeout=60,
    )
    tests = (tmp_path / "test.py").read_bytes()
    history = subprocess.run(
        ["git", "-c", f"safe.directory={tmp_path}", "log", "-p"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout
    failure = subprocess.run(
        [sys.executable, "-m", "pytest", "-v", "test.py"],
        cwd=tmp_path,
        capture_output=True,
        timeout=60,
    )
    assert failure.returncode == 1
    for output, needle in (
        (history, b"banner: select compact transport variant"),
        (failure.stdout, b"diagnostic marker: route-cedar-742"),
    ):
        assert len(output) > 20_000
        assert needle in output[2048:-2048]
        assert needle not in output[:2048] + output[-2048:]
    (tmp_path / "main.py").write_text(
        'def greet():\n    return "Welcome aboard, cedar crew"\n'
    )
    fixed = subprocess.run(
        [sys.executable, "-m", "pytest", "-v", "test.py"],
        cwd=tmp_path,
        capture_output=True,
        timeout=60,
    )
    assert fixed.returncode == 0
    assert len(fixed.stdout) > 20_000
    assert (tmp_path / "test.py").read_bytes() == tests


def test_reference_wrong_and_test_edit_verification():
    task = load_task(TASK)
    commands = ["git log -p", "pytest -v test.py", "pytest -v test.py"]
    for name, expected in (
        ("reference", outcomes.PASSED),
        ("known-wrong", outcomes.FAILED),
    ):
        answer = json.loads((TASK / "expected" / f"{name}.json").read_text())
        assert grader.grade(task, _toc_trajectory(answer, commands)).outcome == expected
    good = json.loads((TASK / "expected/reference.json").read_text())
    for command in ("printf x > test.py", "sed -i s/bye/hello/ test.py"):
        assert (
            grader.grade(task, _toc_trajectory(good, [*commands, command])).outcome
            == outcomes.FAILED
        )
    for field in good:
        assert (
            grader.grade(
                task,
                _toc_trajectory(
                    {**good, field: "failed" if field == "status" else "wrong"},
                    commands,
                ),
            ).outcome
            == outcomes.FAILED
        )


@pytest.mark.parametrize(
    "marker",
    [
        "route-cedar-742",
        "diagnostic marker: route-cedar-742",
        " \t\nroute-cedar-742\n\t ",
        " \t\ndiagnostic marker: route-cedar-742\n\t ",
        "DIAGNOSTIC MARKER: route-cedar-742",
        "Diagnostic Marker:   route-cedar-742",
    ],
)
def test_failure_marker_accepts_token_or_diagnostic_line(marker):
    task = load_task(TASK)
    assert task.task_version == 2
    result = {
        "status": "fixed",
        "regressing_commit_subject": "banner: select compact transport variant",
        "failure_marker": marker,
    }
    trajectory = _toc_trajectory(
        result, ["git log -p", "pytest -v test.py", "pytest -v test.py"]
    )
    assert grader.grade(task, trajectory).outcome == outcomes.PASSED


@pytest.mark.parametrize(
    "marker_fields",
    [
        {"failure_marker": "route-cedar-743"},
        {"failure_marker": "diagnostic marker: route-oak-001"},
        {"failure_marker": "marker route-cedar-742 found"},
        {"failure_marker": "route-cedar-742 (from traceback)"},
        {
            "failure_marker": "Traceback (most recent call last):\n  diagnostic marker: route-cedar-742\nAssertionError"
        },
        pytest.param({}, id="missing"),
        {"failure_marker": ""},
        {"failure_marker": " \t\n"},
        {"failure_marker": None},
        {"failure_marker": 742},
        {"failure_marker": True},
        {"failure_marker": ["route-cedar-742"]},
        {"failure_marker": {"marker": "route-cedar-742"}},
        {"failure_marker": "diagnostic marker:route-cedar-742"},
        {"failure_marker": "diagnostic marker:\nroute-cedar-742"},
        {"failure_marker": "diagnostic marker:\troute-cedar-742"},
        {"failure_marker": "diagnostic marker: ROUTE-CEDAR-742"},
    ],
)
def test_failure_marker_rejections_preserve_verifier_reason(marker_fields):
    result = {
        "status": "fixed",
        "regressing_commit_subject": "banner: select compact transport variant",
        **marker_fields,
    }
    trajectory = _toc_trajectory(
        result, ["git log -p", "pytest -v test.py", "pytest -v test.py"]
    )
    snapshot = toolcalls.build_snapshot(
        trajectory.transcript_rows,
        trajectory.control_rows,
        fallback_calls=trajectory.harness_calls,
    )
    # Exercise the verifier directly, including inputs rejected earlier by the schema.
    verdict = grader._run_verifier_out_of_band(
        load_task(TASK), result, snapshot, python_exe=sys.executable
    )
    assert verdict.outcome == outcomes.FAILED
    assert verdict.reason == "failure_marker does not match the failing traceback"


@pytest.mark.parametrize("task", [None, "coding-toc-large-output"])
def test_driver_task_selection(monkeypatch, task, toc_eval):
    monkeypatch.setattr(toc_eval, "_lock_omission_reason", lambda _: None)
    args = ["--cell", "codex=gpt-6-sol", "--arm", "off", "--arm", "spill", "--dry-run"]
    if task:
        args += ["--task", task]
    plan, omitted = toc_eval._plan(toc_eval._parse_args(args))
    assert not omitted
    assert plan == [
        ("codex", "gpt-6-sol", arm, task or "coding-toc-commands")
        for arm in ("off", "spill")
    ]


def test_ledger_counts_pass_through_as_eligible_only(monkeypatch):
    views = pytest.importorskip(
        "session_ledger.canonical_views.tool_output_compression_ab",
        reason="host integration is not shipped",
    )

    rows = [
        {
            "command_label": "git",
            "backend": "spill",
            "status": "pass-through",
            "arm_source": "launch",
            "bytes_in": 2048,
            "bytes_out": 2048,
        },
        {
            "command_label": "pytest",
            "backend": "spill",
            "status": "ok",
            "arm_source": "launch",
            "bytes_in": 12000,
            "bytes_out": 4300,
        },
    ]
    monkeypatch.setattr(views, "_rows", lambda *args: rows)
    counts = views.fetch_launch_tool_output_compression(None, "test-launch")
    assert counts["rows"] == 2
    assert counts["callsByCommand"] == {"git": 1, "pytest": 1}
    assert counts["okRows"] == 1
    assert counts["bytesIn"] == 14048
    assert counts["bytesOut"] == 6348


@pytest.mark.parametrize(
    "command",
    [
        "git log -p > history.txt",
        "git log -p | head",
        "git log --oneline",
        "pytest -v test.py > result.txt",
        "python3 -m pytest -v test.py",
        "pytest -q test.py",
        "/usr/bin/git log -p",
    ],
)
def test_measurement_commands_cannot_bypass_large_output(command):
    answer = json.loads((TASK / "expected/reference.json").read_text())
    commands = ["git log -p", "pytest -v test.py", "pytest -v test.py"]
    if "git" in command:
        commands[0] = command
    else:
        commands[1] = command
    assert (
        grader.grade(load_task(TASK), _toc_trajectory(answer, commands)).outcome
        == outcomes.FAILED
    )


@pytest.mark.parametrize("field", ["description", "title"])
@pytest.mark.parametrize(
    "text", ["Before & after", "Output > threshold", "Literal $(value) | ; < `"]
)
def test_measurement_ignores_shell_characters_in_metadata(field, text):
    answer = json.loads((TASK / "expected/reference.json").read_text())
    trajectory = _toc_trajectory(
        answer, ["git log -p", "pytest -v test.py", "pytest -v test.py"]
    )
    for call in trajectory.harness_calls:
        call["arguments"][field] = text
    assert grader.grade(load_task(TASK), trajectory).outcome == outcomes.PASSED
