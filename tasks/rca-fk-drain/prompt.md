# RCA task: session-ledger dual-write FK drain

The dispatch daemon is wedged at 98% CPU and the fleet is down. You are handed a
redacted incident bundle staged under `incident/`:

- `incident/daemon-errors.log` — the daemon's error log around the wedge.
- `incident/schema-and-adapters.md` — the relevant migration + adapter notes.

Read every staged file, determine the single root cause, and cite the one
evidence id in the bundle that most directly proves it. Each evidence record is
tagged `evidence-id: <id>`.

Write your submission to `bench-result.json` in your worktree root:

```json
{"root_cause": "<one of the closed set below>",
 "evidence_id": "<the ev-… id that proves the root cause>"}
```

Closed-set root causes:

- `polymorphic-source-column-fk-mismatch` — an FK was added onto a column whose
  value means a different thing per adapter, so it can never match the target.
- `adapter-runtime-id-uniqueness-violation` — the FK target column has duplicate
  values and should be made unique.
- `dual-write-replay-queue-overflow` — the replay queue grew unbounded and needs
  a depth cap / circuit breaker.
- `daemon-cpu-thrash` — the daemon's poll loop is burning CPU and needs throttling.

The bundle contains planted red herrings — theories that look supported by a log
line but are not the root cause. Pick the true root cause, not the loudest symptom.
