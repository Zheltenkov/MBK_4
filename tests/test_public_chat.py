"""Открытый вход в чат: клиент приходит по кнопке с сайта МБК.

Гейт с одним логином на всех годился для стенда, но настоящий клиент войти
через него не может физически — регистрации нет. Заказчик 11.08.2026: «на сайте
кнопка перехода в чат будет».

Открытый чат — это открытый расход на модель, поэтому режим включается явным
флагом и приходит вместе с ограничением на число сессий с одного адреса.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
from session_store import SessionStore


@pytest.fixture
def client(monkeypatch):
    """Клиент без cookie: имитирует посетителя, пришедшего по ссылке."""
    monkeypatch.setenv("MBK_AUTH_USERNAME", "operator")
    monkeypatch.setenv("MBK_AUTH_PASSWORD", "secret")
    main.reset_session_rate_limit()
    # lifespan не запускаем: проверяется middleware, а не диалоговый конвейер,
    # поэтому store поднимаем руками и без хранилища сессий.
    monkeypatch.setattr(main, "store", SessionStore(max_extract_workers=1))
    return TestClient(main.app)


def test_closed_by_default(client, monkeypatch):
    """Забытая переменная не должна открывать чат наружу."""
    monkeypatch.delenv("MBK_PUBLIC_CHAT", raising=False)

    assert client.post("/api/session", json={}).status_code == 401


def test_public_mode_lets_a_visitor_start_a_chat(client, monkeypatch):
    monkeypatch.setenv("MBK_PUBLIC_CHAT", "1")

    assert client.post("/api/session", json={}).status_code != 401


def test_global_usage_stays_closed_even_in_public_mode(client, monkeypatch):
    """Сводный расход по всем диалогам — наша цифра, не клиентская."""
    monkeypatch.setenv("MBK_PUBLIC_CHAT", "1")

    assert client.get("/api/usage/global").status_code == 401


def test_visitor_is_capped_by_sessions_per_hour(client, monkeypatch):
    monkeypatch.setenv("MBK_PUBLIC_CHAT", "1")
    monkeypatch.setenv("MBK_PUBLIC_CHAT_SESSIONS_PER_HOUR", "2")
    headers = {"X-Forwarded-For": "203.0.113.7"}

    codes = [
        client.post("/api/session", json={}, headers=headers).status_code
        for _ in range(3)
    ]

    assert codes[-1] == 429
    assert 429 not in codes[:-1]


def test_visitors_are_counted_separately(client, monkeypatch):
    """Иначе первый же активный посетитель закрывает чат для всех остальных."""
    monkeypatch.setenv("MBK_PUBLIC_CHAT", "1")
    monkeypatch.setenv("MBK_PUBLIC_CHAT_SESSIONS_PER_HOUR", "1")

    first = client.post(
        "/api/session", json={}, headers={"X-Forwarded-For": "203.0.113.7"}
    )
    second = client.post(
        "/api/session", json={}, headers={"X-Forwarded-For": "203.0.113.8"}
    )

    assert first.status_code != 429
    assert second.status_code != 429


def test_spoofed_forwarded_prefix_does_not_reset_the_limit(client, monkeypatch):
    """Доверяем только тому звену, которое дописал наш Caddy.

    X-Forwarded-For накапливается слева направо, и Caddy ДОПИСЫВАЕТ адрес пира
    справа, не затирая присланное клиентом. Значит левые элементы пришли от
    клиента и подделываются свободно, а последний поставили мы. Первая версия
    брала первое звено — то есть лимита не было вовсе: клиент обходил его,
    меняя строку в заголовке. Найдено автоматическим ревью 11.08.2026.
    """
    monkeypatch.setenv("MBK_PUBLIC_CHAT", "1")
    monkeypatch.setenv("MBK_PUBLIC_CHAT_SESSIONS_PER_HOUR", "1")

    # Один и тот же посетитель (203.0.113.7 дописал Caddy), но с разными
    # подставленными префиксами.
    first = client.post(
        "/api/session",
        json={},
        headers={"X-Forwarded-For": "1.2.3.4, 203.0.113.7"},
    )
    second = client.post(
        "/api/session",
        json={},
        headers={"X-Forwarded-For": "5.6.7.8, 203.0.113.7"},
    )

    assert first.status_code != 429
    assert second.status_code == 429


def test_garbage_in_the_header_does_not_become_a_key(client, monkeypatch):
    """Не-IP в заголовке иначе засоряет словарь произвольными строками."""
    monkeypatch.setenv("MBK_PUBLIC_CHAT", "1")
    monkeypatch.setenv("MBK_PUBLIC_CHAT_SESSIONS_PER_HOUR", "1")

    first = client.post(
        "/api/session", json={}, headers={"X-Forwarded-For": "not-an-address-1"}
    )
    second = client.post(
        "/api/session", json={}, headers={"X-Forwarded-For": "not-an-address-2"}
    )

    # Оба свелись к адресу соединения, поэтому второй упирается в лимит.
    assert first.status_code != 429
    assert second.status_code == 429


def test_the_log_shows_which_address_the_visitor_came_from(client, monkeypatch, caplog):
    """Иначе `MBK_TRUSTED_PROXY_HOPS` нечем проверить, кроме как сломав прод.

    Ошибка в этом значении не видна вообще ничем: бот считает всех посетителей
    одним адресом, работает нормально десять диалогов, а потом закрывает чат
    сразу всем. Установщику нужен способ убедиться заранее — он описан в
    docs/INSTALL.md и опирается на эту строку.
    """
    monkeypatch.setenv("MBK_PUBLIC_CHAT", "1")

    with caplog.at_level("INFO", logger=main.logger.name):
        client.post("/api/session", json={}, headers={"X-Forwarded-For": "203.0.113.7"})

    assert "203.0.113.7" in caplog.text


def test_hitting_the_limit_says_whose_address_ran_out(client, monkeypatch, caplog):
    """«Чат не открывается у всех» — самая дорогая жалоба, и она молчаливая."""
    monkeypatch.setenv("MBK_PUBLIC_CHAT", "1")
    monkeypatch.setenv("MBK_PUBLIC_CHAT_SESSIONS_PER_HOUR", "1")
    headers = {"X-Forwarded-For": "203.0.113.9"}
    client.post("/api/session", json={}, headers=headers)

    with caplog.at_level("WARNING", logger=main.logger.name):
        rejected = client.post("/api/session", json={}, headers=headers)

    assert rejected.status_code == 429
    assert "203.0.113.9" in caplog.text


def test_operator_with_a_cookie_is_not_rate_limited(client, monkeypatch):
    """Наши прогоны и эвалы не должны упираться в клиентский лимит."""
    monkeypatch.setenv("MBK_PUBLIC_CHAT", "1")
    monkeypatch.setenv("MBK_PUBLIC_CHAT_SESSIONS_PER_HOUR", "1")
    login = client.post(
        "/api/auth/login", json={"username": "operator", "password": "secret"}
    )
    assert login.status_code == 200

    codes = [client.post("/api/session", json={}).status_code for _ in range(3)]

    assert 429 not in codes


def test_long_message_is_truncated_before_it_reaches_the_model(monkeypatch):
    """Реплика клиента ничем не ограничивалась и ехала в промпт целиком.

    Живой человек не пишет анкетный ответ на десять тысяч знаков, а в открытом
    режиме это прямой рычаг на наш счёт за модель: длина реплики умножается на
    сорок сообщений диалога.
    """
    monkeypatch.delenv("MBK_MAX_MESSAGE_CHARS", raising=False)

    trimmed = main._clamp_message("а" * 50_000)

    assert len(trimmed) == main.DEFAULT_MAX_MESSAGE_CHARS


def test_normal_message_is_untouched():
    assert main._clamp_message("нужно 500 тысяч") == "нужно 500 тысяч"


def test_a_visitor_cannot_read_someone_elses_session(client, monkeypatch):
    """Знание чужого session_id не должно открывать анкету с ПД.

    Аудит 12.08.2026: белый список открывал весь /api/session*, а
    GET /api/session/{id} отдаёт facts целиком — имя, телефон, город, долги.
    Привязки сессии к посетителю не было вовсе, то есть ссылка, попавшая в
    историю браузера или на скриншот, открывала чужие данные любому.
    """
    monkeypatch.setenv("MBK_PUBLIC_CHAT", "1")

    victim = client.post("/api/session", json={}).json()["session_id"]

    # Другой посетитель: своей выданной cookie у него нет.
    stranger = TestClient(main.app)
    response = stranger.get(f"/api/session/{victim}")

    assert response.status_code in (401, 403)


def test_a_visitor_can_read_the_session_they_started(client, monkeypatch):
    """Фронтенд перечитывает состояние при перезагрузке страницы."""
    monkeypatch.setenv("MBK_PUBLIC_CHAT", "1")

    session_id = client.post("/api/session", json={}).json()["session_id"]

    assert client.get(f"/api/session/{session_id}").status_code == 200


def test_a_visitor_cannot_stream_someone_elses_session(client, monkeypatch):
    monkeypatch.setenv("MBK_PUBLIC_CHAT", "1")

    victim = client.post("/api/session", json={}).json()["session_id"]

    stranger = TestClient(main.app)
    response = stranger.get(f"/api/session/{victim}/stream?message=привет")

    assert response.status_code in (401, 403)


def test_the_operator_still_sees_any_session(client, monkeypatch):
    """Разбор инцидента с нашей стороны ломать нельзя."""
    monkeypatch.setenv("MBK_PUBLIC_CHAT", "1")
    visitor_session = client.post("/api/session", json={}).json()["session_id"]

    operator = TestClient(main.app)
    operator.post(
        "/api/auth/login", json={"username": "operator", "password": "secret"}
    )

    assert operator.get(f"/api/session/{visitor_session}").status_code == 200
