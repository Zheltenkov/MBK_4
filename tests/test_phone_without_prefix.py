"""Номер, написанный без «8» и «+7».

Живая база заказчика, 21.08.2026: клиент написал номер десятью цифрами. Шаблон
требовал префикс, и номер не распознался нигде сразу:

* в факты не лёг — бот переспросил номер, который уже был на экране;
* обезличивание не сработало — номер ушёл в зарубежную модель как есть;
* в переписке он остался открытым текстом, хотя заказчик просил хранить
  только отпечаток.

Одна дыра в шаблоне дала три разных отказа, и ни один не виден в логах.
"""
from __future__ import annotations

import pytest

from analytics.transcripts import hash_phones, phone_fingerprint
from pii_masking import Masker, extract_local_pii_updates
from routing.normalize import parse_phone


WRITINGS = [
    "+79501234505",
    "89501234505",
    "8 950 123 45 05",
    "7 950 123 45 05",
    "9501234505",
    "950 123 45 05",
    "950-123-45-05",
    "+7 (950) 123-45-05",
]


@pytest.mark.parametrize("written", WRITINGS)
def test_every_writing_of_one_number_gives_the_same_phone(written):
    assert parse_phone(written) == "+79501234505"


@pytest.mark.parametrize("written", WRITINGS)
def test_no_writing_reaches_the_foreign_model(written):
    masked = Masker.from_facts({}).mask_text(f"мой номер {written}")

    assert "[телефон]" in masked
    assert "9501234505" not in masked.replace(" ", "")


@pytest.mark.parametrize("written", WRITINGS)
def test_no_writing_reaches_the_transcript(written):
    stored = hash_phones(f"звоните на {written}")

    assert "[телефон " in stored
    assert "123" not in stored


def test_the_same_number_written_differently_shares_one_fingerprint():
    assert len({phone_fingerprint(written) for written in WRITINGS}) == 1


def test_a_number_without_a_prefix_is_taken_as_a_contact():
    updates = extract_local_pii_updates("записывайте 9501234505")

    assert {"path": "client.phone", "value": "+79501234505", "confidence": "high"} in updates


@pytest.mark.parametrize(
    "text",
    [
        "сумма 4000000",
        "мне нужно 1500000 рублей",
        "долг 300000",
        "платёж 45000 в месяц",
        "заработок 120000",
    ],
)
def test_amounts_are_not_mistaken_for_phones(text):
    """Десять цифр — телефон, только если это похоже на мобильный номер."""
    assert parse_phone(text) is None
    assert hash_phones(text) == text
