"""HRF-05/HRX-03: checked-in hard-fix variants exercise the runner protocol, never containment.

No agent output uses protocol_backend here; every graded file is a checked-in
variant under expected/. Hidden execution uses only captured inputs.
"""

import json
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from gym import grader, outcomes
from gym.grader import Trajectory
from gym.hidden_tests import REGRESSION_SUBCHECKS
from gym.schema import load_task

ROOT = Path(__file__).resolve().parents[1]
TASKS = tuple(sorted((ROOT / "tasks").glob("hard-fix-*/task.toml")))
V3_TASKS = tuple(path for path in TASKS if path.parent.name.endswith("-v3"))
LEGACY_TASKS = tuple(path for path in TASKS if path not in V3_TASKS)
REGRESSION = tuple(path for path in TASKS if "-regression-test-" in path.parent.name)
NAMES = {path.parent.name for path in TASKS}

# Literal advice fixes the symptom the finding names and fails what the staged
# material says the recommendation breaks.
LITERAL_CASES = {
    "hard-fix-wrong-recommendation-retry-charge": ["passed", "failed", "passed"],
    "hard-fix-wrong-recommendation-config-reload": ["passed", "failed", "failed"],
    "hard-fix-two-findings-v2": ["passed", "failed", "failed"],
    "hard-fix-wrong-recommendation-retry-charge-v2": ["failed", "failed", "passed"],
}
LITERAL = tuple(path for path in TASKS if path.parent.name in LITERAL_CASES)

# HRX-03: a plausible fix that removes the reported symptom and breaks a
# contract the staged code states. The regression test must fail on it too.
PLAUSIBLE_CASES = {
    "hard-fix-regression-test-dedupe-v2": ["passed", "failed", "failed", "failed"],
    "hard-fix-regression-test-quiet-hours-v2": ["passed", "failed", "failed"],
}
PLAUSIBLE = tuple(path for path in TASKS if path.parent.name in PLAUSIBLE_CASES)
PLAUSIBLE_SUBCHECK = "regression_test_fails_on_plausible-wrong"

# The insufficient variant passes what it fixed and fails what it left.
INSUFFICIENT_CASES = {
    "hard-fix-cross-file": ["passed", "passed", "raised"],
    "hard-fix-cross-file-v2": ["passed", "passed", "raised"],
    "hard-fix-regression-test-dedupe": ["failed", "passed", "failed"],
    "hard-fix-regression-test-dedupe-v2": ["failed", "passed", "failed", "passed"],
    "hard-fix-regression-test-quiet-hours": ["failed", "passed", "failed"],
    "hard-fix-regression-test-quiet-hours-v2": ["failed", "passed", "failed"],
    "hard-fix-two-findings": ["passed", "failed", "failed"],
    "hard-fix-two-findings-v2": ["passed", "failed", "failed"],
    "hard-fix-wrong-recommendation-config-reload": ["passed", "passed", "failed"],
    "hard-fix-wrong-recommendation-retry-charge": ["passed", "passed", "failed"],
    "hard-fix-wrong-recommendation-retry-charge-v2": ["passed", "failed", "passed"],
}

# Tasks whose insufficient variant edits one of two editable source files.
TWO_FILE = (
    "hard-fix-cross-file",
    "hard-fix-cross-file-v2",
    "hard-fix-two-findings",
    "hard-fix-two-findings-v2",
    "hard-fix-cross-file-v3",
    "hard-fix-two-findings-v3",
)
TWO_CARDS = {
    "hard-fix-two-findings",
    "hard-fix-two-findings-v2",
    "hard-fix-two-findings-v3",
}

# (checked-in test, submission variant, regression subchecks it must fail)
REGRESSION_GRADES = [
    ("good.py", "reference", set()),
    ("good.py", "alternative", set()),
    (
        "asserts-nothing.py",
        "reference",
        {"regression_test_fails_on_original", "regression_test_fails_on_insufficient"},
    ),
    ("source-changed.py", "reference", {"regression_test_fails_on_insufficient"}),
    (
        "keyed-to-implementation.py",
        "alternative",
        {"regression_test_passes_on_reference"},
    ),
]


def ids(path):
    return path.parent.name


