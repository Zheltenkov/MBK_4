"""Голое приветствие: бот здоровается в ответ, решение о ветке ждёт сути.

Решение человека 12.09.2026, вариант Б. Каждый седьмой разговор начинается с
одного «Здравствуйте», и настоящий вопрос приходит второй репликой — когда
развилка уже закрыта, а бот успел попросить представиться.
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


OPENING = "Добрый день! Я Ника, специалист МБК. Чем я могу вам помочь?"


def _history(*client_messages: str) -> dict:
    history = [{"role": "assistant", "content": OPENING}]
    for message in client_messages:
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": "…"})
    return {"chat_history": history}


@pytest.mark.parametrize(
    "message",
    [
        "Здравствуйте",
        "Добрый день!",
        "доброе утро",
        "Здравствуйте?",
        "Привет",
        "Доброго времени суток",
        "  Добрый вечер ,  ",
        "алло",
    ],
)
def test_bare_greetings(message):
    assert er.is_bare_greeting(message) is True


def test_greeting_may_name_the_assistant():
    assert er.is_bare_greeting("Добрый день, Ника!", assistant_name="Ника") is True
    assert er.is_bare_greeting("Добрый день, Ника!") is False


@pytest.mark.parametrize(
    "message",
    [
        "",
        "   ",
        "?",
        "День",
        "Ника",
        "Здравствуйте, у меня вопрос",
        "Добрый день, нужен кредит",
        "Здравствуйте 89161234567",
        "Всего доброго",
        "Спасибо",
        "Здравствуйте, оставлял заявку",
    ],
)
def test_anything_with_substance_is_not_a_bare_greeting(message):
    assert er.is_bare_greeting(message) is False


def test_decision_is_open_before_the_first_client_message():
    assert er.entry_decision_open(_history()) is True
    assert er.entry_decision_open({}) is True


def test_decision_stays_open_after_a_bare_greeting():
    assert er.entry_decision_open(_history("Здравствуйте")) is True


def test_decision_closes_after_a_substantive_first_message():
    assert er.entry_decision_open(_history("Нужны деньги")) is False


def test_decision_closes_after_two_client_messages():
    """Не дольше двух реплик: два приветствия подряд — анкета, как сегодня."""
    assert er.entry_decision_open(_history("Здравствуйте", "Добрый день")) is False


def test_only_a_bare_first_greeting_holds_the_decision():
    assert er.holds_for_greeting(_history(), "Здравствуйте") is True
    assert er.holds_for_greeting(_history(), "Здравствуйте, нужен кредит") is False
    assert er.holds_for_greeting(_history("Здравствуйте"), "Здравствуйте") is False
    assert (
        er.holds_for_greeting(_history(), "Привет, Ника", assistant_name="Ника")
        is True
    )


LEGACY = AppConfig(openrouter_api_key="sk-or-test", fact_interpretation_mode="legacy")


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
    state["session_id"] = "entry-greeting"
    state["chat_history"] = [{"role": "assistant", "content": prompts.OPENING_MESSAGE}]
    return state


def _turn(state, message, reply, config=LEGACY):
    prepared = core.prepare_turn(state, message, config, submit_delivery=False)
    return prepared, core.commit_turn(prepared, message, reply)


def test_bare_greeting_gets_a_greeting_back_without_the_questionnaire(offline, sink):
    prepared, committed = _turn(
        _fresh_state(), "Здравствуйте", "Здравствуйте! С чем пришли?"
    )

    assert core.turn_instruction(prepared) == prompts.GREETING_INSTRUCTION
    assert committed["messages"] == ["Здравствуйте! С чем пришли?"]
    state = committed["current_state"]
    assert "entry" not in state
    assert er.GREETING_KEY not in state
    script = state.get("script") or {}
    assert not script.get("attempts")
    assert not script.get("asked")
    names = [event.event for event in sink.events]
    assert E.TURN_COMPLETED not in names
    assert E.ENTRY_ROUTED not in names


def test_bare_greeting_does_not_call_the_extractor(offline, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("на голое приветствие извлекатель не зовут")

    monkeypatch.setattr(core.llm_agent, "extract_facts", unexpected)
    monkeypatch.setattr(core.llm_agent, "extract_fact_candidates", unexpected)

    prepared = core.prepare_turn(
        _fresh_state(),
        "Добрый день!",
        AppConfig(openrouter_api_key="sk-or-test", fact_interpretation_mode="semantic"),
        submit_delivery=False,
    )

    assert prepared[er.GREETING_KEY] is True


def test_question_after_a_greeting_is_routed(offline, sink):
    _p, first = _turn(_fresh_state(), "Здравствуйте", "Здравствуйте! С чем пришли?")

    _p, second = _turn(
        first["current_state"],
        "Оставлял заявку, когда позвонит менеджер?",
        "Отсюда заявку не видно.",
    )

    assert second["current_state"]["entry"]["scenario"] == er.EXISTING
    assert second["messages"] == ["Отсюда заявку не видно."]


def test_money_after_a_greeting_starts_the_questionnaire(offline, sink):
    _p, first = _turn(_fresh_state(), "Здравствуйте", "Здравствуйте! С чем пришли?")

    _p, second = _turn(first["current_state"], "Нужны деньги", "Сколько нужно?")

    assert second["current_state"]["entry"]["scenario"] == er.QUESTIONNAIRE
    assert "обращаться" in " ".join(second["messages"])


def test_two_greetings_in_a_row_start_the_questionnaire(offline, sink):
    _p, first = _turn(_fresh_state(), "Здравствуйте", "Здравствуйте! С чем пришли?")

    prepared, second = _turn(first["current_state"], "Добрый день", "Слушаю вас.")

    assert second["current_state"]["entry"]["scenario"] == er.QUESTIONNAIRE
    assert core.turn_instruction(prepared) != prompts.GREETING_INSTRUCTION


def test_greeting_hold_needs_the_flag(offline):
    off = AppConfig(
        openrouter_api_key="sk-or-test",
        fact_interpretation_mode="legacy",
        entry_routing_enabled=False,
    )

    prepared, committed = _turn(
        _fresh_state(), "Здравствуйте", "Здравствуйте!", config=off
    )

    assert er.GREETING_KEY not in prepared
    assert "обращаться" in " ".join(committed["messages"])


def test_bare_greeting_turn_gets_no_questionnaire_chips(offline):
    _p, committed = _turn(_fresh_state(), "Здравствуйте", "Здравствуйте! С чем пришли?")
    state = committed["current_state"]

    # Решение развилки не принято, а `questionnaire_active` читает это как анкету.
    assert "entry" not in state
    assert quick_replies.quick_replies(state, enabled=True) == []
