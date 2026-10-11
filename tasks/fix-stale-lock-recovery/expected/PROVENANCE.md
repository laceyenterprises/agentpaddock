# Maintainer provenance (never staged)

Derived from a private review. No public pull request or review link exists, and
none is recorded here. task_version 2 replaced the verbatim review text, private
identifiers and the private remediation with paraphrased and self-written
equivalents; results recorded at task_version 1 are not comparable.

`source-review.txt` records the paraphrased blocking review and must match
`tasks/review-stale-lock-recovery/expected/source-review.txt` byte for byte.
A self-written remediation diff for the review fixture lives at
`tasks/review-stale-lock-recovery/expected/source-remediation.diff`.
This fix fixture is a stdlib-only Python port of the reviewed mechanism;
`tasks/fix-stale-lock-recovery/fixture/finding.md` stages the review card and explains the fixture mapping.
Ground truth is manually verified, not inferred from the generator.

Python port with injected filesystem operations. remove_if_same is an atomic identity-checked removal contract; a second stat is deliberately insufficient. Tests schedule replacement at each observation boundary without timing or production filesystem probes.
