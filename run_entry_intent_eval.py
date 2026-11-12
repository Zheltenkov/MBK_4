"""Живая проверка развилки на входе по корпусу первых реплик.

Жёсткое требование спеки одно: ни одна реплика с ожидаемым «money» не уходит
в ветку «уже оставлял заявку» или «не наш профиль». Остальные ошибки
допустимы — их лечит возврат в анкету, — но печатаются для разбора.

Реплика подаётся так же, как в бою на первом ходе: история из одного
приветствия, флаг развилки включён, семантический извлекатель. Сбой модели
на реплике означает работу словаря — ровно как в `core.prepare_turn`.

Без --live только проверяет корпус и ничего не вызывает.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import core
from config import load_config
from entry_routing import reconcile_intent
from evaluation.entry_intent import (
    DEFAULT_CORPUS_PATH,
    EntryCase,
    load_entry_corpus,
    score_entry_predictions,
)
from llm_agent import extract_fact_candidates
from prompts import OPENING_MESSAGE
from routing import get_matrix
from routing.entry_intent import EntryIntent, classify_by_dictionary
from state import init_state


ROOT = Path(__file__).resolve().parent
DEFAULT_LOG_ROOT = ROOT / "logs" / "entry_intent_evals"
MAX_WORKERS = 8


def _first_message_payload(message: str) -> dict[str, Any]:
    state = init_state()
    state["chat_history"] = [{"role": "assistant", "content": OPENING_MESSAGE}]
    return core.build_runtime_payload(state, message)


def _classify(case: EntryCase, config, matrix) -> dict[str, Any]:
    try:
        extraction = extract_fact_candidates(
            _first_message_payload(case.message), config, matrix=matrix
        )
        # Та же сверка, что в бою (core.prepare_turn): прогон мерит боевой путь.
        model_intent = reconcile_intent(
            extraction.entry_intent, money_signal=extraction.mentions_money()
        )
        error = None
    except Exception as exc:  # noqa: BLE001 — сбой модели = работа словаря
        model_intent, error = None, f"{type(exc).__name__}: {exc}"[:300]
    effective = model_intent or classify_by_dictionary(case.message)
    return {
        "id": case.id,
        "message": case.message,
        "expected": case.expected.value,
        "model": model_intent.value if model_intent else None,
        "effective": effective.value,
        "source": "model" if model_intent else "dictionary",
        "error": error,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Живая проверка развилки на входе")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument(
        "--live",
        action="store_true",
        help="разрешить реальные вызовы модели; без флага только проверка корпуса",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    cases = load_entry_corpus(args.corpus)
    print(f"корпус: {len(cases)} реплик, {sum(c.count for c in cases)} диалогов")
    if not args.live:
        print("без --live модель не вызывается")
        return 0

    config = replace(
        load_config(),
        fact_interpretation_mode="semantic",
        entry_routing_enabled=True,
    )
    matrix = get_matrix()
    workers = max(1, min(args.workers, MAX_WORKERS))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(lambda case: _classify(case, config, matrix), cases))

    predictions = {row["id"]: EntryIntent(row["effective"]) for row in rows}
    summary = score_entry_predictions(cases, predictions)
    by_id = {row["id"]: row for row in rows}

    print(f"точность: {summary.accuracy:.1%} ({summary.correct}/{summary.total})")
    print(f"ответов модели без намерения: {sum(r['source'] == 'dictionary' for r in rows)}")
    print("ожидалось → получено:")
    for expected, row in summary.confusion.items():
        print(f"  {expected:>9}: {row}")
    print(f"money увели из анкеты: {len(summary.lost_money)} реплик, "
          f"{summary.lost_money_dialogs} диалогов")
    for case_id in summary.lost_money:
        print(f"  ! {case_id} → {by_id[case_id]['effective']}: {by_id[case_id]['message']}")
    print(f"приветствие увели из анкеты: {len(summary.diverted_unclear)}")
    for case_id in summary.diverted_unclear:
        print(f"  ? {case_id} → {by_id[case_id]['effective']}: {by_id[case_id]['message']}")

    out = args.out or DEFAULT_LOG_ROOT / f"entry_intent_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "summary": {
                    "total": summary.total,
                    "correct": summary.correct,
                    "accuracy": summary.accuracy,
                    "confusion": summary.confusion,
                    "lost_money": list(summary.lost_money),
                    "lost_money_dialogs": summary.lost_money_dialogs,
                    "diverted_unclear": list(summary.diverted_unclear),
                },
                "cases": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"отчёт: {out}")
    return 1 if summary.lost_money else 0


if __name__ == "__main__":
    sys.exit(main())
