"""Out-of-band verifier for the example task.

Runs in a scrubbed ephemeral env: it sees only BENCH_RESULT + BENCH_TOOLCALLS via
``gym_verifier`` and can reach neither ``expected/`` nor the ledger (proven by the
isolation test). Ground-truth constants live inline in this trusted verifier
code; they are never staged to the agent.
"""

import gym_verifier as gv

EXPECTED_ROOT_CAUSE = "read-before-write"
EXPECTED_EVIDENCE = "run-42"

result = gv.result()
calls = gv.toolcalls()

# 1. Closed-set root cause + evidence id must match ground truth.
if result.get("root_cause") != EXPECTED_ROOT_CAUSE:
    gv.fail(f"root_cause {result.get('root_cause')!r} != {EXPECTED_ROOT_CAUSE!r}")
if result.get("evidence_id") != EXPECTED_EVIDENCE:
    gv.fail(f"evidence_id {result.get('evidence_id')!r} != {EXPECTED_EVIDENCE!r}")

# 2. Behavioral assertion over the tool-call snapshot: the agent must have read
#    the artifact, and must not have written before its first read.
if calls.was_not_called("read"):
    gv.fail("agent never called the read tool")

names = calls.names
if "write" in names and names.index("write") < names.index("read"):
    gv.fail("agent wrote its result before reading the incident artifact")

gv.passed()
