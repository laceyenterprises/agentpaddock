# Maintainer provenance (never staged)

Synthetic (HARDFIX-01). No source pull request or private input.

plans.py defines opaque stable order/payment identity and mutable, nonunique descriptions, including reconciliation across process restarts. gateway.py defines retained idempotency keys and uncertain timeouts. Hidden cases observe ledger charges and call counts, never one particular key encoding. Delimiter collisions, distinct equal-value payments, changed descriptions, exact retry exhaustion, exception identity, unrelated errors and separate gateway instances are independent contracts.

`must-fail.json` declares the incorrect source variants and the hidden case
indices that distinguish each, plus incorrect regression tests and their
required failing subchecks. These are authoring expectations, not run records.
Every source variant must pass the protected visible smoke tests. Reference
and alternative are independent correct implementations. Only fixture inputs
are staged; expected and hidden content remain verifier material.

Regenerate snapshots and hidden variants with
`python3 scripts/regenerate_hard_fix.py tasks/hard-fix-wrong-recommendation-retry-charge-v3 --write`.
