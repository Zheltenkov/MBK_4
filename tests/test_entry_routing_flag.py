"""Флаг развилки на входе: включён, пока его не выключили явно (решение 12.09.2026)."""
from __future__ import annotations

from pathlib import Path

import pytest

from config import AppConfig, load_config


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "sk-or-test")


def test_entry_routing_is_on_by_default(monkeypatch):
    monkeypatch.delenv("MBK_ENTRY_ROUTING", raising=False)

    assert load_config().entry_routing_enabled is True
    assert AppConfig(openrouter_api_key="sk-or-test").entry_routing_enabled is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "FALSE"])
def test_entry_routing_switches_off_explicitly(monkeypatch, raw):
    monkeypatch.setenv("MBK_ENTRY_ROUTING", raw)

    assert load_config().entry_routing_enabled is False


@pytest.mark.parametrize("raw", ["1", "true", "yes", ""])
def test_anything_else_keeps_it_on(monkeypatch, raw):
    monkeypatch.setenv("MBK_ENTRY_ROUTING", raw)

    assert load_config().entry_routing_enabled is True


def test_env_templates_list_the_flag_switched_on():
    """Ветка поставки не содержит файлов нашего стенда — проверяем те, что есть."""
    for template in (ROOT / ".env.example", ROOT / "deploy" / "prod" / "env.example"):
        if template.exists():
            assert "MBK_ENTRY_ROUTING=1" in template.read_text(encoding="utf-8"), template
