"""Узкие доменные политики после LLM parsing и до deterministic reducer.

Здесь нет словаря разговорных фраз: политики работают только с уже типизированными
canonical-кандидатами и разрешают конкретные противоречия доменной модели.
"""

from __future__ import annotations

from routing import Matrix, get_matrix

from .contracts import (
    CandidateIssueCode,
    Explicitness,
    FactCandidate,
    FactCandidateIssue,
    FactEvidenceBasis,
    FactOperation,
    FactSource,
    FactSubject,
    FactValuePrecision,
    GoalSignal,
    PaymentStatusSignal,
    PreferenceStance,
    SemanticBoundarySignals,
)


_RAW_LOCATION_PATHS = {
    "client.region",
    "client.city",
    "assets.realty.region",
    "assets.realty.city",
}


def apply_candidate_policies(
    candidates: list[FactCandidate],
    matrix: Matrix | None = None,
    *,
    boundaries: SemanticBoundarySignals | None = None,
) -> tuple[list[FactCandidate], list[FactCandidateIssue]]:
    """Применяет минимальный набор явных и тестируемых доменных правил."""
    active_matrix = matrix or get_matrix()
    employment_values = {
        candidate.value
        for candidate in candidates
        if candidate.path == "employment.type"
        and candidate.subject == FactSubject.CLIENT
    }
    retired_without_work = {"retired", "unemployed"} <= employment_values
    allowed_paths = set(active_matrix.fields) | _RAW_LOCATION_PATHS

    accepted: list[FactCandidate] = []
    issues: list[FactCandidateIssue] = []
    for index, candidate in enumerate(candidates):
        if candidate.path not in allowed_paths:
            issues.append(
                FactCandidateIssue(
                    raw_index=index,
                    path=candidate.path,
                    code=CandidateIssueCode.INVALID_SCHEMA,
                    message="candidate path is outside the domain allowlist",
                )
            )
            continue
        boundary_rejection = _boundary_rejection(candidate, boundaries)
        if boundary_rejection is not None:
            issues.append(
                FactCandidateIssue(
                    raw_index=index,
                    path=candidate.path,
                    code=CandidateIssueCode.DOMAIN_POLICY_SUPERSEDED,
                    message=boundary_rejection,
                )
            )
            continue
        # Semantic extractor может показать косвенную гипотезу для диагностики,
        # но она не становится фактом. Единственное разрешённое исключение —
        # интерпретация цели клиента: это и есть отдельная intent-задача.
        if (
            candidate.evidence_basis == FactEvidenceBasis.INFERRED
            and candidate.path != "client.goal"
        ):
            issues.append(
                FactCandidateIssue(
                    raw_index=index,
                    path=candidate.path,
                    code=CandidateIssueCode.DOMAIN_POLICY_SUPERSEDED,
                    message=(
                        "inferred evidence is not trusted for a factual field"
                    ),
                )
            )
            continue
        # В CRM пенсионер — самостоятельный тип занятости. Фраза «пенсионер,
        # не работаю» не должна превращаться в два взаимоисключающих значения.
        if (
            retired_without_work
            and candidate.path == "employment.type"
            and candidate.subject == FactSubject.CLIENT
            and candidate.value == "unemployed"
        ):
            issues.append(
                FactCandidateIssue(
                    raw_index=index,
                    path=candidate.path,
                    code=CandidateIssueCode.DOMAIN_POLICY_SUPERSEDED,
                    message="retired supersedes unemployed for the same client turn",
                )
            )
            continue
        accepted.append(candidate)

    # Предпочтение по залогу конкретного автомобиля не имеет смысла, когда в
    # том же сообщении клиент явно сказал, что автомобиля у него нет. Модель
    # иногда превращает отсутствие объекта в отказ его закладывать; это лишний
    # факт и неверная причинность, хотя текущий маршрут он обычно не меняет.
    no_vehicle_subjects = {
        candidate.subject
        for candidate in accepted
        if candidate.path == "assets.has_vehicle" and candidate.value is False
    }
    if no_vehicle_subjects:
        filtered: list[FactCandidate] = []
        for index, candidate in enumerate(accepted):
            if (
                candidate.path == "preferences.vehicle_collateral_allowed"
                and candidate.subject in no_vehicle_subjects
            ):
                issues.append(
                    FactCandidateIssue(
                        raw_index=index,
                        path=candidate.path,
                        code=CandidateIssueCode.DOMAIN_POLICY_SUPERSEDED,
                        message="vehicle absence supersedes vehicle collateral preference",
                    )
                )
                continue
            filtered.append(candidate)
        accepted = filtered

    # Неопределённость самого наличия доли распространяется на состав владельцев,
    # но не на явно названный тип объекта. Это dependency policy, а не анализ фраз.
    ambiguous_realty_subjects = {
        candidate.subject
        for candidate in accepted
        if candidate.path == "assets.has_realty"
        and candidate.value is True
        and candidate.explicitness == Explicitness.AMBIGUOUS
    }
    normalized: list[FactCandidate] = []
    for index, candidate in enumerate(accepted):
        # У clear нет значения, а значит нет и числовой точности. Модель может
        # прислать precision=vague из-за фразы «с суммой не определился», но
        # явное «уберите её» остаётся точной командой удалить старый факт.
        if candidate.operation == FactOperation.CLEAR:
            target = candidate.model_copy(
                update={
                    "explicitness": Explicitness.EXPLICIT,
                    "precision": FactValuePrecision.EXACT,
                    "evidence_basis": FactEvidenceBasis.DIRECT,
                }
            )
            if target != candidate:
                issues.append(
                    FactCandidateIssue(
                        raw_index=index,
                        path=candidate.path,
                        code=CandidateIssueCode.DOMAIN_POLICY_APPLIED,
                        message="clear operation normalized candidate metadata",
                    )
                )
            candidate = target
            normalized.append(candidate)
            continue
        spec = active_matrix.fields.get(candidate.path)
        if spec and spec.kind == "money":
            target_explicitness = candidate.explicitness
            if (
                candidate.precision == FactValuePrecision.POINT_ESTIMATE
                and isinstance(candidate.value, (int, float))
                and not isinstance(candidate.value, bool)
            ):
                target_explicitness = Explicitness.EXPLICIT
            elif candidate.precision in {
                FactValuePrecision.RANGE,
                FactValuePrecision.VAGUE,
            }:
                target_explicitness = Explicitness.AMBIGUOUS
            if target_explicitness != candidate.explicitness:
                candidate = candidate.model_copy(
                    update={"explicitness": target_explicitness}
                )
                issues.append(
                    FactCandidateIssue(
                        raw_index=index,
                        path=candidate.path,
                        code=CandidateIssueCode.DOMAIN_POLICY_APPLIED,
                        message=(
                            "numeric precision normalized candidate explicitness"
                        ),
                    )
                )
        elif candidate.precision == FactValuePrecision.POINT_ESTIMATE:
            # Point estimate является принимаемым бизнес-форматом только для
            # денег. Приблизительный возраст/год остаётся неопределённым.
            candidate = candidate.model_copy(
                update={
                    "precision": FactValuePrecision.VAGUE,
                    "explicitness": Explicitness.AMBIGUOUS,
                }
            )
            issues.append(
                FactCandidateIssue(
                    raw_index=index,
                    path=candidate.path,
                    code=CandidateIssueCode.DOMAIN_POLICY_APPLIED,
                    message=(
                        "point estimate is only actionable for money fields"
                    ),
                )
            )
        if (
            candidate.path == "client.goal"
            and candidate.evidence_basis == FactEvidenceBasis.INFERRED
        ):
            candidate = candidate.model_copy(
                update={
                    "source": FactSource.SYSTEM_DERIVED,
                    "explicitness": Explicitness.EXPLICIT,
                    "precision": FactValuePrecision.EXACT,
                }
            )
            issues.append(
                FactCandidateIssue(
                    raw_index=index,
                    path=candidate.path,
                    code=CandidateIssueCode.DOMAIN_POLICY_APPLIED,
                    message=(
                        "matching goal boundary confirms inferred intent"
                    ),
                )
            )
        if (
            candidate.path == "assets.realty.owners"
            and candidate.subject in ambiguous_realty_subjects
            and candidate.explicitness == Explicitness.EXPLICIT
        ):
            candidate = candidate.model_copy(
                update={"explicitness": Explicitness.AMBIGUOUS}
            )
            issues.append(
                FactCandidateIssue(
                    raw_index=index,
                    path=candidate.path,
                    code=CandidateIssueCode.DOMAIN_POLICY_APPLIED,
                    message=(
                        "ambiguous has_realty propagates to realty owners"
                    ),
                )
            )
        normalized.append(candidate)

    # Любая конкретная характеристика объекта логически означает наличие объекта
    # у того же субъекта. Такой факт помечается system_derived и не зависит от
    # словаря разговорных формулировок.
    subjects_with_has_realty = {
        candidate.subject
        for candidate in normalized
        if candidate.path == "assets.has_realty"
    }
    detail_by_subject: dict[FactSubject, FactCandidate] = {}
    for candidate in normalized:
        if candidate.path.startswith("assets.realty."):
            detail_by_subject.setdefault(candidate.subject, candidate)
    for subject, detail in detail_by_subject.items():
        if subject in subjects_with_has_realty or len(normalized) >= 12:
            continue
        normalized.append(
            FactCandidate(
                path="assets.has_realty",
                value=True,
                source=FactSource.SYSTEM_DERIVED,
                evidence=detail.evidence,
                explicitness=detail.explicitness,
                evidence_basis=detail.evidence_basis,
                subject=subject,
            )
        )
        issues.append(
            FactCandidateIssue(
                path="assets.has_realty",
                code=CandidateIssueCode.DOMAIN_POLICY_APPLIED,
                message="realty detail implies has_realty for the same subject",
            )
        )

    # Эти зависимости следуют из typed domain model и не анализируют текст.
    _append_implied_boolean(
        normalized,
        issues,
        detail_prefix="assets.vehicle.",
        target_path="assets.has_vehicle",
    )
    _append_implied_goal(normalized, issues)
    return normalized, issues


