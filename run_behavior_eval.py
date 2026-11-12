"""CLI для live behavioral regression текущей conversation model."""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from config import load_config
from evaluation.behavior import load_cases, run_live_cases, write_report


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cases",
        type=Path,
        default=ROOT / "evaluation" / "behavior_cases.json",
    )
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--model",
        help="OpenRouter model id для conversation layer; по умолчанию берётся из .env",
    )
    parser.add_argument(
        "--ids",
        nargs="*",
        help="Необязательный список case id для точечного повторного прогона",
    )
    args = parser.parse_args()

    # Каждый запуск сохраняет отдельный артефакт, чтобы prompt/model версии можно
    # было сравнивать, а не перезаписывать последним результатом.
    cases = load_cases(args.cases)
    if args.ids:
        requested = set(args.ids)
        cases = [case for case in cases if case.id in requested]
        missing = requested - {case.id for case in cases}
        if missing:
            parser.error("неизвестные case id: " + ", ".join(sorted(missing)))
    config = load_config()
    if args.model:
        config = replace(config, model=args.model)
    results = run_live_cases(cases, config)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = args.out or ROOT / "logs" / f"behavior_eval_{timestamp}.json"
    write_report(results, output, model=config.model)

    passed = sum(result.passed for result in results)
    print(f"Behavior eval: {passed}/{len(results)} passed")
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(
            f"  {status} {result.case_id}: {result.latency_sec:.2f}s; "
            f"Mikhail={result.mikhail_legacy_score}; "
            f"v4-humanity={result.v4_humanity_score}"
        )
        for failure in result.failures:
            print(f"    - {failure}")
    print(f"Report: {output}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
