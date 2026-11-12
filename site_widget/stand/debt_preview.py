"""Run the debt page with actual in-memory chat routes and isolated service boundaries."""
from __future__ import annotations

import argparse
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from copy import deepcopy
import os
import json
from pathlib import Path
import re
import secrets
import socket
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.types import ASGIApp, Receive, Scope, Send


WIDGET_ROOT = Path(__file__).resolve().parents[1]


def _isolate_environment(auth_password: str | None = None) -> None:
    """Keep the preview process independent of the operator's deployment credentials."""
    prefixes = ("MBK_", "OPENROUTER_", "OPENAI_", "ANTHROPIC_", "DADATA_", "PG", "DATABASE_", "DB_")
    for key in list(os.environ):
        normalized = key[5:] if key.startswith("$env:") else key
        if normalized.startswith(prefixes):
            os.environ.pop(key, None)
    os.environ.update({
        "MBK_PUBLIC_CHAT": "1", "MBK_AUTH_USERNAME": "local-preview",
        "MBK_AUTH_PASSWORD": auth_password or secrets.token_urlsafe(32), "MBK_AUTH_COOKIE_SECURE": "0",
        "MBK_DEBT_POLICY_ENABLED": "1", "MBK_CRM_LEAD_INTAKE_ENABLED": "0",
        "MBK_CRM_WORKFLOW_ENABLED": "0", "MBK_ROUTER_MOCK": "1",
        "MBK_CURRENT_CONTEXT_ENABLED": "0", "MBK_SITE_CONSENT_PAUSE": "0",
        "MBK_POLICY_VOICE_ENABLED": "0",
    })
    import dotenv
    # Imported application modules cannot discover a deployment .env in this process.
    dotenv.load_dotenv = lambda *args, **kwargs: False


