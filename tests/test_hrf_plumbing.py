"""HRF-01: hard suites and fix-family parity.

A hard-fix task runs agent code, so it must get every safeguard a fix task
gets: the hidden-test permission, the activation seam, Docker containment, the
original-bytes snapshot, integrity_hold and the sealed fix_audit envelope. The
synthetic hard-fix task lives in tests/fixtures/, never in tasks/.
"""

import ast
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from gym import cli, fix_audit, grader, outcomes
from gym.dispatch import WorkerPoolTrajectorySource
from gym.grader import GraderError, Trajectory
from gym.schema import (
    FIX_FAMILY_SUITES,
    SUITES,
    TaskSchemaError,
    is_fix_family,
    load_task,
    parse_task_dict,
)
from gym.suite import SELECTABLE_SUITES, discover_tasks, run_suite

ROOT = Path(__file__).resolve().parents[1]
GYM_PACKAGE = ROOT / "lib/python/gym"
PROBE = ROOT / "tests/fixtures/hard-fix-probe"
RFD_REVIEW = [
    "review-clean-retention-plan",
    "review-corrupt-refresh-checkpoint",
    "review-publish-guard-order",
    "review-rescue-local-tip",
    "review-retry-empty-page",
    "review-stale-lock-recovery",
]
RFD_FIX = [
    "fix-corrupt-refresh-checkpoint",
    "fix-nested-template-copy",
    "fix-publish-guard-order",
    "fix-rescue-local-tip",
    "fix-retry-empty-page",
    "fix-stale-lock-recovery",
]


def probe(suite="hard-fix"):
    """The synthetic hard-fix task, optionally re-labelled for a parity run."""
    task = load_task(PROBE)
    assert task.suite == "hard-fix"
    return task if suite == "hard-fix" else replace(task, suite=suite)


def stage_probe(directory, target=None):
    for name in ("target.py", "staged-manifest.json"):
        shutil.copyfile(PROBE / "fixture" / name, directory / name)
    if target is not None:
        (directory / "target.py").write_text(target)
    return directory


def reference():
    return (PROBE / "expected/reference.py").read_text()


class Source:
    def __init__(self, sandbox_root, transcript_rows=()):
        self.sandbox_root = sandbox_root
        self.transcript_rows = list(transcript_rows)
        self.dispatched = []

    def run(self, task):
        self.dispatched.append(task.id)
        return Trajectory(
            "succeeded",
            {"done": True},
            transcript_rows=self.transcript_rows,
            sandbox_root=self.sandbox_root,
            launch_request_id=f"{task.id}-launch",
        )


# --- the predicate and its guard ---------------------------------------------


def test_fix_family_is_exactly_fix_and_hard_fix():
    assert FIX_FAMILY_SUITES == {"fix", "hard-fix"}
    assert FIX_FAMILY_SUITES <= SUITES
    assert {suite for suite in SUITES if is_fix_family(suite)} == {"fix", "hard-fix"}
    for suite in (None, "all", "hard-review", "review", "docs", "Fix", "fix "):
        assert not is_fix_family(suite)


def _literal_suite_names(node):
    """Suite-name string constants compared in ``node`` (operands and containers)."""
    operands = [node.left, *node.comparators]
    names = []
    for operand in operands:
        items = operand.elts if isinstance(operand, (ast.Tuple, ast.List, ast.Set)) else [operand]
        for item in items:
            if isinstance(item, ast.Constant) and item.value in FIX_FAMILY_SUITES:
                names.append(item.value)
    return names


def test_no_literal_fix_suite_comparison_outside_is_fix_family():
    offenders = []
    for path in sorted(GYM_PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        allowed = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "is_fix_family":
                allowed.update(id(child) for child in ast.walk(node))
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare) and id(node) not in allowed:
                names = _literal_suite_names(node)
                if names:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {names}")
    assert offenders == [], "route fix-only rules through gym.schema.is_fix_family"


