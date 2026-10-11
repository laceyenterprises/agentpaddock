"""PDK-05 (R6): the internal-note guard rejects automation records in the tree.

Fixtures are assembled from fragments: this file ships in the tree the guard
checks, so it must not hold a marker itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import internal_note_guard as guard

MODULE_ROOT = Path(__file__).resolve().parents[1]

RECORDS = [
    ("ham-record", "## HAM " + "record for PR 12"),
    ("ham-record", "Hammer " + "records: rebased, validated, merged"),
    ("adversarial-review-record", "The adversarial review " + "found two blocking issues."),
    ("adversarial-review-record", "adversarial-review " + "verdict: request changes"),
    ("reviewer-verdict", "Reviewer " + "verdict: approve"),
    ("validation-note", "Internal validation " + "note: reran the corpus"),
    ("acceptance-evidence", "## Acceptance " + "evidence"),
    ("remediation-response", "see remediation-" + "reply.json for the fixes"),
    ("launch-contract", "Launch " + "contract binds this workspace"),
    ("worker-workspace", "Work is restricted to the HQ-" + "provisioned checkout."),
    ("worker-result", "wrote WORKER" + "_RESULT.json"),
    ("commit-trailer", "Co-Authored-" + "By: A Bot <bot@example.com>"),
    ("generated-footer", "Generated with " + "[Claude Code](https://example.com)"),
]


@pytest.mark.parametrize(("rule", "line"), RECORDS, ids=[r for r, _ in RECORDS])
def test_each_record_shape_is_rejected(rule, line):
    findings = guard.check_text(f"results summary\n{line}\n", "reports/note.md")
    assert [(f.path, f.line, f.rule) for f in findings] == [("reports/note.md", 2, rule)]


@pytest.mark.parametrize(
    "line",
    [
        "The reviewer must report each blocking finding with its file and line.",
        "Review tasks grade a model's review; fix tasks grade its remediation.",
        "Acceptance criteria: the verifier passes.",
        "co-authored work is welcome; credit contributors in the PR body.",
        "Paddock validates every results document against the schema.",
    ],
)
def test_ordinary_prose_passes(line):
    assert guard.check_text(line) == []


def test_tree_rejects_records_and_artifact_names(tmp_path, capsys):
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "2026-01-01-run.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "check.md").write_text(
        "# Run\n\nInternal validation " + "note: all green\n", encoding="utf-8"
    )
    (tmp_path / ("WORKER" + "_RESULT.json")).write_text("{}\n", encoding="utf-8")
    (tmp_path / ("remediation-" + "reply-7.json")).write_text("{}\n", encoding="utf-8")

    assert guard.main([str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert "reports/check.md:3: internal automation record (validation-note)" in output
    assert ("WORKER" + "_RESULT.json:0: internal automation record (worker-result)") in output
    assert "internal automation record (remediation-response)" in output
    assert "pull request body" in output


def test_clean_tree_passes_and_missing_root_is_a_usage_error(tmp_path, capsys):
    (tmp_path / "AGENTS.md").write_text("Keep records in the PR body.\n", encoding="utf-8")
    assert guard.main([str(tmp_path)]) == 0
    assert "is clean" in capsys.readouterr().out
    assert guard.main([str(tmp_path / "absent")]) == 2


def test_the_rule_sources_pass_the_guard():
    # The full shipped tree is checked by the export and by `ci / fast`; these
    # files describe the rule, so they are the likeliest to trip over it.
    for name in (
        "scripts/internal_note_guard.py",
        "scripts/publication_scan.py",
        "tests/test_internal_note_guard.py",
        "tests/test_publication_scan.py",
        "tests/test_ci_policy.py",
        "AGENTS.md",
        "CONTRIBUTING.md",
        ".github/workflows/ci.yml",
    ):
        assert guard.check_text((MODULE_ROOT / name).read_text(encoding="utf-8"), name) == []
