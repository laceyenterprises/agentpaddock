# RCA task: admit-pressure crash-loop wedged the queue

Two DAG runs sat in `launch_requests.status='requested'` for >25 minutes with no
progress while their walkers reported `running`. You are handed a redacted
incident bundle staged under `incident/`:

- `incident/daemon-timeline.log` — the dispatch-daemon lifecycle around the wedge.
- `incident/retry-contract.md` — the admit-refusal retry contract + recovery-vector notes.

Read every staged file, determine the single root cause, and cite the one
evidence id in the bundle that most directly proves it. Each evidence record is
tagged `evidence-id: <id>`.

Write your submission to `bench-result.json` in your worktree root:

```json
{"root_cause": "<one of the closed set below>",
 "evidence_id": "<the ev-… id that proves the root cause>"}
```

Closed-set root causes:

- `in-memory-only-admit-retry-lost-on-daemon-death` — the only path that retries
  a pressure-refused pre-admit request is the daemon's in-memory admit loop, with
  no persisted state, so a crash-looping daemon never retries and the queue wedges.
- `memory-pressure-thresholds-too-strict` — the admit refusal thresholds are too
  aggressive and refused work that should have been admitted.
- `daemon-oom-jetsam-kill` — the daemon was OOM-killed by jetsam, and stopping the
  kills is the fix.
- `worker-retry-scheduler-gap` — the rolling 24h retry scheduler failed to retry
  the stuck requests.

The bundle contains planted red herrings — theories that look supported by a log
line but are not the root cause. Pick the true root cause, not the loudest symptom.
