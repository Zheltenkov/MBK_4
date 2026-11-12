"""Дата рождения выводится из возраста, когда точной даты нет.

Заказчик 20.08.2026, живой прогон: клиент прошёл анкету, назвал «27 лет», лид
создался — а поле BIRTHDATE осталось пустым. В CRM это обязательная графа
карточки клиента, и менеджер получает лид без возраста вовсе.

Точную дату спрашивает только форма на сайте, а их виджет её не передаёт. В
чате бот спрашивает полные годы — этого достаточно, чтобы отдать в CRM
непротиворечивую дату. Решение заказчика: «01 01 делай», то есть первое января
года рождения.

Год считается от сегодняшнего дня: человеку 27 полных лет — значит родился он
не раньше, чем 27 лет назад. Первое января этого года и уходит.
"""
from __future__ import annotations

from datetime import date

import pytest

from delivery.crm_mapping import map_lead_intake


def _facts(**client) -> dict:
    return {
        "client": {"name": "Тестов Тест Михайлович", "phone": "+79850003131", **client},
        "request": {"amount": 1_500_000},
        "debts": {"total": 400_000},
    }


def _payload(facts: dict) -> dict:
    mapping = map_lead_intake(facts, form_filled_out=True, site_consent_shown=False)
    assert mapping.ready, [i.message for i in mapping.issues]
    assert mapping.request is not None
    return mapping.request.model_dump(by_alias=True, exclude_none=True)


def test_age_alone_gives_the_first_of_january():
    payload = _payload(_facts(age=27))

    expected_year = date.today().year - 27
    assert payload["BIRTHDATE"] == f"01-01-{expected_year}"


def test_exact_birthdate_wins_over_the_derived_one():
    """С формы сайта приходит настоящая дата — выдумывать поверх неё нельзя."""
    payload = _payload(_facts(age=40, birthdate="1985-05-10"))

    assert payload["BIRTHDATE"] == "10-05-1985"


def test_without_age_the_field_stays_empty():
    payload = _payload(_facts())

    assert "BIRTHDATE" not in payload


@pytest.mark.parametrize("age", [0, -5, 17, 121, "двадцать"])
def test_implausible_age_is_not_turned_into_a_date(age):
    """Мусор в возрасте не должен превращаться в правдоподобную дату."""
    payload = _payload(_facts(age=age))

    assert "BIRTHDATE" not in payload


def test_derived_date_matches_the_crm_format():
    payload = _payload(_facts(age=35))

    assert len(payload["BIRTHDATE"]) == 10
    assert payload["BIRTHDATE"][:6] == "01-01-"
