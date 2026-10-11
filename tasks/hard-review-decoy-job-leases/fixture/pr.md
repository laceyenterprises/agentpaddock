# JOBS-07: time-bounded leases and a lease sweeper

Before this PR, `claim` stamped a `worker` column on the job. A worker that died
left its job `running` forever, and the operator requeued it by hand from
`stuck()`. This PR replaces the `worker` column with leases.

- **Claiming.** `claim` grants a lease for 60 seconds.
- **Heartbeat.** Workers call `renew` every 20 seconds and abort the job as soon
  as `renew` raises `LeaseLost`.
- **Sweeping.** `sweeper.py` runs every 15 seconds. It requeues (or kills) every
  job whose lease has expired, so a dead worker's job returns to the queue within
  about 75 seconds.
- **Removed.** `stuck()` and `requeue()` are gone.

`db.py` is new: a small sqlite3 wrapper shared by the queue and the sweeper.

## Contract

- **Exclusive leases.** A job is never run under two holders at once. Once the
  sweeper releases an expired lease, the old holder's `renew`, `complete` and
  `fail` all raise `LeaseLost`, including after another worker has claimed the
  job. A worker whose heartbeat fails stops work on the job.
- **Lease timestamps.** `expires_at` is wall-clock `time.time()` seconds. Leases
  are compared across processes, hosts sharing the volume, and restarts.
- **Attempts.** `attempts` counts claims and is incremented by `claim` itself.
  When an attempt ends (`fail` or an expired lease), `attempts` already includes
  that attempt, so `attempts >= MAX_ATTEMPTS` kills the job after exactly
  `MAX_ATTEMPTS` (3) attempts. That matches the pre-PR behaviour.
- **Transactions.** `Database.transaction()` begins with `BEGIN IMMEDIATE`,
  which takes the SQLite write lock up front. Any read-then-write inside a
  transaction (`claim`, `complete`, `fail`, the sweep) is serialised against
  every other writer; a second claimer blocks at its own `BEGIN`.
- **Errors.** `Database.execute` translates every `sqlite3.Error`, including
  errors from `BEGIN` and `COMMIT`. A lock timeout becomes `BusyError`; anything
  else becomes `StoreError`. Callers never see a raw driver error from it.
- **Sweep retries.** `sweep` retries a busy sweep up to `retries` times and
  re-raises the last `BusyError`; `retries < 1` is refused.
- **Mixins.** `JobQueue` composes `QueryMixin` (row helpers `_one`, `_all`) and
  `LeaseMixin` (lease bookkeeping, which calls those helpers).
- **Reports.** `status_report` reads through `Database.readonly()`, a separate
  `mode=ro` connection opened with the same `_open` (named-column rows), so a
  status call never takes the write lock.

## Testing

Unit tests cover:

- claim, complete, fail to dead after three attempts;
- the sweep requeue path with an explicit `now`;
- `job-sweeper --status` against a fixture database.
