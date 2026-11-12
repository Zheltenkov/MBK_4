"""Словарь-страховка развилки: ловит только очевидное и никого не уводит зря."""
from __future__ import annotations

import pytest

from routing.entry_intent import EntryIntent, classify_by_dictionary


@pytest.mark.parametrize(
    "message",
    [
        "Здравствуйте, я оставлял заявку, когда позвонят?",
        "Добрый день. Заявку вчера оставила, что по ней?",
        "Что по моей заявке?",
        "Подскажите статус заявки",
        "Когда ожидать звонка менеджера?",
        "Никто не перезвонил",
        # existing важнее money: заявка уже есть, это не новое обращение
        "Оставлял заявку на кредит 500 тысяч",
    ],
)
def test_obvious_existing_requests(message):
    assert classify_by_dictionary(message) is EntryIntent.EXISTING


@pytest.mark.parametrize(
    "message",
    [
        "Нужны деньги",
        "Хочу взять кредит",
        # «подать», «оставить» — будущая заявка, а не поданная
        "Хочу подать заявку на займ",
        "Хочу оставить заявку на кредит",
        "Помогите с долгами, приставы списывают",
        "нужно 300 тыс",
        "Можно ли рефинансировать ипотеку?",
        # Обе раскладки: русская к и латинская k
        "нужно 300к",
        "нужно 300k",
    ],
)
def test_money_requests_stay_money(message):
    assert classify_by_dictionary(message) is EntryIntent.MONEY


@pytest.mark.parametrize(
    "message",
    [
        "Здравствуйте",
        "Добрый вечер!",
        "?",
        "",
        "   ",
        # не наш профиль словарь не решает никогда: fail-open
        "Как разблокировать карту Сбербанка?",
        # «долго» — не «долг»
        "Долго ещё?",
    ],
)
def test_everything_else_is_unclear(message):
    assert classify_by_dictionary(message) is EntryIntent.UNCLEAR


def test_intent_values_are_the_contract_with_the_model():
    assert [intent.value for intent in EntryIntent] == [
        "money",
        "existing",
        "off_topic",
        "unclear",
    ]
