"""SQLite access with lazy schema creation and default seed data."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from app.config import DEFAULT_CASH, DEFAULT_USER_ID, DEFAULT_WATCHLIST

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


class Database:
    """Thin wrapper around a SQLite file.

    One short-lived connection per unit of work. A process-wide lock serializes
    transactions so read-check-write sequences (e.g. trade validation) are atomic;
    in the single-user model contention is negligible.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._initialized = False

    def initialize(self) -> None:
        """Create tables and seed defaults if missing. Idempotent."""
        with self._lock:
            if self._initialized:
                return
            if str(self.path) != ":memory:":
                self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = self._connect()
            try:
                conn.executescript(SCHEMA_PATH.read_text())
                self._seed(conn)
                conn.commit()
            finally:
                conn.close()
            self._initialized = True

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection inside a transaction; commit on success, rollback on error."""
        self.initialize()
        with self._lock:
            conn = self._connect()
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _seed(conn: sqlite3.Connection) -> None:
        exists = conn.execute(
            "SELECT 1 FROM users_profile WHERE id = ?", (DEFAULT_USER_ID,)
        ).fetchone()
        if exists:
            return
        now = utc_now()
        conn.execute(
            "INSERT INTO users_profile (id, cash_balance, created_at) VALUES (?, ?, ?)",
            (DEFAULT_USER_ID, DEFAULT_CASH, now),
        )
        conn.executemany(
            "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
            [(new_id(), DEFAULT_USER_ID, t, now) for t in DEFAULT_WATCHLIST],
        )
