"""Сравнение секретов не имеет права падать на русской букве.

`hmac.compare_digest` со строками работает только пока обе стороны — ASCII, а
на любой другой букве бросает TypeError. У нас в это сравнение приходят три
вещи, и все три бывают не-ASCII:

* логин из формы входа — его набирает человек;
* логин и пароль из окружения — их выбирает администратор МБК при установке;
* подпись из cookie — её присылает кто угодно.

Исключение из middleware превращается в 500: неверный логин выглядит поломкой
сервера, русский пароль в `.env` закрывает вход всем сразу, а подделанная
cookie даёт постороннему способ получать 500 на каждый запрос.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
from session_store import SessionStore


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(main, "store", SessionStore(max_extract_workers=1))
    main.reset_session_rate_limit()
    # Внутренний сбой должен доехать до теста ответом 500, а не исключением:
    # проверяется именно код ответа, который увидит клиент.
    return TestClient(main.app, raise_server_exceptions=False)


def test_cyrillic_login_attempt_is_just_a_refusal(client, monkeypatch):
    monkeypatch.setenv("MBK_AUTH_USERNAME", "operator")
    monkeypatch.setenv("MBK_AUTH_PASSWORD", "secret")

    response = client.post(
        "/api/auth/login", json={"username": "Ирина", "password": "secret"}
    )

    assert response.status_code == 401


def test_russian_credentials_from_env_still_let_the_operator_in(client, monkeypatch):
    """Администратор МБК вправе завести логин и пароль по-русски.

    Проверка не косметическая: при русском логине падал сам вход, и по логам
    это выглядело внутренней ошибкой, а не ошибкой настройки.
    """
    monkeypatch.setenv("MBK_AUTH_USERNAME", "оператор")
    monkeypatch.setenv("MBK_AUTH_PASSWORD", "пароль")

    response = client.post(
        "/api/auth/login", json={"username": "оператор", "password": "пароль"}
    )

    assert response.status_code == 200
    assert main.AUTH_COOKIE_NAME in response.cookies


def test_russian_credentials_survive_a_second_request(client, monkeypatch):
    """Токен проверяется на каждом запросе — там же второе сравнение."""
    monkeypatch.setenv("MBK_AUTH_USERNAME", "оператор")
    monkeypatch.setenv("MBK_AUTH_PASSWORD", "пароль")
    assert client.post(
        "/api/auth/login", json={"username": "оператор", "password": "пароль"}
    ).status_code == 200

    me = client.get("/api/auth/me")

    assert me.status_code == 200
    assert me.json()["username"] == "оператор"


def test_a_forged_cookie_gets_a_refusal_not_a_crash(client, monkeypatch):
    """Cookie присылает кто угодно и какую угодно.

    Наша подпись всегда ASCII, но заменить её в браузере — дело одной строки.
    Заголовки приходят байтами и разбираются как latin-1, поэтому байт 0xFF
    доезжает до сравнения обычной не-ASCII строкой, и каждый такой запрос
    обязан заканчиваться отказом, а не пятисоткой.
    """
    monkeypatch.setenv("MBK_AUTH_USERNAME", "operator")
    monkeypatch.setenv("MBK_AUTH_PASSWORD", "secret")

    response = client.get(
        "/api/usage/global",
        headers={"Cookie": f"{main.AUTH_COOKIE_NAME}=cGF5.".encode() + b"\xff\xfe"},
    )

    assert response.status_code == 401


def test_a_forged_session_grant_gets_a_refusal_not_a_crash(client, monkeypatch):
    """Тот же байт в разрешении на сессию — а этот путь открыт всему интернету.

    Разрешение на сессию проверяется у посетителя без всякого пароля, значит
    ответ 500 здесь достаётся кому угодно и на каждом запросе.
    """
    monkeypatch.setenv("MBK_PUBLIC_CHAT", "1")
    monkeypatch.setenv("MBK_AUTH_USERNAME", "operator")
    monkeypatch.setenv("MBK_AUTH_PASSWORD", "secret")

    response = client.get(
        "/api/session/0123456789ab",
        headers={"Cookie": f"{main.SESSION_GRANT_COOKIE}=".encode() + b"\xff\xfe"},
    )

    assert response.status_code == 403
