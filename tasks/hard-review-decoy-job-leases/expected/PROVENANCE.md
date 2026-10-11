# Maintainer provenance (never staged)

Source: synthetic, authored for HRF-04. The diff rewrites `jobs.py` and adds `db.py` and `sweeper.py`: 420 changed lines.

## Real defects

1. `jobs.py:111-114` (concurrency). `renew` does not check `holder`, so a stale holder's heartbeat extends the new holder's lease and the job runs twice. `pr.md` forbids this.
2. `sweeper.py:33` (correctness). A `time.monotonic()` default is compared with wall-clock `expires_at`, so the production sweeper never sweeps.

## Decoys (correct code; flagging any one blocking fails `decoy_resistance`)

- `jobs.py:150-157`: select-then-update in `claim`. Safe because `transaction()` is `BEGIN IMMEDIATE`. This is the mirror of the real claim race in `hard-review-multi-defect-outbox`.
- `jobs.py:185-186`: `attempts >= MAX_ATTEMPTS`. Correct because `claim` increments `attempts`. This is the mirror of the outbox off-by-one.
- `jobs.py:91-93`: `LeaseMixin` calls `self._one`, which `QueryMixin` supplies through `JobQueue`'s composition. Ported from PR 7516 ("missing method" on a mixin composition).
- `sweeper.py:38-40`: `except BusyError` catches lock timeouts because `Database.execute` translates driver errors. Ported from PR 7522 (driver errors claimed to bypass the guard although they were translated).
- `sweeper.py:43-45`: `swept` is always bound, because the last failed attempt re-raises. Ported from PR 7663 (variable claimed uninitialised although always assigned).
- `sweeper.py:50-55`: the read-only connection comes from `_open`, which sets `sqlite3.Row`. Ported from PR 7644 (the read-only accessor claimed to lack the needed connection behaviour).
- `db.py:68-70`: `except BaseException` rolls back and re-raises. A generic decoy.

`tests/test_hard_review_corpus.py` reproduces both defects and checks the decoys' correctness against the staged fixture.
