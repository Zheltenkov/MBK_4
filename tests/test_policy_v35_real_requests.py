"""Real anonymized utterances distinguish a debt question from a past bankruptcy."""
from copy import deepcopy

import core
import debt_policy
from state import init_state
from tests.test_debt_dialogue_progress import offline, settings  # noqa: F401


def test_restructuring_question_clarifies_intent_before_starting_another_loan_intake():
    initial = init_state()
    initial["facts"] = {"client": {"name": "Ирина", "phone": "+70000000001"}}
    initial["chat_history"] = [
        {"role": "user", "content": "Вчера подавал заявку, но не перезвонили"},
        {"role": "assistant", "content": "Чем могу помочь сейчас?"},
    ]
    before = deepcopy(initial["facts"])
    prepared = core.prepare_turn(
        initial, "Есть возможность сделать реструктуризацию?", settings(), submit_delivery=False,
    )
    policy = debt_policy.parse_policy(prepared)
    assert policy is not None and policy.mode == "clarify"
    # Intent is a conversation clarification, outside the loan-field matrix.
    assert prepared["dialog_phase"] == "clarifying_intent"
    assert prepared["plan"]["fields"] == []
    assert prepared["facts"] == before and prepared["chat_history"] == initial["chat_history"]
    assert not prepared.get("delivery")


def test_credit_request_after_a_completed_bankruptcy_keeps_its_credit_goal():
    prepared = core.prepare_turn(
        init_state(),
        "Здравствуйте хотела взять кредит не дают, так как прошла процедуру банкротства, прошло 4 года",
        settings(), submit_delivery=False,
    )
    assert debt_policy.parse_policy(prepared) is None
    assert not prepared.get("delivery")
