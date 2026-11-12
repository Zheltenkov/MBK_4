"""Эвал человечности против живого клиента: роль играет отдельная модель.

    .venv313/Scripts/python run_roleplay_eval.py --base-url https://...
    .venv313/Scripts/python run_roleplay_eval.py --ids anxious_overdue

Вшитые реплики проверяют только тот путь, который мы сами и придумали. Здесь
собеседник ведёт себя как человек: путается, отвечает не на тот вопрос,
пытается разоблачить бота — и человечность ломается именно на этом.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from evaluation.dialogs import LocalDialogClient, load_local_credentials
from evaluation.personas import PERSONAS, persona_by_id
from evaluation.roleplay import client_model, run_roleplay, write_report

ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--ids", nargs="*", help="только эти роли")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    username, password = load_local_credentials(ROOT)
    client = LocalDialogClient(args.base_url)
    client.login(username, password)

    personas = (
        [persona_by_id(persona_id) for persona_id in args.ids]
        if args.ids
        else PERSONAS
    )

    print(f"Клиента играет {client_model()}, ролей: {len(personas)}\n")
    results = []
    for persona in personas:
        print(f"Running {persona.id} ({persona.description})...")
        result = run_roleplay(client, persona)
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        print(
            f"  {status}: ходов={len(result.turns)}; "
            f"человечность={result.mean_humanity}; "
            f"связность={result.coherence_score}; "
            f"маршрут={result.route_id}; "
            f"CRM={result.crm_status}; "
            f"${float(result.usage.get('total_cost_usd') or 0):.4f}"
        )
        for problem in result.violations:
            print(f"    ! {problem}")
        for reason in result.coherence_reasons:
            print(f"    · {reason}")

    output = args.out or (
        ROOT / "logs" / "roleplay" /
        f"roleplay_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    write_report(results, output)
    print(f"\nОтчёт: {output}")

    failed = [result for result in results if not result.passed]
    print(f"Итог: {len(results) - len(failed)}/{len(results)} без замечаний")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
