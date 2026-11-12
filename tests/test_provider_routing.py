"""Запросы к модели уходят к самому отзывчивому провайдеру OpenRouter.

Замер 25.09.2026: у извлекателя фактов (deepseek-v4-pro) одна и та же модель
у разных провайдеров отвечает то за 3 секунды, то за 12. Клиент всё это время
смотрит на многоточие. С сортировкой по задержке медиана почти та же, но
худший случай упал с 12,0 до 5,5 секунды. Модель и промпт не меняются —
меняется только, кому OpenRouter отдаёт запрос.
"""
from __future__ import annotations

import json
from dataclasses import replace

import llm_agent
from config import AppConfig, load_config


CONFIG = AppConfig(openrouter_api_key="sk-or-test")


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _sent_body(monkeypatch, config: AppConfig, payload: dict) -> dict:
    seen: list[dict] = []

    def _capture(cfg, request):
        seen.append(json.loads(request.data.decode("utf-8")))
        return _Response({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(llm_agent, "_urlopen", _capture)
    llm_agent._post(config, payload, max_attempts=1)
    return seen[0]


def test_requests_ask_for_the_lowest_latency_provider(monkeypatch):
    body = _sent_body(monkeypatch, CONFIG, {"model": "deepseek/deepseek-v4-pro", "messages": []})

    assert body["provider"] == {"sort": "latency"}


def test_routing_can_be_switched_off(monkeypatch):
    """Российский шлюз может не пропустить поле, которое понимает только OpenRouter."""
    config = replace(CONFIG, provider_sort=None)

    body = _sent_body(monkeypatch, config, {"model": "m", "messages": []})

    assert "provider" not in body


def test_explicit_provider_choice_is_kept(monkeypatch):
    body = _sent_body(
        monkeypatch, CONFIG, {"model": "m", "messages": [], "provider": {"order": ["Baidu"]}}
    )

    assert body["provider"] == {"order": ["Baidu"]}


def test_setting_reads_from_environment(monkeypatch):
    monkeypatch.setenv("MBK_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("OPENROUTER_PROVIDER_SORT", "off")
    assert load_config().provider_sort is None

    monkeypatch.setenv("OPENROUTER_PROVIDER_SORT", "throughput")
    assert load_config().provider_sort == "throughput"

    monkeypatch.delenv("OPENROUTER_PROVIDER_SORT")
    assert load_config().provider_sort == "latency"
