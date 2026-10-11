# Maintainer provenance (never staged)

Source: synthetic; authored for HRF-04. Four seeded defects across two files (three with the any-of alternative) and four categories:

0. `dispatcher.py:24` (correctness, the obvious one): `attempts >= MAX_ATTEMPTS` permits a sixth attempt.
1. `dispatcher.py:54-63` or `sender.py:12` (error-handling): `PermanentFailure` subclasses `DeliveryError`, so the earlier clause swallows it.
2. `store.py:48-58` (concurrency): select-then-update claim races between dispatchers.
3. `store.py:74-76` (data-loss): dead-lettering deletes the row that `dead_letters` and `replay` look for.

`min_recall` 0.75 needs three of four. Each defect is reproduced by `tests/test_hard_review_corpus.py` against the staged fixture.
