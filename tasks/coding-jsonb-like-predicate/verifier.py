"""Out-of-band verifier for coding-jsonb-like-predicate.

Sees only BENCH_RESULT + BENCH_TOOLCALLS. Ground truth is inline in this
trusted verifier and is never staged to the agent.
"""

import json

import gym_verifier as gv

# `metadata_json LIKE '%artifactEvidenceCheck%'` is on line 8 of query.sql.
# metadata_json is jsonb, and jsonb has no LIKE operator, so Postgres raises
# `operator does not exist: jsonb ~~ unknown`.
EXPECTED_LINE = 8
EXPECTED_KIND = "type-mismatch"
EXPECTED_COLUMN = "metadata_json"

result = gv.result()
calls = gv.toolcalls()

line = result.get("defect_line")
if line != EXPECTED_LINE:
    gv.fail(f"defect_line {line!r} != {EXPECTED_LINE} (the LIKE on a jsonb column)")

if result.get("defect_kind") != EXPECTED_KIND:
    gv.fail(f"defect_kind {result.get('defect_kind')!r} != {EXPECTED_KIND!r}")

column = str(result.get("offending_column") or "").strip().lower()
if EXPECTED_COLUMN not in column:
    gv.fail(
        f"offending_column {result.get('offending_column')!r} does not name {EXPECTED_COLUMN!r}"
    )


# The defect is invisible from query.sql alone: it only exists because
# metadata_json is jsonb. An agent that never opened schema.sql guessed.
#
# Assert on EVIDENCE OF READING, not on tool choice. The first version required
# a tool literally named "read"; the agents used `Bash` (`cat`, `ls`) and the
# transcript records name="Bash", so three correct answers were graded as
# "never read the file". That assertion pinned an implementation detail, not a
# behaviour, and would have failed every model however well it worked.
def _touched(needle: str) -> bool:
    for call in calls.calls:
        if needle in json.dumps(call.get("arguments") or {}):
            return True
    return False


if not _touched("schema.sql"):
    gv.fail("agent answered without opening schema.sql; the column type is the whole defect")

gv.passed()