def _boundary_rejection(
    candidate: FactCandidate,
    boundaries: SemanticBoundarySignals | None,
) -> str | None:
    """Сверяет чувствительный факт с независимой контрастной классификацией."""
    if boundaries is None:
        return None

    if candidate.path == "client.goal":
        expected_goal = (
            GoalSignal(candidate.value)
            if isinstance(candidate.value, str)
            and candidate.value in {item.value for item in GoalSignal}
            else None
        )
        if expected_goal is not None and boundaries.goal != expected_goal:
            return "goal candidate conflicts with semantic boundary"

    if candidate.path == "preferences.explore_alternatives":
        expected = (
            PreferenceStance.ACCEPTS
            if candidate.value is True
            else PreferenceStance.REJECTS
        )
        if boundaries.alternatives != expected:
            return "alternatives candidate conflicts with semantic boundary"

    if candidate.path == "preferences.collateral_allowed":
        expected = (
            PreferenceStance.ACCEPTS
            if candidate.value is True
            else PreferenceStance.REJECTS
        )
        if boundaries.collateral != expected:
            return "collateral candidate conflicts with semantic boundary"

    if candidate.path == "preferences.realty_collateral_allowed":
        expected = (
            PreferenceStance.ACCEPTS
            if candidate.value is True
            else PreferenceStance.REJECTS
        )
        if boundaries.realty_collateral != expected:
            return "realty-collateral candidate conflicts with semantic boundary"

    if candidate.path == "preferences.vehicle_collateral_allowed":
        expected = (
            PreferenceStance.ACCEPTS
            if candidate.value is True
            else PreferenceStance.REJECTS
        )
        if boundaries.vehicle_collateral != expected:
            return "vehicle-collateral candidate conflicts with semantic boundary"

    if candidate.path == "debts.credit_history":
        expected_payment_status = (
            {
                "no_overdue": PaymentStatusSignal.NO_CURRENT_OVERDUE,
                "past_overdue": PaymentStatusSignal.PAST_OVERDUE,
                "has_overdue": PaymentStatusSignal.CURRENT_OVERDUE,
                "ready_bankruptcy": PaymentStatusSignal.BANKRUPTCY_READY,
            }.get(candidate.value)
            if isinstance(candidate.value, str)
            else None
        )
        if (
            expected_payment_status is not None
            and boundaries.payment_status != expected_payment_status
        ):
            return "credit-history candidate conflicts with payment boundary"

    if candidate.path == "debts.overdue_duration":
        allowed_statuses = (
            {
                PaymentStatusSignal.NO_CURRENT_OVERDUE,
                PaymentStatusSignal.PAST_OVERDUE,
            }
            if candidate.value == "no_overdue"
            else {PaymentStatusSignal.CURRENT_OVERDUE}
        )
        if boundaries.payment_status not in allowed_statuses:
            return "overdue-duration candidate conflicts with payment boundary"

    return None


