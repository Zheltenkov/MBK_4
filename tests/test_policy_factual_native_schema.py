"""Native factual output shape is exercised through the real request builder."""
from io import BytesIO
import json

import llm_agent
import policy_factual_audit as factual
from tests.test_policy_factual_audit import frame, wire
from tests.test_policy_semantic_analysis import configuration


def test_factual_adapter_sends_its_parser_schema_as_the_native_response_contract(monkeypatch):
    requests = []

    def transport(config, request):
        body = json.loads(request.data)
        requests.append(body)
        # Synthetic rows exercise the unchanged strict parser after transport.
        value = wire(body["messages"])
        response = BytesIO(json.dumps({"choices": [{"message": {
            "content": json.dumps(value, ensure_ascii=False),
        }}]}).encode("utf-8"))
        response.status = 200
        return response

    monkeypatch.setattr(llm_agent, "_urlopen", transport)
    monkeypatch.setattr(llm_agent.observability, "_get_client", lambda: None)
    result = factual.assess({}, "Возможна реструктуризация.", frame(),
                            configuration(provider_sort="price"))

    assert result.verdict == "supported" and len(requests) == 1
    body = requests[0]
    assert body["response_format"]["type"] == "json_schema"
    native = body["response_format"]["json_schema"]
    assert native["name"] and native["strict"] is True
    assert native["schema"] == factual._FactualResponse.model_json_schema()
    assert body["provider"]["require_parameters"] is True
    assert body["provider"]["sort"] == "price"
    assert body["model"] == "synthetic-extractor"
    assert body["reasoning"] == {"effort": "none", "exclude": True}
    assert body["include_reasoning"] is False
    assert body["max_tokens"] == 2000
