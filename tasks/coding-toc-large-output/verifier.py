"""Out-of-band verifier for coding-toc-large-output.

Ground truth inline; the agent never sees it. The verifier cannot see the
agent's workspace, so "the test passes" is graded from the submitted result
plus the tool-call record: the regressing subject is only readable from
`git log`, and pytest must run both before and after the fix.

Passes are the TOC-05 cost-per-success denominator, so the tool-call checks
read what actually executed: only a command that runs pytest counts as a
pytest run, and a shell write to test.py fails the same as an edit tool.
"""

import json
import re

import gym_verifier as gv
from gym_commands import _command_values

EXPECTED_SUBJECT = "banner: select compact transport variant"
EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit", "apply_patch"}
# Measurement must execute the shim's bare command and return its large stdout.
_PYTEST = re.compile(r"^pytest\s+-v\s+test\.py$")
_GIT_LOG = re.compile(r'^git(?:\s+-c\s+safe\.directory=(?:"\$PWD"|\S+))?\s+log\s+-p$')
_TEST_PY = r"['\"]?(?:\S*/)?test\.py(?=['\")]?(?:\s|$))"
_WRITES_TEST_PY = [
    re.compile(r"\b(?:sed|perl)\b.*\s-i\S*.*\s" + _TEST_PY),
    re.compile(r">>?\s*" + _TEST_PY),
    re.compile(r"\btee\b(?:\s+-\S+)*\s+" + _TEST_PY),
    re.compile(r"\b(?:mv|cp)\b(?:\s+-\S+)*\s+\S+\s+" + _TEST_PY),
    re.compile(r"\b(?:rm|truncate)\b.*\s" + _TEST_PY),
    re.compile(r"open\(\s*['\"](?:\S*/)?test\.py['\"]\s*,\s*['\"][wax]"),
    re.compile(r"test\.py['\"]\)\s*\.write_(?:text|bytes)"),
    re.compile(r"\*\*\* (?:Update|Add|Delete) File:\s*(?:\S*/)?test\.py\b"),
]

result = gv.result()
calls = gv.toolcalls()


def _args(call):
    return json.dumps(call.get("arguments") or {})


def _runs(call, pattern):
    parts = gv.normalized_commands(call)
    if len(parts) != 1 or any(
        re.search(r"[|<>;&`]", command) or "$(" in command
        for command in _command_values(call.get("arguments"))
    ):
        return False
    return bool(pattern.fullmatch(parts[0]))


def _writes_test_py(call):
    if call.get("name") in EDIT_TOOLS:
        return "test.py" in _args(call)
    return any(
        p.search(part) for part in gv.normalized_commands(call) for p in _WRITES_TEST_PY
    )


if result.get("status") != "fixed":
    gv.fail(f"status {result.get('status')!r} != 'fixed'")

subject = result.get("regressing_commit_subject")
if not isinstance(subject, str) or subject.strip() != EXPECTED_SUBJECT:
    gv.fail(f"regressing_commit_subject {subject!r} != {EXPECTED_SUBJECT!r}")

if not any(_runs(call, _GIT_LOG) for call in calls.calls):
    gv.fail("agent never ran an unredirected git log -p")

if sum(1 for call in calls.calls if _runs(call, _PYTEST)) < 2:
    gv.fail(
        "agent did not run unredirected pytest -v test.py both before and after the fix"
    )

for call in calls.calls:
    if _writes_test_py(call):
        gv.fail("agent edited test.py instead of fixing main.py")

marker = result.get("failure_marker")
if not isinstance(marker, str) or not re.fullmatch(
    r"(?:(?i:diagnostic marker): +)?route-cedar-742", marker.strip()
):
    gv.fail("failure_marker does not match the failing traceback")

gv.passed()