@pytest.mark.parametrize(
    "source",
    [
        'if task.suite == "fix":\n    pass\n',
        "if 'fix' != suite:\n    pass\n",
        'if t.suite in ("fix", "hard-fix"):\n    pass\n',
        'ok = suite == "hard-fix"\n',
    ],
)
def test_guard_detects_literal_comparisons(source):
    compares = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Compare)]
    assert any(_literal_suite_names(node) for node in compares)


# --- schema --------------------------------------------------------------------


def _hidden_task(suite):
    return {
        "id": "hidden-probe",
        "suite": suite,
        "result_schema": {"ok": {"type": "boolean"}},
        "verifier": {},
        "hidden_tests": {"entrypoint": "hidden/cases.json", "expected_tests": 1},
    }


@pytest.mark.parametrize("suite", sorted(FIX_FAMILY_SUITES))
def test_fix_family_may_declare_hidden_tests(suite):
    assert parse_task_dict(_hidden_task(suite)).hidden_tests_expected_tests == 1


@pytest.mark.parametrize("suite", sorted(SUITES - FIX_FAMILY_SUITES))
def test_other_suites_may_not_declare_hidden_tests(suite):
    with pytest.raises(TaskSchemaError, match="requires suite"):
        parse_task_dict(_hidden_task(suite))


def test_hard_review_hidden_tests_refused():
    with pytest.raises(TaskSchemaError, match="hard-review"):
        parse_task_dict(_hidden_task("hard-review"))


# --- selection -----------------------------------------------------------------


def test_new_suites_are_selectable():
    assert {"hard-review", "hard-fix"} <= SELECTABLE_SUITES
    for argv in (["run"], ["lock", "check"]):
        for suite in ("hard-review", "hard-fix"):
            assert cli.build_parser().parse_args([*argv, "--suite", suite]).suite == suite
    usage = (ROOT / "bin/hq-gym").read_text()
    assert usage.count("review|fix|docs|hard-review|hard-fix|all") == 2


def test_rfd_suites_still_select_the_six_rfd_tasks():
    tasks = ROOT / "tasks"
    assert [t.id for t in discover_tasks(tasks, suite="review")] == RFD_REVIEW
    assert [t.id for t in discover_tasks(tasks, suite="fix")] == RFD_FIX


def test_hard_suites_select_only_their_own_tasks(tmp_path):
    for name in [*RFD_REVIEW, *RFD_FIX]:
        shutil.copytree(ROOT / "tasks" / name, tmp_path / name)
    shutil.copytree(PROBE, tmp_path / "hard-fix-probe")
    (tmp_path / "hard-review-probe").mkdir()
    (tmp_path / "hard-review-probe/task.toml").write_text(
        'id = "hard-review-probe"\nsuite = "hard-review"\n'
        '[result_schema.answer]\ntype = "string"\n[verifier]\n'
    )
    (tmp_path / "hard-review-probe/verifier.py").write_text("pass\n")

    assert [t.id for t in discover_tasks(tmp_path, suite="hard-review")] == ["hard-review-probe"]
    assert [t.id for t in discover_tasks(tmp_path, suite="hard-fix")] == ["hard-fix-probe"]
    assert [t.id for t in discover_tasks(tmp_path, suite="review")] == RFD_REVIEW
    assert [t.id for t in discover_tasks(tmp_path, suite="fix")] == RFD_FIX
    everything = {t.id for t in discover_tasks(tmp_path, suite="all")}
    assert everything == {*RFD_REVIEW, *RFD_FIX, "hard-review-probe", "hard-fix-probe"}


# --- activation seam -------------------------------------------------------------


@pytest.mark.parametrize("suite", sorted(FIX_FAMILY_SUITES))
def test_activation_seam_holds_fix_family(monkeypatch, tmp_path, suite):
    (tmp_path / "workers").mkdir()
    source = WorkerPoolTrajectorySource(hq_bin="hq", hq_root=tmp_path)
    monkeypatch.setattr(source, "_dispatch", lambda task: pytest.fail("must not dispatch"))
    monkeypatch.setattr("gym.hidden_tests.FIX_ACTIVATION_ENABLED", False)
    with pytest.raises(GraderError, match="integrity acceptance suspended"):
        source.run(probe(suite))

    result = run_suite([probe(suite)], source, taint_mode="enforce")
    [cell] = result.cells
    assert cell.outcome == outcomes.ERROR
    assert "integrity acceptance suspended" in cell.reason
    assert cell.fix_audit is not None


