"""Просьба об имени не повторяется дословно и не напрашивается на «да».

Просьба зашита константой в backend — осознанно, чтобы модель не переврала
контактный вопрос. Раньше варианта для повтора не было, и повтор выходил
дословным: 25 случаев в базе за три недели, из них четыре — про имя.

Сколько раз и где имя звучит — решает не этот текст, а счётчик попыток и
планировщик. Попытка засчитывается только за прозвучавшую просьбу
(`core._question_is_voiced`), а после MAX_FIELD_ATTEMPTS планировщик имя
больше не спрашивает. Отсюда ровно три просьбы за диалог:

  попытка 1 — ветка знакомства (`action="ask"`), на первой реплике клиента;
  попытки 2 и 3 — контактный шаг (`action="contacts"`), когда телефон уже
  есть, а имени нет. Если номера ещё нет, вторая попытка звучит общей
  просьбой «имя и телефон», и имя в одиночку доходит только до третьей.

Ревью 09.09.2026 нашло, что тесты этой ветки проверяли состояния, которых
рантайм не производит: первая попытка на контактном шаге недостижима, а
вторая и третья получали один и тот же текст с вопросом «Продолжим?» — на
него отвечают «да», и клиент получал тот же пузырь символ в символ. Здесь
номера попыток берутся из того, что делает планировщик; сквозная проверка
настоящими ходами от приветствия — tests/test_contact_request_flow.py.
"""
from __future__ import annotations

import re

import pytest

import core
from routing import Plan, get_matrix, plan
from routing.planner import MAX_FIELD_ATTEMPTS, PlannedField


# Анкета пройдена, телефон есть, имени нет — единственное состояние, в
# котором контактный шаг просит имя в одиночку.
SURVEY_DONE_WITH_PHONE = {
    "client": {"age": 44, "region_class": "moscow_mo", "phone": "+79991234567"},
    "request": {"amount": 500_000},
    "debts": {"has_active_credits": False},
    "employment": {"type": "official", "income": 80_000},
    "assets": {"has_realty": False, "has_vehicle": False},
}

# Слова, с которых начинается открытый вопрос. На такой вопрос нельзя
# ответить «да» — только именем или отказом.
_OPEN_QUESTION_WORDS = ("как", "какой", "какая", "какое", "что", "кто", "где", "сколько")


def _plan_for_name(action: str) -> Plan:
    spec = get_matrix().fields["client.name"]
    return Plan(action=action, fields=[PlannedField(path="client.name", spec=spec)])


def _request(action: str) -> str:
    """Текст хода без реакции модели: остаётся то, что пишет backend.

    Пустая реакция заменяется запасной «Давайте разберёмся.» — она одинакова
    для всех попыток и на сравнение не влияет.
    """
    (bubble,) = core._merge_contact_request(
        [], _plan_for_name(action), user_message="нужен кредит"
    )
    return bubble


def _every_request_that_can_sound() -> dict[int, str]:
    """Попытка → текст, по одному на каждую попытку, которую допускает рантайм."""
    return {1: _request("ask"), 2: _request("contacts")}


def test_the_contact_step_asks_for_the_name_exactly_once():
    """Номера попыток в тестах ниже — те, что производит планировщик.

    Первая попытка всегда уходит в ветку знакомства; на контактный шаг имя
    приходит уже с единицей, и этот ход становится второй попыткой. Третьей
    нет: бой 02.10.2026 — на ней бот говорил «не хотите называть имя — ничего
    страшного», клиент с уже названным телефоном уходил, а заявка ждала его
    следующей реплики и не создавалась.
    """
    matrix = get_matrix()

    assert plan(matrix, {}, attempts={}).field_paths == ["client.name"]
    assert plan(matrix, {}, attempts={"client.name": 1}).action == "ask"
    assert "client.name" not in plan(matrix, {}, attempts={"client.name": 1}).field_paths

    asked_once = plan(matrix, SURVEY_DONE_WITH_PHONE, attempts={"client.name": 1})
    assert (asked_once.action, asked_once.field_paths) == ("contacts", ["client.name"])

    for already_asked in range(2, MAX_FIELD_ATTEMPTS + 1):
        result = plan(
            matrix, SURVEY_DONE_WITH_PHONE, attempts={"client.name": already_asked}
        )
        assert result.action in {"route_found", "no_fit"}, (
            f"после {already_asked} попыток заявка не должна ждать имени"
        )


def test_every_name_request_that_can_sound_is_worded_differently():
    texts = _every_request_that_can_sound()

    assert len(set(texts.values())) == len(texts), (
        "две попытки звучат одинаково:\n"
        + "\n".join(f"{attempt}: {text}" for attempt, text in texts.items())
    )


@pytest.mark.parametrize("attempt", [1, 2])
def test_no_name_request_invites_a_bare_yes(attempt):
    """«Продолжим?» получает «да», а «да» — не имя: клиент снова видит просьбу."""
    text = _every_request_that_can_sound()[attempt]

    questions = [
        sentence
        for sentence in re.split(r"(?<=[.!?…])\s+", text)
        if sentence.endswith("?")
    ]
    assert questions, f"просьба об имени должна быть вопросом: {text}"
    for question in questions:
        words = re.findall(r"[^\W\d_]+", question.lower())
        assert any(word in _OPEN_QUESTION_WORDS for word in words), (
            f"вопрос с ответом «да/нет»: {question}"
        )


def test_the_first_request_is_the_familiar_one():
    assert "как к вам обращаться" in _request("ask").lower()


def test_planner_never_asks_for_the_name_alone_outside_contacts():
    """Страховка на связь между планировщиком и текстом контактного запроса.

    Варианты повтора живут только на контактном шаге, потому что только там
    повтор реально возможен. Если планировщик снова начнёт спрашивать имя соло
    вне контактов после первой неудачной попытки, ветка знакомства в `core.py`
    будет повторять свой единственный текст дословно.
    """
    result = plan(get_matrix(), {}, attempts={"client.name": 1})

    assert not (result.action == "ask" and result.field_paths == ["client.name"])
