"""Просьба отменить заявку или удалить данные попадает в аналитику.

Бой 28.09.2026: «я передумала, уберите мой номер из базы». Бот отменить и
удалить ничего не может и честно так отвечает (`prompts.CANCEL_REQUEST_RULE`),
но само обращение терялось. Ответ бизнеса 30.09.2026: «нужен процесс создания
таких обращений». До процесса — событие `cancel_requested`: по нему такие
обращения можно выгрузить и разобрать. Персональных данных в событии нет.
"""
from __future__ import annotations

import pytest

import analytics
import entry_routing


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def emit(self, event, session_id, payload=None, dedup_key=None) -> None:
        self.events.append((event, session_id, payload or {}, dedup_key))


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(analytics, "get_recorder", lambda: rec)
    return rec


@pytest.mark.parametrize(
    "message",
    [
        "Я оставляла заявку на рефинансирование. Я передумала, можете мне пожалуйста не звонить?",
        "Пожалуйста уберите из базы данных мой номер! Мне ничего не надо",
        "отмените мою заявку",
        "Удалите мои данные",
        "заявку аннулируйте пожалуйста",
        "не звоните мне больше",
        "хочу отозвать заявку",
    ],
)
def test_cancel_or_delete_request_is_recognised(message):
    assert entry_routing.client_asks_to_cancel(message)


@pytest.mark.parametrize(
    "message",
    [
        "Мне нужно 500 тысяч",
        "Когда мне позвонят?",
        "Менеджер почему-то не звонит",
        "Передумал, нужно не 300, а 500 тысяч",
        "удалили из квартиры перегородку",
        "",
    ],
)
def test_ordinary_replies_are_not_cancel_requests(message):
    assert not entry_routing.client_asks_to_cancel(message)


def test_event_carries_no_personal_data(recorder):
    state = {
        "session_id": "s1",
        "message_count": 3,
        "facts": {"client": {"phone": "+79990000000", "name": "Александра"}},
        "crm_lead": {"status": "submitted", "lead_id": 123},
    }

    entry_routing.record_cancel_request(state, "уберите мой номер +79990000000 из базы")

    assert len(recorder.events) == 1
    event, session_id, payload, dedup_key = recorder.events[0]
    assert event == analytics.EVENTS.CANCEL_REQUESTED == "cancel_requested"
    assert session_id == "s1"
    assert payload == {"phone_given": True, "lead_sent": True, "turn": 3}
    assert dedup_key == "s1:cancel_requested"


def test_ordinary_reply_writes_nothing(recorder):
    entry_routing.record_cancel_request({"session_id": "s1"}, "Мне нужно 500 тысяч")

    assert recorder.events == []
