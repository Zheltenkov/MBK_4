"""Мутационный эвал: та же история другими словами, живым диалогом.

    .venv313/Scripts/python run_mutation_eval.py --base-url https://...
    .venv313/Scripts/python run_mutation_eval.py --kind meaning_preserved
    .venv313/Scripts/python run_mutation_eval.py --origin terse_skeptic

Проверяется не человечность, а **точность анкеты**: собрал ли бот из живого
разговора ровно те факты, что заложены в роль, и привёл ли движок к тому
маршруту, который считает сам по этим фактам.

Мутации двух видов. Без смены смысла — меняется только манера речи (опечатки,
канцелярит, всё одной репликой, числа словами), маршрут обязан остаться
прежним. Со сменой смысла — подменён один факт, и ожидание считает движок,
а не я: тест проверяет извлечение, а не бизнес-правила матрицы.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from evaluation.dialogs import LocalDialogClient, load_local_credentials
from evaluation.mutations import build_mutations
from evaluation.personas import PERSONAS
from evaluation.roleplay import _violations as roleplay_violations
from evaluation.roleplay import client_model, compare_facts, run_roleplay

ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--kind", choices=["meaning_preserved", "meaning_changed"],
        help="только мутации этого вида",
    )
    parser.add_argument("--origin", nargs="*", help="только от этих ролей")
    parser.add_argument("--limit", type=int, help="первые N мутаций")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    mutations = build_mutations(PERSONAS)
    if args.kind:
        mutations = [m for m in mutations if m.kind == args.kind]
    if args.origin:
        mutations = [m for m in mutations if m.origin in set(args.origin)]
    if args.limit:
        mutations = mutations[: args.limit]

    username, password = load_local_credentials(ROOT)
    client = LocalDialogClient(args.base_url)
    client.login(username, password)

    print(f"Клиента играет {client_model()}, мутаций: {len(mutations)}\n")
    rows = []
    skipped: list[str] = []
    for mutation in mutations:
        print(f"Running {mutation.id} [{mutation.kind}]...")
        try:
            result = run_roleplay(client, mutation.persona)
        except Exception as exc:  # noqa: BLE001
            # Обрыв к модели-клиенту не должен обнулять весь прогон: 12.08.2026
            # один таймаут через прокси убил двадцать пять мутаций на двенадцатой.
            print(f"  SKIP: прогон не состоялся — {type(exc).__name__}: {exc}")
            skipped.append(mutation.id)
            continue
        collected, total, mismatches = compare_facts(
            mutation.expected_facts, result.facts
        )
        route_ok = result.route_id == mutation.expected_route
        # Та же планка, что у ролевого прогона: запреты роли, человечность и
        # связность не ниже 90. Раньше мутация проверяла только факты, и
        # просевший тон в ней не был виден вовсе.
        violations = roleplay_violations(result, mutation.persona)
        passed = route_ok and not mismatches and not violations

        status = "PASS" if passed else "FAIL"
        print(
            f"  {status}: факты {collected}/{total}; "
            f"маршрут {result.route_id} (ждали {mutation.expected_route}); "
            f"человечность={result.mean_humanity}; "
            f"ходов={len(result.turns)}; "
            f"${float(result.usage.get('total_cost_usd') or 0):.4f}"
        )
        for problem in [*mismatches, *violations]:
            print(f"    ! {problem}")

        rows.append(
            {
                "id": mutation.id,
                "kind": mutation.kind,
                "origin": mutation.origin,
                "passed": passed,
                "route_expected": mutation.expected_route,
                "route_actual": result.route_id,
                "facts_collected": collected,
                "facts_expected": total,
                "mismatches": mismatches,
                "violations": violations,
                "mean_humanity": result.mean_humanity,
                "coherence_score": result.coherence_score,
                "turns": len(result.turns),
                "session_id": result.session_id,
                "dialog": [
                    {"user": turn.user, "bot": turn.bubbles} for turn in result.turns
                ],
            }
        )

    output = args.out or (
        ROOT / "logs" / "mutations" /
        f"mutations_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"mutations": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if skipped:
        print("")
        print(f"Не состоялись (сеть): {len(skipped)} — " + ", ".join(skipped))
    failed = [row for row in rows if not row["passed"]]
    facts_ok = sum(row["facts_collected"] for row in rows)
    facts_all = sum(row["facts_expected"] for row in rows)
    print(f"\nОтчёт: {output}")
    print(f"Факты: {facts_ok}/{facts_all}")
    print(f"Итог:  {len(rows) - len(failed)}/{len(rows)} мутаций без замечаний")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
