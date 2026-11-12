"""Native schema requests are scoped to F and preserve existing provider routing."""
import pytest

from config import AppConfig
import llm_agent


SCHEMA = {"type": "object", "properties": {"rows": {"type": "array", "items": {"type": "string"}}},
          "required": ["rows"], "additionalProperties": False}


def test_native_factual_schema_preserves_routing_and_single_request(monkeypatch):
    requests = []

    def post(config, body, **kwargs):
        requests.append((body, kwargs))
        return {"choices": [{"message": {"content": '{"rows":[]}'}}]}

    monkeypatch.setattr(llm_agent, "_post", post)
    config = AppConfig(openrouter_api_key="synthetic-unused", model="deepseek/deepseek-v4-pro",
                       provider_sort="latency", max_tokens=2048)
    assert llm_agent.generate_policy_critique(
        [], config, token_limit=2048, generation_name="policy_factual_audit",
        reasoning_effort="none", response_schema=SCHEMA,
    ) == '{"rows":[]}'
    body, options = requests[0]
    assert len(requests) == 1 and options["max_attempts"] == 1
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert body["provider"] == {"sort": "latency", "require_parameters": True}
    assert body["reasoning"]["effort"] == "none" and body["max_tokens"] == 2048


def test_the_native_schema_override_is_not_available_to_the_style_task():
    with pytest.raises(ValueError):
        llm_agent.generate_policy_critique(
            [], AppConfig(openrouter_api_key="synthetic-unused"), response_schema=SCHEMA,
        )


def test_the_existing_style_json_request_keeps_its_previous_body(monkeypatch):
    requests = []
    monkeypatch.setattr(llm_agent, "_post", lambda config, body, **kwargs: (
        requests.append(body) or {"choices": [{"message": {"content": "{}"}}]}
    ))
    llm_agent.generate_policy_critique([], AppConfig(openrouter_api_key="synthetic-unused"))
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert "provider" not in requests[0]
