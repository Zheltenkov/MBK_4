"""Попытка не сгорает, если клиент ответом исправил другое поле.

Прод 30.09.2026, лид 3508342. Бот трижды спросил срок просрочки и ни разу не
получил ответа: «Оператор», «1300» (сумма долга), «1300000» (поправка суммы
долга). Третий ответ — не отказ отвечать: человек чинил предыдущий ответ. Но
счётчик дошёл до потолка, вопрос о просрочке выпал из анкеты, и CRM осталась
без кредитной истории. Ответ, который поменял другой факт, попыткой
спрошенного поля не считается.
"""
from __future__ import annotations

import pytest

import core
import prompts
from config import AppConfig
from state import init_state
from utils import set_by_path


LEGACY = AppConfig(openrouter_api_key="sk-or-test", fact_interpretation_mode="legacy")


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.delenv("MBK_DADATA_API", raising=False)


def _extractor(updates: list[dict]):
    def _stub(*args, **kwargs):
        return {"fact_updates": updates, "internal_summary": ""}

    return _stub


def _state(attempts: int) -> dict:
    state = init_state()
    state["session_id"] = "attempt-refund-test"
    state["chat_history"] = [
        {"role": "assistant", "content": prompts.OPENING_MESSAGE},
        {"role": "user", "content": "Нужны деньги в долг"},
        {"role": "assistant", "content": "…"},
    ]
    for path, value in [
        ("client.name", "Наталья"),
        ("request.amount", 1_300_000),
        ("client.age", 52),
        ("client.region_class", "moscow_mo"),
        ("debts.has_active_credits", True),
        ("debts.total", 1300),
    ]:
        set_by_path(state["facts"], path, value)
    state["script"] = {
        "asked": ["debts.overdue_duration"],
        "attempts": {"debts.overdue_duration": attempts},
    }
    return state


def test_correction_of_another_field_gives_the_attempt_back(monkeypatch, offline):
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        _extractor([{"path": "debts.total", "value": "1300000", "confidence": "high"}]),
    )

    prepared = core.prepare_turn(_state(3), "1300000", LEGACY, submit_delivery=False)

    assert prepared["facts"]["debts"]["total"] == 1_300_000
    assert prepared["script"]["attempts"]["debts.overdue_duration"] == 2
    assert "debts.overdue_duration" in prepared["plan"].get("fields", []) or (
        "debts.overdue_duration" in prepared["_plan_object"].field_paths
    ), "вопрос о просрочке должен прозвучать ещё раз"


def test_answer_about_nothing_still_counts(monkeypatch, offline):
    """«Оператор» — не поправка: попытка засчитана, иначе вопрос не кончится."""
    monkeypatch.setattr(core.llm_agent, "extract_facts", _extractor([]))

    prepared = core.prepare_turn(_state(1), "Оператор", LEGACY, submit_delivery=False)

    assert prepared["script"]["attempts"]["debts.overdue_duration"] == 1


def test_answer_to_the_asked_field_changes_nothing(monkeypatch, offline):
    monkeypatch.setattr(core.llm_agent, "extract_facts", _extractor([]))

    prepared = core.prepare_turn(_state(1), "Просрочек нет", LEGACY, submit_delivery=False)

    assert prepared["facts"]["debts"]["overdue_duration"] == "no_overdue"
    assert prepared["script"]["attempts"]["debts.overdue_duration"] == 1
