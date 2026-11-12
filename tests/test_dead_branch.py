"""Не добивать вопросы, ответ на которые уже ничего не решает.

Заказчик 30.07.2026: «можно временно, пока по недвиге: если не подходит под наши
критерии — апарты, не апарты, квартиры, то, что в списке написано, — то можно не
спрашивать дополнительные вопросы».

Матрица принимает под залог недвижимости `flat`, `apartments`, `commercial`
(маршруты 1-1 и 1-2) и только `flat` у 1-3. Значит «дом» и «другое» закрывают
все залоговые маршруты по недвижимости разом, а бот после такого ответа всё
равно спрашивал регион, форму собственности и собственников — три хода впустую.
"""
from __future__ import annotations

import pytest

from routing import plan
from utils import set_by_path


BASE = {
    "client": {"name": "Иван", "age": 42, "region_class": "moscow_mo"},
    "request": {"amount": 3_000_000},
    "debts": {"has_active_credits": False},
    "employment": {"type": "official", "income": 150_000},
}

REALTY_DETAILS = (
    "assets.realty.region_class",
    "assets.realty.ownership_form",
    "assets.realty.owners",
)


ANSWERS = {
    "assets.realty.region_class": "moscow_mo",
    "assets.realty.ownership_form": "private",
    "assets.realty.owners": "client_only",
    "assets.has_vehicle": False,
    "assets.vehicle.year": 2018,
    "assets.vehicle.make": "Kia",
    "assets.vehicle.model": "Rio",
    "assets.vehicle.pledged": False,
}


def _walk(matrix, known: dict, answers: dict | None = None) -> list[str]:
    """Начальные факты и ответы на будущие вопросы — разные вещи.

    Если свалить их в один словарь, анкета завершится, не успев ничего
    спросить, и тест начнёт проверять пустоту.
    """
    replies = {**ANSWERS, **(answers or {})}
    facts: dict = {}
    for path, value in known.items():
        set_by_path(facts, path, value)
    asked: list[str] = []
    for _ in range(40):
        result = plan(matrix, facts)
        if result.action != "ask":
            return asked
        for path in result.field_paths:
            asked.append(path)
            set_by_path(facts, path, replies[path])
    raise AssertionError(f"анкета не завершилась: {asked}")


@pytest.mark.parametrize("kind", ["house", "other"])
def test_unsuitable_realty_kind_stops_the_realty_questions(matrix, kind):
    facts = {**BASE, "assets": {"has_realty": True, "realty": {"kind": kind}}}
    asked = _walk(matrix, _flatten(facts))

    for path in REALTY_DETAILS:
        assert path not in asked, f"{kind}: спрошено {path}, хотя маршрут уже закрыт"
    assert "assets.has_vehicle" in asked


@pytest.mark.parametrize("kind", ["flat", "apartments", "commercial"])
def test_suitable_realty_kind_keeps_asking(matrix, kind):
    """Обратная сторона: подходящий тип обязан пройти блок целиком."""
    facts = {**BASE, "assets": {"has_realty": True, "realty": {"kind": kind}}}
    asked = _walk(matrix, _flatten(facts))

    for path in REALTY_DETAILS:
        assert path in asked, f"{kind}: пропущено {path}"


def test_fields_no_route_depends_on_are_never_skipped(matrix):
    """Марка и модель не участвуют ни в одном условии — это данные для CRM.

    Правило «пропускай поле, если все маршруты, которым оно нужно, уже отпали»
    к ним неприменимо: маршрутов, которым оно нужно, нет вовсе.
    """
    facts = {
        **BASE,
        "assets": {
            "has_realty": True,
            "realty": {"kind": "house"},
            "has_vehicle": True,
        },
    }
    asked = _walk(matrix, _flatten(facts))

    assert "assets.vehicle.make" in asked
    assert "assets.vehicle.model" in asked


def _flatten(nested: dict, prefix: str = "") -> dict:
    flat: dict = {}
    for key, value in nested.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten(value, path))
        else:
            flat[path] = value
    return flat


def test_only_fields_marked_in_the_matrix_can_be_skipped(matrix):
    """Правило узкое намеренно, и его область объявлена данными, а не кодом.

    Общая версия («любое поле, чьи маршруты отпали») срезала `employment.type`
    в 8 сценариях корпуса из 15 — опорное поле анкеты и родитель вопроса о
    доходе. Потерять его значит отдать партнёру лид без сведений о занятости.
    Заказчик просил узко и сам сказал «временно, пока по недвиге».
    """
    skippable = {
        path for path, spec in matrix.fields.items() if spec.skip_when_settled
    }

    assert skippable == {
        "assets.realty.region_class",
        "assets.realty.ownership_form",
        "assets.realty.owners",
    }


def test_employment_survives_even_when_every_route_needing_it_failed(matrix):
    facts = {
        "client": {"name": "Иван", "age": 42, "region_class": "rf_other"},
        "request": {"amount": 3_000_000},
        "debts": {"has_active_credits": False},
        "assets": {"has_realty": False},
    }
    asked = _walk(matrix, _flatten(facts), {"employment.type": "official",
                                            "employment.income": 150_000})

    assert "employment.type" in asked