@pytest.mark.parametrize("variant", ["reference", "alternative"])
def test_dedupe_v2_keeps_one_aliased_address_and_all_unaddressed_rows(variant):
    namespace = runpy.run_path(
        str(
            ROOT
            / "tasks"
            / "hard-fix-regression-test-dedupe-v2"
            / "expected"
            / variant
            / "subscribers.py"
        )
    )
    addressed = {"email": " Ann@Example.com "}
    unaddressed = {"name": "anonymous"}
    rows = [addressed, addressed, unaddressed, unaddressed]
    kept = namespace["unique"](rows)
    assert len(kept) == 3
    assert kept[0] is addressed
    assert kept[1] is kept[2] is unaddressed
    assert addressed == {"email": " Ann@Example.com "}
    assert rows == [addressed, addressed, unaddressed, unaddressed]


def staged_manifest(manifest):
    return json.loads((manifest.parent / "fixture/staged-manifest.json").read_text())


def sources(manifest):
    """Editable code files: everything editable except the regression test."""
    path = load_task(manifest.parent).regression_test_path
    return [name for name in staged_manifest(manifest)["editable"] if name != path]


def stage(manifest, directory):
    subprocess.run(
        ["bash", str(manifest.parent / "stages/stage-input.sh"), str(directory)],
        check=True,
        capture_output=True,
    )
    return directory


def apply(manifest, directory, variant, test="good.py"):
    """Copy a checked-in variant's sources and, for regression tasks, a checked-in test."""
    for name in sources(manifest):
        shutil.copyfile(manifest.parent / "expected" / variant / name, directory / name)
    if manifest in REGRESSION and test is not None:
        shutil.copyfile(
            manifest.parent / "expected/regression-tests" / test,
            directory / load_task(manifest.parent).regression_test_path,
        )
    return directory


def grade(manifest, directory):
    return grader.grade(
        load_task(manifest.parent),
        Trajectory("succeeded", {"done": True}, sandbox_root=directory),
    )


def checks(cell):
    return {item["name"]: item["ok"] for item in cell.subchecks}


def recorded_cases(monkeypatch):
    from gym import hidden_tests

    results = []
    run = hidden_tests.run

    def record(*args, **kwargs):
        result = run(*args, **kwargs)
        results.append(result)
        return result

    monkeypatch.setattr(hidden_tests, "run", record)
    return results


def cards(manifest):
    finding = (manifest.parent / "fixture/finding.md").read_text()
    return [line for line in finding.splitlines() if line.startswith("- **")]


def test_hard_fix_contracts():
    assert len(TASKS) == 16
    assert len(LEGACY_TASKS) == 11
    assert len(V3_TASKS) == 5
    assert len(REGRESSION) == 6
    assert len(LITERAL) == 4
    assert set(TWO_FILE) | TWO_CARDS <= NAMES
    assert set(LITERAL_CASES) == {path.parent.name for path in LITERAL}
    assert set(PLAUSIBLE_CASES) == {path.parent.name for path in PLAUSIBLE}
    assert set(INSUFFICIENT_CASES) == {path.parent.name for path in LEGACY_TASKS}
    for manifest in LEGACY_TASKS:
        task = load_task(manifest.parent)
        assert task.suite == "hard-fix"
        ticket = "HRX-03" if manifest.parent.name.endswith("-v2") else "HRF-05"
        assert manifest.read_text().startswith(f"# Synthetic ({ticket})")
        provenance = (manifest.parent / "expected/PROVENANCE.md").read_text()
        assert f"Synthetic ({ticket})" in provenance
        assert task.regression_test_must_fail_variants == (
            ("plausible-wrong",) if manifest in PLAUSIBLE else ()
        )
        cases = json.loads((manifest.parent / task.hidden_tests_entrypoint).read_text())
        expected_tests = (
            4 if manifest.parent.name == "hard-fix-regression-test-dedupe-v2" else 3
        )
        assert len(cases) == task.hidden_tests_expected_tests == expected_tests
        staged = staged_manifest(manifest)
        assert set(task.capture_files) == {*staged["files"], "staged-manifest.json"}
        assert "test_visible.py" in staged["files"]
        assert "test_visible.py" not in staged["editable"]
        assert 1 <= len(sources(manifest)) <= 3
        assert (task.regression_test_path is not None) == (manifest in REGRESSION)
        variants = {
            path.name
            for path in (manifest.parent / "expected").iterdir()
            if path.is_dir()
        }
        expected = {"reference", "insufficient", "alternative"}
        if manifest in LITERAL:
            expected.add("literal-advice")
        if manifest in PLAUSIBLE:
            expected.add("plausible-wrong")
        if manifest in REGRESSION:
            expected.add("regression-tests")
        assert variants == expected
        for variant in expected - {"regression-tests"}:
            shipped = {
                path.name for path in (manifest.parent / "expected" / variant).iterdir()
            }
            assert shipped == set(sources(manifest)), (manifest.parent.name, variant)


