# Maintainer provenance (never staged)

Source: synthetic, authored for HRF-04. The diff adds 419 lines across three new modules.

## Real defects

1. `replies.py:71-75` (concurrency). `reply()` reads the decision and checks `dispatched()` outside the reply lock and never re-checks under it. An operator reply racing the reconcile tick double-dispatches, which `pr.md` forbids.
2. `reconcile.py:62-63` (correctness). The `decision.id in receipts` test should be `threshold in receipts.get(decision.id, [])`. As written, only the first threshold ever pages.

## Decoys (correct code; flagging any one blocking fails `decoy_resistance`)

Each decoy ports a finding a real reviewer raised on correct code, which the author disputed and which was not remediated:

- `reconcile.py:28-30`: `_lock_held=True` while the lock is held. Ported from PR 7695 (shared reply-lock flag; re-acquiring the flock would deadlock).
- `replies.py:100-105`: `existing or {...}` keeps the first record. Ported from PR 7704 (the first answer record was claimed to be overwritten).
- `replies.py:123-125`: `os.fchmod(..., 0o600)` before the write. Ported from PR 7674 (missing fchmod claimed although it preceded the write).
- `decisions.py:59-60`: `vars()` on a non-slotted frozen dataclass. Ported from PR 7864.
- `decisions.py:12-13`: both `canceled` and `cancelled` are terminal. Ported from PR 7546 (`canceled` claimed rejected although it was in the terminal set).
- `decisions.py:120-125`: `with self._conn:` commits. Ported from PR 7615 (enqueue claimed uncommitted).
- `decisions.py:166-182`: f-string SQL that splices only placeholders. A generic decoy.

`pr.md` or the staged code justifies every decoy. `tests/test_hard_review_corpus.py` checks both defects and the decoys' correctness against the staged fixture.
