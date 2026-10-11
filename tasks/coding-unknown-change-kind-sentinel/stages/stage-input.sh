#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
mkdir -p "${sandbox_root}"

cat >"${sandbox_root}/change_kind.py" <<'PY'
def resolve_change_kind(*, explicit=None, body=None, title=None, branch=None):
    if explicit is not None and explicit.strip():
        normalized = normalize_change_kind(explicit)
        if normalized is None:
            raise ValueError(f"bad change kind {explicit!r}")
        return ResolvedChangeKind(normalized, "declared")
    declared = declared_change_kind_from_body(body)
    if declared is not None:
        return ResolvedChangeKind(declared, "declared")
    inferred = infer_change_kind(title=title, body=body, branch=branch)
    if inferred is not None:
        return ResolvedChangeKind(inferred, "inferred")
    return ResolvedChangeKind("unknown", "unknown")
PY

cat >"${sandbox_root}/symptom.txt" <<'TXT'
A previous pass returned kind="unknown", source="unknown" for a PR with no
declared or inferred change kind. DAG reconcile persisted that completion, then
replayed kind="unknown" as explicit input while backfilling a merge. The resolver
raised ValueError and the step remained retryable forever.

Invalid explicit values must still fail. Only the resolver's own terminal value
needs special handling: accept it as a sentinel, then continue deriving from the
body/title/branch rather than treating it as a declared kind.
TXT
