"""Debt preferences follow the visible question and preserve questions about alternatives."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json

import pytest

import core
import debt_policy
import llm_agent
from routing import get_fact
from tests.test_policy_grounded_voice import install, semantic_raw, strict_settings
from tests.test_policy_voice import debt_state


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Question-scope regressions must not call a provider or delivery service")

    monkeypatch.setattr(llm_agent, "_urlopen", forbidden)
    monkeypatch.setattr(llm_agent, "extract_facts", lambda *args, **kwargs: {"fact_updates": [], "internal_summary": ""})
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *args, **kwargs: deepcopy(facts))
    monkeypatch.setattr(core.delivery, "submit", forbidden)
    monkeypatch.setattr(core, "_record_transcript", lambda *args, **kwargs: None)


@pytest.mark.parametrize("previous,declined", [
    ("По долгам вопросы ещё есть?", False),
    ("Номер можно оставить позже. По долгам вопросы ещё есть?", False),
    ("Можем просто обсудить вашу ситуацию здесь.", False),
    ("На какой номер можно с вами связаться?", True),
    ("Оставьте, пожалуйста, номер телефона.", True),
    (None, False),
])
@pytest.mark.parametrize("analysis_available", [True, False])
def test_a_bare_no_uses_the_visible_question_through_prepare_stream_and_commit(
    monkeypatch, previous, declined, analysis_available,
):
    state = debt_state(debts={"total": 280000}, client={"name": "Сергей", "region_class": "moscow_mo"})
    assert state["script"]["asked"] == ["client.phone"]
    state["chat_history"] = [{"role": "assistant", "content": previous}] if previous is not None else []
    history, facts = deepcopy(state["chat_history"]), deepcopy(state["facts"])
    raw = json.loads(semantic_raw())
    if not analysis_available:
        raw["user"] = None
        raw["claims"] = None
    install(monkeypatch, ["Можем продолжить общение здесь."] * 2, [json.dumps(raw)] * 2)
    user = "Нет, спасибо."
    prepared = core.prepare_turn(state, user, strict_settings(), submit_delivery=False)
    # The strict conversation orchestrator owns contact choices, so prepare
    # only selects the debt policy and must not apply the same decision early.
    assert prepared["questionnaire_policy"]["phone_collection"] == "open"
    text = "".join(core.stream_reply(prepared, user, strict_settings()))
    committed = core.commit_turn(prepared, user, text)["current_state"]
    assert (committed["questionnaire_policy"]["phone_collection"] == "declined") is declined
    assert committed["facts"] == facts and committed["chat_history"][:len(history)] == history
    assert not committed.get("delivery")


@pytest.mark.parametrize("analysis_available", [True, False])
def test_an_explicit_phone_refusal_does_not_need_a_previous_question(monkeypatch, analysis_available):
    state = debt_state(debts={"total": 280000}, client={"name": "Сергей", "region_class": "moscow_mo"})
    raw = json.loads(semantic_raw())
    if not analysis_available:
        raw["user"] = None
        raw["claims"] = None
    install(monkeypatch, ["Можем продолжить общение здесь."] * 2, [json.dumps(raw)] * 2)
    user = "Номер оставлять не хочу."
    prepared = core.prepare_turn(state, user, strict_settings(), submit_delivery=False)
    assert prepared["questionnaire_policy"]["phone_collection"] == "open"
    text = "".join(core.stream_reply(prepared, user, strict_settings()))
    committed = core.commit_turn(prepared, user, text)["current_state"]
    assert committed["questionnaire_policy"]["phone_collection"] == "declined"
    assert not committed.get("delivery")


def test_legacy_prepare_retains_early_explicit_phone_refusal():
    state = debt_state(debts={"total": 280000}, client={"name": "Сергей", "region_class": "moscow_mo"})
    config = replace(strict_settings(), policy_semantic_enabled=False)
    prepared = core.prepare_turn(state, "Номер оставлять не хочу.", config, submit_delivery=False)
    assert prepared["questionnaire_policy"]["phone_collection"] == "declined"
    assert not prepared.get("delivery")


def test_an_opted_out_early_interpretation_preserves_the_policy_until_the_renderer_runs():
    state = debt_state(debts={"total": 280000}, client={"name": "Сергей", "region_class": "moscow_mo"})
    original = deepcopy(state[debt_policy.POLICY_KEY])
    selected = core.debt_workflow.select_for_turn(state, "Номер оставлять не хочу.", enabled=False,
                                                 interpret_contact_refusal=False)
    assert selected is not None and selected.phone_collection == "open"
    assert state[debt_policy.POLICY_KEY] == original


@pytest.mark.parametrize("message", [
    "Москва, можно ли без банкротства?", "Казань; а можно без банкротства?",
    "Можно ли, пожалуйста, без банкротства?", "Я живу в Москве. Можно без банкротства?",
])
def test_a_question_about_an_alternative_keeps_the_existing_debt_goal(message):
    policy = debt_policy.DebtPolicyState()
    selected = debt_policy.select_policy({debt_policy.POLICY_KEY: policy.as_state()}, message)
    assert selected is not None and selected.mode == "debt"


@pytest.mark.parametrize("message", ["Банкротство мне не нужно.", "Не хочу банкротство.", "Стоп."])
def test_an_asserted_decline_or_stop_still_cancels_the_debt_goal(message):
    policy = debt_policy.DebtPolicyState()
    selected = debt_policy.select_policy({debt_policy.POLICY_KEY: policy.as_state()}, message)
    assert selected is not None and selected.mode == "cancelled"


@pytest.mark.parametrize("message,region", [
    ("Москва, можно ли без банкротства?", "moscow_mo"),
    ("Казань; а можно без банкротства?", "rf_other"),
    ("Я живу в Москве, можно ли без банкротства?", "moscow_mo"),
])
def test_a_catalogued_location_before_a_procedure_question_is_kept_in_the_same_turn(monkeypatch, message, region):
    state = debt_state(debts={"total": 280000}, client={"phone": "+70000000001"})
    assert state["script"]["asked"] == ["client.region_class"]
    install(monkeypatch, ["Можно обсуждать изменение условий кредита с кредитором."] * 2, [semantic_raw()] * 2)
    prepared = core.prepare_turn(state, message, strict_settings(), submit_delivery=False)
    assert prepared["questionnaire_policy"]["mode"] == "debt"
    assert get_fact(prepared["facts"], "client.region_class") == region
    assert "client.region_class" not in prepared["plan"]["fields"]
    text = "".join(core.stream_reply(prepared, message, strict_settings()))
    committed = core.commit_turn(prepared, message, text)["current_state"]
    assert committed["questionnaire_policy"]["mode"] == "debt"
    assert get_fact(committed["facts"], "client.region_class") == region
    assert not committed.get("delivery")
    assert "В каком городе" not in text


@pytest.mark.parametrize("message", [
    "У брата Москва, можно ли без банкротства?", "Если Москва, можно ли без банкротства?",
    "Москва или Казань, можно ли без банкротства?", "Не Москва, можно ли без банкротства?",
    "Работаю в Москве, можно ли без банкротства?", "Моя квартира в Москве, можно ли без банкротства?",
])
def test_a_question_does_not_turn_a_third_party_hypothesis_or_asset_into_the_clients_region(message):
    state = debt_state(debts={"total": 280000})
    prepared = core.prepare_turn(state, message, strict_settings(), submit_delivery=False)
    assert get_fact(prepared["facts"], "client.region_class") is None
    assert not prepared.get("delivery")
