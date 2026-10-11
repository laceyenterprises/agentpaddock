"""A small sqlite3 wrapper shared by the outbox, its dispatchers and its CLI."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager


class Database:
    """One connection in autocommit mode; ``transaction()`` groups statements."""

    def __init__(self, path: str, *, busy_timeout: float = 30.0) -> None:
        self._conn = sqlite3.connect(path, isolation_level=None, timeout=busy_timeout)
        self._conn.row_factory = sqlite3.Row
        self._in_transaction = False

    def close(self) -> None:
        self._conn.close()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Run one statement. Outside ``transaction()`` it commits on its own."""
        return self._conn.execute(sql, params)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Run the block as one transaction: BEGIN IMMEDIATE ... COMMIT.

        IMMEDIATE takes the database write lock at BEGIN, so no other connection
        can write between a read inside the block and the COMMIT. A second writer
        waits (up to ``busy_timeout``) at its own BEGIN. Statements run before the
        block are not part of it. The block rolls back on any exception.
        """
        if self._in_transaction:
            raise RuntimeError("transactions do not nest")
        self._conn.execute("BEGIN IMMEDIATE")
        self._in_transaction = True
        try:
            yield
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")
        finally:
            self._in_transaction = False
