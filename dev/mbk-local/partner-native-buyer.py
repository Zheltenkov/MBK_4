"""Local terminal buyer mock: durable hash receipts, no forwarding or customer payload log."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from uuid import uuid4


DATA = Path(os.environ.get("MBK_BUYER_DATA", "/data"))
RUN_ID = os.environ["MBK_BUYER_RUN_ID"]
LOCK = threading.Lock()
RECEIPTS = DATA / "receipts.jsonl"
ARM = DATA / "armed-genuine-lead.json"


def snapshots() -> list[dict[str, object]]:
    """Read only locally generated receipt metadata, never submitted payloads."""
    if not RECEIPTS.exists():
        return []
    result: list[dict[str, object]] = []
    for line in RECEIPTS.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError("Malformed receipt metadata")
        result.append(item)
    return result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return  # Paths, request data and headers are intentionally not logged.

    def reply(self, code: int, value: dict[str, object]) -> None:
        body = json.dumps({"receiver_kind": "local_mock_buyer", **value}, allow_nan=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # The committed receipt remains available after response loss.

    def do_GET(self) -> None:
        try:
            with LOCK:
                receipts = snapshots()
            if self.path == "/health":
                self.reply(200, {"run_id": RUN_ID, "armed": ARM.exists(),
                                 "forwarding": False, "receipt_count": len(receipts)})
            elif self.path == "/receipts":
                self.reply(200, {"run_id": RUN_ID, "receipts": receipts})
            else:
                self.reply(404, {"error": "not_found"})
        except (OSError, ValueError):
            self.reply(500, {"error": "local_receipt_storage_error"})

    def do_POST(self) -> None:
        if self.path != "/receive":
            self.reply(404, {"error": "not_found"})
            return
        # Arming happens only after an independently verified genuine Bitrix ID.
        if not ARM.exists():
            self.reply(503, {"error": "awaiting_genuine_bitrix_id"})
            return
        try:
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or self.headers.get("Transfer-Encoding"):
                raise ValueError("Invalid framing")
            length = int(lengths[0])
            if not 0 < length <= 65536:
                raise ValueError("Invalid length")
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise ValueError("Invalid media type")
            self.connection.settimeout(5)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("Incomplete body")
            value = json.loads(raw)
            arm = json.loads(ARM.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or not isinstance(arm, dict):
                raise ValueError("Invalid envelope")
            lead_id = str(value.get("leadId", ""))
            if (not lead_id.isascii() or not lead_id.isdecimal() or int(lead_id) <= 0
                    or lead_id != str(arm.get("genuine_bitrix_id", ""))
                    or arm.get("run_id") != RUN_ID
                    or value.get("campaign_label") != "local-native-" + RUN_ID):
                self.reply(422, {"error": "genuine_id_or_campaign_mismatch"})
                return
            receipt: dict[str, object] = {
                "run_id": RUN_ID, "genuine_bitrix_id": lead_id,
                "campaign_label": value["campaign_label"], "receipt_id": "local-" + uuid4().hex,
                "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                "received_at": datetime.now(timezone.utc).isoformat(), "transport_status": 200,
                "receiver_kind": "local_mock_buyer",
            }
            # Persist and fsync before acknowledging the actual native sender.
            with LOCK:
                with RECEIPTS.open("a", encoding="utf-8") as destination:
                    destination.write(json.dumps(receipt, allow_nan=False) + "\n")
                    destination.flush()
                    os.fsync(destination.fileno())
            self.reply(200, {"status": "ok", "receipt_id": receipt["receipt_id"]})
        except (OSError, ValueError, TypeError):
            self.reply(500, {"error": "invalid_fixture_or_local_storage_error"})


if __name__ == "__main__":
    DATA.mkdir(parents=True, exist_ok=True)
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
