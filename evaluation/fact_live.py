"""Scoring live semantic extraction относительно versioned ground truth."""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

from interpretation import (
    Explicitness,
    FactCandidate,
    FactCandidateIssue,
    FactReductionResult,
    FactSubject,
    reduce_fact_candidates,
)
from routing import Matrix, get_fact

from .fact_interpretation import FactInterpretationCase


def score_fact_interpretation_case(
    case: FactInterpretationCase,
    actual_candidates: list[FactCandidate],
    *,
    matrix: Matrix,
    latency_seconds: float,
    usage: dict[str, Any],
    candidate_issues: list[FactCandidateIssue] | None = None,
) -> dict[str, Any]:
    """Считает extraction и reducer отдельно, чтобы локализовать причину ошибки."""
    expected_by_key = {
        _candidate_key(candidate): candidate for candidate in case.candidates
    }
    optional_by_key = {
        _candidate_key(candidate): candidate
        for candidate in case.optional_candidates
    }
    actual_by_key: dict[
        tuple[str, FactSubject],
        list[FactCandidate],
    ] = defaultdict(list)
    for candidate in actual_candidates:
        actual_by_key[_candidate_key(candidate)].append(candidate)

    expected_keys = set(expected_by_key)
    optional_keys = set(optional_by_key)
    allowed_keys = expected_keys | optional_keys
    actual_keys = set(actual_by_key)
    required_tp = len(expected_keys & actual_keys)
    optional_tp = len(optional_keys & actual_keys)
    path_tp = required_tp + optional_tp
    path_fp = sum(
        len(candidates)
        for key, candidates in actual_by_key.items()
        if key not in allowed_keys
    ) + sum(
        max(0, len(candidates) - 1)
        for key, candidates in actual_by_key.items()
        if key in allowed_keys
    )
    path_fn = len(expected_keys - actual_keys)

    candidate_mismatches: list[dict[str, Any]] = []
    candidate_exact = 0
    for key, expected in expected_by_key.items():
        actual_for_key = actual_by_key.get(key, [])
        if len(actual_for_key) != 1:
            candidate_mismatches.append(
                {
                    **_key_report(key),
                    "reason": "missing"
                    if not actual_for_key
                    else "duplicate",
                }
            )
            continue
        actual = actual_for_key[0]
        mismatched_fields = _candidate_mismatched_fields(expected, actual)
        if mismatched_fields:
            candidate_mismatches.append(
                {
                    **_key_report(key),
                    "reason": "attributes",
                    "fields": mismatched_fields,
                    "expected": expected.model_dump(mode="json"),
                    "actual": actual.model_dump(mode="json"),
                }
            )
        else:
            candidate_exact += 1

    optional_mismatch = False
    for key, expected in optional_by_key.items():
        actual_for_key = actual_by_key.get(key, [])
        if not actual_for_key:
            continue
        if len(actual_for_key) != 1:
            optional_mismatch = True
            candidate_mismatches.append(
                {**_key_report(key), "reason": "optional_duplicate"}
            )
            continue
        mismatched_fields = _candidate_mismatched_fields(
            expected,
            actual_for_key[0],
        )
        if mismatched_fields:
            optional_mismatch = True
            candidate_mismatches.append(
                {
                    **_key_report(key),
                    "reason": "optional_attributes",
                    "fields": mismatched_fields,
                    "expected": expected.model_dump(mode="json"),
                    "actual": actual_for_key[0].model_dump(mode="json"),
                }
            )

    for key in sorted(
        actual_keys - allowed_keys,
        key=lambda item: (item[0], item[1].value),
    ):
        candidate_mismatches.append(
            {
                **_key_report(key),
                "reason": "unexpected",
                "actual": [
                    candidate.model_dump(mode="json")
                    for candidate in actual_by_key[key]
                ],
            }
        )

    actual_reduction = reduce_fact_candidates(
        case.initial_facts,
        {},
        actual_candidates,
        evidence_text=case.utterance,
        matrix=matrix,
    )
    reducer_checks = _reducer_checks(case, actual_reduction)
    reducer_passed = all(check["passed"] for check in reducer_checks)
    extraction_passed = (
        path_fp == 0
        and path_fn == 0
        and candidate_exact == len(case.candidates)
        and not optional_mismatch
    )

    return {
        "id": case.id,
        "phenomenon": case.phenomenon,
        "utterance": case.utterance,
        "passed": extraction_passed and reducer_passed,
        "extraction_passed": extraction_passed,
        "reducer_passed": reducer_passed,
        "path_counts": {
            "tp": path_tp,
            "required_tp": required_tp,
            "optional_tp": optional_tp,
            "fp": path_fp,
            "fn": path_fn,
            "expected": len(case.candidates),
            "optional_expected": len(case.optional_candidates),
            "actual": len(actual_candidates),
            "candidate_exact": candidate_exact,
        },
        "candidate_mismatches": candidate_mismatches,
        "candidate_issues": [
            issue.model_dump(mode="json")
            for issue in (candidate_issues or [])
        ],
        "actual_candidates": [
            candidate.model_dump(mode="json")
            for candidate in actual_candidates
        ],
        "actual_decisions": [
            decision.model_dump(mode="json")
            for decision in actual_reduction.decisions
        ],
        "actual_facts": actual_reduction.facts,
        "actual_statuses": {
            path: status.value
            for path, status in actual_reduction.statuses.items()
        },
        "reducer_checks": reducer_checks,
        "latency_seconds": round(latency_seconds, 3),
        "usage": usage,
    }


