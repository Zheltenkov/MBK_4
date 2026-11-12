"""Корпус первых реплик для развилки на входе.

Живой поток из боевой базы, а не выдуманный набор: первые реплики клиентов с
20.08.2026, дубли схлопнуты (поле `count` — сколько диалогов начались этой
репликой), ожидаемый исход проставлен вручную. Персональные данные вычищены
при выгрузке: телефоны и номера — маркерами, имена — «[имя]».

Спека: docs/superpowers/specs/2026-09-09-dialog-entry-and-order-design.md.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from routing.entry_intent import EntryIntent


DEFAULT_CORPUS_PATH = Path(__file__).with_name("entry_intent_corpus.json")
_ALLOWED_KEYS = frozenset({"id", "message", "expected", "count", "note"})


@dataclass(frozen=True)
class EntryCase:
    """Одна первая реплика и ожидаемый исход."""

    id: str
    message: str
    expected: EntryIntent
    count: int = 1


def load_entry_corpus(path: Path = DEFAULT_CORPUS_PATH) -> list[EntryCase]:
    """Читает корпус и падает на любом нарушении формата, а не молча."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("корпус должен быть JSON-массивом")

    cases: list[EntryCase] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"[{index}] элемент корпуса — не объект")
        extra = set(item) - _ALLOWED_KEYS
        if extra:
            raise ValueError(f"[{index}] лишние ключи: {sorted(extra)}")
        case_id = item.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"[{index}] нет id")
        if case_id in seen:
            raise ValueError(f"повтор id {case_id}")
        message = item.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ValueError(f"{case_id}: пустая реплика")
        try:
            expected = EntryIntent(item.get("expected"))
        except ValueError as exc:
            raise ValueError(f"{case_id}: {exc}") from exc
        count = item.get("count", 1)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError(f"{case_id}: count должен быть целым от 1")
        seen.add(case_id)
        cases.append(EntryCase(case_id, message, expected, count))
    return cases


# Ветки, в которых анкета не начинается. Потеря денег — это money, ушедший
# сюда; money → unclear потерей не считается: оба ведут в анкету.
LEAVES_QUESTIONNAIRE = frozenset({EntryIntent.EXISTING, EntryIntent.OFF_TOPIC})


@dataclass(frozen=True)
class EntryEvalSummary:
    """Итог прогона корпуса."""

    total: int
    correct: int
    # ожидаемое → предсказанное → число реплик
    confusion: dict[str, dict[str, int]]
    # Жёсткое требование спеки: должно быть пусто.
    lost_money: tuple[str, ...]
    # Сколько живых диалогов стоит за потерянными репликами (поле count).
    lost_money_dialogs: int
    # «Здравствуйте», уведённое в ветку: требованием не считается, но
    # выглядит плохо — печатается для разбора.
    diverted_unclear: tuple[str, ...]

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0


def score_entry_predictions(
    cases: list[EntryCase],
    predictions: dict[str, EntryIntent],
) -> EntryEvalSummary:
    """Сравнивает предсказания с разметкой. Нет предсказания — `UNCLEAR`."""
    confusion = {
        expected.value: {predicted.value: 0 for predicted in EntryIntent}
        for expected in EntryIntent
    }
    correct = 0
    lost: list[str] = []
    lost_dialogs = 0
    diverted: list[str] = []
    for case in cases:
        predicted = predictions.get(case.id, EntryIntent.UNCLEAR)
        confusion[case.expected.value][predicted.value] += 1
        if predicted is case.expected:
            correct += 1
        if predicted in LEAVES_QUESTIONNAIRE:
            if case.expected is EntryIntent.MONEY:
                lost.append(case.id)
                lost_dialogs += case.count
            elif case.expected is EntryIntent.UNCLEAR:
                diverted.append(case.id)
    return EntryEvalSummary(
        total=len(cases),
        correct=correct,
        confusion=confusion,
        lost_money=tuple(lost),
        lost_money_dialogs=lost_dialogs,
        diverted_unclear=tuple(diverted),
    )
