# Outbox: bounded retries, dead letters and parallel dispatchers

The outbox ran a single dispatcher and retried every failed delivery forever, so one bad message could stall a topic and a backlog drained slowly. This change:

- Lets operators run several dispatcher processes on the host against the same outbox database file. Delivery endpoints treat every POST as a new event, so two dispatchers must never deliver the same message.
- Caps delivery at `MAX_ATTEMPTS = 5` attempts per message. After the fifth failed attempt the message is dead-lettered instead of retried.
- Treats a 4xx response other than 408 or 429 as permanent: the endpoint rejected the message itself, so it is dead-lettered at once, without retries.
- Keeps dead-lettered messages in the `outbox` table in state `dead` with their last error, so that `outbox replay <id>` can requeue one after the operator fixes the cause.
- Records the claiming worker and claim time on each claim. The existing `outbox reclaim` command (unchanged, not shown) returns claims older than ten minutes to `pending` after a dispatcher crash.

A crash between a successful POST and `mark_sent` can redeliver that one message after a reclaim. That window predates this change and is accepted.

The three files are the complete modules. The `outbox` CLI that calls `dead_letters`, `replay` and `reclaim` is unchanged and not shown.
