"""Adversarial supplied F opinions test code binding, never model accuracy."""
from __future__ import annotations

import json

import pytest

import policy_factual_audit as factual
from tests.test_policy_factual_audit import frame
from tests.test_policy_semantic_analysis import configuration


DEFINITION = "Реструктуризация потребительского кредита — изменение срока или графика платежей."


def supplied_opinion(monkeypatch: pytest.MonkeyPatch, draft: str, sources: dict[str, str], *,
                     relations: tuple[str, ...] = (), citation_count: int | None = None,
                     citation_ordinals: tuple[int, ...] = ()):
    """Supply a deliberately fallible audit while exercising real parsing/binding."""
    monkeypatch.setattr(factual.prompts, "policy_verified_context", lambda _: sources)
    calls = []

    def generate(messages, config, **kwargs):
        calls.append((messages, config, kwargs))
        request = json.loads(messages[-1]["content"])
        source_ids = [source["id"] for source in request["source_catalog"]
                      if not citation_ordinals or source["ordinal"] in citation_ordinals][:(citation_count or 6)]
        return json.dumps({"rows": [
            {"unit_id": unit["id"], "relation": relation,
             "source_ids": [] if relation == "no_assertion" else source_ids,
             "mismatch": "none"}
            for index, unit in enumerate(request["draft_units"])
            for relation in [relations[index] if relations else "supported"]
        ]}, ensure_ascii=False)

    monkeypatch.setattr(factual.llm_agent, "generate_policy_critique", generate)
    result = factual.assess({}, draft, frame(), configuration(max_tokens=4000, request_timeout=30))
    return result, calls


@pytest.mark.parametrize("draft", [
    "Реструктуризацию оформляет сам банк, мы этим не занимаемся.",
    "МБК не оформляет реструктуризацию.",
    "Мы не занимаемся реструктуризацией.",
    "Реструктуризацию оформляет только банк.",
    "Наша компания проводит реструктуризацию.",
    "Реструктуризация — изменение графика платежей. МБК этим не занимается.",
    "Реструктуризацию здесь не оформляют.",
    "Мы не помогаем с реструктуризацией.",
    "У нас нет услуги реструктуризации.",
])
def test_definition_does_not_cover_service_actor_even_when_F_says_supported(monkeypatch, draft):
    result, calls = supplied_opinion(monkeypatch, draft, {"consumer_credit_restructuring": DEFINITION})
    assert result.verdict != "supported"
    assert not result.attempted and not calls and not result.rows
    assert result.evidence_issues


@pytest.mark.parametrize("source,draft", [
    ("Гарантия — обязательство устранять заводские дефекты.", "Магазин ремонтирует устройства."),
    ("Консультация — разъяснение условий услуги.", "Клиника предоставляет бесплатные консультации."),
    ("Замена — предоставление другой детали вместо неисправной.", "Сервис заменяет аккумуляторы."),
])
def test_definition_actor_gap_is_not_specific_to_bankruptcy(monkeypatch, source, draft):
    result, _ = supplied_opinion(monkeypatch, draft, {"consumer_credit_restructuring": source})
    assert result.verdict != "supported" and result.evidence_issues


@pytest.mark.parametrize("source,draft", [
    ("МБК не оформляет реструктуризацию.", "МБК не оформляет реструктуризацию."),
    ("Сервис ремонтирует телефоны.", "Сервис ремонтирует телефоны."),
    ("Сервис не только ремонтирует телефоны, но и заменяет аккумуляторы.", "Сервис ремонтирует телефоны."),
    ("Банк оформляет реструктуризацию.", "Реструктуризацию оформляет банк."),
    ("Сервис ремонтирует телефоны, а магазин может заменять аккумуляторы.", "Сервис ремонтирует телефоны."),
])
def test_actual_actor_evidence_can_support_unforced_word_order(monkeypatch, source, draft):
    result, _ = supplied_opinion(monkeypatch, draft, {"consumer_credit_restructuring": source})
    assert result.verdict == "supported" and not result.evidence_issues


