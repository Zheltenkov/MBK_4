"""Independent self-service debt-screen HTTP contract, with local mocked services."""
from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sse_starlette.sse import AppStatus

import core
import debt_policy
import entry_routing
import main
from config import AppConfig
from customer_context import (
    ApplicationReference, ContextCapabilityCodec, CurrentApplicationFields,
    CurrentApplicationSnapshot, CurrentContextService, ServerApplicationAdapter,
)
from session_store import SessionStore


@pytest.fixture
def landing(monkeypatch):
    monkeypatch.setenv("MBK_PUBLIC_CHAT", "1")
    monkeypatch.setenv("MBK_AUTH_USERNAME", "synthetic-operator")
    monkeypatch.setenv("MBK_AUTH_PASSWORD", "synthetic-http-only-password")
    monkeypatch.setenv("MBK_AUTH_COOKIE_SECURE", "0")
    monkeypatch.setenv("MBK_CRM_LEAD_INTAKE_ENABLED", "0")
    monkeypatch.setenv("MBK_ROUTER_MOCK", "1")
    monkeypatch.setenv("MBK_DEBT_POLICY_ENABLED", "1")
    monkeypatch.setattr(AppStatus, "should_exit_event", None)
    main.reset_session_rate_limit()
    store = SessionStore(max_extract_workers=2)
    config = [AppConfig(openrouter_api_key="synthetic-unused", debt_policy_enabled=True,
                        entry_routing_enabled=False)]
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "load_config", lambda: config[0])
    monkeypatch.setattr(main, "lead_intake_coordinator", None)
    monkeypatch.setattr(main, "crm_dialog_workflow", None)
    monkeypatch.setattr(main, "current_context_service", None)
    monkeypatch.setattr(main, "current_context_lookup", None)
    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *a, **k: {"fact_updates": [], "internal_summary": ""})
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *a, **k: facts)

    def forbidden_external(*args, **kwargs):
        pytest.fail("Self-service debt UI must use its local questionnaire and hold delivery")
    monkeypatch.setattr(core.llm_agent, "stream_conversation", forbidden_external)
    monkeypatch.setattr(core.delivery, "submit", forbidden_external)
    client = TestClient(main.app)
    yield SimpleNamespace(client=client, store=store, config=config)
    client.close()
    store.executor.shutdown(wait=True)


def create_debt(landing):
    response = landing.client.post("/api/session", json={"requested_scenario": "debt"})
    assert response.status_code == 200, response.text
    return response.json()["session_id"], response


def live(landing, sid):
    return asyncio.run(landing.store.get(sid))


def turn(landing, sid, message):
    AppStatus.should_exit_event = None
    response = landing.client.get(f"/api/session/{sid}/stream", params={"message": message})
    assert response.status_code == 200 and "event: error" not in response.text, response.text
    return response


def test_requested_debt_bootstraps_authoritative_first_question_and_own_cookie(landing):
    sid, response = create_debt(landing)
    state = response.json()["state"]
    assert state["questionnaire_policy"]["mode"] == "debt"
    assert state["plan"]["fields"] == ["debts.total"]
    assert state["quick_replies"] == []
    assert state["chat_history"][0]["role"] == "assistant"
    assert "долг" in state["chat_history"][0]["content"].lower()
    stored = live(landing, sid)
    assert stored.state["script"]["asked"] == ["debts.total"]
    assert entry_routing.lead_blocked(stored.state)
    assert stored.state["crm_lead"]["status"] == "not_sent"
    assert stored.state["crm_workflow"]["site_consent_answer"] is None
    assert stored.state.get("customer_context") is None
    assert response.cookies.get("mbk_debt_session_grant")
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()


def test_first_bare_ruble_answer_is_assigned_to_debt_after_bootstrap(landing):
    sid, _ = create_debt(landing)
    turn(landing, sid, "400000")
    state = live(landing, sid).state
    assert state["facts"]["debts"]["total"] == 400000
    assert state["plan"]["fields"] == ["client.region_class"]
    assert state["plan"]["matched"] is None and state["plan"]["routes"] == []
    assert "age" not in state["facts"].get("client", {})
    assert "request" not in state["facts"] and "assets" not in state["facts"]


def test_disabled_debt_create_is_rejected_before_allocating_session_or_cookie(landing, monkeypatch):
    monkeypatch.setenv("MBK_DEBT_POLICY_ENABLED", "0")
    landing.config[0] = replace(landing.config[0], debt_policy_enabled=False)
    response = landing.client.post("/api/session", json={"requested_scenario": "debt"})
    assert response.status_code == 503
    assert len(landing.store.all_sessions()) == 0
    assert "mbk_debt_session_grant" not in response.cookies


