"""A chosen placeholder does not establish the client's exact remaining debt."""
from __future__ import annotations

from copy import deepcopy

import pytest

import core
import debt_policy
import llm_agent
from config import AppConfig
from interpretation import FactCandidate, FactCandidateExtraction
from routing import get_fact
from routing.normalize import MONEY_APPROXIMATION, parse_amount
from tests.test_policy_voice import debt_state


NOMINAL_ANSWERS = [
    "Тогда пусть будет 400 тысяч.",
    "Пусть сумма будет 400000 рублей.",
    "Будем считать 400 тысяч рублей.",
    "Давайте возьмём 400 тысяч.",
    "Тогда поставим 400000 рублей.",
    "Давайте запишем сумму 400000 рублей.",
    "Пусть будет ровно 400000.",
]


@pytest.mark.parametrize("message", NOMINAL_ANSWERS)
def test_a_nominal_choice_keeps_debt_unknown_even_when_a_number_has_units(message):
    observation = debt_policy.detect_debt_observation(message, expected=True)
    assert observation is not None and observation.kind == "unknown"
    assert observation.amount is None and observation.lower is None and observation.upper is None
    assert MONEY_APPROXIMATION.search(message)


@pytest.mark.parametrize("message", [
    "400000", "Ровно 400000", "У меня ровно 400 тысяч рублей долга.",
    "Пожалуйста, 400000 рублей.", "Запишите 400000 рублей, это точный остаток по выписке.",
])
def test_the_clients_literal_or_explicitly_known_exact_amount_stays_supported(message):
    observation = debt_policy.detect_debt_observation(message, expected=True)
    assert observation is not None and observation.kind == "exact" and observation.amount == 400000


@pytest.mark.parametrize("message", [
    "Пусть будет Москва.", "Давайте запишем имя Сергей.", "Будем считать условия подходящими.",
])
def test_nominal_language_without_a_numeric_choice_is_not_a_money_qualifier(message):
    assert not MONEY_APPROXIMATION.search(message)
    assert debt_policy.detect_debt_observation(message, expected=False) is None


def test_legacy_numeric_conversion_remains_separate_from_exact_debt_evidence():
    assert parse_amount("400 тысяч") == 400000
    assert parse_amount("около 400 тысяч") == 400000


@pytest.mark.parametrize("message", ["Пусть будет четыреста тысяч.", "Возьмём триста тысяч рублей."])
def test_word_only_nominal_choices_cannot_be_promoted_by_the_debt_parser(message):
    observation = debt_policy.detect_debt_observation(message, expected=True)
    # The exact debt parser supports numeric amounts. Unsupported word-only
    # choices also stay unconfirmed instead of borrowing generic model conversion.
    assert observation is None or observation.kind == "unknown"


@pytest.fixture
def poisoned_extractor(monkeypatch):
    calls = []

    def forbidden(*args, **kwargs):
        pytest.fail("Nominal confirmation regressions must not call providers or delivery services")

    def legacy(payload, *args, **kwargs):
        calls.append("legacy")
        return {"fact_updates": [{"path": "debts.total", "value": 400000}], "internal_summary": ""}

    def semantic(payload, *args, **kwargs):
        calls.append("semantic")
        return FactCandidateExtraction(candidates=[FactCandidate(
            path="debts.total", value=400000, evidence=payload["latest_user_message"],
        )])

    monkeypatch.setattr(llm_agent, "_urlopen", forbidden)
    monkeypatch.setattr(llm_agent, "extract_facts", legacy)
    monkeypatch.setattr(llm_agent, "extract_fact_candidates", semantic)
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *args, **kwargs: deepcopy(facts))
    monkeypatch.setattr(core.delivery, "submit", forbidden)
    monkeypatch.setattr(core, "_record_transcript", lambda *args, **kwargs: None)
    return calls


@pytest.mark.parametrize("mode", ["legacy", "semantic"])
@pytest.mark.parametrize("message", [NOMINAL_ANSWERS[0], NOMINAL_ANSWERS[2], NOMINAL_ANSWERS[3]])
def test_an_actual_debt_turn_rejects_a_model_promoted_exact_placeholder(poisoned_extractor, mode, message):
    config = AppConfig(openrouter_api_key="synthetic-unused", debt_policy_enabled=True,
                       fact_interpretation_mode=mode)
    state = debt_state(client={"phone": "+70000000001", "region_class": "rf_other"})
    # Preserve the observed conversational sequence, including the earlier estimate.
    estimated = core.prepare_turn(state, "Около 400 тысяч долга.", config, submit_delivery=False)
    previous = core.commit_turn(estimated, "Около 400 тысяч долга.",
                                "Если нет точной суммы, можем продолжить с этой цифрой.")["current_state"]
    history = deepcopy(previous["chat_history"])
    prepared = core.prepare_turn(previous, message, config, submit_delivery=False)
    assert mode in poisoned_extractor
    assert get_fact(prepared["facts"], "debts.total") is None
    assert prepared["questionnaire_policy"]["amount_observation"]["kind"] == "unknown"
    assert prepared["fact_statuses"]["debts.total"] == "needs_confirmation"
    assert prepared["plan"]["fields"] == ["debts.total"]
    assert prepared["_policy_received_fields"] == []
    assert prepared["chat_history"] == history
    assert prepared["dialog_phase"] != "collected_held" and not prepared.get("delivery")


@pytest.mark.parametrize("mode", ["legacy", "semantic"])
def test_a_nominal_correction_preserves_the_previous_verified_value_without_reusing_it_for_completion(
    poisoned_extractor, mode,
):
    config = AppConfig(openrouter_api_key="synthetic-unused", debt_policy_enabled=True,
                       fact_interpretation_mode=mode)
    state = debt_state(debts={"total": 350000}, client={"phone": "+70000000001", "region_class": "rf_other"})
    uncertain = core.prepare_turn(state, "Теперь общий долг около 400 тысяч.", config, submit_delivery=False)
    prepared = core.prepare_turn(uncertain, NOMINAL_ANSWERS[0], config, submit_delivery=False)
    assert get_fact(prepared["facts"], "debts.total") == 350000
    assert prepared["questionnaire_policy"]["amount_observation"]["kind"] == "unknown"
    assert prepared["fact_statuses"]["debts.total"] == "needs_confirmation"
    assert prepared["plan"]["fields"] == ["debts.total"] and not prepared.get("delivery")
