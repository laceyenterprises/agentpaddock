"""RFD-01 contracts: structured findings and read-only workspace inputs."""

from dataclasses import replace
from pathlib import Path

import pytest

from gym import grader
from gym.dispatch import _stage_task_inputs
from gym.schema import SUITES, Stage, TaskSchemaError, parse_task_dict
from gym.suite import discover_tasks


def task(tmp_path, **extra):
    (tmp_path / "verifier.py").write_text("import gym_verifier as gv\ngv.passed()\n")
    return parse_task_dict(
        {
            "id": "probe",
            "suite": "review",
            "verifier": {},
            "result_schema": {
                "findings": {
                    "type": "list",
                    "max_items": 2,
                    "items": {
                        "line": {"type": "integer"},
                        "category": {"type": "enum", "enum": ["docs", "security"]},
                        "summary": {"type": "string", "required": False},
                        "blocking": {"type": "boolean", "required": False},
                        "weight": {"type": "number", "required": False},
                    },
                }
            },
            **extra,
        },
        task_dir=tmp_path,
    )


@pytest.mark.parametrize(
    "findings,verdict",
    [
        ([], "passed"),
        ([{"line": 3, "category": "docs"}], "passed"),
        (
            [{"line": 3, "category": "docs", "summary": "text", "blocking": True, "weight": 0.5}],
            "passed",
        ),
        ([{"line": 3, "category": "docs"}] * 3, "format_failed"),
        ([{"line": 3, "category": "docs", "extra": 1}], "format_failed"),
        ([1], "format_failed"),
        ([[{"line": 3, "category": "docs"}]], "format_failed"),
        ([{"line": True, "category": "docs"}], "format_failed"),
        ([{"category": "docs"}], "format_failed"),
        ([{"line": 3, "category": "invented"}], "format_failed"),
        ([{"line": 3, "category": "docs", "summary": []}], "format_failed"),
        ([{"line": 3, "category": "docs", "blocking": 1}], "format_failed"),
        ([{"line": 3, "category": "docs", "weight": True}], "format_failed"),
        ({}, "format_failed"),
    ],
)
def test_list_results(tmp_path, findings, verdict):
    assert (
        grader.grade(task(tmp_path), grader.Trajectory("succeeded", {"findings": findings})).outcome
        == verdict
    )


def test_nested_list_schema_refused(tmp_path):
    with pytest.raises(TaskSchemaError, match="nested lists"):
        task(
            tmp_path,
            result_schema={
                "f": {
                    "type": "list",
                    "max_items": 2,
                    "items": {
                        "nested": {"type": "list", "items": {}, "max_items": 1},
                    },
                }
            },
        )


@pytest.mark.parametrize(
    "spec",
    [
        {"type": "list", "items": {}},
        {"type": "list", "items": {}, "max_items": True},
        {"type": "list", "items": {}, "max_items": -1},
        {"type": "list", "items": [], "max_items": 1},
        {"type": "string", "items": {}, "max_items": 1},
    ],
)
def test_list_schema_bounds(tmp_path, spec):
    with pytest.raises(TaskSchemaError):
        task(tmp_path, result_schema={"f": spec})


@pytest.mark.parametrize("files", [["../outside"], ["/absolute"], ["."], [""], ["a"] * 9])
def test_capture_schema_refuses_unsafe_paths(tmp_path, files):
    with pytest.raises(TaskSchemaError):
        task(tmp_path, capture={"files": files})


def test_workspace_reaches_verifier_and_missing_is_recorded(tmp_path):
    t = task(tmp_path, capture={"files": ["docs/readme.md", "missing.md"]})
    t.verifier_path.write_text("""import gym_verifier as gv
assert gv.workspace_files() == ["docs/readme.md"]
assert gv.workspace_text("docs/readme.md") == "captured bytes\\n"
assert gv.workspace_text("missing.md") is None
assert gv.workspace_text("../verifier.py") is None
gv.passed()
""")
    root = tmp_path / "worker"
    (root / "docs").mkdir(parents=True)
    (root / "docs/readme.md").write_text("captured bytes\n")
    assert (
        grader.grade(t, grader.Trajectory("succeeded", {"findings": []}, sandbox_root=root)).outcome
        == "passed"
    )
    manifest = grader.capture_workspace(t, root, tmp_path / "copied")
    assert manifest == {"docs/readme.md": "captured", "missing.md": "missing"}


