"""F-only availability and fictional scope examples, without model accuracy claims."""
import json

import policy_factual_audit as factual
from dialogue_frame import DialogueFrame
from tests.test_policy_factual_audit import wire
from tests.test_policy_semantic_analysis import configuration
import llm_agent


def test_the_factual_task_explicitly_disables_hidden_reasoning_without_reducing_json_budget(monkeypatch):
    calls = []
    def generate(messages, config, **kwargs):
        calls.append((messages, config, kwargs))
        return json.dumps(wire(messages), ensure_ascii=False)
    monkeypatch.setattr(llm_agent, "generate_policy_critique", generate)
    result = factual.assess({}, "Проверяемое пояснение.",
                            DialogueFrame(None, "collected_held", question_goal="debt_explanation"),
                            configuration(max_tokens=5000))
    assert result.verdict == "supported"
    assert calls[0][2].get("reasoning_effort") == "none"
    assert calls[0][1].max_tokens == calls[0][2]["token_limit"] == 2048
    assert calls[0][2]["generation_name"] == "policy_factual_audit"


def test_two_fictional_scope_contrasts_do_not_copy_legal_development_probes_or_change_v2():
    examples = getattr(factual, "_SCOPE_EXAMPLES", ())
    assert len(examples) == 2
    assert [example["result"]["rows"][0]["relation"] for example in examples] == ["unsupported", "supported"]
    assert examples[0]["result"]["rows"][0]["mismatch"] == "object_scope"
    assert examples[1]["result"]["rows"][0]["mismatch"] == "none"
    for example in examples:
        encoded = json.dumps(example, ensure_ascii=False)
        assert "аккумулятора" in encoded
        assert all(word not in encoded for word in ("банкрот", "алименты", "возмещение вреда"))
        response = factual._FactualResponse.model_validate_json(json.dumps(example["result"]))
        assert len(response.rows) == 1
        assert factual.WIRE_VERSION == "factual-audit-v2"
    assert "только ID из source_catalog текущего запроса" in factual._SYSTEM
