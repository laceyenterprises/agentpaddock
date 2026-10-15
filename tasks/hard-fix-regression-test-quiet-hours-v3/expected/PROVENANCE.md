# Maintainer provenance (never staged)

Synthetic (HARDFIX-01). No source pull request or private input.

policy.py defines opening-day recurrence, half-open precise boundaries, empty intervals and no-day policies. deployer.py supplies local civil datetimes and handles hotfixes. Hidden cases include the Friday/Saturday and Sunday/Monday transitions plus a full deterministic weekly grid. The reference uses weekday arithmetic; the alternative constructs actual interval endpoints.

`must-fail.json` declares the incorrect source variants and the hidden case
indices that distinguish each, plus incorrect regression tests and their
required failing subchecks. These are authoring expectations, not run records.
Every source variant must pass the protected visible smoke tests. Reference
and alternative are independent correct implementations. Only fixture inputs
are staged; expected and hidden content remain verifier material.

Regenerate snapshots and hidden variants with
`python3 scripts/regenerate_hard_fix.py tasks/hard-fix-regression-test-quiet-hours-v3 --write`.
