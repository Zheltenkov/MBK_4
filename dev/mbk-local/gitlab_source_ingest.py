"""Temporary authenticated loopback receiver for pinned GitLab source blobs."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import tempfile
from threading import Lock, Thread


REF = "240fd2ae748e1b383b7de0018824c0c01987f183"
ORIGIN = "https://gitlab.mbk-credit.ru"
MAX_BLOB = 8 * 1024 * 1024
MAX_REQUEST = 12 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "logs/local-stack-2026-11-06"


class SourceRejected(ValueError):
    """Unpinned, unsafe, mismatched, or conflicting input; never echo raw input."""


def allowed_path(path: str) -> bool:
    pure = PurePosixPath(path)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        return False
    if "\\" in path or ":" in path or "\0" in path:
        return False
    if not path.startswith(("bitrix/modules/", "bitrix/php_interface/", "local/")):
        return False
    blocked = {"dbconn.php", "license_key.php", ".settings.php", ".settings_extra.php"}
    # Module DI definitions are library code; root deployment settings stay denied.
    module_di = path in {f"bitrix/modules/{module}/.settings.php" for module in ("main", "crm", "currency")}
    if (pure.name.lower() in blocked and not module_di) or pure.name.lower().startswith(".env"):
        return False
    # Within module library trees, cache/session/key directories contain source classes.
    # Deployment data outside those trees remains excluded.
    runtime_data = {"upload", "cache", "managed_cache", "stack_cache", "sessions", "backups", "keys"}
    if not path.startswith("bitrix/modules/") and any(part.lower() in runtime_data for part in pure.parts):
        return False
    return pure.suffix.lower() in {".php", ".inc", ".sql", ".json", ".xml", ".wsdl", ".xsd"}


class Store:
    def __init__(self, destination: Path, manifest: Path) -> None:
        self.destination = destination.resolve()
        self.manifest = manifest
        self.lock = Lock()
        self.count = 0
        self.byte_count = 0

    def ingest(self, value: object) -> dict[str, object]:
        if not isinstance(value, dict) or value.get("ref") != REF or value.get("encoding") != "base64":
            raise SourceRejected("untrusted_envelope")
        path, blob, content = value.get("file_path"), value.get("blob_id"), value.get("content")
        if not isinstance(path, str) or not allowed_path(path):
            raise SourceRejected("unsafe_path")
        if not isinstance(blob, str) or re.fullmatch(r"[a-f0-9]{40}", blob) is None:
            raise SourceRejected("invalid_blob_id")
        if not isinstance(content, str) or len(content) > MAX_REQUEST:
            raise SourceRejected("invalid_blob_content")
        try:
            decoded = base64.b64decode(content.replace("\r", "").replace("\n", ""), validate=True)
        except ValueError as exc:
            raise SourceRejected("invalid_base64") from exc
        if len(decoded) > MAX_BLOB:
            raise SourceRejected("blob_too_large")
        actual_blob = hashlib.sha1(b"blob " + str(len(decoded)).encode() + b"\0" + decoded).hexdigest()
        if not hmac.compare_digest(actual_blob, blob):
            raise SourceRejected("blob_hash_mismatch")
        target = (self.destination / path).resolve()
        if not target.is_relative_to(self.destination):
            raise SourceRejected("path_escape")
        with self.lock:
            if target.exists():
                if not target.is_file() or target.read_bytes() != decoded:
                    raise SourceRejected("existing_source_conflict")
                outcome = "cached"
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                descriptor, temporary = tempfile.mkstemp(prefix=".source-", dir=target.parent)
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(decoded)
                    os.replace(temporary, target)
                finally:
                    Path(temporary).unlink(missing_ok=True)
                outcome = "written"
            record = {"path": path, "ref": REF, "blob_id": blob,
                      "sha256": hashlib.sha256(decoded).hexdigest(),
                      "bytes": len(decoded), "status": outcome}
            self.manifest.parent.mkdir(parents=True, exist_ok=True)
            with self.manifest.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record) + "\n")
            self.count += 1
            self.byte_count += len(decoded)
        return {"status": outcome, "bytes": len(decoded), "blob_verified": True}


def handler_type(store: Store, token: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def reply(self, status: int, value: dict[str, object]) -> None:
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", ORIGIN)
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Local-Ingest-Key")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def authorized(self) -> bool:
            origin = self.headers.get("Origin")
            return origin in {None, ORIGIN} and hmac.compare_digest(
                self.headers.get("X-Local-Ingest-Key", "").encode(), token.encode()
            )

        def do_OPTIONS(self) -> None:
            if self.headers.get("Origin") != ORIGIN:
                self.reply(403, {"error": "origin_rejected"})
                return
            self.reply(200, {"ready": True})

        def do_GET(self) -> None:
            if not self.authorized():
                self.reply(401, {"error": "unauthorized"})
                return
            self.reply(200, {"files": store.count, "bytes": store.byte_count})

        def do_POST(self) -> None:
            if not self.authorized():
                self.reply(401, {"error": "unauthorized"})
                return
            if self.path == "/stop":
                self.reply(200, {"stopping": True})
                Thread(target=self.server.shutdown, daemon=True).start()
                return
            if self.path != "/ingest":
                self.reply(404, {"error": "not_found"})
                return
            try:
                lengths = self.headers.get_all("Content-Length", [])
                if len(lengths) != 1 or self.headers.get("Transfer-Encoding"):
                    raise SourceRejected("invalid_framing")
                size = int(lengths[0])
                if not 0 < size <= MAX_REQUEST:
                    raise SourceRejected("invalid_size")
                self.connection.settimeout(15)
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise SourceRejected("incomplete_body")
                result = store.ingest(json.loads(raw))
                self.reply(200, result)
            except (ValueError, UnicodeError):
                self.reply(400, {"error": "source_rejected"})
            except OSError:
                self.reply(500, {"error": "storage_error"})

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    options = parser.parse_args()
    token = secrets.token_hex(32)
    rendezvous = LAB / "source-ingest-session.json"
    store = Store(LAB / "sources/project-2", LAB / "native-source-manifest.jsonl")
    with ThreadingHTTPServer(("127.0.0.1", options.port), handler_type(store, token)) as server:
        rendezvous.write_text(json.dumps({"port": server.server_port, "token": token}), encoding="utf-8")
        print(json.dumps({"port": server.server_port, "token_printed": False}), flush=True)
        try:
            server.serve_forever(poll_interval=0.2)
        finally:
            rendezvous.unlink(missing_ok=True)
    print(json.dumps({"stopped": True, "files": store.count, "bytes": store.byte_count}), flush=True)


if __name__ == "__main__":
    main()
