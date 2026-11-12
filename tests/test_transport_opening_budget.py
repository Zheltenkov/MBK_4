"""One fake-clock opening budget covers proxy failover and stale pooled sockets."""
import http.client
from types import SimpleNamespace

import pytest

import transport
from tests.test_transport_pool import URL, _ConnWithSocket


@pytest.fixture
def clock(monkeypatch):
    current = [100.0]
    monkeypatch.setattr(transport, "time", SimpleNamespace(monotonic=lambda: current[0]), raising=False)

    def forbidden(*args, **kwargs):
        pytest.fail("Opening-budget checks cannot open real connections")

    monkeypatch.setattr(transport.ConnectionPool, "_new_connection", forbidden)
    return current


class TimedConnection(_ConnWithSocket):
    def __init__(self, clock, *, request_cost=0, request_error=None, response_error=None):
        super().__init__(120)
        self.clock = clock
        self.request_cost = request_cost
        self.request_error = request_error
        self.response_error = response_error
        self.request_timeouts = []
        self.response_timeouts = []

    def request(self, *args, **kwargs):
        self.request_timeouts.append(self.sock.timeout)
        super().request(*args, **kwargs)
        self.clock[0] += self.request_cost
        if self.request_error is not None:
            raise self.request_error

    def getresponse(self):
        self.response_timeouts.append(self.sock.timeout)
        if self.response_error is not None:
            raise self.response_error
        return super().getresponse()


def call(pool, **kwargs):
    return pool.request(URL, data=b"{}", headers={}, timeout=10, **kwargs)


@pytest.mark.parametrize("phase", ["request", "response"])
def test_a_pooled_timeout_does_not_replay_the_sent_request_on_a_fresh_socket(monkeypatch, clock, phase):
    error = TimeoutError("synthetic timeout after request began")
    pooled = TimedConnection(clock, request_error=error if phase == "request" else None,
                             response_error=error if phase == "response" else None)
    fresh = []

    def connect(*args):
        fresh.append(TimedConnection(clock))
        return fresh[-1]

    monkeypatch.setattr(transport.ConnectionPool, "_connect", connect)
    pool = transport.ConnectionPool()
    pool.release(("https", "openrouter.ai", None, ""), pooled)
    with pytest.raises(TimeoutError):
        call(pool)
    assert pooled.closed and pooled.requests == 1 and fresh == []


def test_proxy_failover_and_connection_setup_share_the_remaining_opening_budget(monkeypatch, clock):
    seen, conn = [], TimedConnection(clock, request_cost=1)

    def connect(self, target, proxy_url, timeout):
        seen.append((proxy_url, timeout))
        if proxy_url == "http://one:8000":
            clock[0] += 4
            raise ConnectionRefusedError("synthetic first proxy failure")
        clock[0] += 2
        return conn

    monkeypatch.setattr(transport.ConnectionPool, "_connect", connect)
    pool = transport.ConnectionPool()
    with call(pool, proxy_urls=("http://one:8000", "http://two:8000")) as response:
        response.read()
    assert seen == [("http://one:8000", 10), ("http://two:8000", 6)]
    assert conn.request_timeouts == [4] and conn.response_timeouts == [3]
    assert pool._preferred == "http://two:8000"


def test_an_exhausted_proxy_budget_does_not_open_the_next_route(monkeypatch, clock):
    seen = []

    def connect(self, target, proxy_url, timeout):
        seen.append(proxy_url)
        if len(seen) == 1:
            clock[0] += 10
            raise ConnectionRefusedError("synthetic failure at opening deadline")
        return TimedConnection(clock)

    monkeypatch.setattr(transport.ConnectionPool, "_connect", connect)
    with pytest.raises(TimeoutError):
        call(transport.ConnectionPool(), proxy_urls=("http://one:8000", "http://two:8000"))
    assert seen == ["http://one:8000"]


def test_an_immediately_stale_socket_can_recover_only_with_the_same_remaining_budget(monkeypatch, clock):
    stale = TimedConnection(clock, request_cost=3, request_error=http.client.RemoteDisconnected("synthetic stale socket"))
    live, budgets = TimedConnection(clock, request_cost=1), []

    def connect(self, target, proxy_url, timeout):
        budgets.append(timeout)
        clock[0] += 2
        return live

    monkeypatch.setattr(transport.ConnectionPool, "_connect", connect)
    pool = transport.ConnectionPool()
    pool.release(("https", "openrouter.ai", None, ""), stale)
    with call(pool) as response:
        assert response.read() == b"{}"
    assert stale.closed and live.requests == 1
    assert budgets == [7] and live.request_timeouts == [5] and live.response_timeouts == [4]


def test_a_connection_finishing_after_the_budget_is_closed_before_sending(monkeypatch, clock):
    conn = TimedConnection(clock)

    def connect(*args):
        clock[0] += 11
        return conn

    monkeypatch.setattr(transport.ConnectionPool, "_connect", connect)
    with pytest.raises(TimeoutError):
        call(transport.ConnectionPool())
    assert conn.closed and conn.requests == 0


def test_request_time_is_subtracted_before_waiting_for_response_headers(monkeypatch, clock):
    conn = TimedConnection(clock, request_cost=4)
    monkeypatch.setattr(transport.ConnectionPool, "_connect", lambda *args: conn)
    with call(transport.ConnectionPool()) as response:
        response.read()
    assert conn.request_timeouts == [10] and conn.response_timeouts == [6]
