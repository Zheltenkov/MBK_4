"""Движок маршрутизации. Чистые функции, ни одного обращения к LLM.

Это ядро квалификации: по фактам о клиенте оно говорит, какие маршруты уже закрыты,
какие подошли и каких именно фактов не хватает. Разговорный слой сюда не заглядывает —
он только получает список полей, которые надо выяснить.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .schema import Condition, Matrix, Route


Verdict = Literal["pass", "fail", "unknown"]
RouteState = Literal["PASS", "FAIL", "UNKNOWN"]


def get_fact(facts: dict[str, Any], path: str) -> Any:
    """Достаёт факт по пути через точку. Отсутствие и явный None — одно и то же."""
    current: Any = facts
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def check_condition(cond: Condition, facts: dict[str, Any]) -> Verdict:
    value = get_fact(facts, cond.field)
    if value is None or value == "":
        # «Пустое поле» в таблице — легальное значение, а не пробел в данных.
        return "pass" if cond.allow_empty else "unknown"

    op, expected = cond.op, cond.value
    if op == "eq":
        return "pass" if value == expected else "fail"
    if op == "ne":
        return "pass" if value != expected else "fail"
    if op == "in":
        return "pass" if value in expected else "fail"

    if op == "between":
        low, high = expected
        return "pass" if _num(value) is not None and low <= _num(value) <= high else "fail"

    number = _num(value)
    if number is None:
        # Строка вместо числа — это не «неизвестно», это мусор в фактах.
        return "fail"
    if op == "gt":
        return "pass" if number > expected else "fail"
    if op == "gte":
        return "pass" if number >= expected else "fail"
    if op == "lt":
        return "pass" if number < expected else "fail"
    if op == "lte":
        return "pass" if number <= expected else "fail"
    raise ValueError(f"неизвестный оператор: {op}")


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


@dataclass
class RouteStatus:
    route: Route
    state: RouteState
    # Поля, без которых нельзя вынести вердикт, в порядке условий маршрута.
    missing: list[str] = field(default_factory=list)
    # Условия, которые клиент точно не проходит, — причина отказа для лога и отладки.
    failed: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.route.id

    @property
    def priority(self) -> str:
        return self.route.priority


def evaluate_route(route: Route, facts: dict[str, Any]) -> RouteStatus:
    """Вердикт по одному маршруту.

    FAIL важнее UNKNOWN: если клиент уже не проходит хотя бы по одному условию,
    добирать остальные факты ради этого маршрута бессмысленно.
    """
    missing: list[str] = []
    failed: list[str] = []
    for cond in route.conditions:
        verdict = check_condition(cond, facts)
        if verdict == "fail":
            failed.append(cond.field)
        elif verdict == "unknown":
            missing.append(cond.field)

    if failed:
        return RouteStatus(route=route, state="FAIL", missing=missing, failed=failed)
    if missing:
        return RouteStatus(route=route, state="UNKNOWN", missing=missing)
    return RouteStatus(route=route, state="PASS")


def evaluate(matrix: Matrix, facts: dict[str, Any]) -> list[RouteStatus]:
    """Все маршруты по возрастанию приоритета. Выключенные не участвуют."""
    return [
        evaluate_route(route, facts)
        for route in matrix.sorted_routes()
        if route.enabled
    ]


def first_match(statuses: list[RouteStatus]) -> RouteStatus | None:
    """Первый подошедший маршрут — он же самый приоритетный, список уже отсортирован."""
    return next((s for s in statuses if s.state == "PASS"), None)
