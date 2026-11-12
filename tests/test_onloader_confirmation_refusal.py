"""Finite current-offer refusal regressions from the recorded stage12 dialogue."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

import core
from config import AppConfig
from onloader import policy
from session_store import _persistable_state
from tests.test_policy_voice import onloader_state


@pytest.mark.parametrize("message", [
    "Понял, не буду подтверждать.",
    "Не буду подтверждать.",
    "Я не буду подтверждать.",
    "Поняла, я не буду подтверждать.",
    "Не хочу сейчас подтверждать.",
    "Я пока не готова подтверждать.",
    "Сейчас не буду подтверждать.",
    "Понял, я не хочу пока подтверждать.",
    "Я не готов подтверждать.",
    "Я сейчас не хочу подтверждать.",
    "Сейчас я не хочу подтверждать.",
    "Подтверждать сейчас не хочу.",
    "Поняла, подтверждать пока не готова.",
])
def test_direct_confirmation_refusal_binds_only_the_current_presentation(message):
    state = onloader_state()
    state["_last_user_message"] = message
    prepared = policy.prepare(state)
    assert policy.chat_declined(prepared)
    assert prepared[policy.CHAT_KEY]["presentation_id"] == state["onloader"]["presentation_id"]
    assert prepared["onloader"]["decision"] is None
    assert policy.CHAT_KEY not in state


@pytest.mark.parametrize("message", [
    "Не, пока не надо.",
    "Не, сейчас не надо.",
    "Не пока не надо.",
    "Пока нет.",
    "Сейчас нет.",
])
def test_colloquial_temporal_refusal_binds_only_the_current_offer(message):
    state = onloader_state()
    state["_last_user_message"] = message
    prepared = policy.prepare(state)
    assert policy.chat_declined(prepared)
    assert prepared[policy.CHAT_KEY]["presentation_id"] == state["onloader"]["presentation_id"]
    assert prepared["onloader"]["decision"] is None
    assert policy.CHAT_KEY not in state


@pytest.mark.parametrize("message", [
    "Не, пока не надо?",
    "А если пока нет?",
    "Не, пока не надо, если передумаю.",
    '«Не, пока не надо».',
    'Он написал: «Не, пока не надо».',
    "Ей пока не надо.",
    "Я не говорил, что пока нет.",
    "Не, пока не надо переводить деньги.",
    "Пока нет денег на оплату.",
    "Не, пока не надо менять тариф.",
    "Пока нет?",
])
def test_colloquial_temporal_refusal_does_not_claim_other_or_uncertain_operations(message):
    state = onloader_state()
    state["_last_user_message"] = message
    assert not policy.chat_declined(policy.prepare(state))


def test_colloquial_refusal_without_an_actual_offer_cannot_create_a_preference():
    state = onloader_state()
    state.pop("onloader")
    state["_last_user_message"] = "Не, пока не надо."
    prepared = policy.prepare(state)
    assert policy.CHAT_KEY not in prepared
    assert not policy.chat_declined(prepared)


@pytest.mark.parametrize("message", [
    "А если не буду подтверждать?",
    "Не буду подтверждать, если передумаю.",
    "Не буду подтверждать?",
    "Он не будет подтверждать.",
    'Он написал: «Не буду подтверждать».',
    '«Не буду подтверждать».',
    "Я не говорил, что не буду подтверждать.",
    "Не буду подтверждать перевод денег.",
    "Понял, не буду подтверждать оплату.",
    "Не буду подтверждать завтра.",
    "Я пока не решил, буду ли подтверждать.",
    "А если не хочу сейчас подтверждать?",
    "Не хочу сейчас подтверждать, если передумаю.",
    "Не хочу сейчас подтверждать?",
    "Она сейчас не готова подтверждать.",
    'Он написал: «Не хочу сейчас подтверждать».',
    '«Не хочу сейчас подтверждать».',
    "Я не говорил, что не хочу сейчас подтверждать.",
    "Не хочу сейчас подтверждать перевод денег.",
    "Я не готова подтверждать оплату.",
    "Не хочу сейчас подтверждать чужую заявку.",
    "Подтверждать сейчас не хочу?",
    "Подтверждать сейчас не хочу, если передумаю.",
    '«Подтверждать сейчас не хочу».',
    "Подтверждать оплату сейчас не хочу.",
])
def test_questions_conditions_quotes_and_other_operations_do_not_decline_the_offer(message):
    state = onloader_state()
    state["_last_user_message"] = message
    assert not policy.chat_declined(policy.prepare(state))


@pytest.mark.parametrize("status", ["queued", "stale", "outcome_unknown", "signal_pending"])
@pytest.mark.parametrize("message", [
    "Понял, не буду подтверждать.", "Не хочу сейчас подтверждать.", "Не, пока не надо.", "Пока нет.",
])
def test_confirmation_refusal_requires_an_awaiting_offer(status, message):
    state = onloader_state()
    state["onloader"]["status"] = status
    state["_last_user_message"] = message
    assert not policy.chat_declined(policy.prepare(state))


@pytest.mark.parametrize("decision", ["accept", "decline"])
@pytest.mark.parametrize("message", ["Не хочу сейчас подтверждать.", "Не, пока не надо."])
def test_conversational_refusal_does_not_replace_an_existing_formal_decision(decision, message):
    state = onloader_state()
    state["onloader"]["decision"] = decision
    state["_last_user_message"] = message
    prepared = policy.prepare(state)
    assert prepared["onloader"]["decision"] == decision
    assert policy.CHAT_KEY not in prepared


@pytest.mark.parametrize("message", [
    "Понял, не буду подтверждать.", "Не хочу сейчас подтверждать.", "Не, пока не надо.",
])
def test_recorded_refusal_survives_actual_prepare_commit_restore_and_a_followup(monkeypatch, message):
    def forbidden(*args, **kwargs):
        pytest.fail("This refusal regression cannot use a model or submit an application")

    monkeypatch.setattr(core.llm_agent, "_urlopen", forbidden)
    monkeypatch.setattr(core.delivery, "submit", forbidden)
    config = AppConfig(openrouter_api_key="synthetic-unused", policy_voice_enabled=False)
    state = onloader_state()
    state["facts"] = {"debts": {"total": 400000}}
    original_facts = deepcopy(state["facts"])
    prepared = core.prepare_turn(state, message, config, submit_delivery=False)
    assert policy.chat_declined(prepared)
    speech = "".join(core.stream_reply(prepared, message, config))
    committed = core.commit_turn(prepared, message, speech)["current_state"]
    restored = json.loads(json.dumps(_persistable_state(committed), ensure_ascii=False))
    assert policy.chat_declined(restored)
    assert restored["onloader"]["decision"] is None
    assert restored["facts"] == original_facts
    assert not restored.get("delivery")
    assert restored["chat_history"][-2]["content"] == message
    followup = core.prepare_turn(restored, "Зачем нужны сведения?", config, submit_delivery=False)
    assert policy.chat_declined(followup)
    assert followup["onloader"]["decision"] is None
    # A preference is presentation-scoped, never authority over a replacement offer.
    followup["onloader"]["presentation_id"] = "replacement-presentation"
    assert not policy.chat_declined(followup)