@pytest.mark.parametrize("extra", [{"anketa": {}}, {"anketa": {"phone": "+70000000471"}},
                                   {"context_expected": True}])
def test_self_service_mode_rejects_unverified_form_or_context_combination(landing, extra):
    response = landing.client.post("/api/session", json={"requested_scenario": "debt", **extra})
    assert response.status_code == 409
    assert len(landing.store.all_sessions()) == 0


@pytest.mark.parametrize("scenario", [True, 1, "standard", "bankruptcy", {}, ["debt"]])
def test_requested_scenario_is_a_closed_api_choice(landing, scenario):
    response = landing.client.post("/api/session", json={"requested_scenario": scenario})
    assert response.status_code == 422 and len(landing.store.all_sessions()) == 0


def test_browser_url_goal_and_attribution_leave_an_ordinary_session_ordinary(landing):
    response = landing.client.post("/api/session", json={
        "anketa": {"goal": "debt_relief"},
        "utm": {"landing": "https://example.test/bankruptcy?requested_scenario=debt"},
        "source": "trusted-debt", "trusted_signal": True,
    })
    assert response.status_code == 200
    stored = live(landing, response.json()["session_id"])
    assert debt_policy.POLICY_KEY not in stored.state
    assert response.json()["state"]["questionnaire_policy"] is None


def test_normal_and_debt_grants_coexist_in_one_owned_browser(landing):
    normal = landing.client.post("/api/session", json={}).json()["session_id"]
    normal_grant = landing.client.cookies.get("mbk_session_grant")
    debt, _ = create_debt(landing)
    assert debt != normal
    assert landing.client.cookies.get("mbk_session_grant") == normal_grant
    assert landing.client.get(f"/api/session/{normal}").status_code == 200
    restored = landing.client.get(f"/api/session/{debt}")
    assert restored.status_code == 200
    assert restored.json()["session_id"] == debt
    assert restored.json()["state"]["questionnaire_policy"]["mode"] == "debt"


def test_foreign_browser_and_forged_cookie_cannot_read_debt_session(landing):
    sid, _ = create_debt(landing)
    outsider = TestClient(main.app)
    try:
        assert outsider.get(f"/api/session/{sid}").status_code == 403
        outsider.cookies.set("mbk_debt_session_grant", "forged-only-local-test")
        assert outsider.get(f"/api/session/{sid}").status_code == 403
        outsider.cookies.set("mbk_session_grant", "also-forged")
        assert outsider.get(f"/api/session/{sid}").status_code == 403
    finally:
        outsider.close()


def test_existing_debt_restore_remains_held_when_activation_flag_is_disabled(landing, monkeypatch):
    sid, _ = create_debt(landing)
    before = len(landing.store.all_sessions())
    landing.config[0] = replace(landing.config[0], debt_policy_enabled=False)
    monkeypatch.setenv("MBK_DEBT_POLICY_ENABLED", "0")
    response = landing.client.get(f"/api/session/{sid}")
    assert response.status_code == 200 and response.json()["state"]["questionnaire_policy"]["mode"] == "debt"
    assert response.json()["turn_in_progress"] is False
    turn(landing, sid, "250000 рублей")
    assert live(landing, sid).state["facts"]["debts"]["total"] == 250000
    assert entry_routing.lead_blocked(live(landing, sid).state)
    assert len(landing.store.all_sessions()) == before


