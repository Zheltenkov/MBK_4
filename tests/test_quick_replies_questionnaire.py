"""Кнопки вопроса анкеты после настоящего хода бота — и нажатие без модели.

Спека docs/superpowers/specs/2026-09-14-quick-replies-questionnaire-design.md.
"""
from __future__ import annotations

import pytest

import core
import prompts
import quick_replies
from config import AppConfig
from routing import plan as build_plan
from state import init_state
from utils import set_by_path


LEGACY = AppConfig(openrouter_api_key="sk-or-test", fact_interpretation_mode="legacy")
CREDITS = [
    {"label": "Да, есть", "value": "Да, есть"},
    {"label": "Нет", "value": "Нет"},
]


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.delenv("MBK_DADATA_API", raising=False)
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *a, **k: {"fact_updates": [], "internal_summary": ""},
    )


def _state_before_the_credits_question() -> dict:
    state = init_state()
    state["session_id"] = "questionnaire-chips"
    for path, value in {
        "client.name": "Иван",
        "client.display_name": "Иван",
        "request.amount": 500_000,
        "client.age": 40,
        "client.region_class": "rf_other",
        "client.city": "Воронеж",
    }.items():
        set_by_path(state["facts"], path, value)
    state["chat_history"] = [
        {"role": "assistant", "content": prompts.OPENING_MESSAGE},
        {"role": "user", "content": "Нужен кредит"},
        {"role": "assistant", "content": "В каком городе вы живёте?"},
    ]
    state["script"] = {"asked": ["client.region_class"]}
    return state


def _ask_about_credits(matrix) -> dict:
    state = _state_before_the_credits_question()
    assert build_plan(matrix, state["facts"]).field_paths == [
        "debts.has_active_credits"
    ], "предпосылка: следующим бот спрашивает о кредитах"
    prepared = core.prepare_turn(state, "Воронеж", LEGACY, submit_delivery=False)
    committed = core.commit_turn(
        prepared, "Воронеж", "Есть ли сейчас кредиты или займы?"
    )
    return committed["current_state"]


def test_question_about_credits_offers_yes_no_chips(offline, matrix):
    state = _ask_about_credits(matrix)

    assert state["script"]["asked"] == ["debts.has_active_credits"]
    assert quick_replies.quick_replies(state, enabled=True, matrix=matrix) == CREDITS


def test_pressing_a_chip_is_answered_without_the_model(offline, monkeypatch, matrix):
    state = _ask_about_credits(matrix)

    def unexpected(*args, **kwargs):
        raise AssertionError("нажатие кнопки разбирается без модели")

    monkeypatch.setattr(core.llm_agent, "extract_facts", unexpected)
    monkeypatch.setattr(core.llm_agent, "extract_fact_candidates", unexpected)

    answered = core.prepare_turn(state, "Да, есть", LEGACY, submit_delivery=False)

    assert answered["facts"]["debts"]["has_active_credits"] is True
