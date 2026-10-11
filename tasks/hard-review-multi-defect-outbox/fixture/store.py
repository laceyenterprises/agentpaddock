"""SQLite outbox: messages wait here until a dispatcher delivers them."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY,
    topic TEXT NOT NULL,
    body TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    claimed_by TEXT,
    claimed_at REAL,
    last_error TEXT
)
"""


@dataclass(frozen=True)
class Message:
    id: int
    topic: str
    body: str
    attempts: int


class Outbox:
    """Outbox rows move pending -> claimed -> sent, or back to pending on a retry."""

    def __init__(self, path: str) -> None:
        # Autocommit: each statement is its own transaction.
        self._conn = sqlite3.connect(path, isolation_level=None, timeout=30)
        self._conn.execute(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def enqueue(self, topic: str, body: str) -> int:
        cursor = self._conn.execute("INSERT INTO outbox (topic, body) VALUES (?, ?)", (topic, body))
        return cursor.lastrowid

    def claim_batch(self, worker: str, limit: int = 50) -> list[Message]:
        """Claim up to ``limit`` pending messages for ``worker``, oldest first."""
        rows = self._conn.execute(
            "SELECT id, topic, body, attempts FROM outbox"
            " WHERE state = 'pending' ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
        claimed_at = time.time()
        for row in rows:
            self._conn.execute(
                "UPDATE outbox SET state = 'claimed', claimed_by = ?, claimed_at = ? WHERE id = ?",
                (worker, claimed_at, row[0]),
            )
        return [Message(*row) for row in rows]

    def mark_sent(self, message_id: int) -> None:
        self._conn.execute(
            "UPDATE outbox SET state = 'sent', claimed_by = NULL WHERE id = ?", (message_id,)
        )

    def release(self, message_id: int, error: str) -> None:
        """Return a failed message to pending, recording the failed attempt."""
        self._conn.execute(
            "UPDATE outbox SET state = 'pending', attempts = attempts + 1,"
            " last_error = ?, claimed_by = NULL, claimed_at = NULL WHERE id = ?",
            (error, message_id),
        )

    def dead_letter(self, message_id: int, error: str) -> None:
        """Retire a message that will not be retried."""
        self._conn.execute("DELETE FROM outbox WHERE id = ?", (message_id,))

    def dead_letters(self) -> list[tuple[int, str, str]]:
        """Dead-lettered messages for ``outbox replay``: id, topic, last error."""
        return self._conn.execute(
            "SELECT id, topic, last_error FROM outbox WHERE state = 'dead' ORDER BY id"
        ).fetchall()

    def replay(self, message_id: int) -> bool:
        """Requeue one dead-lettered message with a fresh attempt budget."""
        cursor = self._conn.execute(
            "UPDATE outbox SET state = 'pending', attempts = 0, last_error = NULL"
            " WHERE id = ? AND state = 'dead'",
            (message_id,),
        )
        return cursor.rowcount == 1

    def counts(self) -> dict[str, int]:
        return dict(self._conn.execute("SELECT state, COUNT(*) FROM outbox GROUP BY state"))
