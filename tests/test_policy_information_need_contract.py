"""The optional adviser hint has one closed sender/parser contract and private-safe diagnostics."""
from dataclasses import asdict
from copy import deepcopy
import json

import pytest

from dialogue_frame import DialogueFrame
import policy_semantic_analysis as analysis
import policy_semantics as semantics
import prompts
from tests.test_policy_semantic_analysis import configuration, offered_frame, response, stub


def parse_need(need):
    latest = "Не передавать. Для чего сведения?"
    value = response(latest="Не передавать.")
    value["user"]["information_need"] = need
    return semantics.parse_semantics(json.dumps(value, ensure_ascii=False),
                                     latest_user_message=latest, draft="Можем общаться здесь.")


def test_sender_nullable_schema_is_derived_from_the_same_two_kind_parser_contract():
    context = analysis._public_context(DialogueFrame(None, "collected_held", question_goal="debt_amount_purpose"))
    contract = context.get("information_need_contract")
    assert isinstance(contract, dict)
    assert contract["allowed_kinds"] == ["transfer_purpose", "debt_explanation"]
    schema = contract["nullable_schema"]
    assert schema == {"anyOf": [semantics.InformationNeed.model_json_schema(), {"type": "null"}]}
    model = schema["anyOf"][0]
    assert model["additionalProperties"] is False and set(model["required"]) == {"kind", "evidence"}
    assert model["properties"]["evidence"]["minLength"] == 1
    assert model["properties"]["evidence"]["maxLength"] == 400
    assert tuple(getattr(semantics, "INFORMATION_NEED_KINDS", ())) == tuple(contract["allowed_kinds"])
    assert "debt_amount_purpose" not in contract["allowed_kinds"]
    assert context["question_goal"] == "debt_amount_purpose" and context["question_goal_origin"] == "code"


@pytest.mark.parametrize("need,code,field", [
    ({"kind": "PRIVATE_INVALID_KIND", "evidence": "Для чего сведения?"}, "literal_error", "kind"),
    ({"evidence": "Для чего сведения?"}, "missing", "kind"),
    ({"kind": "transfer_purpose", "evidence": 123}, "string_type", "evidence"),
    ({"kind": "transfer_purpose", "evidence": "PRIVATE_TOO_LONG" * 40}, "string_too_long", "evidence"),
    ({"kind": "transfer_purpose", "evidence": "Для чего сведения?", "PRIVATE_KEY": "PRIVATE_VALUE"}, "extra_forbidden", "unknown"),
    ("PRIVATE_WRONG_SHAPE", "model_type", "root"),
])
def test_optional_schema_failure_has_closed_diagnostics_without_poisoning_a_valid_refusal(need, code, field):
    parsed = parse_need(need)
    assert parsed.need_error == "invalid_schema" and parsed.user_error is None and parsed.claims_error is None
    assert parsed.user is not None and parsed.user.information_need is None and parsed.claims is not None
    issues = [asdict(issue) for issue in getattr(parsed, "need_schema_issues", ())]
    assert issues == [{"code": code, "field": field, "count": 1}]
    assert "PRIVATE_" not in json.dumps(issues)
    authority = semantics.TurnAuthority.from_frame(offered_frame(), "Не передавать. Для чего сведения?", "Можем общаться здесь.")
    decision = semantics.evaluate_semantics(authority, parsed)
    assert decision.safe and decision.effective_frame.conversation_declined


def test_unknown_code_goal_is_rejected_as_a_hint_and_does_not_change_code_authority():
    latest = "Я уже сказала сумму. Зачем вам её знать?"
    draft = "От размера долга зависит, какой путь обсуждать."
    value = response()
    value["user"] = {"acts": [], "information_need": {"kind": "debt_amount_purpose", "evidence": "Зачем вам её знать?"}}
    parsed = semantics.parse_semantics(json.dumps(value, ensure_ascii=False), latest_user_message=latest, draft=draft)
    assert parsed.need_error == "invalid_schema" and parsed.user is not None and parsed.user.information_need is None
    assert [asdict(i) for i in getattr(parsed, "need_schema_issues", ())] == [{"code": "literal_error", "field": "kind", "count": 1}]
    frame = DialogueFrame("client.region_class", "collecting_debt_context", question_goal="debt_amount_purpose")
    decision = semantics.evaluate_semantics(semantics.TurnAuthority.from_frame(frame, latest, draft), parsed)
    assert decision.safe and decision.effective_frame.trusted_question_goal == "debt_amount_purpose"
    assert not decision.applied_user_acts


