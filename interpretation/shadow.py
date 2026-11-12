"""Pure shadow comparison: новый контур наблюдает, но не меняет runtime facts."""

from __future__ import annotations

from typing import Any

from routing import Matrix, Plan, get_fact, plan

from .contracts import (
    FactCandidate,
    FactCandidateIssue,
    FactStatus,
    SemanticBoundarySignals,
)
from .reducer import reduce_fact_candidates


_RAW_LOCATION_PATHS = {
    "client.region",
    "client.city",
    "assets.realty.region",
    "assets.realty.city",
}


def begin_shadow_call(
    state: dict[str, Any],
    *,
    mode: str,
    max_calls: int,
) -> bool:
    """Резервирует один shadow-вызов и применяет session budget."""
    metadata = _metadata(state)
    metadata["mode"] = mode
    if mode not in {"shadow", "semantic"}:
        metadata["last_status"] = "not_run"
        return False

    calls = _nonnegative_int(metadata.get("shadow_calls"))
    # В shadow cap защищает экспериментальный бюджет. В semantic это основной
    # extractor: пропуск вызова означал бы молчаливый переход логики посреди
    # сессии, поэтому на него session cap не распространяется.
    if mode == "shadow" and calls >= max_calls:
        metadata["last_status"] = "budget_exhausted"
        metadata["last_error"] = None
        return False

    metadata["shadow_calls"] = calls + 1
    metadata["last_status"] = "running"
    metadata["last_error"] = None
    return True


def record_shadow_error(state: dict[str, Any], error: Exception) -> None:
    """Фиксирует отказ экспериментального контура без исключения наружу."""
    metadata = _metadata(state)
    metadata["last_status"] = "error"
    metadata["last_error"] = f"{type(error).__name__}: {error}"[:500]
    aggregate = _aggregate(metadata)
    aggregate["turns"] += 1
    aggregate["errors"] += 1


def record_shadow_result(
    state: dict[str, Any],
    *,
    base_facts: dict[str, Any],
    legacy_facts: dict[str, Any] | None,
    candidates: list[FactCandidate],
    issues: list[FactCandidateIssue],
    boundaries: SemanticBoundarySignals,
    evidence_text: str,
    matrix: Matrix,
) -> None:
    """Сохраняет semantic-результат и при наличии сравнивает его с legacy."""
    raw_statuses = state.get("fact_statuses")
    statuses = raw_statuses if isinstance(raw_statuses, dict) else {}
    reduction = reduce_fact_candidates(
        base_facts,
        statuses,
        candidates,
        evidence_text=evidence_text,
        matrix=matrix,
    )
    comparison = (
        _compare_facts(
            base_facts,
            legacy_facts,
            reduction.facts,
            candidate_paths={candidate.path for candidate in candidates},
            matrix=matrix,
        )
        if legacy_facts is not None
        else None
    )
    legacy_plan = (
        _plan_signature(plan(matrix, legacy_facts))
        if legacy_facts is not None
        else None
    )
    shadow_plan = _plan_signature(plan(matrix, reduction.facts))
    route_equal = legacy_plan == shadow_plan if legacy_plan is not None else None

    decisions = [
        {
            "path": decision.candidate.path,
            "disposition": decision.disposition.value,
            "status": decision.status.value if decision.status else None,
            "reason": decision.reason,
        }
        for decision in reduction.decisions
    ]
    metadata = _metadata(state)
    metadata["last_status"] = "success"
    metadata["last_error"] = None
    metadata["last_result"] = {
        "candidates": [
            candidate.model_dump(mode="json") for candidate in candidates
        ],
        "candidate_issues": [
            issue.model_dump(mode="json") for issue in issues
        ],
        "boundaries": boundaries.model_dump(mode="json"),
        "decisions": decisions,
        "statuses": {
            path: status.value for path, status in reduction.statuses.items()
        },
        "comparison": comparison,
        "pre_geo_plan": {
            "legacy": legacy_plan,
            "shadow": shadow_plan,
            "equal": route_equal,
        },
    }

    aggregate = _aggregate(metadata)
    aggregate["turns"] += 1
    aggregate["successes"] += 1
    aggregate["needs_confirmation"] += sum(
        decision.status == FactStatus.NEEDS_CONFIRMATION
        for decision in reduction.decisions
    )
    aggregate["candidate_issues"] += len(issues)
    aggregate["invalid_schema_issues"] += sum(
        issue.code.value == "invalid_schema" for issue in issues
    )
    aggregate["domain_policy_issues"] += sum(
        issue.code.value.startswith("domain_policy_") for issue in issues
    )
    if comparison is not None:
        aggregate["agreement_paths"] += len(comparison["agreement_paths"])
        aggregate["legacy_only_paths"] += len(comparison["legacy_only_paths"])
        aggregate["shadow_only_paths"] += len(comparison["shadow_only_paths"])
        aggregate["conflict_paths"] += len(comparison["conflict_paths"])
        if route_equal:
            aggregate["plan_matches"] += 1
        if (
            route_equal
            and not comparison["legacy_only_paths"]
            and not comparison["shadow_only_paths"]
            and not comparison["conflict_paths"]
        ):
            aggregate["exact_turns"] += 1


