"""Локальный стенд виджета: статика сайта + прокси на нашего бота.

Виджет ходит в /chat-api/ того же домена — на сайте это делает nginx. Здесь ту
же роль играет этот сервер: статику берёт из собранного дерева mbk-site, а
/chat-api/ пробрасывает на uvicorn, не буферизуя поток (SSE идёт как есть).
"""
import http.server
import os
import pathlib
import socketserver
import urllib.error
import urllib.request

SITE = pathlib.Path(os.environ["MBK_SITE_PUBLIC"])
STAND = pathlib.Path(__file__).parent
BOT = "http://127.0.0.1:8000"
# Системный прокси не должен вмешиваться в локальный вызов: с ним urllib
# получает закрытое соединение, а curl с --noproxy работает.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _proxy(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() in {"content-type", "accept", "cookie"}}
        request = urllib.request.Request(BOT + self.path.replace("/chat-api", "/api", 1), data=body, headers=headers, method=method)
        try:
            with OPENER.open(request, timeout=300) as upstream:
                self.send_response(upstream.status)
                for key, value in upstream.headers.items():
                    if key.lower() in {"content-type", "set-cookie", "cache-control"}:
                        self.send_header(key, value)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                while chunk := upstream.read(1):
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Type", exc.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:  # соединение с ботом не поднялось
            self.send_error(502, str(exc)[:120])

    def do_POST(self):
        self._proxy("POST")

    def do_GET(self):
        if self.path.startswith("/chat-api/"):
            return self._proxy("GET")
        path = self.path.split("?", 1)[0]
        target = STAND / "index.html" if path in {"", "/"} else SITE / path.lstrip("/")
        if not target.is_file():
            return self.send_error(404)
        data = target.read_bytes()
        kind = {".js": "application/javascript", ".css": "text/css",
                ".html": "text/html; charset=utf-8", ".svg": "image/svg+xml",
                ".png": "image/png", ".jpg": "image/jpeg", ".woff2": "font/woff2"}
        self.send_response(200)
        self.send_header("Content-Type", kind.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    with Server(("127.0.0.1", 8777), Handler) as httpd:
        print("стенд на http://127.0.0.1:8777")
        httpd.serve_forever()
