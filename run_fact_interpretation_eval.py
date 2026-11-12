"""Controlled live-eval semantic extractor. CRM и DaData не импортируются."""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from config import AppConfig, load_config
from evaluation.fact_interpretation import (
    DEFAULT_CASES_PATH,
    FactInterpretationCase,
    load_fact_interpretation_cases,
)
from evaluation.fact_live import (
    aggregate_fact_interpretation_results,
    score_fact_interpretation_case,
    score_fact_interpretation_error,
)
from llm_agent import extract_fact_candidates
from routing import get_matrix
from usage_tracker import SessionUsage


ROOT = Path(__file__).resolve().parent
DEFAULT_LOG_ROOT = ROOT / "logs" / "fact_interpretation_evals"
MAX_LIVE_CASES = 50
MAX_WORKERS = 4


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ground-truth eval нового MBK semantic extractor",
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_PATH,
        help="versioned JSON-корпус; по умолчанию основной набор",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="разрешить реальные OpenRouter-вызовы; без флага только валидация",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=MAX_LIVE_CASES,
        help=f"жёсткий предел 1..{MAX_LIVE_CASES}",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=2,
        help=f"параллелизм 1..{MAX_WORKERS}",
    )
    parser.add_argument(
        "--ids",
        nargs="*",
        default=[],
        help="необязательный список case id",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="директория отчёта; по умолчанию timestamp под logs/",
    )
    return parser


def _select_cases(
    all_cases: list[FactInterpretationCase],
    ids: list[str],
    max_cases: int,
) -> list[FactInterpretationCase]:
    if not 1 <= max_cases <= MAX_LIVE_CASES:
        raise ValueError(f"--max-cases должен быть от 1 до {MAX_LIVE_CASES}")
    if not ids:
        return all_cases[:max_cases]
    requested = set(ids)
    selected = [case for case in all_cases if case.id in requested]
    missing = sorted(requested - {case.id for case in selected})
    if missing:
        raise ValueError(f"неизвестные case id: {missing}")
    if len(selected) > max_cases:
        raise ValueError("число --ids превышает --max-cases")
    return selected


def _run_case(
    case: FactInterpretationCase,
    *,
    config: AppConfig,
) -> dict[str, Any]:
    matrix = get_matrix()
    usage = SessionUsage(session_id=case.id)
    payload = {
        "facts": case.initial_facts,
        "short_history": [
            message.model_dump(mode="json")
            for message in case.history
        ],
        "latest_user_message": case.utterance,
        "internal_summary": "",
    }
    started = time.perf_counter()
    try:
        extraction = extract_fact_candidates(
            payload,
            config,
            usage.collector(role="interpretation_shadow_eval"),
            matrix=matrix,
        )
        return score_fact_interpretation_case(
            case,
            extraction.candidates,
            matrix=matrix,
            latency_seconds=time.perf_counter() - started,
            usage=usage.summary(),
            candidate_issues=extraction.issues,
        )
    except Exception as exc:  # noqa: BLE001 — ошибка является результатом eval
        return score_fact_interpretation_error(
            case,
            exc,
            latency_seconds=time.perf_counter() - started,
            usage=usage.summary(),
        )


def _write_report(
    output_dir: Path,
    results: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    for result in results:
        path = output_dir / f"{result['id']}.json"
        path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    args = _parser().parse_args()
    if not 1 <= args.workers <= MAX_WORKERS:
        raise ValueError(f"--workers должен быть от 1 до {MAX_WORKERS}")
    cases = _select_cases(
        load_fact_interpretation_cases(args.cases),
        args.ids,
        args.max_cases,
    )
    if not args.live:
        print(
            json.dumps(
                {
                    "validated": True,
                    "selected_cases": len(cases),
                    "openrouter_calls": 0,
                    "hint": "добавьте --live для контролируемого прогона",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    config = replace(
        load_config(),
        # Live-eval наследует no-retry shadow transport, короткий output и timeout.
        fact_interpretation_mode="shadow",
    )
    indexed_results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(
        max_workers=args.workers,
        thread_name_prefix="mbk-fact-eval",
    ) as executor:
        future_to_case = {
            executor.submit(_run_case, case, config=config): case
            for case in cases
        }
        for future in as_completed(future_to_case):
            case = future_to_case[future]
            result = future.result()
            indexed_results[case.id] = result
            marker = "PASS" if result.get("passed") else "FAIL"
            print(
                f"[{marker}] {case.id} "
                f"{result.get('latency_seconds', 0)}s"
            )

    results = [indexed_results[case.id] for case in cases]
    summary = {
        "schema_version": "mbk.fact-interpretation-eval.v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "model": (
            config.fact_interpretation_model
            or config.extractor_model
            or config.model
        ),
        "crm_called": False,
        "dadata_called": False,
        "transport_max_attempts": 1,
        **aggregate_fact_interpretation_results(results),
    }
    output_dir = args.output_dir or (
        DEFAULT_LOG_ROOT
        / datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    _write_report(output_dir, results, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"report: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
