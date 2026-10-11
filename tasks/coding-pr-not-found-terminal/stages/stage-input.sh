#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
mkdir -p "${sandbox_root}"

cat >"${sandbox_root}/batch_activation.py" <<'PY'
PR_ABSENT_DETAIL = "pull request not found"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"


def _gh_error_is_unauthenticated(detail):
    lowered = str(detail or "").lower()
    return "requires authentication" in lowered or "bad credentials" in lowered


def _pr_body_fetch_status(detail):
    if detail == PR_ABSENT_DETAIL:
        return STATUS_SKIPPED
    return STATUS_FAILED


def classify_fetch(detail):
    status = _pr_body_fetch_status(detail)
    retryable = status == STATUS_FAILED
    return status, retryable
PY

cat >"${sandbox_root}/symptom.txt" <<'TXT'
The sweep saw a commit subject ending in (#6213). GitHub GraphQL responded:

  Could not resolve to a PullRequest with the number of 6213.

That number was an issue. Retrying cannot make it become a pull request. The
sweep still recorded failed/retryable and held the cursor on every pass, blocking
later post-merge actions behind it.
TXT
