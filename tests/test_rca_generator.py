"""GYM-02 generator: scaffold shape + fail-closed placeholder.

Running scripts/rca_from_postmortem.py against an incident doc must produce the
GYM-01 task skeleton — a task.toml with a PLACEHOLDER closed-set enum, a verifier
stub that fails closed, a staged-artifact dir, and an expected/ placeholder
marked for operator fill-in — and never the answer.
"""

from __future__ import annotations

import json

import pytest
from conftest import REPO_ROOT

import rca_from_postmortem as gen
from gym import outcomes
from gym.grader import Trajectory, grade
from gym.schema import load_task

SEED_DOC = (
    REPO_ROOT / "docs" / "POSTMORTEM-litellm-watchdog-restart-storm-2026-06-01.md"
)


@pytest.fixture
def seed_doc(tmp_path):
    doc = tmp_path / "docs" / SEED_DOC.name
    doc.parent.mkdir()
    doc.write_text("# Test incident\n\nA synthetic incident for scaffolding.\n")
    return doc


def test_derive_task_id_strips_prefix_and_date():
    assert gen.derive_task_id(SEED_DOC) == "rca-litellm-watchdog-restart-storm"
    assert (
        gen.derive_task_id(
            REPO_ROOT / "docs" / "SPEC-session-ledger-fk-recovery-2026-06-04.md"
        )
        == "rca-session-ledger-fk-recovery"
    )


def test_scaffold_produces_skeleton_shape(tmp_path, seed_doc):
    assert seed_doc.is_file()
    task_dir = gen.scaffold(
        seed_doc,
        tmp_path,
        "rca-scaffold-probe",
        force=False,
        repo_root=seed_doc.parents[1],
    )

    # Every skeleton file lands.
    assert (task_dir / "task.toml").is_file()
    assert (task_dir / "verifier.py").is_file()
    assert (task_dir / "prompt.md").is_file()
    assert (task_dir / "stages" / "stage-input.sh").is_file()
    assert (task_dir / "expected" / "ground_truth.json").is_file()

    # It parses on the GYM-01 schema: a closed-set root_cause enum + evidence id.
    task = load_task(task_dir)
    assert task.id == "rca-scaffold-probe"
    assert task.suite == "rca"
    rc = task.field_spec("root_cause")
    assert rc is not None and rc.type == "enum"
    assert task.field_spec("evidence_id") is not None

    # The enum is a PLACEHOLDER — the operator must replace it.
    assert all(gen.PLACEHOLDER in value for value in rc.enum)

    # The verifier is a fail-closed stub, and the source doc is cited for the maintainer.
    verifier_src = (task_dir / "verifier.py").read_text()
    assert "SCAFFOLD" in verifier_src
    toml_src = (task_dir / "task.toml").read_text()
    assert "docs/POSTMORTEM-litellm-watchdog-restart-storm-2026-06-01.md" in toml_src

    # expected/ is a TODO placeholder marked for fill-in, carrying the leak tripwire.
    gt = (task_dir / "expected" / "ground_truth.json").read_text()
    assert "TODO" in gt
    assert gen.GROUND_TRUTH_MARKER in gt


def test_scaffold_fails_closed_until_filled_in(tmp_path, seed_doc):
    # A freshly scaffolded task must NOT grade passed even if the agent submits a
    # placeholder enum value — the verifier stub short-circuits to failed.
    task_dir = gen.scaffold(
        seed_doc,
        tmp_path,
        "rca-scaffold-probe",
        force=False,
        repo_root=seed_doc.parents[1],
    )
    task = load_task(task_dir)
    placeholder_value = task.field_spec("root_cause").enum[0]
    cell = grade(
        task,
        Trajectory(
            terminal_status="succeeded",
            submitted_result={"root_cause": placeholder_value, "evidence_id": "ev-1"},
        ),
    )
    assert cell.outcome == outcomes.FAILED
    assert "SCAFFOLD" in (cell.reason or "")


def test_verifier_guard_checks_both_placeholders():
    verifier = gen._verifier_stub("rca-probe", "docs/probe.md")
    assert (
        'EXPECTED_ROOT_CAUSE.startswith("REPLACE_ME") or EXPECTED_EVIDENCE.startswith("REPLACE_ME")'
        in verifier
    )


def test_scaffold_escapes_quoted_title_and_json_source_path(tmp_path):
    doc = tmp_path / "incident.md"
    doc.write_text('# A "quoted" incident\n', encoding="utf-8")
    task_dir = gen.scaffold(
        doc, tmp_path / "tasks", "rca-quoted", force=False, repo_root=tmp_path
    )

    task = load_task(task_dir)
    assert task.title == 'RCA: A "quoted" incident'
    ground_truth = json.loads((task_dir / "expected" / "ground_truth.json").read_text())
    assert ground_truth["source_doc"] == "incident.md"

    windows_path = r"docs\incident.md"
    assert (
        json.loads(gen._ground_truth_stub("rca-probe", windows_path))["source_doc"]
        == windows_path
    )


def test_scaffold_falls_back_to_filename_for_malformed_utf8_doc(tmp_path):
    doc = tmp_path / "legacy-incident.md"
    doc.write_bytes(b"# legacy \x80 incident\n")

    task_dir = gen.scaffold(
        doc, tmp_path / "tasks", "rca-legacy", force=False, repo_root=tmp_path
    )

    task = load_task(task_dir)
    assert task.title == "RCA: legacy-incident.md"


def test_scaffold_refuses_overwrite_without_force(tmp_path, seed_doc):
    gen.scaffold(seed_doc, tmp_path, "dup", force=False, repo_root=seed_doc.parents[1])
    with pytest.raises(FileExistsError):
        gen.scaffold(
            seed_doc, tmp_path, "dup", force=False, repo_root=seed_doc.parents[1]
        )
    # --force replaces the directory rather than retaining stale task files.
    stale = tmp_path / "dup" / "stages" / "obsolete-stage.sh"
    stale.write_text("obsolete", encoding="utf-8")
    task_dir = gen.scaffold(
        seed_doc, tmp_path, "dup", force=True, repo_root=seed_doc.parents[1]
    )
    assert (task_dir / "task.toml").is_file()
    assert not stale.exists()


def test_scaffold_rejects_missing_doc(tmp_path):
    with pytest.raises(FileNotFoundError):
        gen.scaffold(
            tmp_path / "nope.md", tmp_path, "rca-x", force=False, repo_root=REPO_ROOT
        )


def test_cli_main_scaffolds(tmp_path, seed_doc):
    rc = gen.main(
        ["--doc", str(seed_doc), "--id", "rca-cli-probe", "--out-dir", str(tmp_path)]
    )
    assert rc == 0
    assert (tmp_path / "rca-cli-probe" / "task.toml").is_file()
