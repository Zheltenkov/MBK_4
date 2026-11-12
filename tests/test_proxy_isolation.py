"""Через прокси ходит только модель.

Прокси нужен одному адресату — зарубежному провайдеру модели. CRM заказчика и
DaData живут в российском сегменте, и через зарубежный сервер эти запросы либо
не пройдут вовсе, либо пойдут кругом.

Опасность в том, что выбор делается не нами: `urllib.request.urlopen` читает
системные HTTPS_PROXY и HTTP_PROXY и подставляет их молча. Пропишет
администратор прокси системно, а не в нашей переменной, — и запросы во
внутреннюю CRM уедут за границу. Выглядеть это будет как «бот перестал
создавать лиды», и искать причину будут в коде лида.
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

import transport


ROOT = Path(__file__).resolve().parent.parent


def test_direct_opener_ignores_system_proxies(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://посторонний:8000")
    monkeypatch.setenv("HTTP_PROXY", "http://посторонний:8000")

    # Системные прокси подставляет ProxyHandler и только он. Опенер собран с
    # пустым обработчиком, поэтому в цепочке его нет вовсе — подставлять
    # переменные окружения оказывается некому.
    proxying = [
        h for h in transport._DIRECT_OPENER.handlers
        if isinstance(h, urllib.request.ProxyHandler) and h.proxies
    ]

    assert not proxying, "в прямом опенере не должно быть ни одного прокси"


def test_outbound_calls_go_through_the_direct_opener():
    """Страж: новый вызов наружу не должен снова открыть дверь прокси.

    Проверяется структурно, а не поведением: поймать это на живом запросе можно
    только на сервере заказчика, где прокси и настроен.
    """
    guarded = {
        "delivery/crm.py": "CRM заказчика",
        "geo/dadata.py": "DaData",
        "delivery/microservice.py": "маршрутизирующий микросервис",
    }
    offenders = []
    for name, who in guarded.items():
        source = (ROOT / name).read_text(encoding="utf-8")
        if "urllib.request.urlopen(" in source:
            offenders.append(f"{name} ({who})")

    assert not offenders, (
        "Прямой urlopen подхватывает системный прокси: "
        + ", ".join(offenders)
        + ". Используйте transport.direct_urlopen."
    )