@pytest.mark.parametrize("slug", TWO_FILE, ids=lambda slug: slug)
def test_insufficient_variant_fixes_only_one_file(slug):
    manifest = ROOT / "tasks" / slug / "task.toml"
    names = sources(manifest)
    assert len(names) == 2

    def changed(variant):
        return {
            name
            for name in names
            if (manifest.parent / "expected" / variant / name).read_bytes()
            != (manifest.parent / "fixture" / name).read_bytes()
        }

    assert len(changed("insufficient")) == 1
    assert changed("reference") == set(names)


def test_two_findings_task_stages_two_findings():
    for manifest in TASKS:
        expected = 2 if manifest.parent.name in TWO_CARDS else 1
        assert len(cards(manifest)) == expected, manifest.parent.name


@pytest.mark.parametrize("manifest", TASKS, ids=ids)
def test_original_fails(manifest, tmp_path, protocol_backend):
    cell = grade(manifest, stage(manifest, tmp_path))
    assert cell.outcome == outcomes.FAILED
    assert checks(cell)["edited_only_allowed_files"]
    assert not checks(cell)["hidden_tests_pass"]


@pytest.mark.parametrize("manifest", TASKS, ids=ids)
def test_insufficient_fails(manifest, tmp_path, protocol_backend):
    cell = grade(manifest, apply(manifest, stage(manifest, tmp_path), "insufficient"))
    assert cell.outcome == outcomes.FAILED
    assert checks(cell)["edited_only_allowed_files"]
    assert not checks(cell)["hidden_tests_pass"]


@pytest.mark.parametrize("variant", ["reference", "alternative"])
@pytest.mark.parametrize("manifest", TASKS, ids=ids)
def test_correct_fix_passes(manifest, variant, tmp_path, protocol_backend):
    cell = grade(manifest, apply(manifest, stage(manifest, tmp_path), variant))
    assert cell.outcome == outcomes.PASSED, cell.reason
    expected = {"edited_only_allowed_files", "hidden_tests_pass", "small_diff"}
    if manifest in REGRESSION:
        expected |= set(REGRESSION_SUBCHECKS.values())
    expected |= {
        f"regression_test_fails_on_{name}"
        for name in load_task(manifest.parent).regression_test_must_fail_variants
    }
    assert set(checks(cell)) == expected
    assert all(checks(cell).values())


@pytest.mark.parametrize("manifest", LITERAL, ids=ids)
def test_literal_advice_fails(manifest, tmp_path, protocol_backend, monkeypatch):
    results = recorded_cases(monkeypatch)
    cell = grade(manifest, apply(manifest, stage(manifest, tmp_path), "literal-advice"))
    assert cell.outcome == outcomes.FAILED
    assert checks(cell)["edited_only_allowed_files"]
    assert not checks(cell)["hidden_tests_pass"]
    assert [case["outcome"] for case in results[0]["audit_cases"]] == (
        LITERAL_CASES[manifest.parent.name]
    )


@pytest.mark.parametrize("manifest", PLAUSIBLE, ids=ids)
def test_plausible_wrong_fails(manifest, tmp_path, protocol_backend, monkeypatch):
    results = recorded_cases(monkeypatch)
    cell = grade(
        manifest, apply(manifest, stage(manifest, tmp_path), "plausible-wrong")
    )
    assert cell.outcome == outcomes.FAILED
    assert checks(cell)["edited_only_allowed_files"]
    assert not checks(cell)["hidden_tests_pass"]
    assert [case["outcome"] for case in results[0]["audit_cases"]] == (
        PLAUSIBLE_CASES[manifest.parent.name]
    )


