#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
mkdir -p "${sandbox_root}"

cat >"${sandbox_root}/loop.py" <<'PY'
def _mark_processed(conn, run_id, report):
    now = _utc_now()
    conn.execute(
        """
        INSERT INTO processed_runs (run_id, processed_at, status)
        VALUES (?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET processed_at = excluded.processed_at
        """,
        (run_id, now, report.status),
    )


def run_pass(*, conn=None, hq_root=None, limit=25):
    result = PassResult()
    owned = None
    try:
        active = conn
        if active is None:
            owned = _open_ledger(hq_root)
            active = owned.conn
        for run_id in _select_run_ids(active, limit=limit):
            report = _extract(active, run_id=run_id)
            result.processed += 1
            _mark_processed(active, run_id, report)
    except Exception as exc:
        result.status = "failed"
        result.record_error("pass", exc)
    finally:
        if owned is not None:
            owned.close()
        result.finished_at = _utc_now()
    return result
PY

cat >"${sandbox_root}/symptom.txt" <<'TXT'
Three consecutive passes on the live host, byte-identical:

  runs        : selected 25 processed 25 skipped 25  (capped)
  skipped by  : run_not_linked_to_session=25

SELECT count(*) FROM processed_runs;  ->  0

The selector excludes runs already present in processed_runs. Every pass
re-selects the same 25 runs and the queue never advances.
TXT
