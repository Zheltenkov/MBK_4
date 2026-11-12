"""Actual HTTP/session boundary for current-context data, without external services."""
from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.requests import Request
from sse_starlette.sse import AppStatus

import main
import core
import entry_routing
import lead_intake_coordinator
import debt_policy
from config import AppConfig
from context_policy import CONTEXT_STATE_KEY
from customer_context import (
    ApplicationReference, ContextCapabilityCodec, CurrentApplicationFields,
    CurrentApplicationSnapshot, CurrentContextService,
)
from session_store import SessionStore


@pytest.fixture
def setup(monkeypatch):
    # Each TestClient uses its own event loop; SSE's process-global event cannot cross them.
    monkeypatch.setattr(AppStatus, "should_exit_event", None)
    monkeypatch.setenv("MBK_PUBLIC_CHAT", "1")
    monkeypatch.setenv("MBK_AUTH_USERNAME", "test-operator")
    monkeypatch.setenv("MBK_AUTH_PASSWORD", "isolated-http-test-password")
    monkeypatch.setenv("MBK_CURRENT_CONTEXT_ENABLED", "1")
    monkeypatch.setenv("MBK_CRM_LEAD_INTAKE_ENABLED", "0")
    monkeypatch.setenv("MBK_ROUTER_MOCK", "1")
    main.reset_session_rate_limit()
    store = SessionStore(max_extract_workers=1)
    codec = ContextCapabilityCodec(b"unit-test-context-signing-key-32bytes")
    reference = ApplicationReference(application_id="synthetic-request", subject_id="synthetic-subject", revision="v1")
    calls = []

    class Source:
        failure = False

        def fetch(self, requested):
            calls.append(requested)
            if self.failure:
                raise TimeoutError("private backend error must not escape")
            return CurrentApplicationSnapshot(
                reference=requested, phone=SecretStr("70000000001"), observed_at=datetime.now(timezone.utc),
                fields=CurrentApplicationFields(
                    full_name="Иванов Иван Иванович", debts_total=400000, region="Москва",
                    registration_address="BACKEND_ADDRESS_SENTINEL_REVEAL_SECRET",
                ),
            )

    source = Source()
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "current_context_service", CurrentContextService(codec, source, enabled=True))
    monkeypatch.setattr(main, "current_context_lookup", None)
    client = TestClient(main.app)
    session_id = client.post("/api/session", json={"context_expected": True}).json()["session_id"]
    fixture = SimpleNamespace(client=client, store=store, source=source, codec=codec,
                              reference=reference, calls=calls, session_id=session_id)
    yield fixture
    client.close()
    store.executor.shutdown(wait=True)


def token(setup, **kwargs):
    return setup.codec.issue(kwargs.get("reference", setup.reference),
                             session_id=kwargs.get("session_id", setup.session_id))


def attach(setup, capability=None):
    return setup.client.post(f"/api/session/{setup.session_id}/context",
                             json={"capability": capability if capability is not None else token(setup)})


def session(setup):
    return asyncio.run(setup.store.get(setup.session_id))


def test_actual_signed_attach_seeds_existing_facts_and_hides_private_metadata(setup):
    response = attach(setup)
    assert response.status_code == 200
    live = session(setup)
    assert live.state["facts"]["client"]["phone"] == "+70000000001"
    assert live.state["facts"]["debts"]["total"] == 400000
    assert live.state["facts"]["client"]["region_class"] == "moscow_mo"
    assert live.anketa is None
    assert entry_routing.lead_blocked(live.state)
    assert live.state["crm_lead"]["status"] == "not_sent"
    assert len(setup.calls) == 1
    public = response.text + setup.client.get(f"/api/session/{setup.session_id}").text
    for private in ("70000000001", "synthetic-request", "synthetic-subject", "binding_digest",
                    "BACKEND_ADDRESS_SENTINEL_REVEAL_SECRET", token(setup), "Иванович"):
        assert private not in public


def test_repeat_is_idempotent_and_does_not_reload_source(setup):
    assert attach(setup).status_code == 200
    before = json.dumps(session(setup).storage_record().state, sort_keys=True)
    assert attach(setup).json()["idempotent"] is True
    assert len(setup.calls) == 1
    assert json.dumps(session(setup).storage_record().state, sort_keys=True) == before


