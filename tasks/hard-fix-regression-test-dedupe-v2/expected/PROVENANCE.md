# Maintainer provenance (never staged)

Synthetic (HRX-03), reworking `hard-fix-regression-test-dedupe` (Synthetic
(HRF-05)), which every calibrated combo passed. Shape: regression-test task
with a plausible-wrong variant in `must_fail_variants`. No source PR.

Why the original was easy: a test of the reported symptom (case and
whitespace duplicates) already failed both wrong variants it was graded on.
Here a fix can remove every reported duplicate and still be wrong.

Where the contract is staged: the `unique` docstring in `subscribers.py` says
two addresses match ignoring case and surrounding whitespace, and that kept
subscribers are the caller's own dicts. `newsletter.py` says each address goes
out exactly as typed and that the CRM sync must see the dicts unchanged. The
finding's recommendation ("normalize each subscriber's email") reads naturally
as rewriting the stored address, which is the plausible-wrong fix.

Variants:
- `reference/`: compares `email.strip().lower()`; the dicts are untouched.
- `insufficient/`: compares `email.lower()`; fixes the case half only.
- `plausible-wrong/`: writes `email.strip().lower()` back into each dict, then
  compares. It removes every reported duplicate and passes the visible tests,
  but mails the rewritten spelling and hands the CRM sync altered dicts.
- `alternative/`: a different correct fix with its own `address_key` helper.

Hidden cases: (1) case and whitespace duplicates collapse; (2) kept dicts are
the caller's, unmodified, with subscribers without an address kept; (3)
`send_list` sends the typed spelling and leaves the dicts unchanged.

Checked-in regression tests (`regression-tests/`), graded against the five
variants by `tests/test_hard_fix_corpus.py`:
- `good.py`: pins the duplicates, the unchanged dicts and the typed spelling;
  passes every check.
- `symptom-only.py`: pins the duplicates against the same dicts it passed in,
  so it passes on the plausible-wrong fix; fails
  `regression_test_fails_on_plausible-wrong` only.
- `asserts-nothing.py`: passes everywhere, so it fails every must-fail check.
- `source-changed.py`: asserts only that a duplicate was dropped; fails
  `regression_test_fails_on_insufficient` and
  `regression_test_fails_on_plausible-wrong`.
- `keyed-to-implementation.py`: imports `address_key` from the alternative
  fix; fails `regression_test_passes_on_reference`.
- `sniffing.py`: reads the function source; held in `integrity_hold`.

`hidden/variants/` must equal `expected/` byte for byte; regenerate with
`python3 scripts/regenerate_hard_fix.py tasks/hard-fix-regression-test-dedupe-v2 --write`.
