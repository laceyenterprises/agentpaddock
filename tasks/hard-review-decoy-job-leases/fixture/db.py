"""Thin sqlite3 wrapper: one autocommit connection, explicit transactions, typed errors."""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path


class StoreError(Exception):
    """A job-store operation failed."""


class BusyError(StoreError):
    """The database stayed locked past the busy timeout; the caller may retry."""


def _translate(error: sqlite3.Error) -> StoreError:
    """Map a driver error onto the store's error types.

    Every sqlite3 error raised through ``Database.execute`` leaves as a
    StoreError; lock timeouts ("database is locked", "database table is
    locked") become BusyError.
    """
    message = str(error)
    if isinstance(error, sqlite3.OperationalError) and "locked" in message:
        return BusyError(message)
    return StoreError(message)


class Database:
    """Autocommit connection; group writes with ``transaction()``."""

    def __init__(self, path: str | Path, *, busy_timeout: float = 5.0) -> None:
        self.path = Path(path)
        self._busy_timeout = busy_timeout
        self._conn = self._open(f"file:{self.path}")

    def _open(self, uri: str) -> sqlite3.Connection:
        conn = sqlite3.connect(uri, uri=True, timeout=self._busy_timeout, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def close(self) -> None:
        self._conn.close()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        try:
            return self._conn.execute(sql, params)
        except sqlite3.Error as error:
            raise _translate(error) from error

    @contextlib.contextmanager
    def transaction(self) -> Iterator[Database]:
        """Run the block in one write transaction.

        ``BEGIN IMMEDIATE`` takes SQLite's write lock before the block runs, so
        a second writer waits (up to the busy timeout) at its own BEGIN. Reads
        and writes inside the block are therefore serialised against every
        other writer.
        """
        self.execute("BEGIN IMMEDIATE")
        try:
            yield self
            self.execute("COMMIT")
        except BaseException:
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise

    @contextlib.contextmanager
    def readonly(self) -> Iterator[sqlite3.Connection]:
        """A separate read-only connection for reports; it never takes the write lock."""
        conn = self._open(f"file:{self.path}?mode=ro")
        try:
            yield conn
        finally:
            conn.close()
