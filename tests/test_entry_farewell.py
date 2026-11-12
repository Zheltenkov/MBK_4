"""После прощания в ветке развилки бот молчит на пустое «спасибо».

Ролевой прогон 11.09.2026: в ветках клиент четыре раза подряд писал
«Спасибо», и бот четыре раза прощался заново — дословным повтором. После
финала анкеты это решено давно (conversation_is_over, 19.08.2026); у веток
конечной фазы нет, и правило их не видело.
"""
from __future__ import annotations

import pytest

import core
import entry_routing as er
import main
import prompts
from config import AppConfig
from evaluation.roleplay import _is_finished
from session_store import Session
from state import init_state
from usage_tracker import SessionUsage


LEGACY_ON = AppConfig(
    openrouter_api_key="sk-or-test",
    fact_interpretation_mode="legacy",
    entry_routing_enabled=True,
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("MBK_DADATA_API", raising=False)
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *a, **k: {"fact_updates": [], "internal_summary": ""},
    )


def _branch_state(scenario: str) -> dict:
    state = init_state()
    state["session_id"] = "entry-farewell"
    state["chat_history"] = [
        {"role": "assistant", "content": prompts.OPENING_MESSAGE},
        {"role": "user", "content": "По заявке хочу узнать, когда перезвонят."},
        {"role": "assistant", "content": "Статус заявки отсюда не видно."},
    ]
    state["entry"] = {"scenario": scenario, "intent": scenario, "source": "model"}
    return state


def _turn(state, message, reply):
    prepared = core.prepare_turn(state, message, LEGACY_ON, submit_delivery=False)
    return core.commit_turn(prepared, message, reply)["current_state"]


def test_close_branch_marks_only_branches():
    for scenario in (er.EXISTING, er.OFF_TOPIC):
        state = {"entry": {"scenario": scenario}}
        er.close_branch(state)
        assert er.branch_closed(state) is True

    questionnaire = {"entry": {"scenario": er.QUESTIONNAIRE}}
    er.close_branch(questionnaire)
    assert er.branch_closed(questionnaire) is False
    assert "closed" not in questionnaire["entry"]

    empty: dict = {}
    er.close_branch(empty)
    assert er.branch_closed(empty) is False


@pytest.mark.parametrize("scenario", [er.EXISTING, er.OFF_TOPIC])
def test_goodbye_in_a_branch_closes_the_conversation(scenario):
    state = _turn(_branch_state(scenario), "Понял, спасибо.", "Всего доброго!")

    assert er.branch_closed(state) is True
    assert core.conversation_is_over(state, "Спасибо") is True
    assert core.conversation_is_over(state, "ага") is True


def test_a_real_question_after_goodbye_still_gets_an_answer():
    state = _turn(_branch_state(er.EXISTING), "Понял, спасибо.", "Всего доброго!")

    assert core.conversation_is_over(state, "А кредит у вас можно взять?") is False


def test_a_new_turn_after_goodbye_reopens_the_branch():
    """Вопрос прошёл обычный ход — решение переписано, признак сброшен."""
    state = _turn(_branch_state(er.EXISTING), "Понял, спасибо.", "Всего доброго!")

    state = _turn(state, "а когда вообще перезвонят?", "Отсюда не видно.")

    assert er.branch_closed(state) is False


def test_branch_without_goodbye_is_not_closed():
    state = _turn(_branch_state(er.OFF_TOPIC), "а почему?", "Это к самому банку.")

    assert er.branch_closed(state) is False
    assert core.conversation_is_over(state, "Спасибо") is False


def test_goodbye_in_the_questionnaire_does_not_use_the_branch_rule():
    state = init_state()
    state["session_id"] = "entry-farewell"
    state["chat_history"] = [
        {"role": "assistant", "content": prompts.OPENING_MESSAGE},
        {"role": "user", "content": "Нужны деньги"},
        {"role": "assistant", "content": "Какая сумма нужна?"},
    ]

    state = _turn(state, "Спасибо, до свидания", "Всего доброго!")

    # Ветка не закрывается: анкета продолжится, как только клиент вернётся с
    # делом. Молчим только на второй уход подряд — на первый бот уже ответил
    # (ролевой прогон 04.10.2026: три одинаковых прощания подряд).
    assert er.branch_closed(state) is False
    assert core.conversation_is_over(state, "500 тысяч") is False
    assert core.conversation_is_over(state, "А какая ставка?") is False
    assert core.conversation_is_over(state, "Спасибо") is True


def test_public_state_shows_the_scenario():
    session = Session(
        id="entry-farewell",
        state={**init_state(), "entry": {"scenario": er.EXISTING, "closed": True}},
        anketa=None,
        usage=SessionUsage(session_id="entry-farewell"),
    )

    assert main._public_state(session)["entry"] == {
        "scenario": er.EXISTING,
        "closed": True,
    }


def test_roleplay_sees_a_closed_branch_as_finished():
    assert _is_finished({"entry": {"scenario": er.EXISTING, "closed": True}}) is True
    assert _is_finished({"entry": {"scenario": er.EXISTING}}) is False
