"""Local durable session snapshots with full-record optimistic concurrency control."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from storage.sessions import LeadClaim, SessionCommit, SessionPersistenceError, SessionRecord, session_record_digest


class SessionRevisionConflict(SessionPersistenceError):
    """The caller's snapshot is no longer the authoritative stored revision."""

    def __init__(self) -> None:
        super().__init__("conflict")


class SQLiteSessionRepository:
    """Single-file local repository. A receipt is returned only after a durable COMMIT."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("Unsupported local session schema version")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                last_active TEXT NOT NULL,
                has_started_opening INTEGER NOT NULL CHECK(has_started_opening IN (0, 1)),
                anketa_json TEXT NOT NULL,
                state_json TEXT NOT NULL,
                usage_json TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision >= 1)
            )""")
            connection.execute("PRAGMA user_version=1")
            connection.commit()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.create_function("session_epoch", 1, _epoch, deterministic=True)
            yield connection
        finally:
            connection.close()

    def save(self, record: SessionRecord) -> None:
        self.save_committed(record)

    def save_lead_state(self, record: SessionRecord) -> None:
        self.save_committed(record, lead_owner=True)

    def save_committed(self, record: SessionRecord, *, lead_owner: bool = False) -> SessionCommit:
        with self._connection() as connection:
            # The read, CAS decision and write are serialized across processes by SQLite.
            connection.execute("BEGIN IMMEDIATE")
            current = self._read(connection, record.id)
            stored = self._write(connection, record, current, lead_owner=lead_owner)
            connection.commit()
        # This fingerprint describes the actual committed payload, including DB-owned lead state.
        return SessionCommit(session_id=stored.id, revision=stored.revision,
                             state_digest=session_record_digest(stored))

    @staticmethod
    def _write(connection: sqlite3.Connection, record: SessionRecord,
               current: SessionRecord | None, *, lead_owner: bool) -> SessionRecord:
        if (not isinstance(record.id, str) or not record.id or type(record.has_started_opening) is not bool
                or not isinstance(record.state, dict) or not isinstance(record.usage, list)
                or (record.anketa is not None and not isinstance(record.anketa, dict))):
            raise ValueError("Invalid local session record")
        expected = current.revision if current is not None else 0
        if type(record.revision) is not int or record.revision != expected:
            raise SessionRevisionConflict()
        state = deepcopy(record.state)
        if current is not None and not lead_owner and "crm_lead" in current.state:
            if state.get("crm_lead") != current.state["crm_lead"]:
                raise SessionRevisionConflict()
        # The committed ACK must describe detached data even if a caller later edits its objects.
        stored = replace(record, state=state, anketa=deepcopy(record.anketa),
                         usage=deepcopy(record.usage), revision=expected + 1)
        values = (stored.id, _time(stored.created_at), _time(stored.last_active),
                  int(stored.has_started_opening), _json(stored.anketa), _json(stored.state),
                  _json(stored.usage), stored.revision)
        if current is None:
            connection.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?)", values)
        else:
            affected = connection.execute("""UPDATE sessions SET created_at=?, last_active=?,
                has_started_opening=?, anketa_json=?, state_json=?, usage_json=?, revision=?
                WHERE id=? AND revision=?""", (*values[1:], stored.id, expected)).rowcount
            if affected != 1:
                raise SessionRevisionConflict()
        return stored

    def load(self, session_id: str) -> SessionRecord | None:
        with self._connection() as connection:
            return self._read(connection, session_id)

    @staticmethod
    def _read(connection: sqlite3.Connection, session_id: str) -> SessionRecord | None:
        row = connection.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return _record(row) if row is not None else None

    def load_active_since(self, moment: datetime) -> list[SessionRecord]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM sessions WHERE session_epoch(last_active) >= session_epoch(?) "
                                      "ORDER BY session_epoch(last_active)",
                                      (_time(moment),)).fetchall()
        return [_record(row) for row in rows]

    def delete_before(self, moment: datetime) -> int:
        with self._connection() as connection:
            affected = connection.execute("DELETE FROM sessions WHERE session_epoch(last_active) < session_epoch(?)",
                                          (_time(moment),)).rowcount
            connection.commit()
        return affected

    def claim_lead_submission(self, record: SessionRecord) -> LeadClaim:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._read(connection, record.id)
            authoritative = current if current is not None else record
            lead = (current.state.get("crm_lead") or {}) if current is not None else {}
            # A stale caller cannot remove a persisted delivery hold or take an occupied slot.
            held = any(key in value.state for value in (record, authoritative)
                       for key in ("customer_context", "questionnaire_policy", "onloader"))
            if held or lead.get("status", "not_sent") != "not_sent":
                return LeadClaim(granted=False, lead_state=deepcopy(lead), revision=authoritative.revision)
            stored = self._write(connection, record, current, lead_owner=True)
            connection.commit()
        return LeadClaim(granted=True, revision=stored.revision)


def _time(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone-aware session timestamp is required")
    # Preserve the exact metadata representation included in the commit fingerprint.
    return value.isoformat()


def _epoch(value: str) -> int:
    moment = datetime.fromisoformat(value)
    _time(moment)
    delta = moment.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _record(row: tuple[Any, ...]) -> SessionRecord:
    if (not isinstance(row[0], str) or not row[0] or type(row[3]) is not int or row[3] not in (0, 1)
            or type(row[7]) is not int or row[7] < 1):
        raise ValueError("Invalid local session record metadata")
    anketa, state, usage = (_load_json(value) for value in row[4:7])
    if (anketa is not None and not isinstance(anketa, dict)) or not isinstance(state, dict) or not isinstance(usage, list):
        raise ValueError("Invalid local session record")
    created_at, last_active = datetime.fromisoformat(row[1]), datetime.fromisoformat(row[2])
    _time(created_at)
    _time(last_active)
    return SessionRecord(id=row[0], created_at=created_at,
                         last_active=last_active, has_started_opening=bool(row[3]),
                         anketa=anketa, state=state, usage=usage, revision=row[7])


def _load_json(value: str) -> Any:
    def invalid_constant(_: str) -> None:
        raise ValueError("Invalid local session JSON")
    return json.loads(value, parse_constant=invalid_constant)
