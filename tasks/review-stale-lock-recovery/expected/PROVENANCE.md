# Maintainer provenance (never staged)

Derived from a private review. No public pull request or review link exists, and
none is recorded here. task_version 2 replaced the verbatim review text, private
identifiers and the private remediation with paraphrased and self-written
equivalents; results recorded at task_version 1 are not comparable.

source-review.txt is a paraphrase of the blocking finding and one related non-blocking note. source-remediation.diff is a self-written fix against the staged fixture, not the original remediation. pr.md explains the extraction and omitted context. Ground truth is manually verified, not inferred from the generator.

tasks/fix-stale-lock-recovery is the stdlib-only Python port of this mechanism and stages the same finding card.

The reference remediation uses `fs-ext`'s native POSIX `flockSync` on a
permanent lock inode. Install `fs-ext` in the consuming Node package; this is a
maintainer reference, not a staged dependency or deployed OS lock. Every caller
must switch to `releaseFileLock`, which closes the descriptor without unlinking
the lock path. All cooperating processes must use this protocol, on a local
filesystem with flock support. Mixed old/new callers require a coordinated
cutover with no active holders. The kernel releases ownership on descriptor
close or process exit, so there is no age-based stale deletion or breaker file.
The Python twin models atomic ownership-checked removal through its injected
filesystem primitive; it does not claim separate stat/unlink calls are atomic.
