"""Polza configuration must isolate credentials and bypass stale proxy settings."""
import json
from dataclasses import replace
from io import BytesIO
import urllib.request

import pytest

from config import load_config
import llm_agent


@pytest.fixture
def env(monkeypatch):
    for name in (
        "MBK_LLM_PROVIDER", "POLZA_API_KEY", "POLZA_MODEL", "POLZA_EXTRACTOR_MODEL",
        "OPEN_ROUTER_API_KEY", "OPENROUTER_API_KEY", "OPENROUTER_MODEL",
        "OPENROUTER_EXTRACTOR_MODEL", "OPENROUTER_BASE_URL", "OPENROUTER_PROXY",
        "HTTP_PROXY", "HTTPS_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def polza(env):
    env.setenv("MBK_LLM_PROVIDER", "polza")
    env.setenv("POLZA_API_KEY", "pza_synthetic_unused")
    return load_config()


def test_polza_key_endpoint_and_model_roles(env):
    config = polza(env)
    assert config.llm_provider == "polza"
    assert config.openrouter_api_key == "pza_synthetic_unused"
    assert config.base_url == "https://polza.ai/api/v1"
    assert config.model == "qwen/qwen3.7-max"
    assert config.extractor_model == "deepseek/deepseek-v4-pro"


def test_polza_never_uses_old_openrouter_credentials(env):
    env.setenv("MBK_LLM_PROVIDER", "polza")
    env.setenv("OPEN_ROUTER_API_KEY", "sk-or-private-synthetic")
    with pytest.raises(ValueError, match="POLZA_API_KEY") as error:
        load_config()
    assert "private-synthetic" not in str(error.value)


def test_polza_ignores_all_inherited_proxy_and_old_gateway_settings(env):
    env.setenv("OPENROUTER_PROXY", "socks5://old:private@unreachable.invalid:9999")
    env.setenv("HTTPS_PROXY", "http://system:private@unreachable.invalid:9999")
    env.setenv("HTTP_PROXY", "http://other.invalid:3128")
    env.setenv("OPENROUTER_BASE_URL", "https://old-gateway.invalid/v1")
    config = polza(env)
    assert config.proxy_urls == ()
    assert config.base_url == "https://polza.ai/api/v1"


def test_explicit_polza_models_take_precedence_over_legacy_tuning(env):
    env.setenv("POLZA_MODEL", "qwen/qwen3.7-max")
    env.setenv("POLZA_EXTRACTOR_MODEL", "deepseek/deepseek-v4.1-flash")
    env.setenv("OPENROUTER_MODEL", "old-speaker")
    env.setenv("OPENROUTER_EXTRACTOR_MODEL", "old-extractor")
    config = polza(env)
    assert config.model == "qwen/qwen3.7-max"
    assert config.extractor_model == "deepseek/deepseek-v4.1-flash"


def test_legacy_openrouter_configuration_keeps_its_validation_and_proxy(env):
    env.setenv("OPEN_ROUTER_API_KEY", "sk-or-synthetic")
    env.setenv("OPENROUTER_PROXY", "http://proxy.invalid:3128")
    config = load_config()
    assert config.llm_provider == "openrouter"
    assert config.base_url == "https://openrouter.ai/api/v1"
    assert config.proxy_urls == ("http://proxy.invalid:3128",)
    env.setenv("OPEN_ROUTER_API_KEY", "pza_synthetic_unused")
    with pytest.raises(ValueError, match="OpenRouter"):
        load_config()


def test_unknown_provider_cannot_silently_use_a_different_key(env):
    env.setenv("MBK_LLM_PROVIDER", "unknown")
    env.setenv("OPEN_ROUTER_API_KEY", "sk-or-synthetic")
    with pytest.raises(ValueError, match="MBK_LLM_PROVIDER"):
        load_config()


def test_polza_transport_rejects_a_stale_proxy_carried_by_a_copied_config(env, monkeypatch):
    config = replace(polza(env), proxy_urls=("http://stale.invalid:3128",))
    captured = []
    marker = object()

    def request(*args, **kwargs):
        captured.append(kwargs)
        return marker

    monkeypatch.setattr(llm_agent.transport.POOL, "request", request)
    wire = urllib.request.Request(config.base_url + "/chat/completions", data=b'{}')
    assert llm_agent._urlopen(config, wire) is marker
    assert captured[0]["proxy_urls"] == ()


@pytest.mark.parametrize("provider", ["polza", "openrouter"])
def test_structured_factual_request_uses_the_selected_service_without_losing_schema(env, monkeypatch, provider):
    env.setenv("MBK_LLM_PROVIDER", provider)
    env.setenv("POLZA_API_KEY", "pza_synthetic_unused")
    env.setenv("OPEN_ROUTER_API_KEY", "sk-or-synthetic")
    config = load_config()
    requests = []

    def capture(configuration, request):
        requests.append((configuration, request, json.loads(request.data)))
        return BytesIO(json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode())

    monkeypatch.setattr(llm_agent, "_urlopen", capture)
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    llm_agent.generate_policy_critique(
        [{"role": "user", "content": "Synthetic schema check"}], config,
        generation_name="policy_factual_audit", response_schema=schema, reasoning_effort="high",
    )
    actual, request, body = requests[0]
    assert len(requests) == 1 and request.full_url == config.base_url + "/chat/completions"
    assert request.get_header("Authorization") == "Bearer " + config.openrouter_api_key
    assert body["response_format"]["json_schema"]["schema"] == schema
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["reasoning"] == {"effort": "high", "exclude": True}
    assert body["provider"].get("require_parameters") is (True if provider == "openrouter" else None)
    if provider == "polza":
        assert actual.proxy_urls == ()
