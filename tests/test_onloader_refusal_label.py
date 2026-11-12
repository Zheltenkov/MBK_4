"""The visible refusal label closes persuasion without creating backend consent."""
from __future__ import annotations

import socket

import pytest

import core
from config import AppConfig
from onloader import policy
from session_store import Session
from tests.test_policy_voice import onloader_state
from usage_tracker import SessionUsage


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    counts = {"send": 0}

    def forbidden(*args, **kwargs):
        raise AssertionError("Refusal label tests do not invoke external services")

    def send(*args, **kwargs):
        counts["send"] += 1
        forbidden()

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(core.llm_agent, "generate_reply", forbidden)
    monkeypatch.setattr(core.llm_agent, "_urlopen", forbidden)
    monkeypatch.setattr(core.delivery, "submit", send)
    return counts


def prepare(state, message):
    return core.prepare_turn(state, message, AppConfig(openrouter_api_key="synthetic-unused"),
                             submit_delivery=True)


@pytest.mark.parametrize("label", ["Не передавать", "Не передавать."])
def test_the_actual_visible_refusal_label_closes_persuasion_only(label, offline):
    prepared = prepare(onloader_state(), label)
    assert policy.chat_declined(prepared) is True
    assert policy.read_state(prepared).decision is None
    assert prepared["plan"] == {"action": "onloader", "fields": []}
    assert prepared[policy.CHAT_KEY]["presentation_id"] == prepared[policy.STATE_KEY]["presentation_id"]
    assert offline["send"] == 0


@pytest.mark.parametrize("message", ["Не передавать.", "Я не хочу передавать сейчас."])
def test_refusal_label_survives_committed_record_restore_and_a_following_faq(offline, message):
    prepared = prepare(onloader_state(), message)
    committed = core.commit_turn(prepared, message, "")["current_state"]
    session = Session(id="synthetic-label", state=committed, anketa=None, usage=SessionUsage("synthetic-label"))
    restored = Session.from_record(session.storage_record()).state
    following = prepare(restored, "А можно просто объяснить, кто увидит данные?")
    assert policy.chat_declined(following) is True
    assert policy.read_state(following).decision is None
    assert policy.reply(following) == "Хорошо, можем просто продолжить разговор здесь."
    assert following["plan"]["fields"] == []
    assert offline["send"] == 0


def test_questions_quotes_and_hypothetical_uses_of_the_label_do_not_create_a_choice(offline):
    for message in [
        "Не передавать?", "Если не передавать, что будет?", "Что означает «Не передавать»?",
        "Он сказал: я не хочу передавать сейчас.", '«Я не хочу передавать сейчас».',
    ]:
        prepared = prepare(onloader_state(), message)
        assert policy.chat_declined(prepared) is False
        assert policy.read_state(prepared).decision is None
    assert offline["send"] == 0


def test_the_label_does_not_replace_an_existing_backend_decision(offline):
    state = onloader_state()
    state["onloader"]["decision"] = "accept"
    state["onloader"]["status"] = "queued"
    prepared = prepare(state, "Не передавать.")
    assert policy.read_state(prepared).decision == "accept"
    assert policy.read_state(prepared).status == "queued"
    assert policy.chat_declined(prepared) is False
    assert offline["send"] == 0


def test_the_existing_general_no_rule_remains_a_conversational_decline(offline):
    prepared = prepare(onloader_state(), "Нет.")
    assert policy.chat_declined(prepared) is True
    assert policy.read_state(prepared).decision is None
    assert offline["send"] == 0


@pytest.mark.parametrize("message", [
    "Понятно. Но я не хочу передавать.",
    "Понятно, но я не хочу передавать мои данные.",
    "Условия понятны. Я не хочу передавать сведения сейчас. Можно просто поговорить?",
])
def test_an_independent_declarative_transfer_refusal_survives_commit_restore_and_a_faq(message, offline):
    state = onloader_state()
    original_offer = policy.read_state(state)
    prepared = prepare(state, message)
    assert policy.chat_declined(prepared)
    assert policy.read_state(prepared).decision is None
    committed = core.commit_turn(prepared, message, "")["current_state"]
    session = Session(id="synthetic-clause-no", state=committed, anketa=None, usage=SessionUsage("synthetic-clause-no"))
    restored = Session.from_record(session.storage_record()).state
    following = prepare(restored, "Кто получит данные?")
    assert policy.chat_declined(following)
    assert policy.read_state(following).decision is None
    assert policy.read_state(following).presentation_id == original_offer.presentation_id
    assert policy.reply(following) == "Хорошо, можем просто продолжить разговор здесь."
    assert following["plan"]["fields"] == [] and offline["send"] == 0


@pytest.mark.parametrize("message", [
    "Понятно. Но я не хочу передавать?", "Если я не хочу передавать, что будет?",
    "Если всё понятно, но я не хочу передавать.", 'Он написал: "Я не хочу передавать."',
    "Он написал: но я не хочу передавать.", "Мой брат не хочет передавать данные.",
    "Я не хочу передавать деньги.", "Я не хочу передавать файл.",
    "Я не хочу передавать чужие данные.",
])
def test_only_an_own_declarative_refusal_of_the_current_transfer_sets_the_preference(message, offline):
    prepared = prepare(onloader_state(), message)
    assert not policy.chat_declined(prepared)
    assert policy.read_state(prepared).decision is None and offline["send"] == 0


def test_an_independent_clause_does_not_refuse_an_unavailable_or_replaced_offer(offline):
    state = onloader_state()
    state[policy.STATE_KEY]["status"] = "stale"
    prepared = prepare(state, "Понятно. Но я не хочу передавать.")
    assert not policy.chat_declined(prepared)
    assert policy.CHAT_KEY not in prepared
    without_offer = policy.prepare({"_last_user_message": "Понятно. Но я не хочу передавать."})
    assert policy.CHAT_KEY not in without_offer
    state = onloader_state()
    declined = prepare(state, "Понятно. Но я не хочу передавать.")
    declined[policy.STATE_KEY]["presentation_id"] = "different-local-presentation"
    assert not policy.chat_declined(declined)


@pytest.mark.parametrize("message", ["Я не хочу передавать сейчас.", "Я не хочу передавать свой телефон."])
def test_a_supported_direct_refusal_for_a_retained_stale_presentation_stays_conversational(offline, message):
    state = onloader_state()
    state[policy.STATE_KEY]["status"] = "stale"
    prepared = prepare(state, message)
    assert policy.chat_declined(prepared)
    assert policy.read_state(prepared).decision is None and policy.read_state(prepared).status == "stale"
    assert offline["send"] == 0
