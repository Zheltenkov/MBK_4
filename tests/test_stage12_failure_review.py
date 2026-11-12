"""Independent failure taxonomy proof through the actual diagnostic adapter."""
from __future__ import annotations

from io import BytesIO
import json
from urllib.error import HTTPError

import pytest

from config import AppConfig
from dialogue_frame import DialogueFrame
import llm_agent
import policy_voice_critic


pytestmark = pytest.mark.usefixtures("real_policy_voice_critic")
PRIVATE = "SYNTHETIC_PRIVATE_RESPONSE_DO_NOT_RECORD"


@pytest.mark.parametrize("fault,kind,phase,status", [
    ("http", "http", "open", 429),
    ("open_timeout", "timeout", "open", None),
    ("read_timeout", "timeout", "read", None),
    ("invalid_json", "invalid_json", "decode", None),
    ("empty_response", "empty_response", "response", None),
    ("invalid_schema", "invalid_schema", "schema", None),
])
def test_real_diagnostic_failures_keep_safe_cause_and_phase_without_retrying(
    monkeypatch, fault, kind, phase, status,
):
    requests = []

    class Response(BytesIO):
        def read(self, *args, **kwargs):
            if fault == "read_timeout":
                raise TimeoutError(PRIVATE)
            return super().read(*args, **kwargs)

    def open_request(configuration, request):
        requests.append(json.loads(request.data))
        if fault == "http":
            raise HTTPError("https://synthetic.invalid/" + PRIVATE, 429, PRIVATE, {}, BytesIO(PRIVATE.encode()))
        if fault == "open_timeout":
            raise TimeoutError(PRIVATE)
        if fault == "invalid_json":
            return Response(PRIVATE.encode())
        content = "" if fault == "empty_response" else json.dumps({"accept": "yes", "flags": []})
        return Response(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    configuration = AppConfig(openrouter_api_key="synthetic-unused", extractor_model="synthetic-critic")
    result = policy_voice_critic.assess(
        {"latest_user_message": "Можно продолжить разговор?", "chat_history": []},
        "Конечно, можем обсудить вопросы здесь.", DialogueFrame(None, "collected_held"), configuration,
    )

    assert result.verdict is None and result.attempted is True
    assert len(requests) == 1
    failure = getattr(result, "failure", None)
    assert failure is not None
    assert (failure.kind, failure.phase, failure.http_status) == (kind, phase, status)
    assert isinstance(result.elapsed_ms, int) and result.elapsed_ms >= 0
    assert PRIVATE not in repr(result)
    assert len(requests[0]["messages"]) == 2
    assert requests[0]["model"] == "synthetic-critic"


def test_a_context_failure_is_distinct_from_an_attempted_model_failure(monkeypatch):
    def no_transport(*args, **kwargs):
        pytest.fail("A failed context cannot reach the model transport")

    def bad_context(draft):
        raise ValueError(PRIVATE)

    monkeypatch.setattr(llm_agent, "_urlopen", no_transport)
    result = policy_voice_critic.assess(
        {}, "Можем продолжить здесь.", DialogueFrame(None, "collected_held"),
        AppConfig(openrouter_api_key="synthetic-unused", extractor_model="synthetic-critic"),
        draft_redactor=bad_context,
    )
    assert result.verdict is None and result.attempted is False
    failure = getattr(result, "failure", None)
    assert failure is not None and (failure.kind, failure.phase) == ("context", "context")
    assert PRIVATE not in repr(result)
