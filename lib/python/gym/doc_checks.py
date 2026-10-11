"""Deterministic documentation assertions; captured text is never executed.

Required facts match bounded Markdown sections (or unheaded paragraphs),
with whitespace normalized. Stale claims still match the original text.
Only dotted identifiers in the declared config namespaces are config keys.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_FLAG = re.compile(r"(?<![\w-])--[A-Za-z][A-Za-z0-9_-]*")
_KEY = re.compile(r"(?<![\w.-])[A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*)+(?![\w-]|\.[A-Za-z_])")
_HEADING = re.compile(r"^ {0,3}#{1,6} .+?$", re.MULTILINE)
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_TASK_FILENAMES = {"surface.py", "guide.md", "change.diff", "bench-result.json"}
MAX_LINE_LENGTH = 2048
MAX_FACT_WINDOW = 8192


def _headings(lines: Sequence[str]) -> set[str]:
    headings = set()
    fence = None
    for line in lines:
        marker = _FENCE.match(line)
        if fence:
            if (
                marker
                and marker[1][0] == fence[0]
                and len(marker[1]) >= len(fence)
                and not marker[2].strip()
            ):
                fence = None
        elif marker:
            fence = marker[1]
        elif _HEADING.fullmatch(line):
            headings.add(line.rstrip())
    return headings


def _fact_windows(lines: Sequence[str]) -> list[str]:
    """Keep lists together under a heading; never join separate sections.

    Oversized sections fall back to paragraphs. Oversized paragraphs fail
    closed for facts rather than allowing arbitrary document-wide matching.
    """
    headings = _headings(lines)
    sections = []
    current = []
    headed = False

    def flush():
        if not current:
            return
        section = "\n".join(current)
        if headed and len(section) <= MAX_FACT_WINDOW:
            sections.append(section)
        else:
            sections.extend(
                paragraph
                for paragraph in re.split(r"\n\s*\n", section)
                if len(paragraph) <= MAX_FACT_WINDOW
            )

    for line in lines:
        if line.rstrip() in headings:
            flush()
            current = []
            headed = True
        current.append(line)
    flush()
    return [re.sub(r"\s+", " ", section).strip() for section in sections]


def checks(
    text: str | None,
    *,
    required_facts: Mapping[str, Sequence[str]],
    forbidden_claims: Mapping[str, Sequence[str]],
    known_flags: Sequence[str] = (),
    known_config_keys: Sequence[str] = (),
    preserved_headings: Sequence[str] = (),
    non_config_literals: Sequence[str] = (),
    config_key_prefixes: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Return required gv.check-compatible records; all regexes in a fact must match.

    Each forbidden regex independently rejects a stale claim. Empty fact sets
    are authoring errors, not vacuously satisfied facts. Missing capture fails
    even for an otherwise empty contract.
    """
    records = []

    def record(name, ok, reason):
        records.append(dict(name=name, ok=bool(ok), reason=None if ok else reason, required=True))

    record("document_captured", text is not None, "document was not captured")
    text = text or ""
    # Use the same newline boundary as the task regexes: other Unicode line
    # separators must not let a single regex input bypass the length limit.
    lines = text.split("\n")
    shape_ok = all(len(line) <= MAX_LINE_LENGTH for line in lines)
    record("document_shape", shape_ok, f"document line exceeds {MAX_LINE_LENGTH} characters")
    if not shape_ok:
        # Reject before running any task regex, so stuffing is a failed check,
        # never a verifier timeout. Task patterns must also avoid chained wildcards.
        return records
    windows = _fact_windows(lines)
    options = re.MULTILINE | re.IGNORECASE
    for name, patterns in required_facts.items():
        if not patterns or isinstance(patterns, str):
            raise ValueError(f"fact {name} requires a nonempty regex sequence")
        record(
            f"fact:{name}",
            any(all(re.search(p, window, options) for p in patterns) for window in windows),
            f"missing fact: {name}",
        )
    for name, patterns in forbidden_claims.items():
        if not patterns or isinstance(patterns, str):
            raise ValueError(f"forbidden claim {name} requires a nonempty regex sequence")
        record(
            f"stale:{name}",
            not any(re.search(p, text, options) for p in patterns),
            f"stale claim: {name}",
        )
    flags = sorted(set(_FLAG.findall(text)) - set(known_flags) - {"--help", "-h"})
    prefixes = tuple(
        config_key_prefixes
        if config_key_prefixes is not None
        else {key.split(".", 1)[0] + "." for key in known_config_keys}
    )
    keys = sorted(
        key
        for key in set(_KEY.findall(text))
        - set(known_config_keys)
        - set(non_config_literals)
        - _TASK_FILENAMES
        if key.startswith(prefixes) and not all(len(segment) == 1 for segment in key.split("."))
    )
    record("known_flags", not flags, "unknown flags: " + ", ".join(flags))
    record("known_config_keys", not keys, "unknown config keys: " + ", ".join(keys))
    headings = _headings(lines)
    for heading in preserved_headings:
        record(f"heading:{heading}", heading in headings, f"missing unchanged heading: {heading}")
    return records


def check_document(gv, text: str | None, **contract) -> None:
    """Record all checks through the verifier runtime; caller emits the verdict."""
    for item in checks(text, **contract):
        gv.check(**item)
