"""Запасной прокси к модели: один упал — запросы идут через другой.

Прод 25.09.2026: единственный прокси к OpenRouter упал в 16:15, и бот двое
суток отвечал только приветствием — оно готовым текстом, без модели. Каждая
реплика клиента падала на вызове модели, лидов — ноль. Проверка здоровья при
этом была зелёной: она модель не зовёт.

Теперь `OPENROUTER_PROXY` принимает несколько адресов через запятую. Отказ
подключиться к прокси (соединение, туннель, 407) переводит запрос на следующий,
и пул дальше держится того, что сработал. Ответ сервера модели с ошибкой — не
отказ прокси: его разбирают ретраи в llm_agent.
"""
from __future__ import annotations

import logging
import urllib.error

import pytest

import llm_agent
import transport
from config import AppConfig, load_config


URL = "https://openrouter.ai/api/v1/chat/completions"
ONE = "http://user1:secret1@10.0.0.1:8000"
TWO = "http://user2:secret2@10.0.0.2:8000"


class _Raw:
    def __init__(self, status=200, body=b"{}") -> None:
        self.status = status
        self.reason = "OK" if status < 400 else "Error"
        self.headers = {}
        self.will_close = False
        self._body = body
        self._read = False

    def read(self, *_args) -> bytes:
        self._read = True
        return self._body

    def isclosed(self) -> bool:
        return self._read

    def close(self) -> None:
        pass


class _Conn:
    def __init__(self, proxy, response=None, fail_on_request=False) -> None:
        self.proxy = proxy
        self.response = response or _Raw()
        self.fail_on_request = fail_on_request
        self.closed = False

    def request(self, method, path, body=None, headers=None) -> None:
        if self.fail_on_request:
            raise ConnectionResetError("обрыв после подключения")

    def getresponse(self):
        return self.response

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def network(monkeypatch):
    """Сеть, где каждый прокси может быть мёртв или отвечать заданным ответом."""

    class _Network:
        def __init__(self) -> None:
            self.dead: set[str] = set()
            self.responses: dict[str, _Raw] = {}
            self.fail_on_request: set[str] = set()
            self.connects: list[str | None] = []

    net = _Network()

    def _connect(self, target, proxy_url, timeout):
        net.connects.append(proxy_url)
        if proxy_url in net.dead:
            raise OSError("Tunnel connection failed: 407 Proxy Authentication Required")
        return _Conn(
            proxy_url,
            response=net.responses.get(proxy_url),
            fail_on_request=proxy_url in net.fail_on_request,
        )

    monkeypatch.setattr(transport.ConnectionPool, "_connect", _connect)
    return net


def _call(pool, proxies):
    return pool.request(URL, data=b"{}", headers={}, timeout=5, proxy_urls=proxies)


def test_dead_first_proxy_hands_the_request_to_the_second(network):
    network.dead.add(ONE)
    pool = transport.ConnectionPool()

    with _call(pool, (ONE, TWO)) as response:
        assert response.read() == b"{}"

    assert network.connects == [ONE, TWO]


def test_pool_sticks_to_the_proxy_that_worked(network):
    """Мёртвый прокси не должен стоить каждому следующему вызову попытки."""
    network.dead.add(ONE)
    pool = transport.ConnectionPool()

    with _call(pool, (ONE, TWO)) as response:
        response.read()
    network.connects.clear()
    # Соединение через второй лежит в пуле, но и новое открывается через него.
    pool._idle.clear()
    with _call(pool, (ONE, TWO)) as response:
        response.read()

    assert network.connects == [TWO]


def test_first_proxy_is_used_again_when_the_second_dies(network):
    network.dead.add(ONE)
    pool = transport.ConnectionPool()
    with _call(pool, (ONE, TWO)) as response:
        response.read()

    network.dead = {TWO}
    pool._idle.clear()
    network.connects.clear()
    with _call(pool, (ONE, TWO)) as response:
        response.read()

    assert network.connects == [TWO, ONE]


def test_all_proxies_dead_raises_a_transport_error(network):
    """Ретраи llm_agent ловят TRANSPORT_ERRORS — отказ всех прокси должен туда попадать."""
    network.dead.update({ONE, TWO})
    pool = transport.ConnectionPool()

    with pytest.raises(transport.TRANSPORT_ERRORS):
        _call(pool, (ONE, TWO))

    assert network.connects == [ONE, TWO]


def test_error_answer_of_the_model_is_not_a_proxy_failure(network):
    """429 или 500 от OpenRouter пришли через живой прокси — второй не пробуем."""
    network.responses[ONE] = _Raw(status=429, body=b'{"error":"rate limit"}')
    pool = transport.ConnectionPool()

    with pytest.raises(urllib.error.HTTPError) as exc:
        _call(pool, (ONE, TWO))

    assert exc.value.code == 429
    assert network.connects == [ONE]


