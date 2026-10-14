"""Deterministic documentation assertions; captured text is never executed.

Required facts match bounded Markdown units or legacy sections,
with whitespace normalized. Stale claims still match the original text.
Only dotted identifiers in the declared config namespaces are config keys.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from typing import Any

_FLAG = re.compile(r"(?<![\w-])--[A-Za-z][A-Za-z0-9_-]*")
_KEY = re.compile(
    r"(?<![\w.-])[A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*)+(?![\w-]|\.[A-Za-z_])"
)
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


MAX_FACT_UNIT = 2048
_LIST_ITEM = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")


@dataclass(frozen=True)
class _FactUnit:
    text: str
    default_text: str | None = None


def _table_cells(line: str) -> list[str]:
    """Split Markdown cells on unescaped pipes and decode escaped pipes."""
    cells = []
    current = []
    backslashes = 0
    for char in line.strip():
        if char == "|" and backslashes % 2 == 0:
            cells.append("".join(current).strip())
            current = []
        elif char == "|":
            current.pop()  # Remove the escape, retaining preceding backslashes.
            current.append(char)
        else:
            current.append(char)
        backslashes = backslashes + 1 if char == "\\" else 0
    cells.append("".join(current).strip())
    if cells and not cells[0]:
        cells.pop(0)
    if cells and not cells[-1]:
        cells.pop()
    return cells


def _fact_units(
    lines: Sequence[str], known_config_keys=(), known_flags=()
) -> list[_FactUnit]:
    """Sentences, wrapped list items, and table rows with their header.

    Headings supply context but do not match alone. A colon introduction
    reaches the following list. Adjacent literal-only items form one enum
    unit; independent sibling assertions never join. Blank lines end that
    introduction. List continuations may contain multiple sentences, while
    independent outcome clauses are separated to prevent swapped values.
    Overlong list items and rows fail closed; sentences and literal lists
    also have a length limit, including their context.
    """
    units = []
    heading = ""
    intro = ""
    pending = []
    is_item = False
    header = []
    literals = []
    literals_overlong = False

    def add(raw, default_text=None):
        if len(raw) <= MAX_FACT_UNIT:
            units.append(_FactUnit(re.sub(r"\s+", " ", raw).strip(), default_text))

    def finish_literals():
        nonlocal literals, literals_overlong
        if literals and not literals_overlong:
            add(" ".join((heading, intro, *literals)))
        literals = []
        literals_overlong = False

    def flush():
        nonlocal pending, intro, literals_overlong
        raw = " ".join(pending)
        pending = []
        if is_item and re.fullmatch(r"[ `]*[\w-]+[ `.,;]*", raw) and (intro or heading):
            if not literals_overlong:
                if (
                    len(heading)
                    + len(intro)
                    + sum(len(v) + 1 for v in literals)
                    + len(raw)
                    + 2
                    > MAX_FACT_UNIT
                ):
                    literals_overlong = True
                else:
                    literals.append(raw.rstrip(" .,;"))
            return
        finish_literals()
        if not raw or (is_item and len(raw) > MAX_FACT_UNIT):
            intro = ""
            return
        # Keep identifiers and decimals intact. A list item retains its own
        # explanatory sentences, but not separate outcome/value clauses.
        outcome_split = r";\s*(?=(?:success|exhaustion|invalid)\b)|,?\s+(?:and\s+)?(?=[024]\s+(?:for|on|after|if|when|with|otherwise)\b)"
        split = outcome_split if is_item else r"(?<=[.!?])\s+|" + outcome_split
        for part in re.split(split, raw, flags=re.I):
            add(" ".join((heading, intro, part)))
        if raw.endswith(":"):
            intro = raw
        elif not is_item and any(
            token in set(_KEY.findall(raw)) | set(_FLAG.findall(raw))
            for token in (*known_config_keys, *known_flags)
        ):
            # A key followed by a literal-only enum list is an introduction.
            intro = raw

    for index, line in enumerate(lines):
        if _HEADING.fullmatch(line.rstrip()):
            flush()
            finish_literals()
            heading = line.strip()
            intro = ""
            header = []
            is_item = False
        elif line.strip().startswith("|"):
            flush()
            finish_literals()
            is_item = False
            cells = [c.strip() for c in _table_cells(line)]
            if all(re.fullmatch(r"[: -]+", c) for c in cells):
                continue
            next_line = lines[index + 1].strip() if index + 1 < len(lines) else ""
            has_rule = next_line.startswith("|") and all(
                re.fullmatch(r":?-{3,}:?", cell.strip())
                for cell in _table_cells(next_line)
            )
            if has_rule:
                header = cells
                continue
            if not header and any(
                re.fullmatch(
                    r"Key|Flag|Type|Default(?: / Output)?|Allowed values|Outcome|Condition|Exit code|Code|Alias|Canonical level",
                    c,
                    re.I,
                )
                for c in cells
            ):
                header = cells
                continue
            row = "| " + " | ".join(cells) + " |"
            if header:
                row = " | ".join(header) + " : " + row
            default_text = None
            for i, label in enumerate(header):
                if re.search(r"\bdefault\b", label, re.I) and i < len(cells):
                    default_text = cells[0] + " " + label + " " + cells[i]
            add(row, default_text)
        elif not line.strip():
            flush()
            finish_literals()
            intro = ""
            header = []
            is_item = False
        elif _LIST_ITEM.match(line):
            flush()
            is_item = True
            pending = [_LIST_ITEM.sub("", line).strip()]
        else:
            if is_item and line == line.lstrip():
                flush()
                finish_literals()
                is_item = False
                intro = ""
            header = []
            pending.append(line.strip())
    flush()
    finish_literals()
    return units


MAX_FACT_PARAGRAPH = 4096


def _fact_paragraphs(lines: Sequence[str]) -> list[str]:
    """Bound paragraphs and complete list items, with first-block headings."""
    result = []
    pending = []
    heading = ""
    item_indent = None
    fence = None

    def flush():
        nonlocal pending, heading, item_indent
        if pending:
            raw = " ".join((heading, *pending))
            if len(raw) <= MAX_FACT_PARAGRAPH:
                result.append(re.sub(r"\s+", " ", raw).strip())
            heading = ""
        pending = []
        item_indent = None

    for line in lines:
        marker = _FENCE.match(line)
        if fence:
            pending.append(line)
            if (
                marker
                and marker[1][0] == fence[0]
                and len(marker[1]) >= len(fence)
                and not marker[2].strip()
            ):
                fence = None
                flush()
            continue
        if marker:
            flush()
            fence = marker[1]
            pending.append(line)
        elif _HEADING.fullmatch(line.rstrip()):
            flush()
            heading = line.strip()
        elif not line.strip():
            if item_indent is None:
                flush()
            else:
                pending.append(line)
        elif _LIST_ITEM.match(line):
            indent = len(line) - len(line.lstrip())
            if item_indent is not None and indent <= item_indent:
                flush()
            elif item_indent is None and pending:
                flush()
            if item_indent is None:
                item_indent = indent
            pending.append(line)
        else:
            if (
                item_indent is not None
                and len(line) - len(line.lstrip()) <= item_indent
            ):
                flush()
            pending.append(line)
    flush()
    # A heading can introduce an entire first list, including sibling items.
    headings = _headings(lines)
    for index, line in enumerate(lines):
        if line.rstrip() not in headings:
            continue
        start = index + 1
        while start < len(lines) and not lines[start].strip():
            start += 1
        if start == len(lines) or not _LIST_ITEM.match(lines[start]):
            continue
        block = [line]
        for following in lines[start:]:
            if following.strip() and not (
                _LIST_ITEM.match(following) or following[:1].isspace()
            ):
                break
            block.append(following)
        raw = " ".join(block)
        if len(raw) <= MAX_FACT_PARAGRAPH:
            result.append(re.sub(r"\s+", " ", raw).strip())
    return result


def _rejected(spec, candidate, patterns, options):
    subjects = spec.get("reject_subject", patterns[:1])
    # Self-scoped reject patterns can follow the subject across clauses.
    if not subjects:
        return any(re.search(p, candidate, options) for p in spec.get("reject", ()))
    # Negative statements about a different subject cannot veto a paragraph.
    for clause in re.split(r"(?<=[.!?;])\s+", candidate):
        if any(re.search(p, clause, options) for p in spec.get("reject", ())):
            if all(re.search(p, clause, options) for p in subjects):
                return True
    return False


def _matches_fact(spec, windows, units, paragraphs, options):
    if isinstance(spec, Mapping):
        patterns = spec["patterns"]
        scope = spec.get("scope", "unit")
        if scope not in ("unit", "paragraph", "section"):
            raise ValueError("unknown fact scope")
        candidates = (
            [
                (
                    u.default_text
                    if spec.get("default_column") and u.default_text is not None
                    else u.text,
                    u.text
                    if (
                        spec.get("default_column")
                        and u.default_text is not None
                        and u.default_text != u.text
                        and not spec.get("reject_subject", True)
                    )
                    else None,
                )
                for u in units
            ]
            if scope == "unit"
            else [(p, None) for p in paragraphs]
            if scope == "paragraph"
            else [(w, None) for w in windows]
        )
        rejected = spec.get("reject", ())
    else:
        patterns = spec
        candidates = [(w, None) for w in windows]
        rejected = ()
    if not patterns or isinstance(patterns, str):
        raise ValueError("fact requires a nonempty regex sequence")
    return any(
        all(re.search(p, unit, options) for p in patterns)
        and not (
            rejected
            and (
                _rejected(spec, unit, patterns, options)
                or (
                    full_unit is not None
                    and _rejected(spec, full_unit, patterns, options)
                )
            )
        )
        for unit, full_unit in candidates
    )


def checks(
    text: str | None,
    *,
    required_facts: Mapping[str, Sequence[str] | Mapping[str, Any]],
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
        records.append(
            dict(name=name, ok=bool(ok), reason=None if ok else reason, required=True)
        )

    record("document_captured", text is not None, "document was not captured")
    text = text or ""
    # Use the same newline boundary as the task regexes: other Unicode line
    # separators must not let a single regex input bypass the length limit.
    lines = text.split("\n")
    shape_ok = all(len(line) <= MAX_LINE_LENGTH for line in lines)
    record(
        "document_shape",
        shape_ok,
        f"document line exceeds {MAX_LINE_LENGTH} characters",
    )
    if not shape_ok:
        # Reject before running any task regex, so stuffing is a failed check,
        # never a verifier timeout. Task patterns must also avoid chained wildcards.
        return records
    windows = _fact_windows(lines)
    units = _fact_units(lines, known_config_keys, known_flags)
    paragraphs = _fact_paragraphs(lines)
    options = re.MULTILINE | re.IGNORECASE
    for name, patterns in required_facts.items():
        record(
            f"fact:{name}",
            _matches_fact(patterns, windows, units, paragraphs, options),
            f"missing fact: {name}",
        )
    for name, patterns in forbidden_claims.items():
        if not patterns or isinstance(patterns, str):
            raise ValueError(
                f"forbidden claim {name} requires a nonempty regex sequence"
            )
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
        if key.startswith(prefixes)
        and not all(len(segment) == 1 for segment in key.split("."))
    )
    record("known_flags", not flags, "unknown flags: " + ", ".join(flags))
    record("known_config_keys", not keys, "unknown config keys: " + ", ".join(keys))
    headings = _headings(lines)
    for heading in preserved_headings:
        record(
            f"heading:{heading}",
            heading in headings,
            f"missing unchanged heading: {heading}",
        )
    return records


def check_document(gv, text: str | None, **contract) -> None:
    """Record all checks through the verifier runtime; caller emits the verdict."""
    for item in checks(text, **contract):
        gv.check(**item)
