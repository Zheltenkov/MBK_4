"""Срок хранения переписки и женские формы в чистке квитанций.

Обе правки — из аудита перед выкаткой 1.0.5.

Переписка живёт в базе заказчика дольше сессии: её читают глазами спустя дни,
поэтому 72 часа сессии ей не подходят. Но и бессрочное хранение должно быть
выбором, а не тем, что получилось само.

Чистка квитанций знала только мужской род, и после переименования ассистента
«Поняла вас, давайте разбираться» превращалось в «А вас, давайте разбираться»:
шаблон срезал мужскую форму как приставку женской — прямо в чат клиенту.
"""
from __future__ import annotations

import pytest

import analytics
from core import _safe_contact_reaction


@pytest.mark.parametrize(
    "value, expected",
    [(None, 0), ("", 0), ("0", 0), ("90", 90), ("-5", 0), ("мусор", 0)],
)
def test_retention_reads_the_setting_and_survives_nonsense(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("MBK_DIALOG_RETENTION_DAYS", raising=False)
    else:
        monkeypatch.setenv("MBK_DIALOG_RETENTION_DAYS", value)

    assert analytics.transcript_retention_days() == expected


def test_transcripts_are_kept_forever_by_default(monkeypatch):
    """Молчаливое удаление переписки заказчика было бы хуже её накопления."""
    monkeypatch.delenv("MBK_DIALOG_RETENTION_DAYS", raising=False)

    assert analytics.transcript_retention_days() == 0
    # Ноль дней не идёт в базу вовсе: запрос без срока удалил бы всё.
    assert analytics.purge_transcripts(0, "postgresql://nowhere/none") == 0


def test_purge_does_not_raise_when_the_database_is_gone():
    """Срок хранения не должен ронять приложение."""
    assert analytics.purge_transcripts(30, "postgresql://127.0.0.1:1/none") == 0


@pytest.mark.parametrize(
    "bubble, expected",
    [
        ("Понял вас, давайте разбираться.", "Понял вас, давайте разбираться."),
        ("Поняла вас, давайте разбираться.", "Поняла вас, давайте разбираться."),
        ("Приняла, спасибо.", "Приняла, спасибо."),
        ("Принял, спасибо.", "Принял, спасибо."),
        ("Хорошо, спасибо.", "Хорошо, спасибо."),
    ],
)
def test_acknowledgements_are_not_cut_inside_a_sentence(bubble, expected):
    """Манеру выбирает модель, обработчик не обрезает слова внутри её фразы."""
    assert _safe_contact_reaction([bubble]) == expected


def test_a_word_that_merely_starts_like_an_acknowledgement_survives():
    """«Понятно» — не квитанция «понял», и резать его как приставку нельзя."""
    assert _safe_contact_reaction(["Понятное дело, разберёмся."]) == (
        "Понятное дело, разберёмся."
    )


@pytest.mark.parametrize(
    "bubble, expected",
    [
        (
            "Пётр, данных достаточно. Напишите его, пожалуйста.",
            "Пётр, данных достаточно.",
        ),
        ("Ситуация ясная. Укажите её, пожалуйста.", "Ситуация ясная."),
    ],
)
def test_a_request_left_without_its_subject_is_dropped(bubble, expected):
    """«Напишите его» осмысленно только вместе с вырезанной фразой про номер.

    Собранный стенд 20.08.2026: клиент прочитал «Пётр, данных достаточно.
    Напишите его, пожалуйста» — и следом детерминированный запрос телефона.
    """
    assert _safe_contact_reaction([bubble]) == expected
