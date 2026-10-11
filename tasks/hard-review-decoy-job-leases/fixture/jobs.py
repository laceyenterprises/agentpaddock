"""Durable job queue: workers lease queued jobs and report results."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from db import Database, StoreError

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,
        payload TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'queued',
        attempts INTEGER NOT NULL DEFAULT 0,
        result TEXT,
        enqueued_at REAL NOT NULL,
        finished_at REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS leases (
        job_id INTEGER PRIMARY KEY REFERENCES jobs (id),
        holder TEXT NOT NULL,
        expires_at REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS jobs_state ON jobs (state, id)",
    "CREATE INDEX IF NOT EXISTS leases_expiry ON leases (expires_at)",
)

DEFAULT_LEASE_SECONDS = 60.0
# A job is attempted at most this many times. ``attempts`` counts claims and is
# incremented when the job is claimed, so it already includes the current one.
MAX_ATTEMPTS = 3


class LeaseLost(StoreError):
    """The caller does not hold the job's lease (it expired or was never granted)."""


@dataclass(frozen=True)
class Job:
    id: int
    kind: str
    payload: dict
    state: str
    attempts: int
    result: dict | None

    @classmethod
    def from_row(cls, row) -> Job:
        return cls(
            id=row["id"],
            kind=row["kind"],
            payload=json.loads(row["payload"]),
            state=row["state"],
            attempts=row["attempts"],
            result=None if row["result"] is None else json.loads(row["result"]),
        )


class QueryMixin:
    """Row helpers shared by the queue mixins. Requires ``self.db``."""

    db: Database

    def _one(self, sql: str, params: tuple = ()):
        return self.db.execute(sql, params).fetchone()

    def _all(self, sql: str, params: tuple = ()):
        return self.db.execute(sql, params).fetchall()

    def get(self, job_id: int) -> Job | None:
        row = self._one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        return None if row is None else Job.from_row(row)

    def counts(self) -> dict[str, int]:
        rows = self._all("SELECT state, COUNT(*) AS n FROM jobs GROUP BY state")
        return {row["state"]: row["n"] for row in rows}


class LeaseMixin:
    """Lease bookkeeping. JobQueue composes it with QueryMixin, which supplies _one and _all."""

    db: Database

    def holder_of(self, job_id: int) -> str | None:
        row = self._one("SELECT holder FROM leases WHERE job_id = ?", (job_id,))
        return None if row is None else row["holder"]

    def _require_holder(self, job_id: int, holder: str) -> None:
        current = self.holder_of(job_id)
        if current != holder:
            raise LeaseLost(f"job {job_id} is leased to {current!r}, not {holder!r}")

    def renew(
        self,
        job_id: int,
        holder: str,
        *,
        now: float | None = None,
        seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> float:
        """Extend ``holder``'s lease on ``job_id``; raise LeaseLost if it is gone."""
        now = time.time() if now is None else now
        expires_at = now + seconds
        cursor = self.db.execute(
            "UPDATE leases SET expires_at = ? WHERE job_id = ?",
            (expires_at, job_id),
        )
        if cursor.rowcount != 1:
            raise LeaseLost(f"job {job_id} has no lease")
        return expires_at

    def expired_leases(self, now: float) -> list[tuple[int, str]]:
        rows = self._all(
            "SELECT job_id, holder FROM leases WHERE expires_at <= ? ORDER BY job_id",
            (now,),
        )
        return [(row["job_id"], row["holder"]) for row in rows]


class JobQueue(QueryMixin, LeaseMixin):
    def __init__(self, db: Database) -> None:
        self.db = db
        for statement in SCHEMA:
            self.db.execute(statement)

    def enqueue(self, kind: str, payload: dict, *, now: float | None = None) -> int:
        now = time.time() if now is None else now
        cursor = self.db.execute(
            "INSERT INTO jobs (kind, payload, enqueued_at) VALUES (?, ?, ?)",
            (kind, json.dumps(payload, sort_keys=True), now),
        )
        return cursor.lastrowid

    def claim(
        self,
        holder: str,
        *,
        now: float | None = None,
        seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> Job | None:
        """Lease the oldest queued job to ``holder`` for ``seconds``."""
        now = time.time() if now is None else now
        with self.db.transaction():
            row = self._one("SELECT id FROM jobs WHERE state = 'queued' ORDER BY id LIMIT 1")
            if row is None:
                return None
            self.db.execute(
                "UPDATE jobs SET state = 'running', attempts = attempts + 1 WHERE id = ?",
                (row["id"],),
            )
            self.db.execute(
                "INSERT INTO leases (job_id, holder, expires_at) VALUES (?, ?, ?)",
                (row["id"], holder, now + seconds),
            )
        return self.get(row["id"])

    def complete(self, job_id: int, holder: str, result: dict, *, now: float | None = None) -> None:
        """Record ``result`` and drop ``holder``'s lease."""
        now = time.time() if now is None else now
        with self.db.transaction():
            self._require_holder(job_id, holder)
            self.db.execute(
                "UPDATE jobs SET state = 'done', result = ?, finished_at = ? WHERE id = ?",
                (json.dumps(result, sort_keys=True), now, job_id),
            )
            self.db.execute("DELETE FROM leases WHERE job_id = ?", (job_id,))

    def fail(self, job_id: int, holder: str, error: str, *, now: float | None = None) -> str:
        """End a failed attempt: requeue the job, or mark it dead once attempts are spent."""
        now = time.time() if now is None else now
        with self.db.transaction():
            self._require_holder(job_id, holder)
            state = self._release(job_id, {"error": error}, now=now)
        return state

    def _release(self, job_id: int, result: dict, *, now: float) -> str:
        """Drop the lease and requeue or kill the job. Call inside a transaction."""
        job = self.get(job_id)
        state = "dead" if job.attempts >= MAX_ATTEMPTS else "queued"
        self.db.execute(
            "UPDATE jobs SET state = ?, result = ?, finished_at = ? WHERE id = ?",
            (state, json.dumps(result, sort_keys=True), now if state == "dead" else None, job_id),
        )
        self.db.execute("DELETE FROM leases WHERE job_id = ?", (job_id,))
        return state
