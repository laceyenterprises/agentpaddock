# Maintainer provenance (never staged)

Synthetic (HRX-03), reworking `hard-fix-regression-test-quiet-hours`
(Synthetic (HRF-05)), which every calibrated combo passed. Shape:
regression-test task with a plausible-wrong variant in `must_fail_variants`.
No source PR.

Why the original was easy: the target's own docstring said `start == end`
means no quiet hours, and a test of the overnight symptom already failed both
wrong variants it was graded on. Here the `start == end` contract lives only
in the caller's configuration, `policy.py`, and the finding's recommended fix
("any window whose end is not after its start" wraps) breaks it.

Where the contract is staged: the `in_freeze` docstring in `freeze.py` says the
window is `[start, end)` and may span midnight. `policy.py` says a team that
never freezes sets `start == end`, that such a window is empty, and that the
deploy gate lets that team ship at any hour; its `search` team does so.

Variants:
- `reference/`: ordinary windows unchanged; a wrapped window (`start > end`)
  is `now >= start or now < end`.
- `insufficient/`: a wrapped window is only `now >= start`; fixes the
  finding's 23:30 example and still ships at 02:00.
- `plausible-wrong/`: the finding's advice taken literally: every window with
  `start >= end` takes the wrapped branch. It fixes the overnight window and
  passes the visible tests, but a `start == end` window freezes all day, so
  `search` can never ship.
- `alternative/`: a different correct fix with its own `wraps_midnight`
  helper.

Hidden cases: (1) both sides of the overnight window and its end boundary;
(2) an ordinary window and `start == end` windows, never frozen; (3)
`deployer.may_deploy` across the staged `policy.FREEZES`.

Checked-in regression tests (`regression-tests/`), graded against the five
variants by `tests/test_hard_fix_corpus.py`:
- `good.py`: both sides of midnight, the end boundary, and `start == end`;
  passes every check.
- `symptom-only.py`: the overnight window alone, so it passes on the
  plausible-wrong fix; fails `regression_test_fails_on_plausible-wrong` only.
- `asserts-nothing.py`: passes everywhere, so it fails every must-fail check.
- `source-changed.py`: asserts only that the output differs from the
  original's; fails `regression_test_fails_on_insufficient` and
  `regression_test_fails_on_plausible-wrong`.
- `keyed-to-implementation.py`: imports `wraps_midnight` from the
  alternative fix; fails `regression_test_passes_on_reference`.
- `sniffing.py`: reads the source file; held in `integrity_hold`.

`hidden/variants/` must equal `expected/` byte for byte; regenerate with
`python3 scripts/regenerate_hard_fix.py tasks/hard-fix-regression-test-quiet-hours-v2 --write`.