def test_signed_context_swap_is_rejected_before_source_and_preserves_binding(setup):
    assert attach(setup).status_code == 200
    original = dict(session(setup).state[CONTEXT_STATE_KEY])
    other = ApplicationReference(application_id="other-request", subject_id="other-subject", revision="v1")
    assert attach(setup, token(setup, reference=other)).status_code == 409
    assert len(setup.calls) == 1
    assert session(setup).state[CONTEXT_STATE_KEY] == original


def test_bad_token_cannot_downgrade_successful_context(setup):
    assert attach(setup).status_code == 200
    original = dict(session(setup).state[CONTEXT_STATE_KEY])
    assert attach(setup, "bad.capability").status_code == 403
    assert session(setup).state[CONTEXT_STATE_KEY] == original
    assert len(setup.calls) == 1


def test_other_session_capability_has_no_source_access(setup):
    response = attach(setup, token(setup, session_id="another-session"))
    assert response.status_code == 403
    assert not setup.calls
    assert entry_routing.lead_blocked(session(setup).state)


def test_operator_login_without_owning_cookie_cannot_attach(setup):
    outsider = TestClient(main.app)
    assert outsider.post("/api/auth/login", json={"username": "test-operator",
                                                "password": "isolated-http-test-password"}).status_code == 200
    before = dict(session(setup).state[CONTEXT_STATE_KEY])
    response = outsider.post(f"/api/session/{setup.session_id}/context", json={"capability": token(setup)})
    assert response.status_code == 403
    assert not setup.calls
    assert session(setup).state[CONTEXT_STATE_KEY] == before
    outsider.close()


@pytest.mark.parametrize("body", [
    {"phone": "70000000001"}, {"form_id": "synthetic-request"},
    {"capability": "bad", "phone": "70000000001"}, {"capability": "x" * 5000},
])
def test_browser_fields_and_invalid_body_do_not_grant_access_or_echo_secrets(setup, body):
    response = setup.client.post(f"/api/session/{setup.session_id}/context", json=body)
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_context_request"}
    assert not setup.calls


def test_envelope_is_bounded_before_parsing(setup):
    response = setup.client.post(f"/api/session/{setup.session_id}/context", content=b"x" * 9000)
    assert response.status_code == 413
    assert not setup.calls


@pytest.mark.parametrize("flag,value", [("MBK_CURRENT_CONTEXT_ENABLED", "0"),
                                       ("MBK_CRM_LEAD_INTAKE_ENABLED", "1"),
                                       ("MBK_ROUTER_MOCK", "0")])
def test_stage_one_cannot_run_with_disabled_feature_or_real_delivery(setup, monkeypatch, flag, value):
    monkeypatch.setenv(flag, value)
    assert attach(setup).status_code == 503
    assert not setup.calls


def test_missing_real_backend_adapter_fails_closed(setup, monkeypatch):
    monkeypatch.setattr(main, "current_context_service", None)
    assert attach(setup).status_code == 503
    assert not setup.calls


def test_source_timeout_is_safe_and_keeps_no_submission_hold(setup):
    setup.source.failure = True
    response = attach(setup)
    assert response.status_code == 503
    assert "private backend" not in response.text
    assert entry_routing.lead_blocked(session(setup).state)
    assert session(setup).state[CONTEXT_STATE_KEY]["context_status"] == "unavailable"


def test_optional_history_error_does_not_erase_authorized_form(setup, monkeypatch):
    class BrokenHistory:
        def lookup(self, handle):
            raise TimeoutError("must not be public or persisted")
    monkeypatch.setattr(main, "current_context_lookup", BrokenHistory())
    response = attach(setup)
    assert response.status_code == 200
    live = session(setup)
    assert live.state["facts"]["debts"]["total"] == 400000
    assert live.state[CONTEXT_STATE_KEY]["history_status"] == "unavailable"
    assert "must not be public" not in response.text


