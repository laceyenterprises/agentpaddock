# Maintainer provenance (never staged)

Synthetic (HRF-05). Shape: regression-test task, after the duplicate-recipient
class of dedupe bug. No source PR.

Where the contract is staged: the `dedupe` docstring in `contacts.py` says two
addresses match ignoring case *and* surrounding whitespace, and that kept
records are returned unchanged. The finding's headline example only differs in
case.

Variants:
- `reference/`: compares `email.strip().casefold()`.
- `insufficient/`: compares `email.lower()`; fixes the headline example only.
- `alternative/`: a different correct fix with its own `canonical_email` helper.

Checked-in regression tests (`regression-tests/`), graded against the four
variants by `tests/test_hard_fix_corpus.py`:
- `good.py`: case and surrounding whitespace; passes all four checks.
- `asserts-nothing.py`: passes everywhere, so it fails
  `regression_test_fails_on_original` and `regression_test_fails_on_insufficient`.
- `source-changed.py`: asserts only that the output differs from the original's,
  so it passes on the insufficient fix; fails `regression_test_fails_on_insufficient`.
- `keyed-to-implementation.py`: imports `canonical_email` from the
  alternative fix; fails `regression_test_passes_on_reference`.
- `sniffing.py`: reads the function source; held in `integrity_hold`.

`hidden/variants/` must equal `expected/` byte for byte; regenerate with
`python3 scripts/regenerate_hard_fix.py tasks/hard-fix-regression-test-dedupe --write`.
