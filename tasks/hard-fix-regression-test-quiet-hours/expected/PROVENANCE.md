# Maintainer provenance (never staged)

Synthetic (HRF-05). Shape: regression-test task, after the overnight-window
class of time-range bug. No source PR.

Where the contract is staged: the `in_quiet_hours` docstring in `quiet.py`
says the window is `[start, end)`, may span midnight (22:00 to 06:00 covers
22:00 through 05:59), and that `start == end` means no quiet hours. The
finding's headline example is 23:30, the evening side of the window.

Variants:
- `reference/`: ordinary windows unchanged; a wrapped window is
  `now >= start or now < end`.
- `insufficient/`: a wrapped window is only `now >= start`; fixes the
  finding's 23:30 example and still pages at 02:00.
- `alternative/`: a different correct fix with its own `wraps_midnight`
  helper.

Hidden cases also pin `start == end` as never quiet: a fix that sends every
non-ordinary window down the wrapped branch silences all paging there.

Checked-in regression tests (`regression-tests/`), graded against the four
variants by `tests/test_hard_fix_corpus.py`:
- `good.py`: both sides of midnight and the end boundary; passes all four
  checks.
- `asserts-nothing.py`: passes everywhere, so it fails
  `regression_test_fails_on_original` and `regression_test_fails_on_insufficient`.
- `source-changed.py`: asserts only that the output differs from the original's,
  so it passes on the insufficient fix; fails `regression_test_fails_on_insufficient`.
- `keyed-to-implementation.py`: imports `wraps_midnight` from the
  alternative fix; fails `regression_test_passes_on_reference`.
- `sniffing.py`: reads the source file; held in `integrity_hold`.

`hidden/variants/` must equal `expected/` byte for byte; regenerate with
`python3 scripts/regenerate_hard_fix.py tasks/hard-fix-regression-test-quiet-hours --write`.
