# Maintainer provenance (never staged)

Synthetic (HARDFIX-01). No source pull request or private input.

newsletter.py defines independent list scopes, opaque list ids, one-shot inputs, unchanged row identity and raw delivery spelling. subscribers.py defines normalization and unaddressed rows. The joined-key variant collides on punctuation across components; global-key silently drops a different list subscription; rewrite-row mutates shared data.

`must-fail.json` declares the incorrect source variants and the hidden case
indices that distinguish each, plus incorrect regression tests and their
required failing subchecks. These are authoring expectations, not run records.
Every source variant must pass the protected visible smoke tests. Reference
and alternative are independent correct implementations. Only fixture inputs
are staged; expected and hidden content remain verifier material.

Regenerate snapshots and hidden variants with
`python3 scripts/regenerate_hard_fix.py tasks/hard-fix-regression-test-dedupe-v3 --write`.
