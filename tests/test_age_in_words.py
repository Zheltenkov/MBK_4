"""Возраст и дата рождения, названные живой речью.

Заказчик 20.08.2026: «нужно учитывать, что человек год и дату своего рождения
может писать словами». Ответ на «сколько вам полных лет» приходит каким угодно:
«сорок шесть», «родился в 1985», «27 марта 1985», «двадцать седьмого марта
восемьдесят пятого».

Главный тест здесь — не про слова, а про порядок разбора. Дата рождения должна
разбираться ПЕРЕД голым числом: раньше первым срабатывал поиск `\\b\\d{1,3}\\b`,
и «27 марта 1985» превращалось в двадцать семь лет вместо сорока одного —
молча, с высокой уверенностью и без единого переспроса.
"""
from __future__ import annotations

from datetime import date

import pytest

from delivery.crm_mapping import map_lead_intake
from interpretation.fastpath import parse_expected_answer
from pii_masking import Masker, extract_local_pii_updates
from routing.normalize import normalize_birthdate, parse_age
from routing.ru_dates import parse_birth_year, parse_birthdate


TODAY = date(2026, 8, 20)


# --------------------------------------------------------------------------- #
# Возраст
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "answer, expected",
    [
        ("46", 46),
        ("мне 46", 46),
        ("46 лет", 46),
        ("сорок шесть", 46),
        ("сорок шесть лет", 46),
        ("мне сорок шесть", 46),
        ("двадцать", 20),
        ("тридцать один", 31),
        ("шестьдесят пять", 65),
        ("в сорок шесть", 46),
    ],
)
def test_age_is_understood_in_digits_and_in_words(answer, expected):
    assert parse_age(answer, TODAY) == expected


@pytest.mark.parametrize(
    "answer",
    [
        "27 марта 1985",
        "27.03.1985",
        "27/03/1985",
        "1985-03-27",
        "родился 27 марта 1985 года",
        "двадцать седьмого марта восемьдесят пятого",
        "двадцать седьмого марта тысяча девятьсот восемьдесят пятого года",
    ],
)
def test_a_stated_birthdate_is_not_mistaken_for_the_age(answer):
    """Клиенту сорок один, а не двадцать семь — это и был баг разбора."""
    assert parse_age(answer, TODAY) == 41


@pytest.mark.parametrize(
    "answer, expected",
    [
        ("родился в 1985", 41),
        ("1985", 41),
        ("в восемьдесят пятом", 41),
        ("с девяносто первого", 35),
        ("родился в двухтысячном", 26),
        ("тысяча девятьсот восемьдесят пятого года рождения", 41),
    ],
)
def test_a_birth_year_alone_still_gives_the_age(answer, expected):
    assert parse_age(answer, TODAY) == expected


def test_a_bare_two_digit_number_stays_an_age():
    """«восемьдесят пять» — это восемьдесят пять лет, а не 1985 год.

    Двузначный год распознаётся только с признаком рождения или с предлогом:
    иначе разбор менял бы смысл ответа на противоположный.
    """
    assert parse_age("восемьдесят пять", TODAY) == 85
    assert parse_age("85", TODAY) == 85


@pytest.mark.parametrize(
    "answer", ["не помню", "не скажу", "сто пятьдесят", "давно", ""]
)
def test_an_unparseable_answer_is_left_to_the_model(answer):
    """None — это «переспроси», а не «запиши что-нибудь»."""
    assert parse_age(answer, TODAY) is None


def test_the_fast_path_answers_worded_ages_without_the_model(matrix):
    assert parse_expected_answer("сорок шесть", ["client.age"], matrix) == [
        {"path": "client.age", "value": 46, "confidence": "high"}
    ]


def test_the_fast_path_no_longer_reads_a_date_as_an_age(matrix):
    result = parse_expected_answer("27 марта 1985", ["client.age"], matrix)

    assert result is not None
    assert result[0]["value"] != 27


# --------------------------------------------------------------------------- #
# Дата рождения
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "answer",
    [
        "27 марта 1985",
        "27.03.1985",
        "1985-03-27",
        "родился 27 марта 85 года",
        "двадцать седьмого марта восемьдесят пятого",
    ],
)
def test_a_full_date_is_recognised_however_it_is_written(answer):
    assert parse_birthdate(answer, TODAY) == date(1985, 3, 27)


@pytest.mark.parametrize(
    "answer",
    [
        "родился в 1985",          # года мало для даты
        "31 февраля 1985",         # такого дня не бывает
        "27 марта 2030",           # ещё не наступило
        "27 марта 2020",           # шестилетних клиентов не бывает
        "мне сорок шесть",
        "квартира куплена 15 мая 2019",  # дата есть, но рождением не является
    ],
)
def test_what_is_not_a_plausible_birthdate_gives_nothing(answer):
    assert parse_birthdate(answer, TODAY) is None


def test_a_year_without_a_day_is_not_turned_into_a_date():
    """Первое января ставит маппинг CRM осознанно — здесь это была бы догадка."""
    assert parse_birth_year("родился в 1985", TODAY) == 1985
    assert parse_birthdate("родился в 1985", TODAY) is None


def test_the_spoken_date_reaches_the_facts_as_iso():
    updates = extract_local_pii_updates("родился двадцать седьмого марта восемьдесят пятого")

    assert {"path": "client.birthdate", "value": "1985-03-27", "confidence": "high"} in updates
    assert {"path": "client.age", "value": 41, "confidence": "high"} in updates


def test_normalisation_keeps_only_real_dates():
    assert normalize_birthdate("27 марта 1985", TODAY) == "1985-03-27"
    assert normalize_birthdate("1985-03-27", TODAY) == "1985-03-27"
    assert normalize_birthdate("сорок шесть", TODAY) is None
    assert normalize_birthdate(None, TODAY) is None


# --------------------------------------------------------------------------- #
# Что доезжает до CRM
# --------------------------------------------------------------------------- #
def _payload(**client) -> dict:
    facts = {
        "client": {"name": "Тестов Тест Михайлович", "phone": "+79850003131", **client},
        "request": {"amount": 1_500_000},
        "debts": {"total": 400_000},
    }
    mapping = map_lead_intake(facts, form_filled_out=True, site_consent_shown=False)
    assert mapping.ready, [issue.message for issue in mapping.issues]
    assert mapping.request is not None
    return mapping.request.model_dump(by_alias=True, exclude_none=True)


def test_a_date_named_in_the_chat_beats_the_derived_first_of_january():
    payload = _payload(age=41, birthdate="1985-03-27")

    assert payload["BIRTHDATE"] == "27-03-1985"


def test_without_a_date_the_first_of_january_still_goes():
    payload = _payload(age=41)

    assert payload["BIRTHDATE"] == f"01-01-{date.today().year - 41}"


# --------------------------------------------------------------------------- #
# Персональные данные
# --------------------------------------------------------------------------- #
def test_a_worded_date_does_not_reach_the_foreign_model():
    masker = Masker.from_facts({})

    masked = masker.mask_text("родился двадцать седьмого марта восемьдесят пятого")

    assert "марта" not in masked
    assert "1985 год рождения" in masked


def test_masking_does_not_touch_ordinary_speech():
    masker = Masker.from_facts({})

    text = "мама живёт в Самаре, платил в мае"

    assert masker.mask_text(text) == text
