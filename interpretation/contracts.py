"""Typed-контракты между semantic extractor и deterministic reducer."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from routing.entry_intent import EntryIntent


FactScalar = str | int | float | bool | None


class FactOperation(StrEnum):
    """Намерение изменения state, а не лингвистическая формулировка клиента."""

    SET = "set"
    CORRECT = "correct"
    CLEAR = "clear"


class FactSource(StrEnum):
    """Проверяемое происхождение кандидата факта."""

    USER_EXPLICIT = "user_explicit"
    FORM = "form"
    LOCAL_PARSER = "local_parser"
    DADATA = "dadata"
    SYSTEM_DERIVED = "system_derived"


class Explicitness(StrEnum):
    """Можно ли применять утверждение без дополнительного вопроса."""

    EXPLICIT = "explicit"
    AMBIGUOUS = "ambiguous"


class FactValuePrecision(StrEnum):
    """Форма значения отдельно от уверенности в самом утверждении."""

    EXACT = "exact"
    POINT_ESTIMATE = "point_estimate"
    RANGE = "range"
    VAGUE = "vague"


class FactEvidenceBasis(StrEnum):
    """Как evidence связано с canonical-фактом.

    Поле отделяет понимание перефразированной речи от догадки по корреляции.
    Например, «платить стало тяжело» описывает финансовую нагрузку, но само по
    себе не утверждает наличие просрочки.
    """

    DIRECT = "direct"
    CONTEXTUAL = "contextual"
    INFERRED = "inferred"


class PreferenceStance(StrEnum):
    """Независимая позиция клиента по одному варианту решения."""

    NOT_MENTIONED = "not_mentioned"
    ACCEPTS = "accepts"
    REJECTS = "rejects"


class NewBorrowingStance(StrEnum):
    """Отношение именно к новому займу, не к продуктам вообще."""

    NOT_MENTIONED = "not_mentioned"
    ACCEPTS = "accepts"
    REJECTS = "rejects"


class PaymentStatusSignal(StrEnum):
    """Взаимоисключающие смыслы про просрочку и трудность платежей."""

    NOT_MENTIONED = "not_mentioned"
    DIFFICULTY_ONLY = "difficulty_only"
    NO_CURRENT_OVERDUE = "no_current_overdue"
    PAST_OVERDUE = "past_overdue"
    CURRENT_OVERDUE = "current_overdue"
    BANKRUPTCY_READY = "bankruptcy_ready"


class GoalSignal(StrEnum):
    """Независимая классификация задачи, ради которой пришёл клиент."""

    NOT_MENTIONED = "not_mentioned"
    CASH = "cash"
    DEBT_RELIEF = "debt_relief"
    REFINANCE = "refinance"


class SemanticBoundarySignals(BaseModel):
    """Контрастные оси, не позволяющие смешать соседние бизнес-понятия."""

    model_config = ConfigDict(extra="forbid")

    payment_status: PaymentStatusSignal = PaymentStatusSignal.NOT_MENTIONED
    goal: GoalSignal = GoalSignal.NOT_MENTIONED
    new_borrowing: NewBorrowingStance = NewBorrowingStance.NOT_MENTIONED
    alternatives: PreferenceStance = PreferenceStance.NOT_MENTIONED
    collateral: PreferenceStance = PreferenceStance.NOT_MENTIONED
    realty_collateral: PreferenceStance = PreferenceStance.NOT_MENTIONED
    vehicle_collateral: PreferenceStance = PreferenceStance.NOT_MENTIONED


class FactSubject(StrEnum):
    """О ком именно сказан факт."""

    CLIENT = "client"
    SPOUSE = "spouse"
    OTHER = "other"
    UNKNOWN = "unknown"


class FactStatus(StrEnum):
    """Состояние знания по пути факта.

    Набор сохраняет полезную модель v3, но статус вычисляет reducer, а не LLM.
    """

    KNOWN = "known"
    UNKNOWN = "unknown"
    NEEDS_CONFIRMATION = "needs_confirmation"
    CONFIRMED = "confirmed"
    NOT_APPLICABLE = "not_applicable"
    CONFLICT = "conflict"


class CandidateDisposition(StrEnum):
    """Решение reducer по одному кандидату."""

    ACCEPTED = "accepted"
    CLEARED = "cleared"
    NEEDS_CONFIRMATION = "needs_confirmation"
    CONFLICT = "conflict"
    REJECTED = "rejected"
    NOOP = "noop"


class CandidateIssueCode(StrEnum):
    """Машиночитаемая причина потери отдельного кандидата."""

    INVALID_SCHEMA = "invalid_schema"
    DOMAIN_POLICY_SUPERSEDED = "domain_policy_superseded"
    DOMAIN_POLICY_APPLIED = "domain_policy_applied"


class FactCandidate(BaseModel):
    """Кандидат semantic extractor до доверенной записи в state."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    value: FactScalar = None
    operation: FactOperation = FactOperation.SET
    source: FactSource = FactSource.USER_EXPLICIT
    evidence: str | None = None
    explicitness: Explicitness = Explicitness.EXPLICIT
    precision: FactValuePrecision = FactValuePrecision.EXACT
    evidence_basis: FactEvidenceBasis = FactEvidenceBasis.DIRECT
    subject: FactSubject = FactSubject.CLIENT

    @model_validator(mode="after")
    def validate_operation_value(self) -> "FactCandidate":
        """Не допускает логически противоречивый контракт до reducer."""
        if self.operation == FactOperation.CLEAR and self.value is not None:
            raise ValueError("operation=clear требует value=null")
        if self.operation != FactOperation.CLEAR and self.value is None:
            raise ValueError("operation=set/correct требует непустое value")
        return self


