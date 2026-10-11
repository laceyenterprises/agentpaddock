# Maintainer provenance (never staged)

Synthetic (HRX-03), reworking `hard-fix-cross-file` (Synthetic (HRF-05)),
which every calibrated combo passed. Shape: cross-file task whose second edit
is reached only through an indirection. No source PR.

Why the original was easy: `sweep.py` imported `lookup` by name, and the
finding told the agent to check the nightly sweep. Here the finding names only
`directory.py`, and nothing imports `lookup`: `reconcile.py` calls whatever
`settings.PRESENCE_CHECK` names, resolved by `plugins.resolve`. Searching for
callers of `lookup` finds only the string `"directory:lookup"` in
`settings.py`.

The finding points at `directory.py`. Fixing `lookup` as its docstring says
(None only for `NotFound`, everything else propagates) makes `reconcile` crash
on the first unavailable account, so the fix needs a coordinated edit in
`reconcile.py`.

Where the correct behaviour is staged: the `lookup` docstring says only "no
such user" is None and other errors propagate. `settings.py` names
`directory:lookup` as the presence check. The `reconcile` docstring says an
account whose presence check cannot be answered (`backend.Unavailable`) is
skipped, never deactivated, and the run carries on. The prompt says code that
depends on the change must still behave as documented. Nothing in `hidden/` is
needed to derive the fix.

Variants:
- `reference/`: `lookup` narrows to `NotFound`; `reconcile` catches
  `Unavailable` into `skipped` and continues.
- `insufficient/`: the reference `directory.py` with the original
  `reconcile.py`; the first unavailable account aborts the run.
- `alternative/`: a different correct fix in both files.

Hidden cases: (1) `lookup` lets `Unavailable` through; (2) `lookup` still
returns the entry or None; (3) the reconcile run skips unanswered accounts and
carries on to the end.

`hidden/variants/` must equal `expected/` byte for byte; regenerate with
`python3 scripts/regenerate_hard_fix.py tasks/hard-fix-cross-file-v2 --write`.
