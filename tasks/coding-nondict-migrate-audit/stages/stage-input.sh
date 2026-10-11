#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
mkdir -p "${sandbox_root}"

cat >"${sandbox_root}/daemon_excerpt.py" <<'PY'
def finish_post_ff(db, hq_root, deploy_checkout):
    pg_auto_migrate_payload = _run_pg_auto_migrate(hq_root, deploy_checkout)
    _raise_for_pg_auto_migrate_failure(pg_auto_migrate_payload)
    if pg_auto_migrate_payload.get("outcome") != "already-at-head":
        _audit_pg_auto_migrate_safely(
            db,
            deploy_checkout=deploy_checkout,
            payload=pg_auto_migrate_payload,
        )
    return "main_catchup.applied"
PY

cat >"${sandbox_root}/symptom.txt" <<'TXT'
During a post-fast-forward pass, _run_pg_auto_migrate returned None. The helper
that checks for migration failure accepted it. The next branch raised:

  AttributeError: 'NoneType' object has no attribute 'get'

The pass was marked failed even though there was no migration failure and no
payload to audit.
TXT
