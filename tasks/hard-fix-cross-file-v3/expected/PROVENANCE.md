# Maintainer provenance (never staged)

Synthetic (HARDFIX-01). No source pull request or private input.

backend.py distinguishes authoritative absence, cache misses requiring refresh, and unavailability. directory.py requires other errors to propagate; reconcile.py requires per-account continuation, input ordering and None-based absence. settings.py and plugins.py define the configured check. Cases exercise early and late failures, two-account batches, falsey entries, unexpected errors and a different configured plugin.

`must-fail.json` declares the incorrect source variants and the hidden case
indices that distinguish each, plus incorrect regression tests and their
required failing subchecks. These are authoring expectations, not run records.
Every source variant must pass the protected visible smoke tests. Reference
and alternative are independent correct implementations. Only fixture inputs
are staged; expected and hidden content remain verifier material.

Regenerate snapshots and hidden variants with
`python3 scripts/regenerate_hard_fix.py tasks/hard-fix-cross-file-v3 --write`.
