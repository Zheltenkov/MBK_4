"""Производные факты: то, что анкета не спрашивает, но требуют маршруты.

Анкета спрашивает «есть действующие кредиты?» и «как долго тянется просрочка»,
а маршруты БФЛ опираются на `debts.credit_history`. Значение выводится
детерминированно — так же, как класс региона выводится из ответа DaData.
"""
from __future__ import annotations

import pytest

from routing.derive import derive_facts
from routing.engine import get_fact


@pytest.mark.parametrize(
    ("facts", "expected_history", "expected_total"),
    [
        ({"debts": {"has_active_credits": False}}, "no_overdue", 0),
        (
            {"debts": {"has_active_credits": True, "overdue_duration": "no_overdue"}},
            "no_overdue",
            None,
        ),
        (
            {"debts": {"has_active_credits": True, "overdue_duration": "up_to_1m"}},
            "has_overdue",
            None,
        ),
        (
            {"debts": {"has_active_credits": True, "overdue_duration": "over_6m"}},
            "has_overdue",
            None,
        ),
    ],
)
def test_credit_history_is_derived_from_the_two_asked_questions(
    facts, expected_history, expected_total
):
    result = derive_facts(facts)

    assert get_fact(result, "debts.credit_history") == expected_history
    if expected_total is not None:
        assert get_fact(result, "debts.total") == expected_total


def test_nothing_is_derived_before_the_client_answers():
    result = derive_facts({})

    assert get_fact(result, "debts.credit_history") is None
    assert get_fact(result, "debts.total") is None


def test_explicit_credit_history_is_never_overwritten():
    """Клиент сказал «готов к банкротству» — это ценнее выведенного значения.

    Перезапись открыла бы БФЛ тем, кому он сейчас закрыт, то есть протащила бы
    изменение маршрутизации под видом рефакторинга.
    """
    facts = {
        "debts": {
            "has_active_credits": True,
            "overdue_duration": "over_6m",
            "credit_history": "ready_bankruptcy",
        }
    }

    result = derive_facts(facts)

    assert get_fact(result, "debts.credit_history") == "ready_bankruptcy"


def test_named_debt_total_survives_the_no_credits_answer():
    """Если сумма долга уже названа, «действующих кредитов нет» её не обнуляет."""
    facts = {"debts": {"has_active_credits": False, "total": 250_000}}

    result = derive_facts(facts)

    assert get_fact(result, "debts.total") == 250_000


def test_derive_does_not_mutate_the_input():
    facts = {"debts": {"has_active_credits": False}}

    derive_facts(facts)

    assert get_fact(facts, "debts.credit_history") is None
