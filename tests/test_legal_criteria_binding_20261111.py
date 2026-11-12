"""Specific assessment criteria need their own supplied evidence, not legal topic overlap."""
import json

import pytest

import llm_agent
import policy_factual_audit as factual
from tests.test_factual_evidence_binding_20261111 import supplied_opinion
from tests.test_policy_factual_audit import frame
from tests.test_policy_semantic_analysis import configuration


CRITERIA = "Суд проверяет добросовестность и платёжеспособность."
GENERAL = "Для судебного банкротства оцениваются условия закона."
LIVE = ("Хорошо, обсудим здесь.\n\n"
        "При сумме 400 000 ₽ списание возможно через судебное банкротство по закону о несостоятельности. "
        "Это не гарантия, а процедура с условиями — суд проверяет добросовестность и платёжеспособность.\n\n"
        "Что вас сейчас больше всего беспокоит в этой ситуации?")


def test_exact_previous_raw_supported_cannot_authorize_criteria_absent_from_current_catalog(monkeypatch):
    raw = {"rows": [
        {"mismatch": "none", "relation": "no_assertion", "source_ids": [], "unit_id": "u1-31b590d91b68"},
        {"mismatch": "none", "relation": "supported", "source_ids": [
            "bankruptcy_basics:s1-44de5ee15fca", "bankruptcy_basics:s2-b16cc4d841cd",
            "bankruptcy_basics:s3-5953114d949e", "bankruptcy_basics:s7-c23adee4d7fa",
            "bankruptcy_basics:s9-e8a0025a8dac"], "unit_id": "u2-762e84b18bc5"},
        {"mismatch": "none", "relation": "no_assertion", "source_ids": [], "unit_id": "u3-6102c72f4f13"},
    ]}
    calls = []
    def generate(*args, **kwargs):
        calls.append(True)
        return json.dumps(raw, ensure_ascii=False)
    monkeypatch.setattr(llm_agent, "generate_policy_critique", generate)
    result = factual.assess({}, LIVE, frame(), configuration())
    assert result.verdict == "unknown" and result.reason == "evidence_scope_uncovered"
    assert not result.attempted and not calls and not result.rows
    assert result.evidence_issues


def test_specific_criteria_select_f_even_without_bankruptcy_keyword():
    assert factual.requires_audit(frame(None), CRITERIA)


@pytest.mark.parametrize("draft", [CRITERIA, "Финансовый управляющий оценивает имущество и доходы."])
def test_general_law_conditions_are_not_concrete_assessment_evidence(monkeypatch, draft):
    result, calls = supplied_opinion(monkeypatch, draft, {"bankruptcy_basics": GENERAL})
    assert result.verdict == "unknown" and result.reason == "evidence_scope_uncovered"
    assert not result.attempted and not calls


@pytest.mark.parametrize("source", [GENERAL, CRITERIA,
    "Специалист оценивает долговую нагрузку.",
    "При судебном банкротстве суд проверяет добросовестность.",
    "Суд может проверять добросовестность.",
    "Суд не проверяет платёжеспособность.",
])
def test_exact_synthetic_or_general_source_remains_usable(monkeypatch, source):
    result, calls = supplied_opinion(monkeypatch, source, {"bankruptcy_basics": source})
    assert result.verdict == "supported" and len(calls) == 1
    assert result.rows[0].relation == "supported" and not result.evidence_issues


def test_one_criterion_does_not_cover_a_second_criterion(monkeypatch):
    result, calls = supplied_opinion(monkeypatch, CRITERIA,
                                    {"bankruptcy_basics": "Суд проверяет добросовестность."})
    assert result.verdict == "unknown" and not calls


@pytest.mark.parametrize("relation", ["supported", "no_assertion"])
def test_cited_definition_cannot_hide_criteria_even_when_other_source_can_cover_them(monkeypatch, relation):
    result, calls = supplied_opinion(monkeypatch, CRITERIA,
        {"bankruptcy_basics": GENERAL + " " + CRITERIA}, citation_ordinals=(1,), relations=(relation,))
    assert result.verdict == "unknown" and result.reason == "evidence_scope_uncovered"
    assert len(calls) == 1 and result.rows[0].relation == relation
    assert result.evidence_issues


@pytest.mark.parametrize("source,draft", [
    ("Суд может проверять добросовестность.", "Суд проверяет добросовестность."),
    ("Суд проверяет добросовестность.", "Суд не проверяет добросовестность."),
    ("Если есть доход, суд проверяет добросовестность.", "Суд проверяет добросовестность."),
    ("При судебном банкротстве суд проверяет добросовестность.",
     "При внесудебном банкротстве суд проверяет добросовестность."),
    ("Специалист проверяет добросовестность.", "Суд проверяет добросовестность."),
])
def test_matching_words_do_not_erase_modality_negation_condition_procedure_or_actor(monkeypatch, source, draft):
    result, _ = supplied_opinion(monkeypatch, draft, {"bankruptcy_basics": source})
    assert result.verdict == "unknown" and result.evidence_issues


@pytest.mark.parametrize("draft", [
    "Что проверяет суд при банкротстве?",
    "Вы спрашиваете: «Суд проверяет добросовестность и платёжеспособность».",
    "Укажите номер телефона для связи.",
    "Проверим номер телефона.",
    "Телефон уже есть.",
])
def test_quotes_questions_and_administrative_collection_do_not_gain_assessment_claims(draft):
    assert not factual.requires_audit(frame(None), draft)


def test_generic_law_fact_from_current_catalog_retains_exact_source_identity(monkeypatch):
    result, _ = supplied_opinion(monkeypatch, GENERAL,
                                {"bankruptcy_basics": factual.prompts.BANKRUPTCY_EXPLANATION}, citation_ordinals=(7,))
    assert result.verdict == "supported"
    cited = result.source_catalog[6]
    assert cited.id == "bankruptcy_basics:s7-c23adee4d7fa" and cited.text == GENERAL
