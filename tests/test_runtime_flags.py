"""Флаги, которые фронтенд получает вместе с состоянием.

Панель разбора (итоговый JSON, номер лида, режим отправки, расход токенов)
живёт в том же интерфейсе, что и чат клиента. 10.08.2026 на боевом стенде
клиент увидел бы «✅ Тестовый лид создан в CRM: 3450947» и блок с JSON, потому
что панель ничем не была ограничена. Теперь она за флагом, и направление
умолчания важнее самой проверки: забытая переменная должна означать «скрыто».
"""
from __future__ import annotations

import main
from session_store import Session
from state import init_state
from usage_tracker import SessionUsage


def _runtime(state: dict) -> dict:
    return state["runtime"]


def test_debug_panel_is_hidden_unless_explicitly_enabled(monkeypatch):
    monkeypatch.delenv("MBK_DEBUG_PANEL", raising=False)

    runtime = _runtime(main._public_state(_session()))

    assert runtime["debug_panel"] is False


def test_debug_panel_can_be_switched_on_for_our_own_stand(monkeypatch):
    monkeypatch.setenv("MBK_DEBUG_PANEL", "1")

    runtime = _runtime(main._public_state(_session()))

    assert runtime["debug_panel"] is True


def _session() -> Session:
    return Session(
        id="abc123",
        state=init_state(),
        anketa=None,
        usage=SessionUsage(session_id="abc123"),
    )