def _compare_facts(
    base: dict[str, Any],
    legacy: dict[str, Any],
    shadow: dict[str, Any],
    *,
    candidate_paths: set[str],
    matrix: Matrix,
) -> dict[str, list[str]]:
    """Классифицирует расхождения без сохранения чувствительных значений."""
    result: dict[str, list[str]] = {
        "agreement_paths": [],
        "legacy_only_paths": [],
        "shadow_only_paths": [],
        "conflict_paths": [],
    }
    paths = set(matrix.fields) | _RAW_LOCATION_PATHS
    for path in sorted(paths):
        base_value = get_fact(base, path)
        legacy_value = get_fact(legacy, path)
        shadow_value = get_fact(shadow, path)
        legacy_changed = legacy_value != base_value
        shadow_changed = shadow_value != base_value
        touched = legacy_changed or shadow_changed or path in candidate_paths
        if not touched:
            continue
        if legacy_value == shadow_value:
            result["agreement_paths"].append(path)
        elif legacy_changed and not shadow_changed:
            result["legacy_only_paths"].append(path)
        elif shadow_changed and not legacy_changed:
            result["shadow_only_paths"].append(path)
        else:
            result["conflict_paths"].append(path)
    return result


def _plan_signature(current_plan: Plan) -> dict[str, Any]:
    """Компактный pre-geo след для метрики влияния на orchestration."""
    return {
        "action": current_plan.action,
        "fields": current_plan.field_paths,
        "step": current_plan.step,
        "matched": current_plan.matched.id if current_plan.matched else None,
    }


def _metadata(state: dict[str, Any]) -> dict[str, Any]:
    raw = state.get("interpretation")
    if isinstance(raw, dict):
        metadata = raw
    else:
        metadata = {}
        state["interpretation"] = metadata
    metadata.setdefault("shadow_calls", 0)
    metadata.setdefault("last_result", None)
    metadata.setdefault("runtime_source", "legacy")
    _aggregate(metadata)
    return metadata


def _aggregate(metadata: dict[str, Any]) -> dict[str, int]:
    raw = metadata.get("aggregate")
    aggregate: dict[str, int]
    if isinstance(raw, dict):
        aggregate = {
            key: _nonnegative_int(raw.get(key))
            for key in _aggregate_keys()
        }
    else:
        aggregate = {key: 0 for key in _aggregate_keys()}
    metadata["aggregate"] = aggregate
    return aggregate


def _aggregate_keys() -> tuple[str, ...]:
    return (
        "turns",
        "successes",
        "errors",
        "exact_turns",
        "plan_matches",
        "agreement_paths",
        "legacy_only_paths",
        "shadow_only_paths",
        "conflict_paths",
        "needs_confirmation",
        "candidate_issues",
        "invalid_schema_issues",
        "domain_policy_issues",
    )


def _nonnegative_int(value: Any) -> int:
    return max(0, value) if isinstance(value, int) and not isinstance(value, bool) else 0
