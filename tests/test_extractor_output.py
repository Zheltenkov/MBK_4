"""Извлекатель не пишет то, чего не было.

Замер 31.07.2026: время извлекателя пропорционально объёму выдачи, а не
промпта. Вход кэшируется на 70%, и его сокращение ничего не дало; отказ от
пустых осей срезал выдачу с 252 токенов до 170 и ход с 6.9 до 6.1 секунды.

Семь осей выдавались каждый ход, а несли смысл так: goal — 96% ходов,
payment_status — 12%, остальные 3-7%, alternatives — ни разу за 206 ходов.
"""
from __future__ import annotations

from interpretation.contracts import (
    FactCandidateExtraction,
    PreferenceStance,
    SemanticBoundarySignals,
)
from interpretation.prompt import build_interpretation_system_prompt


def test_prompt_asks_to_omit_axes_that_were_not_discussed(matrix):
    prompt = build_interpretation_system_prompt(matrix)

    assert "не включай" in prompt.lower()
    assert "not_mentioned" in prompt


def test_alternatives_axis_is_still_requested(matrix):
    """Ось выглядела мёртвой и была убрана — оказалось, ошибочно.

    По диалоговому корпусу она не заполнилась ни разу за 206 ходов, но там про
    альтернативы просто никто не заговаривает. В корпусе извлечения на неё
    четыре явных кейса, и без оси они провалились. Мера «сколько раз сработало
    в диалогах» не годится, чтобы объявлять сигнал ненужным.
    """
    prompt = build_interpretation_system_prompt(matrix)

    assert '"alternatives"' in prompt


def test_missing_axes_read_as_not_discussed():
    """Отсутствие ключа должно значить ровно то же, что not_mentioned:
    иначе экономия на выдаче меняла бы смысл ответа."""
    signals = SemanticBoundarySignals.model_validate({"goal": "cash"})

    assert signals.goal == "cash"
    assert signals.collateral == PreferenceStance.NOT_MENTIONED
    assert signals.realty_collateral == PreferenceStance.NOT_MENTIONED
    assert signals.alternatives == PreferenceStance.NOT_MENTIONED


def test_extraction_without_boundaries_block_is_valid():
    """Модель вправе не прислать boundaries вовсе, если не обсуждалось ничего."""
    extraction = FactCandidateExtraction.model_validate({"candidates": []})

    assert extraction.boundaries.goal == "not_mentioned"
