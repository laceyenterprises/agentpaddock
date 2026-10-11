- **Two waiters can both break the same stale lock**
  - **File:** `file-lock.mjs`
  - **Lines:** 24-29
  - **Problem:** Recovery reads the lock's age with `statSync` and deletes the path later with `rmSync`. Every waiter polling a crashed holder's lock sees the same old `mtimeMs` and decides the lock is stale. If waiter A deletes it and takes a new lock with `openSync(lockPath, "wx")` before waiter B reaches its `rmSync`, B deletes A's live lock and then takes one of its own.
  - **Why it matters:** Mutual exclusion fails exactly during crash recovery. A and B both run the critical section at once, which brings back the lost-update problem the lock exists to prevent.
  - **Recommended fix:** Make breaking a stale lock an atomic compare-and-remove that deletes the file only while it is still the one judged stale. Prefer a kernel advisory lock released when its holder exits, using a permanent inode that is never unlinked. Otherwise use an actual atomic identity-checked removal primitive; a second stat or rename alone cannot establish that the path still names the observed stale lock.


Fixture mapping: editable `target.py`. Lines above refer to the review fixture, not to this file.
Python port with injected filesystem operations. remove_if_same is an atomic identity-checked removal contract; a second stat is deliberately insufficient. Tests schedule replacement at each observation boundary without timing or production filesystem probes.
