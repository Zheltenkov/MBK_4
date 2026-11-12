"""Actual child-process restart and crash-cut acceptance for the persistent local preview."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from http.cookiejar import CookieJar
import http.client
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from storage.sqlite_sessions import SQLiteSessionRepository

ROOT = Path(__file__).resolve().parents[1]


def wait_until(predicate, *, timeout=15):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Local child-process condition timed out")
        time.sleep(0.02)


class LocalHttp:
    def __init__(self):
        self.base = ""
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, path, body=None, *, timeout=8):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data,
            headers={"content-type": "application/json"} if data else {}, method="POST" if data is not None else "GET")
        try:
            response = self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read().decode()
            value = json.loads(raw) if raw.startswith("{") else raw
            return response.status, value


FAULT_CHILD = textwrap.dedent('''
    import os
    from pathlib import Path
    import sys
    import threading
    from site_widget.stand.debt_preview import create_preview_app
    assets, state, port, phase = sys.argv[1:]
    app = create_preview_app(Path(assets), onloader_fixture=True, state_directory=Path(state))
    import main
    marker = Path(state) / 'crash-cut.json'
    def reached(stage):
        marker.write_text('{"stage":"' + stage + '"}', encoding='utf-8')
        threading.Event().wait(25)
        raise TimeoutError('Synthetic crash cut was not terminated')
    if phase == 'before_ack':
        original = main.store.persist_durable
        def before_checkpoint(session, **kwargs):
            if (session.state.get('onloader') or {}).get('status') == 'signal_pending':
                reached('before_ack')
            return original(session, **kwargs)
        main.store.persist_durable = before_checkpoint
    else:
        original = main.onloader_service._backend.submit
        def cut_receiver(handle, command):
            if phase == 'after_ack':
                reached('after_ack')
            result = original(handle, command)
            reached('after_receiver_commit')
            return result
        main.onloader_service._backend.submit = cut_receiver
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=int(port), access_log=False)
''')


class PreviewProcess:
    def __init__(self, directory):
        self.directory = directory
        self.assets = directory / "assets"
        self.state = directory / "private-state"
        self.assets.mkdir()
        (self.assets / "widget.css").write_text("body { color: black; }", encoding="utf-8")
        self.client = LocalHttp()
        self.process = None
        self.log = None
        self.generation = 0

    def start(self, phase=None):
        # Binding here registers this precise fixture port with inherited network guards.
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        self.generation += 1
        self.client.base = f"http://127.0.0.1:{port}"
        if phase:
            script = self.directory / "controlled-child.py"
            script.write_text(FAULT_CHILD, encoding="utf-8")
            command = [sys.executable, str(script), str(self.assets), str(self.state), str(port), phase]
        else:
            command = [sys.executable, "-m", "site_widget.stand.debt_preview", "--port", str(port),
                       "--assets-dir", str(self.assets), "--state-dir", str(self.state), "--onloader-fixture"]
        environment = {key: value for key, value in os.environ.items()
            if not key.startswith(("MBK_", "OPENAI_", "OPENROUTER_", "ANTHROPIC_", "DADATA_"))
            and key.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}}
        environment["PYTHONPATH"] = str(ROOT)
        self.log = (self.directory / f"process-{self.generation}.log").open("w", encoding="utf-8")
        self.process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=self.log,
            stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        def ready():
            assert self.process is not None
            assert self.process.poll() is None, "The isolated preview child exited before readiness"
            try:
                return self.client.request("/chat-api/health", timeout=0.3)[0] == 200
            except (OSError, http.client.HTTPException):
                return False
        wait_until(ready)
        return self.process.pid

    def stop(self):
        if self.process is not None:
            self.process.kill()
            self.process.wait(timeout=5)
            self.process = None
        if self.log is not None:
            self.log.close()
            self.log = None

    def record(self, sid):
        record = SQLiteSessionRepository(self.state / "sessions.sqlite").load(sid)
        assert record is not None
        return record

    def operation_count(self):
        with sqlite3.connect(self.state / "onloader.sqlite") as database:
            return database.execute("SELECT COUNT(*) FROM onloader_operations").fetchone()[0]


@pytest.fixture
def preview(tmp_path):
    process = PreviewProcess(tmp_path)
    yield process
    process.stop()


def prepare_onloader(preview):
    status, created = preview.client.request("/chat-api/session", {"onloader_expected": True})
    assert status == 200
    sid = created["session_id"]
    status, authority = preview.client.request("/__local/onloader-capability", {"session_id": sid})
    assert status == 200
    status, attached = preview.client.request(f"/chat-api/session/{sid}/onloader", authority)
    assert status == 200
    value = attached["state"]["onloader"]
    body = {"presentation_id": value["presentation_id"], "offer_id": value["offer"]["offer_id"],
            "offer_version": value["offer"]["version"], "consent_version": value["offer"]["consent_version"],
            "decision": "accept"}
    return sid, body


@pytest.mark.parametrize("scenario", ["normal", "debt"])
def test_actual_process_restart_restores_the_same_chat_history_cursor_and_owner(preview, scenario):
    first_pid = preview.start()
    status, created = preview.client.request("/chat-api/session", {"requested_scenario": "debt"} if scenario == "debt" else {})
    assert status == 200
    sid = created["session_id"]
    status, answer = preview.client.request(f"/chat-api/session/{sid}/stream?message=" + ("400000" if scenario == "debt" else "hello"))
    assert status == 200 and "event: error" not in answer
    if scenario == "normal":
        query = urllib.parse.urlencode({"message": "Меня зовут Антон"})
        status, answer = preview.client.request(f"/chat-api/session/{sid}/stream?{query}")
        assert status == 200 and "event: error" not in answer
    status, before = preview.client.request(f"/chat-api/session/{sid}")
    assert status == 200
    if scenario == "normal":
        assert before["state"]["facts"]["client"]["name"] == "Антон"
    else:
        assert before["state"]["facts"]["debts"]["total"] == 400000
        assert before["state"]["plan"]["fields"] == ["client.region_class"]
    record = preview.record(sid)
    preview.stop()
    second_pid = preview.start()
    assert second_pid != first_pid
    status, restored = preview.client.request(f"/chat-api/session/{sid}")
    assert status == 200
    assert restored["session_id"] == sid
    for field in ("chat_history", "facts", "plan", "questionnaire_policy", "closed"):
        assert restored["state"][field] == before["state"][field]
    assert preview.record(sid).revision == record.revision
    assert preview.operation_count() == 0


@pytest.mark.parametrize("decided", [False, True])
def test_actual_restart_preserves_onloader_authority_and_never_renews_it_by_get(preview, decided):
    first_pid = preview.start()
    sid, body = prepare_onloader(preview)
    if decided:
        assert preview.client.request(f"/chat-api/session/{sid}/onloader/decision", body)[0] == 200
    before = preview.record(sid)
    preview.stop()
    assert preview.start() != first_pid
    status, value = preview.client.request(f"/chat-api/session/{sid}")
    assert status == 200
    assert value["state"]["onloader"]["status"] == ("queued" if decided else "awaiting_consent")
    after = preview.record(sid)
    assert after.revision == before.revision
    for field in ("authorization_expires_at", "offer_expires_at", "intent_expires_at", "presentation_id", "event_id", "decision"):
        assert after.state["onloader"][field] == before.state["onloader"][field]
    assert preview.operation_count() == int(decided)
    # The persisted source registry must still validate the existing handle, without minting.
    endpoint = "reconcile" if decided else "decision"
    status, _ = preview.client.request(f"/chat-api/session/{sid}/onloader/{endpoint}", {} if decided else body)
    assert status == 200
    assert preview.operation_count() == 1


@pytest.mark.parametrize("phase,count,status", [("before_ack", 0, "awaiting_consent"),
    ("after_ack", 0, "signal_pending"), ("after_receiver_commit", 1, "signal_pending")])
def test_real_crash_cuts_preserve_the_last_committed_decision_without_automatic_replay(preview, phase, count, status):
    first_pid = preview.start(phase)
    sid, body = prepare_onloader(preview)
    marker = preview.state / "crash-cut.json"
    def attempt():
        try:
            return preview.client.request(f"/chat-api/session/{sid}/onloader/decision", body, timeout=12)
        except (OSError, http.client.HTTPException):
            return None
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(attempt)
        wait_until(marker.is_file, timeout=8)
        assert json.loads(marker.read_text())["stage"] == phase
        before = preview.record(sid)
        assert before.state["onloader"]["status"] == status
        assert preview.operation_count() == count
        preview.stop()
        pending.result(timeout=5)
    assert preview.start() != first_pid
    response_status, restored = preview.client.request(f"/chat-api/session/{sid}")
    assert response_status == 200
    assert restored["state"]["onloader"]["status"] == status
    assert preview.operation_count() == count
    assert preview.record(sid).revision == before.revision
    if phase == "before_ack":
        assert preview.client.request(f"/chat-api/session/{sid}/onloader/decision", body)[0] == 200
        assert preview.operation_count() == 1
    else:
        assert preview.client.request(f"/chat-api/session/{sid}/onloader/reconcile", {})[0] == 200
        assert preview.operation_count() == count
        _, outcome = preview.client.request(f"/chat-api/session/{sid}")
        assert outcome["state"]["onloader"]["status"] == ("queued" if count else "outcome_unknown")


def test_restart_keeps_expired_authority_expired_until_an_explicit_fresh_capability(preview):
    preview.start()
    sid, body = prepare_onloader(preview)
    preview.stop()
    record = preview.record(sid)
    changed = deepcopy(record.state)
    changed["onloader"]["authorization_expires_at"] = time.time() - 1
    SQLiteSessionRepository(preview.state / "sessions.sqlite").save_committed(replace(record, state=changed))
    preview.start()
    assert preview.client.request(f"/chat-api/session/{sid}/onloader/decision", body)[0] == 409
    assert preview.operation_count() == 0
    _, authority = preview.client.request("/__local/onloader-capability", {"session_id": sid})
    assert preview.client.request(f"/chat-api/session/{sid}/onloader", authority)[0] == 200
    assert preview.client.request(f"/chat-api/session/{sid}/onloader/decision", body)[0] == 200
    assert preview.operation_count() == 1


def test_restored_public_diagnostics_and_logs_never_contain_private_keys_or_contact_handles(preview):
    preview.start()
    sid, body = prepare_onloader(preview)
    assert preview.client.request(f"/chat-api/session/{sid}/onloader/decision", body)[0] == 200
    private = preview.record(sid).state["onloader"]
    preview.stop()
    preview.start()
    status, restored = preview.client.request(f"/chat-api/session/{sid}")
    assert status == 200
    diagnostic = preview.state / "diagnostics" / f"{sid}.json"
    assert diagnostic.is_file()
    text = json.dumps(restored) + diagnostic.read_text(encoding="utf-8")
    assert all(key not in text for key in ("binding_digest", "delivery_handle", "operation_ref", "authorization_expires_at"))
    values = json.loads((preview.state / "preview-keys.json").read_text())
    secrets = [private["delivery_handle"], private["binding_digest"], *values.values()]
    logs = "".join(path.read_text(encoding="utf-8") for path in preview.directory.glob("process-*.log"))
    leaked = any(value in text + logs for value in secrets)
    assert leaked is False
