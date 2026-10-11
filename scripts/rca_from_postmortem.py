#!/usr/bin/env python3
"""Scaffold a postmortem-derived RCA gym task from a real incident doc.

Agent OS owns the one asset an RCA bench has to manufacture — realistic
incidents with real logs, real red herrings, and a *known* root cause — in
months of SEV0 postmortems under ``docs/``. This generator turns one of those
incident docs into a task *skeleton* on the GYM-01 layout
(``tasks/<id>/task.toml`` + ``verifier.py`` + ``stages/`` + ``expected/``), which
an operator then fills in.

What the generator deliberately does NOT do: it never writes the answer. The
enum lands as placeholders, ``expected/ground_truth.json`` lands as a TODO
marked for operator fill-in, and the verifier stub fails closed until the
operator replaces it. This keeps the scaffold step honest — a half-authored
task cannot silently grade ``passed``, and the ground truth is never something
the generator could accidentally leak into a staged artifact.

The corpus it scans is ``docs/POSTMORTEM-*.md``; a seed may also point at another
incident doc under ``docs/`` (e.g. a ``SPEC-`` recovery doc or an assessment).
The staged artifact and the closed-set enum + evidence id are the operator's job
to distill from the real artifacts the incident doc references.

Usage::

    python3 scripts/rca_from_postmortem.py --doc docs/POSTMORTEM-foo-2026-06-01.md
    python3 scripts/rca_from_postmortem.py --doc docs/SPEC-bar.md --id rca-bar --force

Run ``--help`` for the flags. See ``docs/runbooks/RUNBOOK-adversarial-eval-gym.md``
(added in GYM-05) for the fill-in procedure.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

# Written into every scaffolded expected/ground_truth.json (and every seeded
# task). The staging-isolation test asserts this marker never appears in
# anything the agent can see — it is the tripwire that proves expected/ ground
# truth stayed out of band.
GROUND_TRUTH_MARKER = "GYM-GROUND-TRUTH-NEVER-STAGE"

# Placeholder token the operator must replace. The scaffold-shape test keys on
# it, and a live task that still carries it is unfinished by construction.
PLACEHOLDER = "REPLACE_ME"

_DOC_PREFIXES = ("POSTMORTEM-", "SPEC-", "AUDIT-", "ASSESSMENT-", "RCA-")
_TRAILING_DATE = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def derive_task_id(doc_path: Path) -> str:
    """Derive a ``rca-<slug>`` task id from an incident doc filename."""
    stem = doc_path.stem
    for prefix in _DOC_PREFIXES:
        if stem.upper().startswith(prefix):
            stem = stem[len(prefix) :]
            break
    stem = _TRAILING_DATE.sub("", stem)
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
    if not slug:
        raise ValueError(f"cannot derive a task id from {doc_path.name!r}")
    return f"rca-{slug}"


def _doc_title(doc_path: Path) -> str:
    """First markdown H1 in the doc, for a maintainer-facing comment."""
    try:
        for line in doc_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("# "):
                return line[2:].strip()
    except (OSError, UnicodeDecodeError):
        pass
    return doc_path.name


def _task_toml(task_id: str, doc_rel: str, title: str) -> str:
    task_id_literal = json.dumps(task_id)
    title_literal = json.dumps(f"RCA: {title}")
    return f'''\
# GYM-02 RCA task scaffolded by scripts/rca_from_postmortem.py.
#
# Source incident doc: {doc_rel}
#   ({title})
#
# OPERATOR FILL-IN (this scaffold fails closed until you do):
#   1. Read the "Root cause" section of the source doc. Distill the actual root
#      cause plus the plausible-but-wrong theories the responders chased.
#   2. Replace the {PLACEHOLDER} enum values below: exactly one true root cause
#      plus >=1 real red herring (a wrong theory from the incident, not invented
#      noise). Every enum value must be selectable so a wrong answer grades
#      failed, not format_failed.
#   3. Author stages/stage-input.sh to stage a REDACTED incident artifact (logs
#      / config) carrying the evidence the agent must cite AND the planted red
#      herrings. Never stage the answer.
#   4. Fill expected/ground_truth.json AND hardcode the same constants into
#      verifier.py. expected/ is documentation for the author; the trusted
#      verifier is what grades. expected/ is NEVER staged to the agent.

id = {task_id_literal}
title = {title_literal}
suite = "rca"
persona = "claude-code"

[dispatch]
prompt = "prompt.md"

[[stages]]
run = "stages/stage-input.sh"
description = "Stage the redacted incident artifact into the agent sandbox."

# The agent names the root cause from this closed set and cites an evidence id
# it read in the staged artifact. root_cause is an enum so an out-of-set answer
# is format_failed; a wrong-but-in-set answer (a red herring) is a real failed.
[result_schema]
root_cause = {{ type = "enum", enum = ["{PLACEHOLDER}_true_root_cause", "{PLACEHOLDER}_red_herring_1"], required = true }}
evidence_id = {{ type = "string", required = false }}

[verifier]
entrypoint = "verifier.py"
description = "root_cause == ground truth AND evidence_id cites the proving artifact id."
'''


def _verifier_stub(task_id: str, doc_rel: str) -> str:
    return f'''\
"""SCAFFOLD verifier for {task_id} — fails closed until the operator fills it in.

