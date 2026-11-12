"""Детерминированное ядро квалификации МБК.

Матрица маршрутов (`matrix.yaml`) → движок (`engine`) → планировщик вопросов (`planner`).
Ни один модуль этого пакета не обращается к LLM: решение о маршруте воспроизводимо
и полностью покрывается тестами.
"""
from .engine import RouteStatus, evaluate, evaluate_route, first_match, get_fact
from .loader import get_matrix, load_matrix
from .planner import Plan, PlannedField, plan
from .schema import Condition, FieldSpec, Matrix, Route

__all__ = [
    "Condition",
    "FieldSpec",
    "Matrix",
    "Plan",
    "PlannedField",
    "Route",
    "RouteStatus",
    "evaluate",
    "evaluate_route",
    "first_match",
    "get_fact",
    "get_matrix",
    "load_matrix",
    "plan",
]