class FactCandidateIssue(BaseModel):
    """Диагностика отбрасывания или нормализации внутри одного batch."""

    model_config = ConfigDict(extra="forbid")

    raw_index: int | None = Field(default=None, ge=0)
    path: str | None = None
    code: CandidateIssueCode
    message: str = Field(min_length=1, max_length=500)


class FactCandidateExtraction(BaseModel):
    """Ограниченный structured output semantic extractor одного хода."""

    model_config = ConfigDict(extra="forbid")

    candidates: list[FactCandidate] = Field(default_factory=list, max_length=12)
    boundaries: SemanticBoundarySignals = Field(
        default_factory=SemanticBoundarySignals
    )
    issues: list[FactCandidateIssue] = Field(default_factory=list, max_length=12)
    # Намерение реплики для развилки на входе. None — модель поле не вернула
    # или вернула не то: тогда решает словарь-страховка (routing/entry_intent.py).
    entry_intent: EntryIntent | None = None

    def mentions_money(self) -> bool:
        """Нашла ли модель в этом же ответе деньги или долги клиента.

        Нужен развилке на входе: «не наш профиль» при найденной цели или
        долговых фактах — противоречие внутри одного ответа, то есть сомнение.
        """
        if self.boundaries.goal is not GoalSignal.NOT_MENTIONED:
            return True
        if self.boundaries.payment_status is not PaymentStatusSignal.NOT_MENTIONED:
            return True
        return any(
            candidate.path == "client.goal"
            or candidate.path.startswith(("request.", "debts."))
            for candidate in self.candidates
        )


class FactDecision(BaseModel):
    """Объяснимое решение reducer, включая причину отказа или уточнения."""

    model_config = ConfigDict(extra="forbid")

    candidate: FactCandidate
    disposition: CandidateDisposition
    status: FactStatus | None = None
    normalized_value: FactScalar = None
    previous_value: FactScalar = None
    reason: str


class FactEvent(BaseModel):
    """Provenance-событие, пригодное для session state и observability."""

    model_config = ConfigDict(extra="forbid")

    path: str
    operation: FactOperation
    status: FactStatus
    source: FactSource
    subject: FactSubject
    evidence: str | None = None
    previous_value: FactScalar = None
    value: FactScalar = None
    reason: str


class FactReductionResult(BaseModel):
    """Полный результат одного атомарного прохода reducer."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    facts: dict[str, Any]
    statuses: dict[str, FactStatus]
    decisions: list[FactDecision] = Field(default_factory=list)
    events: list[FactEvent] = Field(default_factory=list)
