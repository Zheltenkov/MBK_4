"""HTTP-транспорт к модели с переиспользованием соединений.

Замер 19.08.2026 через прокси заказчика: пустячный запрос к API — 1,7 секунды,
он же по уже открытому соединению — 0,47. Разница уходит на TCP, туннель через
прокси и TLS-рукопожатие, и раньше мы платили её на каждый вызов, дважды за ход
диалога.

`urllib` держать соединение не умеет: каждый `urlopen` открывает новое. Поэтому
здесь тонкая обёртка над `http.client` с пулом живых соединений. Наружу она
выглядит как прежний `urlopen`: тот же объект ответа с `read()`, построчной
итерацией для SSE и `urllib.error.HTTPError` на неуспешном коде — чтобы разбор
ошибок и ретраи в llm_agent остались нетронутыми.

Соединение возвращается в пул, только если ответ дочитан до конца и сервер не
попросил закрыться. Всё остальное закрывается: переиспользовать соединение с
недочитанным телом нельзя, следующий запрос получил бы чужие байты.
"""
from __future__ import annotations

import base64
import http.client
import io
import logging
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit


logger = logging.getLogger(__name__)


# Сколько живых соединений держим на один адрес. Больше двух не нужно:
# одновременно идут разговорный вызов и извлекатель, третий занять некому.
MAX_IDLE_PER_TARGET = 2


# Правило, а не список знакомых классов: каждый раз, когда мы перечисляли
# знакомые, очередной незнакомый пролетал мимо `except`.
#
# `urllib` заворачивает в `URLError` только отправку запроса (`do_open`), а
# `getresponse()` и `read()` отдают то, что пришло с сокета, как есть. Обрыв
# соединения приходит то как `RemoteDisconnected`, то как `ssl.SSLError` (оба
# `OSError`), то как `IncompleteRead` — а это уже `HTTPException`, и никакой
# `OSError` его не поймает. Два корня и покрывают всё, что транспорт способен
# бросить вместо ответа.
#
# `urllib.error.HTTPError` тоже сюда попадает (он наследник `URLError`), но это
# ответ сервера, а не сбой связи: его разбирают отдельной веткой ВЫШЕ.
TRANSPORT_ERRORS = (OSError, http.client.HTTPException)


# Потолок на подключение: TCP до прокси, туннель и TLS — это секунды. Без
# своего потолка подключение ждало бы таймаут всего вызова (у разговорного
# 120 секунд), и мёртвый прокси стоил бы клиенту двух минут тишины перед
# переходом на запасной.
CONNECT_TIMEOUT = 10


class ResponseDeadlineExceeded(TimeoutError):
    """The absolute HTTP budget expired, independently of socket inactivity."""


class _ProxyUnavailable(Exception):
    """Через этот прокси не подключиться: соединение, туннель или 407."""

    def __init__(self, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.cause = cause


class _ResponseDeadline:
    """Interrupt blocking HTTP framing without a background response-reading worker."""

    def __init__(self, deadline: float, sock: socket.socket | None) -> None:
        self.deadline = deadline
        self.expired = threading.Event()
        self._sock = sock
        self._timer: threading.Timer | None = None
        if sock is not None:
            self._timer = threading.Timer(_remaining(deadline), self._expire)
            self._timer.name = "mbk-http-deadline"
            self._timer.daemon = True
            self._timer.start()

    def _expire(self) -> None:
        self.expired.set()
        # shutdown interrupts buffered/chunked reads too. Closing the reader here
        # could deadlock against its read lock, so its owning thread closes it.
        if self._sock is not None:
            try:
                # Avoid SSLSocket.shutdown mutating TLS state in the read thread.
                socket.socket.shutdown(self._sock, socket.SHUT_RDWR)
            except OSError:
                pass

    def check(self) -> None:
        if self.expired.is_set() or time.monotonic() >= self.deadline:
            raise ResponseDeadlineExceeded("HTTP response deadline exhausted")

    def finish(self) -> None:
        # Joining matters: a callback already running must finish before reuse.
        if self._timer is not None:
            self._timer.cancel()
            self._timer.join()


class _PooledResponse:
    """Ответ, который по закрытии возвращает соединение в пул."""

    def __init__(self, pool: "ConnectionPool", key: tuple, conn, raw,
                 deadline: _ResponseDeadline | None = None) -> None:
        self._pool = pool
        self._key = key
        self._conn = conn
        self._raw = raw
        self._released = False
        self._deadline = deadline

    def read(self, *args, **kwargs):
        try:
            if self._deadline is not None:
                self._deadline.check()
            body = self._raw.read(*args, **kwargs)
            if self._deadline is not None:
                self._deadline.check()
            return body
        except BaseException:
            # An interrupted read never qualifies as a fully consumed response.
            self.close(discard=True)
            if self._deadline is not None:
                self._deadline.check()
            raise

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)

    def __iter__(self):
        return iter(self._raw)

    def __enter__(self) -> "_PooledResponse":
        return self

    def __exit__(self, *exc_info) -> bool:
        self.close()
        return False

    def close(self, *, discard: bool = False) -> None:
        if self._released:
            return
        self._released = True
        if self._deadline is not None:
            self._deadline.finish()
            discard = discard or self._deadline.expired.is_set() or time.monotonic() >= self._deadline.deadline
        reusable = False
        try:
            reusable = not discard and bool(self._raw.isclosed()) and not self._raw.will_close
        except Exception:  # noqa: BLE001 — состояние ответа не должно ронять вызов
            reusable = False
        if reusable:
            self._pool.release(self._key, self._conn)
            return
        for target in (self._raw, self._conn):
            try:
                target.close()
            except Exception:  # noqa: BLE001
                pass