@pytest.mark.parametrize("source,draft", [
    ("Сервис не ремонтирует телефоны.", "Сервис ремонтирует телефоны."),
    ("Сервис ремонтирует телефоны.", "Сервис не ремонтирует телефоны."),
    ("Сервис ремонтирует телефоны при подтверждённом заводском дефекте.", "Сервис ремонтирует телефоны."),
    ("Сервис ремонтирует телефоны.", "Сервис ремонтирует любые устройства."),
    ("Сервис может ремонтировать телефоны.", "Сервис ремонтирует телефоны."),
    ("Сервис ремонтирует телефоны.", "Только сервис ремонтирует телефоны."),
    ("Банк оформляет кредиты.", "Реструктуризацию оформляет банк."),
    ("Если есть гарантия, сервис ремонтирует телефоны.", "Если нет гарантии, сервис ремонтирует телефоны."),
])
def test_false_supported_does_not_override_polarity_conditions_object_or_modality(monkeypatch, source, draft):
    result, _ = supplied_opinion(monkeypatch, draft, {"consumer_credit_restructuring": source})
    assert result.verdict != "supported" and result.evidence_issues


def test_each_actor_assertion_needs_coverage_and_cited_sources_can_form_union(monkeypatch):
    sources = {"consumer_credit_restructuring": "Сервис ремонтирует телефоны. Сервис заменяет аккумуляторы."}
    draft = "Сервис ремонтирует телефоны. Сервис заменяет аккумуляторы."
    result, _ = supplied_opinion(monkeypatch, draft, sources)
    assert result.verdict == "supported"
    missing, _ = supplied_opinion(monkeypatch, draft, sources, citation_count=1)
    assert missing.verdict != "supported"


def test_no_assertion_row_cannot_hide_actor_claim_beside_supported_definition(monkeypatch):
    result, _ = supplied_opinion(monkeypatch, DEFINITION + "\n\nМБК не оформляет реструктуризацию.",
                                {"consumer_credit_restructuring": DEFINITION + " МБК не оформляет реструктуризацию."},
                                relations=("supported", "no_assertion"))
    assert result.verdict != "supported" and result.evidence_issues
    assert result.rows[1].relation == "no_assertion"


def test_wrong_definition_citation_preserves_raw_supported_but_effectively_abstains(monkeypatch):
    result, calls = supplied_opinion(monkeypatch, "МБК не оформляет реструктуризацию.",
                                    {"consumer_credit_restructuring": DEFINITION + " МБК не оформляет реструктуризацию."},
                                    citation_count=1)
    assert len(calls) == 1 and result.attempted
    assert result.rows[0].relation == "supported"
    assert result.verdict == "unknown" and result.reason == "evidence_scope_uncovered"
    assert result.evidence_issues[0].kind == "actor_scope"


def test_potentially_covered_actor_still_requires_the_real_F_comparison(monkeypatch):
    result, calls = supplied_opinion(monkeypatch, "Сервис ремонтирует телефоны.",
                                    {"consumer_credit_restructuring": "Сервис ремонтирует телефоны."})
    assert result.verdict == "supported" and result.attempted and len(calls) == 1


def test_bankruptcy_procedure_is_not_mistyped_as_a_bank_service_actor(monkeypatch):
    statement = "Судебное банкротство проводится по правилам закона."
    assert factual._actor_claims(statement) == ()
    assert factual._actor_claims("Банк проводит реструктуризацию.")
    result, calls = supplied_opinion(monkeypatch, statement, {"bankruptcy_basics": statement})
    assert result.verdict == "supported" and result.attempted and len(calls) == 1


@pytest.mark.parametrize("mention", ["МБК оформляет реструктуризацию?", '«МБК оформляет реструктуризацию».',
                                    '"МБК оформляет реструктуризацию".', "'МБК оформляет реструктуризацию'."])
def test_quoted_or_questioned_actor_text_does_not_become_own_service_assertion(monkeypatch, mention):
    result, _ = supplied_opinion(monkeypatch, DEFINITION + "\n\n" + mention,
                                {"consumer_credit_restructuring": DEFINITION},
                                relations=("supported", "no_assertion"))
    assert result.verdict == "supported"


def test_source_context_overflow_abstains_before_model_without_truncation(monkeypatch):
    monkeypatch.setattr(factual.prompts, "policy_verified_context", lambda _: {
        "consumer_credit_restructuring": "Источник. " * 4000})
    monkeypatch.setattr(factual.llm_agent, "generate_policy_critique",
                        lambda *a, **k: pytest.fail("Oversized source must never open model transport"))
    result = factual.assess({}, DEFINITION, frame(), configuration())
    assert result.verdict is None and not result.attempted and result.reason == "input_limit"


