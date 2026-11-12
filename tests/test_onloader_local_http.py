"""Independent real-HTTP acceptance for an opaque-contact, local onloader."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sse_starlette.sse import AppStatus

import core
import main
import session_store
from config import AppConfig
from customer_context import ContextCapabilityCodec
from customer_context.contracts import ApplicationReference
from onloader.contracts import OnloaderOffer, OnloaderSnapshot
from onloader.local_backend import LocalOnloaderBackend
from onloader.service import OnloaderService
from session_store import Session, SessionStore
from storage.sqlite_sessions import SQLiteSessionRepository


class ObservedBackend(LocalOnloaderBackend):
    """The actual SQLite receiver, with faults only at its application boundary."""

    def __init__(self, path, *, clock):
        super().__init__(path, clock=clock)
        self.calls = {name: 0 for name in ("fetch", "inspect", "submit", "reconcile")}
        self.fault = None
        self.receipt_override = {}

    def fetch(self, reference):
        self.calls["fetch"] += 1
        return super().fetch(reference)

    def inspect(self, handle):
        self.calls["inspect"] += 1
        return super().inspect(handle)

    def submit(self, handle, command):
        self.calls["submit"] += 1
        if self.fault == "before_commit":
            raise TimeoutError("synthetic timeout before local commit")
        if self.fault in {"revoke_at_commit", "offer_at_commit", "expire_at_commit"}:
            snapshot = super().inspect(handle)
            assert snapshot is not None
            if self.fault == "revoke_at_commit":
                self.register(snapshot.model_copy(update={"active": False}))
            elif self.fault == "offer_at_commit":
                self.register(snapshot.model_copy(update={"offer": snapshot.offer.model_copy(update={"version": "v2"})}))
            else:
                self.register(snapshot.model_copy(update={"expires_at": datetime.fromtimestamp(self._clock(), timezone.utc)}))
        receipt = super().submit(handle, command)
        if self.receipt_override:
            return receipt.model_copy(update=self.receipt_override)
        if self.fault == "after_commit":
            raise TimeoutError("synthetic timeout after local commit")
        if self.fault == "wrong_receipt":
            return receipt.model_copy(update={"event_id": "foreign-event"})
        return receipt

    def reconcile(self, handle, command):
        self.calls["reconcile"] += 1
        return super().reconcile(handle, command)


@pytest.fixture
def onload(monkeypatch, tmp_path):
    for key, value in {
        "MBK_PUBLIC_CHAT": "1", "MBK_AUTH_USERNAME": "synthetic-onloader",
        "MBK_AUTH_PASSWORD": "synthetic-onloader-password", "MBK_AUTH_COOKIE_SECURE": "0",
        "MBK_ONLOADER_LOCAL_ENABLED": "1", "MBK_CRM_LEAD_INTAKE_ENABLED": "0",
        "MBK_ROUTER_MOCK": "1", "MBK_DEBT_POLICY_ENABLED": "1",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(AppStatus, "should_exit_event", None)
    monkeypatch.setattr(session_store, "SESSIONS_DIR", tmp_path / "sessions")
    main.reset_session_rate_limit()
    session_path = tmp_path / "chat.sqlite"
    repository = SQLiteSessionRepository(session_path)
    store = SessionStore(max_extract_workers=2, repository=repository, require_durable=True)
    clock = [1794000000.0]
    key = b"synthetic-onloader-capability-key-32-bytes-only"
    codec = ContextCapabilityCodec(key, audience="mbk-onloader", clock=lambda: clock[0])
    path = tmp_path / "local-operations.sqlite"
    backend = ObservedBackend(path, clock=lambda: clock[0])
    reference = ApplicationReference(application_id="private-application-A", subject_id="private-subject-A", revision="r1")
    snapshot = OnloaderSnapshot(reference=reference, operation_ref="private-operation-A",
        delivery_handle=SecretStr("private-contact-handle-A-synthetic-only"), contact_known=True,
        offer=OnloaderOffer(offer_id="offer-A", version="v1", consent_version="consent-v1", partner_name="Тестовый партнёр"),
        observed_at=datetime.fromtimestamp(clock[0], timezone.utc),
        expires_at=datetime.fromtimestamp(clock[0] + 300, timezone.utc))
    backend.register(snapshot)
    service = OnloaderService(codec, backend, clock=lambda: clock[0])
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "onloader_service", service, raising=False)
    monkeypatch.setattr(main, "load_config", lambda: AppConfig(openrouter_api_key="synthetic-unused", debt_policy_enabled=True))
    for name in ("lead_intake_coordinator", "crm_dialog_workflow", "current_context_service", "current_context_lookup"):
        monkeypatch.setattr(main, name, None)
    model_calls = []
    model_payloads = []
    def normal_reply(*args, **kwargs):
        model_calls.append("conversation")
        yield "Как к вам обращаться?"
    def extract(*args, **kwargs):
        model_payloads.append(deepcopy(args[0]))
        model_calls.append("extract")
        return {"fact_updates": [], "internal_summary": ""}
    def forbidden_delivery(*args, **kwargs):
        pytest.fail("An onloader session must never create a bot CRM lead")
    monkeypatch.setattr(core.llm_agent, "stream_conversation", normal_reply)
    monkeypatch.setattr(core.llm_agent, "extract_facts", extract)
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *a, **k: deepcopy(facts))
    monkeypatch.setattr(core.delivery, "submit", forbidden_delivery)
    @asynccontextmanager
    async def injected_lifespan(application):
        yield
    monkeypatch.setattr(main.app.router, "lifespan_context", injected_lifespan)
    client = TestClient(main.app)
    client.__enter__()
    yield SimpleNamespace(client=client, store=store, clock=clock, codec=codec, backend=backend,
                          snapshot=snapshot, reference=reference, model_calls=model_calls, model_payloads=model_payloads,
                          monkeypatch=monkeypatch, path=path, key=key, repository=repository, session_path=session_path)
    client.__exit__(None, None, None)
    client.close()
    store.executor.shutdown(wait=True)


def live(env, sid):
    value = asyncio.run(env.store.get(sid))
    assert value is not None
    return value


def create(env, **body):
    response = env.client.post("/api/session", json=body)
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def turn(env, sid, message):
    AppStatus.should_exit_event = None
    response = env.client.get(f"/api/session/{sid}/stream", params={"message": message})
    assert response.status_code == 200 and "event: error" not in response.text, response.text
    return response


def bind(env, sid=None):
    sid = sid or create(env)
    capability = env.codec.issue(env.reference, session_id=sid)
    response = env.client.post(f"/api/session/{sid}/onloader", json={"capability": capability})
    assert response.status_code == 200, response.text
    return sid, response


def public(env, sid):
    response = env.client.get(f"/api/session/{sid}")
    assert response.status_code == 200, response.text
    return response.json()["state"]["onloader"]


def test_model_wording_survives_http_and_durable_history_without_text_consent(onload):
    sid, _ = bind(onload)
    onload.monkeypatch.setattr(main, "load_config", lambda: AppConfig(
        openrouter_api_key="synthetic-unused", debt_policy_enabled=True, policy_voice_enabled=True))
    text = "Номер уже есть, вводить его ещё раз не нужно. Решение можно выбрать под сообщением."
    onload.monkeypatch.setattr(core.llm_agent, "generate_reply", lambda *a, **k: text)
    response = turn(onload, sid, "Да. Номер опять вводить?")
    assert text in response.text
    saved = onload.client.get(f"/api/session/{sid}").json()
    assert saved["state"]["chat_history"][-1]["content"] == text
    assert saved["state"]["onloader"]["decision"] is None
    assert onload.backend.calls["submit"] == onload.backend.operation_count() == 0
    assert "_policy_spoken" not in json.dumps(saved)


def action(env, sid, decision="accept", **overrides):
    value = public(env, sid)
    body = {"presentation_id": value["presentation_id"], "offer_id": value["offer"]["offer_id"],
            "offer_version": value["offer"]["version"], "consent_version": value["offer"]["consent_version"],
            "decision": decision}
    return {**body, **overrides}


def decide(env, sid, body=None, decision="accept"):
    return env.client.post(f"/api/session/{sid}/onloader/decision", json=body or action(env, sid, decision))


def test_existing_chat_retains_identity_history_and_facts_with_an_opaque_known_contact(onload):
    sid = create(onload)
    turn(onload, sid, "Здравствуйте, у меня вопрос")
    session = live(onload, sid)
    session.state["facts"].setdefault("client", {})["name"] = "Антон"
    history, facts = deepcopy(session.state["chat_history"]), deepcopy(session.state["facts"])
    cookie = onload.client.cookies.get("mbk_session_grant")
    calls = onload.model_calls.count("conversation")
    bind(onload, sid)
    assert live(onload, sid).state["facts"] == facts
    assert live(onload, sid).state["chat_history"][:len(history)] == history
    assert onload.client.cookies.get("mbk_session_grant") == cookie
    assert onload.client.cookies.get("mbk_debt_session_grant") is None
    assert len(onload.store.all_sessions()) == 1
    assert public(onload, sid)["contact_known"] is True
    assert live(onload, sid).state["facts"].get("client", {}).get("phone") is None
    before_offer_text = len(onload.model_calls)
    turn(onload, sid, "Да")
    assert len(onload.model_calls) == before_offer_text
    for text in ("Хочу списать долги", "Нужен кредит", "Перезвоните мне"):
        response = turn(onload, sid, text)
        assert "На какой номер" not in response.text
        assert "client.phone" not in live(onload, sid).state["plan"]["fields"]
    assert onload.model_calls.count("conversation") == calls
    payloads = json.dumps(onload.model_payloads, ensure_ascii=False)
    for private in ("private-contact-handle", "private-operation-A", "binding_digest", "delivery_handle"):
        assert private not in payloads
    assert onload.backend.calls["submit"] == onload.backend.operation_count() == 0
    assert live(onload, sid).state["crm_lead"]["status"] == "not_sent"


def test_restore_is_read_only_and_never_publishes_private_authority(onload):
    sid, _ = bind(onload)
    before = dict(onload.backend.calls)
    capability = onload.codec.issue(onload.reference, session_id=sid)
    for _ in range(3):
        response = onload.client.get(f"/api/session/{sid}")
        serialized = response.text
        for private in (capability, "private-contact-handle", "private-application-A", "private-subject-A",
                        "private-operation-A", "binding_digest", "delivery_handle", "authorization_expires_at"):
            assert private not in serialized
        assert response.json()["state"]["onloader"]["mode"] == "local"
    assert onload.backend.calls == before
    assert onload.backend.operation_count() == 0


@pytest.mark.parametrize("field,value", [("presentation_id", "wrong"), ("offer_id", "wrong"),
    ("offer_version", "v2"), ("consent_version", "consent-v2")])
def test_action_is_bound_to_the_exact_presented_offer(onload, field, value):
    sid, _ = bind(onload)
    response = decide(onload, sid, action(onload, sid, **{field: value}))
    assert response.status_code == 409
    assert onload.backend.calls["submit"] == onload.backend.operation_count() == 0


@pytest.mark.parametrize("decision,status", [("accept", "queued"), ("decline", "declined")])
def test_explicit_action_is_durable_idempotent_and_does_not_mean_external_delivery(onload, decision, status):
    sid, _ = bind(onload)
    body = action(onload, sid, decision)
    first = decide(onload, sid, body)
    assert first.status_code == 200, first.text
    assert public(onload, sid)["status"] == status
    for _ in range(3):
        assert decide(onload, sid, body).status_code == 200
    assert onload.backend.calls["submit"] == onload.backend.operation_count() == 1
    assert "delivered" not in first.text
    opposite = {**body, "decision": "decline" if decision == "accept" else "accept"}
    assert decide(onload, sid, opposite).status_code == 409
    assert onload.backend.operation_count() == 1


@pytest.mark.parametrize("fault,count", [("before_commit", 0), ("after_commit", 1), ("wrong_receipt", 1)])
def test_unknown_outcome_is_never_retried_by_get_or_repeated_action(onload, fault, count):
    sid, _ = bind(onload)
    body = action(onload, sid)
    onload.backend.fault = fault
    response = decide(onload, sid, body)
    assert response.status_code == 200, response.text
    assert public(onload, sid)["status"] == "outcome_unknown"
    assert onload.backend.operation_count() == count
    for _ in range(2):
        assert onload.client.get(f"/api/session/{sid}").status_code == 200
        assert decide(onload, sid, body).status_code == 200
    assert onload.backend.calls["submit"] == 1
    onload.backend.fault = None
    reconciled = onload.client.post(f"/api/session/{sid}/onloader/reconcile", json={})
    assert reconciled.status_code == 200, reconciled.text
    assert public(onload, sid)["status"] == ("queued" if count else "outcome_unknown")
    assert onload.backend.calls["submit"] == 1


@pytest.mark.parametrize("change", ["offer", "consent", "revision", "contact", "inactive", "expired"])
def test_fresh_backend_state_is_checked_before_the_first_decision(onload, change):
    sid, _ = bind(onload)
    body = action(onload, sid)
    snapshot = onload.snapshot
    if change in {"offer", "consent"}:
        offer = snapshot.offer.model_copy(update={"version" if change == "offer" else "consent_version": "v2"})
        snapshot = snapshot.model_copy(update={"offer": offer})
    elif change == "revision":
        snapshot = snapshot.model_copy(update={"reference": snapshot.reference.model_copy(update={"revision": "r2"})})
    elif change == "contact":
        snapshot = snapshot.model_copy(update={"contact_known": False})
    elif change == "inactive":
        snapshot = snapshot.model_copy(update={"active": False})
    else:
        onload.clock[0] += 300
    onload.backend.register(snapshot)
    assert decide(onload, sid, body).status_code in {403, 409}
    assert onload.backend.calls["submit"] == onload.backend.operation_count() == 0


@pytest.mark.parametrize("kind", ["forged", "expired", "other_sid", "other_audience"])
def test_invalid_capability_never_reads_the_backend(onload, kind):
    sid = create(onload)
    capability = onload.codec.issue(onload.reference, session_id="foreign" if kind == "other_sid" else sid)
    if kind == "forged":
        capability = ContextCapabilityCodec(b"other-synthetic-signing-key-32-bytes-only",
            audience="mbk-onloader", clock=lambda: onload.clock[0]).issue(onload.reference, session_id=sid)
    elif kind == "expired":
        onload.clock[0] += 300
    elif kind == "other_audience":
        capability = ContextCapabilityCodec(onload.key, clock=lambda: onload.clock[0]).issue(onload.reference, session_id=sid)
    response = onload.client.post(f"/api/session/{sid}/onloader", json={"capability": capability})
    assert response.status_code == 403, response.text
    assert onload.backend.calls["fetch"] == onload.backend.calls["submit"] == 0


def test_another_binding_and_invalid_repeat_cannot_replace_ready_context(onload):
    sid, _ = bind(onload)
    before = deepcopy(live(onload, sid).state["onloader"])
    fetches = onload.backend.calls["fetch"]
    other = onload.reference.model_copy(update={"application_id": "foreign-application"})
    token = onload.codec.issue(other, session_id=sid)
    assert onload.client.post(f"/api/session/{sid}/onloader", json={"capability": token}).status_code == 409
    assert onload.client.post(f"/api/session/{sid}/onloader", json={"capability": "forged"}).status_code == 403
    assert live(onload, sid).state["onloader"] == before
    assert onload.backend.calls["fetch"] == fetches


@pytest.mark.parametrize("endpoint", ["onloader", "onloader/decision", "onloader/reconcile"])
def test_owner_cookie_is_required_even_with_a_valid_capability(onload, endpoint):
    sid, _ = bind(onload)
    body = ({"capability": onload.codec.issue(onload.reference, session_id=sid)} if endpoint == "onloader" else
            action(onload, sid) if endpoint.endswith("decision") else {})
    onload.client.cookies.clear()
    response = onload.client.post(f"/api/session/{sid}/{endpoint}", json=body)
    assert response.status_code == 403, response.text
    assert onload.backend.calls["submit"] == 0


@pytest.mark.parametrize("body", [{"form_id": "untrusted"}, {"phone": "89991234567"},
    {"contact_known": True}, {"capability": "fake", "contact_known": True}])
def test_browser_contact_or_form_data_cannot_authorize_the_onloader(onload, body):
    sid = create(onload)
    response = onload.client.post(f"/api/session/{sid}/onloader", json=body)
    assert response.status_code in {400, 403, 422}
    assert onload.backend.calls["fetch"] == onload.backend.calls["submit"] == 0


def test_session_and_receiver_restart_preserve_the_recorded_operation(onload):
    sid, _ = bind(onload)
    assert decide(onload, sid).status_code == 200
    original = live(onload, sid)
    record = original.storage_record()
    onload.store._sessions[sid] = Session.from_record(replace(record, state=json.loads(json.dumps(record.state))))
    backend = ObservedBackend(onload.path, clock=lambda: onload.clock[0])
    backend.register(onload.snapshot)
    service = OnloaderService(onload.codec, backend, clock=lambda: onload.clock[0])
    onload.monkeypatch.setattr(main, "onloader_service", service)
    assert public(onload, sid)["status"] == "queued"
    assert backend.calls["fetch"] == backend.calls["submit"] == backend.calls["reconcile"] == 0
    assert onload.client.post(f"/api/session/{sid}/onloader/reconcile", json={}).status_code == 200
    assert backend.calls["submit"] == 0 and backend.operation_count() == 1


def test_two_concurrent_actions_have_one_persisted_local_operation(onload):
    sid, _ = bind(onload)
    body = action(onload, sid)
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: decide(onload, sid, body), range(2)))
    assert [response.status_code for response in responses] == [200, 200]
    assert onload.backend.calls["submit"] == onload.backend.operation_count() == 1


@pytest.mark.parametrize("key,value", [("MBK_ONLOADER_LOCAL_ENABLED", "0"),
    ("MBK_CRM_LEAD_INTAKE_ENABLED", "1"), ("MBK_ROUTER_MOCK", "0")])
def test_runtime_gate_prevents_allocation_and_source_reads(onload, key, value):
    onload.monkeypatch.setenv(key, value)
    response = onload.client.post("/api/session", json={"onloader_expected": True})
    assert response.status_code == 503, response.text
    assert len(onload.store.all_sessions()) == 0
    assert onload.backend.calls["fetch"] == onload.backend.calls["submit"] == 0


@pytest.mark.parametrize("extra", [{"anketa": {}}, {"context_expected": True}, {"requested_scenario": "debt"}])
def test_expected_onloader_cannot_be_combined_with_another_context(onload, extra):
    response = onload.client.post("/api/session", json={"onloader_expected": True, **extra})
    assert response.status_code == 409
    assert len(onload.store.all_sessions()) == 0


@pytest.mark.parametrize("malformed", [None, {}, {"status": "queued", "contact_known": True}])
def test_malformed_persisted_marker_keeps_the_phone_and_delivery_hold(onload, malformed):
    sid = create(onload)
    live(onload, sid).state["onloader"] = malformed
    calls = len(onload.model_calls)
    response = turn(onload, sid, "Перезвоните мне")
    assert "На какой номер" not in response.text
    assert len(onload.model_calls) == calls
    assert public(onload, sid)["contact_known"] is False
    assert decide(onload, sid, {"presentation_id": "bad", "offer_id": "bad", "offer_version": "v1",
                               "consent_version": "c1", "decision": "accept"}).status_code == 409
    assert onload.backend.operation_count() == 0


def test_legacy_full_partial_and_consent_paths_cannot_send_an_onloader_lead(onload):
    from datetime import timedelta
    from lead_intake_coordinator import LeadIntakeCoordinator

    sid, _ = bind(onload)
    current = live(onload, sid)
    # A known contact must not accidentally enable the ordinary abandoned-lead timer.
    current.state["facts"].setdefault("client", {})["phone"] = "+70000000532"
    calls = []
    class ForbiddenClient:
        def lead_intake(self, payload):
            calls.append(payload)
            pytest.fail("Ordinary CRM intake crossed the onloader hold")
    coordinator = LeadIntakeCoordinator(ForbiddenClient(), persist=onload.store.persist)
    assert coordinator.partial_is_due(current, now=current.last_active + timedelta(days=1)) is False
    outcome = coordinator.submit_completed(current, site_consent_shown=True, site_consent_answer=True)
    assert outcome.attempted is False
    onload.monkeypatch.setattr(main, "crm_dialog_workflow", SimpleNamespace(
        confirm_site_consent=lambda session: pytest.fail("Legacy consent bypassed the onloader hold")))
    assert onload.client.post("/api/site-consent", json={"session_id": sid}).status_code == 409
    assert calls == [] and onload.backend.operation_count() == 0


def test_disabling_the_feature_cannot_turn_an_existing_onloader_into_normal_intake(onload):
    sid, _ = bind(onload)
    onload.monkeypatch.setenv("MBK_ONLOADER_LOCAL_ENABLED", "0")
    response = turn(onload, sid, "Да")
    assert "На какой номер" not in response.text
    assert "client.phone" not in live(onload, sid).state["plan"]["fields"]
    assert onload.model_calls == []
    assert decide(onload, sid).status_code == 503
    assert onload.backend.calls["submit"] == onload.backend.operation_count() == 0


def test_another_session_for_the_same_backend_operation_does_not_create_a_second_signal(onload):
    sid, _ = bind(onload)
    assert decide(onload, sid).status_code == 200
    another = create(onload)
    bind(onload, another)
    assert decide(onload, another).status_code == 200
    assert onload.backend.operation_count() == 1
    assert public(onload, another)["status"] in {"queued", "outcome_unknown"}


@pytest.mark.parametrize("operation", ["fetch", "inspect"])
def test_source_latency_cannot_extend_the_authorization_lifetime(onload, operation):
    if operation == "inspect":
        sid, _ = bind(onload)
        body = action(onload, sid)
    else:
        sid = create(onload)
    previous = getattr(onload.backend, operation)
    def late_result(*args, **kwargs):
        result = previous(*args, **kwargs)
        onload.clock[0] += 301
        return result
    onload.monkeypatch.setattr(onload.backend, operation, late_result)
    if operation == "inspect":
        response = decide(onload, sid, body)
    else:
        capability = onload.codec.issue(onload.reference, session_id=sid)
        response = onload.client.post(f"/api/session/{sid}/onloader", json={"capability": capability})
    assert response.status_code == 409
    assert onload.backend.calls["submit"] == onload.backend.operation_count() == 0


def test_soft_message_budget_closes_chat_without_disabling_the_explicit_decision(onload):
    from state import SOFT_MESSAGE_LIMIT

    sid, _ = bind(onload)
    current = live(onload, sid)
    current.state["message_count"] = SOFT_MESSAGE_LIMIT
    model_calls = len(onload.model_calls)
    response = turn(onload, sid, "Да")
    assert len(onload.model_calls) == model_calls
    assert '"closed": true' in response.text
    assert onload.backend.calls["submit"] == 0
    assert decide(onload, sid).status_code == 200
    assert public(onload, sid)["status"] == "queued"
    assert onload.client.post(f"/api/session/{sid}/onloader/reconcile", json={}).status_code == 200
    assert onload.backend.operation_count() == 1


def test_pure_onloader_turn_counts_once_and_records_no_consent(onload):
    sid, _ = bind(onload)
    before = live(onload, sid).state.get("message_count", 0)
    turn(onload, sid, "Да")
    assert live(onload, sid).state["message_count"] == before + 1
    assert public(onload, sid)["decision"] is None
    assert onload.backend.calls["submit"] == 0


def test_fresh_capability_renews_read_access_without_changing_the_existing_intent(onload):
    sid, _ = bind(onload)
    onload.backend.fault = "after_commit"
    assert decide(onload, sid).status_code == 200
    original = deepcopy(live(onload, sid).state["onloader"])
    assert original["status"] == "outcome_unknown"
    onload.clock[0] += 301
    snapshot = onload.snapshot.model_copy(update={
        "observed_at": datetime.fromtimestamp(onload.clock[0], timezone.utc),
        "expires_at": datetime.fromtimestamp(onload.clock[0] + 300, timezone.utc),
        "offer": onload.snapshot.offer.model_copy(update={"version": "v2"}),
        "delivery_handle": SecretStr("rotated-private-contact-handle-synthetic"),
    })
    onload.backend.register(snapshot)
    bind(onload, sid)
    renewed = live(onload, sid).state["onloader"]
    for key in ("operation_ref", "event_id", "presentation_id", "offer", "intent_expires_at", "decision"):
        assert renewed[key] == original[key]
    assert renewed["authorization_expires_at"] > original["authorization_expires_at"]
    onload.backend.fault = None
    assert onload.client.post(f"/api/session/{sid}/onloader/reconcile", json={}).status_code == 200
    assert public(onload, sid)["status"] == "queued"
    assert onload.backend.calls["submit"] == onload.backend.operation_count() == 1


def test_a_receipt_read_outage_cannot_erase_a_validated_queued_receipt(onload):
    sid, _ = bind(onload)
    assert decide(onload, sid).status_code == 200
    before = deepcopy(live(onload, sid).state["onloader"])
    def unavailable(*args):
        raise TimeoutError("synthetic read outage")
    onload.monkeypatch.setattr(onload.backend, "reconcile", unavailable)
    assert onload.client.post(f"/api/session/{sid}/onloader/reconcile", json={}).status_code == 200
    assert live(onload, sid).state["onloader"]["receipt_id"] == before["receipt_id"]
    assert public(onload, sid)["status"] == "queued"
    assert onload.backend.calls["submit"] == onload.backend.operation_count() == 1


def test_backend_errors_are_normalized_before_the_http_boundary(onload):
    from onloader.contracts import OnloaderError

    sid, _ = bind(onload)
    def private_error(*args):
        raise OnloaderError("private-contact-handle-with-sensitive-upstream-body", 418)
    onload.monkeypatch.setattr(onload.backend, "inspect", private_error)
    response = decide(onload, sid)
    assert response.status_code == 503
    assert response.json() == {"detail": "onloader_unavailable"}
    assert "private-contact-handle" not in response.text
    assert onload.backend.operation_count() == 0


@pytest.mark.parametrize("fault", ["revoke_at_commit", "offer_at_commit", "expire_at_commit"])
def test_receiver_rechecks_authority_after_service_inspection_before_commit(onload, fault):
    sid, _ = bind(onload)
    onload.backend.fault = fault
    assert decide(onload, sid).status_code == 200
    assert public(onload, sid)["status"] == "outcome_unknown"
    assert onload.backend.operation_count() == 0


@pytest.mark.parametrize("field,value", [("authorization_expires_at", float("nan")),
    ("authorization_expires_at", float("inf")), ("offer_expires_at", float("nan")),
    ("intent_expires_at", float("inf"))])
def test_nonfinite_persisted_authority_never_becomes_a_known_contact(onload, field, value):
    sid, _ = bind(onload)
    record = live(onload, sid).storage_record()
    damaged = deepcopy(record.state)
    damaged["onloader"][field] = value
    # Deliberate disk corruption models a restored nonstandard old record.
    # The strict provider intentionally cannot create this JSON itself.
    with sqlite3.connect(onload.session_path) as database:
        database.execute("UPDATE sessions SET state_json=?, revision=revision+1 WHERE id=?",
                         (json.dumps(damaged, allow_nan=True), sid))
    before = len(onload.model_calls)
    response = onload.client.get(f"/api/session/{sid}")
    assert response.status_code == 503
    assert response.json() == {"detail": "session_storage_unavailable"}
    AppStatus.should_exit_event = None
    response = onload.client.get(f"/api/session/{sid}/stream", params={"message": "Да"})
    assert response.status_code == 503
    assert response.json() == {"detail": "session_storage_unavailable"}
    assert "На какой номер" not in response.text
    assert len(onload.model_calls) == before
    assert onload.backend.calls["submit"] == 0



@pytest.mark.parametrize("field,value", [("event_id", "wrong-event"), ("operation_ref", "wrong-operation"),
    ("presentation_id", "wrong-presentation"), ("offer_id", "wrong-offer"), ("offer_version", "v9"),
    ("consent_version", "consent-v9"), ("decision", "decline"), ("status", "declined"),
    ("status", "delivered"), ("offer_digest", "0" * 64), ("authorization_expires_at", 1.0)])
def test_receiver_receipt_must_match_the_entire_recorded_command(onload, field, value):
    sid, _ = bind(onload)
    onload.backend.receipt_override = {field: value}
    assert decide(onload, sid).status_code == 200
    assert public(onload, sid)["status"] == "outcome_unknown"
    assert onload.backend.calls["submit"] == onload.backend.operation_count() == 1


def test_debt_plan_and_renderer_reuse_the_opaque_contact_without_inventing_a_phone(onload):
    sid, _ = bind(onload)
    current = live(onload, sid)
    current.state["facts"].setdefault("client", {}).update({"name": "Антон", "region_class": "moscow_mo"})
    current.state["facts"].setdefault("debts", {})["total"] = 400000
    response = turn(onload, sid, "Хочу списать свои долги")
    assert current.state["plan"]["fields"] == []
    assert current.state["facts"]["client"].get("phone") is None
    assert "На какой номер" not in response.text
    current.state.pop("_plan_object", None)
    assert "На какой номер" not in core._debt_reply(current.state)
    assert current.state["dialog_phase"] == "collected_held"
    assert onload.backend.calls["submit"] == 0


def test_decline_with_a_lost_response_keeps_refusal_wording_and_never_becomes_acceptance(onload):
    sid, _ = bind(onload)
    onload.backend.fault = "after_commit"
    assert decide(onload, sid, decision="decline").status_code == 200
    assert public(onload, sid)["status"] == "outcome_unknown"
    assert public(onload, sid)["decision"] == "decline"
    last = live(onload, sid).state["chat_history"][-1]["content"]
    assert last.startswith("Отказ сохранён.")
    assert "передача поставлена" not in last.lower()
    onload.backend.fault = None
    assert onload.client.post(f"/api/session/{sid}/onloader/reconcile", json={}).status_code == 200
    assert public(onload, sid)["status"] == "declined"
    assert onload.backend.calls["submit"] == 1


@pytest.mark.parametrize("field,value", [("delivery_owner", "bot"), ("contact_known", "true"), ("active", "true")])
def test_a_forged_typed_backend_snapshot_still_requires_strict_contract_validation(onload, field, value):
    forged = onload.snapshot.model_copy(update={field: value})
    onload.monkeypatch.setattr(onload.backend, "fetch", lambda reference: forged)
    sid = create(onload)
    token = onload.codec.issue(onload.reference, session_id=sid)
    response = onload.client.post(f"/api/session/{sid}/onloader", json={"capability": token})
    assert response.status_code == 403
    assert public(onload, sid)["contact_known"] is False
    assert onload.backend.calls["submit"] == onload.backend.operation_count() == 0


def test_cancelled_decision_keeps_turn_lock_until_the_receiver_worker_completes(onload):
    import threading
    from starlette.requests import Request

    sid, _ = bind(onload)
    current = live(onload, sid)
    body = json.dumps(action(onload, sid)).encode()
    started, release = threading.Event(), threading.Event()
    original_submit = onload.backend.submit
    def blocked_submit(*args):
        started.set()
        assert release.wait(3), "Synthetic receiver was not released"
        return original_submit(*args)
    onload.monkeypatch.setattr(onload.backend, "submit", blocked_submit)
    cookie = onload.client.cookies.get("mbk_session_grant")

    async def exercise():
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}
        request = Request({"type": "http", "method": "POST", "scheme": "http",
            "path": f"/api/session/{sid}/onloader/decision", "query_string": b"",
            "headers": [(b"cookie", f"mbk_session_grant={cookie}".encode())], "server": ("testserver", 80)}, receive)
        task = asyncio.create_task(main.decide_onloader(sid, request))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert current.turn_lock.locked()
            assert current.state["onloader"]["status"] == "signal_pending"
            assert current.state["onloader"]["decision"] == "accept"
        finally:
            release.set()
        await asyncio.wait_for(current.turn_lock.acquire(), timeout=3)
        current.turn_lock.release()
        assert current.state["onloader"]["status"] == "queued"
    asyncio.run(exercise())
    assert onload.backend.calls["submit"] == onload.backend.operation_count() == 1


@pytest.mark.parametrize("body,status", [(b"x" * 8193, 413),
    (b'{"capability":"PRIVATE_INVALID_JSON_AUTHORITY', 422)])
def test_onloader_http_envelope_is_bounded_and_never_echoes_input(onload, body, status):
    sid = create(onload)
    response = onload.client.post(f"/api/session/{sid}/onloader", content=body,
                                 headers={"content-type": "application/json"})
    assert response.status_code == status
    assert "PRIVATE_INVALID_JSON_AUTHORITY" not in response.text
    assert onload.backend.calls["fetch"] == onload.backend.calls["submit"] == 0


def test_operator_authentication_alone_does_not_grant_onloader_session_ownership(onload):
    sid = create(onload)
    capability = onload.codec.issue(onload.reference, session_id=sid)
    onload.client.cookies.clear()
    login = onload.client.post("/api/auth/login", json={"username": "synthetic-onloader", "password": "synthetic-onloader-password"})
    assert login.status_code == 200
    assert onload.client.post(f"/api/session/{sid}/onloader", json={"capability": capability}).status_code == 403
    assert onload.backend.calls["fetch"] == onload.backend.calls["submit"] == 0


def test_new_expected_onloader_returns_an_idle_authoritative_pending_envelope(onload):
    response = onload.client.post("/api/session", json={"onloader_expected": True})
    assert response.status_code == 200
    assert response.json()["turn_in_progress"] is False
    sid = response.json()["session_id"]
    assert response.json()["state"]["onloader"]["status"] == "pending"
    assert onload.backend.calls["fetch"] == onload.backend.calls["submit"] == 0
    turn(onload, sid, "Здравствуйте")
    assert onload.model_calls == []
    assert "client.phone" not in live(onload, sid).state["plan"]["fields"]
    bind(onload, sid)
    assert public(onload, sid)["contact_known"] is True
    assert len(onload.store.all_sessions()) == 1