@pytest.mark.parametrize("status", ["pending", "ready", "forbidden", "unavailable", "stale"])
def test_full_and_abandoned_submission_are_both_blocked(setup, monkeypatch, status):
    live = session(setup)
    live.state[CONTEXT_STATE_KEY] = {"context_status": status}
    live.state["facts"] = {"client": {"phone": "+70000000001"}}
    live.last_active = datetime.now(timezone.utc) - timedelta(hours=1)

    class NeverSend:
        def lead_intake(self, payload):
            pytest.fail("Context session must not reach an intake transport")
    coordinator = lead_intake_coordinator.LeadIntakeCoordinator(NeverSend(), persist=lambda _: None)
    assert not coordinator.partial_is_due(live)
    result = coordinator.submit_completed(live, site_consent_shown=True, site_consent_answer=True)
    assert result.attempted is False
    assert result.reason == "context_delivery_held"
    assert live.state["crm_lead"]["status"] == "not_sent"


def test_pending_context_stream_does_not_start_llm_or_ordinary_questionnaire(setup, monkeypatch):
    def never(*args, **kwargs):
        pytest.fail("Pending context must not invoke the ordinary dialog pipeline")
    monkeypatch.setattr(main, "prepare_turn", never)
    response = setup.client.get(f"/api/session/{setup.session_id}/stream", params={"message": "Здравствуйте"})
    assert response.status_code == 200
    assert "Проверяю данные" in response.text
    assert not setup.calls


def test_storage_roundtrip_keeps_binding_and_hold_without_raw_capability(setup):
    cap = token(setup)
    assert attach(setup, cap).status_code == 200
    live = session(setup)
    record = live.storage_record()
    persisted = json.dumps(record.state, ensure_ascii=False)
    assert cap not in persisted and "synthetic-subject" not in persisted
    from session_store import Session
    restored = Session.from_record(record)
    assert entry_routing.lead_blocked(restored.state)
    assert restored.state[CONTEXT_STATE_KEY]["binding_digest"] == live.state[CONTEXT_STATE_KEY]["binding_digest"]


def test_started_dialogue_cannot_be_overwritten_by_attachment(setup):
    live = session(setup)
    live.state["chat_history"] = [{"role": "user", "content": "Нужен кредит"}]
    assert attach(setup).status_code == 409
    assert not setup.calls
    assert live.state["chat_history"][0]["content"] == "Нужен кредит"


def test_browser_form_cannot_be_upgraded_to_trusted_context(setup):
    live = session(setup)
    live.anketa = {"phone": "70000000002"}
    assert attach(setup).status_code == 409
    assert not setup.calls
    assert entry_routing.lead_blocked(live.state)


def test_cancelled_attachment_holds_turn_lock_until_backend_worker_finishes(setup, monkeypatch):
    live = session(setup)
    started, release = threading.Event(), threading.Event()
    original_fetch = setup.source.fetch

    def blocked_fetch(reference):
        started.set()
        if not release.wait(3):
            raise TimeoutError("Test backend was not released")
        return original_fetch(reference)

    monkeypatch.setattr(setup.source, "fetch", blocked_fetch)
    monkeypatch.setattr(main, "_session_grant_matches", lambda *args: True)
    body = json.dumps({"capability": token(setup)}).encode()

    async def exercise():
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request({"type": "http", "method": "POST", "scheme": "http",
                           "path": f"/api/session/{live.id}/context", "headers": [],
                           "query_string": b"", "server": ("testserver", 80)}, receive)
        task = asyncio.create_task(main.attach_current_context(live.id, request))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            # Cancellation does not stop the executor thread; a second turn must wait.
            assert live.turn_lock.locked()
            assert live.state[CONTEXT_STATE_KEY]["context_status"] == "pending"
        finally:
            release.set()
        await asyncio.wait_for(live.turn_lock.acquire(), timeout=3)
        live.turn_lock.release()
        assert live.state[CONTEXT_STATE_KEY]["context_status"] == "ready"

    asyncio.run(exercise())


@pytest.mark.parametrize("enabled", [False, True])
def test_only_live_authorized_current_goal_can_seed_debt_policy(setup, monkeypatch, enabled):
    monkeypatch.setenv("MBK_DEBT_POLICY_ENABLED", "1" if enabled else "0")
    original_fetch = setup.source.fetch

    def debt_snapshot(reference):
        snapshot = original_fetch(reference)
        return snapshot.model_copy(update={"fields": snapshot.fields.model_copy(update={"goal": "debt_relief"})})

    monkeypatch.setattr(setup.source, "fetch", debt_snapshot)
    assert attach(setup).status_code == 200
    live = session(setup)
    if enabled:
        policy = debt_policy.parse_policy(live.state)
        assert policy is not None and policy.mode == "debt" and policy.reason == "trusted_context"
        assert live.state["facts"]["debts"]["total"] == 400000
        assert "synthetic-subject" not in setup.client.get(f"/api/session/{live.id}").text
    else:
        assert debt_policy.POLICY_KEY not in live.state


