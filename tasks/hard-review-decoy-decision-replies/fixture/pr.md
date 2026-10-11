# DEC-02: decision replies, replay and deadline pages

Adds the reply path for operator decisions. Three new modules:

- `decisions.py`: the SQLite store (`decisions`, `dispatches`, `page_outbox`).
- `replies.py`: `reply()` records an operator's answer and dispatches it to the
  worker that raised the decision.
- `reconcile.py`: the periodic tick. It replays answers that were recorded while
  the dispatcher was down, and pages the operator about overdue open decisions.
  `decisions-reconcile --dry-run` runs one tick against a database without
  delivering anything.

## Contract

- **Exactly-once dispatch.** An answer reaches its worker exactly once. Workers
  do not deduplicate answers: a second dispatch resumes the worker a second time
  with the same answer. The one accepted window is a process crash after
  `dispatch()` returns and before `record_dispatch` commits.
- **Concurrent callers.** `hq decision reply` (operator CLI) and the reconcile
  tick (HQ daemon) are separate processes and routinely act on the same decision
  at the same moment. Each decision's `.reply.lock` file serialises them.
- **Re-entry.** The reply lock is an `flock` on a freshly opened file. Taking it
  a second time in one process blocks forever, because the second open is a new
  open file description. A caller that already holds it passes
  `_lock_held=True`; `reconcile_answered` does this.
- **Replays.** Replaying an answered-but-undispatched decision sends the
  recorded answer text.
- **Answer records.** `<id>.answer.json` is a write-once audit record. If one
  already exists, it is kept unchanged and returned; it is never replaced.
- **Diagnostics log.** `reply-diagnostics.log` is operator-only (0600).
  The legacy reply script created it 0644. `os.open`'s mode applies only on creation,
  so the file is re-`fchmod`ed on every open, before anything is written.
- **Deadline pages.** An open decision pages the operator once at each of 15, 60
  and 240 minutes, so a decision left open for five hours produces exactly three
  pages. A page's receipt is saved only after its outbox row is queued.
- **Status spelling.** Rows written by the legacy (pre-DEC-02) writer spell
  cancellation `cancelled`; this code writes `canceled`. Both are terminal.
- **Commits.** Every store method that writes uses `with self._conn:`, which
  commits on normal exit. A row is therefore durable before the method returns,
  and in particular before `reply()` writes the answer record.
- **SQL.** `prune_terminal` builds its SQL with f-strings only to splice in the
  `?` placeholder list. Every value is bound.

`Decision` is a plain frozen dataclass (no `__slots__`); `to_json` serialises
`vars(self)`.

## Testing

Unit tests run against an on-disk SQLite database. They cover:

- reply, re-reply and replay of answered decisions;
- pruning, including legacy `cancelled` rows;
- the 15-minute page.