def _append_implied_boolean(
    candidates: list[FactCandidate],
    issues: list[FactCandidateIssue],
    *,
    detail_prefix: str,
    target_path: str,
) -> None:
    """Добавляет наличие сущности, если уже извлечена её характеристика."""
    subjects_with_target = {
        candidate.subject
        for candidate in candidates
        if candidate.path == target_path
    }
    detail_by_subject: dict[FactSubject, FactCandidate] = {}
    for candidate in candidates:
        if candidate.path.startswith(detail_prefix):
            detail_by_subject.setdefault(candidate.subject, candidate)
    for subject, detail in detail_by_subject.items():
        if subject in subjects_with_target or len(candidates) >= 12:
            continue
        candidates.append(
            FactCandidate(
                path=target_path,
                value=True,
                source=FactSource.SYSTEM_DERIVED,
                evidence=detail.evidence,
                explicitness=detail.explicitness,
                evidence_basis=detail.evidence_basis,
                subject=subject,
            )
        )
        issues.append(
            FactCandidateIssue(
                path=target_path,
                code=CandidateIssueCode.DOMAIN_POLICY_APPLIED,
                message=f"{detail_prefix} detail implies {target_path}",
            )
        )


def _append_implied_goal(
    candidates: list[FactCandidate],
    issues: list[FactCandidateIssue],
) -> None:
    """Любая заявленная запрашиваемая сумма означает цель получить деньги.

    Диапазон не становится рабочим amount, но сама цель cash при этом однозначна.
    """
    amount = next(
        (
            candidate
            for candidate in candidates
            if candidate.path == "request.amount"
            and candidate.subject == FactSubject.CLIENT
            and candidate.operation == FactOperation.SET
        ),
        None,
    )
    if amount is None:
        return
    existing_goal = next(
        (
            candidate
            for candidate in candidates
            if candidate.path == "client.goal"
            and candidate.subject == FactSubject.CLIENT
        ),
        None,
    )
    if existing_goal is not None:
        if existing_goal.value != "cash":
            return
        if (
            existing_goal.explicitness == Explicitness.EXPLICIT
            and existing_goal.precision == FactValuePrecision.EXACT
        ):
            return
        replacement_index = candidates.index(existing_goal)
        candidates[replacement_index] = existing_goal.model_copy(
            update={
                "source": FactSource.SYSTEM_DERIVED,
                "explicitness": Explicitness.EXPLICIT,
                "precision": FactValuePrecision.EXACT,
            }
        )
        issues.append(
            FactCandidateIssue(
                raw_index=replacement_index,
                path="client.goal",
                code=CandidateIssueCode.DOMAIN_POLICY_APPLIED,
                message=(
                    "explicit request.amount confirms inferred cash goal"
                ),
            )
        )
        return
    if len(candidates) >= 12:
        return
    candidates.append(
        FactCandidate(
            path="client.goal",
            value="cash",
            source=FactSource.SYSTEM_DERIVED,
            evidence=amount.evidence,
            subject=FactSubject.CLIENT,
        )
    )
    issues.append(
        FactCandidateIssue(
            path="client.goal",
            code=CandidateIssueCode.DOMAIN_POLICY_APPLIED,
            message="explicit request.amount implies client.goal=cash",
        )
    )
