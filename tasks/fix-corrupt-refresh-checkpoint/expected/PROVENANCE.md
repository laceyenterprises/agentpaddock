# Maintainer provenance (never staged)

Derived from a private review. No public pull request or review link exists, and
none is recorded here. task_version 2 replaced the verbatim review text, private
identifiers and the private remediation with paraphrased and self-written
equivalents; results recorded at task_version 1 are not comparable.

`source-review.txt` records the paraphrased blocking review and must match
`tasks/review-corrupt-refresh-checkpoint/expected/source-review.txt` byte for byte.
A self-written remediation diff for the review fixture lives at
`tasks/review-corrupt-refresh-checkpoint/expected/source-remediation.diff`.
This fix fixture is a stdlib-only Python port of the reviewed mechanism;
`tasks/fix-corrupt-refresh-checkpoint/fixture/finding.md` stages the review card and explains the fixture mapping.
Ground truth is manually verified, not inferred from the generator.

Port of the checkpoint constructor into load(root). The fixture keeps loading, per-file isolation, quarantine and reset semantics; database ingestion and alert transport are omitted. Logging stands in for the service alert boundary.

The port quarantines decode and schema/version failures and requires ERROR
logging for each quarantine. Missing files represent an absent checkpoint;
other read OSErrors propagate without renaming a potentially healthy cache.
This narrows the original review's blanket OSError recommendation: a permission
or transient I/O failure is not evidence of corrupt bytes.

RFDFIX-01 clarified the staged contract on 2026-10-05. See the
[calibration comparability boundary](../../../../../projects/gym-review-fix-docs/calibration.md):
pre-clarification results must be rerun before comparison or aggregation with
later results. Baseline keys do not include task content, so those cohorts
must be separated explicitly.
