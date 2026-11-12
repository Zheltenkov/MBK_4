"""Real loopback HTTP bodies must spend one wall budget despite byte trickles."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import threading
import time
from types import SimpleNamespace
import urllib.error
from urllib.request import Request

import pytest

import transport
import llm_agent
from config import AppConfig


@contextmanager
def local_server(mode: str, *, status: int = 200, tls: ssl.SSLContext | None = None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def handle(self):
            try:
                super().handle()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # A successful deadline abort also ends the server's keepalive read.
                pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            requests.append(self.client_address)
            if mode == "headers":
                try:
                    for byte in b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}":
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.025)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                return
            self.send_response(status)
            if mode.startswith("chunk"):
                self.send_header("Transfer-Encoding", "chunked")
            else:
                self.send_header("Content-Length", "24" if mode != "fast" else "2")
            if mode == "close":
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.flush()
            if mode == "fast":
                self.wfile.write(b"{}")
                self.wfile.flush()
                return
            body = {
                "chunk_size": b"000000000000000001\r\nx\r\n0\r\n\r\n",
                "chunk_trailer": b"1\r\nx\r\n0\r\nX-Trailer: abcdefghijk\r\n\r\n",
                "chunk_payload": b"18\r\n" + b"x" * 24 + b"\r\n0\r\n\r\n",
            }.get(mode, b"x" * 24)
            try:
                for byte in body:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    # Every byte arrives faster than the socket inactivity timeout.
                    time.sleep(0.025)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    if tls is not None:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
    thread.start()
    try:
        scheme = "https" if tls is not None else "http"
        yield f"{scheme}://127.0.0.1:{server.server_port}/completion", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def request(pool, url, *, timeout=0.16, **kwargs):
    return pool.request(url, data=b"{}", headers={}, timeout=timeout, **kwargs)


@pytest.mark.parametrize("mode", ["headers", "length", "chunk_size", "chunk_payload", "chunk_trailer", "close"])
def test_trickled_body_deadline_interrupts_framing_and_does_not_replay(mode):
    pool = transport.ConnectionPool()
    with local_server(mode) as (url, requests):
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            with request(pool, url) as response:
                response.read()
        elapsed = time.monotonic() - started

    assert elapsed < 0.45
    assert len(requests) == 1
    assert not any(pool._idle.values())
    assert not any(t.name.startswith("mbk-http-deadline") for t in threading.enumerate())


def test_http_error_body_is_bounded_before_building_the_buffered_http_error():
    with local_server("chunk_trailer", status=429) as (url, requests):
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            request(transport.ConnectionPool(), url)
        assert time.monotonic() - started < 0.45
        assert len(requests) == 1


def test_fast_complete_responses_cancel_the_watchdog_before_pool_reuse():
    pool = transport.ConnectionPool()
    with local_server("fast") as (url, requests):
        for _ in range(3):
            with request(pool, url) as response:
                assert response.read() == b"{}"
            assert not any(t.name.startswith("mbk-http-deadline") for t in threading.enumerate())
        assert len(requests) == 3
        assert len(set(requests)) == 1


def test_fast_error_keeps_status_and_readable_diagnostic_body():
    with local_server("fast", status=429) as (url, requests):
        with pytest.raises(urllib.error.HTTPError) as error:
            request(transport.ConnectionPool(), url)
        assert error.value.code == 429
        assert error.value.read() == b"{}"
        assert len(requests) == 1


def test_streaming_keeps_its_existing_body_inactivity_semantics():
    with local_server("length") as (url, requests):
        with request(transport.ConnectionPool(), url, streaming=True) as response:
            assert response.read() == b"x" * 24
        assert len(requests) == 1


@pytest.mark.parametrize("streaming", [False, True])
def test_actual_llm_adapter_marks_the_streaming_transport_explicitly(monkeypatch, streaming):
    seen = []
    monkeypatch.setattr(transport.POOL, "request", lambda *a, **kwargs: seen.append(kwargs))
    config = AppConfig(openrouter_api_key="synthetic-unused")

    llm_agent._urlopen(config, Request("https://example.invalid/completion",
        data=json.dumps({"stream": streaming}).encode()))

    assert seen[0]["streaming"] is streaming


def test_finished_watchdog_joins_an_already_running_callback_before_release(monkeypatch):
    entered, unblock, finished = threading.Event(), threading.Event(), threading.Event()

    expire = transport._ResponseDeadline._expire

    def blocked_expire(self):
        entered.set()
        assert unblock.wait(1)
        expire(self)

    monkeypatch.setattr(transport._ResponseDeadline, "_expire", blocked_expire)

    class Raw:
        will_close = False

        def isclosed(self):
            return True

        def close(self):
            pass

    pool = transport.ConnectionPool()
    local, remote = socket.socketpair()
    deadline = transport._ResponseDeadline(time.monotonic() + 0.02, local)
    assert entered.wait(1)
    response = transport._PooledResponse(pool, ("test",), Raw(), Raw(), deadline)
    closer = threading.Thread(target=lambda: (response.close(), finished.set()))
    closer.start()
    try:
        assert not finished.wait(0.03)
        assert pool._idle == {}
    finally:
        unblock.set()
        closer.join(1)
        local.close()
        remote.close()
    assert finished.is_set()
    assert pool._idle == {}
    assert not any(t.name.startswith("mbk-http-deadline") for t in threading.enumerate())


def test_nonstream_retries_and_backoff_share_one_remaining_budget(monkeypatch):
    clock, budgets, sleeps = [100.0], [], []

    def open_request(config, request):
        budgets.append(config.request_timeout)
        clock[0] += 4 if len(budgets) == 1 else 5
        raise ConnectionResetError("synthetic interruption")

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(llm_agent, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep))
    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    config = AppConfig(openrouter_api_key="synthetic-unused", request_timeout=10)
    with pytest.raises(llm_agent.ModelRequestError):
        llm_agent._post(config, {"model": "synthetic"}, max_attempts=3)
    assert budgets == [10, 4.5]
    assert sleeps == [1.5]


def test_no_new_attempt_or_backoff_when_short_budget_cannot_fit_it(monkeypatch):
    calls, sleeps = [], []

    def open_request(config, request):
        calls.append(config.request_timeout)
        raise ConnectionResetError("synthetic interruption")

    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    monkeypatch.setattr(llm_agent.time, "sleep", lambda seconds: sleeps.append(seconds))
    config = replace(AppConfig(openrouter_api_key="synthetic-unused"), request_timeout=0.2)
    with pytest.raises(llm_agent.ModelRequestError):
        llm_agent._post(config, {"model": "synthetic"}, max_attempts=3)
    assert len(calls) == 1
    assert 0 < calls[0] <= 0.2
    assert sleeps == []


def test_absolute_deadline_keeps_a_distinct_safe_failure_from_inactivity(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(llm_agent, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def open_request(config, request):
        clock[0] = 10.0
        from io import BytesIO
        return BytesIO(b"{}")

    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    config = AppConfig(openrouter_api_key="synthetic-unused", request_timeout=10)
    with pytest.raises(llm_agent.ModelRequestError) as caught:
        llm_agent._post(config, {"model": "synthetic"}, max_attempts=1)
    assert caught.value.failure == llm_agent.ModelFailure("deadline", "read", "DeadlineExceeded")
    assert llm_agent.model_failure(TimeoutError("untrusted provider detail"), phase="read").kind == "timeout"


@pytest.fixture
def tls_contexts(tmp_path):
    executable = shutil.which("openssl")
    if executable is None:
        git_openssl = Path("C:/Program Files/Git/usr/bin/openssl.exe")
        if git_openssl.is_file():
            executable = str(git_openssl)
    if executable is None:
        pytest.skip("Local TLS test requires the existing OpenSSL executable")
    key, certificate = tmp_path / "synthetic.key", tmp_path / "synthetic.pem"
    subprocess.run([executable, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-days", "1", "-subj", "/CN=localhost", "-keyout", str(key), "-out", str(certificate)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(certificate, key)
    client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client.check_hostname = False
    client.verify_mode = ssl.CERT_NONE
    return server, client


@pytest.mark.parametrize("mode", ["chunk_trailer", "fast"])
def test_real_tls_body_deadline_or_clean_reuse(monkeypatch, tls_contexts, mode):
    server_context, client_context = tls_contexts
    pool = transport.ConnectionPool()
    monkeypatch.setattr(pool, "_new_connection", lambda target, proxy, timeout:
        transport.http.client.HTTPSConnection(target.hostname, target.port,
            timeout=timeout, context=client_context))
    with local_server(mode, tls=server_context) as (url, requests):
        started = time.monotonic()
        if mode == "fast":
            for _ in range(2):
                with request(pool, url, timeout=0.3) as response:
                    assert response.read() == b"{}"
            assert len(requests) == 2 and len(set(requests)) == 1
        else:
            with pytest.raises(TimeoutError):
                with request(pool, url) as response:
                    response.read()
            assert time.monotonic() - started < 0.45
            assert not any(pool._idle.values())
            assert len(requests) == 1
    assert not any(t.name.startswith("mbk-http-deadline") for t in threading.enumerate())
