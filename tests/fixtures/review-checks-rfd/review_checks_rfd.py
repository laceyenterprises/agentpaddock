# Frozen copy of gym/review_checks.py as RFD shipped it (PR 7636), before HRF-02.
# test_review_checks_rfd_parity.py grades RFD-style calls through both copies
# and requires identical subchecks. Never edit this file.
"""Deterministic review checks, also shipped as ``gym_review_checks`` to verifiers.

Windows are dicts with file, line_start, line_end; expected_category may be
specified per defect. All defect windows must be found. Clean controls require
read_files explicitly, since they have no defective file to infer evidence from.
"""

from __future__ import annotations

import json
import re
import shlex

CATEGORIES = (
    "correctness",
    "concurrency",
    "data-loss",
    "security",
    "error-handling",
    "resource-leak",
    "api-contract",
    "test-gap",
    "docs",
)


def result_schema_snippet(max_items: int = 32) -> str:
    """Canonical TOML result schema to copy into review task manifests."""
    if type(max_items) is not int or max_items < 0:
        raise ValueError("max_items must be a nonnegative integer")
    fields = ", ".join(
        f'{name} = {{ type = "{kind}", required = true }}'
        for name, kind in (
            ("file", "string"),
            ("line_start", "integer"),
            ("line_end", "integer"),
            ("blocking", "boolean"),
            ("summary", "string"),
        )
    )
    categories = json.dumps(CATEGORIES)
    return (
        '[result_schema]\nfindings = { type = "list", required = true, '
        f"max_items = {max_items}, items = {{ {fields}, category = "
        f'{{ type = "enum", enum = {categories}, required = true }} }} }}\n'
    )


def _range(value):
    start, end = value.get("line_start"), value.get("line_end")
    if type(start) is not int or type(end) is not int or start < 1 or end < start:
        raise ValueError("line windows must be positive ordered integers")
    if not isinstance(value.get("file"), str) or not value["file"]:
        raise ValueError("window file must be nonempty")
    return start, end


def _overlaps(finding, window, slack):
    start, end = _range(finding)
    low, high = _range(window)
    return (
        finding["file"] == window["file"] and start <= high + slack and end >= max(1, low - slack)
    )


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)

    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _read_arguments(call):
    name = str(call.get("name", "")).lower().rsplit(".", 1)[-1]
    if name in ("read", "read_file", "readfile", "open_file", "get_file"):
        yield from _strings(call.get("arguments"))
    elif name in ("exec", "exec_command", "bash", "shell", "command_execution", "shell_command"):
        try:
            from gym_commands import normalized_commands
        except ModuleNotFoundError:
            from gym.harness_toolcalls import normalized_commands
        for command in normalized_commands(call):
            try:
                words = shlex.split(command)
            except ValueError:
                continue
            if words and words[0].rsplit("/", 1)[-1] in (
                "cat",
                "sed",
                "head",
                "tail",
                "awk",
                "rg",
                "grep",
                "less",
                "nl",
            ):
                yield command


def evaluate(
    findings,
    toolcalls,
    *,
    defects=(),
    tolerated=(),
    expected_category=None,
    slack=3,
    max_false_blocking=1,
    clean_control=False,
    read_files=None,
):
    """Return gv.check-compatible dicts; use ``record`` to emit them.

    Only read-like tools and read command segments count as read evidence.
    Slack widens both defect and tolerated windows for precision consistently.
    Invalid author windows raise; malformed submissions fail a required check.
    """
    for value in (slack, max_false_blocking):
        if type(value) is not int or value < 0:
            raise ValueError("slack and max_false_blocking must be nonnegative integers")
    if not clean_control and not defects:
        raise ValueError("non-control tasks require defect windows")
    if clean_control and defects:
        raise ValueError("clean controls cannot declare defects")
    if expected_category is not None and expected_category not in CATEGORIES:
        raise ValueError("unknown expected category")
    for window in (*defects, *tolerated):
        _range(window)
        if window.get("expected_category", expected_category) not in (*CATEGORIES, None):
            raise ValueError("unknown window category")
    valid = []
    for finding in findings:
        try:
            _range(finding)
        except ValueError:
            continue
        if type(finding.get("blocking")) is bool and finding.get("category") in CATEGORIES:
            valid.append(finding)
    # Invalid findings cannot earn matching credit or disappear from clean
    # controls. Their required check fails even alongside an otherwise valid hit.
    blocking = [f for f in findings if f.get("blocking") is True]
    valid_blocking = [f for f in valid if f["blocking"]]
    matches = [[f for f in valid_blocking if _overlaps(f, d, slack)] for d in defects]
    # One finding can earn one window's credit; repeated hits on the same
    # defect are excess blocking findings. Augmenting paths avoid order bias
    # when author windows overlap.
    owners = {}

    def assign(index, seen):
        for window_index, window in enumerate(defects):
            if window_index in seen or not _overlaps(valid_blocking[index], window, slack):
                continue
            seen.add(window_index)
            if window_index not in owners or assign(owners[window_index], seen):
                owners[window_index] = index
                return True
        return False

    false_count = len(blocking) - len(valid_blocking)
    for index, finding in enumerate(valid_blocking):
        if not assign(index, set()) and not any(_overlaps(finding, w, slack) for w in tolerated):
            false_count += 1
    checks = []

    def add(name, ok, reason, required=True):
        checks.append(dict(name=name, ok=ok, reason=reason, required=required))

    if clean_control:
        add("no_blocking", not blocking, f"{len(blocking)} blocking findings")
    else:
        add(
            "defect_found",
            len(owners) == len(defects),
            "every defect window needs a distinct blocking finding",
        )
    add(
        "well_formed_findings",
        len(valid) == len(findings),
        f"{len(findings) - len(valid)} malformed findings",
    )
    add(
        "precision",
        false_count <= max_false_blocking,
        f"{false_count} false blocking findings; allowed {max_false_blocking}",
    )
    categorized = [
        (d, hits)
        for d, hits in zip(defects, matches)
        if d.get("expected_category", expected_category) is not None
    ]
    if categorized:
        add(
            "category",
            all(
                any(f["category"] == d.get("expected_category", expected_category) for f in hits)
                for d, hits in categorized
            ),
            "matched findings use the expected categories",
            False,
        )
    files = set(read_files if read_files is not None else (d["file"] for d in defects))
    if not files:
        raise ValueError("read_files required for clean controls")
    calls = toolcalls.get("calls", []) if isinstance(toolcalls, dict) else toolcalls
    arguments = [s for call in calls for s in _read_arguments(call)]

    def mentioned(path):
        pattern = re.compile(r"(?<![\w./-])" + re.escape(path) + r"(?![\w./-])")
        # Absolute paths also count when the relative path is a complete suffix.
        suffix = re.compile(r"/" + re.escape(path) + r"(?![\w./-])")
        return any(pattern.search(s) or suffix.search(s) for s in arguments)

    add(
        "read_evidence",
        all(mentioned(path) for path in files),
        "each target file appears in read-like tool arguments or a read command segment",
    )
    return checks


def record(gv, **kwargs):
    """Score gv.result()['findings'] and gv.toolcalls(), recording all checks."""
    for check in evaluate(gv.result()["findings"], gv.toolcalls(), **kwargs):
        gv.check(**check)