def test_break_after_connecting_is_left_to_the_retries(network):
    """Прокси пустил, а связь оборвалась на запросе: это не повод менять прокси.

    Повтор делает llm_agent; если прокси действительно умер, повтор не сможет
    к нему подключиться и уже тогда уйдёт на запасной.
    """
    network.fail_on_request.add(ONE)
    pool = transport.ConnectionPool()

    with pytest.raises(ConnectionResetError):
        _call(pool, (ONE, TWO))

    assert network.connects == [ONE]


def test_switch_is_logged_without_address_and_password(network, caplog):
    network.dead.add(ONE)
    pool = transport.ConnectionPool()

    with caplog.at_level(logging.WARNING, logger="transport"):
        with _call(pool, (ONE, TWO)) as response:
            response.read()

    text = caplog.text
    assert "1 из 2" in text
    for secret in ("user1", "secret1", "10.0.0.1"):
        assert secret not in text


def test_single_proxy_keeps_working_as_before(network):
    pool = transport.ConnectionPool()

    with pool.request(URL, data=b"{}", headers={}, timeout=5, proxy_url=ONE) as response:
        assert response.read() == b"{}"

    assert network.connects == [ONE]


def test_new_connection_is_established_with_a_short_timeout(monkeypatch):
    """Мёртвый прокси не должен держать клиента весь таймаут вызова модели.

    Подключение и туннель — секунды; 120 секунд разговорного вызова на них
    означали бы две минуты тишины перед переходом на запасной.
    """
    seen: dict[str, float] = {}

    class _FakeHTTPS:
        def __init__(self, host, port, timeout=None, context=None) -> None:
            self.timeout = timeout
            self.sock = None

        def set_tunnel(self, host, port, headers=None) -> None:
            pass

        def connect(self) -> None:
            seen["connect_timeout"] = self.timeout

    monkeypatch.setattr(transport.http.client, "HTTPSConnection", _FakeHTTPS)
    pool = transport.ConnectionPool()
    conn = pool._connect(transport.urlsplit(URL), ONE, 120)

    assert seen["connect_timeout"] == transport.CONNECT_TIMEOUT
    assert transport.CONNECT_TIMEOUT < 120
    assert conn.timeout == 120, "после подключения действует таймаут самого вызова"


# --------------------------------------------------------------------------- #
# Настройка: список прокси в OPENROUTER_PROXY.
# --------------------------------------------------------------------------- #
@pytest.fixture
def env(monkeypatch):
    # These cases exercise OpenRouter failover independently of the local service.
    monkeypatch.setenv("MBK_LLM_PROVIDER", "openrouter")
    for name in ("OPENROUTER_PROXY", "HTTPS_PROXY", "HTTP_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "sk-or-test")
    return monkeypatch


def test_two_proxies_separated_by_a_comma(env):
    env.setenv("OPENROUTER_PROXY", f" {ONE} , {TWO} ")

    assert load_config().proxy_urls == (ONE, TWO)


def test_one_proxy_as_before(env):
    env.setenv("OPENROUTER_PROXY", ONE)

    assert load_config().proxy_urls == (ONE,)


def test_system_proxy_is_still_a_fallback(env):
    env.setenv("HTTPS_PROXY", TWO)

    assert load_config().proxy_urls == (TWO,)


def test_no_proxy_means_direct(env):
    assert load_config().proxy_urls == ()


def test_socks_address_is_refused_with_a_clear_message(env):
    """Транспорт ходит через HTTP-туннель. socks5:// он принял бы за HTTP-прокси
    и молча не подключился бы — лучше не стартовать с понятной ошибкой."""
    env.setenv("OPENROUTER_PROXY", "socks5://u:p@10.0.0.1:8000")

    with pytest.raises(ValueError, match="http://"):
        load_config()


def test_llm_agent_passes_all_proxies_to_the_transport(monkeypatch):
    seen: dict = {}

    def _request(url, **kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr(transport.POOL, "request", _request)
    config = AppConfig(openrouter_api_key="sk-or-test", proxy_urls=(ONE, TWO))
    request = llm_agent.urllib.request.Request(URL, data=b"{}")

    llm_agent._urlopen(config, request)

    assert seen["proxy_urls"] == (ONE, TWO)


def test_config_error_does_not_print_the_password(env):
    """Адрес без схемы — частая ошибка вставки; в сообщении не должно быть доступа."""
    env.setenv("OPENROUTER_PROXY", "login:secret@10.0.0.1:8000")

    with pytest.raises(ValueError) as exc:
        load_config()

    assert "secret" not in str(exc.value)
    assert "login" not in str(exc.value)