@pytest.mark.parametrize("suite", ["hard-review", "review"])
def test_activation_seam_ignores_non_fix_family(monkeypatch, tmp_path, suite):
    (tmp_path / "workers").mkdir()
    source = WorkerPoolTrajectorySource(hq_bin="hq", hq_root=tmp_path)
    seen = []

    def dispatch(task):
        seen.append(task.suite)
        raise RuntimeError("synthetic dispatch sentinel")

    monkeypatch.setattr(source, "_dispatch", dispatch)
    monkeypatch.setattr("gym.hidden_tests.FIX_ACTIVATION_ENABLED", False)
    result = source.run(replace(probe(), suite=suite, hidden_tests_entrypoint=None))
    assert seen == [suite]
    assert result.error == "synthetic dispatch sentinel"


# --- original-bytes snapshot ------------------------------------------------------


@pytest.mark.parametrize("suite", sorted(FIX_FAMILY_SUITES))
def test_original_bytes_snapshotted_for_fix_family(suite):
    snapshot = grader.snapshot_verifiers([probe(suite)])
    for name in ("target.py", "staged-manifest.json"):
        path = PROBE / "fixture" / name
        assert snapshot[path] == path.read_bytes()


def test_original_bytes_not_snapshotted_outside_fix_family():
    snapshot = grader.snapshot_verifiers([probe("hard-review")])
    assert not any("fixture" in path.parts for path in snapshot)


# --- sealed fix_audit envelope -----------------------------------------------------


@pytest.mark.parametrize("suite", sorted(FIX_FAMILY_SUITES))
def test_success_seals_audit_envelope(tmp_path, protocol_backend, audit_keys, suite):
    target = stage_probe(tmp_path, reference()) / "target.py"
    source = Source(tmp_path)
    cell = grader.grade_task(probe(suite), source)
    assert source.dispatched == ["hard-fix-probe"]
    assert cell.outcome == outcomes.PASSED, cell.reason
    plain = fix_audit.decrypt(cell.fix_audit, *map(str, audit_keys))
    assert plain["verdict"] == outcomes.PASSED
    assert plain["task"] == "hard-fix-probe"
    assert plain["launch_request_id"] == "hard-fix-probe-launch"
    assert plain["files"][0]["submitted"] == target.read_text()
    assert "-    return sum(values[1:])" in plain["files"][0]["diff"]
    assert [case["outcome"] for case in plain["cases"]] == ["passed"]
    # Hidden expectations never reach the envelope.
    assert '"ok": 9' not in json.dumps(plain)


def test_original_submission_fails_with_envelope(tmp_path, protocol_backend, audit_keys):
    cell = grader.grade_task(probe(), Source(stage_probe(tmp_path)))
    assert cell.outcome == outcomes.FAILED
    assert {c["name"]: c["ok"] for c in cell.subchecks} == {
        "edited_only_allowed_files": True,
        "hidden_tests_pass": False,
    }
    plain = fix_audit.decrypt(cell.fix_audit, *map(str, audit_keys))
    assert plain["verdict"] == outcomes.FAILED


@pytest.mark.parametrize("suite", sorted(FIX_FAMILY_SUITES))
def test_integrity_hold_and_held_verdict_sealed(tmp_path, protocol_backend, audit_keys, suite):
    stage_probe(tmp_path, reference())
    source = Source(tmp_path, transcript_rows=[{"text": "read /proc/self nonce"}])
    cell = grader.grade_task(probe(suite), source)
    assert cell.outcome == outcomes.INTEGRITY_HOLD
    assert cell.integrity_flags == ["nonce", "/proc/self"]
    assert cell.held_verdict["verdict"] == outcomes.PASSED
    plain = fix_audit.decrypt(cell.fix_audit, *map(str, audit_keys))
    assert plain["integrity_flags"] == cell.integrity_flags
    assert plain["held_verdict"] == cell.held_verdict