def score_fact_interpretation_error(
    case: FactInterpretationCase,
    error: Exception,
    *,
    latency_seconds: float,
    usage: dict[str, Any],
) -> dict[str, Any]:
    """Единый формат результата для transport/schema errors."""
    return {
        "id": case.id,
        "phenomenon": case.phenomenon,
        "utterance": case.utterance,
        "passed": False,
        "extraction_passed": False,
        "reducer_passed": False,
        "error": f"{type(error).__name__}: {error}"[:1000],
        "path_counts": {
            "tp": 0,
            "required_tp": 0,
            "optional_tp": 0,
            "fp": 0,
            "fn": len(case.candidates),
            "expected": len(case.candidates),
            "optional_expected": len(case.optional_candidates),
            "actual": 0,
            "candidate_exact": 0,
        },
        "candidate_mismatches": [],
        "candidate_issues": [],
        "actual_candidates": [],
        "actual_decisions": [],
        "actual_facts": case.initial_facts,
        "actual_statuses": {},
        "reducer_checks": [],
        "latency_seconds": round(latency_seconds, 3),
        "usage": usage,
    }


def aggregate_fact_interpretation_results(
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Сводит case reports в migration gate без обращения к модели."""
    counts = {
        "tp": 0,
        "required_tp": 0,
        "optional_tp": 0,
        "fp": 0,
        "fn": 0,
        "expected": 0,
        "optional_expected": 0,
        "actual": 0,
    }
    exact_candidates = 0
    total_cost = 0.0
    total_calls = 0
    total_input = 0
    total_output = 0
    total_cached = 0
    latencies: list[float] = []
    issue_counts: dict[str, int] = defaultdict(int)
    by_phenomenon: dict[str, dict[str, int]] = defaultdict(
        lambda: {"cases": 0, "passed": 0}
    )

    for result in results:
        path_counts = result.get("path_counts") or {}
        for key in counts:
            counts[key] += int(path_counts.get(key, 0) or 0)
        exact_candidates += int(path_counts.get("candidate_exact", 0) or 0)
        usage = result.get("usage") or {}
        total_cost += float(usage.get("total_cost_usd", 0.0) or 0.0)
        total_calls += int(usage.get("total_calls", 0) or 0)
        total_input += int(usage.get("total_input_tokens", 0) or 0)
        total_output += int(usage.get("total_output_tokens", 0) or 0)
        total_cached += int(usage.get("total_cached_tokens", 0) or 0)
        latency = float(result.get("latency_seconds", 0.0) or 0.0)
        if latency > 0:
            latencies.append(latency)
        for issue in result.get("candidate_issues") or []:
            if isinstance(issue, dict):
                code = str(issue.get("code") or "unknown")
                issue_counts[code] += 1
        phenomenon = str(result.get("phenomenon") or "unknown")
        by_phenomenon[phenomenon]["cases"] += 1
        by_phenomenon[phenomenon]["passed"] += int(
            bool(result.get("passed"))
        )

    precision = _safe_ratio(counts["tp"], counts["tp"] + counts["fp"])
    recall = _safe_ratio(
        counts["required_tp"],
        counts["required_tp"] + counts["fn"],
    )
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    passed_cases = sum(bool(result.get("passed")) for result in results)
    errors = sum("error" in result for result in results)
    expected_candidates = counts["expected"]

    return {
        "cases": len(results),
        "passed_cases": passed_cases,
        "case_pass_rate": round(_safe_ratio(passed_cases, len(results)), 4),
        "errors": errors,
        "path_precision": round(precision, 4),
        "path_recall": round(recall, 4),
        "path_f1": round(f1, 4),
        "candidate_attribute_accuracy": round(
            _safe_ratio(exact_candidates, expected_candidates),
            4,
        ),
        "candidate_issues": {
            "total": sum(issue_counts.values()),
            "by_code": dict(sorted(issue_counts.items())),
        },
        "path_counts": counts,
        "usage": {
            "total_calls": total_calls,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cached_tokens": total_cached,
            "cache_hit_ratio": round(
                _safe_ratio(total_cached, total_input),
                4,
            ),
            "total_cost_usd": round(total_cost, 6),
        },
        "latency_seconds": _latency_summary(latencies),
        "by_phenomenon": {
            key: {
                **value,
                "pass_rate": round(
                    _safe_ratio(value["passed"], value["cases"]),
                    4,
                ),
            }
            for key, value in sorted(by_phenomenon.items())
        },
        "failed_case_ids": [
            str(result.get("id"))
            for result in results
            if not result.get("passed")
        ],
    }


def _candidate_mismatched_fields(
    expected: FactCandidate,
    actual: FactCandidate,
) -> list[str]:
    mismatches: list[str] = []
    if (
        expected.explicitness != Explicitness.AMBIGUOUS
        and expected.value != actual.value
    ):
        mismatches.append("value")
    # evidence_basis — fail-closed управляющий сигнал между extractor и policy.
    # Его проверяют policy-тесты и реальные false-positive кейсы, но не точное
    # совпадение wording-корпуса: две допустимые трактовки direct/contextual не
    # должны искусственно занижать semantic quality.
    for field in ("operation", "explicitness", "precision", "subject"):
        if getattr(expected, field) != getattr(actual, field):
            mismatches.append(field)
    return mismatches


def _reducer_checks(
    case: FactInterpretationCase,
    actual_reduction: FactReductionResult,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for path, expected in case.expected_values.items():
        actual = get_fact(actual_reduction.facts, path)
        checks.append(
            {
                "kind": "value",
                "path": path,
                "expected": expected,
                "actual": actual,
                "passed": actual == expected,
            }
        )
    for path in case.expected_absent_paths:
        actual = get_fact(actual_reduction.facts, path)
        checks.append(
            {
                "kind": "absent",
                "path": path,
                "expected": None,
                "actual": actual,
                "passed": actual is None,
            }
        )
    for path, expected in case.expected_statuses.items():
        actual_status = actual_reduction.statuses.get(path)
        actual = actual_status.value if actual_status else None
        checks.append(
            {
                "kind": "status",
                "path": path,
                "expected": expected.value,
                "actual": actual,
                "passed": actual == expected.value,
            }
        )

    expected_dispositions = {
        _candidate_key(candidate): disposition.value
        for candidate, disposition in zip(
            case.candidates,
            case.expected_dispositions,
            strict=True,
        )
    }
    actual_dispositions: dict[
        tuple[str, FactSubject],
        list[str],
    ] = defaultdict(list)
    for decision in actual_reduction.decisions:
        actual_dispositions[_candidate_key(decision.candidate)].append(
            decision.disposition.value
        )
    for key, expected in expected_dispositions.items():
        actual = actual_dispositions.get(key, [])
        checks.append(
            {
                "kind": "disposition",
                **_key_report(key),
                "expected": expected,
                "actual": actual,
                "passed": actual == [expected],
            }
        )
    return checks


def _candidate_key(candidate: FactCandidate) -> tuple[str, FactSubject]:
    """Различает одинаковый факт о клиенте, супруге и другом лице."""
    return candidate.path, candidate.subject


def _key_report(key: tuple[str, FactSubject]) -> dict[str, str]:
    """Сериализуемое представление semantic key для eval-артефакта."""
    path, subject = key
    return {"path": path, "subject": subject.value}


def _latency_summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    ordered = sorted(values)
    return {
        "mean": round(statistics.fmean(ordered), 3),
        "p50": round(statistics.median(ordered), 3),
        "p95": round(_percentile(ordered, 0.95), 3),
        "max": round(max(ordered), 3),
    }


def _percentile(ordered: list[float], quantile: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _safe_ratio(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0
