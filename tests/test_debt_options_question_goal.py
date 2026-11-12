"""A procedure question after a place answer defers the next intake question."""
from copy import deepcopy

import core
from tests.test_policy_grounded_voice import install, semantic_raw, strict_settings
from tests.test_policy_voice import debt_state


def test_an_alternative_procedure_question_has_its_own_answer_goal(monkeypatch):
    state = debt_state(debts={"total": 280000}, client={"region_class": "moscow_mo"})
    before = deepcopy(state["facts"])
    answer = "Реструктуризация меняет условия договора. Возможность для конкретного человека зависит от обстоятельств."
    generations, _ = install(monkeypatch, [answer], [semantic_raw()])
    text = core._policy_conversation(state, "Москва, можно ли без банкротства?", strict_settings())
    assert text == answer and state["facts"] == before
    assert state["_policy_spoken"].asked_field is None
    assert "Человек просит объяснить понятие" in generations[0]["turn_instruction"]
