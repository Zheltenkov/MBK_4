"""Публичное состояние несёт кнопки: по нему виджет восстанавливает их после перезагрузки."""
from __future__ import annotations

import main
from prompts import OPENING_MESSAGE
from session_store import Session
from state import init_state
from usage_tracker import SessionUsage


def _session(*client_messages: str) -> Session:
    state = init_state()
    state["chat_history"] = [{"role": "assistant", "content": OPENING_MESSAGE}]
    for message in client_messages:
        state["chat_history"].append({"role": "user", "content": message})
    return Session(
        id="chips-test",
        state=state,
        anketa=None,
        usage=SessionUsage(session_id="chips-test"),
    )


def test_greeting_state_carries_the_chips(monkeypatch):
    monkeypatch.setenv("MBK_DEBT_POLICY_ENABLED", "1")
    chips = main._public_state(_session())["quick_replies"]

    assert [chip["value"] for chip in chips] == [
        "Получить деньги", "Разобраться с долгами", "Я уже оставлял заявку", "Другой вопрос",
    ]


def test_disabled_debt_policy_does_not_offer_a_credit_routed_debt_chip(monkeypatch):
    monkeypatch.setenv("MBK_DEBT_POLICY_ENABLED", "0")

    chips = main._public_state(_session())["quick_replies"]

    assert [chip["value"] for chip in chips] == [
        "Получить деньги", "Я уже оставлял заявку", "Другой вопрос",
    ]


def test_chips_are_gone_after_the_client_spoke():
    assert main._public_state(_session("Нужны деньги"))["quick_replies"] == []


def test_flag_off_empties_the_chips(monkeypatch):
    monkeypatch.setenv("MBK_QUICK_REPLIES", "0")

    assert main._public_state(_session())["quick_replies"] == []


def test_entry_routing_off_empties_the_chips(monkeypatch):
    monkeypatch.setenv("MBK_ENTRY_ROUTING", "0")

    assert main._public_state(_session())["quick_replies"] == []


def test_closed_dialog_has_no_chips(monkeypatch):
    monkeypatch.setattr(main, "should_close_dialog", lambda state: True)

    assert main._public_state(_session())["quick_replies"] == []
