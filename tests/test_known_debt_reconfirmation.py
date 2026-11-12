"""A confirmed total cannot be reopened through an immediately linked follow-up."""
from __future__ import annotations

from dataclasses import replace
import socket

import pytest

from dialogue_frame import DialogueFrame, constrain_collection, request_fields
import policy_voice
from tests.test_debt_transfer_preference import known_state, REFUSAL
from tests.test_policy_v22_independent import actual_turn


G1 = ("Конечно, обсудим всё в чате.\n\nСумма долга 400 тысяч рублей — "
      "это общая задолженность по всем кредитам и займам вместе?")
G2 = ("Без проблем, общаемся только здесь.\n\nВижу общую сумму долга 400 000 рублей. "
      "Это точная цифра или примерная?")
G3 = "Хорошо, давайте разбираться. Общая сумма долга у нас 400 000 рублей — это верно?"
FRAME = DialogueFrame(None, "contact_declined_held", frozenset({"debts.total"}), conversation_declined=True)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*a, **k):
        raise AssertionError("Known-debt guard tests cannot use external services")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.mark.parametrize("draft", [G1, G2])
def test_actual_live_candidates_are_hard_invalid_for_an_already_confirmed_total(draft):
    assert "debts.total" in constrain_collection(draft, FRAME).removed_fields
    assert policy_voice._model_reply(draft, FRAME, "offline_known_total") is None


@pytest.mark.parametrize("draft", [G1, G2])
def test_the_normal_current_chat_cannot_serve_either_saved_confirmation(monkeypatch, draft):
    state = known_state()
    state["questionnaire_policy"] = {
        "version": 1, "mode": "debt", "reason": "explicit_request", "ever_debt": True,
    }
    safe = "Да, остаёмся в чате."
    answer, _, committed, _ = actual_turn(monkeypatch, state, REFUSAL, replies=[draft, safe])
    assert answer == safe and committed["facts"]["debts"]["total"] == 400000
    assert committed["questionnaire_policy"]["transfer_declined"] is True


@pytest.mark.parametrize("draft", [
    "Общая сумма долга 500 тысяч рублей. Эта сумма точная или приблизительная?",
    "Вижу долг 280000 рублей. Это вся задолженность?",
    "Сумма долга — 400 тысяч рублей. Это общая сумма по всем кредитам вместе?",
    "Вижу общую сумму долга 400.5 тысячи рублей. Это точная цифра?",
    "Долг 900000 рублей — это вся сумма?",
    "Задолженность 400000 рублей — это точная сумма или примерная?",
])
def test_precision_and_totality_questions_use_a_local_debt_amount_antecedent(draft):
    assert "debts.total" in constrain_collection(draft, FRAME).removed_fields


@pytest.mark.parametrize("draft", [
    "Это точная цифра или примерная?",
    "Цена консультации 400000 рублей. Это точная цифра или примерная?",
    "Доход 400000 рублей. Это точная сумма?",
    "В примере сумма долга 400000 рублей. Это точная цифра?",
    "Если сумма долга 400000 рублей. Это точная цифра?",
    "«Вижу общую сумму долга 400000 рублей». Это точная цифра?",
    "Он сказал: сумма долга 400000 рублей. Это точная сумма?",
    "Вижу общую сумму долга 400000 рублей. Цена консультации 1000 рублей. Это точная цифра?",
    "Сумма долга 400000 рублей.\n\nЭто точная цифра или примерная?",
    "Долг 400000 рублей. Это точная цифра в вашем примере?",
    "При долге 400000 рублей можно обсудить варианты. Это точная цифра?",
])
def test_a_missing_or_unrelated_antecedent_does_not_turn_any_precision_question_into_intake(draft):
    assert "debts.total" not in constrain_collection(draft, FRAME).removed_fields


