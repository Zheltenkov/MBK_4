"""Atomic request reservations across workers sharing this private SQLite ledger."""
from __future__ import annotations

import math
import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path


class SqliteLookupBudget:
    """At most limit reservations per sliding 24h; never refund unknown HTTP outcomes.

    Every bot worker MUST use the same persistent ledger. This is our budget only;
    callers outside this bot can still exhaust upstream's shared 100/day quota.
    Separate host-local copies do not coordinate replicas and are not deployment-ready.
    No phone, CRM body, capability or token is persisted here.
    """

    def __init__(self, path: str | Path, *, limit: int = 100) -> None:
        if type(limit) is not int or not 1 <= limit <= 100 or str(path) == ":memory:":
            raise ValueError("shared persistent lookup budget and limit1..100 required")
        self._path = Path(path).resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._limit = limit
        self._lock = threading.Lock()
        # Private file creation precedes SQLite access; do not chmod existing unrelated files.
        try:
            descriptor = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass  # A second worker may already have created the shared ledger.
        else:
            os.close(descriptor)
        with closing(self._connect()) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS context_lookup_reservations (reserved_at REAL NOT NULL)")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=2.0)

    def reserve(self, now: float) -> bool:
        if not math.isfinite(now):
            return False
        # BEGIN IMMEDIATE serializes independent processes before count+insert.
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM context_lookup_reservations WHERE reserved_at <= ?", (now - 86400,))
            count = connection.execute("SELECT COUNT(*) FROM context_lookup_reservations").fetchone()[0]
            if count >= self._limit:
                return False
            connection.execute("INSERT INTO context_lookup_reservations (reserved_at) VALUES (?)", (now,))
            return True
