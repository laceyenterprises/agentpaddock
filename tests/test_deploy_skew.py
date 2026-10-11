"""Deploy and interruption fixtures; never dispatch to the live worker pool."""

import io
import json
import shutil
from pathlib import Path

import pytest
from conftest import FIXTURES_DIR

from gym import cli, grader, outcomes, suite, taint
from gym.alerting import RecordingAlerter


def test_suite_stages_snapshot_after_disk_changes(tmp_path, monkeypatch):
    support = tmp_path / "support"
    support.mkdir()
    for filename in (
        "verifier_runtime.py",
        "harness_toolcalls.py",
        "doc_checks.py",
        "review_checks.py",
    ):
        shutil.copyfile(Path(grader.__file__).with_name(filename), support / filename)
    monkeypatch.setattr(grader, "_SUPPORT_FILES", grader.snapshot_support_files(support))
    tasks_dir = tmp_path / "tasks"
    shutil.copytree(FIXTURES_DIR / "mini-suite", tasks_dir)
    tasks = suite.discover_tasks(tasks_dir)

    class DeploySource:
        def run(self, task):
            for path in [*support.glob("*.py"), *tasks_dir.glob("*/verifier.py")]:
                path.write_text("raise RuntimeError('new deployed code')\n")
            return grader.Trajectory("succeeded", {"answer": "ok"})

    result = suite.run_suite(tasks, DeploySource(), taint_mode=taint.ENFORCE)
    assert result.passed == 2
    assert grader._VERIFIER_FILES.get() is None


@pytest.mark.parametrize("failure", ["import", "grader", "interrupt"])
@pytest.mark.parametrize("model", [None, "fixture-model"])
def test_cli_preserves_results_and_continues(tmp_path, monkeypatch, failure, model):
    tasks_dir = tmp_path / "tasks"
    shutil.copytree(FIXTURES_DIR / "mini-suite", tasks_dir)
    if failure == "import":
        (tasks_dir / "mini-fail" / "verifier.py").write_text("import missing_gym_module\n")
    original_grade = grader.grade

    def broken_grade(task, trajectory, **kwargs):
        if failure == "grader" and task.id == "mini-fail":
            raise ValueError("fixture grader failure")
        return original_grade(task, trajectory, **kwargs)

    monkeypatch.setattr(grader, "grade", broken_grade)
    dispatched = []

    class Source:
        def run(self, task):
            dispatched.append(task.id)
            if failure == "interrupt" and len(dispatched) == 2:
                raise KeyboardInterrupt
            return grader.Trajectory("succeeded", {"answer": "ok"})

    class Resolver:
        def resolve(self, agent):
            return ["bash:safe", "read", "write"]

    results_dir = tmp_path / "results"
    argv = [
        "run",
        "--tasks-dir",
        str(tasks_dir),
        "--locks-dir",
        str(FIXTURES_DIR / "locks"),
        "--results-dir",
        str(results_dir),
        "--baseline",
        str(tmp_path / "baseline.json"),
        "--taint-mode",
        "enforce",
        "--json",
    ]
    if model:
        argv += ["--model", model]
    out = io.StringIO()
    if failure == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            cli.main(
                argv, source=Source(), resolver=Resolver(), alerter=RecordingAlerter(), out=out
            )
    else:
        assert (
            cli.main(
                argv, source=Source(), resolver=Resolver(), alerter=RecordingAlerter(), out=out
            )
            == cli.EXIT_ERROR
        )
    paths = list(results_dir.glob("*/*.json"))
    assert len(paths) == 1
    document = json.loads(paths[0].read_text())
    cells = document["summary"]["cells"]
    assert dispatched == ["mini-fail", "mini-pass"]
    if failure == "interrupt":
        assert len(cells) == 1
        assert cells[0]["verdict"] == outcomes.PASSED
    else:
        assert [c["verdict"] for c in cells] == [outcomes.ERROR, outcomes.PASSED]
        assert document["summary"]["errors"] == 1
        assert ("ModuleNotFoundError" if failure == "import" else "ValueError") in cells[0][
            "reason"
        ]
        assert json.loads(out.getvalue())["cells"] == cells