Source incident doc: {doc_rel}

Replace EXPECTED_ROOT_CAUSE / EXPECTED_EVIDENCE with the real closed-set answer
distilled from the doc's "Root cause" section, then delete the scaffold guard.
The ground-truth constants live inline in this trusted verifier; they are never
staged to the agent (see the sibling seeded tasks for the finished shape).
"""

import gym_verifier as gv

# OPERATOR: replace these and remove the guard below.
EXPECTED_ROOT_CAUSE = "{PLACEHOLDER}_true_root_cause"
EXPECTED_EVIDENCE = "{PLACEHOLDER}_evidence_id"

if EXPECTED_ROOT_CAUSE.startswith("{PLACEHOLDER}") or EXPECTED_EVIDENCE.startswith("{PLACEHOLDER}"):
    gv.fail("SCAFFOLD: operator must fill in ground truth before this task can grade")

result = gv.result()
if result.get("root_cause") != EXPECTED_ROOT_CAUSE:
    gv.fail(f"root_cause {{result.get('root_cause')!r}} != {{EXPECTED_ROOT_CAUSE!r}}")
evidence = result.get("evidence_id")
if not evidence:
    gv.fail("no evidence_id cited")
if evidence != EXPECTED_EVIDENCE:
    gv.fail(f"evidence_id {{evidence!r}} != {{EXPECTED_EVIDENCE!r}}")
gv.passed()
'''


def _prompt_md(title: str) -> str:
    return f"""\
# RCA task: {title}

You are handed a redacted incident artifact staged under `incident/`. Read every
staged file, determine the single root cause, and cite the one evidence id in the
artifact that most directly proves it.

Write your submission to `bench-result.json` in your worktree root as a JSON
object:

```json
{{"root_cause": "<one of the closed-set values listed in the task prompt below>",
 "evidence_id": "<the evidence id from the artifact that proves the root cause>"}}
```

OPERATOR: replace this line with the closed-set root-cause values (from the enum
in task.toml) and a one-line framing of the incident. The artifact contains
planted red herrings — plausible wrong theories the responders actually chased.
Pick the true root cause, not the loudest symptom.
"""


def _stage_stub(task_id: str, doc_rel: str) -> str:
    return f"""\
#!/usr/bin/env bash
# SCAFFOLD stage for {task_id}. Source incident doc: {doc_rel}
#
# Stages run before dispatch and write ONLY inputs the agent may see — never the
# expected/ ground truth. Replace the placeholder below with a redacted excerpt
# of the real incident artifacts the source doc references (logs / config),
# carrying the evidence ids the agent must cite AND the planted red herrings.
set -euo pipefail

