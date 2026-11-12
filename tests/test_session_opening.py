"""Регрессии порядка opening → первая реплика клиента."""

from __future__ import annotations

from main import _ensure_opening_history
from prompts import OPENING_MESSAGE
from session_store import Session
from state import init_state
from usage_tracker import SessionUsage


def _session() -> Session:
    """Создаёт локальную сессию без сетевых зависимостей."""
    return Session(
        id="opening-test",
        state=init_state(),
        anketa=None,
        usage=SessionUsage(session_id="opening-test"),
    )


def test_backend_seeds_opening_before_direct_first_message() -> None:
    """Даже прямой turn API не может начать историю с сообщения клиента."""
    session = _session()

    changed = _ensure_opening_history(session)

    assert changed is True
    assert session.has_started_opening is True
    assert session.state["chat_history"] == [
        {"role": "assistant", "content": OPENING_MESSAGE}
    ]


def test_backend_never_duplicates_existing_opening() -> None:
    """Повторная страховка не создаёт второе приветствие."""
    session = _session()
    assert _ensure_opening_history(session) is True

    changed = _ensure_opening_history(session)

    assert changed is False
    assert session.state["chat_history"] == [
        {"role": "assistant", "content": OPENING_MESSAGE}
    ]
