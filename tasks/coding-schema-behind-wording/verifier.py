"""Out-of-band verifier for coding-schema-behind-wording."""

import json

import gym_verifier as gv

EXPECTED_LINE = 4
EXPECTED_WORDING = "behind Alembic head"

result = gv.result()
calls = gv.toolcalls()

if result.get("defect_line") != EXPECTED_LINE:
    gv.fail(f"defect_line {result.get('defect_line')!r} != {EXPECTED_LINE} (regex literal)")

wording = str(result.get("missing_wording") or "").strip().lower()
if EXPECTED_WORDING.lower() not in wording:
    gv.fail(f"missing_wording {result.get('missing_wording')!r} does not name {EXPECTED_WORDING!r}")

if result.get("recoverable") is not True:
    gv.fail("the behind-head message describes the recoverable self-heal path")

if not any("schema_parser.py" in json.dumps(call.get("arguments") or {}) for call in calls.calls):
    gv.fail("agent answered without opening schema_parser.py")

gv.passed()
