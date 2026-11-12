"""A debt chat keeps known facts and a durable negative-only transfer choice."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import socket

import pytest

import core
import debt_policy
from config import AppConfig
from dialogue_frame import DialogueFrame, constrain_collection, request_fields
from interpretation import FactCandidateExtraction
import policy_semantics
from session_store import SessionStore
from state import init_state
from storage.sqlite_sessions import SQLiteSessionRepository
from tests.test_policy_v22_independent import actual_turn


REFUSAL = "Пока хочу обсудить всё здесь. Никуда передавать мои данные не нужно."
CONFIG = AppConfig(openrouter_api_key="synthetic-unused", debt_policy_enabled=True,
                   policy_voice_enabled=True, policy_semantic_enabled=True)


@pytest.fixture(autouse=True)
def offline(monkeypatch, local_loop):
    def blocked(*args, **kwargs):
        raise AssertionError("Transfer preference tests cannot connect or deliver")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(core.delivery, "submit", blocked)
    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *a, **k: {"fact_updates": [], "internal_summary": ""})
    monkeypatch.setattr(core.llm_agent, "extract_fact_candidates", lambda *a, **k: FactCandidateExtraction())
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *a, **k: deepcopy(facts))
    monkeypatch.setattr(core, "_record_transcript", lambda *a, **k: None)


@pytest.fixture(scope="module")
def local_loop():
    # Windows creates the loop's internal socketpair before the network trap.
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def known_state():
    state = init_state({"full_name": "Тест", "phone": "89990000401", "region": "Казань", "debts_total": 400000})
    state["fact_statuses"] = {"debts.total": "confirmed", "client.region_class": "known"}
    return state


def enter(state=None):
    return core.prepare_turn(state or known_state(), "Разобраться с долгами", CONFIG, submit_delivery=False)


def test_a_bare_numeric_confirmation_is_collection_of_an_already_known_exact_debt():
    frame = DialogueFrame(None, "collected_held", frozenset({"debts.total"}))
    draft = "400000 — это верно?"
    assert request_fields(draft, frame) == {"debts.total"}
    assert constrain_collection(draft, frame).removed_fields == ("debts.total",)


def test_normal_debt_entry_does_not_serve_the_saved_reconfirmation(monkeypatch):
    state = known_state()
    answer, prepared, committed, _ = actual_turn(monkeypatch, state, "Разобраться с долгами",
                                               replies=["400000 — это верно?", "Да, можем обсудить здесь."])
    assert "верно?" not in answer
    assert prepared["_plan_object"].field_paths == []
    assert committed["facts"] == state["facts"]


@pytest.mark.parametrize("frame", [
    DialogueFrame("debts.total", "collecting_debt_context", confirming=True, require_question=True),
    DialogueFrame(None, "collected_held"),
])
def test_numeric_confirmation_is_not_bound_to_an_unproven_debt(frame):
    assert request_fields("400000 — это верно?", frame) == set()


def test_legal_threshold_and_other_money_questions_are_not_known_debt_reconfirmation():
    frame = DialogueFrame(None, "collected_held", frozenset({"debts.total"}))
    assert request_fields("Минимум 190000 рублей — это верно?", frame) == set()
    assert request_fields("Цена консультации 400000 рублей — это верно?", frame) == set()


def test_a_pending_region_confirmation_cannot_reopen_a_known_exact_debt():
    frame = DialogueFrame("client.region_class", "collecting_debt_context", frozenset({"debts.total"}),
                          confirming=True)
    assert constrain_collection("400000 — это верно?", frame).removed_fields == ("debts.total",)


def test_a_truly_uncertain_pending_debt_keeps_its_confirmation_question():
    frame = DialogueFrame("debts.total", "collecting_debt_context", confirming=True)
    question = "400000 рублей — это верно?"
    assert request_fields(question, frame) == {"debts.total"}
    assert constrain_collection(question, frame).text == question


@pytest.mark.parametrize("message", [
    REFUSAL, "Не передавайте мои данные.", "Я не хочу передавать свои данные.",
    "Без передачи моих данных.", "Не разрешаю передачу моих данных.",
])
def test_an_own_transfer_refusal_is_durable_even_before_any_offer(message):
    prepared = core.prepare_turn(enter(), message, CONFIG, submit_delivery=False)
    policy = debt_policy.parse_policy(prepared)
    assert policy is not None and policy.transfer_declined is True
    assert policy.phone_collection == "open" and policy.mode == "debt"
    assert prepared["_plan_object"].field_paths == []
    assert prepared["dialog_phase"] == "contact_declined_held"
    assert debt_policy.debt_delivery_held(prepared)
    assert not prepared.get("delivery") and not prepared.get("onloader")


@pytest.mark.parametrize("message", [
    "Нет.", "Если я не хочу передавать свои данные, что будет?", "Не передавайте мои данные?",
    "Он сказал: не передавайте мои данные.", "«Не передавайте мои данные».",
    "Мама не хочет передавать свои данные.", "Не хочу ли я передавать свои данные?",
    "Я не против передачи моих данных.", "Передавайте мои данные.",
])
def test_unknown_and_nonasserted_mentions_cannot_create_a_transfer_decision(message):
    prepared = core.prepare_turn(enter(), message, CONFIG, submit_delivery=False)
    policy = debt_policy.parse_policy(prepared)
    assert policy is not None and policy.transfer_declined is False


def test_code_refusal_is_available_to_the_renderer_without_an_offer():
    frame = DialogueFrame(None, "collected_held")
    assert policy_semantics.explicit_conversational_declines(REFUSAL, frame) == ("decline_transfer",)


@pytest.mark.parametrize("quoted", [
    "Менеджер написал: «Это пример. Не передавайте мои данные. Это только цитата».",
    'Он написал: "Это пример. Не передавайте мои данные. Это только цитата".',
    "Он написал: «Это пример. Не передавайте мои данные.",
])
def test_a_refusal_inside_a_multisentence_or_unfinished_quote_is_not_an_own_decision(quoted):
    assert debt_policy.transfer_contact_declined(quoted) is False
    assert debt_policy.pure_debt_turn_act(quoted, active_debt=True) is None


def test_an_independent_own_refusal_after_a_complete_quote_remains_effective():
    message = "Он написал: «Передавайте данные. Всё готово». Не передавайте мои данные."
    assert debt_policy.transfer_contact_declined(message) is True
    assert debt_policy.pure_debt_turn_act(message, active_debt=True) is None


def test_an_object_first_negative_transfer_instruction_is_the_same_no_fact_act():
    message = "Мои данные никуда не передавайте."
    prepared = core.prepare_turn(enter(), message, CONFIG, submit_delivery=False)
    assert debt_policy.parse_policy(prepared).transfer_declined is True
    assert debt_policy.pure_debt_turn_act(message, active_debt=True) == "decline_transfer"


@pytest.mark.parametrize("message", [
    "Его данные никуда не передавайте.", "Он сказал: мои данные никуда не передавайте.",
    "Мои данные никуда не передавайте?", "Если мои данные никуда не передавайте.",
    "«Мои данные никуда не передавайте».", "Мои данные никуда не передавайте, но отправьте заявку.",
])
def test_an_object_first_fragment_still_needs_a_whole_own_asserted_instruction(message):
    assert debt_policy.transfer_contact_declined(message) is False
    assert debt_policy.pure_debt_turn_act(message, active_debt=True) is None


def test_a_refusal_and_model_outage_keep_the_chat_and_contacts(monkeypatch):
    initial = enter()
    answer, _, committed, _ = actual_turn(monkeypatch, initial, REFUSAL, unavailable=True)
    policy = debt_policy.parse_policy(committed)
    assert answer and policy is not None and policy.transfer_declined is True
    assert policy.phone_collection == "open"
    assert committed["facts"] == initial["facts"]
    assert committed["chat_history"][-2]["content"] == REFUSAL


def test_transfer_preference_survives_real_sqlite_and_a_new_store(tmp_path, local_loop):
    async def scenario():
        path = tmp_path / "sessions.sqlite"
        first = SessionStore(repository=SQLiteSessionRepository(path), require_durable=True)
        session = await first.create(None)
        session.state = core.prepare_turn(enter(), REFUSAL, CONFIG, submit_delivery=False)
        session.state["session_id"] = session.id
        session.state = core.commit_turn(session.state, REFUSAL, "")["current_state"]
        first.persist_durable(session)
        second = SessionStore(repository=SQLiteSessionRepository(path), require_durable=True)
        try:
            restored = await second.get(session.id)
            assert restored is not None and restored.id == session.id
            assert debt_policy.parse_policy(restored.state).transfer_declined is True
            assert restored.state["facts"] == session.state["facts"]
            assert restored.state["chat_history"] == session.state["chat_history"]
            for message in ("Что такое реструктуризация?", "Спасибо.", "Теперь можно передавать мои данные."):
                restored.state = core.prepare_turn(restored.state, message, CONFIG, submit_delivery=False)
                assert debt_policy.parse_policy(restored.state).transfer_declined is True
                assert not restored.state.get("delivery")
        finally:
            first.executor.shutdown(wait=True)
            second.executor.shutdown(wait=True)
    local_loop.run_until_complete(scenario())


def test_old_policy_records_default_to_no_transfer_refusal():
    policy = debt_policy.DebtPolicyState()
    raw = policy.as_state()
    raw.pop("transfer_declined", None)
    restored = debt_policy.parse_policy({debt_policy.POLICY_KEY: raw})
    assert restored is not None and restored.transfer_declined is False


def test_transfer_refusal_does_not_confirm_an_unknown_or_approximate_debt():
    state = enter()
    policy = debt_policy.parse_policy(state)
    state[debt_policy.POLICY_KEY] = policy.with_observation(debt_policy.DebtObservation(kind="range", lower=300000)).as_state()
    prepared = core.prepare_turn(state, REFUSAL, CONFIG, submit_delivery=False)
    assert prepared["_plan_object"].field_paths == ["debts.total"]
    assert prepared["_plan_object"].fields[0].needs_confirmation is True


@pytest.mark.parametrize("message,active,expected", [
    ("Разобраться с долгами", False, "entry"), (REFUSAL, True, "decline_transfer"),
    ("Не передавайте мои данные.", True, "decline_transfer"), ("Нет.", True, None),
    ("Не передавайте мои данные. Мой долг 450000 рублей.", True, None),
    ("Не передавайте мои данные. Что такое банкротство?", True, None),
    ("Не передавайте мои данные.", False, None),
])
def test_only_whole_code_proven_no_fact_acts_can_skip_fact_extraction(message, active, expected):
    assert debt_policy.pure_debt_turn_act(message, active_debt=active) == expected


@pytest.mark.parametrize("message", ["Разобраться с долгами", REFUSAL])
def test_the_exact_chip_and_whole_refusal_need_no_model_extraction(monkeypatch, message):
    state = known_state() if message.startswith("Разобраться") else enter()
    def unwanted(*a, **k):
        raise AssertionError("A proved no-fact act must skip the extractor")
    monkeypatch.setattr(core, "_run_extractors", unwanted)
    prepared = core.prepare_turn(state, message, CONFIG, submit_delivery=False)
    assert prepared["facts"] == state["facts"]


def test_mixed_refusal_still_extracts_and_accepts_a_real_exact_debt_correction(monkeypatch):
    calls = []
    original = core._run_extractors
    def observed(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(core, "_run_extractors", observed)
    prepared = core.prepare_turn(enter(), "Не передавайте мои данные. Мой долг 450000 рублей.", CONFIG,
                                 submit_delivery=False)
    assert calls and prepared["facts"]["debts"]["total"] == 450000
    assert debt_policy.parse_policy(prepared).transfer_declined is True
