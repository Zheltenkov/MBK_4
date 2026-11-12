"""Кнопка «Другой вопрос»: бот просит рассказать вопрос, решение о ветке ждёт.

Спека docs/superpowers/specs/2026-09-14-quick-replies-greeting-design.md.
Человек нажал кнопку под приветствием, но вопроса ещё не задал: начать анкету
значило бы его не услышать, а решать ветку пока не по чему.
"""
from __future__ import annotations

import pytest

import analytics
import core
import entry_routing as er
import prompts
import quick_replies
from analytics import AnalyticsRecorder, MemorySink
from analytics import EVENTS as E
from config import AppConfig
from state import init_state


LEGACY = AppConfig(openrouter_api_key="sk-or-test", fact_interpretation_mode="legacy")


def _history(*client_messages: str) -> dict:
    history = [{"role": "assistant", "content": prompts.OPENING_MESSAGE}]
    for message in client_messages:
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": "…"})
    return {"chat_history": history}


def test_other_question_holds_only_as_the_first_message():
    assert er.holds_for_other_question(_history(), "Другой вопрос") is True
    assert er.holds_for_other_question(_history(), "другой вопрос!") is True
    assert er.holds_for_other_question(_history("Здравствуйте"), "Другой вопрос") is False
    assert er.holds_for_other_question(_history(), "Получить деньги") is False


def test_decision_stays_open_after_the_chip_only_with_chips_on():
    assert er.entry_decision_open(_history("Другой вопрос"), chips_enabled=True) is True
    assert er.entry_decision_open(_history("Другой вопрос"), chips_enabled=False) is False
    assert er.entry_decision_open(_history("Другой вопрос")) is False


def test_decision_closes_after_two_client_messages_even_after_the_chip():
    state = _history("Другой вопрос", "Какой курс доллара?")

    assert er.entry_decision_open(state, chips_enabled=True) is False


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.delenv("MBK_DADATA_API", raising=False)
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *a, **k: {"fact_updates": [], "internal_summary": ""},
    )


@pytest.fixture
def sink():
    memory = MemorySink()
    analytics.set_recorder(AnalyticsRecorder(memory))
    yield memory
    analytics.set_recorder(None)


def _fresh_state() -> dict:
    state = init_state()
    state["session_id"] = "entry-other-question"
    state["chat_history"] = [{"role": "assistant", "content": prompts.OPENING_MESSAGE}]
    return state


def _turn(state, message, reply, config=LEGACY):
    prepared = core.prepare_turn(state, message, config, submit_delivery=False)
    return prepared, core.commit_turn(prepared, message, reply)


def test_chip_gets_a_request_for_the_question_without_the_questionnaire(offline, sink):
    prepared, committed = _turn(
        _fresh_state(), "Другой вопрос", "Расскажите, какой у вас вопрос?"
    )

    assert core.turn_instruction(prepared) == prompts.OTHER_QUESTION_INSTRUCTION
    assert committed["messages"] == ["Расскажите, какой у вас вопрос?"]
    state = committed["current_state"]
    assert "entry" not in state
    assert er.OTHER_QUESTION_KEY not in state
    script = state.get("script") or {}
    assert not script.get("attempts")
    assert not script.get("asked")
    names = [event.event for event in sink.events]
    assert E.TURN_COMPLETED not in names
    assert E.ENTRY_ROUTED not in names


