"""Out-of-band verifier for rca-fk-drain.

Ground truth (from docs/SPEC-session-ledger-fk-recovery-2026-06-04.md): the root
cause is an FK added onto a polymorphic source column that can never match its
target (ev-703 is the FK-violation line; ev-704 states the polymorphic
source mismatch directly). The retarget-to-adapter_runtime_id theory (ev-705),
the replay-queue-overflow symptom (ev-702), and the 98%-CPU symptom (ev-701) are the red herrings the responders
chased first.

Runs in a scrubbed ephemeral env: sees only BENCH_RESULT + BENCH_TOOLCALLS via
``gym_verifier`` and can reach neither ``expected/`` nor the ledger. The ground-
truth constants are inline in this trusted verifier; they are never staged.
"""

import gym_verifier as gv

EXPECTED_ROOT_CAUSE = "polymorphic-source-column-fk-mismatch"
EXPECTED_EVIDENCE = ("ev-703", "ev-704")

result = gv.result()

gv.check(
    "root_cause",
    result.get("root_cause") == EXPECTED_ROOT_CAUSE,
    f"root_cause {result.get('root_cause')!r} != {EXPECTED_ROOT_CAUSE!r}",
    required=True,
)

evidence = result.get("evidence_id")
gv.check(
    "evidence",
    evidence in EXPECTED_EVIDENCE,
    f"evidence_id {evidence!r} must cite one of {EXPECTED_EVIDENCE!r}",
    required=True,
)

gv.passed()
