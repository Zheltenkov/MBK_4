"""Independent debt-flow acceptance through the real orchestration boundaries."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import socket

import pytest
from pydantic import SecretStr

import core
import debt_policy
import llm_agent
from config import AppConfig
from customer_context import (
    ApplicationReference, ContextAccessError, ContextCapabilityCodec, CurrentApplicationFields,
    CurrentApplicationSnapshot, CurrentContextService, ServerApplicationAdapter,
)
from delivery.crm_contracts import LeadIntakeResponse
from interpretation import FactCandidate, FactCandidateExtraction, FactOperation, FactSource
from lead_intake_coordinator import LeadIntakeCoordinator
from routing import get_fact
from session_store import Session
from state import init_state
from usage_tracker import SessionUsage


NOW = datetime(2026, 11, 6, tzinfo=timezone.utc)
POLICY_KEY = "questionnaire_policy"
_ORIGINAL_EXTRACT_FACTS = llm_agent.extract_facts


def config(mode="legacy", *, enabled=True):
    return AppConfig(openrouter_api_key="synthetic-unused", fact_interpretation_mode=mode,
                     debt_policy_enabled=enabled, entry_routing_enabled=False)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Independent debt acceptance never connects to a live service")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *a, **k: {"fact_updates": [], "internal_summary": ""})
    monkeypatch.setattr(core.llm_agent, "extract_fact_candidates", lambda *a, **k: FactCandidateExtraction())
    # Geography is a separate validated component, not the policy under evaluation.
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *a, **k: deepcopy(facts))
    monkeypatch.setattr(core.delivery, "submit", forbidden)


def fresh(**facts):
    state = init_state()
    state["facts"] = deepcopy(facts)
    state["session_id"] = "independent-debt-case"
    return state


def enter(state=None, *, mode="legacy"):
    prepared = core.prepare_turn(state or fresh(), "Хочу списать свои долги.", config(mode), submit_delivery=False)
    assert prepared[POLICY_KEY]["mode"] == "debt", "The test must exercise an active debt branch, not a held clarification"
    return prepared


@pytest.mark.parametrize("mode", ["legacy", "semantic"])
def test_explicit_debt_entry_asks_about_debt_before_unknown_contacts(mode):
    prepared = enter(mode=mode)
    assert prepared[POLICY_KEY]["mode"] == "debt"
    assert prepared["_plan_object"].field_paths == ["debts.total"]
    assert prepared["_plan_object"].matched is None
    assert not {"request.amount", "client.age", "assets.has_vehicle", "client.name", "client.phone"}.intersection(
        prepared["_plan_object"].field_paths)
    assert get_fact(prepared["facts"], "debts.credit_history") is None


@pytest.mark.parametrize("message", ["Хочу списать свои долги.", "Мне нужна реструктуризация моих долгов.",
    "Помогите списать мои долги."])
def test_first_person_debt_requests_enter_without_a_product_promise(message):
    policy = debt_policy.select_policy({}, message, enabled=True)
    assert policy is not None and policy.mode == "debt"
    assert policy.ever_debt is True and policy.version == 1


@pytest.mark.parametrize("message", ["Банкротство мне не нужно, нужен кредит.",
    "Хочу кредит, чтобы погасить долги.", "Нужен займ на погашение долгов."])
def test_credit_or_refinance_with_debt_words_does_not_silently_become_writeoff(message):
    assert debt_policy.select_policy({}, message, enabled=True) is None


@pytest.mark.parametrize("message", ["Что такое банкротство?", "У моего друга долги, он хочет банкротство.",
    "Он сказал: «Хочу списать долги». Что это значит?", "Хочу кредит и хочу списать долги.",
    "Если я хочу банкротство, что это значит?", "Он написал: хочу списать долги",
    "Не уверен, хочу ли я банкротство", "Брат хочет списать долги",
    "'Хочу списать долги' — так написано в рекламе."])
def test_ambiguous_question_third_party_or_mixed_goals_require_confirmation(message):
    selected = debt_policy.select_policy({}, message, enabled=True)
    assert selected is not None and selected.mode == "clarify"
    state = {POLICY_KEY: selected.as_state()}
    confirmed = debt_policy.select_policy(state, "Да", enabled=False)
    assert confirmed is not None and confirmed.mode == "debt"
    declined = debt_policy.select_policy(state, "Нет", enabled=False)
    assert declined is not None and declined.mode == "cancelled"
    assert debt_policy.debt_delivery_held({POLICY_KEY: declined.as_state()})


def test_explicit_debt_refusal_cancels_but_does_not_release_delivery_or_switch_to_credit():
    selected = debt_policy.select_policy({}, "Хочу списать мои долги", enabled=True)
    assert selected is not None and selected.mode == "debt"
    cancelled = debt_policy.select_policy({POLICY_KEY: selected.as_state()}, "Банкротство мне не нужно", enabled=False)
    assert cancelled is not None and cancelled.mode == "cancelled"
    assert debt_policy.debt_delivery_held({POLICY_KEY: cancelled.as_state()})


@pytest.mark.parametrize("message", ["У меня есть долг, зарплата 400 тысяч", "Долги есть, хочу кредит 300 тысяч"])
@pytest.mark.parametrize("expected", [True, False])
def test_a_salary_or_new_loan_amount_is_not_the_total_debt(message, expected):
    observed = debt_policy.detect_debt_observation(message, expected=expected)
    assert observed is None or observed.kind != "exact"


def test_new_entry_is_default_off_and_explicit_request_does_not_bypass_it():
    assert AppConfig(openrouter_api_key="unused").debt_policy_enabled is False
    prepared = core.prepare_turn(fresh(), "Хочу списать свои долги", config(enabled=False), submit_delivery=False)
    assert POLICY_KEY not in prepared


def authoritative_handle(session_id="trusted-debt-A"):
    now = 1800000000.0
    reference = ApplicationReference(application_id="server-debt-A", subject_id="server-person-A", revision="v1")
    snapshot = CurrentApplicationSnapshot(reference=reference, phone=SecretStr("70000000401"),
        fields=CurrentApplicationFields(goal="debt_relief"), observed_at=datetime.fromtimestamp(now, timezone.utc))
    codec = ContextCapabilityCodec(b"independent-debt-source-key-32bytes", clock=lambda: now)
    service = CurrentContextService(codec, ServerApplicationAdapter(lambda _: snapshot), enabled=True, clock=lambda: now)
    handle = service.resolve(codec.issue(reference, session_id=session_id), session_id=session_id)
    return handle, now


def test_verified_goal_hint_is_bound_to_the_same_live_authorized_context():
    handle, now = authoritative_handle()
    signal = debt_policy.trusted_signal_from_authorized(handle, now)
    assert signal is not None
    state = {"customer_context": handle.safe_context_state()}
    selected = debt_policy.select_policy(state, "", enabled=True, trusted_signal=signal)
    assert selected is not None and selected.mode == "debt" and selected.reason == "trusted_context"
    other, _ = authoritative_handle("trusted-debt-B")
    for unbound in ({}, {"customer_context": {"context_status": "pending"}},
                    {"customer_context": other.safe_context_state()}):
        assert debt_policy.select_policy(unbound, "", enabled=True, trusted_signal=signal) is None
    with pytest.raises(ContextAccessError):
        debt_policy.trusted_signal_from_authorized(handle, now + 300)
    forged = debt_policy.TrustedDebtSignal(object(), handle.binding_digest)
    assert debt_policy.select_policy(state, "", enabled=True, trusted_signal=forged) is None


@pytest.mark.parametrize("message", ["Банкротство мне не нужно", "Хочу кредит"])
def test_verified_old_goal_hint_does_not_override_current_refusal_or_new_credit_request(message):
    handle, now = authoritative_handle()
    signal = debt_policy.trusted_signal_from_authorized(handle, now)
    selected = debt_policy.select_policy({"customer_context": handle.safe_context_state()}, message,
                                       enabled=True, trusted_signal=signal)
    assert selected is None or selected.mode != "debt"


@pytest.mark.parametrize("amount", [189999, 190000, 299999, 300000])
def test_an_answer_to_the_actual_rubles_question_is_grounded_without_a_multiplier_guess(amount):
    observation = debt_policy.detect_debt_observation(str(amount), expected=True)
    assert observation is not None and observation.kind == "exact" and observation.amount == amount
    assert debt_policy.detect_debt_observation(str(amount), expected=False) is None


def test_unknown_and_small_unit_ambiguous_answers_do_not_become_zero_or_thousands():
    for text in ("Не знаю", "300"):
        observed = debt_policy.detect_debt_observation(text, expected=True)
        assert observed is not None and observed.kind == "unknown" and observed.amount is None


@pytest.mark.parametrize("corrupt", [None, {}, {"version": 2, "mode": "debt"}, {"mode": "standard"}])
def test_malformed_saved_policy_restores_as_held_clarification_not_credit(corrupt):
    state = fresh(client={"name": "Тестовый"})
    state[POLICY_KEY] = corrupt
    parsed = debt_policy.parse_policy(state)
    assert parsed is not None and parsed.mode == "clarify"
    core.replan(state, config(enabled=False))
    assert state["_plan_object"].matched is None
    assert state["_plan_object"].field_paths == []
    assert debt_policy.debt_delivery_held(state)


@pytest.mark.parametrize(("amount", "band"), [(0, "below_190k"), (189999, "below_190k"),
    (190000, "190k_to_299999"), (299999, "190k_to_299999"), (300000, "300k_plus")])
def test_amount_boundaries_are_local_metadata_not_crm_acceptance(amount, band):
    assert debt_policy.debt_band(amount) == band
    state = enter(fresh(client={"name": "Тестовый", "phone": "+70000000401", "region_class": "moscow_mo"},
                        debts={"total": amount}))
    assert state["_plan_object"].matched is None
    assert state["_plan_object"].field_paths == []
    assert not state.get("delivery")


@pytest.mark.parametrize("value", [None, True, False, "190000", {"min": 190000, "max": 299999}, float("inf"), float("nan"), -1])
def test_invalid_or_absent_amount_is_never_a_known_boundary(value):
    assert debt_policy.debt_band(value) == "unknown"


@pytest.mark.parametrize(("text", "low", "high"), [
    ("Долг от 190000 до 299999 рублей", 190000, 299999),
    ("Долг от 190 до 300 тысяч", 190000, 300000),
    ("Долг от 300000 до 400000 рублей", 300000, 400000)])
def test_interval_is_preserved_as_evidence_and_always_requests_exact_amount(text, low, high):
    observation = debt_policy.detect_debt_observation(text, expected=True)
    assert observation is not None and observation.kind == "range"
    assert (observation.lower, observation.upper, observation.amount) == (low, high, None)
    previous = fresh(client={"name": "Тестовый", "region_class": "moscow_mo"}, debts={"total": 250000})
    selected = debt_policy.select_policy(previous, "Хочу списать мои долги", enabled=True)
    assert selected is not None
    previous[POLICY_KEY] = selected.with_observation(observation).as_state()
    core.replan(previous, config(enabled=False))
    assert get_fact(previous["facts"], "debts.total") == 250000, "An interval cannot replace a known scalar by its lower bound"
    assert previous["_plan_object"].field_paths == ["debts.total"]
    assert previous["_plan_object"].fields[0].needs_confirmation


@pytest.mark.parametrize("previous_total", [None, 250000])
def test_actual_range_answer_never_persists_its_lower_bound_as_the_debt(previous_total):
    facts = {"total": previous_total} if previous_total is not None else {}
    state = enter(fresh(debts=facts))
    updated = core.prepare_turn(state, "Мой долг от 190 до 300 тысяч", config(), submit_delivery=False)
    assert get_fact(updated["facts"], "debts.total") == previous_total
    observation = updated[POLICY_KEY]["amount_observation"]
    assert observation["kind"] == "range" and observation["lower"] == 190000 and observation["upper"] == 300000
    assert updated["_plan_object"].field_paths == ["debts.total"]
    assert updated["_plan_object"].fields[0].needs_confirmation


@pytest.mark.parametrize("message", ["Мой долг 400 тысяч долларов", "Мой долг €400000"])
def test_foreign_currency_cannot_be_ruble_debt_or_silently_confirm_an_old_total(message):
    state = enter(fresh(client={"name": "Тестовый", "phone": "+70000000401", "region_class": "moscow_mo"},
                        debts={"total": 250000}))
    updated = core.prepare_turn(state, message, config(), submit_delivery=False)
    assert get_fact(updated["facts"], "debts.total") == 250000
    assert updated[POLICY_KEY]["amount_observation"]["kind"] == "unknown"
    assert updated["_plan_object"].field_paths == ["debts.total"]
    assert updated["_plan_object"].fields[0].needs_confirmation


@pytest.mark.parametrize("untrusted", [
    {"facts": {"client": {"goal": "debt_relief"}}},
    {"attribution": {"landing": "https://www.mbk.ru/bankruptcy?verified=true"}},
    {"entry_context": {"product_hint": "debt", "source": "verified", "url": "https://www.mbk.ru/restructuring"}},
    {"attribution": {"landing": "https://www.mbk.ru.evil.invalid/bankruptcy"}},
    {"attribution": {"landing": "https://evil.invalid/?next=https://www.mbk.ru/bankruptcy"}},
])
def test_browser_metadata_or_old_goal_cannot_activate_debt(untrusted):
    state = fresh(client={"name": "Тестовый"})
    state.update(deepcopy(untrusted))
    state["facts"].setdefault("client", {}).setdefault("name", "Тестовый")
    prepared = core.prepare_turn(state, "", config(), submit_delivery=False)
    assert POLICY_KEY not in prepared
    assert "request.amount" in prepared["_plan_object"].field_paths


@pytest.mark.parametrize("mode", ["legacy", "semantic"])
@pytest.mark.parametrize("known", [None, 400000])
def test_no_current_credits_does_not_erase_or_invent_debt_or_history(mode, known):
    debts = {"has_active_credits": False}
    if known is not None:
        debts["total"] = known
    prepared = enter(fresh(debts=debts), mode=mode)
    assert get_fact(prepared["facts"], "debts.total") == known
    assert get_fact(prepared["facts"], "debts.credit_history") is None
    if known is None:
        assert prepared["_plan_object"].field_paths == ["debts.total"]


@pytest.mark.parametrize("mode", ["legacy", "semantic"])
def test_extractor_cannot_translate_no_active_credits_into_zero_total(monkeypatch, mode):
    state = enter(fresh(debts={"total": 400000}))
    text = "Действующих кредитов нет."
    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *a, **k: {"fact_updates": [
        {"path": "debts.total", "value": 0, "confidence": "high"},
        {"path": "debts.has_active_credits", "value": False, "confidence": "high"}], "internal_summary": ""})
    monkeypatch.setattr(core.llm_agent, "extract_fact_candidates", lambda *a, **k: FactCandidateExtraction(candidates=[
        FactCandidate(path="debts.total", value=0, operation=FactOperation.CORRECT, evidence=text),
        FactCandidate(path="debts.has_active_credits", value=False, evidence=text)]))
    prepared = core.prepare_turn(state, text, config(mode), submit_delivery=False)
    assert get_fact(prepared["facts"], "debts.total") == 400000
    assert get_fact(prepared["facts"], "debts.credit_history") is None


@pytest.mark.parametrize("mode", ["legacy", "semantic"])
def test_model_defaults_cannot_change_product_assets_or_income(monkeypatch, mode):
    state = enter(fresh(debts={"total": 400000}, request={"amount": 120000}))
    text = "Продолжим обсуждение моих долгов"
    invented = {"client.goal": "cash", "client.age": 99, "request.amount": 900000,
                "assets.has_vehicle": False, "employment.income": 0,
                "debts.credit_history": "ready_bankruptcy"}
    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *a, **k: {"fact_updates": [
        {"path": path, "value": value, "confidence": "high"} for path, value in invented.items()], "internal_summary": ""})
    monkeypatch.setattr(core.llm_agent, "extract_fact_candidates", lambda *a, **k: FactCandidateExtraction(candidates=[
        FactCandidate(path=path, value=value, operation=FactOperation.CORRECT, evidence=text)
        for path, value in invented.items()]))
    updated = core.prepare_turn(state, text, config(mode), submit_delivery=False)
    assert updated[POLICY_KEY]["mode"] == "debt"
    assert get_fact(updated["facts"], "request.amount") == 120000
    for path in ("client.goal", "client.age", "assets.has_vehicle", "employment.income", "debts.credit_history"):
        assert get_fact(updated["facts"], path) is None


def test_debt_extraction_messages_remain_private_when_global_pii_is_disabled(monkeypatch):
    name, address, phone = "PRIVATEDEBTNAMEQZ", "PRIVATEDEBTADDRESSQZ", "70000000401"
    state = enter(fresh(client={"name": name, "phone": phone, "registration_address": address,
                               "residence_address": address}, debts={"total": 400000}))
    payload = core.build_runtime_payload(state, f"Продолжим, телефон {phone}")
    captured = []

    def extraction_spy(config, model, messages, usage_collector, attempt):
        captured.append(messages)
        return {"fact_updates": [], "internal_summary": ""}

    monkeypatch.setattr(llm_agent, "_extract_attempt", extraction_spy)
    monkeypatch.setattr(llm_agent, "extract_facts", _ORIGINAL_EXTRACT_FACTS)
    llm_agent.extract_facts(payload, "Продолжим", replace(config(), enable_pii_masking=False))
    serialized = json.dumps(captured, ensure_ascii=False)
    assert len(captured) == 1
    assert "400000" in serialized
    assert all(private not in serialized for private in (name, address, phone))


def test_diagnostic_snapshot_closes_corrupted_policy_metadata_without_mutating_backend_facts():
    sentinel = "PRIVATEDEBTPOLICYRAWNOTEQZ"
    state = fresh(client={"name": "PRIVATEDEBTNAMEQZ", "phone": "70000000401",
                          "registration_address": "PRIVATEDEBTADDRESSQZ"}, debts={"total": 400000})
    state[POLICY_KEY] = {"version": 1, "mode": "debt", "reason": "explicit_request", "ever_debt": True,
                         "unsafe_note": sentinel}
    before = deepcopy(state)
    session = Session(id="independent-debt-private-snapshot", state=state, anketa=None,
                      usage=SessionUsage("independent-debt-private-snapshot"))
    snapshot = session.snapshot_dict()
    text = json.dumps(snapshot, ensure_ascii=False)
    assert sentinel not in text
    assert "PRIVATEDEBTNAMEQZ" not in text and "PRIVATEDEBTADDRESSQZ" not in text and "70000000401" not in text
    assert snapshot["state"][POLICY_KEY]["mode"] == "clarify"
    assert get_fact(snapshot["state"]["facts"], "debts.total") == 400000
    assert state == before


@pytest.mark.parametrize("mode", ["legacy", "semantic"])
@pytest.mark.parametrize(("path", "value", "text"), [("client.phone", "+70000000402", "Номер не дам"),
    ("client.region_class", "rf_other", "Не скажу")])
def test_refused_phone_or_region_does_not_receive_model_known_status_or_provenance(monkeypatch, mode, path, value, text):
    client = {"name": "Тестовый"}
    if path == "client.phone":
        client["region_class"] = "moscow_mo"
    else:
        client["phone"] = "+70000000401"
    state = enter(fresh(client=client, debts={"total": 250000}))
    reply = "".join(core.stream_reply(state, "Хочу списать свои долги", config(mode)))
    state = core.commit_turn(state, "Хочу списать свои долги", reply)["current_state"]
    assert state["script"]["asked"] == [path]
    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *a, **k: {"fact_updates": [
        {"path": path, "value": value, "confidence": "high"}], "internal_summary": ""})
    source = FactSource.SYSTEM_DERIVED if path == "client.region_class" else FactSource.USER_EXPLICIT
    monkeypatch.setattr(core.llm_agent, "extract_fact_candidates", lambda *a, **k: FactCandidateExtraction(candidates=[
        FactCandidate(path=path, value=value, operation=FactOperation.CORRECT, source=source, evidence=text)]))
    updated = core.prepare_turn(state, text, config(mode), submit_delivery=False)
    assert get_fact(updated["facts"], path) is None
    assert updated["fact_statuses"].get(path) not in {"known", "confirmed"}
    assert all(event.get("path") != path for event in updated["fact_events"])


def test_feature_off_keeps_legacy_derivation_and_questionnaire():
    state = fresh(client={"name": "Тестовый", "goal": "debt_relief"}, debts={"has_active_credits": False})
    prepared = core.prepare_turn(state, "", config(enabled=False), submit_delivery=False)
    assert POLICY_KEY not in prepared
    assert get_fact(prepared["facts"], "debts.total") == 0
    assert get_fact(prepared["facts"], "debts.credit_history") == "no_overdue"
    assert set(prepared["_plan_object"].field_paths) == {"request.amount", "client.age"}


@pytest.mark.parametrize("mode", ["legacy", "semantic"])
def test_actual_dialogue_uses_only_missing_debt_facts_and_never_calls_conversation_model(monkeypatch, mode):
    calls = []

    def model_spy(*args, **kwargs):
        calls.append(True)
        yield "MODEL_SHOULD_NOT_DECIDE_THE_DEBT_QUESTION"

    monkeypatch.setattr(core.llm_agent, "stream_conversation", model_spy)
    state = enter(fresh(client={"name": "Тестовый", "phone": "+70000000401"}), mode=mode)
    assert state["_plan_object"].field_paths == ["debts.total"]
    reply = "".join(core.stream_reply(state, "Хочу списать свои долги.", config(mode)))
    assert reply and "долг" in reply.lower()
    state = core.commit_turn(state, "Хочу списать свои долги.", reply)["current_state"]
    assert state["script"]["asked"] == ["debts.total"]
    state = core.prepare_turn(state, "250000 рублей", config(mode), submit_delivery=False)
    assert get_fact(state["facts"], "debts.total") == 250000
    assert state["_plan_object"].field_paths == ["client.region_class"]
    reply = "".join(core.stream_reply(state, "250000 рублей", config(mode)))
    state = core.commit_turn(state, "250000 рублей", reply)["current_state"]
    state = core.prepare_turn(state, "Москва", config(mode), submit_delivery=False)
    assert state["_plan_object"].field_paths == []
    assert state["_plan_object"].matched is None
    assert calls == [], "A generic conversation model must not replace the selected debt question"
    assert get_fact(state["facts"], "assets.has_vehicle") is None
    assert get_fact(state["facts"], "request.amount") is None
    assert get_fact(state["facts"], "debts.credit_history") is None


def test_saved_debt_policy_survives_restore_replan_and_disabled_flag():
    state = enter(fresh(debts={"has_active_credits": False}))
    original_policy = deepcopy(state[POLICY_KEY])
    session = Session(id="independent-debt-restore", state=state, anketa=None,
                      usage=SessionUsage("independent-debt-restore"))
    record = session.storage_record()
    # Serialize exactly what the store persists, not a deepcopy of the runtime Plan.
    record = replace(record, state=json.loads(json.dumps(record.state)))
    restored = Session.from_record(record)
    assert "_plan_object" not in restored.state
    core.replan(restored.state, config(enabled=False))
    assert restored.state[POLICY_KEY] == original_policy
    assert restored.state["_plan_object"].field_paths == ["debts.total"]
    assert get_fact(restored.state["facts"], "debts.total") is None


@pytest.mark.parametrize("topic", ["amount", "region"])
def test_three_unanswered_questions_stop_as_incomplete_held_without_inventing_facts(topic):
    facts = {"client": {"name": "Тестовый", "phone": "+70000000401"}}
    if topic == "region":
        facts["debts"] = {"total": 250000}
    state = enter(fresh(**facts))
    expected_path = "debts.total" if topic == "amount" else "client.region_class"
    previous_message = "Хочу списать свои долги"
    for _ in range(3):
        assert state["_plan_object"].field_paths == [expected_path]
        reply = "".join(core.stream_reply(state, previous_message, config()))
        state = core.commit_turn(state, previous_message, reply)["current_state"]
        state = core.prepare_turn(state, "Не знаю", config(), submit_delivery=False)
        previous_message = "Не знаю"
    assert get_fact(state["facts"], expected_path) is None
    assert state["_plan_object"].field_paths == [] and state["_plan_object"].matched is None
    assert state["dialog_phase"] == "incomplete_held"
    final_reply = "".join(core.stream_reply(state, "Не знаю", config()))
    assert "сведения собраны" not in final_reply.lower()
    assert not state.get("delivery")


class IntakeCounter:
    def __init__(self):
        self.calls = 0

    def lead_intake(self, payload):
        self.calls += 1
        return LeadIntakeResponse.model_validate({"success": True, "result": {"leadID": 904001}})


@pytest.mark.parametrize("mode", ["completed", "abandoned"])
@pytest.mark.parametrize("kind", ["debt", "clarify", "cancelled", "malformed", "null"])
def test_debt_hold_blocks_real_coordinator_with_a_live_legacy_positive_control(mode, kind):
    counter = IntakeCounter()
    coordinator = LeadIntakeCoordinator(counter, persist=lambda _: None, now=lambda: NOW)

    def session(identifier):
        return Session(id=identifier, state=init_state({"full_name": "Тестовый Клиент", "phone": "+70000000401"}),
                       anketa=None, usage=SessionUsage(identifier), created_at=NOW - timedelta(hours=2),
                       last_active=NOW - timedelta(hours=1))

    protected = session("independent-debt-held")
    policy = deepcopy(enter()[POLICY_KEY])
    if kind in {"clarify", "cancelled"}:
        policy["mode"] = kind
    elif kind == "malformed":
        policy = {"version": 999, "mode": "standard", "ever_debt": False}
    elif kind == "null":
        policy = None
    protected.state[POLICY_KEY] = policy

    def submit(item):
        if mode == "completed":
            return coordinator.submit_completed(item, site_consent_shown=True, site_consent_answer=True)
        return coordinator.submit_partial_if_due(item, now=NOW)

    assert submit(protected).attempted is False
    assert counter.calls == 0
    assert submit(session("independent-debt-control")).attempted is True
    assert counter.calls == 1
