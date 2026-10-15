# Maintainer provenance (never staged)

Synthetic (HARDFIX-01). No source pull request or private input.

proxy.py defines weighted capacity, independent clients, exact expiration and the minimum sufficient wait for the rejected cost. It also requires exact integer seconds for arbitrary Python integer durations. One review recommendation uses the oldest request even when several expirations are needed; the other correctly describes whole-second waits. The visible tests use cost one and do not inspect the wait value.

`must-fail.json` declares the incorrect source variants and the hidden case
indices that distinguish each, plus incorrect regression tests and their
required failing subchecks. These are authoring expectations, not run records.
Every source variant must pass the protected visible smoke tests. Reference
and alternative are independent correct implementations. Only fixture inputs
are staged; expected and hidden content remain verifier material.

Regenerate snapshots and hidden variants with
`python3 scripts/regenerate_hard_fix.py tasks/hard-fix-two-findings-v3 --write`.