@pytest.mark.parametrize("manifest", LEGACY_TASKS, ids=ids)
def test_insufficient_fails_a_hidden_case(
    manifest, tmp_path, protocol_backend, monkeypatch
):
    results = recorded_cases(monkeypatch)
    grade(manifest, apply(manifest, stage(manifest, tmp_path), "insufficient"))
    observed = [case["outcome"] for case in results[0]["audit_cases"]]
    assert observed == INSUFFICIENT_CASES[manifest.parent.name]


@pytest.mark.parametrize("manifest", TASKS, ids=ids)
def test_visible_test_edit_instead_of_fix_fails(manifest, tmp_path, protocol_backend):
    stage(manifest, tmp_path)
    (tmp_path / "test_visible.py").write_text("import unittest\n")
    cell = grade(manifest, tmp_path)
    assert cell.outcome == outcomes.FAILED
    assert not checks(cell)["edited_only_allowed_files"]
    assert not checks(cell)["hidden_tests_pass"]


def protected_inputs():
    for manifest in TASKS:
        staged = staged_manifest(manifest)
        for name in [*staged["files"], "staged-manifest.json"]:
            if name not in staged["editable"]:
                yield pytest.param(manifest, name, id=f"{manifest.parent.name}:{name}")


@pytest.mark.parametrize("manifest, protected", list(protected_inputs()))
def test_reference_cannot_edit_protected_input(
    manifest, protected, tmp_path, protocol_backend
):
    apply(manifest, stage(manifest, tmp_path), "reference")
    with (tmp_path / protected).open("a") as handle:
        handle.write("\n# unauthorized edit\n")
    cell = grade(manifest, tmp_path)
    assert cell.outcome == outcomes.FAILED
    assert not checks(cell)["edited_only_allowed_files"]


@pytest.mark.parametrize("manifest", TASKS, ids=ids)
def test_staging_and_visible_tests_on_every_variant(manifest, tmp_path):
    tmp_path = tmp_path / "workspace"
    stage(manifest, tmp_path)
    task = load_task(manifest.parent)
    assert {path.name for path in tmp_path.iterdir()} == set(task.capture_files)
    assert not (tmp_path / "hidden").exists()
    assert not (tmp_path / "expected").exists()
    variants = ["original", "insufficient", "reference", "alternative"]
    if manifest in LITERAL:
        variants.append("literal-advice")
    if manifest in PLAUSIBLE:
        variants.append("plausible-wrong")
    if manifest in V3_TASKS:
        variants.extend(
            v for v in wrong_variants(manifest)["fixes"] if v not in variants
        )
    for variant in variants:
        if variant != "original":
            apply(manifest, tmp_path, variant, test=None)
        result = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "test_visible"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (manifest.parent.name, variant, result.stderr)


@pytest.mark.parametrize("test, submission, failing", REGRESSION_GRADES)
@pytest.mark.parametrize("manifest", REGRESSION, ids=ids)
def test_checked_in_regression_tests(
    manifest, test, submission, failing, tmp_path, protocol_backend
):
    cell = grade(manifest, apply(manifest, stage(manifest, tmp_path), submission, test))
    assert checks(cell)["edited_only_allowed_files"]
    assert checks(cell)["hidden_tests_pass"]
    regression = {name: checks(cell)[name] for name in REGRESSION_SUBCHECKS.values()}
    extra = {
        f"regression_test_fails_on_{name}"
        for name in load_task(manifest.parent).regression_test_must_fail_variants
    }
    regression.update({name: checks(cell)[name] for name in extra})
    if test in {"asserts-nothing.py", "source-changed.py"}:
        failing = failing | extra
    assert {name for name, ok in regression.items() if not ok} == failing
    assert cell.outcome == (outcomes.FAILED if failing else outcomes.PASSED), (
        cell.reason
    )


