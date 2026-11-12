"""Разбор возраста на боевых краях: чужая дата, посторонний год, форма.

Аудит перед выкаткой 1.0.5. Разбор ответа про возраст брал первое число
строки, и любое соседнее число молча становилось возрастом клиента: год
трудоустройства, возраст сына, дата покупки квартиры. Ошибка не видна ни
клиенту, ни менеджеру — в CRM просто уезжает не тот человек.

Общее правило, которое держат тесты: своё сильнее чужого, а при
неразрешимой неоднозначности разбор молчит и бот переспрашивает.
"""
from __future__ import annotations

from datetime import date

import pytest

from delivery.crm_mapping import _crm_date, map_lead_intake
from pii_masking import Masker, extract_local_pii_updates
from routing.normalize import parse_age
from state import seed_facts


TODAY = date(2026, 8, 20)


# --------------------------------------------------------------------------- #
# Посторонний год в ответе
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "answer, expected",
    [
        ("мне 46, работаю с 1998 года", 46),
        ("46, с 1998", 46),
        ("мне 27, кредит с 2015", 27),
        ("46 лет, стаж с 2010", 46),
    ],
)
def test_a_stated_age_beats_a_year_that_belongs_to_something_else(answer, expected):
    """«Мне 46, работаю с 1998» — это сорок шесть, а не двадцать восемь."""
    assert parse_age(answer, TODAY) == expected


def test_a_year_alone_is_still_a_year():
    assert parse_age("1985", TODAY) == 41
    assert parse_age("родился в 1985", TODAY) == 41


# --------------------------------------------------------------------------- #
# Чужой человек в ответе
# --------------------------------------------------------------------------- #
def test_the_clients_own_age_wins_next_to_someone_elses():
    assert parse_age("мне 46, а жене 40", TODAY) == 46
    assert parse_age("сыну 12, мне 40", TODAY) == 40
    assert parse_age("у детей 5 и 7, мне 33", TODAY) == 33


@pytest.mark.parametrize(
    "answer",
    [
        "жена родилась 5 мая 1990",
        "сын родился 3 марта 2007",
        "жене 40",
        "маме 70",
    ],
)
def test_someone_elses_age_is_not_taken_at_all(answer):
    """Лучше переспросить, чем записать клиенту возраст его жены."""
    assert parse_age(answer, TODAY) is None


@pytest.mark.parametrize(
    "message",
    [
        "жена родилась 5 мая 1990",
        "сын родился 3 марта 2007",
        "квартиру купил 15 мая 2005",
        "ипотеку оформил 20 июня 2011",
    ],
)
def test_a_date_that_is_not_a_birthday_never_becomes_one(message):
    assert extract_local_pii_updates(message) == []


@pytest.mark.parametrize(
    "message",
    ["27 марта 1985", "мне 27.03.1985", "родился 27 марта 1985", "мой др 27.03.1985"],
)
def test_the_clients_own_birthday_is_still_taken(message):
    paths = {update["path"] for update in extract_local_pii_updates(message)}

    assert paths == {"client.birthdate", "client.age"}


def test_a_date_spelled_out_in_words_is_still_the_clients_own():
    """Проверка «ответ и есть дата» считала слова, а у даты словами слова и есть.

    Собранный стенд 20.08.2026: «двадцать седьмого марта восемьдесят пятого»
    отвергалось как чужая дата, бот отвечал «мне нужны полные годы», и анкета
    доезжала до конца с пустым возрастом — то есть лид уходил без него.
    """
    paths = {
        update["path"]
        for update in extract_local_pii_updates("двадцать седьмого марта восемьдесят пятого")
    }

    assert paths == {"client.birthdate", "client.age"}


def test_the_model_sees_that_the_year_is_a_birth_year():
    """Голый год без пояснения экстрактор не связывает с возрастом."""
    masker = Masker.from_facts({})

    masked = masker.mask_text("двадцать седьмого марта восемьдесят пятого")

    assert masked == "1985 год рождения"


# --------------------------------------------------------------------------- #
# Дата рождения с формы сайта
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw", ["10.05.1985", "1985-05-10", "10 мая 1985", "10/05/1985"]
)
def test_the_form_date_is_normalised_whatever_the_site_sends(raw):
    facts = seed_facts({"birth_date": raw})

    assert facts["client"]["birthdate"] == "1985-05-10"


def test_an_unreadable_form_date_is_dropped_instead_of_stored():
    facts = seed_facts({"birth_date": "не помню"})

    assert "birthdate" not in facts.get("client", {})


def test_a_broken_date_never_blocks_the_lead():
    """Одна необязательная графа не должна отменять заявку целиком.

    До правки «10.05.1985» с формы не проходило проверку payload, и лид не
    уходил в CRM вовсе: клиент прошёл анкету, оставил телефон — и пропал.
    """
    facts = {
        "client": {
            "name": "Тестов Тест Михайлович",
            "phone": "+79850003131",
            "age": 41,
            "birthdate": "не помню",
        },
        "request": {"amount": 1_500_000},
        "debts": {"total": 400_000},
    }

    mapping = map_lead_intake(facts, form_filled_out=True, site_consent_shown=False)

    assert mapping.ready, [issue.message for issue in mapping.issues]
    assert mapping.request is not None
    payload = mapping.request.model_dump(by_alias=True, exclude_none=True)
    assert payload["BIRTHDATE"] == f"01-01-{date.today().year - 41}"


@pytest.mark.parametrize(
    "value, expected",
    [
        ("1985-03-27", "27-03-1985"),
        ("27-03-1985", "27-03-1985"),
        ("10.05.1985", "10-05-1985"),
        ("10 мая 1985", "10-05-1985"),
        ("не помню", None),
        (None, None),
    ],
)
def test_the_crm_date_understands_what_the_rest_of_the_code_understands(value, expected):
    assert _crm_date(value) == expected


# --------------------------------------------------------------------------- #
# Маскирование
# --------------------------------------------------------------------------- #
def test_a_date_of_something_else_is_not_called_a_birthday_for_the_model():
    """«Квартиру купил 2005 год рождения» модель читала как возраст клиента."""
    masker = Masker.from_facts({})

    assert masker.mask_text("квартиру купил 15 мая 2005") == "квартиру купил 2005"
    assert masker.mask_text("кредит оформил 12.03.2019") == "кредит оформил 2019"


def test_a_real_birthday_keeps_its_label():
    masker = Masker.from_facts({})

    assert masker.mask_text("родился 27 марта 1985") == "родился 1985 год рождения"
    assert "15 мая" not in masker.mask_text("родился 15 мая 1985")