sandbox_root="${{1:-.}}"
mkdir -p "${{sandbox_root}}/incident"
cat >"${{sandbox_root}}/incident/artifact.txt" <<'ARTIFACT'
REPLACE_ME: redacted incident artifact goes here. Include enumerated evidence
records (e.g. "evidence-id: ev-01  <log line>") so the agent can cite one, and
plant the red herrings the responders actually chased. Never write the answer.
ARTIFACT
"""


def _ground_truth_stub(task_id: str, doc_rel: str) -> str:
    payload = {
        "root_cause": "TODO-operator-fill-from-source-doc",
        "evidence_id": "TODO-operator-fill",
        "red_herrings": ["TODO-list-the-real-wrong-theories"],
        "source_doc": doc_rel,
        "__ground_truth_marker__": GROUND_TRUTH_MARKER,
        "note": (
            "Ground truth. NEVER staged to the agent and NEVER reachable from the "
            "out-of-band verifier env. Fill this in AND mirror the same constants "
            "into verifier.py, which is what actually grades."
        ),
    }
    return json.dumps(payload, indent=2) + "\n"


def scaffold(doc_path: Path, out_dir: Path, task_id: str, *, force: bool, repo_root: Path) -> Path:
    """Write a task skeleton for ``doc_path`` under ``out_dir/<task_id>``.

    Returns the created task directory. Refuses to clobber an existing task dir
    unless ``force`` is set.
    """
    if not doc_path.is_file():
        raise FileNotFoundError(f"incident doc not found: {doc_path}")

    task_dir = out_dir / task_id
    if task_dir.exists() and not force:
        raise FileExistsError(f"task dir already exists (pass --force to overwrite): {task_dir}")
    if task_dir.exists():
        shutil.rmtree(task_dir)

    try:
        doc_rel = str(doc_path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        doc_rel = doc_path.name

    title = _doc_title(doc_path)

    (task_dir / "stages").mkdir(parents=True, exist_ok=True)
    (task_dir / "expected").mkdir(parents=True, exist_ok=True)

    (task_dir / "task.toml").write_text(_task_toml(task_id, doc_rel, title), encoding="utf-8")
    (task_dir / "verifier.py").write_text(_verifier_stub(task_id, doc_rel), encoding="utf-8")
    (task_dir / "prompt.md").write_text(_prompt_md(title), encoding="utf-8")
    stage = task_dir / "stages" / "stage-input.sh"
    stage.write_text(_stage_stub(task_id, doc_rel), encoding="utf-8")
    stage.chmod(0o755)
    (task_dir / "expected" / "ground_truth.json").write_text(
        _ground_truth_stub(task_id, doc_rel), encoding="utf-8"
    )
    return task_dir


def _default_out_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "tasks"


def _default_repo_root() -> Path:
    # modules/adversarial-eval-gym/scripts/ -> repo root is three parents up.
    return Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--doc",
        required=True,
        type=Path,
        help="Incident doc under docs/ (POSTMORTEM-*.md, SPEC-*, assessment).",
    )
    parser.add_argument(
        "--id",
        dest="task_id",
        default=None,
        help="Task id (default: rca-<slug> derived from the doc filename).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Tasks directory root (default: this module's tasks/).",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repo root for the source-doc relative path (default: inferred).",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite an existing task dir.")
    args = parser.parse_args(argv)

    out_dir = args.out_dir or _default_out_dir()
    repo_root = args.repo_root or _default_repo_root()
    task_id = args.task_id or derive_task_id(args.doc)

    try:
        task_dir = scaffold(args.doc, out_dir, task_id, force=args.force, repo_root=repo_root)
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"scaffolded {task_id} at {task_dir}")
    print(
        "next: fill the enum + expected/ground_truth.json + verifier.py, author stages/stage-input.sh (see the comments)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
