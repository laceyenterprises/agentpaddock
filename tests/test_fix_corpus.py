"""Checked-in fix variants exercise the runner protocol, never containment.

No agent output or activation probe uses protocol_backend here. The capture
boundary checks all declared staged inputs; it cannot inventory arbitrary new
files in the worker workspace. Hidden execution uses only captured inputs.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("gym.dispatch", reason="host integration is not shipped")

from gym import baseline, grader, outcomes
from gym.dispatch import WorkerPoolTrajectorySource
from gym.grader import Trajectory
from gym.schema import load_task
from gym.suite import discover_tasks, run_suite

ROOT = Path(__file__).resolve().parents[1]
TASKS = tuple(sorted((ROOT / "tasks").glob("fix-*/task.toml")))


def stage(manifest, directory):
    subprocess.run(
        ["bash", str(manifest.parent / "stages/stage-input.sh"), str(directory)],
        check=True,
        capture_output=True,
    )
    return directory


def grade(manifest, directory):
    return grader.grade(
        load_task(manifest.parent), Trajectory("succeeded", {"done": True}, sandbox_root=directory)
    )


def checks(cell):
    return {item["name"]: item["ok"] for item in cell.subchecks}


def test_six_fix_contracts():
    assert len(TASKS) == 6
    assert sum("Real-derived" in path.read_text() for path in TASKS) == 3
    for manifest in TASKS:
        task = load_task(manifest.parent)
        assert task.suite == "fix"
        assert task.hidden_tests_expected_tests == 3
        cases = json.loads((manifest.parent / task.hidden_tests_entrypoint).read_text())
        assert len(cases) == task.hidden_tests_expected_tests
        staged = json.loads((manifest.parent / "fixture/staged-manifest.json").read_text())
        assert staged["editable"] == ["target.py"]
        assert set(task.capture_files) == {*staged["files"], "staged-manifest.json"}
        assert "[hidden_tests]" in manifest.read_text()


def test_fix_tasks_dispatch(monkeypatch):
    tasks = [task for task in discover_tasks(ROOT / "tasks", suite="all") if task.suite == "fix"]
    seen = []

    def dispatch(self, task):
        seen.append(task.id)
        raise RuntimeError("synthetic dispatch sentinel")

    monkeypatch.setattr(WorkerPoolTrajectorySource, "_dispatch", dispatch)
    result = run_suite(tasks, WorkerPoolTrajectorySource(), suite="all", taint_mode="enforce")
    assert seen == [task.id for task in tasks]
    assert result.errored == 6
    assert result.has_agent_errors
    assert all(cell.outcome == outcomes.AGENT_ERROR for cell in result.cells)
    previous = baseline.Baseline(True, None, {key: outcomes.PASSED for key in result.verdicts()})
    assert baseline.compare(previous, result.verdicts()).has_regressions is False
    assert all("synthetic dispatch sentinel" in cell.reason for cell in result.cells)


def alternative_correct_fix(manifest):
    reference = (manifest.parent / "expected/reference.py").read_text()
    slug = manifest.parent.name
    if slug == "fix-corrupt-refresh-checkpoint":
        return reference.replace("logging.error(", "logging.exception(")
    if slug == "fix-stale-lock-recovery":
        return reference.replace(
            "return fs.remove_if_same(path, observed)",
            'checked = fs.stat(path)\n        return now - checked["mtime"] > stale_after and fs.remove_if_same(path, checked)',
        ).replace("return False", "return None")
    if slug == "fix-nested-template-copy":
        return reference.replace(
            "selected = template.copy()", "import copy\n    selected = copy.deepcopy(template)"
        )
    if slug == "fix-publish-guard-order":
        return "class Disabled(PermissionError):\n    pass\n\n" + reference.replace(
            "raise PermissionError(account)", "raise Disabled(account)"
        )
    if slug == "fix-retry-empty-page":
        return reference.replace(
            "def read_page(client, cursor):",
            "class Retryable(TemporaryUnavailable):\n    pass\n\ndef read_page(client, cursor):",
        ).replace(
            "except TemporaryUnavailable:\n        raise",
            "except TemporaryUnavailable as exc:\n        raise Retryable() from exc",
        )
    if slug == "fix-rescue-local-tip":
        return "def provision(local, remote, branch, trunk):\n    return local.setdefault(branch, remote.get(branch, trunk))\n"
    raise AssertionError(slug)


@pytest.mark.parametrize("manifest", TASKS, ids=lambda p: p.parent.name)
def test_alternative_correct_fix_passes(manifest, tmp_path, protocol_backend):
    stage(manifest, tmp_path)
    # Diagnostics are legal in every task, including target import time.
    (tmp_path / "target.py").write_text(
        alternative_correct_fix(manifest)
        + "\nimport warnings\nwarnings.warn('diagnostic')\nprint('debug')\n"
    )
    cell = grade(manifest, tmp_path)
    assert cell.outcome == outcomes.PASSED, cell.reason
    assert checks(cell)["hidden_tests_pass"]


@pytest.mark.parametrize("manifest", TASKS, ids=lambda p: p.parent.name)
def test_original_fails_hidden(manifest, tmp_path, protocol_backend):
    cell = grade(manifest, stage(manifest, tmp_path))
    assert cell.outcome == outcomes.FAILED
    assert checks(cell)["edited_only_allowed_files"]
    assert not checks(cell)["hidden_tests_pass"]
    assert "regression_tests_pass" not in checks(cell)


@pytest.mark.parametrize("manifest", TASKS, ids=lambda p: p.parent.name)
def test_insufficient_fails_hidden(manifest, tmp_path, protocol_backend):
    stage(manifest, tmp_path)
    (tmp_path / "target.py").write_bytes(
        (manifest.parent / "expected/insufficient.py").read_bytes()
    )
    cell = grade(manifest, tmp_path)
    assert cell.outcome == outcomes.FAILED
    assert checks(cell)["edited_only_allowed_files"]
    assert not checks(cell)["hidden_tests_pass"]


@pytest.mark.parametrize("manifest", TASKS, ids=lambda p: p.parent.name)
def test_reference_passes(manifest, tmp_path, protocol_backend):
    stage(manifest, tmp_path)
    (tmp_path / "target.py").write_bytes((manifest.parent / "expected/reference.py").read_bytes())
    cell = grade(manifest, tmp_path)
    assert cell.outcome == outcomes.PASSED, cell.reason
    assert set(checks(cell)) == {"edited_only_allowed_files", "hidden_tests_pass", "small_diff"}
    assert all(checks(cell).values())


@pytest.mark.parametrize("manifest", TASKS, ids=lambda p: p.parent.name)
def test_visible_test_edit_instead_of_fix_fails(manifest, tmp_path, protocol_backend):
    stage(manifest, tmp_path)
    (tmp_path / "test_visible.py").write_text("import unittest\n")
    cell = grade(manifest, tmp_path)
    assert cell.outcome == outcomes.FAILED
    assert not checks(cell)["edited_only_allowed_files"]
    assert not checks(cell)["hidden_tests_pass"]


@pytest.mark.parametrize(
    "protected", ["test_visible.py", "support.py", "finding.md", "staged-manifest.json"]
)
@pytest.mark.parametrize("manifest", TASKS, ids=lambda p: p.parent.name)
def test_reference_cannot_edit_protected_input(manifest, protected, tmp_path, protocol_backend):
    stage(manifest, tmp_path)
    (tmp_path / "target.py").write_bytes((manifest.parent / "expected/reference.py").read_bytes())
    with (tmp_path / protected).open("a") as handle:
        handle.write("\n# unauthorized edit\n")
    cell = grade(manifest, tmp_path)
    assert cell.outcome == outcomes.FAILED
    assert not checks(cell)["edited_only_allowed_files"]


@pytest.mark.parametrize("manifest", TASKS, ids=lambda p: p.parent.name)
def test_staging_and_visible_tests_before_after(manifest, tmp_path):
    tmp_path = tmp_path / "workspace"
    stage(manifest, tmp_path)
    task = load_task(manifest.parent)
    assert {path.name for path in tmp_path.iterdir()} == set(task.capture_files)
    assert not (tmp_path / "hidden").exists()
    assert not (tmp_path / "expected").exists()
    for variant in ("original", "insufficient", "reference", "regression-broken"):
        if variant != "original":
            (tmp_path / "target.py").write_bytes(
                (manifest.parent / f"expected/{variant}.py").read_bytes()
            )
        result = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "test_visible"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (manifest, variant, result.stderr)


def test_real_findings_preserve_review_cards():
    titles = {
        "rescue-local-tip": "Rescue provisioning discards unpushed local commits",
        "stale-lock-recovery": "Two waiters can both break the same stale lock",
        "corrupt-refresh-checkpoint": "A damaged checkpoint halts every later refresh",
    }
    for slug, title in titles.items():
        review = ROOT / f"tasks/review-{slug}/expected/source-review.txt"
        finding = (ROOT / f"tasks/fix-{slug}/fixture/finding.md").read_text()
        source = review.read_text()
        assert (ROOT / f"tasks/fix-{slug}/expected/source-review.txt").read_bytes() == (
            review.read_bytes()
        )
        card = finding.split("\n\nFixture mapping:")[0]
        assert card in source
        assert title in card
        for field in ("File", "Lines", "Problem", "Why it matters"):
            assert f"**{field}:**" in card
        provenance = (ROOT / f"tasks/fix-{slug}/expected/PROVENANCE.md").read_text()
        assert "Derived from a private review" in provenance
        assert "github.com" not in provenance
        assert "Python port" in provenance
        remediation = f"tasks/review-{slug}/expected/source-remediation.diff"
        assert remediation in provenance
        assert (ROOT / remediation).is_file()
        assert f"tasks/fix-{slug}/fixture/finding.md" in provenance


@pytest.mark.parametrize("manifest", TASKS, ids=lambda p: p.parent.name)
def test_missing_protected_input_fails(manifest, tmp_path, protocol_backend):
    stage(manifest, tmp_path)
    (tmp_path / "target.py").write_bytes((manifest.parent / "expected/reference.py").read_bytes())
    (tmp_path / "finding.md").unlink()
    cell = grade(manifest, tmp_path)
    assert cell.outcome == outcomes.FAILED
    assert not checks(cell)["edited_only_allowed_files"]


@pytest.mark.parametrize(
    "variant, expected",
    [
        ("original", outcomes.FAILED),
        ("insufficient", outcomes.FAILED),
        ("reference", outcomes.PASSED),
    ],
)
def test_lock_interleaving_repeated_counts(variant, expected, tmp_path, protocol_backend):
    manifest = ROOT / "tasks/fix-stale-lock-recovery/task.toml"
    stage(manifest, tmp_path)
    if variant == "alternative":
        (tmp_path / "target.py").write_text(alternative_correct_fix(manifest))
    elif variant != "original":
        (tmp_path / "target.py").write_bytes(
            (manifest.parent / f"expected/{variant}.py").read_bytes()
        )
    results = [grade(manifest, tmp_path).outcome for _ in range(10)]
    assert results == [expected] * 10


@pytest.mark.parametrize("manifest", TASKS, ids=lambda p: p.parent.name)
def test_correct_defect_fix_with_regression_fails(manifest, tmp_path, protocol_backend):
    stage(manifest, tmp_path)
    (tmp_path / "target.py").write_bytes(
        (manifest.parent / "expected/regression-broken.py").read_bytes()
    )
    cell = grade(manifest, tmp_path)
    assert cell.outcome == outcomes.FAILED
    assert checks(cell)["edited_only_allowed_files"]
    assert not checks(cell)["hidden_tests_pass"]


@pytest.mark.parametrize("manifest", TASKS, ids=lambda p: p.parent.name)
def test_small_diff_is_optional(manifest, tmp_path, protocol_backend):
    stage(manifest, tmp_path)
    reference = (manifest.parent / "expected/reference.py").read_text()
    (tmp_path / "target.py").write_text(reference + "\n# padding\n" * 70)
    cell = grade(manifest, tmp_path)
    assert cell.outcome == outcomes.PASSED
    assert not checks(cell)["small_diff"]
    assert cell.score == pytest.approx(2 / 3)


def test_unittest_tampering_is_rejected(tmp_path, protocol_backend):
    """Synthetic hostile bytes pin the known bypass; this proves no containment."""
    manifest = ROOT / "tasks/fix-nested-template-copy/task.toml"
    stage(manifest, tmp_path)
    target = tmp_path / "target.py"
    target.write_text(
        target.read_text()
        + "\nimport unittest\nunittest.TestCase.assertEqual = lambda *args, **kwargs: None\n"
    )
    cell = grade(manifest, tmp_path)
    assert checks(cell)["edited_only_allowed_files"]
    assert cell.outcome == outcomes.FAILED


def test_checkpoint_repair_requires_error_logging(tmp_path, protocol_backend):
    manifest = ROOT / "tasks/fix-corrupt-refresh-checkpoint/task.toml"
    stage(manifest, tmp_path)
    reference = (manifest.parent / "expected/reference.py").read_text()
    (tmp_path / "target.py").write_text(
        reference.replace('logging.error("quarantined corrupt cache %s", path.name)', "pass")
    )
    cell = grade(manifest, tmp_path)
    assert cell.outcome == outcomes.FAILED
    assert not checks(cell)["hidden_tests_pass"]


def test_checkpoint_old_literal_advice_fails_read_error_case(
    tmp_path, protocol_backend, monkeypatch
):
    from gym import hidden_tests

    manifest = ROOT / "tasks/fix-corrupt-refresh-checkpoint/task.toml"
    stage(manifest, tmp_path)
    (tmp_path / "target.py").write_bytes(
        (manifest.parent / "expected/old-literal-advice.py").read_bytes()
    )
    results = []
    run = hidden_tests.run

    def record(*args, **kwargs):
        result = run(*args, **kwargs)
        results.append(result)
        return result

    monkeypatch.setattr(hidden_tests, "run", record)
    cell = grade(manifest, tmp_path)
    assert cell.outcome == outcomes.FAILED
    assert checks(cell)["edited_only_allowed_files"]
    assert not checks(cell)["hidden_tests_pass"]
    assert [case["outcome"] for case in results[0]["audit_cases"]] == [
        "passed",
        "passed",
        "failed",
    ]


def test_staged_checkpoint_finding_explains_corruption_contract(tmp_path):
    manifest = ROOT / "tasks/fix-corrupt-refresh-checkpoint/task.toml"
    stage(manifest, tmp_path)
    finding = (tmp_path / "finding.md").read_text()
    assert (
        "Fixture contract: only decode, shape and version errors are corruption "
        "and get quarantined."
    ) in finding
    assert (
        "A read that fails with any other `OSError` (for example permission denied or EIO) "
        "must propagate unchanged, with no rename and no quarantine file."
    ) in finding
    assert "`FileNotFoundError` means no checkpoint." in finding


@pytest.fixture
def pinned_ruff():
    from regenerate_rfd_fix import RuffUnavailable, require_ruff

    try:
        return require_ruff()
    except RuffUnavailable as exc:
        pytest.skip(str(exc))


def test_checkpoint_generated_inputs_are_current(pinned_ruff):
    from regenerate_rfd_fix import regenerate

    assert regenerate(ROOT / "tasks/fix-corrupt-refresh-checkpoint") == []


@pytest.mark.parametrize(
    "nested_name",
    [
        "nested directory/support.py",
        'nested "quotes"/support.py',
        "nested $(touch injected)/support.py",
        "nested `touch injected`/support.py",
        "nested ’ unicode/support.py",
    ],
)
def test_regenerated_stage_copies_nested_inputs(tmp_path, pinned_ruff, nested_name):
    from regenerate_rfd_fix import regenerate

    task_dir = tmp_path / "task"
    shutil.copytree(ROOT / "tasks/fix-corrupt-refresh-checkpoint", task_dir)
    fixture = task_dir / "fixture"
    (fixture / nested_name).parent.mkdir()
    (fixture / "support.py").rename(fixture / nested_name)
    manifest_path = fixture / "staged-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][manifest["files"].index("support.py")] = nested_name
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    regenerate(task_dir, write=True)
    assert regenerate(task_dir) == []
    sandbox = tmp_path / "new sandbox"
    subprocess.run(
        ["bash", str(task_dir / "stages/stage-input.sh"), str(sandbox)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    assert not (tmp_path / "injected").exists()
    for name in [*manifest["files"], "staged-manifest.json"]:
        assert (sandbox / name).read_bytes() == (fixture / name).read_bytes()


@pytest.mark.parametrize("manifest", TASKS, ids=lambda p: p.parent.name)
@pytest.mark.parametrize(
    "variant, expected",
    [
        ("original", "failed"),
        ("insufficient", "failed"),
        ("reference", "passed"),
        ("alternative", "passed"),
    ],
)
def test_host_real_engine_variants(manifest, variant, expected, tmp_path, docker_backend):
    stage(manifest, tmp_path)
    if variant == "alternative":
        (tmp_path / "target.py").write_text(alternative_correct_fix(manifest))
    elif variant != "original":
        (tmp_path / "target.py").write_bytes(
            (manifest.parent / f"expected/{variant}.py").read_bytes()
        )
    assert grade(manifest, tmp_path).outcome == expected


@pytest.mark.parametrize(
    "hostile",
    [
        "import unittest; unittest.TestCase.assertEqual=lambda *a,**k:None",
        "print('GYM_RESULT {\"ok\":true}', flush=True)",
    ],
)
def test_host_real_engine_tampering(hostile, tmp_path, docker_backend):
    manifest = ROOT / "tasks/fix-nested-template-copy/task.toml"
    stage(manifest, tmp_path)
    path = tmp_path / "target.py"
    path.write_text(path.read_text() + "\n" + hostile + "\n")
    assert grade(manifest, tmp_path).outcome == (
        "integrity_hold" if "GYM_RESULT" in hostile else "failed"
    )
