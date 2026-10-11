#!/usr/bin/env bash
# Stage the redacted admit-pressure crash-loop bundle. Source doc:
# docs/POSTMORTEM-admit-pressure-recovery-2026-05-30.md
#
# Only ever writes inputs the agent may see. Carries ALL evidence ids (correct +
# red-herring); never reveals which one is the answer (that lives in expected/
# and verifier.py, out of band).
set -euo pipefail

sandbox_root="${1:-.}"
mkdir -p "${sandbox_root}/incident"

cat >"${sandbox_root}/incident/daemon-timeline.log" <<'LOG'
# Redacted dispatch-daemon timeline (UTC; host paths scrubbed).

evidence-id: ev-903  03:32:45  daemon(pid=97783) refuse_admit_memory_pressure for
  lrq_ad70a976: availableMB=3495, freeMB=251, swapUsedPct=91.2,
  pressureLevel=elevated, inFlight=2. Two more refusals by 03:37.

evidence-id: ev-904  ~03:43..03:58  daemon crash loop: pid 97783 -> 17296 -> 24507,
  three deaths in ~15 min. No Python traceback and no "received signal 15;
  finishing pass and exiting" line before any death -> SIGKILL, consistent with
  the macOS jetsam killer under host-wide memory pressure. Each new daemon spends
  60-90s in startup recovery (lease heartbeats, worker_runs reconcile) and dies
  before reaching the admit loop.

evidence-id: ev-901  ~03:58  memory pressure has eased (vm.swapusage used
  5451M -> 3191M, free pages climbing). Admit COULD now succeed cleanly, but the
  two LRQs are still status=requested: no daemon lives long enough to re-run the
  in-memory admit pass, and there is no persisted retry state on the rows for a
  fresh daemon to act on. The queue stays wedged after the pressure is gone.
LOG

cat >"${sandbox_root}/incident/retry-contract.md" <<'MD'
# Redacted retry-contract + recovery-vector notes (from the on-call investigation)

## The pre-admit retry contract (as documented in the scheduler)

evidence-id: ev-902  worker_retry_scheduler docstring (line 13):
  "memory-pressure refusals already keep the LRQ as status=requested and the
   daemon retries them on every admit pass."
  But "every admit pass" == every iteration of the IN-MEMORY main loop. There is
  no persisted backoff state on the LRQ and no startup-recovery step that
  re-examines stuck requested-state rows. When the loop never runs, the retry
  contract is silently broken.

## Why the existing recovery vectors did not fire

evidence-id: ev-905  the worker_retry_scheduler rolling 24h chain only covers
  FAILED-class LRQs (post-admit). These rows were stuck in requested (pre-admit),
  so the scheduler never considered them. startup recovery walks
  worker_recovery_leases, not requested-state LRQs. The system had multiple
  recovery substrates for post-admit failure and ZERO for pre-admit
  retry-on-daemon-death.
MD
