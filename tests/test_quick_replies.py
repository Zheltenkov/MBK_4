"""Чипсы приветствия: кнопки вычисляются из разговора, нажатие узнаётся по тексту.

Спека docs/superpowers/specs/2026-09-14-quick-replies-greeting-design.md.
"""
from __future__ import annotations

import pytest

import prompts
import quick_replies as qr


GREETING = [
    {"label": "Получить деньги", "value": "Получить деньги"},
    {"label": "Разобраться с долгами", "value": "Разобраться с долгами"},
    {"label": "Я уже оставлял заявку", "value": "Я уже оставлял заявку"},
    {"label": "Другой вопрос", "value": "Другой вопрос"},
]


def _state(*client_messages: str) -> dict:
    history = [{"role": "assistant", "content": prompts.OPENING_MESSAGE}]
    for message in client_messages:
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": "…"})
    return {"chat_history": history}


def test_greeting_offers_two_chips():
    """Historical test ID retained; the approved greeting now has four choices."""
    assert qr.quick_replies(_state(), enabled=True) == GREETING


def test_state_before_the_greeting_offers_the_same_chips():
    """POST /session отдаёт состояние ещё до приветствия — кнопки те же."""
    assert qr.quick_replies({}, enabled=True) == GREETING


def test_chips_disappear_after_the_first_client_message():
    assert qr.quick_replies(_state("Нужны деньги"), enabled=True) == []


def test_disabled_chips_are_empty():
    assert qr.quick_replies(_state(), enabled=False) == []


def test_client_gets_only_label_and_value():
    for chip in qr.quick_replies(_state(), enabled=True):
        assert set(chip) == {"label", "value"}


def test_existing_client_chip_is_not_offered_yet():
    """Historical test ID retained; the new four-choice decision supersedes the hold."""
    assert qr.GREETING_CHIPS[2] == {
        "label": "Я уже оставлял заявку",
        "value": "Я уже оставлял заявку",
        "choice": "existing",
    }


@pytest.mark.parametrize(
    "message, choice",
    [
        ("Получить деньги", "money"),
        ("получить деньги", "money"),
        ("  Получить   деньги! ", "money"),
        ("Другой вопрос", "other"),
        ("другой вопрос?", "other"),
        ("«Другой вопрос»", "other"),
    ],
)
def test_chip_press_is_recognized(message, choice):
    assert qr.chip_choice(message) == choice


@pytest.mark.parametrize(
    "message",
    [
        "",
        "   ",
        "деньги",
        "Получить деньги срочно",
        "У меня другой вопрос по карте",
        "вопрос",
    ],
)
def test_other_text_is_not_a_chip_press(message):
    assert qr.chip_choice(message) is None


def test_press_counts_only_as_the_first_client_message():
    assert qr.first_message_choice(_state(), "Другой вопрос") == "other"
    assert qr.first_message_choice(_state("Здравствуйте"), "Другой вопрос") is None


CREDITS = [
    {"label": "Да, есть", "value": "Да, есть"},
    {"label": "Нет", "value": "Нет"},
]


def _questionnaire_state(
    asked,
    *,
    client_messages=("Иван",),
    answer_in_flight=False,
    entry=None,
) -> dict:
    history = [{"role": "assistant", "content": prompts.OPENING_MESSAGE}]
    for message in client_messages:
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": "Есть ли сейчас кредиты?"})
    if answer_in_flight:
        history.append({"role": "user", "content": "Да"})
    state = {"chat_history": history, "script": {"asked": list(asked)}}
    if entry is not None:
        state["entry"] = entry
    return state


def test_single_closed_question_offers_its_chips(matrix):
    state = _questionnaire_state(["debts.has_active_credits"])

    assert qr.quick_replies(state, enabled=True, matrix=matrix) == CREDITS


def test_two_questions_at_once_get_no_chips(matrix):
    state = _questionnaire_state(["request.amount", "client.age"])

    assert qr.quick_replies(state, enabled=True, matrix=matrix) == []


def test_question_without_chips_gets_none(matrix):
    state = _questionnaire_state(["client.region_class"])

    assert qr.quick_replies(state, enabled=True, matrix=matrix) == []


def test_no_chips_while_the_answer_is_on_its_way(matrix):
    state = _questionnaire_state(["debts.has_active_credits"], answer_in_flight=True)

    assert qr.quick_replies(state, enabled=True, matrix=matrix) == []


def test_entry_branches_get_no_questionnaire_chips(matrix):
    state = _questionnaire_state(
        ["debts.has_active_credits"],
        entry={"scenario": "off_topic", "intent": "off_topic", "source": "model"},
    )

    assert qr.quick_replies(state, enabled=True, matrix=matrix) == []


def test_farewell_gets_no_chips_of_the_previous_question(matrix):
    """На прощании script.asked не обновляется — кнопки прошлого вопроса не показываем."""
    import core

    farewell = "Спасибо, всего доброго"
    assert core.client_is_saying_goodbye(farewell), "предпосылка: это прощание"
    state = _questionnaire_state(
        ["debts.has_active_credits"], client_messages=("Иван", farewell)
    )

    assert qr.quick_replies(state, enabled=True, matrix=matrix) == []


def test_questionnaire_chips_obey_the_flag(matrix):
    state = _questionnaire_state(["debts.has_active_credits"])

    assert qr.quick_replies(state, enabled=False, matrix=matrix) == []
