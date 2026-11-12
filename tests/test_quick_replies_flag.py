"""Флаг чипсов: включён, пока не выключен явно, и работает только с развилкой."""
from __future__ import annotations

from pathlib import Path

import pytest

import config
from config import AppConfig, load_config


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "sk-or-test")
    monkeypatch.delenv("MBK_QUICK_REPLIES", raising=False)


def test_quick_replies_are_on_by_default():
    assert load_config().quick_replies_enabled is True
    assert AppConfig(openrouter_api_key="sk-or-test").quick_replies_enabled is True
    assert config.quick_replies_active() is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "OFF"])
def test_quick_replies_switch_off_explicitly(monkeypatch, raw):
    monkeypatch.setenv("MBK_QUICK_REPLIES", raw)

    assert load_config().quick_replies_enabled is False
    assert config.quick_replies_active() is False


@pytest.mark.parametrize("raw", ["1", "true", "yes", ""])
def test_anything_else_keeps_them_on(monkeypatch, raw):
    monkeypatch.setenv("MBK_QUICK_REPLIES", raw)

    assert load_config().quick_replies_enabled is True


def test_chips_need_entry_routing(monkeypatch):
    """Без развилки нажатие «Другой вопрос» некому обработать."""
    monkeypatch.setenv("MBK_ENTRY_ROUTING", "0")

    assert config.quick_replies_active() is False


def test_env_templates_list_the_flag_switched_on():
    """Ветка поставки не содержит файлов нашего стенда — проверяем те, что есть."""
    for template in (ROOT / ".env.example", ROOT / "deploy" / "prod" / "env.example"):
        if template.exists():
            assert "MBK_QUICK_REPLIES=1" in template.read_text(encoding="utf-8"), template
