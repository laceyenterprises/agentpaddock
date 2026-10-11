# Maintainer provenance (never staged)

Synthetic (HRF-05). Shape: cross-file task, after the outage-reads-as-absent
class of reconciliation bug. No source PR.

The finding points at `directory.py`. Fixing `lookup` as its docstring says
(None only for `NotFound`, everything else propagates) makes `sweep` crash on
the first unavailable account, so the fix needs a coordinated edit in
`sweep.py`.

Where the correct behaviour is staged: the `lookup` docstring says only "no
such user" is None and other errors propagate; the `sweep` docstring says an
account whose lookup cannot be answered (`backend.Unavailable`) is skipped,
never deactivated, and the sweep carries on. `backend.py` names both
exceptions. Nothing in `hidden/` is needed to derive the fix.

Variants:
- `reference/`: `lookup` narrows to `NotFound`; `sweep` catches
  `Unavailable` into `skipped` and continues.
- `insufficient/`: the reference `directory.py` with the original `sweep.py`;
  the first unavailable account aborts the sweep.
- `alternative/`: a different correct fix in both files.

Hidden cases: (1) `lookup` lets `Unavailable` through; (2) `lookup` still
returns the entry or None; (3) the sweep skips unanswered accounts and
carries on to the end.

`hidden/variants/` must equal `expected/` byte for byte; regenerate with
`python3 scripts/regenerate_hard_fix.py tasks/hard-fix-cross-file --write`.