def test_integrity_scan_not_applied_outside_fix_family():
    task = load_task(ROOT / "tests/fixtures/mini-suite/mini-pass")
    task = replace(task, suite="hard-review")
    source = type(
        "Source",
        (),
        {
            "run": lambda self, task: Trajectory(
                "succeeded", {"answer": "ok"}, transcript_rows=[{"text": "nonce"}]
            )
        },
    )()
    cell = grader.grade_task(task, source)
    assert cell.outcome == outcomes.PASSED
    assert cell.fix_audit is None


@pytest.mark.parametrize("suite", sorted(FIX_FAMILY_SUITES))
def test_grading_error_seals_incomplete_envelope(tmp_path, monkeypatch, audit_keys, suite):
    stage_probe(tmp_path, reference())

    def broken(*args, **kwargs):
        raise GraderError("synthetic verifier failure")

    monkeypatch.setattr(grader, "_run_verifier_out_of_band", broken)
    result = run_suite([probe(suite)], Source(tmp_path), taint_mode="enforce")
    [cell] = result.cells
    assert cell.outcome == outcomes.ERROR
    assert "synthetic verifier failure" in cell.reason
    plain = fix_audit.decrypt(cell.fix_audit, *map(str, audit_keys))
    assert plain["verdict"] == "error"
    assert plain["incomplete"] is True
    assert plain["task"] == "hard-fix-probe"
    assert len(plain["cases"]) == 1


@pytest.mark.parametrize("suite", sorted(FIX_FAMILY_SUITES))
def test_dispatch_error_seals_envelope(audit_keys, suite):
    class Failing:
        def run(self, task):
            raise RuntimeError("synthetic dispatch failure")

    result = run_suite([probe(suite)], Failing(), taint_mode="enforce")
    [cell] = result.cells
    assert cell.outcome == outcomes.ERROR
    plain = fix_audit.decrypt(cell.fix_audit, *map(str, audit_keys))
    assert plain == {
        "version": 1,
        "task": "hard-fix-probe",
        "files": [],
        "cases": [],
        "incomplete": True,
        "verdict": "error",
    }


@pytest.mark.parametrize("suite", sorted(FIX_FAMILY_SUITES))
def test_missing_recipient_prevents_dispatch(tmp_path, monkeypatch, suite):
    monkeypatch.delenv("GYM_FIX_AUDIT_CERT", raising=False)
    monkeypatch.setattr("agent_os_config.roots.hq_root", lambda: tmp_path)
    source = type("Source", (), {"run": lambda *_: pytest.fail("must not dispatch")})()
    with pytest.raises(GraderError, match="operator-provisioned"):
        grader.grade_task(probe(suite), source)
    with pytest.raises(GraderError, match="operator-provisioned"):
        run_suite([probe(suite)], source, taint_mode="enforce")


# --- containment -----------------------------------------------------------------


def test_hard_fix_hidden_cases_run_in_real_containment(tmp_path, docker_backend):
    from gym import hidden_tests

    task = probe()
    stage_probe(tmp_path, reference())
    files = grader.snapshot_verifiers([task])
    assert hidden_tests.run(task, tmp_path, files)["status"] == "passed"
    # The agent's code cannot reach the network from inside the container.
    contained = (
        "import socket\ns=socket.socket()\ntry:\n s.connect(('1.1.1.1',443)); result=False\n"
        "except OSError:\n result=True\nfinally:\n s.close()"
    )
    files[PROBE / task.hidden_tests_entrypoint] = json.dumps(
        [{"call": contained, "expected": {"ok": True}}]
    ).encode()
    assert hidden_tests.run(task, tmp_path, files)["status"] == "passed"


def test_hidden_runner_refuses_non_fix_family(tmp_path):
    from gym import hidden_tests

    with pytest.raises(GraderError, match="fix-family"):
        hidden_tests.run(probe("hard-review"), tmp_path, {})
