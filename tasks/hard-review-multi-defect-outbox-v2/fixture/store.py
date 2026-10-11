"""The outbox table: messages written in the caller's transaction, delivered later.

Each message moves pending -> claimed -> sent, or back to pending for a retry, or
to dead once it has used its attempts. Several dispatcher processes share one
table, so every transition out of ``claimed`` names the worker holding the claim.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from db import Database

# A message is attempted at most this many times before it is dead-lettered.
MAX_ATTEMPTS = 5
# A claim older than this belongs to a dispatcher that crashed or hung.
CLAIM_TIMEOUT_SECONDS = 600.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    body TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at REAL NOT NULL,
    sent_at REAL
)
"""

# Columns added by this change, with the value existing rows take.
ADDED_COLUMNS = (
    ("next_attempt_at", "REAL NOT NULL DEFAULT 0"),
    ("claimed_by", "TEXT"),
    ("claimed_at", "REAL"),
    ("updated_at", "REAL NOT NULL DEFAULT 0"),
)

INDEXES = (
    "CREATE INDEX IF NOT EXISTS outbox_due ON outbox (state, next_attempt_at, id)",
    "CREATE INDEX IF NOT EXISTS outbox_claims ON outbox (state, claimed_at)",
)

STATES = ("pending", "claimed", "sent", "dead")


@dataclass(frozen=True)
class Message:
    id: int
    topic: str
    body: str
    attempts: int
    last_error: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Message:
        return cls(row["id"], row["topic"], row["body"], row["attempts"], row["last_error"])


