"""Тесты движка: по одному позитивному и негативному кейсу на маршрут.

Позитивные кейсы проверяют маршрут изолированно (evaluate_route), потому что при
сквозном прогоне более приоритетный маршрут перехватил бы клиента — это правильно
для продакшена, но бесполезно для проверки самих условий.
"""
from __future__ import annotations

import pytest

from routing import evaluate, evaluate_route, first_match, load_matrix
from routing.engine import check_condition
from routing.loader import MATRIX_PATH
from routing.schema import Condition


def route_by_id(matrix, route_id: str):
    return next(r for r in matrix.routes if r.id == route_id)


REALTY_BASE = {
    "request": {"amount": 500_000},
    "client": {"age": 40, "region_class": "moscow_mo"},
    "assets": {
        "has_realty": True,
        "realty": {
            "region_class": "moscow_mo",
            "kind": "flat",
            "ownership_form": "private",
            "owners": "client_only",
        },
    },
}

REGIONAL_REALTY_BASE = {
    "request": {"amount": 500_000},
    "client": {"age": 40, "region_class": "rf_other"},
    "assets": {
        "has_realty": True,
        "realty": {
            # Лист «МБК» — именно он ведёт в маршрут 1-2.
            "region_class": "mbk_list",
            "kind": "flat",
            "ownership_form": "private",
            "owners": "client_only",
        },
    },
}

AUTO_BASE = {
    "request": {"amount": 400_000},
    "client": {"age": 40, "region_class": "moscow_mo"},
    "assets": {"has_vehicle": True, "vehicle": {"year": 2015, "pledged": False}},
    "debts": {"overdue_duration": "no_overdue"},
}

BFL_BASE = {
    "client": {"region_class": "moscow_mo"},
    "debts": {"total": 500_000, "credit_history": "has_overdue"},
}

CONSUMER_BASE = {
    "request": {"amount": 600_000},
    "client": {"age": 35, "region_class": "moscow_mo"},
    "debts": {"credit_history": "no_overdue", "overdue_duration": "no_overdue"},
    "employment": {"type": "official"},
}


# --------------------------------------------------------------------------- #
# Загрузка матрицы
# --------------------------------------------------------------------------- #
def test_matrix_loads_all_routes(matrix):
    # 12 строк, а не 11: приоритет 3-1 занимают два маршрута — АПИ Мск/МО и АПИ РФ.
    assert len(matrix.routes) == 12
    assert [r.priority for r in matrix.sorted_routes()][:3] == ["1-1", "1-2", "1-3"]


def test_both_region_lists_enable_their_own_regional_routes(matrix):
    """У 1-2 и 1-3 разные справочники — это два разных листа исходного файла.

    Лист «МБК» — 43 города без региона, лист «Кредит.Клаб» — пары «город+регион».
    Пока оба маршрута висели на списке Кредит.Клаб, условия 1-3 были строгим
    подмножеством 1-2, и маршрут 1-3 не мог стать результатом ни для кого.
    """
    # В исходнике 326 строк; две пары дублировались только регистром региона.
    assert len(matrix.regions["realty_list"]) == 324
    assert len(matrix.regions["mbk_list"]) == 43
    assert route_by_id(matrix, "realty_rf").requires_region_list == "mbk_list"
    assert route_by_id(matrix, "credit_club").requires_region_list == "realty_list"
    assert route_by_id(matrix, "realty_rf").enabled is True
    assert route_by_id(matrix, "credit_club").enabled is True
    assert [s.id for s in evaluate(matrix, {})].count("realty_rf") == 1


def test_missing_location_source_fails_fast(tmp_path):
    """Матрица не должна тихо стартовать без приложенного бизнес-справочника."""
    original = MATRIX_PATH.read_text(encoding="utf-8")
    patched_path = tmp_path / "matrix.yaml"
    patched_path.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="не найден"):
        load_matrix(patched_path)


# --------------------------------------------------------------------------- #
# Условия
# --------------------------------------------------------------------------- #
def test_missing_fact_is_unknown_not_fail():
    cond = Condition(field="request.amount", op="gte", value=150_000)
    assert check_condition(cond, {}) == "unknown"


def test_allow_empty_treats_missing_fact_as_pass():
    """«Пустое поле» в таблице — легальное значение, а не пробел в данных."""
    cond = Condition(field="debts.credit_history", op="eq", value="no_overdue", allow_empty=True)
    assert check_condition(cond, {}) == "pass"
    assert check_condition(cond, {"debts": {"credit_history": "has_overdue"}}) == "fail"


def test_between_is_inclusive():
    cond = Condition(field="client.age", op="between", value=[21, 65])
    assert check_condition(cond, {"client": {"age": 21}}) == "pass"
    assert check_condition(cond, {"client": {"age": 65}}) == "pass"
    assert check_condition(cond, {"client": {"age": 66}}) == "fail"


def test_bool_is_not_treated_as_number():
    """True не должен пролезать в числовое сравнение как 1."""
    cond = Condition(field="assets.vehicle.year", op="gte", value=2008)
    assert check_condition(cond, {"assets": {"vehicle": {"year": True}}}) == "fail"