@pytest.mark.parametrize("submission", ["reference", "alternative"])
@pytest.mark.parametrize("manifest", PLAUSIBLE, ids=ids)
def test_symptom_only_regression_test_misses_the_plausible_fix(
    manifest, submission, tmp_path, protocol_backend
):
    # A test of the reported symptom alone passes on the plausible-wrong fix.
    cell = grade(
        manifest,
        apply(manifest, stage(manifest, tmp_path), submission, "symptom-only.py"),
    )
    assert checks(cell)["edited_only_allowed_files"]
    assert checks(cell)["hidden_tests_pass"]
    assert {name for name, ok in checks(cell).items() if not ok} == {PLAUSIBLE_SUBCHECK}
    assert cell.outcome == outcomes.FAILED, cell.reason


@pytest.mark.parametrize("manifest", REGRESSION, ids=ids)
def test_sniffing_regression_test_is_held(manifest, tmp_path, protocol_backend):
    cell = grade(
        manifest, apply(manifest, stage(manifest, tmp_path), "reference", "sniffing.py")
    )
    assert cell.outcome == outcomes.INTEGRITY_HOLD


@pytest.mark.parametrize("manifest", REGRESSION, ids=ids)
def test_regression_test_without_the_fix_is_not_run(
    manifest, tmp_path, protocol_backend
):
    # A good test alone does not grade the test: the hidden cases gate it.
    cell = grade(manifest, apply(manifest, stage(manifest, tmp_path), "insufficient"))
    assert cell.outcome == outcomes.FAILED
    assert not any(checks(cell)[name] for name in REGRESSION_SUBCHECKS.values())


@pytest.mark.parametrize(
    "slug, path, evidence",
    [
        (
            "hard-fix-wrong-recommendation-retry-charge",
            "gateway.py",
            "Calls that share an idempotency_key create at most one",
        ),
        (
            "hard-fix-wrong-recommendation-config-reload",
            "daemon.py",
            "reload keeps the last good",
        ),
        ("hard-fix-cross-file", "sweep.py", "never deactivated"),
        (
            "hard-fix-regression-test-dedupe-v2",
            "newsletter.py",
            "exactly as the subscriber typed it",
        ),
        ("hard-fix-regression-test-quiet-hours-v2", "policy.py", "start == end"),
        ("hard-fix-two-findings-v2", "proxy.py", "smallest whole number of seconds"),
        (
            "hard-fix-wrong-recommendation-retry-charge-v2",
            "plans.py",
            "each payment is a separate charge",
        ),
        ("hard-fix-cross-file-v2", "settings.py", "directory:lookup"),
    ],
)
def test_correct_behaviour_is_staged(slug, path, evidence, tmp_path):
    # The fix the hidden cases demand is derivable from what the agent sees.
    manifest = ROOT / "tasks" / slug / "task.toml"
    stage(manifest, tmp_path)
    assert evidence in (tmp_path / path).read_text()


@pytest.fixture
def pinned_ruff():
    from regenerate_rfd_fix import RuffUnavailable, require_ruff

    try:
        return require_ruff()
    except RuffUnavailable as exc:
        pytest.skip(str(exc))


@pytest.mark.parametrize("manifest", TASKS, ids=ids)
def test_generated_inputs_are_current(manifest, pinned_ruff):
    from regenerate_hard_fix import regenerate

    assert regenerate(manifest.parent) == []


# HARDFIX-01: declarations are maintained with each task, never staged or
# inferred from the reference implementation. All grading uses the production
# grader, verifier and hidden runner with the synthetic protocol backend.
def wrong_variants(manifest):
    return json.loads((manifest.parent / "expected/must-fail.json").read_text())


V3_IDS = {
    "hard-fix-cross-file-v3",
    "hard-fix-regression-test-dedupe-v3",
    "hard-fix-regression-test-quiet-hours-v3",
    "hard-fix-two-findings-v3",
    "hard-fix-wrong-recommendation-retry-charge-v3",
}


