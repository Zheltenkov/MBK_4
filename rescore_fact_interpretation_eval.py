"""Пересчёт сохранённых LLM-ответов после уточнения ground truth, без API."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from evaluation.fact_interpretation import load_fact_interpretation_cases
from evaluation.fact_live import (
    aggregate_fact_interpretation_results,
    score_fact_interpretation_case,
)
from interpretation import FactCandidate, FactCandidateIssue
from routing import get_matrix


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rescore сохранённых fact interpretation eval artifacts",
    )
    parser.add_argument(
        "--source-dirs",
        type=Path,
        nargs="+",
        required=True,
        help="от старых к новым; более новый case-файл имеет приоритет",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def _load_latest_case_artifact(
    case_id: str,
    source_dirs: list[Path],
) -> dict[str, Any]:
    for source_dir in reversed(source_dirs):
        path = source_dir / f"{case_id}.json"
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
            raise ValueError(f"{path} не содержит JSON object")
    raise FileNotFoundError(f"нет сохранённого результата для {case_id}")


def _incurred_usage(source_dirs: list[Path]) -> dict[str, Any]:
    totals = {
        "total_calls": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cached_tokens": 0,
        "total_cost_usd": 0.0,
    }
    for source_dir in source_dirs:
        summary_path = source_dir / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        usage = summary.get("usage") or {}
        for key in totals:
            totals[key] += usage.get(key, 0) or 0
    totals["total_cost_usd"] = round(
        float(totals["total_cost_usd"]),
        6,
    )
    return totals


def main() -> int:
    args = _parser().parse_args()
    cases = load_fact_interpretation_cases()
    matrix = get_matrix()
    results: list[dict[str, Any]] = []
    for case in cases:
        artifact = _load_latest_case_artifact(case.id, args.source_dirs)
        candidates = [
            FactCandidate.model_validate(candidate)
            for candidate in artifact.get("actual_candidates", [])
        ]
        candidate_issues = [
            FactCandidateIssue.model_validate(issue)
            for issue in artifact.get("candidate_issues", [])
        ]
        results.append(
            score_fact_interpretation_case(
                case,
                candidates,
                matrix=matrix,
                latency_seconds=float(
                    artifact.get("latency_seconds", 0.0) or 0.0
                ),
                usage=artifact.get("usage") or {},
                candidate_issues=candidate_issues,
            )
        )

    summary = {
        "schema_version": "mbk.fact-interpretation-rescore.v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "evidence_type": "incremental_mixed_prompt_revisions",
        "openrouter_calls_during_rescore": 0,
        "crm_called": False,
        "dadata_called": False,
        "source_dirs": [str(path) for path in args.source_dirs],
        "experiment_incurred_usage": _incurred_usage(args.source_dirs),
        **aggregate_fact_interpretation_results(results),
    }
    args.output_dir.mkdir(parents=True, exist_ok=False)
    for result in results:
        (args.output_dir / f"{result['id']}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
