"""Offline scoring и budget guards live-eval без OpenRouter."""

from __future__ import annotations

import pytest

from evaluation.fact_interpretation import load_fact_interpretation_cases
from evaluation.fact_live import (
    aggregate_fact_interpretation_results,
    score_fact_interpretation_case,
)
from interpretation import (
    CandidateIssueCode,
    FactCandidate,
    FactCandidateIssue,
)
from run_fact_interpretation_eval import MAX_LIVE_CASES, _select_cases


EMPTY_USAGE = {
    "total_calls": 0,
    "total_input_tokens": 0,
    "total_output_tokens": 0,
    "total_cached_tokens": 0,
    "total_cost_usd": 0.0,
}


def _case(case_id: str):
    return next(
        case
        for case in load_fact_interpretation_cases()
        if case.id == case_id
    )


def test_exact_ground_truth_candidates_pass(matrix):
    case = _case("goal_cash_with_amount")

    result = score_fact_interpretation_case(
        case,
        case.candidates,
        matrix=matrix,
        latency_seconds=1.25,
        usage=EMPTY_USAGE,
    )

    assert result["passed"] is True
    assert result["path_counts"] == {
        "tp": 2,
        "required_tp": 2,
        "optional_tp": 0,
        "fp": 0,
        "fn": 0,
        "expected": 2,
        "optional_expected": 0,
        "actual": 2,
        "candidate_exact": 2,
    }


def test_optional_candidate_may_be_present_or_absent(matrix):
    case = _case("credit_no_current_overdue")

    without_optional = score_fact_interpretation_case(
        case,
        case.candidates,
        matrix=matrix,
        latency_seconds=1.0,
        usage=EMPTY_USAGE,
    )
    with_optional = score_fact_interpretation_case(
        case,
        [*case.candidates, *case.optional_candidates],
        matrix=matrix,
        latency_seconds=1.0,
        usage=EMPTY_USAGE,
    )

    assert without_optional["passed"] is True
    assert with_optional["passed"] is True
    assert with_optional["path_counts"]["optional_tp"] == 1


def test_same_path_for_spouse_and_client_is_not_a_duplicate(matrix):
    """Субъект входит в semantic key одного утверждения."""
    case = _case("spouse_property_not_client")

    result = score_fact_interpretation_case(
        case,
        case.candidates,
        matrix=matrix,
        latency_seconds=1.0,
        usage=EMPTY_USAGE,
    )

    assert result["passed"] is True
    assert result["path_counts"]["required_tp"] == 3
    assert result["path_counts"]["fp"] == 0
    assert result["actual_facts"]["assets"]["has_realty"] is False


def test_wrong_value_fails_attributes_and_reducer(matrix):
    case = _case("goal_cash_with_amount")
    wrong = [
        case.candidates[0],
        case.candidates[1].model_copy(update={"value": 900_000}),
    ]

    result = score_fact_interpretation_case(
        case,
        wrong,
        matrix=matrix,
        latency_seconds=1.0,
        usage=EMPTY_USAGE,
    )

    assert result["passed"] is False
    assert result["extraction_passed"] is False
    assert result["reducer_passed"] is False
    assert result["candidate_mismatches"][0]["fields"] == ["value"]


def test_ambiguous_raw_value_can_vary_without_inventing_a_fact(matrix):
    case = _case("ambiguous_debt_range")
    actual = [
        case.candidates[0].model_copy(
            update={"value": "примерно от 300 до 500 тысяч"}
        )
    ]

    result = score_fact_interpretation_case(
        case,
        actual,
        matrix=matrix,
        latency_seconds=1.0,
        usage=EMPTY_USAGE,
    )

    assert result["passed"] is True
    assert result["actual_facts"] == {}
    assert result["actual_statuses"]["debts.total"] == "needs_confirmation"


def test_unexpected_candidate_counts_as_false_positive(matrix):
    case = _case("no_relevant_fact")
    actual = [
        FactCandidate(
            path="client.goal",
            value="cash",
            evidence="хочу",
        )
    ]

    result = score_fact_interpretation_case(
        case,
        actual,
        matrix=matrix,
        latency_seconds=1.0,
        usage=EMPTY_USAGE,
    )

    assert result["passed"] is False
    assert result["path_counts"]["fp"] == 1
    assert result["candidate_mismatches"][0]["reason"] == "unexpected"


def test_aggregate_reports_precision_recall_and_failed_ids(matrix):
    passed_case = _case("goal_refinance")
    failed_case = _case("vehicle_absent")
    passed = score_fact_interpretation_case(
        passed_case,
        passed_case.candidates,
        matrix=matrix,
        latency_seconds=1.0,
        usage=EMPTY_USAGE,
    )
    failed = score_fact_interpretation_case(
        failed_case,
        [],
        matrix=matrix,
        latency_seconds=2.0,
        usage=EMPTY_USAGE,
    )

    summary = aggregate_fact_interpretation_results([passed, failed])

    assert summary["cases"] == 2
    assert summary["passed_cases"] == 1
    assert summary["path_precision"] == 1.0
    assert summary["path_recall"] == 0.5
    assert summary["failed_case_ids"] == ["vehicle_absent"]
    assert summary["latency_seconds"]["p50"] == 1.5


def test_candidate_issues_are_observable_without_failing_valid_case(matrix):
    case = _case("employment_retired")
    issue = FactCandidateIssue(
        raw_index=1,
        path="employment.type",
        code=CandidateIssueCode.DOMAIN_POLICY_SUPERSEDED,
        message="retired supersedes unemployed",
    )
    result = score_fact_interpretation_case(
        case,
        case.candidates,
        matrix=matrix,
        latency_seconds=1.0,
        usage=EMPTY_USAGE,
        candidate_issues=[issue],
    )

    summary = aggregate_fact_interpretation_results([result])

    assert result["passed"] is True
    assert result["candidate_issues"][0]["code"] == (
        "domain_policy_superseded"
    )
    assert summary["candidate_issues"] == {
        "total": 1,
        "by_code": {"domain_policy_superseded": 1},
    }


def test_live_case_selection_has_hard_cap():
    cases = load_fact_interpretation_cases()

    with pytest.raises(ValueError, match="от 1 до 50"):
        _select_cases(cases, [], MAX_LIVE_CASES + 1)
    with pytest.raises(ValueError, match="неизвестные case id"):
        _select_cases(cases, ["missing_case"], MAX_LIVE_CASES)