def test_a_duplicate_optional_hint_stays_invalid_and_has_only_a_closed_field_class():
    value = response(latest="Не передавать.")
    raw = json.dumps(value).replace('"information_need": null', '"information_need": null, "information_need": {}')
    parsed = semantics.parse_semantics(raw, latest_user_message="Не передавать.", draft="Можем общаться здесь.")
    assert parsed.need_error == "invalid_schema" and parsed.user is not None and parsed.claims is not None
    assert [asdict(i) for i in getattr(parsed, "need_schema_issues", ())] == [{"code": "duplicate_field", "field": "root", "count": 1}]


def test_schema_diagnostics_are_bounded_and_never_include_unknown_extra_names():
    need = {"kind": "transfer_purpose", "evidence": "Для чего сведения?",
            **{f"PRIVATE_FIELD_{n}": "PRIVATE_VALUE" for n in range(50)}}
    parsed = parse_need(need)
    issues = [asdict(i) for i in getattr(parsed, "need_schema_issues", ())]
    assert issues == [{"code": "extra_forbidden", "field": "unknown", "count": 16}]
    assert "PRIVATE_" not in json.dumps(issues)


@pytest.mark.parametrize("need", [None, {"kind": "transfer_purpose", "evidence": "Для чего сведения?"}])
def test_a_valid_or_absent_hint_has_no_schema_diagnostics(need):
    parsed = parse_need(need)
    assert parsed.need_error is None and getattr(parsed, "need_schema_issues", ()) == ()


def test_combined_adviser_receives_the_shared_verified_facts_without_private_input_fields(monkeypatch):
    facts = {"bankruptcy_basics": "PUBLIC_VERIFIED_RULE"}
    monkeypatch.setattr(prompts, "policy_verified_context", lambda frame: deepcopy(facts))
    calls = stub(monkeypatch, response())
    analysis.analyze({"latest_user_message": "Не передавать.",
                      "facts": {"client": {"name": "PRIVATE_NAME", "phone": "+79991234567"}}},
                     "Можем общаться здесь.", offered_frame(), configuration())
    wire = json.loads(calls[0][0][-1]["content"])
    assert wire["context"].get("verified_context") == facts
    assert wire["context"].get("information_need_contract") is not None
    assert "PRIVATE_NAME" not in json.dumps(wire) and "+79991234567" not in json.dumps(wire)
    assert "context.verified_context" in calls[0][0][0]["content"]
    assert "unsupported_claim" in calls[0][0][0]["content"]


def test_optional_need_failure_does_not_masquerade_as_a_bad_style_or_valid_information_hint(monkeypatch):
    value = response()
    value["user"]["information_need"] = {"kind": "PRIVATE_KIND", "evidence": "Не передавать."}
    stub(monkeypatch, value)
    result = analysis.analyze({"latest_user_message": "Не передавать."}, "Можем общаться здесь.", offered_frame(), configuration())
    assert result.assessment.reason == "accepted" and result.assessment.verdict.accept
    assert result.semantics.need_error == "invalid_schema" and result.semantics.user.information_need is None
    assert [asdict(i) for i in getattr(result.semantics, "need_schema_issues", ())] == [{"code": "literal_error", "field": "kind", "count": 1}]


@pytest.mark.parametrize("deferred", [False, True])
def test_the_code_owned_followup_deferral_is_only_a_public_readonly_context_field(deferred):
    frame = DialogueFrame(None, "collected_held", defer_discussion_followup=deferred)
    context = analysis._public_context(frame)
    assert context.get("defer_discussion_followup") is deferred
    assert frame.defer_discussion_followup is deferred
    assert context["question_goal_origin"] == "code"
