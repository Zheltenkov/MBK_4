"""Offline regressions from the Polza audit; no model or delivery adapters."""
from __future__ import annotations

from copy import deepcopy

import pytest

import debt_policy as debt
from routing import get_matrix


OWN_REQUESTS = [
    "Новый кредит уже не нужен. Хочу уменьшить платежи по тем долгам на 600000, о которых написал выше.",
    "Мне нужен не новый кредит, а помощь с платежами по существующим долгам.",
    "Хочу снизить платежи по своим долгам.",
    "Мне нужна помощь с платежами по моим долгам.",
]


@pytest.mark.parametrize("message", OWN_REQUESTS)
def test_asserted_own_debt_help_exits_clarify_without_editing_known_facts(message: str) -> None:
    # A goal decision changes policy only; requested credit and existing debts stay distinct.
    policy = debt.DebtPolicyState(mode="clarify", reason="ambiguous_request")
    state = {debt.POLICY_KEY: policy.as_state(), "facts": {
        "request": {"amount": 400000}, "debts": {"total": 600000},
        "client": {"name": "Сергей", "phone": "+70000000001", "region_class": "moscow_mo"},
    }}
    before = deepcopy(state)
    selected = debt.select_policy(state, message, enabled=True)
    assert selected is not None and selected.mode == "debt"
    assert selected.reason == "confirmed_intent"
    assert state == before
    assert selected.transfer_declined is False and selected.phone_collection == "open"


@pytest.mark.parametrize("message", [
    "Не хочу уменьшить платежи по своим долгам.",
    "Мне не нужна помощь с платежами по существующим долгам.",
    "Если я хочу уменьшить платежи по своим долгам, что делать?",
    "А если я хочу уменьшить платежи по своим долгам.",
    "Я хотел бы уменьшить платежи по своим долгам.",
    "Хочу уменьшить платежи по своим долгам?",
    "У моего друга долги. Хочу уменьшить платежи по его долгам.",
    "Хочу уменьшить платежи по долгам моего отца.",
    "Хочу уменьшить платежи по долгам клиента.",
    "Он сказал: хочу уменьшить платежи по своим долгам.",
    "«Хочу уменьшить платежи по своим долгам».",
    "Хочу новый кредит, чтобы уменьшить платежи по своим долгам.",
    "Мне нужен кредит на оплату существующих долгов.",
    "Брат сказал: хочу уменьшить платежи по своим долгам.",
    "Коллектор написал: мне нужна помощь с платежами по существующим долгам.",
    "Я не говорил, что хочу уменьшить платежи по своим долгам.",
    "Вы сказали, что я хочу уменьшить платежи по своим долгам.",
    "Мне нужна помощь с платежами по существующим долгам организации.",
    "Не думаю, что хочу уменьшить платежи по своим долгам.",
    "Пересказ: мне нужна помощь с платежами по моим долгам.",
])
def test_other_subject_question_hypothesis_negation_or_credit_does_not_confirm(message: str) -> None:
    # Contrasting intent must never be promoted merely because debt words occur.
    assert debt.detect_entry(message) != "explicit"
    selected = debt.select_policy(
        {debt.POLICY_KEY: debt.DebtPolicyState(mode="clarify", reason="ambiguous_request").as_state()},
        message, enabled=True,
    )
    assert selected is not None and selected.mode != "debt"


@pytest.mark.parametrize("message", OWN_REQUESTS)
def test_new_debt_help_entry_still_requires_enabled_feature(message: str) -> None:
    assert debt.select_policy({}, message) is None
    selected = debt.select_policy({}, message, enabled=True)
    assert selected is not None and selected.mode == "debt"


def test_goal_confirmation_does_not_turn_approximate_debt_into_exact_amount() -> None:
    # Entry recognition grants neither a numeric fact nor permission to skip precision.
    policy = debt.DebtPolicyState(mode="clarify", reason="ambiguous_request",
                                 amount_observation=debt.DebtObservation(kind="unknown"))
    selected = debt.select_policy({debt.POLICY_KEY: policy.as_state()}, OWN_REQUESTS[1], enabled=True)
    assert selected is not None and selected.mode == "debt"
    plan = debt.plan_debt(get_matrix(), {"debts": {"total": 400000}}, selected)
    assert plan.field_paths == ["debts.total"]
    assert plan.fields[0].needs_confirmation is True
    assert debt.detect_debt_observation("Общий долг около 400000 рублей.", expected=True) == debt.DebtObservation(kind="unknown")
    assert debt.detect_debt_observation("Общий долг ровно 400000 рублей.", expected=True) == debt.DebtObservation(kind="exact", amount=400000)


@pytest.mark.parametrize("message", [
    "Нет, не разрешаю передавать мои данные специалисту. Давайте обсуждать здесь.",
    "Не разрешаю отправлять мои данные менеджеру.",
    "Нет, я не разрешаю передавать мои данные консультанту.",
])
def test_literal_transfer_refusal_is_code_evidence_without_refusing_phone(message: str) -> None:
    assert debt.transfer_contact_declined(message) is True
    assert debt.phone_contact_declined(message, expected=False) is False
    assert debt.pure_debt_turn_act(message, active_debt=True) == "decline_transfer"
    assert debt.pure_debt_turn_act(message, active_debt=False) is None


@pytest.mark.parametrize("message", [
    "Нет, не разрешаю передавать мои данные специалисту?",
    "Если не разрешаю передавать мои данные специалисту.",
    "Он сказал: нет, не разрешаю передавать мои данные специалисту.",
    "Мама не разрешает передавать свои данные специалисту.",
    "«Нет, не разрешаю передавать мои данные специалисту».",
    "Не разрешаю передавать его данные специалисту.",
    "Нет, не разрешаю передавать мои данные специалисту, но отправьте заявку.",
    "Да, разрешаю передавать мои данные специалисту.",
])
def test_literal_refusal_requires_a_whole_own_assertion_not_permission(message: str) -> None:
    assert debt.transfer_contact_declined(message) is False
    assert debt.pure_debt_turn_act(message, active_debt=True) is None
