"""Out-of-band verifier for coding-nondict-migrate-audit."""

import json

import gym_verifier as gv

EXPECTED_LINE = 4
EXPECTED_ACTION = "skip-audit"

result = gv.result()
calls = gv.toolcalls()

if result.get("defect_line") != EXPECTED_LINE:
    gv.fail(f"defect_line {result.get('defect_line')!r} != {EXPECTED_LINE} (bare payload .get())")

if result.get("guard_needed") is not True:
    gv.fail(
        "guard_needed must be true: the audit predicate needs an isinstance(payload, dict) guard"
    )

if result.get("non_dict_action") != EXPECTED_ACTION:
    gv.fail("a non-dict payload carries nothing to audit, so the branch should skip the audit")

if not any("daemon_excerpt.py" in json.dumps(call.get("arguments") or {}) for call in calls.calls):
    gv.fail("agent answered without opening daemon_excerpt.py")

gv.passed()
