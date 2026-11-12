"""A validator failure can read back a confirmed fact, never invent a balance."""
from copy import deepcopy
from dataclasses import replace

import pytest

import policy_grounded_voice as grounded
from tests.test_policy_faq_recovery_20261111 import frame, run, UNCHECKED


QUESTION = "Какую сумму долга вы сейчас учитываете?"


def test_c_timeout_answers_known_amount_without_releasing_pause_or_no(monkeypatch):
    selected = replace(frame(), expected="client.phone", conversation_paused=True)
    result, requests, payload = run(monkeypatch, QUESTION, selected=selected)
    assert result.spoken.text == "Сейчас учитываю долг 400 000 рублей."
    assert result.spoken.source == "fallback"
    assert result.spoken.reason == "code_known_debt_amount:semantic_provider_unavailable"
    assert [request["role"] for request in requests] == ["G", "C"]
    assert result.paused and result.effective_frame.conversation_paused
    assert result.effective_frame.conversation_declined
    assert result.effective_frame.expected == "client.phone"
    assert not result.effective_frame.refused_fields and not result.applied_user_acts
    assert result.spoken.asked_field is None and not result.catalog_recovery_sources
    assert result.spoken.critic_assessments[0].failure is not None
    assert UNCHECKED not in result.spoken.text
    assert payload["facts"]["debts"]["total"] == 400000


@pytest.mark.parametrize("latest", [QUESTION,
    "Какая сумма моего долга у вас записана?", "Сколько долга у вас сейчас записано?"])
@pytest.mark.parametrize("amount", [0, 190000, 400000.0, 600000])
def test_fallback_projects_only_confirmed_current_debt(latest, amount):
    facts = {"debts": {"total": amount}, "request": {"amount": 999999}}
    before = deepcopy(facts)
    result = grounded._known_debt_amount_recovery(latest, frame(), facts, "test")
    assert result is not None
    assert str(int(amount)) == result.text.split("долг ")[1].split(" рублей")[0].replace(" ", "")
    assert "999" not in result.text and result.asked_field is None
    assert facts == before


@pytest.mark.parametrize("latest", [
    "Какую сумму кредита вы сейчас учитываете?", "Сколько долга у банка?",
    "Вы учитываете 500000 рублей?", "Поставьте 500000. " + QUESTION,
    "Она спросила: «" + QUESTION + "»", "Не передавайте мои данные. " + QUESTION,
    QUESTION + " Передайте заявку.", "Какую сумму долга мне указать?",
])
def test_latest_cannot_supply_a_balance_or_gain_new_authority(latest):
    assert grounded._known_debt_amount_recovery(latest, frame(), {"debts": {"total": 400000}}, "test") is None


@pytest.mark.parametrize("amount", [None, True, "400000", 400000.5, float("nan"), float("inf"), -1, 2**53])
def test_invalid_or_unconfirmed_storage_is_not_a_balance(amount):
    assert grounded._known_debt_amount_recovery(QUESTION, frame(), {"debts": {"total": amount}}, "test") is None


@pytest.mark.parametrize("selected", [
    replace(frame(), mode="onloader"), replace(frame(), closing=True),
    replace(frame(), confirming=True), replace(frame(), offer_present=True),
    replace(frame(), expected="debts.total"), replace(frame(), known_fields=frozenset({"intent"})),
    replace(frame(), known_fields=frozenset({"debts.total"})),
])
def test_recovery_does_not_turn_candidates_or_another_flow_into_fact(selected):
    assert grounded._known_debt_amount_recovery(QUESTION, selected, {"debts": {"total": 400000}}, "test") is None


def test_normal_successful_generation_keeps_its_own_live_wording(monkeypatch):
    answer = "Вы назвали долг в 400 000 рублей — его и учитываю."
    result, requests, _ = run(monkeypatch, QUESTION, failing="none", draft=answer)
    assert result.spoken.text == answer and result.spoken.source == "model"
    assert [request["role"] for request in requests] == ["G", "C"]
