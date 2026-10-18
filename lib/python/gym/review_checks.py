"""Deterministic review checks, also shipped as ``gym_review_checks`` to verifiers.

Windows are dicts with file, line_start, line_end; expected_category may be
specified per defect. All defect windows must be found unless min_recall is set.
A defect may instead be {"any_of": [window, ...]}, credited once when any
alternative is flagged. Decoy windows mark correct code that looks wrong; their
blocking hits have their own budget. Clean controls require read_files
explicitly, since they have no defective file to infer evidence from.
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


def _alternatives(defect):
    """Windows that can credit a defect: the defect itself, or its any_of list."""
    if "any_of" not in defect:
        return (defect,)
    if set(defect) - {"any_of", "expected_category"}:
        raise ValueError("any_of defects take only any_of and expected_category")
    alternatives = defect["any_of"]
    if not isinstance(alternatives, list) or not alternatives:
        raise ValueError("any_of must be a nonempty list of windows")
    for window in alternatives:
        if not isinstance(window, dict) or {"any_of", "expected_category"} & set(window):
            raise ValueError("any_of alternatives are plain windows; categorize the defect")
    return tuple(alternatives)


def _hits(finding, defect, slack):
    return any(_overlaps(finding, window, slack) for window in _alternatives(defect))


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)

    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _outside_single_quotes(command):
    """Return command without single-quoted text, where ``$NAME`` stays literal."""
    kept, quote, escaped = [], None, False
    for char in command:
        if quote == "'":
            if char == "'":
                quote = None
            continue
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "'" and quote is None:
            quote = "'"
            continue
        elif char == '"':
            quote = None if quote == '"' else '"'
        kept.append(char)
    return "".join(kept)


def _read_arguments(call):
    name = str(call.get("name", "")).lower().rsplit(".", 1)[-1]
    if name in ("read", "read_file", "readfile", "open_file", "get_file"):
        yield from _strings(call.get("arguments"))
    # ``execute`` is the ACP tool kind acpx names shell calls by (OCEXPORT-01).
    elif name in (
        "exec",
        "exec_command",
        "bash",
        "shell",
        "command_execution",
        "shell_command",
        "execute",
    ):
        try:
            from gym_commands import normalized_commands
        except ModuleNotFoundError:
            from gym.harness_toolcalls import normalized_commands
        # A ``for NAME in WORD ...`` segment binds NAME for later segments of
        # this call only, so a loop body reading "$NAME" names every WORD.
        loops = {}
        for command in normalized_commands(call):
            try:
                words = shlex.split(command)
            except ValueError:
                continue
            # ``command`` bypasses an alias; do/then/else open a compound body.
            while words and words[0] in ("command", "do", "then", "else"):
                words = words[1:]
            if (
                len(words) >= 3
                and words[0] == "for"
                and words[2] == "in"
                and re.fullmatch(r"[A-Za-z_]\w*", words[1])
            ):
                loops[words[1]] = words[3:]
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
                expanded = _outside_single_quotes(command)
                for name, bound in loops.items():
                    reference = r"\$(?:" + name + r"(?!\w)|\{" + name + r"\})"
                    if re.search(reference, expanded):
                        yield from bound


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
    min_recall=None,
    decoys=(),
    max_decoy_blocking=0,
    enforce_category=False,
):
    """Return gv.check-compatible dicts; use ``record`` to emit them.

    Only read-like tools and read command segments count as read evidence.
    Slack widens defect, tolerated and decoy windows consistently.
    Invalid author windows raise; malformed submissions fail a required check.

    The defaults grade exactly as RFD shipped. ``min_recall`` replaces the
    all-or-nothing ``defect_found`` with a ``recall`` threshold. A blocking
    finding overlapping a decoy counts toward ``decoy_resistance`` (budget
    ``max_decoy_blocking``) instead of ``precision``, even if it also earned
    defect credit. Decoys must sit more than ``slack`` lines from every defect
    and tolerated window, so a finding on a defect window never hits a decoy.
    ``enforce_category`` restricts defect credit to its declared category; by
    default category mismatches remain advisory for existing tasks.
    """
    for value in (slack, max_false_blocking, max_decoy_blocking):
        if type(value) is not int or value < 0:
            raise ValueError(
                "slack, max_false_blocking and max_decoy_blocking must be nonnegative integers"
            )
    if type(enforce_category) is not bool:
        raise ValueError("enforce_category must be a boolean")
    if not clean_control and not defects:
        raise ValueError("non-control tasks require defect windows")
    if clean_control and defects:
        raise ValueError("clean controls cannot declare defects")
    if expected_category is not None and expected_category not in CATEGORIES:
        raise ValueError("unknown expected category")
    if min_recall is not None:
        if type(min_recall) not in (int, float) or not 0 < min_recall <= 1:
            raise ValueError("min_recall must be a number in (0, 1]")
        if clean_control:
            raise ValueError("clean controls cannot set min_recall")
    for window in (*defects, *tolerated):
        if window.get("expected_category", expected_category) not in (*CATEGORIES, None):
            raise ValueError("unknown window category")
    if any("any_of" in window for window in tolerated):
        raise ValueError("tolerated windows cannot use any_of")
    author = [w for d in defects for w in _alternatives(d)] + list(tolerated)
    for window in author:
        _range(window)
    if max_decoy_blocking and not decoys:
        raise ValueError("max_decoy_blocking requires decoy windows")
    for decoy in decoys:
        _range(decoy)
        if any(_overlaps(decoy, window, slack) for window in author):
            raise ValueError("decoy windows must not overlap defect or tolerated windows")
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

    def eligible(finding, defect):
        category = defect.get("expected_category", expected_category)
        return _hits(finding, defect, slack) and (
            not enforce_category or category is None or finding["category"] == category
        )

    matches = [[f for f in valid_blocking if eligible(f, d)] for d in defects]
    # One finding can earn one defect's credit; repeated hits on the same
    # defect, including on two any_of alternatives, are excess blocking
    # findings. Augmenting paths avoid order bias when author windows overlap.
    owners = {}

    def assign(index, seen):
        for window_index, defect in enumerate(defects):
            if window_index in seen or not eligible(valid_blocking[index], defect):
                continue
            seen.add(window_index)
            if window_index not in owners or assign(owners[window_index], seen):
                owners[window_index] = index
                return True
        return False

    on_decoy = [any(_overlaps(f, w, slack) for w in decoys) for f in valid_blocking]
    false_count = len(blocking) - len(valid_blocking)
    decoy_count = sum(on_decoy)
    # Decoy hits claim credit last, so they never displace a clean finding.
    for index in sorted(range(len(valid_blocking)), key=on_decoy.__getitem__):
        finding = valid_blocking[index]
        if (
            not assign(index, set())
            and not on_decoy[index]
            and not any(_overlaps(finding, w, slack) for w in tolerated)
        ):
            false_count += 1
    checks = []

    def add(name, ok, reason, required=True):
        checks.append(dict(name=name, ok=ok, reason=reason, required=required))

    if clean_control:
        add("no_blocking", not blocking, f"{len(blocking)} blocking findings")
    elif min_recall is None:
        add(
            "defect_found",
            len(owners) == len(defects),
            "every defect window needs a distinct blocking finding",
        )
    else:
        total = len(defects)
        needed = next(k for k in range(total + 1) if k / total >= min_recall)
        add(
            "recall",
            len(owners) >= needed,
            f"found {len(owners)} of {total} defects; required {needed}",
        )
    add(
        "well_formed_findings",
        len(valid) == len(findings),
        f"{len(findings) - len(valid)} malformed findings",
    )
    if decoys:
        add(
            "decoy_resistance",
            decoy_count <= max_decoy_blocking,
            f"{decoy_count} blocking findings on {len(decoys)} decoys; budget {max_decoy_blocking}",
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
    files = set(
        read_files
        if read_files is not None
        else (w["file"] for d in defects for w in _alternatives(d))
    )
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
