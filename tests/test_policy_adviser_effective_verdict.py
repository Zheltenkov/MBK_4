"""Raw model opinion and code-grounded diagnostic decisions remain distinct."""
import json

import pytest

from dialogue_frame import DialogueFrame
import llm_agent
import policy_semantic_analysis as analysis
from policy_semantics import parse_semantics
from policy_voice_critic import CriticAssessment, SemanticVerdict
from tests.test_policy_semantic_analysis import configuration, stub

BAD = "Алименты и возмещение вреда всё равно остаются."
GOOD = "Алименты и возмещение вреда жизни или здоровью сохраняются."
FAQ = "Чем реструктуризация отличается от списания?"


def frame():
    return DialogueFrame(None, "collected_held", question_goal="debt_explanation")


def test_sent_classifier_contract_requires_preserving_qualification_and_category_scope(monkeypatch):
    calls = stub(monkeypatch, analysis._empty_response())
    analysis.analyze({"latest_user_message": FAQ}, GOOD, frame(), configuration())
    system = calls[0][0][0]["content"]
    assert "квалификаторы" in system and "категорию" in system and "условия" in system
    assert "context.verified_context" in system and "unsupported_claim" in system


def test_sent_classifier_contract_binds_negation_to_its_own_clause(monkeypatch):
    calls = stub(monkeypatch, analysis._empty_response())
    analysis.analyze({"latest_user_message": FAQ}, GOOD, frame(), configuration())
    system = calls[0][0][0]["content"]
    assert "Отрицание действует только" in system and "позднейшую" in system


@pytest.mark.parametrize("draft,has_issue", [(BAD, True), (GOOD, False)])
def test_transport_unavailability_keeps_raw_none_and_only_code_violations_derive_a_rejection(monkeypatch, draft, has_issue):
    def unavailable(*args, **kwargs):
        raise TimeoutError("Synthetic timeout")
    monkeypatch.setattr(llm_agent, "generate_policy_critique", unavailable)
    result = analysis.analyze({"latest_user_message": FAQ}, draft, frame(), configuration())
    assert result.assessment.verdict is None and result.assessment.reason == "model_unavailable"
    assert bool(getattr(result, "code_content_issues", ())) is has_issue
    effective = getattr(result, "effective_verdict", None)
    if has_issue:
        assert effective is not None and not effective.accept and effective.flags == ["unsupported_claim"]
    else:
        assert effective is None


def test_code_diagnosis_uses_the_exact_rendered_body_without_rewriting_raw_model_opinion(monkeypatch):
    calls = stub(monkeypatch, analysis._empty_response())
    result = analysis.analyze({"latest_user_message": FAQ}, BAD, frame(), configuration(), draft_redactor=lambda text: GOOD)
    assert json.loads(calls[0][0][-1]["content"])["draft"] == GOOD
    assert result.assessment.verdict is not None and result.assessment.verdict.accept
    assert result.assessment.reason == "accepted"
    assert getattr(result, "code_content_issues", ()) == ("overbroad_harm_exception",)
    effective = getattr(result, "effective_verdict", None)
    assert effective is not None and not effective.accept and effective.flags == ["unsupported_claim"]


def test_derived_rejection_merges_flags_without_mutating_the_raw_assessment():
    raw = SemanticVerdict(accept=False, flags=["unneeded_question"])
    assessment = CriticAssessment(raw, "synthetic-analysis", "semantic_rejection", True)
    parsed = parse_semantics(json.dumps(analysis._empty_response()), latest_user_message=FAQ, draft=BAD)
    result = analysis.SemanticAnalysis(assessment, parsed, None, code_content_issues=("overbroad_harm_exception",))
    effective = result.effective_verdict
    assert effective is not None and effective.flags == ["unneeded_question", "unsupported_claim"]
    assert assessment.verdict is raw and raw.flags == ["unneeded_question"]
    assert assessment.reason == "semantic_rejection"


def test_original_three_argument_constructor_preserves_the_raw_opinion_when_no_code_issue_exists():
    raw = SemanticVerdict(accept=False, flags=["unneeded_question"])
    assessment = CriticAssessment(raw, "synthetic-analysis", "semantic_rejection", True)
    parsed = parse_semantics(json.dumps(analysis._empty_response()), latest_user_message=FAQ, draft=GOOD)
    result = analysis.SemanticAnalysis(assessment, parsed, None)
    assert getattr(result, "code_content_issues", ()) == ()
    assert getattr(result, "effective_verdict", None) is raw