@pytest.mark.parametrize("draft", [
    "Общая сумма долга известна. Точную цифру можно уточнить в выписке.",
    "Банкротство не освобождает от всех обязательств автоматически.",
    "Что такое общая задолженность по кредитам?",
    "Как узнать точную сумму долга по выписке?",
    "Сумма долга 400000 рублей. «Это точная цифра или примерная?»",
])
def test_faq_and_quoted_explanations_keep_their_own_role(draft):
    assert "debts.total" not in constrain_collection(draft, FRAME).removed_fields


def test_the_same_precision_question_remains_available_when_the_debt_is_actually_unknown():
    frame = replace(FRAME, expected="debts.total", known_fields=frozenset(), confirming=True)
    assert not constrain_collection(G2, frame).removed_fields


def test_a_confirmed_debt_stays_confirmed_while_another_field_needs_clarification():
    frame = replace(FRAME, expected="client.region_class", confirming=True)
    assert "debts.total" in constrain_collection(G2, frame).removed_fields


def test_a_preceding_amount_acknowledgment_itself_is_not_a_question():
    assert request_fields("Вижу общую сумму долга 400000 рублей.", FRAME) == set()


def test_direct_request_observation_and_collection_use_the_same_bounded_context():
    assert "debts.total" in request_fields(G2, FRAME)
    unbound = "Вижу общую сумму долга 400000 рублей.\n\nЭто точная цифра?"
    assert "debts.total" not in request_fields(unbound, FRAME)


def test_a_multisentence_quote_cannot_supply_a_debt_antecedent_and_a_question():
    quoted = "Он написал: «Пример. Вижу долг 400000 рублей. Это точная цифра или примерная? Это цитата»."
    assert "debts.total" not in constrain_collection(quoted, FRAME).removed_fields
    assert "debts.total" not in request_fields(quoted, FRAME)


def test_a_quoted_example_cannot_hide_a_separate_actual_reconfirmation_after_it():
    text = "Он написал: «Это пример. Здесь нет вопроса». Вижу долг 400000 рублей. Это точная цифра?"
    assert "debts.total" in constrain_collection(text, FRAME).removed_fields


def test_the_new_live_truth_confirmation_is_rejected_in_the_normal_entry(monkeypatch):
    safe = "Да, можем обсудить здесь."
    answer, prepared, committed, calls = actual_turn(monkeypatch, known_state(), "Разобраться с долгами", replies=[G3, safe])
    # The forbidden question is removed before C, retaining G1's complete reaction.
    assert answer == "Хорошо, давайте разбираться."
    assert [body["model"] for body in calls] == ["synthetic-speaker", "synthetic-analysis"]
    assert prepared["_policy_spoken"].critic_calls == 1
    assert committed["facts"]["debts"]["total"] == 400000


@pytest.mark.parametrize("draft", [
    G3,
    "У вас общая сумма долга 400000 рублей. Это правильно?",
    "У нас сумма долга 280000 рублей — это так?",
    "Общая сумма долга составляет 500000 рублей. Это точная сумма или примерная?",
    "Задолженность получается 250000 рублей — это верно?",
    "Общая сумма долга у вас 400000 рублей. Это общая задолженность?",
])
def test_truth_precision_and_totality_share_the_same_debt_declaration_binding(draft):
    assert "debts.total" in constrain_collection(draft, FRAME).removed_fields


@pytest.mark.parametrize("draft", [
    "Это верно?",
    "Минимальная сумма долга для процедуры 400000 рублей. Это верно?",
    "Если сумма долга составляет 400000 рублей. Это правильно?",
    "Цена консультации у нас 400000 рублей. Это верно?",
    "Он сказал: «Общая сумма долга у нас 400000 рублей. Это верно?»",
    "Общая сумма долга у нас 400000 рублей. Доход 200000 рублей. Это правильно?",
])
def test_truth_confirmation_does_not_acquire_an_unrelated_or_nonasserted_antecedent(draft):
    assert "debts.total" not in constrain_collection(draft, FRAME).removed_fields


def test_the_new_declaration_does_not_override_real_pending_uncertainty():
    frame = replace(FRAME, expected="debts.total", known_fields=frozenset(), confirming=True)
    assert "debts.total" not in constrain_collection(G3, frame).removed_fields
