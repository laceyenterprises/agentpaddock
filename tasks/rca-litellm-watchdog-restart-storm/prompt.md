# RCA task: litellm-watchdog probe storm

The litellm-watchdog force-recreated the LiteLLM compose stack every ~90s for
~8.7 hours until the host stopped responding and had to be power-cycled. You are
handed a redacted incident bundle staged under `incident/`:

- `incident/watchdog.log` — the watchdog's probe/recreate log around the storm.
- `incident/probe-config.md` — the relevant probe configuration + prior-fix history.

Read every staged file, determine the single root cause, and cite the one
evidence id in the bundle that most directly proves it. Each evidence record is
tagged `evidence-id: <id>`.

Write your submission to `bench-result.json` in your worktree root:

```json
{"root_cause": "<one of the closed set below>",
 "evidence_id": "<the ev-… id that proves the root cause>"}
```

Closed-set root causes:

- `probe-substring-matches-healthy-banner` — a log-probe substring matches a
  string the healthy service prints on every boot, so the probe fires on health.
- `fail-threshold-too-low` — the probe trips on a single match and the failure
  threshold should be raised.
- `force-recreate-orphan-pileup` — `--force-recreate` races leave orphan
  containers that pile up and wedge the stack.
- `docker-vm-memory-exhaustion` — the Docker VM ran out of memory and that drove
  the restart loop.

The bundle contains planted red herrings — theories that look supported by a log
line but are not the root cause. Pick the true root cause, not the loudest symptom.
