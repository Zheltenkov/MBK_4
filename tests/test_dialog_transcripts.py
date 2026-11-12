"""Переписка в базе заказчика: пишем всё, кроме номера телефона.

Заказчик просил складывать диалоги в его Postgres, чтобы читать их глазами, и
сам назвал единственные персональные данные — номер: «его хэшируй и всё».
Поэтому тесты держат две границы: реплики доезжают целиком и по порядку, а
номер не доезжает ни в каком виде и ни в одной записи.
"""
from __future__ import annotations

import pytest

import analytics
import core
from analytics.transcripts import (
    MemoryTranscriptSink,
    ROLE_ASSISTANT,
    ROLE_CLIENT,
    TranscriptRecorder,
    hash_phones,
    phone_fingerprint,
    reset_salt_cache,
)


@pytest.fixture
def sink(monkeypatch):
    monkeypatch.setenv("MBK_DIALOG_PHONE_SALT", "тестовая-соль")
    reset_salt_cache()
    memory = MemoryTranscriptSink()
    analytics.set_transcripts(TranscriptRecorder(memory))
    yield memory
    analytics.set_transcripts(None)
    reset_salt_cache()


def test_a_turn_is_stored_in_the_order_the_client_saw_it(sink):
    analytics.get_transcripts().record_turn(
        "s-1", 3, "Мне нужно 500 тысяч", ["Понимаю.", "Сколько вам полных лет?"]
    )

    assert [(m.role, m.position, m.message) for m in sink.messages] == [
        (ROLE_CLIENT, 0, "Мне нужно 500 тысяч"),
        (ROLE_ASSISTANT, 1, "Понимаю."),
        (ROLE_ASSISTANT, 2, "Сколько вам полных лет?"),
    ]
    assert {m.turn for m in sink.messages} == {3}
    assert {m.session_id for m in sink.messages} == {"s-1"}


def test_the_phone_never_reaches_the_table(sink):
    analytics.get_transcripts().record_turn(
        "s-1", 4, "Записывайте: 8 999 123-45-67", ["Спасибо, записала."]
    )

    stored = [m.message for m in sink.messages]
    assert not any("999" in text for text in stored)
    assert stored[0].startswith("Записывайте: [телефон ")


def test_the_same_number_gives_the_same_fingerprint_in_any_notation():
    """Иначе диалог не связать с лидом, ради чего отпечаток и нужен."""
    assert phone_fingerprint("+7 (999) 123-45-67") == phone_fingerprint("89991234567")
    assert phone_fingerprint("8-999-123-45-67") == phone_fingerprint("+79991234567")
    assert phone_fingerprint("89991234567") != phone_fingerprint("89991234568")


def test_the_salt_changes_the_fingerprint(monkeypatch):
    """Соль — единственное, что отделяет отпечаток от открытого номера."""
    monkeypatch.setenv("MBK_DIALOG_PHONE_SALT", "первая")
    reset_salt_cache()
    first = phone_fingerprint("89991234567")

    monkeypatch.setenv("MBK_DIALOG_PHONE_SALT", "вторая")
    reset_salt_cache()
    second = phone_fingerprint("89991234567")

    assert first != second
    reset_salt_cache()


def test_several_numbers_in_one_message_are_all_replaced(sink):
    text = "Мой 8 999 123-45-67, жены +7 999 765-43-21"

    hashed = hash_phones(text)

    assert "999" not in hashed
    assert hashed.count("[телефон ") == 2


def test_a_silent_turn_writes_nothing(sink):
    """После финала бот молчит — пустых строк в переписке быть не должно."""
    analytics.get_transcripts().record_turn("s-1", 9, "", [])

    assert sink.messages == []


def test_storage_failure_does_not_break_the_turn():
    class Broken:
        enabled = True

        def write(self, message):  # noqa: ARG002 — приёмник обязан упасть
            raise RuntimeError("база недоступна")

    analytics.set_transcripts(TranscriptRecorder(Broken()))
    try:
        analytics.get_transcripts().record_turn("s-1", 1, "Здравствуйте", ["Добрый день"])
    finally:
        analytics.set_transcripts(None)


def test_disabled_recorder_is_the_default():
    assert analytics.get_transcripts().enabled is False


def test_commit_turn_records_what_the_client_saw(sink):
    state = {"session_id": "s-42", "message_count": 2, "chat_history": []}

    core.commit_turn(state, "Мне 46 лет", "Спасибо.\n\nВ каком городе живёте?")

    assert [(m.role, m.message) for m in sink.messages] == [
        (ROLE_CLIENT, "Мне 46 лет"),
        (ROLE_ASSISTANT, "Спасибо."),
        (ROLE_ASSISTANT, "В каком городе живёте?"),
    ]
    # Номер хода — уже увеличенный: ход считается состоявшимся.
    assert {m.turn for m in sink.messages} == {3}


def test_a_session_without_an_id_is_not_recorded(sink):
    """Ход эвала и локального прогона не должен уезжать в базу заказчика."""
    core.commit_turn({"chat_history": []}, "Привет", "Добрый день")

    assert sink.messages == []


def test_transcripts_are_switched_off_by_the_environment(monkeypatch):
    from analytics.postgres import build_transcript_recorder

    monkeypatch.setenv("MBK_DIALOG_TRANSCRIPTS_ENABLED", "0")

    recorder = build_transcript_recorder("postgresql://localhost/mbk")

    assert recorder.enabled is False
