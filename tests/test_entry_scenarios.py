"""Ветки развилки: что бот говорит и чего не делает."""
from __future__ import annotations

import pytest

import analytics
import core
import entry_routing as er
import prompts
from analytics import AnalyticsRecorder, MemorySink
from analytics import EVENTS as E
from config import AppConfig
from routing import plan as build_plan
from state import init_state


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


@pytest.fixture
def sink():
    memory = MemorySink()
    analytics.set_recorder(AnalyticsRecorder(memory))
    yield memory
    analytics.set_recorder(None)


def _fresh_state() -> dict:
    state = init_state()
    state["session_id"] = "entry-scenario"
    state["chat_history"] = [{"role": "assistant", "content": prompts.OPENING_MESSAGE}]
    return state


def _turn(state, message, reply):
    prepared = core.prepare_turn(state, message, LEGACY_ON, submit_delivery=False)
    return core.commit_turn(prepared, message, reply)


def _instruction_state(matrix, scenario, message="Здравствуйте"):
    return {
        "_plan_object": build_plan(matrix, {}),
        "_last_user_message": message,
        "entry": {"scenario": scenario, "intent": scenario, "source": "model"},
    }


def test_existing_branch_gets_its_own_instruction(matrix):
    state = _instruction_state(matrix, er.EXISTING)

    assert core.turn_instruction(state) == prompts.EXISTING_CLIENT_INSTRUCTION


def test_off_topic_branch_gets_its_own_instruction(matrix):
    state = _instruction_state(matrix, er.OFF_TOPIC)

    assert core.turn_instruction(state) == prompts.OFF_TOPIC_INSTRUCTION


@pytest.mark.parametrize("scenario", [er.EXISTING, er.OFF_TOPIC])
def test_goodbye_in_a_branch_is_still_a_goodbye(matrix, scenario):
    state = _instruction_state(matrix, scenario, message="Спасибо, до свидания")

    assert core.turn_instruction(state) == prompts.FAREWELL_INSTRUCTION


def test_questionnaire_instruction_is_untouched(matrix):
    state = _instruction_state(matrix, er.QUESTIONNAIRE)

    assert core.turn_instruction(state) == prompts.turn_instruction(state["_plan_object"])


def test_branch_instructions_forbid_the_questionnaire_and_promises():
    for text in (prompts.EXISTING_CLIENT_INSTRUCTION, prompts.OFF_TOPIC_INSTRUCTION):
        lowered = text.casefold()
        assert "не спрашивай имя" in lowered
        assert "не начинай подбор" in lowered
    assert "не обещай" in prompts.EXISTING_CLIENT_INSTRUCTION.casefold()


def test_existing_client_is_not_asked_for_name_or_questionnaire(sink):
    committed = _turn(
        _fresh_state(),
        "Оставлял заявку вчера, когда позвонит менеджер?",
        "Отсюда заявку не видно.",
    )

    assert committed["messages"] == ["Отсюда заявку не видно."]
    script = committed["current_state"].get("script") or {}
    assert not script.get("attempts")
    assert not script.get("asked")


def test_existing_branch_is_recorded_instead_of_a_questionnaire_step(sink):
    _turn(
        _fresh_state(),
        "Оставлял заявку вчера, когда позвонит менеджер?",
        "Отсюда заявку не видно.",
    )

    names = [event.event for event in sink.events]
    assert E.TURN_COMPLETED not in names
    assert E.QUALIFICATION_COMPLETED not in names
    (routed,) = [e for e in sink.events if e.event == E.ENTRY_ROUTED]
    assert routed.payload["scenario"] == er.EXISTING
    assert routed.payload["source"] == "dictionary"
    (request,) = [e for e in sink.events if e.event == E.EXISTING_CLIENT_REQUEST]
    assert request.payload["phone_given"] is False


def test_money_first_message_keeps_todays_questionnaire(sink):
    """Флаг включён, человек пришёл за деньгами — всё как без развилки:
    имя спрашивается на первой реплике, шаг анкеты пишется."""
    committed = _turn(_fresh_state(), "Нужны деньги", "Сколько нужно?")

    assert "обращаться" in " ".join(committed["messages"])
    assert E.TURN_COMPLETED in [event.event for event in sink.events]
    (routed,) = [e for e in sink.events if e.event == E.ENTRY_ROUTED]
    assert routed.payload["scenario"] == er.QUESTIONNAIRE


def test_return_from_a_branch_starts_the_questionnaire(sink):
    # Словарь «не наш профиль» не решает никогда, поэтому ветку ставим руками —
    # так, как её поставила бы модель на первой реплике.
    state = _fresh_state()
    state["chat_history"].append(
        {"role": "user", "content": "Как разблокировать карту Сбербанка?"}
    )
    state["chat_history"].append({"role": "assistant", "content": "Это к самому банку."})
    state["entry"] = {"scenario": er.OFF_TOPIC, "intent": "off_topic", "source": "model"}

    committed = _turn(state, "А вообще мне нужен кредит", "Какая сумма нужна?")

    assert committed["current_state"]["entry"]["scenario"] == er.QUESTIONNAIRE
    assert "обращаться" in " ".join(committed["messages"])
    (routed,) = [e for e in sink.events if e.event == E.ENTRY_ROUTED]
    assert routed.payload["scenario"] == er.QUESTIONNAIRE
