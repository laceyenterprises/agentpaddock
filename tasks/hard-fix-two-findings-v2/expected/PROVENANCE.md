# Maintainer provenance (never staged)

Synthetic (HRX-03), reworking `hard-fix-two-findings` (Synthetic (HRF-05)),
which every calibrated combo passed. Shape: two-findings task whose second
card's recommended fix is subtly wrong. No source PR.

Why the original was easy: both cards' recommended fixes were right and the
contracts sat in the target files' own docstrings. Here the second card's
advice is wrong, and the evidence is in the protected caller, not in the file
being edited.

`finding.md` stages two review cards, one per editable file: `Limiter.allow`
counts rejected requests against the window, and `rejection_headers` sends a
millisecond wait as `Retry-After`. The second card recommends
`str(wait_ms // 1000)`.

Where the correct behaviour is staged: the `Limiter` docstring says only
admitted requests count and that the wait is the time until the next request
is admitted. The `proxy.handle` docstring in `proxy.py` says SDK clients sleep
exactly `Retry-After` seconds and retry once, so `Retry-After` must be the
smallest whole number of seconds after which the retry is admitted: the
ceiling of the wait, never the floor. A floor sends `0` for any wait under a
second, and the retry is rejected.

Variants:
- `reference/`: `allow` records only admitted requests; `rejection_headers`
  sends the ceiling of the wait in seconds.
- `insufficient/`: the reference `limiter.py` with the original
  `headers.py`; fixes the first finding only.
- `literal-advice/`: the reference `limiter.py` with the second card's
  `wait_ms // 1000`. It fixes the unit, but a client told `0` or `1` retries
  too early and is rejected again.
- `alternative/`: a different correct fix in both files (a deque per client;
  `math.ceil`).

Hidden cases: (1) limiter waits, including recovery once the window slides;
(2) `Retry-After` values at and around whole seconds; (3) an SDK client that
sleeps `Retry-After` seconds and retries once through `proxy.handle`.

`hidden/variants/` must equal `expected/` byte for byte; regenerate with
`python3 scripts/regenerate_hard_fix.py tasks/hard-fix-two-findings-v2 --write`.
