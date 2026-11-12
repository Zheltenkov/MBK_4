"""Four greeting choices remain ordinary messages in the existing chat."""
from __future__ import annotations

from copy import deepcopy

import pytest

import quick_replies
import core
import debt_policy
import entry_routing
from config import AppConfig
from interpretation import FactCandidateExtraction
from routing.entry_intent import EntryIntent
from state import init_state


CHOICES = (
    ("Получить деньги", "money"),
    ("Разобраться с долгами", "debt"),
    ("Я уже оставлял заявку", "existing"),
    ("Другой вопрос", "other"),
)


def test_four_greeting_choices_have_the_requested_order_and_public_shape():
    state = {"chat_history": [{"role": "assistant", "content": "Здравствуйте!"}]}
    before = deepcopy(state)

    chips = quick_replies.quick_replies(state, enabled=True)

    assert chips == [{"label": text, "value": text} for text, _ in CHOICES]
    assert state == before


@pytest.mark.parametrize(("text", "choice"), CHOICES)
def test_exact_greeting_choices_are_recognized_without_model_authority(text, choice):
    assert quick_replies.first_message_choice({}, text) == choice
    assert quick_replies.chip_choice("  " + text.lower() + "! ") == choice
    assert quick_replies.chip_choice(text + " и ещё вопрос") is None


@pytest.mark.parametrize(("text", "choice"), CHOICES)
def test_every_greeting_choice_disappears_after_the_client_spoke(text, choice):
    state = {
        "chat_history": [
            {"role": "assistant", "content": "Здравствуйте!"},
            {"role": "user", "content": text},
        ],
        "client": {"name": "Тест", "phone": "79990000000"},
        "debts": {"total": 400000},
    }
    before = deepcopy(state)

    assert quick_replies.quick_replies(state, enabled=True) == []
    assert quick_replies.first_message_choice(state, text) is None
    assert state == before


def test_four_choices_remain_disabled_under_the_existing_ui_flag():
    assert quick_replies.quick_replies({}, enabled=False) == []


def test_disabled_debt_feature_hides_only_the_debt_greeting_choice():
    chips = quick_replies.quick_replies({}, enabled=True, debt_enabled=False)
    assert [chip["value"] for chip in chips] == [
        "Получить деньги", "Я уже оставлял заявку", "Другой вопрос",
    ]


@pytest.mark.parametrize("message", ["Разобраться с долгами", "  РАЗОБРАТЬСЯ   С ДОЛГАМИ. "])
def test_whole_debt_choice_is_explicit_but_still_needs_the_debt_feature(message):
    assert debt_policy.detect_entry(message) == "explicit"
    assert debt_policy.select_policy({}, message, enabled=False) is None
    policy = debt_policy.select_policy({}, message, enabled=True)
    assert policy is not None and policy.mode == "debt"


@pytest.mark.parametrize("message", [
    "Разобраться с долгами?", '"Разобраться с долгами"', "«Разобраться с долгами»",
    "Не хочу разобраться с долгами", "Разобраться с долгами друга",
    "Если разобраться с долгами", "Разобраться с долгами и получить кредит",
])
def test_debt_choice_does_not_promote_a_question_quote_or_extra_words(message):
    assert debt_policy.detect_entry(message) != "explicit"


@pytest.mark.parametrize(("message", "wrong_intent", "expected"), [
    ("Получить деньги", EntryIntent.EXISTING, entry_routing.QUESTIONNAIRE),
    ("Я уже оставлял заявку", EntryIntent.MONEY, entry_routing.EXISTING),
    ("Разобраться с долгами", EntryIntent.MONEY, "debt"),
    ("Другой вопрос", EntryIntent.MONEY, "other"),
])
def test_normal_prepare_routes_exact_choices_in_the_same_chat_preserving_known_facts(
    monkeypatch, message, wrong_intent, expected,
):
    monkeypatch.delenv("MBK_DADATA_API", raising=False)
    monkeypatch.setattr(core.llm_agent, "extract_fact_candidates", lambda *a, **k:
        FactCandidateExtraction(entry_intent=wrong_intent))
    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *a, **k:
        {"fact_updates": [], "internal_summary": ""})
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *a, **k: deepcopy(facts))
    state = init_state()
    state["session_id"] = "same-chat-four-choices"
    state["chat_history"] = [{"role": "assistant", "content": "Здравствуйте!"}]
    state["facts"]["client"] = {"name": "Тест", "phone": "79990000000"}
    state["facts"]["debts"] = {"total": 400000}
    before = deepcopy(state)
    config = AppConfig(openrouter_api_key="synthetic-unused", debt_policy_enabled=True,
        fact_interpretation_mode="semantic", policy_voice_enabled=False)

    prepared = core.prepare_turn(state, message, config, submit_delivery=False)

    assert prepared["session_id"] == state["session_id"]
    assert prepared["chat_history"] == state["chat_history"]
    assert prepared["facts"]["client"]["name"] == "Тест"
    assert prepared["facts"]["client"]["phone"] == "79990000000"
    assert prepared["facts"]["debts"]["total"] == 400000
    assert state == before
    if expected == "debt":
        policy = debt_policy.parse_policy(prepared)
        assert policy is not None and policy.mode == "debt"
        assert debt_policy.debt_delivery_held(prepared)
    elif expected == "other":
        assert prepared[entry_routing.OTHER_QUESTION_KEY] is True
        assert "entry" not in prepared
    else:
        assert prepared["entry"]["scenario"] == expected
        assert prepared["entry"]["source"] == "dictionary"
        assert not debt_policy.debt_delivery_held(prepared)


@pytest.mark.parametrize(("message", "entry_enabled", "debt_enabled"), [
    ("Я уже оставлял заявку", False, False),
    ("Получить деньги", False, False),
    ("Разобраться с долгами", True, False),
])
def test_exact_choices_do_not_enable_a_disabled_branch(
    monkeypatch, message, entry_enabled, debt_enabled,
):
    monkeypatch.delenv("MBK_DADATA_API", raising=False)
    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *a, **k:
        {"fact_updates": [], "internal_summary": ""})
    config = AppConfig(openrouter_api_key="synthetic-unused",
        fact_interpretation_mode="legacy", entry_routing_enabled=entry_enabled,
        debt_policy_enabled=debt_enabled)

    prepared = core.prepare_turn(init_state(), message, config, submit_delivery=False)

    assert not debt_policy.debt_delivery_held(prepared)
    if not entry_enabled:
        assert "entry" not in prepared


def test_exact_existing_choice_does_not_reclassify_an_active_questionnaire():
    state = {"chat_history": [{"role": "user", "content": "Получить деньги"}]}
    entry_routing.route_turn(state, user_message="Я уже оставлял заявку",
        model_intent=EntryIntent.EXISTING, first_message=False)
    assert "entry" not in state
