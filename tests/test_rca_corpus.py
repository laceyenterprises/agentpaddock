"""GYM-02 seeded RCA corpus: exact-match grading, red-herring rejection, and
staging isolation (expected/ never reaches the agent).

Each seeded task is graded through the real out-of-band grader (gym.grader.grade
runs the task's verifier.py in a scrubbed ephemeral env), so these tests exercise
the same path a live dispatch would.
"""

from __future__ import annotations

import subprocess

import pytest
from conftest import TASKS_DIR

import rca_from_postmortem as gen
from gym import outcomes
from gym.grader import Trajectory, grade
from gym.schema import load_task

# The three seeded incidents: correct closed-set answer, plus a red-herring enum
# value (a real wrong theory from the incident) with its plausible evidence, and
# a wrong-but-real evidence id for the correct root cause.
SEEDS = {
    "rca-fk-drain": {
        "correct_rc": "polymorphic-source-column-fk-mismatch",
        "correct_ev": "ev-703",
        "red_herring_rc": "adapter-runtime-id-uniqueness-violation",
        "red_herring_ev": "ev-705",
        "wrong_ev": "ev-701",
    },
    "rca-litellm-watchdog-restart-storm": {
        "correct_rc": "probe-substring-matches-healthy-banner",
        "correct_ev": "ev-801",
        "red_herring_rc": "fail-threshold-too-low",
        "red_herring_ev": "ev-803",
        "wrong_ev": "ev-804",
    },
    "rca-admit-pressure-crash-loop": {
        "correct_rc": "in-memory-only-admit-retry-lost-on-daemon-death",
        "correct_ev": "ev-901",
        "red_herring_rc": "daemon-oom-jetsam-kill",
        "red_herring_ev": "ev-904",
        "wrong_ev": "ev-903",
    },
}


def _grade(task, result):
    return grade(task, Trajectory(terminal_status="succeeded", submitted_result=result))


@pytest.mark.parametrize("task_id", sorted(SEEDS))
def test_seeded_verifier_passes_on_exact_answer(task_id):
    seed = SEEDS[task_id]
    task = load_task(TASKS_DIR / task_id)
    cell = _grade(task, {"root_cause": seed["correct_rc"], "evidence_id": seed["correct_ev"]})
    assert cell.outcome == outcomes.PASSED, cell.reason


@pytest.mark.parametrize("task_id", sorted(SEEDS))
def test_seeded_verifier_rejects_red_herring(task_id):
    seed = SEEDS[task_id]
    task = load_task(TASKS_DIR / task_id)
    # A planted red-herring enum value (in the closed set) with plausible evidence
    # is selectable and grades failed — not format_failed.
    cell = _grade(
        task, {"root_cause": seed["red_herring_rc"], "evidence_id": seed["red_herring_ev"]}
    )
    assert cell.outcome == outcomes.FAILED, cell.reason


@pytest.mark.parametrize("task_id", sorted(SEEDS))
def test_seeded_verifier_rejects_wrong_evidence(task_id):
    seed = SEEDS[task_id]
    task = load_task(TASKS_DIR / task_id)
    # Correct root cause, wrong (but real) evidence id -> failed.
    cell = _grade(task, {"root_cause": seed["correct_rc"], "evidence_id": seed["wrong_ev"]})
    assert cell.outcome == outcomes.FAILED, cell.reason


@pytest.mark.parametrize("task_id", sorted(SEEDS))
def test_seeded_verifier_rejects_missing_evidence(task_id):
    seed = SEEDS[task_id]
    task = load_task(TASKS_DIR / task_id)
    # evidence_id is optional at the schema layer so a missing citation reaches
    # the verifier, which is the single grading authority and fails it.
    cell = _grade(task, {"root_cause": seed["correct_rc"]})
    assert cell.outcome == outcomes.FAILED, cell.reason
    assert "evidence" in (cell.reason or "").lower()


@pytest.mark.parametrize("task_id", sorted(SEEDS))
def test_seeded_out_of_enum_is_format_failed(task_id):
    task = load_task(TASKS_DIR / task_id)
    # A root cause outside the closed set is a format failure, never a silent pass.
    cell = _grade(task, {"root_cause": "not-a-real-enum-value", "evidence_id": "ev-1"})
    assert cell.outcome == outcomes.FORMAT_FAILED, cell.reason


