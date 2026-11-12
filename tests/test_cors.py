"""Кросс-доменный доступ к API.

Аудит 12.08.2026: стояло `allow_origins=["*"]` вместе с `allow_credentials=True`.
Starlette в этой комбинации подставляет в ответ origin запрашивающего и
разрешает cookie — то есть любой посторонний сайт мог из браузера залогиненного
оператора дёргать наш API и читать ответы.

Фронтенд ходит на тот же домен через Caddy, поэтому кросс-доменный доступ не
нужен вовсе. Открывается только явным списком.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

import main
from session_store import SessionStore


def test_no_cross_origin_access_by_default(monkeypatch):
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)

    assert main._cors_origins() == []


def test_wildcard_is_refused_together_with_credentials(monkeypatch):
    """Звёздочку нельзя принимать молча: с cookie она открывает API всем."""
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "*")

    assert main._cors_origins() == []


def test_explicit_origins_are_honoured(monkeypatch):
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS", "https://mbk.ru, https://www.mbk.ru"
    )

    assert main._cors_origins() == ["https://mbk.ru", "https://www.mbk.ru"]


# --------------------------------------------------------------------------- #
# Разрешение проверяется сквозь всё приложение, а не только по списку доменов
# --------------------------------------------------------------------------- #
@pytest.fixture()
def cross_domain(monkeypatch):
    """Приложение, поднятое с одним разрешённым чужим доменом.

    Список читается на импорте, и порядок middleware складывается там же,
    поэтому модуль приходится пересобирать: проверяется именно он.
    """
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://mbk.ru")
    monkeypatch.setenv("MBK_PUBLIC_CHAT", "1")
    monkeypatch.setenv("MBK_AUTH_USERNAME", "operator")
    monkeypatch.setenv("MBK_AUTH_PASSWORD", "secret")
    reloaded = importlib.reload(main)
    reloaded.reset_session_rate_limit()
    monkeypatch.setattr(reloaded, "store", SessionStore(max_extract_workers=1))

    yield TestClient(reloaded.app)

    # Остальной набор рассчитывает на модуль без CORS — возвращаем как было.
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    importlib.reload(main)


def test_preflight_is_answered_before_authorization(cross_domain):
    """Разрешение браузер спрашивает методом OPTIONS и без единой cookie.

    Порядок middleware решает здесь всё: авторизация добавлена последней,
    значит стоит снаружи CORS и отвечает на предполётный запрос отказом. Для
    браузера это означает, что настоящий запрос он не отправит вовсе — чат,
    встроенный на домен МБК, не открывается ни у кого. В консоли видно только
    «CORS policy», список доменов при этом настроен верно, а в наших логах
    ошибка выглядит обычным отказом доступа.
    """
    response = cross_domain.options(
        "/api/session",
        headers={
            "Origin": "https://mbk.ru",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200, response.text
    assert response.headers.get("access-control-allow-origin") == "https://mbk.ru"


def test_refusal_stays_readable_for_the_allowed_origin(cross_domain):
    """Ответ без заголовка CORS браузер не отдаёт коду вообще.

    Фронтенд на чужом домене не отличит «нет доступа к сессии» от обрыва связи
    и покажет клиенту не то сообщение.
    """
    response = cross_domain.get(
        "/api/session/deadbeef", headers={"Origin": "https://mbk.ru"}
    )

    assert response.status_code == 403
    assert response.headers.get("access-control-allow-origin") == "https://mbk.ru"
