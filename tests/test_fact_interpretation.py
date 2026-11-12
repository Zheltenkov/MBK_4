"""Fail-closed проверки нового deep-модуля интерпретации фактов."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from evaluation.fact_interpretation import load_fact_interpretation_cases
from interpretation import (
    CandidateDisposition,
    Explicitness,
    FactCandidate,
    FactEvidenceBasis,
    FactOperation,
    FactSource,
    FactStatus,
    FactSubject,
    FactValuePrecision,
    GoalSignal,
    NewBorrowingStance,
    PaymentStatusSignal,
    PreferenceStance,
    SemanticBoundarySignals,
    apply_candidate_policies,
    reduce_fact_candidates,
)
from routing import get_fact


VARIANT_CASES = (
    Path(__file__).resolve().parents[1]
    / "evaluation"
    / "fact_interpretation_variants.json"
)


def test_offline_cases_are_unique_and_cover_risky_speech_phenomena(matrix):
    """Корпус фиксирует именно те случаи, где перечни подстрок хрупки."""
    cases = load_fact_interpretation_cases()
    ids = [case.id for case in cases]
    phenomena = {case.phenomenon for case in cases}

    assert len(cases) == 50
    assert len(ids) == len(set(ids))
    assert {
        "negation",
        "correction",
        "ambiguity",
        "subject_resolution",
        "colloquial_synonym",
        "temporal_context",
        "retraction",
    } <= phenomena

    # Ground truth обязан использовать только пути матрицы.
    known_paths = set(matrix.fields) | {
        "client.region",
        "client.city",
        "assets.realty.region",
        "assets.realty.city",
    }
    for case in cases:
        assert {candidate.path for candidate in case.candidates} <= known_paths
        assert {
            candidate.path for candidate in case.optional_candidates
        } <= known_paths


@pytest.mark.parametrize(
    "case",
    load_fact_interpretation_cases(),
    ids=lambda case: case.id,
)
def test_offline_case_reduces_to_expected_state(case, matrix):
    """Проверяет воспроизводимость state transition для каждого примера."""
    result = reduce_fact_candidates(
        case.initial_facts,
        {},
        case.candidates,
        evidence_text=case.utterance,
        matrix=matrix,
    )

    assert [decision.disposition for decision in result.decisions] == (
        case.expected_dispositions
    )
    for path, expected in case.expected_values.items():
        assert get_fact(result.facts, path) == expected
    for path in case.expected_absent_paths:
        assert get_fact(result.facts, path) is None
    for path, expected in case.expected_statuses.items():
        assert result.statuses[path] == expected


def test_noncanonical_conversational_value_is_rejected(matrix):
    """Reducer не содержит скрытый словарь «квартира» → flat."""
    candidate = FactCandidate(
        path="assets.realty.kind",
        value="квартира",
        evidence="квартира",
    )

    result = reduce_fact_candidates(
        {},
        {},
        [candidate],
        evidence_text="У меня квартира.",
        matrix=matrix,
    )

    assert result.decisions[0].disposition == CandidateDisposition.REJECTED
    assert result.decisions[0].reason == "non_canonical_value"
    assert get_fact(result.facts, candidate.path) is None


def test_rejected_non_canonical_value_still_leaves_an_audit_event(matrix):
    """Отклонённая сумма не должна пропадать без единого следа в fact_events.

    Раньше `_decision(...)` для `non_canonical_value` не выставлял ``status``, и
    `_event_from_decision` тихо пропускал такое решение — в проде это выглядело как
    сумма, которую клиент назвал, но которая просто исчезла без объяснения.
    """
    candidate = FactCandidate(
        path="request.amount",
        value="около миллиона",
        evidence="около миллиона",
    )

    result = reduce_fact_candidates(
        {},
        {},
        [candidate],
        evidence_text="Нужно около миллиона.",
        matrix=matrix,
    )

    assert get_fact(result.facts, "request.amount") is None
    assert len(result.events) == 1
    assert result.events[0].reason == "non_canonical_value"


def test_user_claim_cannot_set_derived_region_class(matrix):
    """Класс региона приходит из DaData/детерминированного classifier, не из LLM."""
    candidate = FactCandidate(
        path="client.region_class",
        value="moscow_mo",
        evidence="Москва",
    )

    rejected = reduce_fact_candidates(
        {},
        {},
        [candidate],
        evidence_text="Я живу в Москве.",
        matrix=matrix,
    )
    trusted = reduce_fact_candidates(
        {},
        {},
        [candidate.model_copy(update={"source": FactSource.DADATA})],
        evidence_text="",
        matrix=matrix,
    )

    assert rejected.decisions[0].reason == "derived_path_requires_trusted_source"
    assert get_fact(trusted.facts, candidate.path) == "moscow_mo"


def test_missing_grounding_is_rejected(matrix):
    """LLM не может записать факт, которого нет в проверяемой реплике."""
    candidate = FactCandidate(
        path="assets.realty.kind",
        value="flat",
        evidence="квартира",
    )

    result = reduce_fact_candidates(
        {},
        {},
        [candidate],
        evidence_text="Мне нужна консультация.",
        matrix=matrix,
    )

    assert result.decisions[0].reason == "evidence_not_found"
    assert result.events == []


def test_set_conflict_does_not_overwrite_but_correction_does(matrix):
    """Противоречие требует explicit operation=correct."""
    current: dict[str, Any] = {"request": {"amount": 400_000}}
    base = FactCandidate(
        path="request.amount",
        value=700_000,
        evidence="700 тысяч",
    )

    conflict = reduce_fact_candidates(
        current,
        {},
        [base],
        evidence_text="Теперь нужно 700 тысяч.",
        matrix=matrix,
    )
    corrected = reduce_fact_candidates(
        current,
        {},
        [base.model_copy(update={"operation": FactOperation.CORRECT})],
        evidence_text="Точнее, 700 тысяч.",
        matrix=matrix,
    )

    assert get_fact(conflict.facts, base.path) == 400_000
    assert conflict.statuses[base.path] == FactStatus.CONFLICT
    assert get_fact(corrected.facts, base.path) == 700_000
    assert corrected.statuses[base.path] == FactStatus.CONFIRMED


def test_ambiguous_or_non_client_fact_requires_confirmation(matrix):
    """Неопределённость и чужой субъект не загрязняют client facts."""
    candidates = [
        FactCandidate(
            path="debts.total",
            value=400_000,
            evidence="около четырёхсот",
            explicitness=Explicitness.AMBIGUOUS,
        ),
        FactCandidate(
            path="assets.has_realty",
            value=True,
            evidence="квартира жены",
            subject=FactSubject.SPOUSE,
        ),
    ]

    result = reduce_fact_candidates(
        {},
        {},
        candidates,
        evidence_text="Долг около четырёхсот, квартира жены.",
        matrix=matrix,
    )

    assert all(
        decision.disposition == CandidateDisposition.NEEDS_CONFIRMATION
        for decision in result.decisions
    )
    assert get_fact(result.facts, "debts.total") is None
    assert get_fact(result.facts, "assets.has_realty") is None


@pytest.mark.parametrize("client_first", [True, False])
def test_client_fact_wins_over_spouse_status_regardless_of_order(
    matrix,
    client_first,
):
    """Порядок LLM-кандидатов не меняет итоговый client state."""
    client = FactCandidate(
        path="assets.has_realty",
        value=False,
        evidence="не у меня",
    )
    spouse = FactCandidate(
        path="assets.has_realty",
        value=True,
        evidence="у жены",
        subject=FactSubject.SPOUSE,
    )
    candidates = [client, spouse] if client_first else [spouse, client]

    result = reduce_fact_candidates(
        {},
        {},
        candidates,
        evidence_text="Квартира есть у жены, не у меня.",
        matrix=matrix,
    )

    assert get_fact(result.facts, "assets.has_realty") is False
    assert result.statuses["assets.has_realty"] == FactStatus.KNOWN


def test_typed_policy_adds_realty_presence_without_phrase_markers():
    """Характеристика объекта даёт логическое, а не лексическое следствие."""
    candidates, issues = apply_candidate_policies(
        [
            FactCandidate(
                path="assets.realty.owners",
                value="client_and_spouse",
                evidence="не только моя",
            )
        ]
    )

    assert [(item.path, item.value) for item in candidates] == [
        ("assets.realty.owners", "client_and_spouse"),
        ("assets.has_realty", True),
    ]
    assert candidates[1].source == FactSource.SYSTEM_DERIVED
    assert issues[0].code.value == "domain_policy_applied"


def test_typed_policy_propagates_presence_ambiguity_only_to_owners():
    candidates, _ = apply_candidate_policies(
        [
            FactCandidate(
                path="assets.has_realty",
                value=True,
                evidence="вроде есть",
                explicitness=Explicitness.AMBIGUOUS,
            ),
            FactCandidate(
                path="assets.realty.kind",
                value="flat",
                evidence="квартира",
            ),
            FactCandidate(
                path="assets.realty.owners",
                value="others",
                evidence="доля",
            ),
        ]
    )

    assert candidates[1].explicitness == Explicitness.EXPLICIT
    assert candidates[2].explicitness == Explicitness.AMBIGUOUS


def test_point_estimate_is_usable_but_range_stays_ambiguous(matrix):
    candidates, issues = apply_candidate_policies(
        [
            FactCandidate(
                path="debts.total",
                value=720_000,
                evidence="ориентир",
                explicitness=Explicitness.AMBIGUOUS,
                precision=FactValuePrecision.POINT_ESTIMATE,
            ),
            FactCandidate(
                path="request.amount",
                value="между двумя значениями",
                evidence="между",
                precision=FactValuePrecision.RANGE,
            ),
        ],
        matrix,
    )

    assert candidates[0].explicitness == Explicitness.EXPLICIT
    assert candidates[1].explicitness == Explicitness.AMBIGUOUS
    assert {issue.code.value for issue in issues} == {
        "domain_policy_applied"
    }


def test_point_estimate_is_not_actionable_for_non_money_field(matrix):
    candidates, issues = apply_candidate_policies(
        [
            FactCandidate(
                path="client.age",
                value=40,
                evidence="около сорока",
                precision=FactValuePrecision.POINT_ESTIMATE,
            )
        ],
        matrix,
    )

    assert candidates[0].precision == FactValuePrecision.VAGUE
    assert candidates[0].explicitness == Explicitness.AMBIGUOUS
    assert issues[0].code.value == "domain_policy_applied"


def test_typed_vehicle_detail_and_requested_amount_add_entity_facts(matrix):
    candidates, _ = apply_candidate_policies(
        [
            FactCandidate(
                path="request.amount",
                value=520_000,
                evidence="сумма",
            ),
            FactCandidate(
                path="assets.vehicle.year",
                value=2018,
                evidence="2018",
            ),
        ],
        matrix,
    )

    assert {(candidate.path, candidate.value) for candidate in candidates} >= {
        ("client.goal", "cash"),
        ("assets.has_vehicle", True),
    }


def test_amount_correction_does_not_create_a_new_goal(matrix):
    candidates, _ = apply_candidate_policies(
        [
            FactCandidate(
                path="request.amount",
                value=900_000,
                operation=FactOperation.CORRECT,
                evidence="теперь 900",
            )
        ],
        matrix,
    )

    assert [candidate.path for candidate in candidates] == [
        "request.amount"
    ]


def test_explicit_amount_confirms_ambiguous_inferred_cash_goal(matrix):
    candidates, issues = apply_candidate_policies(
        [
            FactCandidate(
                path="request.amount",
                value=1_200_000,
                evidence="нужна сумма",
            ),
            FactCandidate(
                path="client.goal",
                value="cash",
                evidence="нужна сумма",
                explicitness=Explicitness.AMBIGUOUS,
                precision=FactValuePrecision.VAGUE,
                evidence_basis=FactEvidenceBasis.INFERRED,
            ),
        ],
        matrix,
    )

    goal = next(item for item in candidates if item.path == "client.goal")
    assert goal.explicitness == Explicitness.EXPLICIT
    assert goal.precision == FactValuePrecision.EXACT
    assert goal.source == FactSource.SYSTEM_DERIVED
    assert any(
        issue.message == "matching goal boundary confirms inferred intent"
        for issue in issues
    )


def test_request_range_implies_cash_goal_without_inventing_amount(matrix):
    candidates, _ = apply_candidate_policies(
        [
            FactCandidate(
                path="request.amount",
                value="от 400 до 600 тысяч",
                evidence="от 400 до 600 тысяч",
                explicitness=Explicitness.AMBIGUOUS,
                precision=FactValuePrecision.RANGE,
            )
        ],
        matrix,
    )

    assert {(candidate.path, candidate.value) for candidate in candidates} == {
        ("request.amount", "от 400 до 600 тысяч"),
        ("client.goal", "cash"),
    }
    result = reduce_fact_candidates(
        {},
        {},
        candidates,
        evidence_text="Нужно от 400 до 600 тысяч.",
        matrix=matrix,
    )
    assert get_fact(result.facts, "request.amount") is None
    assert get_fact(result.facts, "client.goal") == "cash"


def test_inferred_correlations_are_rejected_but_goal_intent_is_allowed(matrix):
    """Policy доверяет intent-задаче, но не превращает корреляции в факты."""
    candidates, issues = apply_candidate_policies(
        [
            FactCandidate(
                path="debts.credit_history",
                value="has_overdue",
                evidence="платить тяжело",
                evidence_basis=FactEvidenceBasis.INFERRED,
            ),
            FactCandidate(
                path="preferences.explore_alternatives",
                value=True,
                evidence="этот кредит не нужен",
                evidence_basis=FactEvidenceBasis.INFERRED,
            ),
            FactCandidate(
                path="client.goal",
                value="debt_relief",
                evidence="надо решить вопрос с долгами",
                evidence_basis=FactEvidenceBasis.INFERRED,
            ),
        ],
        matrix,
    )

    assert [(candidate.path, candidate.value) for candidate in candidates] == [
        ("client.goal", "debt_relief")
    ]
    assert [
        issue.path
        for issue in issues
        if issue.code.value == "domain_policy_superseded"
    ] == [
        "debts.credit_history",
        "preferences.explore_alternatives",
    ]
    assert all(
        issue.code.value == "domain_policy_superseded"
        for issue in issues[:2]
    )


def test_contextual_answer_remains_trusted(matrix):
    candidates, issues = apply_candidate_policies(
        [
            FactCandidate(
                path="preferences.explore_alternatives",
                value=False,
                evidence="нет",
                evidence_basis=FactEvidenceBasis.CONTEXTUAL,
            )
        ],
        matrix,
    )

    assert len(candidates) == 1
    assert candidates[0].value is False
    assert issues == []


def test_contrastive_boundaries_keep_neighboring_concepts_separate(matrix):
    """Отказ от нового займа не является ответом про залог или альтернативы."""
    boundaries = SemanticBoundarySignals(
        payment_status=PaymentStatusSignal.DIFFICULTY_ONLY,
        new_borrowing=NewBorrowingStance.REJECTS,
        alternatives=PreferenceStance.NOT_MENTIONED,
        collateral=PreferenceStance.NOT_MENTIONED,
    )
    candidates, issues = apply_candidate_policies(
        [
            FactCandidate(
                path="debts.credit_history",
                value="has_overdue",
                evidence="тяжело платить",
            ),
            FactCandidate(
                path="preferences.explore_alternatives",
                value=False,
                evidence="не хочу новый заём",
            ),
            FactCandidate(
                path="preferences.collateral_allowed",
                value=False,
                evidence="не хочу новый заём",
            ),
        ],
        matrix,
        boundaries=boundaries,
    )

    assert candidates == []
    assert [issue.path for issue in issues] == [
        "debts.credit_history",
        "preferences.explore_alternatives",
        "preferences.collateral_allowed",
    ]


def test_contrastive_boundaries_allow_supported_sensitive_facts(matrix):
    boundaries = SemanticBoundarySignals(
        payment_status=PaymentStatusSignal.CURRENT_OVERDUE,
        alternatives=PreferenceStance.ACCEPTS,
        collateral=PreferenceStance.REJECTS,
    )
    candidates, issues = apply_candidate_policies(
        [
            FactCandidate(
                path="debts.credit_history",
                value="has_overdue",
                evidence="есть задержка",
            ),
            FactCandidate(
                path="debts.overdue_duration",
                value="one_to_three_months",
                evidence="два месяца",
            ),
            FactCandidate(
                path="preferences.explore_alternatives",
                value=True,
                evidence="посмотрю варианты",
            ),
            FactCandidate(
                path="preferences.collateral_allowed",
                value=False,
                evidence="без залога",
            ),
        ],
        matrix,
        boundaries=boundaries,
    )

    assert len(candidates) == 4
    assert issues == []


def test_specific_collateral_refusal_is_not_promoted_to_global_refusal(matrix):
    boundaries = SemanticBoundarySignals(
        collateral=PreferenceStance.NOT_MENTIONED,
        realty_collateral=PreferenceStance.REJECTS,
        vehicle_collateral=PreferenceStance.NOT_MENTIONED,
    )
    candidates, issues = apply_candidate_policies(
        [
            FactCandidate(
                path="preferences.realty_collateral_allowed",
                value=False,
                evidence="квартиру закладывать не хочу",
            ),
            FactCandidate(
                path="preferences.collateral_allowed",
                value=False,
                evidence="квартиру закладывать не хочу",
            ),
        ],
        matrix,
        boundaries=boundaries,
    )

    assert [candidate.path for candidate in candidates] == [
        "preferences.realty_collateral_allowed"
    ]
    assert [issue.path for issue in issues] == [
        "preferences.collateral_allowed"
    ]


def test_matching_goal_boundary_confirms_inferred_intent(matrix):
    boundaries = SemanticBoundarySignals(goal=GoalSignal.DEBT_RELIEF)
    candidates, issues = apply_candidate_policies(
        [
            FactCandidate(
                path="client.goal",
                value="debt_relief",
                evidence="нужно решить вопрос с долгами",
                explicitness=Explicitness.AMBIGUOUS,
                precision=FactValuePrecision.VAGUE,
                evidence_basis=FactEvidenceBasis.INFERRED,
            )
        ],
        matrix,
        boundaries=boundaries,
    )

    assert candidates[0].explicitness == Explicitness.EXPLICIT
    assert candidates[0].precision == FactValuePrecision.EXACT
    assert issues[0].message == (
        "matching goal boundary confirms inferred intent"
    )


def test_conflicting_goal_boundary_rejects_candidate(matrix):
    boundaries = SemanticBoundarySignals(goal=GoalSignal.CASH)
    candidates, issues = apply_candidate_policies(
        [
            FactCandidate(
                path="client.goal",
                value="debt_relief",
                evidence="долги",
                evidence_basis=FactEvidenceBasis.INFERRED,
            )
        ],
        matrix,
        boundaries=boundaries,
    )

    assert candidates == []
    assert issues[0].message == (
        "goal candidate conflicts with semantic boundary"
    )


def test_unknown_path_and_string_boolean_are_rejected(matrix):
    """В state нельзя протащить придуманное поле или truthy-строку."""
    candidates = [
        FactCandidate(path="client.magic", value="yes", evidence="yes"),
        FactCandidate(
            path="assets.has_realty",
            value="false",
            evidence="недвижимости нет",
        ),
    ]

    result = reduce_fact_candidates(
        {},
        {},
        candidates,
        evidence_text="yes, недвижимости нет",
        matrix=matrix,
    )

    assert [decision.reason for decision in result.decisions] == [
        "unknown_path",
        "non_canonical_value",
    ]


def test_candidate_policy_drops_path_outside_domain_allowlist(matrix):
    candidates, issues = apply_candidate_policies(
        [
            FactCandidate(
                path="preferences.new_borrowing",
                value=False,
                evidence="новый заём не нужен",
            )
        ],
        matrix,
    )

    assert candidates == []
    assert issues[0].code.value == "invalid_schema"
    assert issues[0].message == (
        "candidate path is outside the domain allowlist"
    )


def test_clear_prunes_empty_parents_and_preserves_unknown_status(matrix):
    """Отзыв факта не оставляет пустые domain-ветки в публичных facts."""
    candidate = FactCandidate(
        path="request.amount",
        operation=FactOperation.CLEAR,
        evidence="сумму уберите",
    )

    result = reduce_fact_candidates(
        {"request": {"amount": 500_000}},
        {},
        [candidate],
        evidence_text="Пока сумму уберите.",
        matrix=matrix,
    )

    assert result.facts == {}
    assert result.statuses[candidate.path] == FactStatus.UNKNOWN
    assert result.events[0].previous_value == 500_000


def test_money_clear_is_not_downgraded_by_numeric_precision_policy(matrix):
    """Для clear нет числовой точности: value намеренно отсутствует."""
    candidate = FactCandidate(
        path="request.amount",
        operation=FactOperation.CLEAR,
        evidence="уберите её",
        explicitness=Explicitness.EXPLICIT,
        precision=FactValuePrecision.VAGUE,
    )

    candidates, issues = apply_candidate_policies([candidate], matrix)
    result = reduce_fact_candidates(
        {"request": {"amount": 500_000}},
        {},
        candidates,
        evidence_text="Пока не определился с суммой, уберите её.",
        matrix=matrix,
    )

    assert issues[0].message == "clear operation normalized candidate metadata"
    assert result.facts == {}
    assert result.decisions[0].disposition == CandidateDisposition.CLEARED


def test_vehicle_absence_drops_inferred_vehicle_collateral_preference(matrix):
    candidates, issues = apply_candidate_policies(
        [
            FactCandidate(
                path="assets.has_vehicle",
                value=False,
                evidence="машины нет",
            ),
            FactCandidate(
                path="preferences.vehicle_collateral_allowed",
                value=False,
                evidence="машины нет",
            ),
        ],
        matrix,
    )

    assert [candidate.path for candidate in candidates] == ["assets.has_vehicle"]
    assert issues[-1].message == (
        "vehicle absence supersedes vehicle collateral preference"
    )


def test_invalid_legacy_status_is_dropped(matrix):
    """Reducer fail-closed нормализует состояние со старой сессии."""
    result = reduce_fact_candidates(
        {},
        {"client.age": "maybe", "client.goal": FactStatus.KNOWN},
        [],
        evidence_text="",
        matrix=matrix,
    )

    assert result.statuses == {"client.goal": FactStatus.KNOWN}


def test_holdout_variants_are_separate_and_strictly_valid():
    main_ids = {case.id for case in load_fact_interpretation_cases()}
    variants = load_fact_interpretation_cases(VARIANT_CASES)

    assert len(variants) == 14
    assert not main_ids & {case.id for case in variants}
    assert {
        "numeric_point_estimate",
        "numeric_range",
        "typed_entity_dependency",
        "intent_negative_control",
    } <= {case.phenomenon for case in variants}


@pytest.mark.parametrize(
    "case",
    load_fact_interpretation_cases(VARIANT_CASES),
    ids=lambda case: case.id,
)
def test_holdout_variants_reduce_to_expected_state(case, matrix):
    result = reduce_fact_candidates(
        case.initial_facts,
        {},
        case.candidates,
        evidence_text=case.utterance,
        matrix=matrix,
    )

    assert [decision.disposition for decision in result.decisions] == (
        case.expected_dispositions
    )
    for path, expected in case.expected_values.items():
        assert get_fact(result.facts, path) == expected
    for path in case.expected_absent_paths:
        assert get_fact(result.facts, path) is None
    for path, expected in case.expected_statuses.items():
        assert result.statuses[path] == expected
