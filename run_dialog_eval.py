"""CLI ограниченного сквозного прогона локальных разговоров."""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from evaluation.dialogs import (
    LocalDialogClient,
    load_dialog_scenarios,
    load_local_credentials,
    run_dialog_scenario,
    write_dialog_report,
)


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cases",
        type=Path,
        default=ROOT / "evaluation" / "dialog_cases.json",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--ids",
        nargs="*",
        help="Необязательный список scenario id для точечного повторного прогона",
    )
    args = parser.parse_args()

    username, password = load_local_credentials(ROOT)
    client = LocalDialogClient(args.base_url)
    client.login(username, password)

    scenarios = load_dialog_scenarios(args.cases)
    if args.ids:
        requested = set(args.ids)
        scenarios = [scenario for scenario in scenarios if scenario.id in requested]
        missing = requested - {scenario.id for scenario in scenarios}
        if missing:
            parser.error("неизвестные scenario id: " + ", ".join(sorted(missing)))
    results = []
    for scenario in scenarios:
        print(f"Running {scenario.id} ({len(scenario.messages)} user turns)...")
        result = run_dialog_scenario(client, scenario)
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        print(
            f"  {status}: route={result.route_id}; "
            f"calls={result.usage.get('total_calls')}; "
            f"cost=${float(result.usage.get('total_cost_usd') or 0):.6f}; "
            f"humanity={result.mean_v4_humanity_score}; "
            f"coherence={result.dialog_coherence_score}"
        )
        # Причины связности печатаются всегда, а не только при FAIL: сценарий
        # может пройти порог и всё равно накопить замечания, ради которых эта
        # метрика и заводилась.
        for reason in result.dialog_coherence_reasons:
            print(f"    ~ {reason}")
        for failure in result.failures:
            print(f"    - {failure}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = args.out or ROOT / "logs" / "dialog_evals" / timestamp
    write_dialog_report(results, output)
    passed = sum(result.passed for result in results)
    print(f"Dialog eval: {passed}/{len(results)} passed")
    print(f"Artifacts: {output}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