def test_v3_discovery_and_pilot_membership():
    from gym.suite import discover_tasks
    import tomllib

    discovered = discover_tasks(ROOT / "tasks", suite="hard-fix")
    assert len(discovered) == 16
    assert {task.id for task in discovered if task.id.endswith("-v3")} == V3_IDS
    by_id = {task.id: task for task in discovered}
    for slug in V3_IDS:
        assert by_id[slug].task_version == 1
        assert slug.removesuffix("-v3") in by_id
        assert slug.removesuffix("-v3") + "-v2" in by_id
    battery = tomllib.loads((ROOT / "batteries/hrf-pilot.toml").read_text())
    selected = [
        task
        for task in discover_tasks(ROOT / "tasks")
        if task.suite in battery["suites"] and task.id not in battery["exclude_tasks"]
    ]
    models = sum(len(family["models"]) for family in battery["families"].values())
    assert (
        len(selected) * models * len(battery["levels"]) * battery["repetitions"]
        == battery["budget"]["max_cells"]
        == 144
    )
    assert V3_IDS <= set(battery["exclude_tasks"])


@pytest.mark.parametrize("manifest", V3_TASKS, ids=ids)
def test_v3_authoring_contract(manifest):
    task = load_task(manifest.parent)
    declaration = wrong_variants(manifest)
    assert len(declaration["fixes"]) >= 2
    assert set(declaration) == {"fixes", "tests"}
    assert set(task.regression_test_must_fail_variants) <= set(declaration["fixes"])
    if task.regression_test_path:
        assert len(task.regression_test_must_fail_variants) >= 2
        assert declaration["tests"]
    variants = {p.name for p in (manifest.parent / "expected").iterdir() if p.is_dir()}
    assert variants == {"reference", "alternative", *declaration["fixes"]} | (
        {"regression-tests"} if task.regression_test_path else set()
    )
    for variant in variants - {"regression-tests"}:
        assert {
            p.name for p in (manifest.parent / "expected" / variant).iterdir()
        } == set(sources(manifest))
    staged = staged_manifest(manifest)
    assert set(task.capture_files) == {*staged["files"], "staged-manifest.json"}
    assert "test_visible.py" not in staged["editable"]
    assert 1 <= len(sources(manifest)) <= 3
    assert task.hidden_tests_timeout_seconds == 20
    assert (
        len(json.loads((manifest.parent / task.hidden_tests_entrypoint).read_text()))
        == task.hidden_tests_expected_tests
    )


def v3_wrong_fixes():
    for manifest in V3_TASKS:
        for variant, caught in wrong_variants(manifest)["fixes"].items():
            yield pytest.param(
                manifest, variant, caught, id=f"{manifest.parent.name}:{variant}"
            )


@pytest.mark.parametrize("manifest, variant, caught", list(v3_wrong_fixes()))
def test_v3_declared_wrong_fix_fails_real_verifier(
    manifest, variant, caught, tmp_path, protocol_backend, monkeypatch
):
    results = recorded_cases(monkeypatch)
    cell = grade(manifest, apply(manifest, stage(manifest, tmp_path), variant))
    assert cell.outcome == outcomes.FAILED, cell.reason
    assert checks(cell)["edited_only_allowed_files"]
    assert not checks(cell)["hidden_tests_pass"]
    observed = results[0]["audit_cases"]
    assert len(observed) == load_task(manifest.parent).hidden_tests_expected_tests
    assert all(
        case["executed"] and case["outcome"] in {"passed", "failed", "raised"}
        for case in observed
    )
    assert caught and all(
        observed[index]["outcome"] in {"failed", "raised"} for index in caught
    )


def v3_wrong_tests():
    for manifest in V3_TASKS:
        for test, failing in wrong_variants(manifest)["tests"].items():
            yield pytest.param(
                manifest, test, failing, id=f"{manifest.parent.name}:{test}"
            )


@pytest.mark.parametrize("manifest, test, failing", list(v3_wrong_tests()))
@pytest.mark.parametrize("submission", ["reference", "alternative"])
def test_v3_declared_wrong_test_fails_real_verifier(
    manifest, test, failing, submission, tmp_path, protocol_backend
):
    cell = grade(manifest, apply(manifest, stage(manifest, tmp_path), submission, test))
    assert checks(cell)["edited_only_allowed_files"]
    assert checks(cell)["hidden_tests_pass"]
    assert {name for name, passed in checks(cell).items() if not passed} == set(failing)
    assert cell.outcome == outcomes.FAILED, cell.reason
