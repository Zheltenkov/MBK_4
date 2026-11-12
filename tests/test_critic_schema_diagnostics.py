"""Private-safe schema failure classes from the real critic parser, without network."""
from dataclasses import asdict
import json

import pytest
from pydantic import TypeAdapter, ValidationError

import llm_agent
from config import AppConfig
from dialogue_frame import DialogueFrame
import policy_voice_critic as critic

pytestmark = pytest.mark.usefixtures("real_policy_voice_critic")


@pytest.mark.parametrize(("raw", "code", "field"), [
    ('{"accept":"PRIVATE_BOOL","flags":[]}', "bool_type", "accept"),
    ('{"flags":[]}', "missing", "accept"),
    ('{"accept":false,"flags":["PRIVATE_FLAG"]}', "literal_error", "flags_item"),
    ('{"accept":true,"flags":[],"PRIVATE_FIELD":"PRIVATE_VALUE"}', "extra_forbidden", "unknown"),
    ('{"accept":true,"flags":["unsupported_claim"]}', "value_error", "root"),
    ('{"accept":false,"flags":["unsupported_claim","unsupported_claim"]}', "value_error", "root"),
    ('{"accept":true,"flags":"PRIVATE_FLAGS"}', "list_type", "flags"),
    ('[]', "model_type", "root"),
    ('PRIVATE_BROKEN_JSON{', "json_invalid", "root"),
    (json.dumps({"accept": False, "flags": ["unsupported_claim"] * 6}), "too_long", "flags"),
])
def test_actual_critic_schema_errors_have_only_closed_codes_and_field_classes(monkeypatch, raw, code, field):
    monkeypatch.setattr(llm_agent, "generate_policy_critique", lambda *args, **kwargs: raw)
    config = AppConfig(openrouter_api_key="synthetic-unused", extractor_model="synthetic-distinct-critic")
    assessment = critic.assess({}, "Можно продолжить здесь.", DialogueFrame(None, "collected_held"), config)
    trace = [{"code": issue.code, "field": issue.field, "count": issue.count}
             for issue in getattr(assessment, "schema_issues", ())]
    assert trace == [{"code": code, "field": field, "count": 1}]
    assert assessment.verdict is None and assessment.reason == "invalid_schema"
    assert assessment.failure.kind == "invalid_schema"
    assert "PRIVATE_" not in json.dumps(asdict(assessment), ensure_ascii=False)


def test_unknown_extra_names_are_aggregated_and_capped_without_becoming_paths(monkeypatch):
    raw = json.dumps({"accept": True, "flags": [], **{f"PRIVATE_FIELD_{i}": "PRIVATE_VALUE" for i in range(50)}})
    monkeypatch.setattr(llm_agent, "generate_policy_critique", lambda *args, **kwargs: raw)
    assessment = critic.assess({}, "Ответ.", DialogueFrame(None, "collected_held"),
                              AppConfig(openrouter_api_key="synthetic-unused", extractor_model="synthetic-critic"))
    trace = [{"code": issue.code, "field": issue.field, "count": issue.count}
             for issue in getattr(assessment, "schema_issues", ())]
    assert trace == [{"code": "extra_forbidden", "field": "unknown", "count": 16}]
    assert len(trace) <= 8
    assert "PRIVATE_" not in json.dumps(asdict(assessment))


def test_existing_assessment_constructor_defaults_to_no_schema_diagnostics():
    assessment = critic.CriticAssessment(None, "synthetic-critic", "model_unavailable", True)
    assert getattr(assessment, "schema_issues", None) == ()


def test_unrecognized_pydantic_codes_use_a_closed_fallback_without_error_messages():
    with pytest.raises(ValidationError) as failure:
        TypeAdapter(int).validate_python("PRIVATE_UNRECOGNIZED_INPUT")
    issues = critic._schema_issue_summary(failure.value)
    assert [asdict(issue) for issue in issues] == [{"code": "other", "field": "root", "count": 1}]
    assert "PRIVATE_" not in json.dumps([asdict(issue) for issue in issues])
