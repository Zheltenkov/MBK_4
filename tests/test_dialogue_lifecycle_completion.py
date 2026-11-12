"""Closed chat acknowledgements stay silent while meaningful input remains usable."""
from __future__ import annotations

from copy import deepcopy
import json
import socket

import pytest

import core
import debt_policy
from onloader import policy as onloader_policy
from tests.test_policy_voice import onloader_state


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Lifecycle tests do not invoke models or external services")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(core.llm_agent, "generate_reply", forbidden)
    monkeypatch.setattr(core.llm_agent, "_urlopen", forbidden)
    monkeypatch.setattr(core.delivery, "submit", forbidden)


def completed_tail():
    state = onloader_state()
    state["dialog_phase"] = "onloader"
    state["chat_history"] = [
        {"role": "user", "content": "Понял, спасибо."},
        {"role": "assistant", "content": "Если появятся вопросы, напишите."},
        {"role": "user", "content": "Спасибо."},
    ]
    return state


@pytest.mark.parametrize("message", ["Да.", "Ага."])
def test_an_affirmative_answer_to_the_bots_open_question_continues_the_same_chat(message):
    state = completed_tail()
    state["chat_history"][-1] = {
        "role": "assistant", "content": "Хотите разобраться, какие способы решения долгов бывают?"}
    before = deepcopy(state)
    assert core.conversation_is_over(state, message) is False
    assert state == before
    assert onloader_policy.read_state(state).decision is None
    assert core.conversation_is_over(state, "Спасибо.") is True
    state["chat_history"].append({"role": "user", "content": "Спасибо."})
    assert core.conversation_is_over(state, message) is True


@pytest.mark.parametrize("message", ["Ага.", "Угу", "Ок", "Хорошо", "Спасибо.", "👍", "Да."])
def test_onloader_ack_after_a_farewell_does_not_resume_persuasion(message):
    state = completed_tail()
    before = deepcopy(state)
    assert core.conversation_is_over(state, message) is True
    assert state == before
    assert onloader_policy.read_state(state).decision is None


def test_silent_ack_tail_remains_closed_across_record_serialization():
    state = json.loads(json.dumps(completed_tail()))
    for message in ["Ага.", "Спасибо", "Угу", "Ок", "👍"]:
        assert core.conversation_is_over(state, message) is True
        # Match main._remember_unanswered and local_style_eval's actual silent turn.
        state["chat_history"].append({"role": "user", "content": message})
        state = json.loads(json.dumps(state))


@pytest.mark.parametrize("message", [
    "А кто увидит данные?", "Можно без передачи", "Я передумал, хочу продолжить.",
    "Мой новый номер +7 000 000-00-01", "Долг 400000 рублей.", "Москва",
    "Спасибо, а какие документы нужны?", "Да, разрешаю передать данные.",
])
def test_a_completed_tail_does_not_block_faq_new_facts_or_a_new_explicit_request(message):
    assert core.conversation_is_over(completed_tail(), message) is False


def test_a_substantive_turn_breaks_the_old_farewell_tail_for_a_later_pending_answer():
    state = completed_tail()
    state["chat_history"].extend([
        {"role": "user", "content": "А можно продолжить по этому предложению?"},
        {"role": "assistant", "content": "Объяснить условия предложения?"},
    ])
    assert core.conversation_is_over(state, "Да.") is False


@pytest.mark.parametrize("message", ["Да", "Ага", "Ок"])
def test_an_active_onloader_offer_without_a_farewell_keeps_its_normal_answer_surface(message):
    state = onloader_state()
    state["dialog_phase"] = "onloader"
    state["chat_history"] = [{"role": "assistant", "content": "Объяснить предложение?"}]
    assert core.conversation_is_over(state, message) is False


def test_a_conversational_onloader_decline_quiets_ack_without_recording_backend_consent():
    state = onloader_state()
    state["dialog_phase"] = "onloader"
    state["onloader_dialogue"] = {"presentation_id": state["onloader"]["presentation_id"], "declined": True}
    assert core.conversation_is_over(state, "Ага.") is True
    assert core.conversation_is_over(state, "А можно всё-таки продолжить?") is False
    assert onloader_policy.read_state(state).decision is None


@pytest.mark.parametrize("status,decision", [("queued", "accept"), ("declined", "decline"), ("rejected", "accept")])
def test_native_onloader_terminal_status_suppresses_only_empty_acknowledgements(status, decision):
    state = onloader_state()
    state["onloader"]["status"] = status
    state["onloader"]["decision"] = decision
    state["dialog_phase"] = "onloader"
    assert core.conversation_is_over(state, "Ага.") is True
    assert core.conversation_is_over(state, "А почему такой результат?") is False
    assert state["onloader"]["decision"] == decision


@pytest.mark.parametrize("status", ["pending", "signal_pending", "outcome_unknown", "stale", "forbidden", "unavailable"])
def test_unresolved_onloader_status_without_a_farewell_is_not_an_invented_completion(status):
    state = onloader_state()
    state["onloader"]["status"] = status
    state["dialog_phase"] = "onloader"
    assert core.conversation_is_over(state, "Ага.") is False


@pytest.mark.parametrize("phase", ["collected_held", "contact_declined_held", "incomplete_held", "below_local_floor_held", "cancelled_held"])
def test_completed_debt_task_suppresses_ack_but_keeps_followup_questions(phase):
    state = {"questionnaire_policy": debt_policy.DebtPolicyState().as_state(), "dialog_phase": phase, "chat_history": []}
    assert core.conversation_is_over(state, "Ага.") is True
    assert core.conversation_is_over(state, "А какой следующий шаг?") is False


def test_debt_intent_confirmation_remains_answerable():
    state = {"questionnaire_policy": debt_policy.DebtPolicyState(mode="clarify").as_state(),
             "dialog_phase": "clarifying_intent", "chat_history": []}
    assert core.conversation_is_over(state, "Да.") is False


@pytest.mark.parametrize("farewell", ["До свидания!", "До связи.", "Хорошего дня!"])
def test_a_clear_assistant_farewell_can_close_the_tail_without_changing_the_business_state(farewell):
    state = onloader_state()
    state["dialog_phase"] = "onloader"
    state["chat_history"] = [{"role": "user", "content": "Ок"}, {"role": "assistant", "content": farewell}]
    assert core.conversation_is_over(state, "Ага.") is True
    assert onloader_policy.read_state(state).decision is None


def test_an_assistant_thank_you_alone_does_not_close_an_active_questionnaire():
    state = {"dialog_phase": "qualification", "chat_history": [
        {"role": "user", "content": "Меня зовут Ирина."},
        {"role": "assistant", "content": "Спасибо."},
    ]}
    assert core.conversation_is_over(state, "Ага") is False
