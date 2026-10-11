# Outbox: parallel dispatchers, bounded retries, dead letters and operator commands

OUTBOX-14. The outbox ran one dispatcher that retried every failed delivery on every pass, forever. One rejected message stalled its topic, and a backlog drained only as fast as one process could POST. This change makes the dispatcher safe to scale out and gives operators the tools to handle what it gives up on.

## Behaviour

- **Parallel dispatchers.** Operators run several `outbox run` processes on the host against the same database file. Endpoints treat every POST as a new event, so two dispatchers must never deliver the same message. Each claim records the claiming worker (`host:pid`), and a dispatcher records an outcome only while it still holds the claim.
- **Attempts.** `attempts` counts started attempts: claiming a message increments it, so while a message is claimed its `attempts` is the number of the attempt in progress. A message is attempted at most `MAX_ATTEMPTS = 5` times. When an attempt fails and it was the fifth, the message is dead-lettered instead of retried.
- **Backoff.** A failed attempt waits `backoff.delay(n)` seconds, where `n` is the number of the attempt that failed (`backoff.py` is unchanged and shared with the webhook client). If the endpoint sent a Retry-After, the wait is at least that long.
- **Permanent failures.** `sender.py` is unchanged; since OUTBOX-12 it raises `PermanentFailure` for a 4xx response other than 408 or 429, and `RetryLater` for a 429 or 503 with Retry-After. A permanently rejected message is dead-lettered at once, whatever its attempt count.
- **Crashed dispatchers.** Every dispatcher releases claims older than ten minutes once a minute (`reclaim_expired`, also `outbox reclaim`). An expired claim counts as a failed attempt: the message goes back to pending, or to dead if that was its fifth attempt. A batch that raises unexpectedly is logged and the dispatcher carries on; the claims that batch held expire and are released this way.
- **Dead letters** stay in the `outbox` table in state `dead` with their last error until an operator acts. `outbox replay <id>...` requeues them with a fresh set of attempts. `outbox discard <id>...` deletes them; it is the only command that removes a dead letter.
- **Purge.** `outbox purge --older-than DAYS` deletes `sent` messages last updated more than DAYS ago, to keep the table small.
- **Status.** `outbox status` prints counts and the age of the oldest due message, and exits non-zero when that lag exceeds `--max-lag` or any message is dead.
- `outbox run` stops after its current batch on SIGTERM or SIGINT and prints its totals.

## Notes for review

- `db.Database.transaction()` (unchanged) is `BEGIN IMMEDIATE ... COMMIT`: it takes the write lock at BEGIN, so reads inside the block see no concurrent writes before COMMIT. Statements outside a block autocommit one at a time.
- A crash between a successful POST and `mark_sent` can redeliver that one message after its claim expires. That window predates this change and is accepted.
- `migrate` adds the new columns to existing tables; existing pending rows become due immediately.

The six Python files are the complete modules. `db.py`, `sender.py` and `backoff.py` are unchanged and included for context.
