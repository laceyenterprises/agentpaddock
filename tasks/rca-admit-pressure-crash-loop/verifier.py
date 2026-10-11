"""Out-of-band verifier for rca-admit-pressure-crash-loop.

Ground truth (from docs/POSTMORTEM-admit-pressure-recovery-2026-05-30.md): the
root cause is that the ONLY path retrying a pressure-refused pre-admit request is
the daemon's in-memory admit loop, with no persisted state, so a crash-looping
daemon never retries and the queue stays wedged even after pressure eases (ev-901
is the pressure-eased-but-no-retry line; ev-902 states the in-memory-only
retry contract directly). The seductive red
herring is ev-904 (the jetsam OOM crash loop): it is the trigger, but the doc
calls it a SEPARATE bug — the wedge is the missing persistence. Blaming the
thresholds (ev-903) or the 24h retry scheduler (ev-905) are the other distractors.

Runs in a scrubbed ephemeral env: sees only BENCH_RESULT + BENCH_TOOLCALLS via
``gym_verifier`` and can reach neither ``expected/`` nor the ledger.
"""

import gym_verifier as gv

EXPECTED_ROOT_CAUSE = "in-memory-only-admit-retry-lost-on-daemon-death"
EXPECTED_EVIDENCE = ("ev-901", "ev-902")

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
