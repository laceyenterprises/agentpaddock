"""Lease sweeper: requeue jobs whose holder stopped renewing."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from db import BusyError, Database
from jobs import JobQueue

LOGGER = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 15.0
BUSY_BACKOFF_SECONDS = 0.05


def _sweep_once(queue: JobQueue, now: float) -> dict[str, list[int]]:
    swept: dict[str, list[int]] = {"queued": [], "dead": []}
    with queue.db.transaction():
        for job_id, holder in queue.expired_leases(now):
            state = queue._release(job_id, {"error": f"lease expired (holder {holder})"}, now=now)
            swept[state].append(job_id)
    return swept


def sweep(queue: JobQueue, *, now: float | None = None, retries: int = 3) -> dict[str, list[int]]:
    """Release every expired lease; retry a sweep that hits a busy database."""
    if retries < 1:
        raise ValueError("retries must be at least 1")
    now = time.monotonic() if now is None else now
    for attempt in range(retries):
        try:
            swept = _sweep_once(queue, now)
            break
        except BusyError:
            if attempt == retries - 1:
                raise
            LOGGER.warning("sweeper.busy attempt=%d", attempt + 1)
            time.sleep(BUSY_BACKOFF_SECONDS * (attempt + 1))
    for job_id in swept["dead"]:
        LOGGER.error("sweeper.job_dead job_id=%d", job_id)
    return swept


def status_report(db: Database) -> dict[str, object]:
    """Queue and lease counts from a read-only connection."""
    with db.readonly() as conn:
        states = {
            row["state"]: row["n"]
            for row in conn.execute("SELECT state, COUNT(*) AS n FROM jobs GROUP BY state")
        }
        leased = conn.execute("SELECT COUNT(*) AS n FROM leases").fetchone()["n"]
    return {"states": states, "leased": leased}


def run_forever(queue: JobQueue, *, interval: float = SWEEP_INTERVAL_SECONDS) -> None:
    while True:
        try:
            swept = sweep(queue)
        except BusyError:
            LOGGER.exception("sweeper.gave_up")
        else:
            if swept["queued"] or swept["dead"]:
                LOGGER.info("sweeper.swept %s", json.dumps(swept, sort_keys=True))
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="job-sweeper")
    parser.add_argument("--db", required=True, help="job-store SQLite database")
    parser.add_argument("--once", action="store_true", help="sweep once and print the result")
    parser.add_argument("--status", action="store_true", help="print queue and lease counts")
    args = parser.parse_args(argv)
    db = Database(args.db)
    try:
        if args.status:
            print(json.dumps(status_report(db), sort_keys=True))
            return 0
        queue = JobQueue(db)
        if args.once:
            print(json.dumps(sweep(queue), sort_keys=True))
            return 0
        run_forever(queue)
    except KeyboardInterrupt:
        print("job-sweeper: interrupted", file=sys.stderr)
        return 130
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
