"""HRX-04: the HRX activation file validates and its probe grades a recorded sample."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytest.importorskip(
    "cwp_dispatch.activation_contract", reason="host activation contract is not shipped"
)

from cwp_dispatch.activation_contract import (
    evaluate_output,
    parse_activation_yaml_document,
    validate_pack,
    validate_schema_runtime_contract,
)
from gym.suite import discover_tasks
from gym_test_support import checkout_root

MODULE = Path(__file__).resolve().parents[1]
REPO = checkout_root(MODULE)
ACTIVATION = REPO / "projects/gym-hard-review-fix-rework/activation"
SPEC_REF = json.loads((ACTIVATION.parent / "plan.json").read_text())["specRef"]
DOCUMENT = yaml.safe_load((ACTIVATION / "hrx.yaml").read_text())
ASSERTIONS = parse_activation_yaml_document(
    DOCUMENT, expected_spec_ref=SPEC_REF
).assertions
# HRX-02 adds three hard-review -v2 tasks and HRX-03 five hard-fix -v2 tasks.
REWORKED = {
    "hard-review": [
        "hard-review-cross-file-v2",
        "hard-review-multi-defect-outbox-v2",
        "hard-review-multi-defect-tool-calls-v2",
    ],
    "hard-fix": [
        "hard-fix-cross-file-v2",
        "hard-fix-regression-test-dedupe-v2",
        "hard-fix-regression-test-quiet-hours-v2",
        "hard-fix-two-findings-v2",
        "hard-fix-wrong-recommendation-retry-charge-v2",
    ],
}
MUST_FAIL = {
    "hard-fix-regression-test-dedupe-v2": ["plausible-wrong"],
    "hard-fix-regression-test-quiet-hours-v2": ["plausible-wrong"],
}


def probe_program():
    return ASSERTIONS[0].command.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


def sample():
    return (ACTIVATION / "samples/discovery.json").read_text()


@pytest.fixture
def probe_roots(monkeypatch, tmp_path):
    from agent_os_config import roots

    monkeypatch.setenv("AGENT_OS_ROOT", str(REPO))
    monkeypatch.setenv("AGENT_OS_ROOTS_HQ", str(tmp_path / "shared-hq"))
    roots.reset_cache()
    assert roots.deploy_root() == str(REPO)


# --- schema ----------------------------------------------------------------------


def test_activation_schema_and_pinned_interpreter():
    assert [assertion.id for assertion in ASSERTIONS] == ["hrx-v2-task-discovery"]
    assertion = ASSERTIONS[0]
    assert assertion.kind == "probe"
    assert assertion.completed_spec_ref == SPEC_REF
    assert validate_schema_runtime_contract(assertion).ok
    compile(probe_program(), "hrx.yaml", "exec")


def test_pack_validates_beside_plan():
    # The PMA reads every activation/*.yaml beside plan.json against its specRef.
    ids = [
        assertion.id
        for assertion in validate_pack(
            REPO, "projects/gym-hard-review-fix-rework/plan.json"
        )
    ]
    assert ids == ["hrx-v2-task-discovery"]


# --- discovery probe -------------------------------------------------------------


def test_probe_executes_real_root_resolver(probe_roots, tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", probe_program()],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(REPO / "modules/worker-pool/lib/python")},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == json.loads(sample())
    assert evaluate_output(ASSERTIONS[0], result.stdout).passed


def test_probe_bootstrap_without_config_on_pythonpath(probe_roots):
    # PMA exports the worker-pool package; the helper must locate config itself.
    # Disable site hooks so an editable config install cannot mask a broken bootstrap.
    bootstrap = probe_program().split("\nfrom gym.suite", 1)[0]
    result = subprocess.run(
        [sys.executable, "-S", "-c", bootstrap + "\nprint(module)"],
        cwd="/",
        env={**os.environ, "PYTHONPATH": str(REPO / "modules/worker-pool/lib/python")},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(MODULE)


@pytest.mark.parametrize("suite", sorted(REWORKED))
def test_actual_discovery_matches_recorded_sample(suite):
    discovered = [
        task.id
        for task in discover_tasks(MODULE / "tasks", suite=suite)
        if task.id.endswith("-v2")
    ]
    assert discovered == json.loads(sample())[suite] == REWORKED[suite]


@pytest.mark.parametrize("suite", sorted(REWORKED))
def test_each_reworked_task_keeps_its_hrf_original(suite):
    # New ids, not replacements: the saturated HRF task stays beside its -v2.
    ids = {task.id for task in discover_tasks(MODULE / "tasks", suite=suite)}
    assert {task_id.removesuffix("-v2") for task_id in REWORKED[suite]} <= ids


def test_must_fail_variants_match_recorded_sample():
    declared = {
        task.id: list(task.regression_test_must_fail_variants)
        for task in discover_tasks(MODULE / "tasks", suite="hard-fix")
        if task.regression_test_must_fail_variants
    }
    assert declared == json.loads(sample())["must_fail_variants"] == MUST_FAIL


def test_recorded_expectation_holds():
    assert evaluate_output(ASSERTIONS[0], sample()).passed


def _drifted(edit):
    report = json.loads(sample())
    edit(report)
    return json.dumps(report, sort_keys=True)


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param(
            lambda report: report["hard-review"].pop(), id="missing-review-v2"
        ),
        pytest.param(lambda report: report["hard-fix"].pop(0), id="missing-fix-v2"),
        pytest.param(
            lambda report: report["hard-fix"].append("hard-fix-extra-v2"),
            id="extra-fix-v2",
        ),
        pytest.param(
            lambda report: report["must_fail_variants"].pop(
                "hard-fix-regression-test-dedupe-v2"
            ),
            id="must-fail-variants-dropped",
        ),
        pytest.param(
            lambda report: report["must_fail_variants"].clear(),
            id="must-fail-variants-absent",
        ),
    ],
)
def test_discovery_drift_fails(edit):
    assert not evaluate_output(ASSERTIONS[0], _drifted(edit)).passed
