#!/usr/bin/env bash
# Stage the query + schema the agent is allowed to see. Never the ground truth.
set -euo pipefail

sandbox_root="${1:-.}"
mkdir -p "${sandbox_root}"

cat >"${sandbox_root}/query.sql" <<'SQL'
SELECT wr.run_id, wr.launch_request_id, wr.status, wr.failure_class
  FROM worker_runs wr
  LEFT JOIN dispatch_audit_records dar
    ON dar.record_id = (
      SELECT dar2.record_id
        FROM dispatch_audit_records dar2
       WHERE dar2.launch_request_id = wr.launch_request_id
         AND dar2.metadata_json LIKE '%artifactEvidenceCheck%'
       ORDER BY dar2.recorded_at DESC
       LIMIT 1
    )
 WHERE wr.status = 'failed'
   AND wr.failure_class = 'supervision_recovery_lease_lost_with_artifact'
SQL

cat >"${sandbox_root}/schema.sql" <<'SQL'
CREATE TABLE worker_runs (
  run_id            text PRIMARY KEY,
  launch_request_id text,
  status            text,
  failure_class     text,
  failure_detail    text,
  ended_at          timestamptz
);

CREATE TABLE dispatch_audit_records (
  record_id         text PRIMARY KEY,
  launch_request_id text,
  record_kind       text,
  metadata_json     jsonb,
  recorded_at       timestamptz
);
SQL
