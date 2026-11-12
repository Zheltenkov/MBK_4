"""Local HTTP receiver: record transport metadata without forwarding requests."""

from __future__ import annotations

from collections import deque
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Lock


EVENTS: deque[dict[str, object]] = deque(maxlen=1000)
LOCK = Lock()
MAX_BODY_BYTES = 1_048_576


class CaptureHandler(BaseHTTPRequestHandler):
    """Accept requests locally; never make an outgoing HTTP request."""

    def log_message(self, format: str, *args: object) -> None:
        # The default logger prints request paths, which may contain credentials.
        return

    def _reply(self, status: int, body: dict[str, object]) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._reply(200, {"service": "local-capture", "forwarding": False})
        elif self.path == "/events":
            with LOCK:
                snapshot = list(EVENTS)
            self._reply(200, {"events": snapshot})
        else:
            self._reply(404, {"error": "not_found"})

    def do_POST(self) -> None:
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._reply(400, {"error": "invalid_content_length"})
            return
        if not 0 <= size <= MAX_BODY_BYTES:
            self._reply(413, {"error": "body_too_large"})
            return
        body = self.rfile.read(size)
        # Keep hashes and lengths, not phone numbers, bearer tokens or raw URLs.
        event: dict[str, object] = {
            "method": "POST",
            "path_sha256": hashlib.sha256(self.path.encode()).hexdigest(),
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "body_bytes": len(body),
        }
        with LOCK:
            EVENTS.append(event)
        self._reply(200, {"local_capture": True, "forwarded": False})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), CaptureHandler).serve_forever()
