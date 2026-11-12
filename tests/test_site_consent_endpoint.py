"""Эндпоинт, через который сайт приносит согласие клиента.

Схема заказчика от 21.08.2026: клиент жмёт кнопку на офер-странице, страница
зовёт `www.mbk.ru/api/chat-consent`, их бэкенд стучится к нам, и лид уходит в
CRM уже с согласием.

Ключ в заголовке заказчик отклонил («не усложняй»), поэтому единственная защита
на нашей стороне — список адресов, с которых запрос принимается. Без него
подтвердить согласие за клиента мог бы любой, у кого есть ссылка: номер сессии
виден в её параметрах.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
from session_store import SessionStore


THEIR_SERVER = "111.88.127.189"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MBK_AUTH_USERNAME", "operator")
    monkeypatch.setenv("MBK_AUTH_PASSWORD", "secret")
    monkeypatch.setenv("MBK_SITE_CONSENT_ALLOWED_IPS", THEIR_SERVER)
    monkeypatch.setenv("MBK_TRUSTED_PROXY_HOPS", "1")
    main.reset_session_rate_limit()
    monkeypatch.setattr(main, "store", SessionStore(max_extract_workers=1))
    return TestClient(main.app)


def _post(client, session_id: str = "a1b2c3d4e5f6", ip: str = THEIR_SERVER):
    return client.post(
        "/api/site-consent",
        json={"session_id": session_id},
        headers={"X-Forwarded-For": ip},
    )


def test_the_endpoint_needs_no_login():
    """Их бэкенд про наш пароль не знает и знать не должен."""
    assert "/api/site-consent" in main.PUBLIC_API_PATHS


def test_a_request_from_another_address_is_refused_when_the_list_is_set(client):
    """Заполненный список по-прежнему работает — на случай постоянного адреса."""

    response = _post(client, ip="203.0.113.7")

    assert response.status_code == 403
    assert "session" not in response.text.lower()


def test_an_unknown_session_is_not_confirmed(client):
    response = _post(client, session_id="ffffffffffff")

    assert response.status_code == 404


def test_a_missing_session_id_is_rejected(client):
    response = client.post(
        "/api/site-consent",
        json={},
        headers={"X-Forwarded-For": THEIR_SERVER},
    )

    assert response.status_code == 422


def test_without_an_allowlist_the_address_is_not_checked(client, monkeypatch):
    """Список адресов необязателен: внутри докера адрес не постоянен.

    Backend заказчика 24.08.2026: «allowlist убирай, внутри контейнеров
    работает нестабильно, не статично». К нам приходит адрес шлюза
    docker-сети, и он меняется при пересоздании стека — смоук 21.08 показал
    172.20.0.1 из сети, которой сегодня уже нет.

    Дальше запрос идёт обычным путём: сессии нет — 404, а не отказ по адресу.
    """
    monkeypatch.delenv("MBK_SITE_CONSENT_ALLOWED_IPS", raising=False)

    assert _post(client, ip="203.0.113.7").status_code == 404


def test_several_addresses_are_allowed(client, monkeypatch):
    monkeypatch.setenv(
        "MBK_SITE_CONSENT_ALLOWED_IPS", f"198.51.100.5, {THEIR_SERVER}"
    )

    # Чужой адрес по-прежнему не проходит, свой — доходит до поиска сессии.
    assert _post(client, ip="203.0.113.7").status_code == 403
    assert _post(client, ip="198.51.100.5").status_code == 404