class ConnectionPool:
    """Живые соединения по адресу назначения (и прокси, если он задан)."""

    def __init__(self) -> None:
        self._idle: dict[tuple, list[Any]] = {}
        self._lock = threading.Lock()
        # Прокси, через который последний раз удалось подключиться. С него
        # начинается следующий вызов: мёртвый первый в списке не должен стоить
        # попытки каждому запросу.
        self._preferred: str | None = None

    def request(
        self,
        url: str,
        *,
        data: bytes,
        headers: dict[str, str],
        timeout: float,
        proxy_url: str | None = None,
        proxy_urls: tuple[str, ...] | list[str] = (),
        method: str = "POST",
        streaming: bool = False,
    ):
        """Выполняет запрос через первый живой прокси из списка.

        Прод 25.09.2026: единственный прокси к модели упал, и бот двое суток
        отвечал только приветствием. Теперь отказ подключиться через прокси
        (соединение, туннель, 407) переводит запрос на следующий. Всё, что
        случилось после подключения, — ответ модели с ошибкой или обрыв на
        запросе, — остаётся ретраям llm_agent: прокси тут ни при чём, а если
        он всё-таки умер, повтор не сможет к нему подключиться и уйдёт дальше.
        """
        deadline = time.monotonic() + timeout
        proxies = tuple(proxy_urls) or ((proxy_url,) if proxy_url else ())
        if len(proxies) <= 1:
            return self._request_via(
                url, data, headers, timeout, proxies[0] if proxies else None, method,
                failover=False, deadline=deadline, streaming=streaming,
            )

        last_error: BaseException | None = None
        for proxy in self._proxy_order(proxies):
            try:
                response = self._request_via(
                    url, data, headers, timeout, proxy, method, failover=True, deadline=deadline,
                    streaming=streaming,
                )
            except _ProxyUnavailable as exc:
                last_error = exc.cause
                # Номер, а не адрес: в адресе лежат логин и пароль.
                logger.warning(
                    "Прокси к модели %d из %d недоступен (%s): пробую следующий",
                    proxies.index(proxy) + 1,
                    len(proxies),
                    type(exc.cause).__name__,
                )
                continue
            self._preferred = proxy
            return response
        logger.error("Все прокси к модели недоступны: %d из %d", len(proxies), len(proxies))
        assert last_error is not None
        raise last_error

    def _proxy_order(self, proxies: tuple[str, ...]) -> list[str]:
        if self._preferred in proxies:
            return [self._preferred, *(p for p in proxies if p != self._preferred)]
        return list(proxies)

    def _request_via(
        self,
        url: str,
        data: bytes,
        headers: dict[str, str],
        timeout: float,
        proxy_url: str | None,
        method: str,
        *,
        failover: bool,
        deadline: float | None = None,
        streaming: bool = False,
    ):
        """Запрос через один прокси (или напрямую), переиспользуя соединение.

        Соединение из пула могло быть закрыто с той стороны, пока лежало без
        дела. Такой отказ виден до получения ответа и означает лишь «возьми
        новое» — поэтому одна повторная попытка на свежем соединении делается
        здесь, а не считается сбоем вызова.
        """
        deadline = time.monotonic() + timeout if deadline is None else deadline
        target = urlsplit(url)
        key = (target.scheme, target.hostname, target.port, proxy_url or "")
        path = target.path or "/"
        if target.query:
            path = f"{path}?{target.query}"

        for pooled in (True, False):
            remaining = _remaining(deadline)
            conn = self._acquire(key) if pooled else None
            if pooled and conn is None:
                continue
            if conn is None:
                try:
                    conn = self._connect(target, proxy_url, remaining)
                except TRANSPORT_ERRORS as exc:
                    if failover:
                        raise _ProxyUnavailable(exc) from exc
                    raise
            response_deadline: _ResponseDeadline | None = None
            raw = None
            try:
                # Setup, a stale socket and proxy failover spend one opening budget.
                _apply_timeout(conn, _remaining(deadline))
                # Capture before getresponse: Connection:close can detach conn.sock.
                if not streaming:
                    response_deadline = _ResponseDeadline(deadline, getattr(conn, "sock", None))
                conn.request(method, path, body=data, headers=headers)
                _apply_timeout(conn, _remaining(deadline))
                raw = conn.getresponse()
                if response_deadline is not None:
                    response_deadline.check()
            except BaseException as exc:
                if response_deadline is not None:
                    response_deadline.finish()
                if raw is not None:
                    try:
                        raw.close()
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
                if response_deadline is not None:
                    response_deadline.check()
                if pooled and isinstance(exc, Exception) and not isinstance(exc, TimeoutError):
                    # Соединение протухло, пока лежало в пуле. Пробуем новое.
                    continue
                raise

            response = _PooledResponse(self, key, conn, raw, response_deadline)
            if raw.status >= 400:
                try:
                    body = response.read()
                finally:
                    response.close(discard=True)
                raise urllib.error.HTTPError(
                    url, raw.status, raw.reason, raw.headers, io.BytesIO(body)
                )
            return response

        raise RuntimeError("не удалось выполнить запрос")

    def release(self, key: tuple, conn) -> None:
        with self._lock:
            idle = self._idle.setdefault(key, [])
            if len(idle) >= MAX_IDLE_PER_TARGET:
                extra = conn
            else:
                idle.append(conn)
                return
        try:
            extra.close()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ #

    def _acquire(self, key: tuple):
        with self._lock:
            idle = self._idle.get(key) or []
            if idle:
                return idle.pop()
        return None

    def _connect(self, target, proxy_url: str | None, timeout: float):
        """Открывает соединение сразу, а не на первом запросе.

        Отказ прокси должен случиться здесь — до отправки тела, пока запрос
        можно без вреда отдать запасному прокси. Подключение идёт с коротким
        потолком, дальше соединение живёт с таймаутом самого вызова.
        """
        conn = self._new_connection(target, proxy_url, min(CONNECT_TIMEOUT, timeout))
        try:
            conn.connect()
        except BaseException:
            conn.close()
            raise
        _apply_timeout(conn, timeout)
        return conn

    def _new_connection(self, target, proxy_url: str | None, timeout: float):
        secure = target.scheme != "http"
        host = target.hostname or ""
        port = target.port or (443 if secure else 80)

        if not proxy_url:
            if secure:
                return http.client.HTTPSConnection(
                    host, port, timeout=timeout, context=ssl.create_default_context()
                )
            return http.client.HTTPConnection(host, port, timeout=timeout)

        proxy = urlsplit(proxy_url)
        proxy_host = proxy.hostname or ""
        proxy_port = proxy.port or 8080
        tunnel_headers: dict[str, str] = {}
        if proxy.username:
            credentials = f"{proxy.username}:{proxy.password or ''}".encode()
            tunnel_headers["Proxy-Authorization"] = (
                "Basic " + base64.b64encode(credentials).decode()
            )
        if secure:
            conn = http.client.HTTPSConnection(
                proxy_host,
                proxy_port,
                timeout=timeout,
                context=ssl.create_default_context(),
            )
        else:
            conn = http.client.HTTPConnection(proxy_host, proxy_port, timeout=timeout)
        conn.set_tunnel(host, port, headers=tunnel_headers)
        return conn


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ResponseDeadlineExceeded("HTTP opening deadline exhausted")
    return remaining


def _apply_timeout(conn, timeout: float) -> None:
    """Переносит потолок ожидания на уже открытое соединение."""
    conn.timeout = timeout
    sock = getattr(conn, "sock", None)
    if sock is not None:
        try:
            sock.settimeout(timeout)
        except OSError:  # noqa: BLE001 — соединение всё равно будет проверено запросом
            pass


POOL = ConnectionPool()


# Прокси нужен ровно одному адресату — зарубежному провайдеру модели. Всё
# остальное (CRM заказчика, DaData) живёт в российском сегменте, и через
# зарубежный сервер эти запросы либо не пройдут вовсе, либо пойдут кругом.
#
# Само по себе это не наш выбор: `urllib.request.urlopen` читает системные
# HTTPS_PROXY и HTTP_PROXY и подставляет их молча. Стоит администратору
# прописать прокси системно, а не в нашей переменной, — и запросы во внутреннюю
# CRM уедут за границу. Выглядеть это будет как «бот перестал создавать лиды».
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def direct_urlopen(request: urllib.request.Request, timeout: float):
    """Запрос в обход любых прокси, системных в том числе."""
    return _DIRECT_OPENER.open(request, timeout=timeout)
