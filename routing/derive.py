"""Факты, которые анкета не спрашивает, но выводит из её ответов.

Это тот же приём, что уже используется для класса региона: `client.region_class`
никто не спрашивает, его вычисляет geo-слой из ответа DaData. Здесь так же —
`debts.credit_history` требуют маршруты БФЛ, но в анкете заказчика такого вопроса
нет: он спрашивает «есть действующие кредиты?» и «как долго тянется просрочка».
Подтверждённое частное владение клиента также закрывает пропущенный общий
признак недвижимости. Неуверенные сведения и явное отрицание сохраняются.

Правило одно: только заполнять пустое, никогда не перезаписывать сказанное
клиентом. Иначе «готов к банкротству» тихо превратится в «есть просрочки», и
маршрутизация изменится под видом технической правки.
"""
from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping
from typing import Any

from utils import set_by_path

from .engine import get_fact


# Сроки просрочки, означающие, что просрочка действительно есть.
_OVERDUE_VALUES = {"up_to_1m", "from_1m_to_3m", "from_3m_to_6m", "over_6m"}

def derive_facts(facts: dict[str, Any], *, fact_statuses: Mapping[str, object] | None = None) -> dict[str, Any]:
    """Возвращает копию фактов с заполненными производными полями."""
    updated = deepcopy(facts)
    # Confirmed private ownership by the client proves presence even when the
    # extractor omitted its parent boolean. Unknown assets and explicit No stay intact.
    realty_paths = ("assets.has_realty", "assets.realty.ownership_form", "assets.realty.owners")
    if (get_fact(updated, "assets.has_realty") in (None, "")
            and get_fact(updated, "assets.realty.ownership_form") in (None, "", "private")
            and get_fact(updated, "assets.realty.owners") in ("client_only", "client_and_spouse")
            and all(_settled(fact_statuses, path) for path in realty_paths)):
        _fill(updated, "assets.has_realty", True)
    has_credits = get_fact(updated, "debts.has_active_credits")
    if has_credits is None:
        return updated

    if has_credits is False:
        # Кредитов нет — значит нет и просрочки, и остатка. Это делает маршруты
        # БФЛ честно FAIL вместо вечного UNKNOWN.
        _fill(updated, "debts.credit_history", "no_overdue")
        _fill(updated, "debts.total", 0)
        return updated

    overdue = get_fact(updated, "debts.overdue_duration")
    if overdue == "no_overdue":
        _fill(updated, "debts.credit_history", "no_overdue")
    elif overdue in _OVERDUE_VALUES:
        _fill(updated, "debts.credit_history", "has_overdue")
    return updated


def _settled(statuses: Mapping[str, object] | None, path: str) -> bool:
    if statuses is None:
        return True
    if not isinstance(statuses, Mapping):
        return False
    if path not in statuses:
        return True
    value = statuses[path]
    if isinstance(value, Mapping):
        value = value.get("status")
    return isinstance(value, str) and value in ("known", "confirmed")


def _fill(facts: dict[str, Any], path: str, value: Any) -> None:
    """Заполняет только пустое. Ответ клиента всегда важнее вывода."""
    if get_fact(facts, path) in (None, ""):
        set_by_path(facts, path, value)
