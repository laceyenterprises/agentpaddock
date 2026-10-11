# Maintainer provenance (never staged)

Source: synthetic, authored for HRX-02 as the reworked `hard-review-multi-defect-outbox`. Same defect themes as the original (exception ordering, claim race, attempt off-by-one, dead-letter loss), moved inside a 407-line diff that is otherwise correct. `min_recall` is 1.0: every defect is required.

## Real defects

0. `dispatcher.py:121-124` or `sender.py:20` (error-handling; the context defect). `except DeliveryError` precedes `except PermanentFailure`. That is wrong only because `sender.py`, unchanged and absent from the diff, declares `PermanentFailure(DeliveryError)`.
1. `store.py:90-104` (concurrency). The due-id SELECT runs outside `transaction()`, and the UPDATE matches by id only.
2. `store.py:148-154` (correctness). `reclaim_expired` uses `attempts > ?`; claim already incremented `attempts`, so an expired fifth attempt is retried a sixth time.
3. `store.py:208-214` (data-loss, the obvious one). `purge` deletes dead letters; `pr.md` says it deletes only `sent` messages.

## Decoys (correct code; one blocking finding on any of them fails `decoy_resistance`)

- `dispatcher.py:145-147`: `attempts >= MAX_ATTEMPTS` is right because claiming increments `attempts` (`store.claim_batch`, `pr.md`). This mirrors the original task's real off-by-one.
- `dispatcher.py:148`: `backoff.delay(message.attempts)` is right because `backoff.delay` takes the 1-based number of the failed attempt (`backoff.py` docstring).
- `dispatcher.py:108-112`: the `run_forever` catch-all is documented in `pr.md`; expired claims are released by `reclaim_expired`.
- `store.py:175-185`: `replay` reads and updates inside `transaction()`, which is `BEGIN IMMEDIATE` (`db.py`). This mirrors the real claim race.
- `store.py:188-193`: `discard` deleting a dead letter is the documented operator action (`pr.md`).

`tests/test_hard_review_corpus.py` reproduces each defect and checks each decoy against the staged fixture.
