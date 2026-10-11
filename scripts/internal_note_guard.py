"""Internal-note guard: automation records stay out of the public tree (PDK-05, R6).

Review bots, merge automation and build workers write records of their own
work: review verdicts and findings, merge-automation logs, validation and
acceptance write-ups, worker launch details, commit trailers. Those belong in
the pull request body, where a reviewer reads them. Committed to the tree they
become permanent, public and stale. This guard rejects a tree holding one.

Markers are matched case-insensitively, by content and by file name. A file
documenting the rule names the markers through patterns, never literally (the
sources below write ``[_]`` and ``[-]`` for that reason), so the guard needs no
exemptions.

    python3 scripts/internal_note_guard.py [ROOT] [--tracked-only]

The pre-push mirror uses ``--tracked-only`` to scan index blobs and every
unpublished commit reachable from HEAD, excluding all remote-tracking refs,
without reading local untracked metadata. Only HEAD is checked, not other pushed
refs; the index also includes staged work that a push does not publish. Local
edits and deletions cannot hide committed content. Gitlinks are not file blobs.
The default full-tree mode also works on exports without Git metadata.

Exit status: 0 clean, 1 findings, 2 usage or read error. Stdlib only.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from publication_scan import iter_file_contents

#: ``(rule, regex)`` over file content.
MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (rule, re.compile(pattern, re.IGNORECASE))
    for rule, pattern in (
        ("ham-record", r"\b(?:HAM|hammer)\s+records?\b"),
        ("adversarial-review-record", r"\badversarial[- ]review\s+(?:found|flagged|verdict|round)"),
        ("reviewer-verdict", r"\breviewer\s+verdict\b"),
        ("validation-note", r"\bvalidation\s+notes?\b"),
        ("acceptance-evidence", r"\bacceptance\s+evidence\b"),
        ("remediation-response", r"\bremediation[- ]repl(?:y|ies)\b"),
        ("launch-contract", r"\blaunch\s+contract\b"),
        ("worker-workspace", r"\bHQ[- ](?:worker|provisioned)\b"),
        ("worker-result", r"\bWORKER[_]RESULT\b"),
        ("commit-trailer", r"^\s*Co-Authored-By:"),
        ("generated-footer", r"Generated with \[Claude Code\]"),
    )
)

#: ``(rule, regex)`` over file names: artifacts automation writes beside its work.
ARTIFACT_NAMES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("worker-result", re.compile(r"^WORKER[_]RESULT(?:\.[a-z]+)?$", re.IGNORECASE)),
    ("remediation-response", re.compile(r"^remediation[-]reply.*\.json$", re.IGNORECASE)),
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    blob: str | None = None

    def __str__(self) -> str:
        return f"{self.path}" + (f" [blob {self.blob}]" if self.blob else "") + f":{self.line}: internal automation record ({self.rule})"


def check_text(text: str, path: str = "<text>") -> list[Finding]:
    return [
        Finding(path, number, rule)
        for number, line in enumerate(text.splitlines(), start=1)
        for rule, regex in MARKERS
        if regex.search(line)
    ]


def check_tree(root: Path, *, tracked_only: bool = False) -> list[Finding]:
    findings: list[Finding] = []
    for path, content, oid in iter_file_contents(root, tracked_only=tracked_only):
        relative = path.relative_to(root).as_posix()
        findings.extend(
            Finding(relative, 0, rule, oid) for rule, regex in ARTIFACT_NAMES if regex.match(path.name)
        )
        text = content.decode("utf-8", errors="replace")
        findings.extend(replace(finding, blob=oid) for finding in check_text(text, relative))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--tracked-only", action="store_true", help="scan index and unpublished HEAD-history blobs (repository root only)"
    )
    args = parser.parse_args(argv)
    if not args.root.is_dir():
        print(f"internal-note guard: {args.root} is not a directory", file=sys.stderr)
        return 2
    try:
        findings = check_tree(args.root, tracked_only=args.tracked_only)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"internal-note guard: {exc}", file=sys.stderr)
        return 2
    for finding in findings:
        print(finding)
    if findings:
        print(
            f"internal-note guard: {len(findings)} record(s); keep review and automation "
            "records in the pull request body (see AGENTS.md)"
        )
        return 1
    print(f"internal-note guard: {args.root} is clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