def test_chip_does_not_call_the_extractor(offline, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("на «Другой вопрос» извлекатель не зовут")

    monkeypatch.setattr(core.llm_agent, "extract_facts", unexpected)
    monkeypatch.setattr(core.llm_agent, "extract_fact_candidates", unexpected)

    prepared = core.prepare_turn(
        _fresh_state(),
        "Другой вопрос",
        AppConfig(openrouter_api_key="sk-or-test", fact_interpretation_mode="semantic"),
        submit_delivery=False,
    )

    assert prepared[er.OTHER_QUESTION_KEY] is True


def test_question_after_the_chip_is_routed(offline, sink):
    _p, first = _turn(_fresh_state(), "Другой вопрос", "Какой у вас вопрос?")

    _p, second = _turn(
        first["current_state"],
        "Оставлял заявку, когда позвонит менеджер?",
        "Отсюда заявку не видно.",
    )

    assert second["current_state"]["entry"]["scenario"] == er.EXISTING
    assert second["messages"] == ["Отсюда заявку не видно."]


def test_money_after_the_chip_starts_the_questionnaire(offline, sink):
    _p, first = _turn(_fresh_state(), "Другой вопрос", "Какой у вас вопрос?")

    _p, second = _turn(first["current_state"], "Нужны деньги", "Сколько нужно?")

    assert second["current_state"]["entry"]["scenario"] == er.QUESTIONNAIRE
    assert "обращаться" in " ".join(second["messages"])


def test_chip_hold_needs_quick_replies(offline):
    off = AppConfig(
        openrouter_api_key="sk-or-test",
        fact_interpretation_mode="legacy",
        quick_replies_enabled=False,
    )

    prepared, committed = _turn(_fresh_state(), "Другой вопрос", "Слушаю вас.", config=off)

    assert er.OTHER_QUESTION_KEY not in prepared
    assert committed["current_state"]["entry"]["scenario"] == er.QUESTIONNAIRE


def test_chip_hold_needs_entry_routing(offline):
    off = AppConfig(
        openrouter_api_key="sk-or-test",
        fact_interpretation_mode="legacy",
        entry_routing_enabled=False,
    )

    prepared, _committed = _turn(_fresh_state(), "Другой вопрос", "Слушаю вас.", config=off)

    assert er.OTHER_QUESTION_KEY not in prepared


def test_chip_turn_gets_no_questionnaire_chips(offline):
    _p, committed = _turn(
        _fresh_state(), "Другой вопрос", "Расскажите, какой у вас вопрос?"
    )
    state = committed["current_state"]

    # Решение развилки не принято, а `questionnaire_active` читает это как анкету.
    assert "entry" not in state
    assert quick_replies.quick_replies(state, enabled=True) == []


# --------------------------------------------------------------------------- #
# Неясная реплика после кнопки «Другой вопрос»
# --------------------------------------------------------------------------- #
# Стенд 25.09.2026: «Другой вопрос» → «Библиотека?» → бот попросил имя и начал
# анкету. Обе модели извлекателя отвечают на голое «Библиотека?» unclear, а
# unclear на первой содержательной реплике уходит в анкету — чтобы не терять
# денежных клиентов. Но после этой кнопки человек сам сказал, что пришёл не за
# деньгами: для денег есть своя кнопка.


def test_unclear_after_the_chip_is_not_our_profile():
    scenario = er.next_scenario(
        None, er.EntryIntent.UNCLEAR, first_message=True, after_other_question=True
    )

    assert scenario == er.OFF_TOPIC


def test_unclear_without_the_chip_still_goes_to_the_questionnaire():
    scenario = er.next_scenario(None, er.EntryIntent.UNCLEAR, first_message=True)

    assert scenario == er.QUESTIONNAIRE


def test_money_after_the_chip_still_goes_to_the_questionnaire():
    scenario = er.next_scenario(
        None, er.EntryIntent.MONEY, first_message=True, after_other_question=True
    )

    assert scenario == er.QUESTIONNAIRE


def test_unclear_with_money_in_the_same_reply_is_not_sent_away():
    """unclear из противоречия «не наш профиль, но про деньги» — это сомнение."""
    state = _history("Другой вопрос")

    er.route_turn(
        state,
        user_message="Библиотека, а ещё займ нужен",
        model_intent=er.EntryIntent.UNCLEAR,
        first_message=True,
        money_signal=True,
    )

    assert state["entry"]["scenario"] == er.QUESTIONNAIRE


def test_bare_off_topic_word_after_the_chip_gets_the_branch(offline, sink):
    _p, first = _turn(_fresh_state(), "Другой вопрос", "Расскажите, что вас интересует?")

    _p, second = _turn(first["current_state"], "Библиотека?", "Это не ко мне.")

    assert second["current_state"]["entry"]["scenario"] == er.OFF_TOPIC
