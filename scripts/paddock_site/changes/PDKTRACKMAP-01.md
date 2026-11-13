# Checkpoint track maps

Race pages show each recorded checkpoint's pass rate across every submission
at that version, alongside the existing funnel views. Track cards retain their
disclosure policy; races without cards use the grading's checkpoint order.
Optional checks are marked, and verifiers that grade a single result have an
explicit no-checkpoints message.

The importer accepts `--checks-source EXPORT --checks-document FILENAME` to
write a deterministic `.checks.json.gz` companion beside an immutable results
document. `--check` verifies this companion without importing the export as a
document. A companion run must match the published run id, task, version,
attempt and score (within 0.00005). Result loading attaches validated checks
only to records without their own checks.
