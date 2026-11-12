from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from routing import load_matrix  # noqa: E402


@pytest.fixture
def real_policy_voice_critic():
    """Opt diagnostic/transport tests into the real assessment adapter boundary."""
    return None


@pytest.fixture(autouse=True)
def offline_semantic_critic(request, monkeypatch):
    """Existing core unit tests double this new external dependency explicitly.

    This is simulated acceptance, not evidence about a real critic. Focused
    schema, privacy and transport tests opt out through real_policy_voice_critic.
    """
    if "real_policy_voice_critic" in request.fixturenames:
        return
    import policy_voice_critic

    def accept(*args, **kwargs):
        return policy_voice_critic.CriticAssessment(
            policy_voice_critic.SemanticVerdict(accept=True, flags=[]),
            "synthetic-offline-critic", "accepted", True,
        )

    monkeypatch.setattr(policy_voice_critic, "assess", accept)


@pytest.fixture(autouse=True)
def predictable_environment(monkeypatch):
    """Убирает влияние .env на тесты, которые про поведение, а не про настройки.

    Потолок обращений к справочнику адресов — счётчик у класса, общий на весь
    прогон: с боевым значением из .env первые же тесты его исчерпывали, и
    дальше падало всё, что трогает DaData. Число доверенных прокси меняет
    разбор X-Forwarded-For, то есть адрес посетителя.

    Тест, который проверяет саму настройку, задаёт её сам — monkeypatch внутри
    теста сильнее этой фикстуры.
    """
    from geo.dadata import DadataClient

    monkeypatch.setenv("MBK_DADATA_REQUEST_BUDGET", "1000000")
    monkeypatch.delenv("MBK_TRUSTED_PROXY_HOPS", raising=False)
    # Флаг развилки из локального .env не должен решать за тесты: без
    # переменной действует умолчание кода (развилка включена). Тест, которому
    # нужно поведение без развилки, передаёт entry_routing_enabled=False сам.
    monkeypatch.delenv("MBK_ENTRY_ROUTING", raising=False)
    # Чипсы — так же: действует умолчание кода (включены).
    monkeypatch.delenv("MBK_QUICK_REPLIES", raising=False)
    # Тесты паузы согласия считают от умолчания кода (5 минут). В .env.example
    # стоит 900 секунд, и прогон по инструкции `cp .env.example .env` ронял их.
    monkeypatch.delenv("MBK_SITE_CONSENT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(DadataClient, "_request_count", 0)
    monkeypatch.setattr(DadataClient, "_window_started", 0.0)


@pytest.fixture()
def matrix():
    """Свежая матрица на каждый тест — маршруты мутируются загрузчиком (enabled)."""
    return load_matrix()