@pytest.mark.parametrize(
    "kind", ["symlink", "parent-symlink", "oversize", "directory", "fifo", "escape"]
)
def test_capture_refuses_unsafe_files(tmp_path, kind):
    import os

    t = task(tmp_path)
    root = tmp_path / "worker"
    root.mkdir()
    name = "file"
    if kind == "symlink":
        (root / name).symlink_to(t.verifier_path)
    elif kind == "parent-symlink":
        (root / name).symlink_to(tmp_path, target_is_directory=True)
        name += "/verifier.py"
    elif kind == "oversize":
        (root / name).write_bytes(b"x" * (256 * 1024 + 1))
    elif kind == "directory":
        (root / name).mkdir()
    elif kind == "fifo":
        os.mkfifo(root / name)
    else:
        name = "../verifier.py"
    with pytest.raises(grader.GraderError, match="capture"):
        grader.capture_workspace(replace(t, capture_files=(name,)), root, tmp_path / "copied")


def test_capture_limit_inclusive(tmp_path):
    t = task(tmp_path, capture={"files": ["file"]})
    root = tmp_path / "worker"
    root.mkdir()
    (root / "file").write_bytes(b"x" * (256 * 1024))
    assert grader.capture_workspace(t, root, tmp_path / "copied") == {"file": "captured"}


def test_hidden_snapshot_is_immutable(tmp_path):
    t = task(tmp_path)
    hidden = tmp_path / "hidden/nested/test.py"
    hidden.parent.mkdir(parents=True)
    hidden.write_bytes(b"original")
    snapshot = grader.snapshot_verifiers([t])
    hidden.write_bytes(b"changed")
    assert snapshot[hidden] == b"original"


def test_staging_cannot_copy_hidden_or_expected(tmp_path):
    t = task(tmp_path)
    for private in ("hidden", "expected"):
        (tmp_path / private).mkdir()
        (tmp_path / private / "answer.txt").write_text("PRIVATE SENTINEL")
    (tmp_path / "stage.sh").write_text('cp -R "$(dirname "$0")"/. "$1"/\n')
    root = tmp_path / "hq/workers/probe/workspace"
    root.parents[1].mkdir(parents=True)
    _stage_task_inputs(replace(t, stages=(Stage("stage.sh"),)), root)
    for path in root.rglob("*"):
        assert "hidden" not in path.relative_to(root).parts
        assert "expected" not in path.relative_to(root).parts
        if path.is_file():
            assert b"PRIVATE SENTINEL" not in path.read_bytes()


def test_every_suite_selects_its_tasks_and_all_includes_them(tmp_path):
    for suite in SUITES:
        directory = tmp_path / suite
        directory.mkdir()
        (directory / "task.toml").write_text(f'''id = "{suite}-probe"
suite = "{suite}"
[result_schema.answer]
type = "string"
[verifier]
''')
        (directory / "verifier.py").write_text("pass\n")
        assert [t.id for t in discover_tasks(tmp_path, suite=suite)] == [f"{suite}-probe"]
    assert {t.suite for t in discover_tasks(tmp_path, suite="all")} == SUITES


@pytest.mark.parametrize("private", ["hidden", "hidden/file.py"])
def test_hidden_snapshot_refuses_symlinks(tmp_path, private):
    t = task(tmp_path)
    path = tmp_path / private
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(t.verifier_path)
    with pytest.raises(grader.GraderError, match="symlink"):
        grader.snapshot_verifiers([t])


@pytest.mark.parametrize("task_dir", sorted((Path(__file__).parent.parent / "tasks").glob("*")))
def test_every_staged_corpus_task_keeps_hidden_and_expected_private(tmp_path, task_dir):
    import shutil
    from gym.schema import load_task

    if not (task_dir / "task.toml").exists():
        pytest.skip("not a task")
    source = tmp_path / "task"
    shutil.copytree(task_dir, source)
    (source / "hidden").mkdir(exist_ok=True)
    (source / "hidden/private.txt").write_text("RFD PRIVATE HIDDEN SENTINEL")
    t = load_task(source)
    root = tmp_path / "hq/workers/probe/workspace"
    root.parents[1].mkdir(parents=True)
    _stage_task_inputs(t, root)
    for path in root.rglob("*"):
        assert not {"hidden", "expected"}.intersection(path.relative_to(root).parts)
        if path.is_file():
            assert b"RFD PRIVATE HIDDEN SENTINEL" not in path.read_bytes()


def test_stage_script_cannot_escape_public_copy(tmp_path):
    t = task(tmp_path)
    script = tmp_path / "hidden/stage.sh"
    script.parent.mkdir()
    script.write_text('echo leaked > "$1/leak"\n')
    root = tmp_path / "hq/workers/probe/workspace"
    root.parents[1].mkdir(parents=True)
    with pytest.raises(RuntimeError, match="escapes public task copy"):
        _stage_task_inputs(replace(t, stages=(Stage(str(script)),)), root)
    assert not (root / "leak").exists()
