"""Out-of-band verifier for rca-litellm-watchdog-restart-storm.

Ground truth (from docs/POSTMORTEM-litellm-watchdog-restart-storm-2026-06-01.md):
the root cause is the probe's bare substring "query-engine" matching Prisma's
HEALTHY startup banner (ev-801 is the classified-healthy-banner line that proves
it). Raising the fail threshold (ev-803 / #1146), the orphan-container pileup
(ev-805), and the Docker VM memory exhaustion (ev-804) are the red herrings.

Runs in a scrubbed ephemeral env: sees only BENCH_RESULT + BENCH_TOOLCALLS via
``gym_verifier`` and can reach neither ``expected/`` nor the ledger.
"""

import gym_verifier as gv

EXPECTED_ROOT_CAUSE = "probe-substring-matches-healthy-banner"
EXPECTED_EVIDENCE = "ev-801"

result = gv.result()

if result.get("root_cause") != EXPECTED_ROOT_CAUSE:
    gv.fail(f"root_cause {result.get('root_cause')!r} != {EXPECTED_ROOT_CAUSE!r}")

evidence = result.get("evidence_id")
if not evidence:
    gv.fail("no evidence_id cited")
if evidence != EXPECTED_EVIDENCE:
    gv.fail(f"evidence_id {evidence!r} != {EXPECTED_EVIDENCE!r} (the healthy-banner match line)")

gv.passed()
