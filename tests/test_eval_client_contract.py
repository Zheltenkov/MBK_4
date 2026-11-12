"""Клиент эвалов обязан собирать ход целиком, а не последний его кусок.

19.08.2026 контракт SSE изменился: пузыри уходят по одному, отдельными
событиями, чтобы в чате появляться с паузой. Клиент оценки остался прежним — он
брал ПОСЛЕДНЕЕ событие `bubbles`, то есть видел только заключительную реплику
хода.

Дальше это тихо портило измерения. Проверяющий связность видел «бот не ответил
на вопрос клиента» там, где ответ был в первом пузыре, а до него просто не
доехало. Тем же путём читают дословные проверки диалоговых сценариев и оценка
человечности: `forbidden_any` перестал видеть большую часть реплик, то есть
проверка запретов молча ослабла.

Такого теста не было вовсе — поэтому смена контракта и прошла незамеченной.
"""
from __future__ import annotations

import json

import pytest

from evaluation.dialogs import LocalDialogClient


def _sse(events: list[tuple[str, dict]]) -> bytes:
    lines = []
    for name, payload in events:
        lines.append(f"event: {name}")
        lines.append("data: " + json.dumps(payload, ensure_ascii=False))
        lines.append("")
    return "\n".join(lines).encode("utf-8")


class _Response:
    def __init__(self, body: bytes) -> None:
        self._lines = [line + b"\n" for line in body.split(b"\n")]
        self._index = 0

    def readline(self) -> bytes:
        """Клиент читает поток построчно, как настоящий ответ SSE."""
        if self._index >= len(self._lines):
            return b""
        line = self._lines[self._index]
        self._index += 1
        return line

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


@pytest.fixture
def client(monkeypatch):
    instance = LocalDialogClient("http://127.0.0.1:8000")

    def _open(url, timeout=None):
        return _Response(_open.body)

    _open.body = b""
    monkeypatch.setattr(instance._opener, "open", _open)
    instance._fake = _open
    return instance


def test_all_bubbles_of_the_turn_are_collected(client):
    """Ход из трёх пузырей приходит тремя событиями — нужны все три."""
    client._fake.body = _sse([
        ("bubbles", {"bubbles": ["Переживать тут не о чем."]}),
        ("bubbles", {"bubbles": ["Ситуация обычная."]}),
        ("bubbles", {"bubbles": ["Какая сумма вам нужна?"]}),
        ("state", {"state": {"dialog_phase": "qualification"}, "delivery": None}),
        ("done", {}),
    ])

    turn = client.stream_turn("s1", "здравствуйте")

    assert turn["bubbles"] == [
        "Переживать тут не о чем.",
        "Ситуация обычная.",
        "Какая сумма вам нужна?",
    ]


def test_old_contract_with_one_event_still_works(client):
    """Несколько пузырей одним событием — прежняя форма, ломать её незачем."""
    client._fake.body = _sse([
        ("bubbles", {"bubbles": ["Раз.", "Два?"]}),
        ("state", {"state": {}, "delivery": None}),
        ("done", {}),
    ])

    assert client.stream_turn("s1", "привет")["bubbles"] == ["Раз.", "Два?"]


def test_silent_turn_gives_an_empty_list(client):
    """После финала бот молчит: событие приходит с пустым списком."""
    client._fake.body = _sse([
        ("bubbles", {"bubbles": []}),
        ("state", {"state": {"dialog_phase": "survey_completed"}, "delivery": None}),
        ("done", {}),
    ])

    assert client.stream_turn("s1", "спасибо")["bubbles"] == []


def test_missing_event_is_still_a_protocol_error(client):
    client._fake.body = _sse([("state", {"state": {}}), ("done", {})])

    with pytest.raises(RuntimeError, match="bubbles"):
        client.stream_turn("s1", "привет")


def test_state_is_taken_from_the_last_snapshot(client):
    client._fake.body = _sse([
        ("bubbles", {"bubbles": ["Раз."]}),
        ("state", {"state": {"dialog_phase": "qualification"}, "delivery": None}),
        ("state", {"state": {"dialog_phase": "survey_completed"}, "delivery": {"status": "completed"}}),
        ("done", {}),
    ])

    turn = client.stream_turn("s1", "привет")

    assert turn["state"]["dialog_phase"] == "survey_completed"
    assert turn["delivery"] == {"status": "completed"}