def test_debt_hold_is_saved_before_a_failing_http_extraction_worker(setup, monkeypatch):
    ordinary = setup.client.post("/api/session", json={}).json()["session_id"]
    live = asyncio.run(setup.store.get(ordinary))
    assert live is not None and not entry_routing.lead_blocked(live.state)
    monkeypatch.setattr(main, "load_config", lambda: AppConfig(openrouter_api_key="unused", debt_policy_enabled=True))
    observed = []

    def failing_prepare(state, *args, **kwargs):
        observed.append(state.get(debt_policy.POLICY_KEY))
        assert debt_policy.debt_delivery_held(state)
        raise TimeoutError("private extractor diagnostic")

    monkeypatch.setattr(main, "prepare_turn", failing_prepare)
    response = setup.client.get(f"/api/session/{ordinary}/stream", params={"message": "Хочу списать свои долги."})
    assert response.status_code == 200 and "event: error" in response.text
    assert len(observed) == 1 and observed[0]["mode"] == "debt"
    assert debt_policy.debt_delivery_held(live.storage_record().state)
    assert "private extractor diagnostic" not in response.text


def test_debt_site_consent_callback_cannot_reach_workflow(setup, monkeypatch):
    live = session(setup)
    # Remove the context hold so this proves the independent persisted debt hold.
    live.state.pop(CONTEXT_STATE_KEY)
    live.state[debt_policy.POLICY_KEY] = debt_policy.DebtPolicyState().as_state()
    monkeypatch.setattr(main, "_consent_caller_allowed", lambda request: True)

    class NeverConfirm:
        def confirm_site_consent(self, current):
            pytest.fail("Debt policy must block the callback before CRM workflow")

    monkeypatch.setattr(main, "crm_dialog_workflow", NeverConfirm())
    response = setup.client.post("/api/site-consent", json={"session_id": live.id})
    assert response.status_code == 409


def test_complete_local_debt_conversation_through_actual_http_sse(setup, monkeypatch):
    ordinary = setup.client.post("/api/session", json={}).json()["session_id"]
    monkeypatch.setattr(main, "load_config", lambda: AppConfig(
        openrouter_api_key="unused", debt_policy_enabled=True, entry_routing_enabled=False))
    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *a, **k: {
        "fact_updates": [], "internal_summary": ""})
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *a, **k: facts)
    monkeypatch.setattr(main, "crm_dialog_workflow", None)

    def no_external_call(*args, **kwargs):
        pytest.fail("The local debt conversation must not call a model or delivery transport")

    monkeypatch.setattr(core.llm_agent, "stream_conversation", no_external_call)
    monkeypatch.setattr(core.delivery, "submit", no_external_call)
    turns = [
        ("Хочу списать свои долги. Общая сумма долга 400000 рублей.", "client.region_class"),
        ("Москва", "client.name"),
        ("Василий", "client.phone"),
        ("+70000000127", None),
    ]
    for text, next_field in turns:
        # Without a lifespan context TestClient opens a fresh loop per request.
        monkeypatch.setattr(AppStatus, "should_exit_event", None)
        response = setup.client.get(f"/api/session/{ordinary}/stream", params={"message": text})
        assert response.status_code == 200 and "event: error" not in response.text
        live = asyncio.run(setup.store.get(ordinary))
        assert live is not None
        assert live.state["plan"]["fields"] == ([next_field] if next_field else [])
        assert entry_routing.lead_blocked(live.state)
    assert live.state["facts"]["debts"]["total"] == 400000
    assert live.state["facts"]["client"]["phone"] == "+70000000127"
    assert live.state["dialog_phase"] == "collected_held"
    # The API also records its existing static greeting before the four tested turns.
    assert len([item for item in live.state["chat_history"] if item["role"] == "assistant"]) == 5
    assert live.state["crm_lead"]["status"] == "not_sent" and not setup.calls