def test_get_exposes_live_turn_progress_while_extraction_worker_is_busy(landing, monkeypatch):
    sid, _ = create_debt(landing)
    entered, release = threading.Event(), threading.Event()
    actual_prepare = main.prepare_turn
    def blocked_prepare(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return actual_prepare(*args, **kwargs)
    monkeypatch.setattr(main, "prepare_turn", blocked_prepare)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(turn, landing, sid, "400000 рублей")
        try:
            assert entered.wait(timeout=5)
            response = landing.client.get(f"/api/session/{sid}")
            assert response.status_code == 200 and response.json()["turn_in_progress"] is True
        finally:
            release.set()
        pending.result(timeout=10)
    assert landing.client.get(f"/api/session/{sid}").json()["turn_in_progress"] is False


def test_server_verified_known_contacts_are_reused_and_private_state_stays_private(landing, monkeypatch):
    sid, _ = create_debt(landing)
    monkeypatch.setenv("MBK_CURRENT_CONTEXT_ENABLED", "1")
    reference = ApplicationReference(application_id="private-site-request", subject_id="private-subject", revision="v1")
    source_calls = []
    def resolve(requested):
        source_calls.append(requested)
        return CurrentApplicationSnapshot(reference=requested, phone=SecretStr("70000000471"),
            observed_at=datetime.now(timezone.utc), fields=CurrentApplicationFields(
                full_name="Проверенный Контакт", debts_total=400000, region="Москва", goal="debt_relief"))
    codec = ContextCapabilityCodec(b"synthetic-debt-landing-key-32-bytes")
    monkeypatch.setattr(main,"current_context_service",CurrentContextService(codec,ServerApplicationAdapter(resolve),enabled=True))
    capability = codec.issue(reference,session_id=sid)
    attach = landing.client.post(f"/api/session/{sid}/context",json={"capability":capability})
    assert attach.status_code == 200, attach.text
    response = turn(landing,sid,"Продолжим")
    state = live(landing,sid).state
    assert state["plan"]["fields"] == [] and state["dialog_phase"] == "collected_held"
    assert "На какой номер" not in response.text
    assert state["facts"]["client"]["phone"] == "+70000000471"
    assert len(source_calls) == 1 and state["crm_lead"]["status"] == "not_sent"
    public = landing.client.get(f"/api/session/{sid}").text + response.text
    for private in ("private-site-request","private-subject","binding_digest",capability,"70000000471"):
        assert private not in public


def test_failed_sse_keeps_same_debt_session_held_with_safe_error(landing,monkeypatch):
    sid,_ = create_debt(landing)
    def failed(*args,**kwargs):
        raise TimeoutError("PRIVATE_UPSTREAM_DIAGNOSTIC")
    monkeypatch.setattr(main,"prepare_turn",failed)
    AppStatus.should_exit_event = None
    response = landing.client.get(f"/api/session/{sid}/stream",params={"message":"400000"})
    assert response.status_code == 200 and "event: error" in response.text
    assert "PRIVATE_UPSTREAM_DIAGNOSTIC" not in response.text
    restored = landing.client.get(f"/api/session/{sid}")
    assert restored.status_code == 200 and restored.json()["session_id"] == sid
    assert restored.json()["state"]["questionnaire_policy"]["mode"] == "debt"
    assert entry_routing.lead_blocked(live(landing,sid).state)
    assert len(landing.store.all_sessions()) == 1


def test_closed_public_policy_never_includes_private_observation_amount_or_consent(landing):
    sid,_ = create_debt(landing)
    turn(landing,sid,"200-300тысяч")
    metadata = landing.client.get(f"/api/session/{sid}").json()["state"]["questionnaire_policy"]
    assert set(metadata) == {"version","mode","reason"}
    assert metadata["mode"] == "debt"
    assert "amount_observation" not in json.dumps(metadata)
    assert live(landing,sid).state["crm_workflow"]["site_consent_answer"] is None


@pytest.mark.parametrize("mode", ["debt", "clarify", "cancelled", "malformed"])
def test_every_held_debt_mode_omits_legacy_cash_chips(landing, monkeypatch, mode):
    monkeypatch.setenv("MBK_QUICK_REPLIES", "1")
    monkeypatch.setenv("MBK_ENTRY_ROUTING", "1")
    sid, _ = create_debt(landing)
    live(landing,sid).state[debt_policy.POLICY_KEY] = (
        {} if mode == "malformed" else debt_policy.DebtPolicyState(mode=mode).as_state())
    assert landing.client.get(f"/api/session/{sid}").json()["state"]["quick_replies"] == []


def test_ordinary_greeting_keeps_its_existing_quick_reply_choices(landing, monkeypatch):
    monkeypatch.setenv("MBK_QUICK_REPLIES", "1")
    monkeypatch.setenv("MBK_ENTRY_ROUTING", "1")
    response = landing.client.post("/api/session", json={})
    assert response.status_code == 200
    chips = response.json()["state"]["quick_replies"]
    assert [item["value"] for item in chips] == [
        "Получить деньги", "Разобраться с долгами", "Я уже оставлял заявку", "Другой вопрос",
    ]


@pytest.mark.parametrize("branch", ["ordinary", "completed_ack", "pending_context"])
def test_every_sse_state_envelope_carries_the_exact_owned_session_id(landing, monkeypatch, branch):
    sid, _ = create_debt(landing)
    if branch == "completed_ack":
        monkeypatch.setattr(main, "conversation_is_over", lambda *args: True)
    elif branch == "pending_context":
        live(landing, sid).state["customer_context"] = {"context_status": "pending"}
    response = turn(landing, sid, "400000" if branch == "ordinary" else "Спасибо")
    payloads = []
    event = None
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: ") and event == "state":
            payloads.append(json.loads(line[6:]))
    assert payloads and all(item.get("session_id") == sid for item in payloads)
