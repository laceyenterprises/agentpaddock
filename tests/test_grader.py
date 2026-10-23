"""Grader: fake-dispatch pass/fail, one outcome per cell, verifier isolation.

No real worker is dispatched — a FakeSource returns a synthetic trajectory, so
the grader's seam is exercised end to end against the shipped example task.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import FIXTURES_DIR, TASKS_DIR

from gym import outcomes
from gym.grader import (
    Cell,
    GraderError,
    Trajectory,
    _parse_verdict,
    clean_completion,
    grade,
    grade_task,
)
from gym.schema import load_task


class FakeSource:
    """Fake dispatch/ledger seam: hands back a canned trajectory."""

    def __init__(self, trajectory: Trajectory):
        self._trajectory = trajectory
        self.calls = 0

    def run(self, task):
        self.calls += 1
        return self._trajectory


def _tool_row(ordinal, name):
    return {
        "ordinal": ordinal,
        "event_type": "tool_call",
        "tool_metadata_json": json.dumps(
            {"tool_calls": [{"id": f"c{ordinal}", "name": name, "arguments": {}}]}
        ),
    }


def _read_then_write_rows():
    return [_tool_row(1, "read"), _tool_row(2, "write")]


def _write_then_read_rows():
    return [_tool_row(1, "write"), _tool_row(2, "read")]


@pytest.fixture
def example_task():
    return load_task(TASKS_DIR / "example-read-before-write")


def test_fake_dispatch_grades_passed(example_task):
    source = FakeSource(
        Trajectory(
            terminal_status="succeeded",
            submitted_result={"root_cause": "read-before-write", "evidence_id": "run-42"},
            transcript_rows=_read_then_write_rows(),
            runtime_session_id="sess-1",
        )
    )
    cell = grade_task(example_task, source)
    assert source.calls == 1
    assert isinstance(cell, Cell)
    assert cell.outcome == outcomes.PASSED
    assert cell.runtime_session_id == "sess-1"
    # The snapshot carried the ordered tool names to the verifier.
    assert [c["name"] for c in cell.tool_calls["calls"]] == ["read", "write"]


def test_fake_dispatch_grades_failed_on_behavior(example_task):
    # Correctly formatted result, but the agent wrote before reading -> verifier fail.
    source = FakeSource(
        Trajectory(
            terminal_status="succeeded",
            submitted_result={"root_cause": "read-before-write", "evidence_id": "run-42"},
            transcript_rows=_write_then_read_rows(),
        )
    )
    cell = grade_task(example_task, source)
    assert cell.outcome == outcomes.FAILED
    assert "before reading" in (cell.reason or "")


def test_fake_dispatch_grades_failed_on_wrong_evidence(example_task):
    source = FakeSource(
        Trajectory(
            terminal_status="succeeded",
            submitted_result={"root_cause": "read-before-write", "evidence_id": "wrong"},
            transcript_rows=_read_then_write_rows(),
        )
    )
    cell = grade_task(example_task, source)
    assert cell.outcome == outcomes.FAILED


def test_format_failed_on_out_of_enum(example_task):
    cell = grade(
        example_task,
        Trajectory(
            terminal_status="succeeded",
            submitted_result={"root_cause": "not-a-real-cause", "evidence_id": "run-42"},
            transcript_rows=_read_then_write_rows(),
        ),
    )
    assert cell.outcome == outcomes.FORMAT_FAILED
    assert "closed set" in (cell.reason or "")


def test_no_submission(example_task):
    cell = grade(
        example_task,
        Trajectory(terminal_status="succeeded", submitted_result=None),
    )
    assert cell.outcome == outcomes.NO_SUBMISSION


def test_agent_error_on_failed_run(example_task):
    cell = grade(
        example_task,
        Trajectory(terminal_status="failed", submitted_result=None),
    )
    assert cell.outcome == outcomes.AGENT_ERROR


# Synthetic status table. A host's producer owns the launch vocabulary; without a
# host verdict the grader recognizes only the exact success spelling.
_NOT_SUCCESS_STATUSES = [
    "requested",
    "admitted",
    "leased",
    "starting",
    "running",
    "waiting_approval",
    "waiting_input",
    "blocked_needs_decision",
    "failed",
    "operator_triage_required",
    "canceled",
    "cancelled",
    "superseded",
    "reaped_stuck_requested",
    # Legacy aliases: unknown evidence, never success.
    "completed",
    "complete",
    "done",
    "success",
    " succeeded",
    "Succeeded",
    "",
]


def test_only_exact_succeeded_is_clean_without_a_host_verdict():
    assert clean_completion(Trajectory(terminal_status="succeeded"))
    for status in _NOT_SUCCESS_STATUSES:
        assert not clean_completion(Trajectory(terminal_status=status)), status


@pytest.mark.parametrize("status", ["succeeded", *_NOT_SUCCESS_STATUSES])
def test_an_explicit_host_verdict_wins_over_the_status(status):
    assert clean_completion(Trajectory(terminal_status=status, launch_succeeded=True))
    assert not clean_completion(Trajectory(terminal_status=status, launch_succeeded=False))


@pytest.mark.parametrize("verdict", [0, 1, "true", "false"])
def test_a_non_boolean_host_verdict_is_missing(verdict):
    assert clean_completion(Trajectory(terminal_status="succeeded", launch_succeeded=verdict))
    assert not clean_completion(Trajectory(terminal_status="completed", launch_succeeded=verdict))


@pytest.mark.parametrize("status", ["completed", "complete", "done", "blocked_needs_decision"])
def test_an_unclean_status_without_submission_is_agent_error(example_task, status):
    cell = grade(example_task, Trajectory(terminal_status=status, submitted_result=None))
    assert cell.outcome == outcomes.AGENT_ERROR
    assert f"run terminal status {status!r} is not a clean completion" in cell.reason


def test_an_explicit_false_verdict_makes_succeeded_unclean(example_task):
    cell = grade(
        example_task,
        Trajectory(terminal_status="succeeded", submitted_result=None, launch_succeeded=False),
    )
    assert cell.outcome == outcomes.AGENT_ERROR


def test_agent_error_on_explicit_error(example_task):
    cell = grade(
        example_task,
        Trajectory(terminal_status="dispatch_failed", error="hq dispatch failed"),
    )
    assert cell.outcome == outcomes.AGENT_ERROR
    assert cell.reason == "hq dispatch failed"


def test_missing_verdict_preserves_multiline_stderr(example_task):
    stderr = 'Traceback (most recent call last):\n  File "verifier.py", line 7\nBoom'
    with pytest.raises(GraderError) as exc_info:
        _parse_verdict(example_task, "", stderr, 1)
    assert stderr in str(exc_info.value)


def test_each_cell_resolves_to_exactly_one_outcome(example_task):
    cases = [
        Trajectory(
            terminal_status="succeeded",
            submitted_result={"root_cause": "read-before-write", "evidence_id": "run-42"},
            transcript_rows=_read_then_write_rows(),
        ),
        Trajectory(
            terminal_status="succeeded",
            submitted_result={"root_cause": "read-before-write", "evidence_id": "run-42"},
            transcript_rows=_write_then_read_rows(),
        ),
        Trajectory(
            terminal_status="succeeded",
            submitted_result={"root_cause": "bogus", "evidence_id": "run-42"},
        ),
        Trajectory(terminal_status="succeeded", submitted_result=None),
        Trajectory(terminal_status="failed"),
    ]
    resolved = {grade(example_task, t).outcome for t in cases}
    assert resolved == {
        outcomes.PASSED,
        outcomes.FAILED,
        outcomes.FORMAT_FAILED,
        outcomes.NO_SUBMISSION,
        outcomes.AGENT_ERROR,
    }
    for t in cases:
        assert grade(example_task, t).outcome in outcomes.OUTCOMES


def test_verifier_isolation_cannot_reach_expected_or_backend():
    # The isolation-probe verifier tries hard to read expected/ and import the
    # backend; the out-of-band env must defeat every attempt. A LEAK verdict
    # would mean the sandbox was porous.
    task = load_task(FIXTURES_DIR / "isolation-probe")
    assert (task.expected_dir / "ground_truth.json").exists()  # ground truth IS on disk
    cell = grade(
        task,
        Trajectory(
            terminal_status="succeeded",
            submitted_result={"answer": "anything"},
        ),
    )
    assert cell.outcome == outcomes.FAILED
    assert (cell.reason or "").startswith("ISOLATED"), cell.reason
    assert "LEAK" not in (cell.reason or "")


@pytest.mark.parametrize(
    "terminal,expected",
    [
        ("passed()", '{"verdict": "passed"}\n'),
        ('fail("wrong")', '{"verdict": "failed", "reason": "wrong"}\n'),
    ],
)
def test_legacy_verdict_bytes(terminal, expected):
    import subprocess
    import sys
    from gym import verifier_runtime

    proc = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            f"import sys; sys.path.insert(0, {str(Path(verifier_runtime.__file__).parent)!r}); "
            f"from verifier_runtime import *; {terminal}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout == expected


@pytest.mark.parametrize(
    "required,terminal,outcome",
    [
        (True, "passed()", outcomes.FAILED),
        (False, "passed()", outcomes.PASSED),
        (False, 'fail("explicit failure")', outcomes.FAILED),
    ],
)
def test_subchecks_preserve_required_and_explicit_failure(
    example_task, tmp_path, required, terminal, outcome
):
    from dataclasses import replace

    verifier = tmp_path / "verifier.py"
    verifier.write_text(
        "import gym_verifier as gv\n"
        "gv.check('first', True)\n"
        f"gv.check('later', False, 'wrong later', required={required})\n"
        f"gv.{terminal}\n"
    )
    # Task resolves its entrypoint relative to its directory.
    task = replace(example_task, task_dir=tmp_path, verifier_entrypoint="verifier.py")
    cell = grade(
        task,
        Trajectory(
            terminal_status="succeeded",
            submitted_result={"root_cause": "read-before-write", "evidence_id": "run-42"},
        ),
    )
    assert cell.outcome == outcome
    assert cell.score == 0.5
    assert cell.subchecks[1]["reason"] == "wrong later"


def test_no_subchecks_score_is_null(example_task):
    cell = grade(
        example_task,
        Trajectory(
            terminal_status="succeeded",
            submitted_result={"root_cause": "read-before-write", "evidence_id": "run-42"},
            transcript_rows=_read_then_write_rows(),
        ),
    )
    assert cell.subchecks == []
    assert cell.score is None
    assert cell.to_dict()["score"] is None
