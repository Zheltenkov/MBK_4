"""Вопрос о занятости: три варианта вместо пяти.

Заказчик 31.07.2026: «1 = найм, 2 = бизнес, 3 = не работает… если не работаю —
доход не спрашиваем, если первые два — спрашиваем официальный доход».

До этого бот спрашивал «свой бизнес, официальное трудоустройство или найм», и
клиент на скриншоте отвечал вопросом: «а чем отличается официальное
трудоустройство от найма?». Различия и нет — человек по трудовому договору и
есть наёмный работник.
"""
from __future__ import annotations

import pytest

from routing import plan
from routing.normalize import normalize_fact
from utils import set_by_path


def test_question_offers_exactly_three_options(matrix):
    ask = matrix.fields["employment.type"].ask.lower()

    assert "найм" in ask
    assert "бизнес" in ask
    assert "не работа" in ask
    # Из-за него и возник вопрос клиента: это то же самое, что найм.
    assert "официальное трудоустройство" not in ask


@pytest.mark.parametrize(
    "reply, expected",
    [
        ("найм", "official"),
        ("работаю по найму", "official"),
        ("работаю официально по ТК", "official"),
        # Модель видит в перечислении оба значения и на «работаю по найму»
        # пишет то одно, то другое. Маршрут от этого не меняется — условие
        # принимает оба, — а в CRM это разные коды: 376 и 3082. Заказчик
        # 12.08.2026 выбрал official, поэтому синоним сворачивается здесь.
        ("hired", "official"),
        ("свой бизнес", "business"),
        ("ИП", "business"),
        ("сейчас не работаю", "unemployed"),
        ("безработный", "unemployed"),
    ],
)
def test_three_offered_answers_land_where_expected(reply, expected):
    assert normalize_fact("employment.type", reply) == expected


@pytest.mark.parametrize(
    "reply, expected",
    [
        # Вариантов не предлагаем, но люди отвечают своими словами. Услышать
        # это важнее, чем причесать: «неофициально» отсекает Потреб, и подменять
        # его наймом значило бы молча изменить, кому доступен продукт.
        ("работаю неофициально", "unofficial"),
        ("зарплата в конверте, серая", "unofficial"),
        ("я самозанятый", "self_employed"),
        ("я на пенсии", "retired"),
    ],
)
def test_volunteered_answers_are_still_recognised(reply, expected):
    assert normalize_fact("employment.type", reply) == expected


def test_the_semantic_extractor_cannot_smuggle_the_other_code_in():
    """Извлекатель отдаёт готовое значение матрицы мимо разбора свободного текста.

    Проверка идёт через reducer: именно он в режиме semantic решает, что
    записать в факты, и до 12.08.2026 пропускал `hired` как есть.
    """
    from interpretation.contracts import FactCandidate
    from interpretation.reducer import reduce_fact_candidates

    candidate = FactCandidate(
        path="employment.type",
        value="hired",
        evidence="работаю по найму",
    )

    reduction = reduce_fact_candidates(
        {},
        {},
        [candidate],
        evidence_text="Работаю по найму, продавцом.",
    )

    assert reduction.facts["employment"]["type"] == "official"


BASE = {
    "client": {"name": "Иван", "age": 42, "region_class": "moscow_mo"},
    "request": {"amount": 600_000},
    "debts": {"has_active_credits": False},
}


def _next_after_employment(matrix, employment_type: str) -> list[str]:
    facts: dict = {}
    for path, value in _flat(BASE).items():
        set_by_path(facts, path, value)
    set_by_path(facts, "employment.type", employment_type)
    return plan(matrix, facts).field_paths


@pytest.mark.parametrize("employment_type", ["official", "business"])
def test_income_is_asked_of_the_first_two(matrix, employment_type):
    assert _next_after_employment(matrix, employment_type) == ["employment.income"]


@pytest.mark.parametrize("employment_type", ["unemployed", "retired"])
def test_income_is_not_asked_of_those_without_work(matrix, employment_type):
    assert "employment.income" not in _next_after_employment(matrix, employment_type)


def _flat(nested: dict, prefix: str = "") -> dict:
    flat: dict = {}
    for key, value in nested.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flat(value, path))
        else:
            flat[path] = value
    return flat
