"""A whole reference to an already saved phone needs no fact-extraction call."""
from __future__ import annotations

from copy import deepcopy

import pytest

import core
import debt_policy
import debt_workflow
from config import AppConfig
from state import init_state
from tests.test_policy_v22_independent import actual_turn


MESSAGE = "Да, согласен на передачу специалисту. Телефон я уже оставил выше."


def _state():
    state = init_state({"full_name": "Тест", "phone": "+70000000001", "debts_total": 600000,
                        "desired_amount": 400000, "region": "Москва"})
    state[debt_policy.POLICY_KEY] = debt_policy.DebtPolicyState(transfer_declined=True).as_state()
    return state


@pytest.mark.parametrize("message", [MESSAGE, "Телефон я уже оставил выше.",
                                      "Я уже сообщила свой номер ранее."])
def test_whole_own_reference_has_no_new_fact(message):
    assert debt_workflow.known_phone_reference(_state(), message)


@pytest.mark.parametrize("message", [
    "Телефон я уже оставил выше. Общий долг теперь 450000 рублей.",
    "Телефон я уже оставил выше. Новый номер +70000000002.",
    "Телефон я уже оставил выше. Меня зовут Иван.",
    "Если телефон я уже оставил выше, что будет?",
    "Он сказал: телефон я уже оставил выше.",
    "«Телефон я уже оставил выше».",
    "Телефон я уже оставил выше?",
    "Телефон я уже оставил выше. Не передавайте данные.",
    "Телефон мамы я уже оставил выше.",
])
def test_mixed_uncertain_or_nonown_messages_keep_normal_extraction(message):
    assert not debt_workflow.known_phone_reference(_state(), message)


@pytest.mark.parametrize("change", ["unknown_phone", "clarify", "onloader"])
def test_reference_needs_known_phone_and_active_debt_without_onloader(change):
    state = _state()
    if change == "unknown_phone":
        state["facts"]["client"].pop("phone")
    elif change == "clarify":
        state[debt_policy.POLICY_KEY] = debt_policy.DebtPolicyState(mode="clarify").as_state()
    else:
        state["onloader"] = {"status": "awaiting_consent"}
    assert not debt_workflow.known_phone_reference(state, MESSAGE)


def test_reference_skips_only_extraction_and_does_not_grant_transfer(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("A proved known-phone reference needs no model extraction or delivery")

    monkeypatch.setattr(core, "_run_extractors", forbidden)
    monkeypatch.setattr(core.delivery, "submit", forbidden)
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *a, **k: deepcopy(facts))
    monkeypatch.setattr(core, "_record_transcript", lambda *a, **k: None)
    state = _state()
    original = deepcopy(state)
    prepared = core.prepare_turn(state, MESSAGE, AppConfig(openrouter_api_key="synthetic-unused",
                                debt_policy_enabled=True), submit_delivery=False)
    assert prepared["facts"] == original["facts"]
    assert prepared["chat_history"] == original["chat_history"]
    assert prepared["_last_user_message"] == MESSAGE
    assert core.build_runtime_payload(prepared, MESSAGE)["latest_user_message"] == MESSAGE
    assert debt_policy.parse_policy(prepared).transfer_declined
    assert prepared["crm_workflow"] == original["crm_workflow"]
    assert prepared.get("delivery") == original.get("delivery")


def test_reference_still_reaches_full_generation_critic_and_commit(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Only fact extraction must be skipped")

    monkeypatch.setattr(core, "_run_extractors", forbidden)
    state = _state()
    reply = "Передача заявки по долгам из этого чата пока не подключена."
    _, _, committed, calls = actual_turn(monkeypatch, state, MESSAGE, replies=[reply, reply])
    for model in ("synthetic-speaker", "synthetic-analysis"):
        bodies = [body for body in calls if body["model"] == model]
        assert bodies
        assert any(MESSAGE in " ".join(item["content"] for item in body["messages"]) for body in bodies)
    assert committed["chat_history"][-2]["content"] == MESSAGE
    assert committed["facts"] == state["facts"]
    assert debt_policy.parse_policy(committed).transfer_declined
    assert not committed.get("delivery")
