"""Что клиент видит, когда что-то падает.

Аудит 12.08.2026: пять мест отдавали в SSE текст исключения целиком, а фронтенд
показывает его клиенту красной плашкой. В тексте оказывалось тело ответа
OpenRouter (до 800 знаков), детали подключения к Postgres и прочая внутренняя
кухня — то есть и утечка, и нечитаемое сообщение одновременно.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import main
from session_store import Session
from state import init_state
from usage_tracker import SessionUsage

SECRET = "OpenRouter HTTP 429: org-mbk-internal quota exceeded"


class _NeverDisconnects:
    async def is_disconnected(self) -> bool:
        return False


def _session() -> Session:
    return Session(
        id="abc123",
        state=init_state(),
        anketa=None,
        usage=SessionUsage(session_id="abc123"),
    )


def _events(monkeypatch) -> list[dict]:
    def _boom(*args, **kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(main, "prepare_turn", _boom)

    async def _collect() -> list[dict]:
        return [
            event
            async for event in main._run_turn_sse_unlocked(
                _session(), "привет", False, _NeverDisconnects()
            )
        ]

    return asyncio.run(_collect())


@pytest.fixture(autouse=True)
def _store(monkeypatch):
    from session_store import SessionStore

    monkeypatch.setattr(main, "store", SessionStore(max_extract_workers=1))


def test_internal_detail_never_reaches_the_client(monkeypatch):
    body = json.dumps(_events(monkeypatch), ensure_ascii=False)

    assert SECRET not in body
    assert "OpenRouter" not in body
    assert "RuntimeError" not in body


def test_client_gets_a_human_message_instead(monkeypatch):
    events = _events(monkeypatch)

    errors = [e for e in events if e.get("event") == "error"]
    assert errors
    assert main.CLIENT_ERROR_MESSAGE in errors[0]["data"]


def test_detail_is_kept_in_the_log(monkeypatch, caplog):
    import logging

    with caplog.at_level(logging.ERROR):
        _events(monkeypatch)

    assert SECRET in caplog.text


def test_a_second_stream_on_a_busy_session_does_not_hang(monkeypatch):
    """Ход сериализуется блокировкой, но ждать её можно было вечно.

    Аудит 12.08.2026: пока идёт ход, второй запрос по той же сессии висел на
    turn_lock без ограничения. Наш фронтенд второй запрос не шлёт — кнопка
    заблокирована, — но в открытом режиме десяток параллельных стримов на одну
    сессию держал бы столько же соединений до самого таймаута модели.
    """
    monkeypatch.setattr(main, "TURN_LOCK_TIMEOUT_SECONDS", 0.05)
    session = _session()

    async def _scenario() -> list[dict]:
        await session.turn_lock.acquire()  # ход «уже идёт»
        try:
            return [
                event
                async for event in main._run_turn_sse(
                    session, "привет", _NeverDisconnects()
                )
            ]
        finally:
            session.turn_lock.release()

    events = asyncio.run(_scenario())

    errors = [e for e in events if e.get("event") == "error"]
    assert errors
    assert "предыдущ" in errors[0]["data"].casefold()
    assert events[-1]["event"] == "done"