def test_source_sentence_count_overflow_is_not_silently_truncated(monkeypatch):
    monkeypatch.setattr(factual.prompts, "policy_verified_context", lambda _: {
        "consumer_credit_restructuring": "Источник. " * 33})
    monkeypatch.setattr(factual.llm_agent, "generate_policy_critique",
                        lambda *a, **k: pytest.fail("Too many source facts must not open transport"))
    result = factual.assess({}, DEFINITION, frame(), configuration())
    assert result.verdict is None and not result.attempted and result.reason == "input_limit"


def test_F_deadline_is_ten_seconds_without_reducing_complete_JSON_budget(monkeypatch):
    result, calls = supplied_opinion(monkeypatch, DEFINITION, {"consumer_credit_restructuring": DEFINITION})
    assert result.verdict == "supported"
    assert calls[0][1].request_timeout == factual.MAX_PHASE_SECONDS == 10
    assert calls[0][1].max_tokens == calls[0][2]["token_limit"] == 2048


def test_deadline_output_never_becomes_supported_even_if_late_JSON_is_valid(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(factual.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(factual.prompts, "policy_verified_context", lambda _: {"consumer_credit_restructuring": DEFINITION})

    def delayed(messages, config, **kwargs):
        request = json.loads(messages[-1]["content"])
        clock[0] = 10.01
        return json.dumps({"rows": [{"unit_id": request["draft_units"][0]["id"],
                                    "relation": "supported", "source_ids": [request["source_catalog"][0]["id"]],
                                    "mismatch": "none"}]})

    monkeypatch.setattr(factual.llm_agent, "generate_policy_critique", delayed)
    result = factual.assess({}, DEFINITION, frame(), configuration(request_timeout=30))
    assert result.verdict is None and result.reason == "deadline_exhausted" and result.attempted


# R2 contrasts are run against the frozen R1 implementation before changing it.
@pytest.mark.parametrize("draft", [
    "Доход учитывается при расчёте прожиточного минимума.",
    "Размер удержаний составляет 50% дохода.",
])
def test_r2_income_eligibility_fact_cannot_support_calculation(monkeypatch, draft):
    sources = {"bankruptcy_basics": factual.prompts.BANKRUPTCY_EXPLANATION}
    result, calls = supplied_opinion(monkeypatch, draft, sources, citation_ordinals=(8,))
    assert result.verdict == "unknown" and result.reason == "evidence_scope_uncovered"
    assert not result.attempted and not calls and not result.rows
    assert any(issue.kind == "material_predicate" for issue in result.evidence_issues)


@pytest.mark.parametrize("source,draft,expected", [
    ("Доход учитывается при расчёте прожиточного минимума.",
     "При расчёте прожиточного минимума учитывается доход.", "supported"),
    ("Страховая выплата доступна при заводском дефекте.",
     "Страховую выплату рассчитывают по стоимости устройства.", "unknown"),
    ("Стоимость доставки рассчитывают по расстоянию.",
     "Стоимость доставки рассчитывают по расстоянию.", "supported"),
    ("Стоимость доставки рассчитывают по расстоянию.",
     "Стоимость ремонта рассчитывают по возрасту устройства.", "unknown"),
    ("Если есть гарантия, выплату рассчитывают по стоимости устройства.",
     "Выплату рассчитывают по стоимости устройства.", "unknown"),
    ("Если есть гарантия, выплату рассчитывают по стоимости устройства.",
     "Если есть гарантия, выплату рассчитывают по стоимости устройства.", "supported"),
])
def test_r2_material_predicate_and_object_coverage_cross_domain(monkeypatch, source, draft, expected):
    result, _ = supplied_opinion(monkeypatch, draft, {"consumer_credit_restructuring": source})
    assert result.verdict == expected


@pytest.mark.parametrize("draft", [
    "Само наличие дохода не определяет доступность банкротства.",
    "Для внесудебного банкротства через МФЦ наличие дохода не определяет доступность процедуры.",
    "При любом банкротстве само наличие дохода не определяет доступность процедуры.",
    "Наличие работы не мешает банкротству.",
])
@pytest.mark.parametrize("citations", [(8,), (7, 8)])
def test_r2_contextual_judicial_fact_cannot_be_promoted_to_generic_or_MFC_claim(monkeypatch, draft, citations):
    sources = {"bankruptcy_basics": factual.prompts.BANKRUPTCY_EXPLANATION}
    result, _ = supplied_opinion(monkeypatch, draft, sources, citation_ordinals=citations)
    assert result.verdict == "unknown" and result.reason == "evidence_scope_uncovered"
    assert result.rows[0].relation == "supported"
    assert result.evidence_issues


def test_r2_explicit_judicial_income_keeps_ordered_source_antecedent(monkeypatch):
    sources = {"bankruptcy_basics": factual.prompts.BANKRUPTCY_EXPLANATION}
    draft = "При судебном банкротстве само наличие или отсутствие дохода не определяет доступность процедуры."
    result, calls = supplied_opinion(monkeypatch, draft, sources, citation_ordinals=(8,))
    assert result.verdict == "supported" and len(calls) == 1
    eighth = next(f for f in result.source_catalog if f.ordinal == 8)
    seventh = next(f for f in result.source_catalog if f.ordinal == 7)
    context = next(s for s in result.source_contexts if s.fact_id == eighth.id)
    assert context.procedural_scopes == ("judicial_bankruptcy",)
    assert seventh.id in context.antecedent_ids
    assert context.inherited is True
    assert eighth.text == factual._source_catalog(sources)[7].text


def test_r2_context_resets_on_explicit_new_procedure_and_does_not_cross_source_groups(monkeypatch):
    sources = {"bankruptcy_basics": "Для судебного банкротства оцениваются условия закона. "
               "Доход не определяет доступность процедуры. "
               "Для внесудебного банкротства через МФЦ оцениваются условия закона. "
               "Доход не определяет доступность процедуры."}
    result, _ = supplied_opinion(monkeypatch,
        "Для внесудебного банкротства через МФЦ доход не определяет доступность процедуры.",
        sources, citation_ordinals=(4,))
    assert result.verdict == "supported"
    scopes = {c.fact_id: c for c in result.source_contexts}
    assert scopes[result.source_catalog[1].id].procedural_scopes == ("judicial_bankruptcy",)
    assert scopes[result.source_catalog[3].id].procedural_scopes == ("extrajudicial_bankruptcy",)
    split_sources = {"bankruptcy_basics": "Для судебного банкротства оцениваются условия закона.",
                     "consumer_credit_restructuring": "Доход не определяет доступность процедуры."}
    unbound, _ = supplied_opinion(monkeypatch,
        "При судебном банкротстве доход не определяет доступность процедуры.", split_sources)
    assert unbound.verdict == "unknown"


@pytest.mark.parametrize("invitation", [
    "Если есть другие вопросы по списанию долгов — спрашивайте, разберёмся вместе.",
    "Если у вас остались вопросы по банкротству, спрашивайте.",
    "Задавайте вопросы по реструктуризации, обсудим их здесь.",
])
def test_r2_whole_topic_invitation_is_not_a_hidden_factual_assertion(monkeypatch, invitation):
    result, _ = supplied_opinion(monkeypatch, DEFINITION + "\n\n" + invitation,
                                {"consumer_credit_restructuring": DEFINITION},
                                relations=("supported", "no_assertion"))
    assert result.verdict == "supported" and not result.evidence_issues


@pytest.mark.parametrize("invitation", [
    "Спрашивайте, всем спишут долги.",
    "Если есть вопросы по списанию долгов — спрашивайте. Ваш долг обязательно спишут.",
    "Задавайте вопросы по банкротству, оно доступно всем.",
    "Задавайте вопросы по банкротству где долги будут списаны.",
    "Если есть вопросы по списанию долгов которые будут списаны — спрашивайте.",
])
def test_r2_invitation_does_not_hide_a_later_claim_or_promise(monkeypatch, invitation):
    result, _ = supplied_opinion(monkeypatch, DEFINITION + "\n\n" + invitation,
                                {"consumer_credit_restructuring": DEFINITION},
                                relations=("supported", "no_assertion"))
    assert result.verdict == "unknown"
    assert any(issue.kind == "hidden_assertion" for issue in result.evidence_issues)
