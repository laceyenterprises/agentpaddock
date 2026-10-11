"""GYMSKEW-02: one gym-library version per run, even when a deploy lands mid-run.

The mid-run simulations run in a subprocess against a temp copy of the gym
package, so rewriting "deployed" modules never touches this checkout and never
pins this test process.
"""

import ast
import json
import os
import pkgutil
import shutil
import subprocess
import sys
import tracemalloc
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("gym.dispatch", reason="host integration is not shipped")
from conftest import FIXTURES_DIR

import gym
from gym import grader, library, suite, taint
from gym.dispatch import _guarded_prompt, _materialize_task_inputs, _stage_task_inputs
from gym.schema import Stage, load_task

GYM_DIR = Path(gym.__file__).resolve().parent
NEW_SYMBOL = "GYMSKEW_DEPLOYED_SYMBOL"

# Mirrors the 2026-10-06 run: the process starts, then a deploy lands while a
# fix-family cell is in flight. The new hidden_tests.py imports a symbol only
# the new schema.py has; gym.schema in sys.modules is still the old one.
_DRIVER = """
import json, sys
from pathlib import Path

package, task_dir, mode = sys.argv[1:4]
from gym import grader, schema, suite, taint

if mode != "no-preload":
    import gym

    gym.preload_library()

gym_dir = Path(package) / "gym"


class DeployMidRun:
    def run(self, task):
        schema_file = gym_dir / "schema.py"
        schema_file.write_text(schema_file.read_text() + "\\n%(symbol)s = True\\n")
        hidden = gym_dir / "hidden_tests.py"
        hidden.write_text(
            hidden.read_text() + "\\nfrom gym.schema import %(symbol)s  # noqa: F401\\n"
        )
        if mode == "guard":
            # A module the run never imported: the next import reaches the disk.
            sys.modules.pop("gym.hidden_tests")
        return grader.Trajectory("succeeded", None)


task = schema.load_task(Path(task_dir))
result = suite.run_suite([task], DeployMidRun(), taint_mode=taint.ENFORCE)
print(json.dumps({"outcome": result.graded[0].cell.outcome, "reason": result.graded[0].cell.reason}))
""" % {"symbol": NEW_SYMBOL}


def _copy_package(tmp_path: Path) -> Path:
    package = tmp_path / "lib"
    shutil.copytree(GYM_DIR, package / "gym", ignore=shutil.ignore_patterns("__pycache__"))
    return package


def _python(package: Path, *args: str, tmp_path: Path) -> subprocess.CompletedProcess:
    path = [str(package)] + [p for p in sys.path if p and Path(p).resolve() != GYM_DIR.parent]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(path), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, "-B", *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _run_driver(tmp_path: Path, mode: str) -> dict:
    package = _copy_package(tmp_path)
    driver = tmp_path / "driver.py"
    driver.write_text(_DRIVER)
    task_dir = FIXTURES_DIR / "hard-fix-regression-probe"
    proc = _python(package, str(driver), str(package), str(task_dir), mode, tmp_path=tmp_path)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_mid_run_upgrade_grades_normally_with_preload(tmp_path):
    cell = _run_driver(tmp_path, "preload")
    assert cell["outcome"] == "no_submission", cell
    assert "ImportError" not in (cell["reason"] or "")


def test_mid_run_upgrade_reproduces_import_error_without_preload(tmp_path):
    # Negative control: this is the production failure the preload prevents.
    cell = _run_driver(tmp_path, "no-preload")
    assert cell["outcome"] == "error"
    assert f"ImportError: cannot import name '{NEW_SYMBOL}' from 'gym.schema'" in cell["reason"]


def test_guard_turns_late_import_of_changed_module_into_harness_error(tmp_path):
    cell = _run_driver(tmp_path, "guard")
    assert cell["outcome"] == "error"
    assert cell["reason"].startswith(f"{library.REASON}: gym.hidden_tests (hidden_tests.py)")


