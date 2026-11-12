"""Deterministic reducer кандидатов в canonical facts.

Этот module не понимает разговорную речь по подстрокам. Semantic extractor обязан
вернуть canonical value и evidence; reducer проверяет только контракт, источник,
контекст исправления и тип значения.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any, Mapping

from routing import Matrix, get_fact, get_matrix
from routing.normalize import canonical_enum_value, parse_phone
from utils import set_by_path

from .contracts import (
    CandidateDisposition,
    Explicitness,
    FactCandidate,
    FactDecision,
    FactEvent,
    FactOperation,
    FactReductionResult,
    FactSource,
    FactStatus,
    FactSubject,
    FactScalar,
)


# Эти строки являются исходными географическими фактами, но не условиями матрицы.
_RAW_LOCATION_PATHS = {
    "client.region",
    "client.city",
    "assets.realty.region",
    "assets.realty.city",
    "assets.realty_2.region",
    "assets.realty_2.city",
}

# Классы региона устанавливает только проверяемый deterministic источник.
_DERIVED_ONLY_PATHS = {
    "client.region_class",
    "assets.realty.region_class",
    "assets.realty_2.region_class",
}
_DERIVED_SOURCES = {FactSource.DADATA, FactSource.SYSTEM_DERIVED}
_AUTHORITATIVE_CLIENT_STATUSES = {
    FactStatus.KNOWN,
    FactStatus.CONFIRMED,
    FactStatus.CONFLICT,
}


def reduce_fact_candidates(
    facts: dict[str, Any],
    statuses: Mapping[str, str | FactStatus],
    candidates: list[FactCandidate],
    *,
    evidence_text: str,
    matrix: Matrix | None = None,
    already_clarified: frozenset[str] | set[str] | None = None,
) -> FactReductionResult:
    """Атомарно применяет доверенные кандидаты и объясняет каждое решение.

    `evidence_text` должен быть тем же безопасным текстом, который видел extractor.
    Для локальных PII-парсеров доказательство необязательно.

    `already_clarified` — пути, о которых бот уже переспрашивал. Приблизительный
    ответ («вроде», «кажется», «наверное») держится ровно одно уточнение: если
    человек повторил то же самое, значит он так и отвечает, и третий заход
    превращается в допрос. Решение заказчика 11.08.2026 после ролевого прогона,
    где на «нет, вроде нет просрочек» бот переспросил дважды.
    """
    active_matrix = matrix or get_matrix()
    updated_facts = deepcopy(facts)
    updated_statuses = _normalize_statuses(statuses)
    decisions: list[FactDecision] = []
    events: list[FactEvent] = []

    for candidate in candidates:
        decision = _reduce_one(
            updated_facts,
            updated_statuses,
            candidate,
            evidence_text=evidence_text,
            matrix=active_matrix,
            already_clarified=already_clarified or frozenset(),
        )
        decisions.append(decision)
        if event := _event_from_decision(decision):
            events.append(event)

    return FactReductionResult(
        facts=updated_facts,
        statuses=updated_statuses,
        decisions=decisions,
        events=events,
    )


def _reduce_one(
    facts: dict[str, Any],
    statuses: dict[str, FactStatus],
    candidate: FactCandidate,
    *,
    evidence_text: str,
    matrix: Matrix,
    already_clarified: frozenset[str] | set[str] = frozenset(),
) -> FactDecision:
    """Применяет один кандидат после всех fail-closed проверок."""
    if candidate.path not in set(matrix.fields) | _RAW_LOCATION_PATHS:
        return _decision(
            candidate,
            CandidateDisposition.REJECTED,
            reason="unknown_path",
        )

    if (
        candidate.path in _DERIVED_ONLY_PATHS
        and candidate.source not in _DERIVED_SOURCES
    ):
        return _decision(
            candidate,
            CandidateDisposition.REJECTED,
            reason="derived_path_requires_trusted_source",
        )

    if candidate.source == FactSource.USER_EXPLICIT and not _evidence_matches(
        candidate.evidence,
        evidence_text,
    ):
        return _decision(
            candidate,
            CandidateDisposition.REJECTED,
            reason="evidence_not_found",
        )

    previous = get_fact(facts, candidate.path)
    previous_scalar = _as_fact_scalar(previous)

    if candidate.subject != FactSubject.CLIENT:
        # Один ход может одновременно сказать «у жены есть, у меня нет».
        # Чужой объект требует уточнения, но не должен затирать уже известный
        # явный ответ клиента независимо от порядка кандидатов в JSON.
        if statuses.get(candidate.path) not in _AUTHORITATIVE_CLIENT_STATUSES:
            statuses[candidate.path] = FactStatus.NEEDS_CONFIRMATION
        return _decision(
            candidate,
            CandidateDisposition.NEEDS_CONFIRMATION,
            status=FactStatus.NEEDS_CONFIRMATION,
            previous_value=previous_scalar,
            reason="subject_is_not_client",
        )

    if (
        candidate.explicitness == Explicitness.AMBIGUOUS
        and candidate.path not in already_clarified
    ):
        statuses[candidate.path] = FactStatus.NEEDS_CONFIRMATION
        return _decision(
            candidate,
            CandidateDisposition.NEEDS_CONFIRMATION,
            status=FactStatus.NEEDS_CONFIRMATION,
            previous_value=previous_scalar,
            reason="ambiguous_candidate",
        )

    if candidate.operation == FactOperation.CLEAR:
        return _clear_candidate(
            facts,
            statuses,
            candidate,
            previous_value=previous_scalar,
        )

    normalized = _canonical_value(candidate, matrix)
    if normalized is None:
        # Кандидат отклонён, но не должен пропасть без следа: раньше отсутствие
        # ``status`` здесь тихо гасило событие в `_event_from_decision`, и сумма,
        # которую клиент назвал, но которую extractor прислал не в каноническом
        # формате, исчезала из fact_events так, что это было невозможно диагностировать.
        return _decision(
            candidate,
            CandidateDisposition.REJECTED,
            status=(
                FactStatus.KNOWN if previous is not None else FactStatus.UNKNOWN
            ),
            previous_value=previous_scalar,
            reason="non_canonical_value",
        )

    if previous is not None and previous != normalized:
        if candidate.operation != FactOperation.CORRECT:
            statuses[candidate.path] = FactStatus.CONFLICT
            return _decision(
                candidate,
                CandidateDisposition.CONFLICT,
                status=FactStatus.CONFLICT,
                normalized_value=normalized,
                previous_value=previous_scalar,
                reason="different_value_requires_correct_operation",
            )
        set_by_path(facts, candidate.path, normalized)
        statuses[candidate.path] = FactStatus.CONFIRMED
        return _decision(
            candidate,
            CandidateDisposition.ACCEPTED,
            status=FactStatus.CONFIRMED,
            normalized_value=normalized,
            previous_value=previous_scalar,
            reason="explicit_correction_applied",
        )

    if candidate.operation == FactOperation.CORRECT and previous is None:
        statuses[candidate.path] = FactStatus.NEEDS_CONFIRMATION
        return _decision(
            candidate,
            CandidateDisposition.NEEDS_CONFIRMATION,
            status=FactStatus.NEEDS_CONFIRMATION,
            normalized_value=normalized,
            reason="correction_without_previous_value",
        )

    set_by_path(facts, candidate.path, normalized)
    status = FactStatus.CONFIRMED if previous == normalized else FactStatus.KNOWN
    statuses[candidate.path] = status
    return _decision(
        candidate,
        CandidateDisposition.NOOP
        if previous == normalized
        else CandidateDisposition.ACCEPTED,
        status=status,
        normalized_value=normalized,
        previous_value=previous_scalar,
        reason="value_already_known" if previous == normalized else "canonical_value_applied",
    )


def _clear_candidate(
    facts: dict[str, Any],
    statuses: dict[str, FactStatus],
    candidate: FactCandidate,
    *,
    previous_value: FactScalar,
) -> FactDecision:
    """Удаляет отозванный факт, сохраняя UNKNOWN для последующего вопроса."""
    if previous_value is None:
        statuses[candidate.path] = FactStatus.UNKNOWN
        return _decision(
            candidate,
            CandidateDisposition.NOOP,
            status=FactStatus.UNKNOWN,
            reason="clear_without_previous_value",
        )
    _delete_by_path(facts, candidate.path)
    statuses[candidate.path] = FactStatus.UNKNOWN
    return _decision(
        candidate,
        CandidateDisposition.CLEARED,
        status=FactStatus.UNKNOWN,
        previous_value=previous_value,
        reason="explicit_retraction_applied",
    )


def _canonical_value(candidate: FactCandidate, matrix: Matrix) -> FactScalar:
    """Проверяет canonical type, не интерпретируя разговорные синонимы."""
    spec = matrix.fields.get(candidate.path)
    value = candidate.value
    if spec is None:
        return value.strip() if isinstance(value, str) and value.strip() else None

    if spec.kind == "bool":
        return value if isinstance(value, bool) else None

    if spec.kind == "enum":
        if not isinstance(value, str) or not spec.values:
            return None
        # Значение приходит уже готовым кодом матрицы, мимо разбора свободного
        # текста, — поэтому свод синонимов нужен и здесь, иначе `hired` уезжает
        # в CRM отдельным кодом. См. routing/normalize._CANONICAL_SYNONYMS.
        return (
            canonical_enum_value(candidate.path, value)
            if value in spec.values
            else None
        )

    if spec.kind == "money":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return int(value) if value >= 0 else None

    if spec.kind == "int":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        normalized = int(value)
        if candidate.path == "client.age":
            return normalized if 0 < normalized < 120 else None
        if candidate.path == "assets.vehicle.year":
            return (
                normalized
                if 1900 <= normalized <= date.today().year + 1
                else None
            )
        return normalized

    if not isinstance(value, str) or not value.strip():
        return None
    if candidate.path == "client.phone":
        return parse_phone(value)
    return value.strip()


def _evidence_matches(evidence: str | None, evidence_text: str) -> bool:
    """Проверяет grounding без лингвистических догадок."""
    if not evidence or not evidence.strip():
        return False
    normalized_evidence = " ".join(evidence.casefold().split())
    normalized_text = " ".join(evidence_text.casefold().split())
    return normalized_evidence in normalized_text


def _normalize_statuses(
    statuses: Mapping[str, str | FactStatus],
) -> dict[str, FactStatus]:
    """Отбрасывает неизвестные legacy-статусы вместо молчаливого доверия."""
    normalized: dict[str, FactStatus] = {}
    for path, raw in statuses.items():
        try:
            normalized[path] = FactStatus(raw)
        except ValueError:
            continue
    return normalized


def _decision(
    candidate: FactCandidate,
    disposition: CandidateDisposition,
    *,
    reason: str,
    status: FactStatus | None = None,
    normalized_value: FactScalar = None,
    previous_value: FactScalar = None,
) -> FactDecision:
    return FactDecision(
        candidate=candidate,
        disposition=disposition,
        status=status,
        normalized_value=normalized_value,
        previous_value=previous_value,
        reason=reason,
    )


def _event_from_decision(decision: FactDecision) -> FactEvent | None:
    """Сохраняет только решения, влияющие на знание или требующие уточнения."""
    if decision.status is None:
        return None
    return FactEvent(
        path=decision.candidate.path,
        operation=decision.candidate.operation,
        status=decision.status,
        source=decision.candidate.source,
        subject=decision.candidate.subject,
        evidence=decision.candidate.evidence,
        previous_value=decision.previous_value,
        value=decision.normalized_value,
        reason=decision.reason,
    )


def _delete_by_path(facts: dict[str, Any], path: str) -> None:
    """Удаляет leaf и пустые родительские словари внутри копии facts."""
    parts = path.split(".")
    current: dict[str, Any] = facts
    parents: list[tuple[dict[str, Any], str]] = []
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            return
        parents.append((current, part))
        current = child
    current.pop(parts[-1], None)
    for parent, key in reversed(parents):
        child = parent.get(key)
        if isinstance(child, dict) and not child:
            parent.pop(key, None)
        else:
            break


def _as_fact_scalar(value: Any) -> FactScalar:
    """Не пропускает сложные значения в scalar provenance-контракт."""
    return value if isinstance(value, (str, int, float, bool)) or value is None else None
