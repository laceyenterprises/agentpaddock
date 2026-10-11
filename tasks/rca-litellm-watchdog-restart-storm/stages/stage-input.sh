#!/usr/bin/env bash
# Stage the redacted litellm-watchdog storm bundle. Source doc:
# docs/POSTMORTEM-litellm-watchdog-restart-storm-2026-06-01.md
#
# Only ever writes inputs the agent may see. Carries ALL evidence ids (correct +
# red-herring); never reveals which one is the answer (that lives in expected/
# and verifier.py, out of band).
set -euo pipefail

sandbox_root="${1:-.}"
mkdir -p "${sandbox_root}/incident"

cat >"${sandbox_root}/incident/watchdog.log" <<'LOG'
# Redacted litellm-watchdog log excerpt (host paths scrubbed).

evidence-id: ev-801  00:52:03  local_log_probe: ERROR (LOCAL_LOG_FAIL_THRESHOLD=1,
  LOCAL_LOG_LOOKBACK_S=75). matched signature "query-engine" on log line:
    litellm-litellm-db-1 | Successfully spawned query-engine ac9d7041ed77bcc8a8dbd2ab6616b39013829574
  -> classified as Prisma engine death -> force-recreate LiteLLM stack.
  (This line is Prisma's HEALTHY startup banner. The signature is a bare
  substring; it matches every healthy boot.)

evidence-id: ev-802  00:52:05..07:44  force-recreate cadence ~90s, ~390+ cycles.
  Each `compose up --force-recreate` re-emits the "Successfully spawned
  query-engine <SHA>" banner, which lands inside the next 75s lookback window ->
  next probe matches it again. The loop fights its own startup output; raising
  the threshold cannot break it because every recreate regenerates the banner.

evidence-id: ev-806  01:24:15  Connection refused on /v1/chat/completions —
  litellm not yet bound to :4000 mid-recreate. It came up cleanly each cycle,
  then the next watchdog cycle killed it before consumers could connect.
LOG

cat >"${sandbox_root}/incident/probe-config.md" <<'MD'
# Redacted probe config + prior-fix history (from the on-call investigation)

## local_log_probe signature set (LOCAL_ERROR_SIGNATURES, excerpt)

    "query-engine"        <- bare substring
    "query engine"        <- bare substring
    "engine_process_death"
    "Client is not connected to the query engine"

With LOCAL_LOG_FAIL_THRESHOLD=1 and LOCAL_LOG_LOOKBACK_S=75, a single match in
the last 75s of `docker logs` flips the probe to ERROR.

## Prior fix history (the tempting-but-wrong precedent)

evidence-id: ev-803  2026-05-31 (#1146): DB_FAIL_THRESHOLD was raised 1 -> 3 for
  a DIFFERENT probe (the readiness probe that timed out under load). That fix
  ALSO switched the probe target from /spend/logs to /health/readiness — the
  threshold raise was belt-and-suspenders, not the real fix. Applying the same
  "raise the threshold" reflex here only delays the loop; it does not break it.

## Downstream churn observed during the storm

evidence-id: ev-805  interrupted-recreate races (watchdog `docker restart` racing
  the in-flight `compose up --force-recreate`) left hash-prefixed orphan
  litellm-litellm-db-1 containers behind on some cycles (same amplifier as the
  2026-05-25 orphan-pileup incident, different upstream driver).

evidence-id: ev-804  07:16:47  JetsamEvent: largestProcess =
  com.apple.Virtualization.Virtual (the Docker VM), 395229 pages x 16KB ~= 6.4 GB,
  at its configured memoryMiB=6144 ceiling. Host stopped responding to input;
  operator force-powered it off (boot fault btn_rst,finger_reset force_off — a
  force power-off, NOT a kernel panic). Memory pressure was a consequence of the
  ~390 expensive recreate cycles, not the driver of the loop.
MD
