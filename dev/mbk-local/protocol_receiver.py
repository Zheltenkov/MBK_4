"""Persistent local protocol simulator; it never executes Bitrix or forwards data."""

from __future__ import annotations

import base64
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time


EVIDENCE_LEVEL = "protocol_simulation"
ID_OFFSET = 700_000_000
MAX_BODY_BYTES = 65_536


class IdempotencyConflict(Exception):
    """An existing operation key was submitted with a different payload."""


class StorageError(Exception):
    """No receipt may be returned because the ledger transaction failed."""


@dataclass(frozen=True)
class Receipt:
    lead_id: str
    created: bool


class Ledger:
    """Bind each operation durably; phone aliases keep their original binding."""

    def __init__(self, path: Path, run_id: str, *, clock: Callable[[], float] = time.time,
                 before_commit: Callable[[], None] | None = None, dedup_seconds: int = 900) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id) or dedup_seconds <= 0:
            raise ValueError("Invalid simulator namespace or deduplication interval")
        self.path, self.run_id = Path(path), run_id
        self.clock, self.before_commit, self.dedup_seconds = clock, before_commit, dedup_seconds
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as connection:
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS simulated_leads (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL, phone_hash TEXT NOT NULL,
                        payload_hash TEXT NOT NULL, created_at REAL NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS simulated_phone_window
                        ON simulated_leads(run_id,phone_hash,created_at);
                    CREATE TABLE IF NOT EXISTS operations (
                        run_id TEXT NOT NULL, operation_id TEXT NOT NULL,
                        payload_hash TEXT NOT NULL, lead_id INTEGER NOT NULL,
                        PRIMARY KEY(run_id,operation_id),
                        FOREIGN KEY(lead_id) REFERENCES simulated_leads(id)
                    );
                    CREATE TABLE IF NOT EXISTS ledger_metadata (
                        key TEXT PRIMARY KEY, value TEXT NOT NULL
                    );
                    INSERT OR IGNORE INTO ledger_metadata(key,value)
                        VALUES('evidence_level','protocol_simulation');
                """)
        except (sqlite3.Error, OSError) as error:
            raise StorageError("Simulator ledger initialization failed") from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def accept(self, operation_id: str, payload: dict[str, object], phone: str) -> Receipt:
        if not operation_id or len(operation_id) > 256 or not phone:
            raise ValueError("Invalid local operation identity")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        payload_hash = hashlib.sha256(canonical.encode()).hexdigest()
        phone_hash = hashlib.sha256(phone.encode()).hexdigest()
        now = float(self.clock())
        if not math.isfinite(now):
            raise ValueError("Invalid simulator clock")
        try:
            connection = self._connect()
        except sqlite3.Error as error:
            raise StorageError("Simulator connection failed; no receipt") from error
        try:
            # Serialize the identity check and insert, including concurrent retries.
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT payload_hash,lead_id FROM operations WHERE run_id=? AND operation_id=?",
                (self.run_id, operation_id),
            ).fetchone()
            if previous is not None:
                if previous[0] != payload_hash:
                    raise IdempotencyConflict("Local operation payload changed")
                connection.commit()
                return Receipt(str(ID_OFFSET + previous[1]), False)
            existing = connection.execute(
                "SELECT id FROM simulated_leads WHERE run_id=? AND phone_hash=? "
                "AND created_at>? AND created_at<=? ORDER BY created_at DESC,id DESC LIMIT 1",
                (self.run_id, phone_hash, now - self.dedup_seconds, now),
            ).fetchone()
            created = existing is None
            if created:
                cursor = connection.execute(
                    "INSERT INTO simulated_leads(run_id,phone_hash,payload_hash,created_at) VALUES(?,?,?,?)",
                    (self.run_id, phone_hash, payload_hash, now),
                )
                lead_id = cursor.lastrowid
                if lead_id is None:
                    raise StorageError("Simulator did not obtain a durable identity")
            else:
                lead_id = existing[0]
            # Phone-dedup aliases must survive beyond the phone window as operations.
            connection.execute("INSERT INTO operations(run_id,operation_id,payload_hash,lead_id) VALUES(?,?,?,?)",
                               (self.run_id, operation_id, payload_hash, lead_id))
            if self.before_commit is not None:
                self.before_commit()
            connection.commit()
            return Receipt(str(ID_OFFSET + lead_id), created)
        except IdempotencyConflict:
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            raise StorageError("Simulator transaction failed; no receipt") from error
        finally:
            connection.close()

    def stats(self) -> dict[str, object]:
        try:
            with closing(self._connect()) as connection:
                leads = connection.execute("SELECT id,payload_hash FROM simulated_leads WHERE run_id=? ORDER BY id",
                                           (self.run_id,)).fetchall()
                operations = connection.execute("SELECT COUNT(*) FROM operations WHERE run_id=?", (self.run_id,)).fetchone()[0]
            return {"evidence_level": EVIDENCE_LEVEL, "lead_count": len(leads), "operation_count": operations,
                    "lead_ids": [str(ID_OFFSET + row[0]) for row in leads], "payload_hashes": [row[1] for row in leads]}
        except sqlite3.Error as error:
            raise StorageError("Simulator statistics unavailable") from error


def validate_payload(value: object) -> tuple[dict[str, object], str, str]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("invalid_object")
    if any(not isinstance(item, (str, int, float, bool, type(None))) for item in value.values()):
        raise ValueError("invalid_field_type")
    phone = re.sub(r"\D", "", str(value.get("PHONE", "")))
    other_phone = re.sub(r"\D", "", str(value.get("PHONE_DEAL", "")))
    if not re.fullmatch(r"[78]000\d{7}", phone) or phone != other_phone:
        raise ValueError("not_synthetic_phone")
    operation = value.get("UTM_CAMPAIGN")
    category = value.get("UF_CRM_1491296976")
    # The real Laravel sanitizer emits these two fields as strings on the wire.
    # Validate equivalent wire forms without rewriting the payload or its hash.
    category_valid = (isinstance(category, int) and not isinstance(category, bool)) or (
        isinstance(category, str) and re.fullmatch(r"0|[1-9]\d{0,9}", category) is not None
    )
    return_response = value.get("RETURN_RESPONSE")
    return_valid = return_response is True or (isinstance(return_response, str) and return_response == "1")
    if (not isinstance(operation, str) or not re.fullmatch(r"local-write-[A-Za-z0-9]+", operation)
            or not return_valid or value.get("IS_BFL_PRODUCT") is not True
            or not category_valid or str(value.get("SUMM")) != "0"):
        raise ValueError("unsupported_fixture")
    return value, phone, operation


def reject_json_constant(value: str) -> None:
    raise ValueError("Non-finite JSON constants are not accepted")


def make_handler(ledger: Ledger, path: str, username: str, password: str) -> type[BaseHTTPRequestHandler]:
    expected_auth = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return  # No raw paths, payloads, phone numbers or auth headers in logs.

        def reply(self, status: int, body: dict[str, object]) -> None:
            content = json.dumps({**body, "evidence_level": EVIDENCE_LEVEL}, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass  # A committed receipt stays durable after a lost response.

        def authorized(self) -> bool:
            return hmac.compare_digest(self.headers.get("Authorization", "").encode(), expected_auth.encode())

        def do_GET(self) -> None:
            if self.path == "/health":
                self.reply(200, {"service": "local-protocol-receiver", "forwarding": False})
            elif self.path == "/lab/ledger-stats":
                if not self.authorized():
                    self.reply(401, {"error": "unauthorized"})
                    return
                try:
                    self.reply(200, ledger.stats())
                except StorageError:
                    self.reply(500, {"error": "storage_error"})
            else:
                self.reply(404, {"error": "not_found"})

        def do_POST(self) -> None:
            if self.path != path:
                self.reply(404, {"error": "not_found"})
                return
            if not self.authorized():
                self.reply(401, {"error": "unauthorized"})
                return
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or self.headers.get("Transfer-Encoding"):
                self.reply(400, {"error": "invalid_framing"})
                return
            try:
                size = int(lengths[0])
                if not 0 < size <= MAX_BODY_BYTES:
                    raise ValueError()
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    raise ValueError()
                self.connection.settimeout(5)
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise ValueError()
            except (ValueError, OSError):
                self.reply(400, {"error": "invalid_framing"})
                return
            try:
                value = json.loads(raw, parse_constant=reject_json_constant)
            except (ValueError, UnicodeError):
                self.reply(400, {"error": "invalid_json"})
                return
            if not isinstance(value, dict):
                self.reply(400, {"error": "invalid_envelope"})
                return
            try:
                payload, phone, operation = validate_payload(value)
                receipt = ledger.accept(operation, payload, phone)
                self.reply(200, {"result": {"leadID": receipt.lead_id, "created": receipt.created}})
            except ValueError:
                self.reply(422, {"error": "unsupported_fixture_input"})
            except IdempotencyConflict:
                self.reply(409, {"error": "idempotency_conflict"})
            except StorageError:
                self.reply(500, {"error": "storage_error"})

    return Handler


def main() -> None:
    username, password, path = (os.environ[name] for name in ["MBK_PROTOCOL_USER", "MBK_PROTOCOL_PASSWORD", "MBK_PROTOCOL_PATH"])
    if not username or not password or not path.startswith("/") or "?" in path or "#" in path:
        raise ValueError("Invalid local receiver configuration")
    ledger = Ledger(Path(os.environ["MBK_PROTOCOL_DB"]), os.environ["MBK_PROTOCOL_RUN_ID"],
                    dedup_seconds=int(os.environ.get("MBK_PROTOCOL_DEDUP_SECONDS", "900")))
    ThreadingHTTPServer(("0.0.0.0", 8000), make_handler(ledger, path, username, password)).serve_forever()


if __name__ == "__main__":
    main()
