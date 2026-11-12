"""Reasoning is a bounded factual-task choice, not a global model change."""
import pytest
import io
from urllib.error import HTTPError

from config import AppConfig
import llm_agent


@pytest.mark.parametrize("role,effort,expected_cap", [
    ("policy_factual_audit", "high", 2048),
    ("policy_voice_critic", None, 1000),
])
def test_actual_request_keeps_reasoning_scoped_and_the_requested_output_budget(
    monkeypatch, role, effort, expected_cap,
):
    requests = []

    def post(settings, body, **kwargs):
        requests.append((body, kwargs))
        return {"choices": [{"message": {"content": "{}"}}], "usage": {}}

    monkeypatch.setattr(llm_agent, "_post", post)
    llm_agent.generate_policy_critique(
        [], AppConfig(openrouter_api_key="synthetic-unused", model="deepseek/deepseek-v4-pro", max_tokens=5000),
        token_limit=2048, generation_name=role, reasoning_effort=effort,
    )
    body, kwargs = requests[0]
    assert body["reasoning"]["effort"] == (effort or "none")
    assert body["reasoning"]["exclude"] is True and body["include_reasoning"] is False
    assert body["max_tokens"] == expected_cap and kwargs["max_attempts"] == 1


def test_a_factual_reasoning_override_cannot_change_the_extraction_or_style_task():
    with pytest.raises(ValueError):
        llm_agent.generate_policy_critique(
            [], AppConfig(openrouter_api_key="synthetic-unused"), reasoning_effort="high",
        )


def test_a_rejected_factual_override_does_not_change_reasoning_defaults_for_other_tasks(monkeypatch):
    monkeypatch.setattr(llm_agent, "_REJECTS_REASONING_PARAM", set())

    def reject(settings, request):
        raise HTTPError(request.full_url, 400, "Synthetic rejection", {},
                        io.BytesIO(b'{"error":{"message":"reasoning unsupported"}}'))

    monkeypatch.setattr(llm_agent, "_urlopen", reject)
    with pytest.raises(llm_agent.ModelRequestError):
        llm_agent.generate_policy_critique(
            [], AppConfig(openrouter_api_key="synthetic-unused", model="deepseek/deepseek-v4-pro"),
            generation_name="policy_factual_audit", reasoning_effort="high",
        )
    assert llm_agent._REJECTS_REASONING_PARAM == set()
