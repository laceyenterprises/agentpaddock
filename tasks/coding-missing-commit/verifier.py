"""Out-of-band verifier for coding-missing-commit.

Ground truth inline; the agent never sees it.
"""

import json

import gym_verifier as gv

EXPECTED_KIND = "missing-commit"
# `conn.execute(` for the INSERT begins on line 3 of loop.py; the handle is
# closed on line 30 inside `finally:`. Nothing commits between them, so the
# close discards the transaction.
INSERT_LINE = 3
TEARDOWN_LINE = 30
TOLERANCE = 2  # accept pointing at the statement rather than its exact first line

result = gv.result()
calls = gv.toolcalls()

if result.get("defect_kind") != EXPECTED_KIND:
    gv.fail(f"defect_kind {result.get('defect_kind')!r} != {EXPECTED_KIND!r}")


def _near(value, expected, label):
    if not isinstance(value, int):
        gv.fail(f"{label} {value!r} is not an integer line number")
    if abs(value - expected) > TOLERANCE:
        gv.fail(f"{label} {value} is not within {TOLERANCE} of {expected}")


_near(result.get("insert_line"), INSERT_LINE, "insert_line")
_near(result.get("teardown_line"), TEARDOWN_LINE, "teardown_line")

# Evidence of reading, not tool choice: agents open staged files with `Bash`
# (`cat`, `sed`), and the transcript records name="Bash". Requiring a tool
# literally named "read" failed correct answers for using the wrong tool.
if not any("loop.py" in json.dumps(call.get("arguments") or {}) for call in calls.calls):
    gv.fail("agent answered without opening loop.py")

gv.passed()
