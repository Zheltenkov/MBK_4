"""Network-free backend simulator with a durable, cross-session operation ledger."""
from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from customer_context.contracts import ApplicationReference

from .contracts import OnloaderCommand, OnloaderError, OnloaderReceipt, OnloaderSnapshot


class LocalOnloaderBackend:
    """Synthetic source and signal receiver. It creates no CRM lead or external delivery."""

    def __init__(self, ledger_path: Path, *, clock: Callable[[], float] = time.time) -> None:
        self._path = str(ledger_path)
        self._clock = clock
        self._lock = threading.RLock()
        with self._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS onloader_operations (
                operation_ref TEXT PRIMARY KEY, command_json TEXT NOT NULL,
                receipt_json TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS onloader_sources (
                handle TEXT PRIMARY KEY, application_id TEXT NOT NULL, subject_id TEXT NOT NULL,
                snapshot_json TEXT NOT NULL, UNIQUE(application_id, subject_id)
            )""")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path, timeout=5)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            with connection:
                yield connection
        finally:
            connection.close()

    def register(self, snapshot: OnloaderSnapshot) -> None:
        # Fixture setup is explicit. No HTTP endpoint can register a source or issue a capability.
        with self._lock, self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            # Handle rotation replaces the previous authoritative revision of this form.
            db.execute("DELETE FROM onloader_sources WHERE application_id=? AND subject_id=?",
                       (snapshot.reference.application_id, snapshot.reference.subject_id))
            raw = snapshot.model_dump(mode="json")
            raw["delivery_handle"] = snapshot.delivery_handle.get_secret_value()
            db.execute("INSERT INTO onloader_sources VALUES (?, ?, ?, ?)", (
                snapshot.delivery_handle.get_secret_value(), snapshot.reference.application_id,
                snapshot.reference.subject_id, json.dumps(raw, ensure_ascii=False, allow_nan=False),
            ))

    def fetch(self, reference: ApplicationReference) -> OnloaderSnapshot | None:
        with self._lock, self._connection() as db:
            row = db.execute("SELECT snapshot_json FROM onloader_sources WHERE application_id=? AND subject_id=?",
                             (reference.application_id, reference.subject_id)).fetchone()
        snapshot = OnloaderSnapshot.model_validate_json(row[0]) if row is not None else None
        return snapshot if snapshot is not None and snapshot.reference == reference else None

    def inspect(self, handle: str) -> OnloaderSnapshot | None:
        with self._lock, self._connection() as db:
            return self._read_snapshot(db, handle)

    @staticmethod
    def _read_snapshot(db: sqlite3.Connection, handle: str) -> OnloaderSnapshot | None:
        row = db.execute("SELECT snapshot_json FROM onloader_sources WHERE handle=?", (handle,)).fetchone()
        return OnloaderSnapshot.model_validate_json(row[0]) if row is not None else None

    def submit(self, handle: str, command: OnloaderCommand) -> OnloaderReceipt:
        serialized = command.model_dump_json()
        # The stable backend operation key is independent of SID, event and offer revisions.
        with self._lock, self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT command_json, receipt_json FROM onloader_operations WHERE operation_ref=?",
                             (command.operation_ref,)).fetchone()
            if row is not None:
                if json.loads(row[0]) != command.model_dump(mode="json"):
                    raise OnloaderError("onloader_operation_conflict")
                return OnloaderReceipt.model_validate_json(row[1])
            snapshot = self._read_snapshot(db, handle)
            if snapshot is None or snapshot.operation_ref != command.operation_ref:
                raise OnloaderError("onloader_binding_conflict")
            if snapshot.active is not True or snapshot.contact_known is not True or snapshot.delivery_owner != "site_backend":
                raise OnloaderError("onloader_forbidden")
            now = self._clock()
            if (not 0 <= now - snapshot.observed_at.timestamp() < 300
                    or now >= min(snapshot.expires_at.timestamp(), command.authorization_expires_at)):
                raise OnloaderError("onloader_stale")
            if (snapshot.offer.offer_id, snapshot.offer.version, snapshot.offer.consent_version) != (
                command.offer_id, command.offer_version, command.consent_version
            ) or snapshot.offer.digest() != command.offer_digest:
                raise OnloaderError("onloader_offer_conflict")
            receipt = OnloaderReceipt(**command.model_dump(),
                                      status="queued" if command.decision == "accept" else "declined",
                                      receipt_id=str(uuid.uuid4()))
            db.execute("INSERT INTO onloader_operations VALUES (?, ?, ?)",
                       (command.operation_ref, serialized, receipt.model_dump_json()))
            return receipt

    def reconcile(self, handle: str, command: OnloaderCommand) -> OnloaderReceipt | None:
        snapshot = self.inspect(handle)
        if snapshot is None or snapshot.operation_ref != command.operation_ref:
            raise OnloaderError("onloader_binding_conflict")
        with self._connection() as db:
            row = db.execute("SELECT command_json, receipt_json FROM onloader_operations WHERE operation_ref=?",
                             (command.operation_ref,)).fetchone()
        if row is None:
            return None
        if json.loads(row[0]) != command.model_dump(mode="json"):
            raise OnloaderError("onloader_operation_conflict")
        return OnloaderReceipt.model_validate_json(row[1])

    def operation_count(self) -> int:
        """Local acceptance metric. A queued signal is distinct from partner delivery."""
        with self._connection() as db:
            return int(db.execute("SELECT COUNT(*) FROM onloader_operations").fetchone()[0])
