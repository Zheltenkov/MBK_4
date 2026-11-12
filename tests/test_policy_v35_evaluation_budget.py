"""Evaluation preserves operator caps while budgeting independent model roles."""
from contextlib import contextmanager
from dataclasses import replace
from io import BytesIO
import json
import sys

import pytest

from config import AppConfig
from dialogue_frame import DialogueFrame
from evaluation import local_style_eval as evaluation
import llm_agent
import policy_factual_audit
import policy_grounded_voice
from tests.test_policy_grounded_voice import semantic_raw


def configured(**changes):
    return AppConfig(openrouter_api_key="synthetic-unused", model="synthetic-speaker",
                     extractor_model="deepseek/deepseek-v4-pro", max_tokens=5000, request_timeout=120,
                     policy_voice_enabled=True, policy_semantic_enabled=True,
                     policy_factual_audit_enabled=True, **changes)


@pytest.mark.parametrize("factual,tokens,seconds,expected_tokens,expected_seconds", [
    (True, 5000, 120, 2048, 40), (False, 5000, 120, 650, 30),
    (True, 512, 3, 512, 3), (False, 512, 3, 512, 3),
])
def test_local_config_keeps_low_operator_limits_and_old_default_off_behavior(
    factual, tokens, seconds, expected_tokens, expected_seconds,
):
    helper = getattr(evaluation, "local_evaluation_config", None)
    assert callable(helper)
    original = replace(configured(), policy_factual_audit_enabled=factual, max_tokens=tokens, request_timeout=seconds)
    result = helper(original)
    assert result.max_tokens == expected_tokens and result.request_timeout == expected_seconds
    assert original.max_tokens == tokens and original.request_timeout == seconds
    assert result.model == original.model and result.extractor_model == original.extractor_model


def test_main_entry_uses_the_loaded_factual_opt_in_before_configuring_the_run(monkeypatch, tmp_path):
    captures = []
    monkeypatch.setattr(evaluation, "load_config", configured)
    @contextmanager
    def boundary(config, **kwargs):
        captures.append(config)
        yield {"model_http_requests": 0, "delivery_attempts": 0}
    monkeypatch.setattr(evaluation, "service_boundaries", boundary)
    monkeypatch.setattr(evaluation, "run_case", lambda *args, **kwargs: {
        "id": "targeted_debt_detours", "error_class": None, "contract_passed": True,
        "hard_contract_acceptance_eligible": True, "voice_quality_eligible": True,
        "usage": {"total_calls": 0, "total_cost_usd": 0, "by_role": {}},
    })
    monkeypatch.setattr(sys, "argv", ["evaluation.local_style_eval", "--out", str(tmp_path / "run.json"),
                                    "--ids", "targeted_debt_detours", "--max-turns", "12"])
    assert evaluation.main() == 0
    assert len(captures) == 1 and captures[0].policy_factual_audit_enabled
    assert captures[0].max_tokens == 2048 and captures[0].request_timeout == 40


def test_actual_grounded_http_requests_keep_g650_c1000_and_f2048(monkeypatch):
    helper = getattr(evaluation, "local_evaluation_config", None)
    assert callable(helper)
    requests = []
    reply = "При реструктуризации меняют условия потребительского кредита. Сам долг остаётся."
    def open_request(settings, request):
        body = json.loads(request.data)
        requests.append(body)
        if body["model"] == "synthetic-speaker":
            content = reply
        elif "source_catalog" in json.loads(body["messages"][-1]["content"]):
            factual_input = json.loads(body["messages"][-1]["content"])
            fact_id = next(fact["id"] for fact in factual_input["source_catalog"]
                           if fact["source_id"] == "consumer_credit_restructuring")
            content = json.dumps({"rows": [{"unit_id": unit["id"], "relation": "supported",
                                            "source_ids": [fact_id], "mismatch": "none"}
                                           for unit in factual_input["draft_units"]]})
        else:
            content = semantic_raw()
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    outcome = policy_grounded_voice.render(
        {"latest_user_message": "Что такое реструктуризация?"}, helper(configured()),
        frame=DialogueFrame(None, "collected_held", question_goal="debt_explanation"),
        fallback="Проверенное резервное пояснение.",
    )
    assert outcome.spoken.text == reply and len(requests) == 3
    assert [body["max_tokens"] for body in requests] == [650, 1000, 2048]
    assert requests[1]["reasoning"]["effort"] == "none"
    assert requests[2]["reasoning"]["effort"] == "none"


def test_actual_factual_wire_preserves_an_explicit_lower_operator_cap(monkeypatch):
    captured = []
    def open_request(settings, request):
        body = json.loads(request.data)
        captured.append((settings, body))
        factual_input = json.loads(body["messages"][-1]["content"])
        source_id = factual_input["source_catalog"][0]["id"]
        content = json.dumps({"rows": [{"unit_id": unit["id"], "relation": "supported",
                                        "source_ids": [source_id], "mismatch": "none"}
                                       for unit in factual_input["draft_units"]]})
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    settings = evaluation.local_evaluation_config(replace(configured(), max_tokens=512, request_timeout=3))
    result = policy_factual_audit.assess({}, "Проверяемое пояснение.",
                                       DialogueFrame(None, "collected_held", question_goal="debt_explanation"), settings)
    assert result.verdict == "supported" and len(captured) == 1
    actual, body = captured[0]
    assert 0 < actual.request_timeout <= 3 and body["max_tokens"] == 512
    assert body["reasoning"]["effort"] == "none"
