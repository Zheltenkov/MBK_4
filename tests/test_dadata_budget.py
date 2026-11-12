"""Бюджет обращений к справочнику адресов: защита, а не выключатель.

Счётчик жил столько же, сколько процесс, и не обнулялся никогда. В проде
контейнер работает неделями: после исчерпания лимита ни один клиент больше не
получал регион, а без региона CRM не присваивает категорию — лид выглядел как
«не подошёл». Предохранитель от разогнавшегося цикла превращался в тихую
поломку, причём видимую только в логе.
"""
from __future__ import annotations

import pytest

from geo.dadata import DadataClient, DadataRequestError


@pytest.fixture(autouse=True)
def fresh_counter(monkeypatch):
    """Счётчик у класса общий — каждый тест начинает с чистого окна."""
    monkeypatch.setattr(DadataClient, "_request_count", 0)
    monkeypatch.setattr(DadataClient, "_window_started", 0.0)


def _client(monkeypatch, budget: str) -> DadataClient:
    monkeypatch.setenv("MBK_DADATA_REQUEST_BUDGET", budget)
    return DadataClient(token="test-token")


def test_the_budget_stops_a_runaway_loop(monkeypatch):
    client = _client(monkeypatch, "2")
    monkeypatch.setattr("geo.dadata.time.monotonic", lambda: 100.0)

    client._consume_budget()
    client._consume_budget()

    with pytest.raises(DadataRequestError, match="MBK_DADATA_REQUEST_BUDGET"):
        client._consume_budget()


def test_the_budget_starts_over_the_next_day(monkeypatch):
    """Иначе одна авария выключает регионы до перезапуска контейнера."""
    client = _client(monkeypatch, "2")
    now = {"value": 100.0}
    monkeypatch.setattr("geo.dadata.time.monotonic", lambda: now["value"])

    client._consume_budget()
    client._consume_budget()
    with pytest.raises(DadataRequestError):
        client._consume_budget()

    now["value"] += 24 * 60 * 60 + 1

    client._consume_budget()  # новое окно — считаем заново


def test_the_window_does_not_reset_within_the_day(monkeypatch):
    client = _client(monkeypatch, "1")
    now = {"value": 100.0}
    monkeypatch.setattr("geo.dadata.time.monotonic", lambda: now["value"])

    client._consume_budget()
    now["value"] += 23 * 60 * 60

    with pytest.raises(DadataRequestError):
        client._consume_budget()


def test_zero_still_forbids_every_request(monkeypatch):
    """Ноль — осознанный запрет обращений, и суточное окно его не смягчает."""
    client = _client(monkeypatch, "0")
    monkeypatch.setattr("geo.dadata.time.monotonic", lambda: 100.0)

    with pytest.raises(DadataRequestError):
        client._consume_budget()


def test_the_default_budget_is_large_enough_for_a_working_day(monkeypatch):
    """Умолчание не должно останавливать нормальный поток обращений."""
    monkeypatch.delenv("MBK_DADATA_REQUEST_BUDGET", raising=False)

    assert DadataClient(token="test-token")._request_budget >= 1000