def _persistent_keys(directory: Path) -> dict[str, str]:
    """Create one local-only key bundle. An incomplete existing file fails closed."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "preview-keys.json"
    value = {"auth_password": secrets.token_urlsafe(32), "capability_key": secrets.token_hex(32)}
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        value = json.loads(path.read_text(encoding="utf-8"))
    else:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
    if (not isinstance(value, dict) or set(value) != {"auth_password", "capability_key"}
            or not isinstance(value["auth_password"], str) or len(value["auth_password"]) < 32
            or not isinstance(value["capability_key"], str)
            or re.fullmatch(r"[0-9a-f]{64}", value["capability_key"]) is None):
        raise ValueError("Invalid persistent preview key bundle")
    return value


def _guard_network() -> None:
    """Allow only connections to this process's own loopback listeners."""
    ports: set[int] = set()
    original_bind = socket.socket.bind
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_dns = socket.getaddrinfo

    def allowed(address: Any) -> bool:
        return isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"} and address[1] in ports

    def bind_local(current: socket.socket, address: Any) -> None:
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"}:
            raise OSError("Preview accepts loopback listeners")
        original_bind(current, address)
        ports.add(int(current.getsockname()[1]))

    def connect_local(current: socket.socket, address: Any) -> None:
        if not allowed(address):
            raise OSError("Preview connection blocked")
        original_connect(current, address)

    def connect_ex_local(current: socket.socket, address: Any) -> int:
        if not allowed(address):
            raise OSError("Preview connection blocked")
        return original_connect_ex(current, address)

    def local_dns(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        if host not in {"127.0.0.1", "::1", "localhost", None}:
            raise OSError("Preview external DNS blocked")
        return original_dns(host, port, *args, **kwargs)

    # Windows asyncio creates an internal loopback socket pair during startup.
    # Its listener is recorded by the same guard as the preview's HTTP listener.
    setattr(socket.socket, "bind", bind_local)
    setattr(socket.socket, "connect", connect_local)
    setattr(socket.socket, "connect_ex", connect_ex_local)
    socket.getaddrinfo = local_dns


def create_preview_app(assets_directory: Path, *, onloader_fixture: bool = False,
                       state_directory: Path | None = None) -> ASGIApp:
    keys = None
    if state_directory is not None:
        state_directory = state_directory.resolve()
        if state_directory.is_relative_to(WIDGET_ROOT.parent.resolve()):
            raise ValueError("Persistent preview state must live outside the repository")
        keys = _persistent_keys(state_directory)
    _isolate_environment(keys["auth_password"] if keys is not None else None)
    os.environ["MBK_ONLOADER_LOCAL_ENABLED"] = "1" if onloader_fixture else "0"
    _guard_network()
    import core
    import main
    from config import AppConfig
    from interpretation import FactCandidateExtraction
    from routing import Matrix, Plan, get_fact
    from routing.normalize import classify_region_confident
    from session_store import SessionStore
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from utils import set_by_path

    configuration = AppConfig(openrouter_api_key="local-preview-unused", debt_policy_enabled=True,
                              entry_routing_enabled=False)
    main.load_config = lambda: configuration
    if state_directory is None:
        main.store = SessionStore(max_extract_workers=2)
    else:
        from storage.sqlite_sessions import SQLiteSessionRepository
        import session_store as session_store_module
        # Keep private-mode diagnostics inside the same isolated, non-public state directory.
        session_store_module.SESSIONS_DIR = state_directory / "diagnostics"
        session_store_module.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        main.store = SessionStore(max_extract_workers=2,
                                  repository=SQLiteSessionRepository(state_directory / "sessions.sqlite"),
                                  require_durable=True)
    main.crm_dialog_workflow = None
    main.current_context_service = None
    main.current_context_lookup = None
    main.onloader_service = None
    # Only this opt-in loopback composition can mint a synthetic onloader authority.
    mint_fixture: Callable[[str], str] | None = None
    if onloader_fixture:
        from customer_context import ContextCapabilityCodec
        from customer_context.contracts import ApplicationReference
        from onloader.contracts import OnloaderOffer, OnloaderSnapshot
        from onloader.local_backend import LocalOnloaderBackend
        from onloader.service import OnloaderService
        from pydantic import SecretStr

        assets_directory.mkdir(parents=True, exist_ok=True)
        backend = LocalOnloaderBackend((state_directory or assets_directory) / "onloader.sqlite")
        codec = ContextCapabilityCodec(bytes.fromhex(keys["capability_key"]) if keys else secrets.token_bytes(32),
                                       audience="mbk-onloader")
        main.configure_onloader(OnloaderService(codec, backend))
        handles: dict[str, str] = {}

        def issue_fixture(session_id: str) -> str:
            # The SID is owner-checked below. No client chooses a form, recipient or contact.
            reference = ApplicationReference(application_id="local-form-" + session_id,
                                             subject_id="local-subject-" + session_id, revision="1")
            now = datetime.now(timezone.utc)
            existing = backend.fetch(reference)
            handle = (existing.delivery_handle.get_secret_value() if existing is not None else
                      handles.setdefault(session_id, secrets.token_urlsafe(32)))
            backend.register(OnloaderSnapshot(
                reference=reference, operation_ref="local-operation-" + session_id,
                delivery_handle=SecretStr(handle), contact_known=True,
                offer=OnloaderOffer(offer_id="local-consultation", version="1", consent_version="local-v1",
                                   partner_name="Тестовый партнёр МБК"),
                observed_at=now, expires_at=now + timedelta(minutes=5),
            ))
            return codec.issue(reference, session_id=session_id)

        mint_fixture = issue_fixture
    # The actual policy, session store, owner grants and SSE routes execute normally.
    core.llm_agent.extract_facts = lambda *args, **kwargs: {"fact_updates": [], "internal_summary": ""}
    core.llm_agent.extract_fact_candidates = lambda *args, **kwargs: FactCandidateExtraction()
    core.llm_agent.stream_opening = lambda *args, **kwargs: iter(["Здравствуйте! Чем могу помочь?"])
    core.llm_agent.stream_conversation = lambda *args, **kwargs: iter(["Расскажите о вашем обращении."])
    ordinary_reply = main.stream_reply

    def planned_reply(state: dict[str, Any], message: str, config: AppConfig | None = None,
                      usage_collector: Any = None, critic_usage_collector: Any = None) -> Iterator[str]:
        if core.debt_policy.debt_delivery_held(state) or "onloader" in state:
            yield from ordinary_reply(state, message, config, usage_collector, critic_usage_collector)
            return
        plan = state.get("_plan_object")
        yield core._plan_question(plan) if isinstance(plan, Plan) and plan.fields else "Расскажите о вашем обращении."

    def local_geography(facts: dict[str, Any], message: str, matrix: Matrix, *,
                        asked_paths: list[str] | None = None) -> dict[str, Any]:
        updated = deepcopy(facts)
        if "client.region_class" in (asked_paths or []):
            region = classify_region_confident(message, matrix)
            if region is not None and get_fact(updated, "client.region_class") is None:
                set_by_path(updated, "client.region_class", region)
                set_by_path(updated, "client.region", message.strip())
        return updated

    main.stream_reply = planned_reply
    core.geo.enrich_locations = local_geography
    core._record_transcript = lambda *args, **kwargs: None
    main.log_dialog = lambda *args, **kwargs: None
    main.log_summary = lambda *args, **kwargs: None

    @asynccontextmanager
    async def local_lifespan(application: FastAPI) -> AsyncIterator[None]:
        try:
            if state_directory is not None and main.store is not None:
                # Restoring a saved pending intent does not call the sender or refresh authority.
                await main.store.restore()
            yield
        finally:
            if main.store is not None:
                main.store.executor.shutdown(wait=True)

    # Deployment startup would initialize repositories and periodic jobs.
    main.app.router.lifespan_context = local_lifespan
    compiled_css = assets_directory.resolve() / "widget.css"
    assets = {
        "/debt/": (WIDGET_ROOT / "debt/index.html", "text/html"),
        "/debt-assets/widget.js": (WIDGET_ROOT / "_ai-chat.js", "application/javascript"),
        "/debt-assets/widget.css": (compiled_css, "text/css"),
        "/debt-assets/debt-page.css": (WIDGET_ROOT / "debt/debt-page.css", "text/css"),
        "/debt-assets/avatar.svg": (WIDGET_ROOT / "debt/avatar.svg", "image/svg+xml"),
    }
    headers = {
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; "
        "img-src 'self'; connect-src 'self'; font-src 'self'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'",
    }

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await main.app(scope, receive, send)
            return
        path = scope["path"]
        if path == "/__local/onloader-capability" and mint_fixture is not None:
            request = Request(scope, receive)
            if request.method != "POST":
                await Response(status_code=405, headers=headers)(scope, receive, send)
                return
            # Bound the only input before parsing, and require the actual signed owner cookie.
            raw = await request.body()
            if len(raw) > 512:
                await Response(status_code=413, headers=headers)(scope, receive, send)
                return
            session_id = ""
            try:
                value = json.loads(raw)
                candidate = value.get("session_id") if isinstance(value, dict) else None
                session_id = candidate if isinstance(candidate, str) else ""
                valid = bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session_id))
                owned = valid and main._session_grant_matches(request, "/api/session/" + session_id)
            except (ValueError, TypeError):
                owned = False
            if not owned:
                await Response(status_code=403, headers=headers)(scope, receive, send)
                return
            await JSONResponse({"capability": mint_fixture(session_id)}, headers=headers)(scope, receive, send)
            return
        if path == "/debt-assets/onloader-host.js" and mint_fixture is not None:
            content = """'use strict';
const host = document.getElementById('mbk-ai-chat');
host.mbkResolveOnloader = async (sessionId) => {
  const response = await fetch('/__local/onloader-capability', {
    method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session_id: sessionId})
  });
  if (!response.ok) throw new Error('local-onloader-unavailable');
  const result = await response.json();
  return result.capability;
};
"""
            await Response(content, media_type="application/javascript", headers=headers)(scope, receive, send)
            return
        if path.startswith("/chat-api/"):
            translated = "/api/" + path[len("/chat-api/"):]
            forwarded = dict(scope, path=translated, raw_path=translated.encode("utf-8"))
            await main.app(forwarded, receive, send)
            return
        if path == "/favicon.ico":
            await Response(status_code=204)(scope, receive, send)
            return
        if path == "/widget/":
            markup = (WIDGET_ROOT / "test/ai-chat-widget.blade.php").read_text(encoding="utf-8")
            markup = re.sub(r"\{\{--.*?--\}\}", "", markup, flags=re.S)
            markup = markup.replace("/app/img/chat-nika.png", "/debt-assets/avatar.svg")
            # Keep functional QA speech visibly separate from model-language examples.
            notice = ('<p role="note">Техническая проверка. Ответы здесь тестовые, '
                      'языковая модель отключена. Передача партнёрам моделируется.</p>')
            host_script = '<script src="/debt-assets/onloader-host.js" defer></script>' if onloader_fixture else ''
            content = ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
                       '<meta name="viewport" content="width=device-width,initial-scale=1">'
                       '<title>Чат МБК</title><link rel="stylesheet" href="/debt-assets/widget.css">'
                       '<link rel="stylesheet" href="/debt-assets/debt-page.css">'
                       '</head><body>' + notice + markup +
                       host_script + '<script src="/debt-assets/widget.js" defer></script></body></html>')
            await Response(content, media_type="text/html", headers=headers)(scope, receive, send)
            return
        resource = assets.get(path)
        if resource is None or not resource[0].is_file():
            await Response("Страница не найдена", status_code=404, headers=headers)(scope, receive, send)
            return
        target, media_type = resource
        await Response(target.read_bytes(), media_type=media_type, headers=headers)(scope, receive, send)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8879)
    parser.add_argument("--assets-dir", type=Path, required=True)
    parser.add_argument("--onloader-fixture", action="store_true")
    parser.add_argument("--state-dir", type=Path)
    options = parser.parse_args()
    app = create_preview_app(options.assets_dir, onloader_fixture=options.onloader_fixture,
                             state_directory=options.state_dir)
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=options.port, access_log=False)


if __name__ == "__main__":
    main()
