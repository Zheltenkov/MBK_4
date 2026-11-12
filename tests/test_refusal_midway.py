"""Отказ посреди анкеты — это уход клиента, а не пропущенный ответ.

Разбор боевых диалогов 02–04.10.2026 (страница /bfl-finish): пятеро сказали
«ничего не надо» и получили следующий вопрос анкеты. «Тогда ничего не нужно» →
«Жаль, но без суммы я не смогу ничего подобрать. Сколько вам нужно?»;
«Ничего не надо» → «Хорошо, если передумаете — я на связи. В каком городе и
регионе живёте?»; а «До свидания» на вопросе о городе разобралось как название
места: «„До свидания“ не похож на название города или области».

Прощание посреди анкеты уже распознаётся (tests/test_farewell_midway.py);
здесь к нему добавлен отказ словами и запрет читать уход как ответ на вопрос.
"""
from __future__ import annotations

import pytest

import core
import prompts
from state import init_state


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *a, **k: {"fact_updates": [], "internal_summary": ""},
    )
    monkeypatch.setenv("MBK_ROUTER_MOCK", "1")
    monkeypatch.delenv("MBK_DADATA_API", raising=False)
    monkeypatch.setenv("MBK_FACT_INTERPRETATION_MODE", "legacy")


@pytest.mark.parametrize(
    "message",
    [
        "Ничего не надо",
        "Ничего не нужно",
        "Тогда ничего не нужно",
        "Извините Не нужно спасибо",
        "Спасибо не нужно",
        "Нет нечего не надо спасибо",
        "Мне уже не надо",
        "Больше не нужно, спасибо",
        "Я передумала пока нечего не буду брать",
        "Я передумал",
    ],
)
def test_a_refusal_is_a_goodbye(message):
    assert core.client_is_saying_goodbye(message) is True


@pytest.mark.parametrize(
    "message",
    [
        # Ответы на вопросы анкеты, а не уход.
        "Нет",
        "Нет, спасибо",
        "Не надо",
        "Не нужно",
        "Ничего нет",
        "Нет, ничего нет",
        "Страховку не надо, спасибо",
        "Залог не нужен",
        "Мне ничего не надо кроме 50 тысяч",
        "Ничего не надо, а какая ставка?",
        "Не хочу",
        "Передумал брать машину в залог, давайте без него",
    ],
)
def test_an_answer_that_only_sounds_like_a_refusal_is_not_a_goodbye(message):
    assert core.client_is_saying_goodbye(message) is False


def _asking(paths: list[str]) -> dict:
    state = init_state()
    state["facts"] = {"client": {"name": "Иван", "age": 40}, "request": {"amount": 300_000}}
    state["script"] = {"asked": list(paths), "attempts": {}}
    state["entry"] = {"scenario": "questionnaire"}
    return state


@pytest.mark.parametrize("message", ["Ничего не надо", "Тогда ничего не нужно"])
def test_the_turn_after_a_refusal_says_goodbye_and_asks_nothing(message):
    prepared = core.prepare_turn(
        _asking(["client.region_class"]), message, submit_delivery=False
    )

    assert core.turn_instruction(prepared) is prompts.FAREWELL_INSTRUCTION

    committed = core.commit_turn(prepared, message, "Хорошо, если передумаете — я на связи.")
    assert committed["messages"] == ["Хорошо, если передумаете — я на связи."]
    attempts = committed["current_state"]["script"].get("attempts") or {}
    assert not attempts.get("client.region_class")


@pytest.mark.parametrize("message", ["До свидания", "Ничего не надо", "Спасибо"])
def test_a_goodbye_is_not_read_as_the_place_of_residence(message):
    """Бой 03.10.2026, сессия 4cc0a9baba54: «До свидания» на вопросе о городе."""
    prepared = core.prepare_turn(
        _asking(["client.region_class"]), message, submit_delivery=False
    )

    client = prepared["facts"]["client"]
    assert "region" not in client and "city" not in client, client
    assert "location" not in client, client
    assert core.turn_instruction(prepared) is prompts.FAREWELL_INSTRUCTION


def test_a_plain_no_is_still_an_answer():
    """«Нет» на закрытый вопрос — ответ: анкета идёт дальше."""
    prepared = core.prepare_turn(
        _asking(["debts.has_active_credits"]), "Нет", submit_delivery=False
    )

    assert core.get_fact(prepared["facts"], "debts.has_active_credits") is False
    assert core.turn_instruction(prepared) is not prompts.FAREWELL_INSTRUCTION


def _after(*client_messages: str) -> dict:
    """Состояние после реплик клиента, на каждую из которых бот попрощался."""
    state = _asking(["client.region_class"])
    for message in client_messages:
        prepared = core.prepare_turn(state, message, submit_delivery=False)
        state = core.commit_turn(prepared, message, "Хорошо, буду на связи.")["current_state"]
    return state


@pytest.mark.parametrize("again", ["Ничего не нужно", "Спасибо", "До свидания"])
def test_the_second_goodbye_in_a_row_gets_no_reply(again):
    """Ролевой прогон 04.10.2026: трижды «ничего не нужно» — трижды дословно
    «Хорошо, буду на связи, если понадоблюсь»."""
    state = _after("Ничего не надо")

    assert core.conversation_is_over(state, again) is True


def test_the_first_goodbye_is_always_answered():
    assert core.conversation_is_over(_after("Мне 40 лет"), "Ничего не надо") is False


@pytest.mark.parametrize("message", ["Хотя нет, давайте продолжим", "А какая ставка?", "Москва"])
def test_a_client_who_comes_back_is_answered(message):
    assert core.conversation_is_over(_after("Ничего не надо"), message) is False
