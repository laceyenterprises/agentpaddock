"""Every task must explain its submission channel and schema (TOCTASK-01)."""

import json
import re
from pathlib import Path

import pytest

from gym import grader, outcomes
from gym.grader import Trajectory
from gym.schema import load_task, validate_result

TASKS = Path(__file__).resolve().parents[1] / "tasks"
# Task directory -> reason for using a different submission channel.
# No current task has an alternative channel.
ALTERNATIVE_CHANNELS: dict[str, str] = {}


def _check_prompt(task, prompt):
    assert "bench-result.json" in prompt, "missing submission channel"
    blocks = re.findall(r"```json\s*\n(.*?)```", prompt, re.DOTALL)
    assert blocks, "missing JSON shape"
    # House templates use both quoted and bare <...> placeholders. Preserve
    # JSON strings and quote only bare placeholders before parsing the object.
    template = re.sub(
        r'"(?:\\.|[^"\\])*"|<[^>]*>',
        lambda match: json.dumps(match[0]) if match[0].startswith("<") else match[0],
        blocks[0],
    )
    result = json.loads(template)
    assert isinstance(result, dict), "JSON shape must be an object"
    for spec in task.result_schema:
        if spec.name not in result:
            continue  # validate_result reports missing required fields.
        value = result[spec.name]
        if spec.type == "enum":
            assert all(str(choice) in prompt for choice in spec.enum), (
                f"{spec.name}: missing enum choices"
            )
            assert isinstance(value, str), f"{spec.name}: enum shape must be a string"
            # Choices may be inline in the template or in a closed-set list below.
            assert value in spec.enum or "|" in value or "one of" in value, (
                f"{spec.name}: invalid enum shape"
            )
            if "|" in value:
                choices = value.strip("<>").removeprefix("one of:").strip().split("|")
                assert {choice.strip() for choice in choices} == set(spec.enum), (
                    f"{spec.name}: inconsistent enum shape"
                )
            result[spec.name] = spec.enum[0]
        elif isinstance(value, str) and value.startswith("<") and value.endswith(">"):
            if spec.type == "integer":
                assert "integer" in value or "1-indexed" in value, (
                    f"{spec.name}: wrong integer placeholder"
                )
                result[spec.name] = 1
            elif spec.type == "boolean":
                assert value == "<true|false>", f"{spec.name}: wrong boolean placeholder"
                result[spec.name] = True
    reason = validate_result(task, result)
    assert reason is None, reason


@pytest.mark.parametrize(
    "prompt_path", sorted(TASKS.glob("*/prompt.md")), ids=lambda p: p.parent.name
)
def test_task_prompt_documents_submission(prompt_path):
    reason = ALTERNATIVE_CHANNELS.get(prompt_path.parent.name)
    if reason is not None:
        assert reason.strip(), "alternative channel needs an explicit reason"
        return
    task = load_task(prompt_path.parent)
    _check_prompt(task, prompt_path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "prompt, message",
    [
        ('```json\n{"status": "fixed", "regressing_commit_subject": "<string>"}\n```', "channel"),
        ("bench-result.json", "JSON shape"),
        ('bench-result.json fixed failed\n```json\n{"status": "fixed"}\n```', "missing required"),
        (
            'bench-result.json fixed failed\n```json\n{"status": "fixed", "regressing_commit_subject": 1}\n```',
            "must be a string",
        ),
        (
            'bench-result.json\n```json\n{"status": "fixed", "regressing_commit_subject": "<string>"}\n```',
            "enum choices",
        ),
        (
            'bench-result.json fixed failed\n```json\n{"status": "other", "regressing_commit_subject": "<string>"}\n```',
            "invalid enum",
        ),
        (
            'bench-result.json fixed failed\n```json\n{"status": "fixed | other", "regressing_commit_subject": "<string>"}\n```',
            "inconsistent enum",
        ),
        (
            'bench-result.json fixed failed\n```json\n{"status": "fixed", "regressing_commit_subject": "<string>", "extra": true}\n```',
            "unknown field",
        ),
    ],
)
def test_guard_rejects_incomplete_submission_instructions(prompt, message):
    with pytest.raises(AssertionError, match=message):
        _check_prompt(load_task(TASKS / "coding-toc-commands"), prompt)


def test_toc_submission_file_grades_pass():
    trajectory = Trajectory(
        terminal_status="succeeded",
        submitted_result={
            "status": "fixed",
            "regressing_commit_subject": "greet: reuse the farewell banner string",
        },
        harness_calls=[
            {"name": "shell", "arguments": {"command": command}}
            for command in ("git log", "pytest test.py", "pytest test.py")
        ],
    )
    cell = grader.grade(load_task(TASKS / "coding-toc-commands"), trajectory)
    assert cell.outcome == outcomes.PASSED, cell.reason