REVISED_EVIDENCE = {
    "rca-fk-drain": (("ev-703", "ev-704"), ("ev-701", "ev-702", "ev-705", "ev-706")),
    "rca-admit-pressure-crash-loop": (("ev-901", "ev-902"), ("ev-903", "ev-904", "ev-905")),
}


@pytest.mark.parametrize(
    "task_id,evidence,accepted",
    [
        (task_id, evidence, accepted)
        for task_id, (valid, rejected) in REVISED_EVIDENCE.items()
        for accepted, ids in ((True, valid), (False, rejected))
        for evidence in ids
    ],
)
@pytest.mark.parametrize("correct_root", [True, False])
def test_revised_rca_required_subchecks(task_id, evidence, accepted, correct_root):
    seed = SEEDS[task_id]
    task = load_task(TASKS_DIR / task_id)
    # Exercise every wrong enum against every accepted and rejected evidence id.
    roots = (
        [seed["correct_rc"]]
        if correct_root
        else [
            root
            for root in next(
                field for field in task.result_schema if field.name == "root_cause"
            ).enum
            if root != seed["correct_rc"]
        ]
    )
    for root in roots:
        cell = _grade(task, {"root_cause": root, "evidence_id": evidence})
        expected = outcomes.PASSED if correct_root and accepted else outcomes.FAILED
        assert cell.outcome == expected, cell.reason
        assert [(check["name"], check["ok"], check["required"]) for check in cell.subchecks] == [
            ("root_cause", correct_root, True),
            ("evidence", accepted, True),
        ]
        assert cell.score == (int(correct_root) + int(accepted)) / 2


def _discover_rca_tasks():
    return sorted(p for p in TASKS_DIR.glob("rca-*") if (p / "task.toml").is_file())


def _stage_into(task, sandbox):
    """Run every stage script into ``sandbox`` and return the staged file paths."""
    for stage in task.stages:
        script = task.task_dir / stage.run
        subprocess.run([str(script), str(sandbox)], check=True)
    return [p for p in sandbox.rglob("*") if p.is_file()]


def test_all_seeded_rca_tasks_discovered():
    ids = {p.name for p in _discover_rca_tasks()}
    assert set(SEEDS).issubset(ids)


@pytest.mark.parametrize("task_dir", _discover_rca_tasks(), ids=lambda p: p.name)
def test_staging_isolation_never_leaks_expected(task_dir, tmp_path):
    task = load_task(task_dir)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    staged_files = _stage_into(task, sandbox)
    assert staged_files, f"{task.id} staged nothing"

    # What the agent can see: everything the stages wrote + the dispatch prompt.
    prompt = (task_dir / "prompt.md").read_text(encoding="utf-8")
    staged_blobs = {p: p.read_text(encoding="utf-8", errors="replace") for p in staged_files}
    staged_blobs[task_dir / "prompt.md"] = prompt

    # 1. No staged file is (or is under) expected/, and the verifier is not staged.
    for path in staged_files:
        parts = set(path.relative_to(sandbox).parts)
        assert "expected" not in parts, f"{path} lives under expected/"
        assert path.name != "verifier.py", f"verifier {path} was staged"

    # 2. The ground-truth leak tripwire never appears in anything the agent sees.
    for path, blob in staged_blobs.items():
        assert gen.GROUND_TRUTH_MARKER not in blob, f"ground-truth marker leaked into {path}"

    # 3. The full expected/ground_truth.json bytes never appear in a staged file.
    gt_path = task_dir / "expected" / "ground_truth.json"
    assert gt_path.is_file()  # ground truth IS on disk, just never staged
    gt_bytes = gt_path.read_text(encoding="utf-8")
    for path, blob in staged_blobs.items():
        assert gt_bytes not in blob, f"ground-truth file content leaked into {path}"


def test_staging_isolation_seeded_tasks_are_answerable(tmp_path):
    # Sanity: the correct evidence id DOES appear in the staged artifact (so the
    # agent can cite it) — the marker check above is not vacuously true because
    # the artifact legitimately carries evidence ids, just not the answer key.
    for task_id, seed in SEEDS.items():
        task = load_task(TASKS_DIR / task_id)
        sandbox = tmp_path / task_id
        sandbox.mkdir()
        staged = "\n".join(
            p.read_text(encoding="utf-8", errors="replace") for p in _stage_into(task, sandbox)
        )
        assert seed["correct_ev"] in staged
        assert seed["red_herring_ev"] in staged
