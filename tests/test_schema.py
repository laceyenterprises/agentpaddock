"""Schema fixtures: strict parse, mandatory tables, loud on unknown keys."""

from __future__ import annotations

import copy

import pytest
from conftest import TASKS_DIR

from gym.schema import (
    FieldSpec,
    Task,
    TaskSchemaError,
    load_task,
    parse_task_dict,
    validate_result,
)

VALID = {
    "id": "t1",
    "suite": "rca",
    "persona": "claude-code",
    "result_schema": {
        "root_cause": {"type": "enum", "enum": ["a", "b"], "required": True},
        "evidence_id": {"type": "string"},
    },
    "verifier": {"entrypoint": "verifier.py"},
}


def test_valid_task_dict_parses():
    task = parse_task_dict(copy.deepcopy(VALID))
    assert task.id == "t1"
    assert task.suite == "rca"
    assert task.verifier_entrypoint == "verifier.py"
    names = {spec.name for spec in task.result_schema}
    assert names == {"root_cause", "evidence_id"}
    rc = task.field_spec("root_cause")
    assert rc is not None and rc.type == "enum" and rc.enum == ("a", "b")


def test_shipped_example_task_loads_from_disk():
    task = load_task(TASKS_DIR / "example-read-before-write")
    assert task.id == "example-read-before-write"
    assert task.verifier_path is not None and task.verifier_path.exists()
    assert task.field_spec("root_cause").type == "enum"


def test_missing_result_schema_rejected():
    data = copy.deepcopy(VALID)
    del data["result_schema"]
    with pytest.raises(TaskSchemaError, match="result_schema"):
        parse_task_dict(data)


def test_missing_verifier_rejected():
    data = copy.deepcopy(VALID)
    del data["verifier"]
    with pytest.raises(TaskSchemaError, match="verifier"):
        parse_task_dict(data)


def test_unknown_top_level_key_fails_loudly():
    data = copy.deepcopy(VALID)
    data["stagez"] = ["typo.sh"]  # not silently dropped
    with pytest.raises(TaskSchemaError, match="unknown key"):
        parse_task_dict(data)


def test_task_version_defaults_to_one_and_is_recorded():
    assert parse_task_dict(copy.deepcopy(VALID)).task_version == 1
    data = copy.deepcopy(VALID)
    data["task_version"] = 2
    assert parse_task_dict(data).task_version == 2


@pytest.mark.parametrize("version", [0, -1, True, "2", 1.0])
def test_task_version_must_be_a_positive_integer(version):
    data = copy.deepcopy(VALID)
    data["task_version"] = version
    with pytest.raises(TaskSchemaError, match="task_version"):
        parse_task_dict(data)


def test_pinned_model_with_an_effort_segment_rejected():
    # A pinned "provider@effort:high" would alias the leveled cohort's key.
    data = copy.deepcopy(VALID)
    data["dispatch"] = {"model": "provider@effort:high"}
    with pytest.raises(TaskSchemaError, match="dispatch.model"):
        parse_task_dict(data)


def test_unknown_field_spec_key_fails_loudly():
    data = copy.deepcopy(VALID)
    data["result_schema"]["evidence_id"]["typ"] = "string"  # typo of 'type'
    with pytest.raises(TaskSchemaError, match="unknown key"):
        parse_task_dict(data)


def test_enum_requires_values():
    data = copy.deepcopy(VALID)
    data["result_schema"]["root_cause"] = {"type": "enum"}
    with pytest.raises(TaskSchemaError, match="enum"):
        parse_task_dict(data)


def test_bad_suite_rejected():
    data = copy.deepcopy(VALID)
    data["suite"] = "all"  # 'all' is a selector, not a task membership
    with pytest.raises(TaskSchemaError, match="suite"):
        parse_task_dict(data)


def test_empty_result_schema_rejected():
    data = copy.deepcopy(VALID)
    data["result_schema"] = {}
    with pytest.raises(TaskSchemaError, match="at least one field"):
        parse_task_dict(data)


def test_validate_result_accepts_conforming():
    task = parse_task_dict(copy.deepcopy(VALID))
    assert validate_result(task, {"root_cause": "a", "evidence_id": "e1"}) is None


def test_validate_result_rejects_out_of_enum():
    task = parse_task_dict(copy.deepcopy(VALID))
    reason = validate_result(task, {"root_cause": "z", "evidence_id": "e1"})
    assert reason is not None and "closed set" in reason


def test_validate_result_rejects_unknown_field():
    task = parse_task_dict(copy.deepcopy(VALID))
    reason = validate_result(task, {"root_cause": "a", "evidence_id": "e1", "extra": 1})
    assert reason is not None and "unknown field" in reason


def test_validate_result_rejects_missing_required():
    task = parse_task_dict(copy.deepcopy(VALID))
    reason = validate_result(task, {"evidence_id": "e1"})
    assert reason is not None and "root_cause" in reason


@pytest.mark.parametrize("findings", [[], [{"line": 3}], [{"line": 3}] * 3])
def test_programmatic_list_without_limit_accepts_valid_items(findings):
    task = Task(
        id="programmatic",
        result_schema=(
            FieldSpec("findings", "list", items=(FieldSpec("line", "integer"),)),
        ),
        verifier_entrypoint="verifier.py",
    )
    assert validate_result(task, {"findings": findings}) is None


@pytest.mark.parametrize(
    "findings,reason",
    [
        ({}, "must be a list"),
        ([1], "items must be objects"),
        ([{}], "missing required field"),
        ([{"line": True}], "must be an integer"),
        ([{"line": 3, "extra": 1}], "unknown field"),
    ],
)
def test_programmatic_unbounded_list_still_validates_items(findings, reason):
    task = Task(
        id="programmatic",
        result_schema=(
            FieldSpec("findings", "list", items=(FieldSpec("line", "integer"),)),
        ),
        verifier_entrypoint="verifier.py",
    )
    assert reason in validate_result(task, {"findings": findings})


def test_programmatic_zero_list_limit_accepts_only_empty_list():
    task = Task(
        id="programmatic",
        result_schema=(FieldSpec("findings", "list", max_items=0),),
        verifier_entrypoint="verifier.py",
    )
    assert validate_result(task, {"findings": []}) is None
    assert "exceeds max_items" in validate_result(task, {"findings": [{}]})


@pytest.mark.parametrize("commands", [[], ["git", "git"], ["rg"], [["git"]], "git"])
def test_required_commands_reject_empty_duplicates_and_nonseam_labels(
    commands, monkeypatch
):
    from gym import host_services

    monkeypatch.setattr(
        host_services.current(),
        "large_output_commands",
        lambda: frozenset({"git", "pytest"}),
    )
    data = copy.deepcopy(VALID)
    data["required_large_output_commands"] = commands
    with pytest.raises(TaskSchemaError, match="distinct TOC command labels"):
        parse_task_dict(data)


def test_required_commands_follow_the_seam_owner(monkeypatch):
    from gym import host_services

    monkeypatch.setattr(
        host_services.current(),
        "large_output_commands",
        lambda: frozenset({"git", "future-label"}),
    )
    data = copy.deepcopy(VALID)
    data["required_large_output_commands"] = ["future-label", "git"]
    assert parse_task_dict(data).required_large_output_commands == (
        "future-label",
        "git",
    )
