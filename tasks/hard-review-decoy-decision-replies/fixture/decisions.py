"""Operator decisions: raised by workers, answered by operators, paged when overdue."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

OPEN_STATUS = "open"
ANSWERED_STATUS = "answered"
# Rows written before DEC-02 carry the legacy writer's "cancelled" spelling.
TERMINAL_STATUSES = frozenset({"answered", "canceled", "cancelled", "expired"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    worker TEXT NOT NULL,
    question TEXT NOT NULL,
    status TEXT NOT NULL,
    raised_at REAL NOT NULL,
    answered_at REAL,
    answer TEXT
);
CREATE TABLE IF NOT EXISTS dispatches (
    decision_id TEXT PRIMARY KEY REFERENCES decisions (id),
    receipt TEXT NOT NULL,
    dispatched_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS page_outbox (
    id INTEGER PRIMARY KEY,
    decision_id TEXT NOT NULL REFERENCES decisions (id),
    threshold_minutes INTEGER NOT NULL,
    text TEXT NOT NULL,
    queued_at REAL NOT NULL,
    sent_at REAL
);
"""


class DecisionError(Exception):
    """A decision operation is invalid for the decision's current state."""


@dataclass(frozen=True)
class Decision:
    id: str
    worker: str
    question: str
    status: str
    raised_at: float
    answered_at: float | None
    answer: str | None

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def to_json(self) -> str:
        return json.dumps(vars(self), sort_keys=True)


class DecisionStore:
    """SQLite-backed decisions. Each write commits before the method returns."""

    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def _decision(self, row: sqlite3.Row | None) -> Decision | None:
        return None if row is None else Decision(**dict(row))

    def raise_decision(
        self, decision_id: str, worker: str, question: str, *, now: float | None = None
    ) -> Decision:
        raised_at = time.time() if now is None else now
        with self._conn:
            self._conn.execute(
                "INSERT INTO decisions (id, worker, question, status, raised_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (decision_id, worker, question, OPEN_STATUS, raised_at),
            )
        return self.get(decision_id)

    def get(self, decision_id: str) -> Decision | None:
        row = self._conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
        return self._decision(row)

    def open_decisions(self) -> list[Decision]:
        rows = self._conn.execute(
            "SELECT * FROM decisions WHERE status = ? ORDER BY raised_at, id", (OPEN_STATUS,)
        ).fetchall()
        return [self._decision(row) for row in rows]

    def answered_undispatched(self) -> list[Decision]:
        """Answered decisions whose answer never reached the worker."""
        rows = self._conn.execute(
            "SELECT d.* FROM decisions AS d"
            " LEFT JOIN dispatches AS x ON x.decision_id = d.id"
            " WHERE d.status = ? AND x.decision_id IS NULL"
            " ORDER BY d.answered_at, d.id",
            (ANSWERED_STATUS,),
        ).fetchall()
        return [self._decision(row) for row in rows]

    def record_answer(self, decision_id: str, answer: str, *, now: float) -> bool:
        """Mark an open decision answered; return False when it was not open."""
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE decisions SET status = ?, answer = ?, answered_at = ?"
                " WHERE id = ? AND status = ?",
                (ANSWERED_STATUS, answer, now, decision_id, OPEN_STATUS),
            )
        return cursor.rowcount == 1

    def record_dispatch(self, decision_id: str, receipt: str, *, now: float) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO dispatches (decision_id, receipt, dispatched_at) VALUES (?, ?, ?)",
                (decision_id, receipt, now),
            )

    def dispatched(self, decision_id: str) -> str | None:
        """The dispatch receipt for ``decision_id``, or None if never dispatched."""
        row = self._conn.execute(
            "SELECT receipt FROM dispatches WHERE decision_id = ?", (decision_id,)
        ).fetchone()
        return None if row is None else row["receipt"]

    def cancel(self, decision_id: str, *, now: float) -> bool:
        """Cancel an open decision; return False when it was not open."""
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE decisions SET status = 'canceled', answered_at = ?"
                " WHERE id = ? AND status = ?",
                (now, decision_id, OPEN_STATUS),
            )
        return cursor.rowcount == 1

    def enqueue_page(
        self, decision_id: str, threshold_minutes: int, text: str, *, now: float
    ) -> int:
        """Queue an operator page and return its outbox id."""
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO page_outbox (decision_id, threshold_minutes, text, queued_at)"
                " VALUES (?, ?, ?, ?)",
                (decision_id, threshold_minutes, text, now),
            )
        return cursor.lastrowid

    def pages(self, decision_id: str) -> list[int]:
        """Thresholds already queued for ``decision_id``, in queue order."""
        rows = self._conn.execute(
            "SELECT threshold_minutes FROM page_outbox WHERE decision_id = ? ORDER BY id",
            (decision_id,),
        ).fetchall()
        return [row["threshold_minutes"] for row in rows]

    def prune_terminal(self, *, older_than: float) -> int:
        """Delete terminal decisions (and their dispatch rows) answered before ``older_than``."""
        statuses = sorted(TERMINAL_STATUSES)
        marks = ", ".join("?" for _ in statuses)
        with self._conn:
            self._conn.execute(
                f"DELETE FROM dispatches WHERE decision_id IN"
                f" (SELECT id FROM decisions WHERE status IN ({marks}) AND answered_at < ?)",
                (*statuses, older_than),
            )
            self._conn.execute(
                f"DELETE FROM page_outbox WHERE decision_id IN"
                f" (SELECT id FROM decisions WHERE status IN ({marks}) AND answered_at < ?)",
                (*statuses, older_than),
            )
            cursor = self._conn.execute(
                f"DELETE FROM decisions WHERE status IN ({marks}) AND answered_at < ?",
                (*statuses, older_than),
            )
        return cursor.rowcount
