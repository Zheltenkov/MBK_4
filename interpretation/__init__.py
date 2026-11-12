"""Deep module интерпретации фактов.

Публичный interface намеренно мал: typed-кандидаты входят в reducer, а наружу
выходят canonical facts, статусы, решения и provenance-события.
"""

from .contracts import (
    CandidateDisposition,
    CandidateIssueCode,
    Explicitness,
    FactCandidate,
    FactCandidateExtraction,
    FactCandidateIssue,
    FactDecision,
    FactEvidenceBasis,
    FactEvent,
    FactOperation,
    FactReductionResult,
    FactSource,
    FactStatus,
    FactSubject,
    FactValuePrecision,
    GoalSignal,
    NewBorrowingStance,
    PaymentStatusSignal,
    PreferenceStance,
    SemanticBoundarySignals,
)
from .policies import apply_candidate_policies
from .reducer import reduce_fact_candidates
from .shadow import (
    begin_shadow_call,
    record_shadow_error,
    record_shadow_result,
)

__all__ = [
    "CandidateDisposition",
    "CandidateIssueCode",
    "Explicitness",
    "FactCandidate",
    "FactCandidateExtraction",
    "FactCandidateIssue",
    "FactDecision",
    "FactEvidenceBasis",
    "FactEvent",
    "FactOperation",
    "FactReductionResult",
    "FactSource",
    "FactStatus",
    "FactSubject",
    "FactValuePrecision",
    "GoalSignal",
    "NewBorrowingStance",
    "PaymentStatusSignal",
    "PreferenceStance",
    "SemanticBoundarySignals",
    "apply_candidate_policies",
    "begin_shadow_call",
    "record_shadow_error",
    "record_shadow_result",
    "reduce_fact_candidates",
]
