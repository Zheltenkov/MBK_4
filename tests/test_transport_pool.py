"""Соединение к модели переиспользуется между вызовами.

Замер 19.08.2026 через прокси заказчика: пустячный запрос — 1,7 секунды, он же
по уже открытому соединению — 0,47. Разница уходит на TCP, туннель и TLS, и
раньше мы платили её дважды за каждый ход диалога.

Главная опасность пула — вернуть в него соединение с недочитанным телом:
следующий запрос получил бы чужие байты. Поэтому здесь проверяется не только
экономия, но и все случаи, когда соединение переиспользовать нельзя.
"""
from __future__ import annotations

import urllib.error

import pytest

import transport


class _Raw:
    """Ответ http.client в объёме, который использует наш код."""

    def __init__(self, status=200, body=b"{}", will_close=False) -> None:
        self.status = status
        self.reason = "OK" if status < 400 else "Error"
        self.headers = {}
        self.will_close = will_close
        self._body = body
        self._read = False
        self.closed = False

    def read(self, *_args) -> bytes:
        self._read = True
        return self._body

    def isclosed(self) -> bool:
        return self._read

    def close(self) -> None:
        self.closed = True


class _Conn:
    """Соединение-заглушка. Считает запросы и умеет притворяться протухшим."""

    def __init__(self, responses=None, stale=False) -> None:
        self.responses = list(responses or [_Raw()])
        self.stale = stale
        self.requests = 0
        self.closed = False

    def request(self, method, path, body=None, headers=None) -> None:
        if self.stale:
            raise ConnectionResetError("соединение закрыто с той стороны")
        self.requests += 1

    def getresponse(self):
        return self.responses.pop(0) if self.responses else _Raw()

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def pool(monkeypatch):
    made: list[_Conn] = []

    def _connect(self, target, proxy_url, timeout):
        conn = _Conn()
        made.append(conn)
        return conn

    monkeypatch.setattr(transport.ConnectionPool, "_connect", _connect)
    instance = transport.ConnectionPool()
    instance.made = made
    return instance


URL = "https://openrouter.ai/api/v1/chat/completions"


def _call(pool, **kwargs):
    return pool.request(URL, data=b"{}", headers={}, timeout=5, **kwargs)


def test_second_call_reuses_the_connection(pool):
    with _call(pool) as response:
        response.read()
    with _call(pool) as response:
        response.read()

    assert len(pool.made) == 1, "второй вызов должен идти по живому соединению"
    assert pool.made[0].requests == 2


def test_unread_response_does_not_go_back_to_the_pool(pool):
    """Недочитанное тело делает соединение непригодным.

    Так закрывается стрим: мы выходим из цикла на «[DONE]», не дочитав остаток.
    Вернуть такое соединение — значит отдать следующему вызову чужие байты.
    """
    with _call(pool):
        pass  # тело не прочитано

    with _call(pool) as response:
        response.read()

    assert len(pool.made) == 2
    assert pool.made[0].closed


def test_server_asking_to_close_is_obeyed(pool, monkeypatch):
    conn = _Conn(responses=[_Raw(will_close=True)])
    monkeypatch.setattr(
        transport.ConnectionPool, "_connect", lambda self, *a: conn
    )
    with _call(pool) as response:
        response.read()

    assert conn.closed


def test_stale_connection_is_replaced_without_an_error(pool, monkeypatch):
    """Соединение могло закрыться, пока лежало в пуле без дела.

    Для вызывающего это не сбой: он просто получает ответ по новому соединению.
    """
    live = _Conn()
    dead = _Conn(stale=True)
    monkeypatch.setattr(
        transport.ConnectionPool, "_connect", lambda self, *a: live
    )

    pool.release(("https", "openrouter.ai", None, ""), dead)
    with _call(pool) as response:
        assert response.read() == b"{}"

    assert dead.closed
    assert live.requests == 1


def test_error_status_raises_the_same_exception_as_before(pool, monkeypatch):
    """Ретраи и разбор ошибок в llm_agent ловят urllib.error.HTTPError.

    Пул обязан говорить на том же языке, иначе вся политика повторов ниже по
    коду перестала бы срабатывать молча.
    """
    conn = _Conn(responses=[_Raw(status=429, body=b'{"error":"rate limit"}')])
    monkeypatch.setattr(
        transport.ConnectionPool, "_connect", lambda self, *a: conn
    )

    with pytest.raises(urllib.error.HTTPError) as exc:
        _call(pool)

    assert exc.value.code == 429
    assert b"rate limit" in exc.value.read()
    assert conn.closed, "соединение с ошибкой в пул не возвращается"


def test_pool_does_not_grow_without_bound(pool):
    key = ("https", "openrouter.ai", None, "")
    extra = [_Conn() for _ in range(transport.MAX_IDLE_PER_TARGET + 2)]
    for conn in extra:
        pool.release(key, conn)

    assert sum(not c.closed for c in extra) == transport.MAX_IDLE_PER_TARGET


def test_different_proxies_do_not_share_connections(pool):
    with _call(pool, proxy_url="http://one:8000") as response:
        response.read()
    with _call(pool, proxy_url="http://two:8000") as response:
        response.read()

    assert len(pool.made) == 2


class _ConnWithSocket(_Conn):
    """Соединение, помнящее таймаут — как настоящий http.client."""

    def __init__(self, timeout) -> None:
        super().__init__()
        self.timeout = timeout

        class _Sock:
            def __init__(self) -> None:
                self.timeout = timeout

            def settimeout(self, value) -> None:
                self.timeout = value

        self.sock = _Sock()


def test_reused_connection_obeys_the_timeout_of_the_new_call(monkeypatch):
    """Пул отменял собственный потолок ожидания извлекателя.

    Таймаут сокета задаётся при открытии соединения. Разговорный вызов открывал
    его на 120 секунд, извлекатель брал то же соединение из пула — и ждал 120
    вместо своих 30. Правка про короткий потолок в бою просто не действовала.
    """
    monkeypatch.setattr(transport.time, "monotonic", lambda: 100.0)
    made = []
    monkeypatch.setattr(
        transport.ConnectionPool,
        "_connect",
        lambda self, target, proxy_url, timeout: made.append(_ConnWithSocket(timeout))
        or made[-1],
    )
    pool = transport.ConnectionPool()

    with pool.request(URL, data=b"{}", headers={}, timeout=120) as response:
        response.read()
    with pool.request(URL, data=b"{}", headers={}, timeout=30) as response:
        response.read()

    assert len(made) == 1, "соединение должно переиспользоваться"
    assert made[0].sock.timeout == 30, "короткий вызов не должен ждать чужие 120 секунд"
    assert made[0].timeout == 30
