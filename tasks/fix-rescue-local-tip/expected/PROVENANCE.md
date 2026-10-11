# Maintainer provenance (never staged)

Derived from a private review. No public pull request or review link exists, and
none is recorded here. task_version 2 replaced the verbatim review text, private
identifiers and the private remediation with paraphrased and self-written
equivalents; results recorded at task_version 1 are not comparable.

`source-review.txt` records the paraphrased blocking review and must match
`tasks/review-rescue-local-tip/expected/source-review.txt` byte for byte.
A self-written remediation diff for the review fixture lives at
`tasks/review-rescue-local-tip/expected/source-remediation.diff`.
This fix fixture is a stdlib-only Python port of the reviewed mechanism;
`tasks/fix-rescue-local-tip/fixture/finding.md` stages the review card and explains the fixture mapping.
Ground truth is manually verified, not inferred from the generator.

Python port of local/remote ref resolution and worktree -B mutation. Commit ancestry is represented by opaque tip strings; no Git subprocess is needed.