# --------------------------------------------------------------------------- #
# Маршруты: позитив
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "route_id, facts",
    [
        ("realty_msk", REALTY_BASE),
        # Региональные маршруты включены справочником, но условия по-прежнему
        # тестируем изолированно, чтобы более высокий приоритет не маскировал ошибку.
        ("realty_rf", REGIONAL_REALTY_BASE),
        (
            "credit_club",
            {
                **REGIONAL_REALTY_BASE,
                # 1-3 работает по своему списку — лист Кредит.Клаб, а не «МБК».
                "assets": {
                    **REGIONAL_REALTY_BASE["assets"],
                    "realty": {
                        **REGIONAL_REALTY_BASE["assets"]["realty"],
                        "region_class": "realty_list",
                    },
                },
                "debts": {"overdue_duration": "from_1m_to_3m"},
            },
        ),
        ("bfl_msk", BFL_BASE),
        ("bfl_rf", {**BFL_BASE, "client": {"region_class": "rf_other"}}),
        ("bfl_cpa", {"client": {"region_class": "rf_other"},
                     "debts": {"total": 250_000, "credit_history": "has_overdue"}}),
        ("auto_api_msk", AUTO_BASE),
        ("auto_api_rf", {**AUTO_BASE, "client": {"age": 40, "region_class": "rf_other"}}),
        ("auto_msk", {**AUTO_BASE, "request": {"amount": 800_000}}),
        ("auto_rf", {**AUTO_BASE, "request": {"amount": 800_000},
                     "client": {"age": 40, "region_class": "rf_other"}}),
        ("consumer", CONSUMER_BASE),
        ("consumer_second", {**CONSUMER_BASE, "request": {"amount": 200_000},
                             "employment": {"type": "other"}}),
    ],
)
def test_route_passes_on_matching_profile(matrix, route_id, facts):
    status = evaluate_route(route_by_id(matrix, route_id), facts)
    assert status.state == "PASS", f"{route_id}: не хватает {status.missing}, провалено {status.failed}"


# --------------------------------------------------------------------------- #
# Маршруты: негатив
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "route_id, facts, broken_field",
    [
        ("realty_msk", {**REALTY_BASE, "request": {"amount": 100_000}}, "request.amount"),
        ("realty_msk", {**REALTY_BASE, "client": {"age": 19, "region_class": "moscow_mo"}}, "client.age"),
        (
            "realty_rf",
            {
                **REGIONAL_REALTY_BASE,
                "assets": {
                    "has_realty": True,
                    "realty": {
                        "region_class": "realty_list",
                        "kind": "house",
                        "ownership_form": "private",
                        "owners": "client_only",
                    },
                },
            },
            "assets.realty.kind",
        ),
        (
            "credit_club",
            {
                **REGIONAL_REALTY_BASE,
                "debts": {
                    # Кредитная история сама по себе не должна закрыть маршрут:
                    # исходная колонка задаёт именно продолжительность просрочки.
                    "credit_history": "no_overdue",
                    "overdue_duration": "from_3m_to_6m",
                },
            },
            "debts.overdue_duration",
        ),
        ("bfl_msk", {**BFL_BASE, "debts": {"total": 100_000, "credit_history": "has_overdue"}}, "debts.total"),
        ("bfl_cpa", {**BFL_BASE, "debts": {"total": 500_000, "credit_history": "has_overdue"}}, "debts.total"),
        ("auto_msk", {**AUTO_BASE, "request": {"amount": 800_000},
                      "assets": {"has_vehicle": True, "vehicle": {"year": 2005, "pledged": False}}},
         "assets.vehicle.year"),
        ("auto_msk", {**AUTO_BASE, "request": {"amount": 800_000},
                      "assets": {"has_vehicle": True, "vehicle": {"year": 2015, "pledged": True}}},
         "assets.vehicle.pledged"),
        ("consumer", {**CONSUMER_BASE, "employment": {"type": "other"}}, "employment.type"),
    ],
)
def test_route_fails_on_broken_condition(matrix, route_id, facts, broken_field):
    status = evaluate_route(route_by_id(matrix, route_id), facts)
    assert status.state == "FAIL"
    assert broken_field in status.failed


def test_fail_wins_over_unknown(matrix):
    """Если клиент уже точно не проходит, добирать остальные факты ради этого
    маршрута бессмысленно — статус FAIL, а не UNKNOWN."""
    status = evaluate_route(route_by_id(matrix, "realty_msk"), {"request": {"amount": 50_000}})
    assert status.state == "FAIL"
    assert status.missing, "недостающие поля всё равно фиксируем для отладки"


# --------------------------------------------------------------------------- #
# Приоритеты
# --------------------------------------------------------------------------- #
def test_higher_priority_wins_when_both_match(matrix):
    """Клиент с квартирой в Москве и чистым авто подходит и под 1-1, и под 3-x.
    Выбран должен быть 1-1."""
    facts = {
        "request": {"amount": 400_000},
        "client": {"age": 40, "region_class": "moscow_mo"},
        "assets": {
            "has_realty": True,
            "realty": {"region_class": "moscow_mo", "kind": "flat",
                       "ownership_form": "private", "owners": "client_only"},
            "has_vehicle": True,
            "vehicle": {"year": 2015, "pledged": False},
        },
        "debts": {"overdue_duration": "no_overdue"},
    }
    statuses = evaluate(matrix, facts)
    matched = first_match(statuses)
    assert matched is not None and matched.id == "realty_msk"


def test_bfl_ignores_amount_and_age(matrix):
    """Наблюдение из разбора матрицы: БФЛ не смотрит ни на сумму, ни на возраст.
    Клиент с маленьким запросом всё равно доходит до БФЛ по размеру долга."""
    facts = {
        "request": {"amount": 50_000},
        "client": {"age": 19, "region_class": "moscow_mo"},
        "debts": {"total": 400_000, "credit_history": "has_overdue"},
    }
    matched = first_match(evaluate(matrix, facts))
    assert matched is not None and matched.id == "bfl_msk"
