"""Нажатие кнопки приветствия пишется в аналитику отдельным событием.

По нему считается, сколько раньше молчавших посетителей начали разговор
кнопкой (спека docs/superpowers/specs/2026-09-14-quick-replies-greeting-design.md).
"""
from __future__ import annotations

import pytest

import analytics
import core
import prompts
import quick_replies
from analytics import AnalyticsRecorder, MemorySink
from analytics import EVENTS as E
from config import AppConfig
from state import init_state


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
    state["session_id"] = "quick-reply-analytics"
    state["chat_history"] = [{"role": "assistant", "content": prompts.OPENING_MESSAGE}]
    return state


def _turn(state, message, reply, config=LEGACY):
    prepared = core.prepare_turn(state, message, config, submit_delivery=False)
    return prepared, core.commit_turn(prepared, message, reply)


def _chip_payloads(sink) -> list[dict]:
    return [event.payload for event in sink.events if event.event == E.QUICK_REPLY_USED]


def test_money_chip_is_recorded(offline, sink):
    _p, committed = _turn(_fresh_state(), "Получить деньги", "Как к вам обращаться?")

    assert _chip_payloads(sink) == [{"choice": "money", "turn": 1}]
    assert quick_replies.CHOICE_KEY not in committed["current_state"]


def test_other_question_chip_is_recorded(offline, sink):
    _turn(_fresh_state(), "Другой вопрос", "Какой у вас вопрос?")

    assert _chip_payloads(sink) == [{"choice": "other", "turn": 1}]


def test_typed_text_is_not_a_chip(offline, sink):
    _turn(_fresh_state(), "Нужны деньги", "Как к вам обращаться?")

    assert _chip_payloads(sink) == []


def test_chip_text_later_in_the_dialog_is_not_counted(offline, sink):
    _p, first = _turn(_fresh_state(), "Здравствуйте", "Здравствуйте! С чем пришли?")

    _turn(first["current_state"], "Другой вопрос", "Слушаю вас.")

    assert _chip_payloads(sink) == []


def test_nothing_is_recorded_with_chips_off(offline, sink):
    off = AppConfig(
        openrouter_api_key="sk-or-test",
        fact_interpretation_mode="legacy",
        quick_replies_enabled=False,
    )

    _turn(_fresh_state(), "Получить деньги", "Как к вам обращаться?", config=off)

    assert _chip_payloads(sink) == []