def test_every_module_is_preloaded_or_excluded_with_reason(tmp_path):
    proc = _python(
        GYM_DIR.parent,
        "-c",
        "import json, sys\n"
        "from gym import library\n"
        "library.preload_library()\n"
        "print(json.dumps({'preloaded': library.preloaded_modules(),"
        " 'loaded': sorted(m for m in sys.modules if m.startswith('gym.'))}))",
        tmp_path=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    on_disk = set(library.module_names()) - {"gym"}
    walked = {info.name for info in pkgutil.walk_packages(gym.__path__, prefix="gym.")}
    assert walked == on_disk, "preload walk must see every module file"
    preloaded = set(report["preloaded"])
    assert preloaded | set(library.EXCLUDED) == on_disk, (
        "new gym module neither preloaded nor in library.EXCLUDED: "
        f"{sorted(on_disk - preloaded - set(library.EXCLUDED))}"
    )
    assert not preloaded & set(library.EXCLUDED)
    assert set(library.EXCLUDED) <= on_disk, "stale EXCLUDED entry"
    assert not set(report["loaded"]) & set(library.EXCLUDED), "preload imported an exclusion"


# Each exclusion's reason is a module-level import the preload must not pay for
# or require. If the import goes away, so must the exclusion.
_EXCLUSION_DEPENDENCIES = {"gym.matrix_report": "sew", "gym.toc_eval": "cwp_dispatch"}


@pytest.mark.parametrize("name", sorted(library.EXCLUDED))
def test_exclusion_reason_still_holds(name):
    assert library.EXCLUDED[name].strip()
    dependency = _EXCLUSION_DEPENDENCIES[name]
    assert dependency in library.EXCLUDED[name]
    tree = ast.parse((GYM_DIR / (name.split(".", 1)[1] + ".py")).read_text())
    top_level = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level.add(node.module.split(".")[0])
    assert dependency in top_level, f"{name} no longer imports {dependency}; drop the exclusion"


def _pinned_identity(tmp_path, monkeypatch):
    package = _copy_package(tmp_path)
    identity = library.library_identity(package / "gym")
    monkeypatch.setattr(library, "_pinned", identity)
    return package / "gym", identity


def test_finder_loads_only_pinned_bytes(tmp_path, monkeypatch):
    gym_dir, identity = _pinned_identity(tmp_path, monkeypatch)
    finder = library._PinnedLibraryFinder(identity)
    spec = finder.find_spec("gym.outcomes", [str(gym_dir)])
    original = (gym_dir / "outcomes.py").read_bytes()
    # Rewritten after the hash check: the loader still executes the verified bytes.
    (gym_dir / "outcomes.py").write_text("raise RuntimeError('deployed')\n")
    assert spec.loader.get_code("gym.outcomes").co_filename == str(gym_dir / "outcomes.py")
    assert spec.loader._source == original

    with pytest.raises(library.GymLibraryChanged, match=library.REASON):
        finder.find_spec("gym.outcomes", [str(gym_dir)])
    (gym_dir / "brand_new.py").write_text("X = 1\n")
    with pytest.raises(library.GymLibraryChanged, match="gym.brand_new"):
        finder.find_spec("gym.brand_new", [str(gym_dir)])
    assert finder.find_spec("json", None) is None


def test_import_error_after_disk_change_is_classified_as_skew(tmp_path, monkeypatch):
    gym_dir, _ = _pinned_identity(tmp_path, monkeypatch)
    error = ImportError(f"cannot import name '{NEW_SYMBOL}' from 'gym.schema'", name="gym.schema")
    assert library.skew_reason(error) is None  # Same disk: an ordinary bug, not skew.
    assert library.skew_reason(ImportError("x", name="numpy")) is None
    (gym_dir / "schema.py").write_text("CHANGED = True\n")
    try:
        raise RuntimeError("grading failed") from error
    except RuntimeError as wrapped:
        assert library.skew_reason(wrapped).startswith(f"{library.REASON}: ImportError")
    assert library.skew_reason(library.GymLibraryChanged(f"{library.REASON}: x")).endswith("x")


def test_preload_fails_closed_when_support_bundle_differs(monkeypatch):
    monkeypatch.setattr(library, "_pinned", None)
    monkeypatch.setattr(library, "_preloaded", ())
    monkeypatch.setattr(sys, "meta_path", list(sys.meta_path))
    finders = list(sys.meta_path)
    monkeypatch.setitem(grader._SUPPORT_FILES, "gym_verifier.py", b"# older deploy\n")
    with pytest.raises(library.GymLibraryChanged, match="verifier_runtime.py"):
        library.preload_library()
    assert library.pinned() is None
    assert sys.meta_path == finders


def test_preload_refuses_missing_start_identity_before_reading_disk(monkeypatch):
    monkeypatch.setattr(library, "_pinned", None)
    monkeypatch.setattr(library, "_preloaded", ())
    monkeypatch.setattr(gym, "_START_IDENTITY", None)
    finders = list(sys.meta_path)

    def unreadable(root=None):
        raise OSError("initial snapshot unreadable")

    monkeypatch.setattr(library, "library_identity", unreadable)
    assert library.start_identity(GYM_DIR) is None

    def fresh_snapshot(root=None):
        pytest.fail("must not replace the missing process-start identity")

    monkeypatch.setattr(library, "library_identity", fresh_snapshot)
    with pytest.raises(library.GymLibraryChanged, match="identity.*process start"):
        library.preload_library()
    assert library.pinned() is None
    assert library.preloaded_modules() == ()
    assert sys.meta_path == finders


@pytest.mark.parametrize(
    ("entrypoint", "args"),
    [
        ("matrix", ["run", "--battery", "unused.toml"]),
        ("matrix", ["run", "--resume", "saved-run", "--battery", "unused.toml"]),
        ("toc_eval", []),
        ("endpoints", []),
    ],
)
def test_entrypoints_report_preload_refusal_cleanly(entrypoint, args, monkeypatch, capsys):
    import importlib

    module = importlib.import_module(f"gym.{entrypoint}")

    def refused():
        raise library.GymLibraryChanged(f"{library.REASON}: cannot verify process start")

    monkeypatch.setattr(library, "preload_library", refused)
    assert module.main(args) == 1
    assert capsys.readouterr().err == (f"[hq gym] {library.REASON}: cannot verify process start\n")


@pytest.mark.parametrize("resume_args", [[], ["--resume", "saved-run"]], ids=["fresh", "resume"])
def test_matrix_installs_library_pin_before_accessing_run_state(tmp_path, resume_args):
    # A fresh subprocess exercises the real preload without pinning pytest.
    proc = _python(
        GYM_DIR.parent,
        "-c",
        "import sys\n"
        "from gym import dispatch, library, matrix\n"
        "assert library.pinned() is None\n"
        "class PinnedBeforeState(Exception):\n"
        "    pass\n"
        "def check_pin(root):\n"
        "    assert library.pinned() is not None\n"
        "    assert any(isinstance(f, library._PinnedLibraryFinder) for f in sys.meta_path)\n"
        "    raise PinnedBeforeState\n"
        "dispatch._resolve_hq_root = check_pin\n"
        "try:\n"
        "    matrix.main(['run', *sys.argv[1:]])\n"
        "except PinnedBeforeState:\n"
        "    print('pinned before state access')\n"
        "else:\n"
        "    raise AssertionError('matrix never reached the state boundary')\n",
        *resume_args,
        tmp_path=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "pinned before state access"


def _staged_task(tmp_path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "task.toml").write_text(
        'id = "stage-probe"\nsuite = "rca"\n[dispatch]\nprompt = "prompt.md"\n'
        '[result_schema.answer]\ntype = "string"\n[verifier]\n'
    )
    (task_dir / "verifier.py").write_text("import gym_verifier as gv\ngv.passed()\n")
    (task_dir / "prompt.md").write_text("start prompt\n")
    (task_dir / "fixtures").mkdir()
    (task_dir / "fixtures" / "input.txt").write_text("start fixture\n")
    (task_dir / "stage.sh").write_text('cp "$(dirname "$0")"/fixtures/input.txt "$1"/input.txt\n')
    task = replace(load_task(task_dir), stages=(Stage("stage.sh"),))
    root = tmp_path / "hq/workers/probe/workspace"
    root.parents[1].mkdir(parents=True)
    return task, task_dir, root


def _deploy_task_inputs(task_dir):
    (task_dir / "prompt.md").write_text("deployed prompt\n")
    (task_dir / "fixtures" / "input.txt").write_text("deployed fixture\n")
    (task_dir / "stage.sh").write_text('echo deployed > "$1"/input.txt\n')


def test_run_stages_task_inputs_read_at_run_start(tmp_path):
    task, task_dir, root = _staged_task(tmp_path)
    seen = {}

    class DeployThenDispatch:
        def run(self, task):
            _deploy_task_inputs(task_dir)
            _stage_task_inputs(task, root)
            seen["prompt"] = _guarded_prompt(task, root).read_text()
            seen["input"] = (root / "input.txt").read_text()
            return grader.Trajectory("succeeded", {"answer": "x"})

    result = suite.run_suite([task], DeployThenDispatch(), taint_mode=taint.ENFORCE)
    assert result.graded[0].cell.outcome == "passed"
    assert seen["input"] == "start fixture\n"
    assert seen["prompt"].endswith("start prompt\n")
    assert grader._TASK_INPUTS.get() is None and grader._VERIFIER_FILES.get() is None


def test_unpinned_staging_still_reads_disk(tmp_path):
    task, task_dir, root = _staged_task(tmp_path)
    _deploy_task_inputs(task_dir)
    _stage_task_inputs(task, root)
    assert (root / "input.txt").read_text() == "deployed\n"
    assert _guarded_prompt(task, root).read_text().endswith("deployed prompt\n")


def test_snapshot_keeps_symlink_refusal_per_task(tmp_path):
    task, task_dir, root = _staged_task(tmp_path)
    (task_dir / "fixtures" / "link").symlink_to(task_dir / "prompt.md")
    with grader.pinned_run_files([task]):
        with pytest.raises(RuntimeError, match="stage source symlink refused"):
            _stage_task_inputs(task, root)


def test_snapshot_never_holds_hidden_or_expected(tmp_path):
    task, task_dir, _ = _staged_task(tmp_path)
    for private in ("hidden", "expected"):
        (task_dir / private).mkdir()
        (task_dir / private / "answer.txt").write_text("PRIVATE")
    with grader.pinned_run_files([task]):
        entries = grader._TASK_INPUTS.get()[task_dir]
        assert not any({"hidden", "expected"} & set(Path(name).parts) for name in entries)
        assert entries["stage.sh"][1].read_bytes().startswith(b"cp ")


def test_matrix_input_snapshot_uses_bounded_heap_and_cleans_up(tmp_path):
    tasks = []
    for index in range(3):
        directory = tmp_path / str(index)
        directory.mkdir()
        task, task_dir, _ = _staged_task(directory)
        with (task_dir / "fixtures" / "large.bin").open("wb") as stream:
            stream.truncate(8 * 1024 * 1024)
        tasks.append(task)
    tracemalloc.start()
    try:
        with grader.pinned_run_files(tasks):
            _, peak = tracemalloc.get_traced_memory()
            assert peak < 4 * 1024 * 1024, f"24 MiB of inputs retained {peak} heap bytes"
            inputs = grader._TASK_INPUTS.get()
            copies = [inputs[task.task_dir]["fixtures/large.bin"][1] for task in tasks]
            assert all(isinstance(path, Path) and path.is_file() for path in copies)
            with grader.pinned_run_files(tasks[:1]):
                assert grader._TASK_INPUTS.get() is inputs
            assert all(path.is_file() for path in copies)
        assert all(not path.exists() for path in copies)
        assert grader._TASK_INPUTS.get() is None
    finally:
        tracemalloc.stop()


def test_input_snapshot_cleans_up_when_run_raises(tmp_path):
    task, task_dir, _ = _staged_task(tmp_path)
    with pytest.raises(RuntimeError, match="run failed"):
        with grader.pinned_run_files([task]):
            copy = grader._TASK_INPUTS.get()[task_dir]["prompt.md"][1]
            raise RuntimeError("run failed")
    assert not copy.exists()
    assert grader._TASK_INPUTS.get() is None and grader._VERIFIER_FILES.get() is None


def test_disk_snapshot_preserves_modes_and_empty_directories(tmp_path):
    task, task_dir, _ = _staged_task(tmp_path)
    (task_dir / "stage.sh").chmod(0o751)
    (task_dir / "empty").mkdir(mode=0o750)
    with grader.pinned_run_files([task]):
        entries = grader._TASK_INPUTS.get()[task_dir]
        staged = tmp_path / "staged"
        _materialize_task_inputs(entries, staged)
        assert (staged / "stage.sh").stat().st_mode & 0o777 == 0o751
        assert (staged / "empty").is_dir()
        assert (staged / "empty").stat().st_mode & 0o777 == 0o750


def test_disk_snapshot_copy_failure_is_isolated_to_affected_task(tmp_path, monkeypatch):
    task, task_dir, root = _staged_task(tmp_path)
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other, other_task_dir, other_root = _staged_task(other_dir)
    original = shutil.copyfile

    def copy(source, destination):
        if source == task_dir / "prompt.md":
            raise OSError("snapshot read failed")
        return original(source, destination)

    monkeypatch.setattr(shutil, "copyfile", copy)
    with grader.pinned_run_files([task, other]):
        with pytest.raises(RuntimeError, match="snapshot read failed"):
            _stage_task_inputs(task, root)
        _stage_task_inputs(other, other_root)
        assert (other_root / "input.txt").read_text() == "start fixture\n"
        copy_path = grader._TASK_INPUTS.get()[other_task_dir]["prompt.md"][1]
    assert not copy_path.exists()


def test_enclosing_pin_is_reused_by_nested_run(tmp_path):
    # `hq gym matrix` pins at start, then grades each cell through run_suite.
    task, task_dir, _ = _staged_task(tmp_path)
    with grader.pinned_run_files([task]):
        (task_dir / "verifier.py").write_text("import gym_verifier as gv\ngv.fail('new')\n")

        class Source:
            def run(self, task):
                return grader.Trajectory("succeeded", {"answer": "x"})

        result = suite.run_suite([task], Source(), taint_mode=taint.ENFORCE)
        assert result.graded[0].cell.outcome == "passed"
        assert grader._TASK_INPUTS.get() is not None
    assert grader._TASK_INPUTS.get() is None
    # Without an enclosing pin, the run takes its own (GYMSKEW-01) snapshot.
    result = suite.run_suite([task], Source(), taint_mode=taint.ENFORCE)
    assert result.graded[0].cell.outcome == "failed"


def test_dispatch_skew_is_not_graded_as_agent_failure(monkeypatch):
    from gym.dispatch import WorkerPoolTrajectorySource

    task = load_task(FIXTURES_DIR / "mini-suite" / "mini-pass")
    source = WorkerPoolTrajectorySource(hq_bin="hq", hq_root=Path("/nonexistent"))

    def broken(task):
        raise RuntimeError("hq dispatch failed")

    monkeypatch.setattr(source, "_dispatch", broken)
    assert source._run_once(task).terminal_status == "dispatch_failed"

    def skewed(task):
        raise library.GymLibraryChanged(f"{library.REASON}: gym.endpoints changed")

    monkeypatch.setattr(source, "_dispatch", skewed)
    with pytest.raises(library.GymLibraryChanged):
        source._run_once(task)


def test_matrix_dispatch_skew_is_terminal_harness_error(tmp_path):
    from test_matrix import prepare

    run = prepare(tmp_path)

    def dispatch(cell):
        raise library.GymLibraryChanged(f"{library.REASON}: gym.endpoints changed")

    run.backend.dispatch = dispatch
    for _ in range(4):
        run.tick()
    assert run.cells and all(
        c["status"] == "done"
        and c["outcome"] == "error"
        and c["reason"].startswith(library.REASON)
        and not c["attempts"]
        for c in run.cells
    )


def test_deploy_between_process_start_and_preload_refuses_the_run(tmp_path):
    # `python -m gym.cli` imports half the library before main() can preload.
    package = _copy_package(tmp_path)
    proc = _python(
        package,
        "-c",
        "import sys\n"
        "from pathlib import Path\n"
        "from gym import cli\n"
        f"outcomes = Path({str(package)!r}) / 'gym' / 'outcomes.py'\n"
        "outcomes.write_text(outcomes.read_text() + '\\nDEPLOYED = True\\n')\n"
        "sys.exit(cli.main(['run']))\n",
        tmp_path=tmp_path,
    )
    assert proc.returncode == 1, proc.stderr
    assert f"{library.REASON}: the gym library changed on disk after this process started" in (
        proc.stderr
    )


@pytest.mark.parametrize("error_type", [OSError, ImportError, ModuleNotFoundError, SyntaxError])
def test_preload_wraps_import_failures(monkeypatch, error_type):
    monkeypatch.setattr(library, "_pinned", None)
    monkeypatch.setattr(library, "_preloaded", ())
    failure = error_type("deploy interrupted import")

    def interrupted(name):
        raise failure

    monkeypatch.setattr(library.importlib, "import_module", interrupted)
    with pytest.raises(library.GymLibraryChanged, match=library.REASON) as caught:
        library.preload_library()
    assert caught.value.__cause__ is failure
    assert library.pinned() is None
    assert library.preloaded_modules() == ()


def test_pinned_loader_source_survives_disk_rewrite(tmp_path):
    path = tmp_path / "pinned.py"
    original = b"# coding: latin-1\nvalue = 'caf\xe9'\n"
    path.write_bytes(original)
    loader = library._PinnedSourceLoader("gym.pinned", str(path), original)
    path.write_text("raise RuntimeError('new deployment')\n")
    assert loader.get_source("gym.pinned") == "# coding: latin-1\nvalue = 'caf\u00e9'\n"
    namespace = {}
    exec(loader.get_code("gym.pinned"), namespace)
    assert namespace["value"] == "caf\u00e9"
