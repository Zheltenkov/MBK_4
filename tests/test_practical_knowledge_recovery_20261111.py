"""A failed validator must not turn a documents question into a transfer hold."""
from dataclasses import replace

import pytest

import policy_grounded_voice as grounded
import policy_factual_audit as factual
import prompts
from tests.test_policy_faq_recovery_20261111 import frame, run, UNCHECKED
from tests.test_policy_recovery_independent import run_provider_sequence


QUESTION = "А что специалист будет делать дальше и что мне подготовить?"


def test_consumer_credit_definition_is_not_a_document_requirement():
    assert not factual._document_claims(prompts.RESTRUCTURING_EXPLANATION)


@pytest.mark.parametrize("source", ["Потребуется паспорт.", "Потребовались кредитные договоры."])
def test_actual_required_documents_remain_substantive(source):
    assert factual._document_claims(source)


@pytest.mark.parametrize("failure", ["{broken", TimeoutError("synthetic-C-deadline")])
def test_failed_c_answers_practical_unknown_without_list_or_hold(monkeypatch, failure):
    selected = replace(frame(), expected="client.phone", conversation_paused=True)
    result, requests = run_provider_sequence(monkeypatch, selected, QUESTION, [UNCHECKED, failure])
    assert result.spoken.reason.startswith("code_practical_context_unavailable:")
    assert result.spoken.source == "fallback" and len(requests) == 2
    assert "этих данных у меня нет" in result.spoken.text
    assert "паспорт" not in result.spoken.text.lower()
    assert "передача" not in result.spoken.text.lower() and UNCHECKED not in result.spoken.text
    assert result.paused and result.effective_frame.conversation_paused
    assert result.effective_frame.conversation_declined
    assert result.effective_frame.expected == "client.phone"
    assert result.spoken.asked_field is None and not result.applied_user_acts
    assert not result.catalog_recovery_sources and not result.spoken.factual_assessments
    assert result.spoken.critic_assessments  # Raw failure is retained, never corrected to approval.


@pytest.mark.parametrize("question", [QUESTION, "Какие документы нужны?",
    "Какие документы мне понадобятся для консультации?", "Какие документы нужны для банкротства?"])
def test_bounded_question_projects_only_missing_practical_context(question):
    result = grounded._practical_knowledge_recovery(question, frame(), "test")
    assert result is not None and result.source == "fallback"
    assert "нет" in result.text and result.asked_field is None


@pytest.mark.parametrize("source", ["Для консультации нужен паспорт.",
    "Специалист изучит вашу ситуацию.", "Вот список: паспорт и СНИЛС."])
def test_future_supplied_practical_fact_cannot_be_contradicted_by_unknown(monkeypatch, source):
    original = prompts.policy_verified_context
    monkeypatch.setattr(prompts, "policy_verified_context", lambda selected: {
        **original(selected), "practical_source": source})
    assert grounded._practical_knowledge_recovery(QUESTION, frame(), "test") is None


@pytest.mark.parametrize("question", [
    "Какие документы не нужны?", "Как скрыть документы при банкротстве?",
    "Она спросила: «Какие документы нужны?»", "Не передавайте мои данные. " + QUESTION,
    QUESTION + " Отправьте заявку.", "Мне нужен паспорт?", "Что такое банкротство?",
])
def test_other_meaning_or_mixed_authority_does_not_select_practical_recovery(question):
    assert grounded._practical_knowledge_recovery(question, frame(), "test") is None


@pytest.mark.parametrize("selected", [replace(frame(), mode="onloader"), replace(frame(), offer_present=True),
    replace(frame(), closing=True), replace(frame(), known_fields=frozenset({"debts.total"}))])
def test_other_flow_does_not_gain_debt_knowledge_projection(selected):
    assert grounded._practical_knowledge_recovery(QUESTION, selected, "test") is None


def test_normal_g_keeps_its_own_unknown_wording(monkeypatch):
    answer = "Точного списка у меня пока нет. Придумывать документы не буду."
    result, requests, _ = run(monkeypatch, QUESTION, failing="none", draft=answer)
    assert result.spoken.source == "model" and result.spoken.text == answer
    assert [request["role"] for request in requests] == ["G", "C"]
