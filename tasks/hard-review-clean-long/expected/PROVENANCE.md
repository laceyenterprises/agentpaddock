# Maintainer provenance (never staged)

Source: synthetic, authored for HRF-04 as the clean control. The diff rewrites `notify.py` and adds `alerts.py`, `quiet_hours.py`, `digest.py` and `notifyctl.py`: 427 changed lines. The code is correct. `pr.md` describes intentional behaviour changes: quiet hours hold non-critical alerts, critical alerts bypass the rate limit, dedupe is new, and the rate limit now slides.

The task passes only with zero blocking findings: it is a clean control with `max_false_blocking` 0. Decoys are code a reviewer may flag but that is correct given `pr.md` or the staged code:

- `notify.py:83-86`: holding non-critical alerts during quiet hours. This is intentional behaviour change 1.
- `notify.py:79-82`: critical alerts bypass the rate limit. This is intentional behaviour change 2.
- `notify.py:27-30`: SHA-1 dedupe key. It is not a security boundary (`pr.md`).
- `quiet_hours.py:56-58`: `or` for a window that wraps midnight. Correct.
- `digest.py:43`: `mkstemp` creates the file 0600 and `os.replace` keeps the mode. Ported from PR 7674 (missing-permission finding on code that already restricted the mode).
- `digest.py:67-70`: the negated rank gives most severe first. Ported from PR 7537 (an ordering claimed wrong that was correct).
- `digest.py:94-97`: send then clear then save is the at-least-once order `pr.md` accepts.

The reference result raises one non-blocking note, which is allowed. `tests/test_hard_review_corpus.py` checks the decoys' correctness against the staged fixture.