class Outbox:
    def __init__(self, db: Database) -> None:
        self._db = db

    def migrate(self) -> None:
        """Create the table, add this change's columns and build the indexes."""
        self._db.execute(SCHEMA)
        existing = {row["name"] for row in self._db.execute("PRAGMA table_info(outbox)")}
        for name, declaration in ADDED_COLUMNS:
            if name not in existing:
                self._db.execute(f"ALTER TABLE outbox ADD COLUMN {name} {declaration}")
        for statement in INDEXES:
            self._db.execute(statement)

    def enqueue(self, topic: str, body: str, *, now: float) -> int:
        cursor = self._db.execute(
            "INSERT INTO outbox (topic, body, created_at, next_attempt_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (topic, body, now, now, now),
        )
        return cursor.lastrowid

    def claim_batch(self, worker: str, *, now: float, limit: int) -> list[Message]:
        """Claim up to ``limit`` due messages for ``worker``, oldest first.

        Claiming starts an attempt, so it increments ``attempts``: a claimed
        message's ``attempts`` is the number of the attempt now in progress.
        """
        due = self._db.execute(
            "SELECT id FROM outbox WHERE state = 'pending' AND next_attempt_at <= ? "
            "ORDER BY id LIMIT ?",
            (now, limit),
        ).fetchall()
        ids = [row["id"] for row in due]
        if not ids:
            return []
        marks = ", ".join("?" for _ in ids)
        with self._db.transaction():
            self._db.execute(
                "UPDATE outbox SET state = 'claimed', claimed_by = ?, claimed_at = ?, "
                f"attempts = attempts + 1, updated_at = ? WHERE id IN ({marks})",
                (worker, now, now, *ids),
            )
            rows = self._db.execute(
                f"SELECT * FROM outbox WHERE id IN ({marks}) ORDER BY id", tuple(ids)
            ).fetchall()
        return [Message.from_row(row) for row in rows]

    def mark_sent(self, message_id: int, worker: str, *, now: float) -> bool:
        """Record a delivery. False when ``worker`` no longer holds the claim."""
        cursor = self._db.execute(
            "UPDATE outbox SET state = 'sent', sent_at = ?, claimed_by = NULL, "
            "claimed_at = NULL, updated_at = ? "
            "WHERE id = ? AND state = 'claimed' AND claimed_by = ?",
            (now, now, message_id, worker),
        )
        return cursor.rowcount == 1

    def retry(
        self, message_id: int, worker: str, *, error: str, next_attempt_at: float, now: float
    ) -> bool:
        """Return a failed message to pending, due at ``next_attempt_at``."""
        cursor = self._db.execute(
            "UPDATE outbox SET state = 'pending', last_error = ?, next_attempt_at = ?, "
            "claimed_by = NULL, claimed_at = NULL, updated_at = ? "
            "WHERE id = ? AND state = 'claimed' AND claimed_by = ?",
            (error, next_attempt_at, now, message_id, worker),
        )
        return cursor.rowcount == 1

    def dead_letter(self, message_id: int, worker: str, *, error: str, now: float) -> bool:
        """Move a message to dead with its last error; it stays for replay."""
        cursor = self._db.execute(
            "UPDATE outbox SET state = 'dead', last_error = ?, claimed_by = NULL, "
            "claimed_at = NULL, updated_at = ? "
            "WHERE id = ? AND state = 'claimed' AND claimed_by = ?",
            (error, now, message_id, worker),
        )
        return cursor.rowcount == 1

    def reclaim_expired(self, *, now: float, timeout: float = CLAIM_TIMEOUT_SECONDS) -> int:
        """Release claims older than ``timeout``; each counts as a failed attempt.

        A released message whose attempts are used up is dead-lettered instead of
        returned to pending. Returns how many claims were released.
        """
        cursor = self._db.execute(
            "UPDATE outbox SET "
            "state = CASE WHEN attempts > ? THEN 'dead' ELSE 'pending' END, "
            "last_error = 'claim expired', claimed_by = NULL, claimed_at = NULL, "
            "next_attempt_at = ?, updated_at = ? "
            "WHERE state = 'claimed' AND claimed_at < ?",
            (MAX_ATTEMPTS, now, now, now - timeout),
        )
        return cursor.rowcount

    def get(self, message_id: int) -> Message | None:
        row = self._db.execute("SELECT * FROM outbox WHERE id = ?", (message_id,)).fetchone()
        return None if row is None else Message.from_row(row)

    def state_of(self, message_id: int) -> str | None:
        row = self._db.execute("SELECT state FROM outbox WHERE id = ?", (message_id,)).fetchone()
        return None if row is None else row["state"]

    def dead_letters(self, *, limit: int = 100) -> list[Message]:
        rows = self._db.execute(
            "SELECT * FROM outbox WHERE state = 'dead' ORDER BY updated_at, id LIMIT ?",
            (limit,),
        ).fetchall()
        return [Message.from_row(row) for row in rows]

    def replay(self, message_id: int, *, now: float) -> bool:
        """Requeue one dead letter with a fresh set of attempts."""
        with self._db.transaction():
            row = self._db.execute(
                "SELECT state FROM outbox WHERE id = ?", (message_id,)
            ).fetchone()
            if row is None or row["state"] != "dead":
                return False
            self._db.execute(
                "UPDATE outbox SET state = 'pending', attempts = 0, last_error = NULL, "
                "next_attempt_at = ?, updated_at = ? WHERE id = ?",
                (now, now, message_id),
            )
        return True

    def discard(self, message_id: int) -> bool:
        """Delete one dead letter the operator has decided not to deliver."""
        cursor = self._db.execute(
            "DELETE FROM outbox WHERE id = ? AND state = 'dead'", (message_id,)
        )
        return cursor.rowcount == 1

    def counts(self) -> dict[str, int]:
        rows = self._db.execute("SELECT state, COUNT(*) AS n FROM outbox GROUP BY state")
        return {row["state"]: row["n"] for row in rows.fetchall()}

    def oldest_due(self, *, now: float) -> float | None:
        """Seconds the oldest due pending message has waited, or None."""
        row = self._db.execute(
            "SELECT MIN(next_attempt_at) AS due FROM outbox "
            "WHERE state = 'pending' AND next_attempt_at <= ?",
            (now,),
        ).fetchone()
        return None if row["due"] is None else now - row["due"]

    def purge(self, *, older_than: float, now: float) -> int:
        """Delete finished messages last updated more than ``older_than`` seconds ago."""
        cursor = self._db.execute(
            "DELETE FROM outbox WHERE state NOT IN ('pending', 'claimed') AND updated_at < ?",
            (now - older_than,),
        )
        return cursor.rowcount
