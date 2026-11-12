"""A protective pause closes contact speech, not the pending questionnaire."""
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import json

import pytest

from config import AppConfig
import core
import debt_policy
from dialogue_frame import DialogueFrame, constrain_collection
import llm_agent
from state import init_state
from tests.test_policy_semantics import wire


@pytest.mark.parametrize("path", ["client.name", "client.phone"])
def test_pause_blocks_contact_capability_but_retains_raw_expected(path):
    frame = DialogueFrame(path, "collecting_contacts", conversation_paused=True)
    assert frame.expected == path and frame.allowed_field is None
    assert not frame.allows(path)


@pytest.mark.parametrize("path", ["client.name", "client.phone"])
def test_unpaused_unknown_contact_question_is_still_allowed(path):
    frame = DialogueFrame(path, "collecting_contacts")
    assert frame.allowed_field == path and frame.allows(path)


@pytest.mark.parametrize("paused", [False, True])
def test_known_phone_is_never_requested_again(paused):
    frame = DialogueFrame("client.phone", "collecting_contacts", known_fields=frozenset({"client.phone"}),
                          conversation_paused=paused)
    assert frame.allowed_field is None


def test_phone_only_refusal_does_not_turn_into_transfer_refusal_or_change_old_name_rule():
    frame = DialogueFrame("client.name", "collecting_contacts", refused_fields=frozenset({"client.phone"}))
    assert not frame.conversation_declined and frame.allowed_field == "client.name"
    assert replace(frame, conversation_paused=True).allowed_field is None


@pytest.mark.parametrize("path", ["client.name", "client.phone"])
def test_actual_transfer_refusal_still_closes_both_contact_questions(path):
    assert DialogueFrame(path, "collecting_contacts", conversation_declined=True).allowed_field is None


@pytest.mark.parametrize("path,question", [
    ("debts.total", "Назовите общую сумму долга."),
    ("intent", "Вы хотите обсудить списание или реструктуризацию именно ваших долгов?"),
])
def test_pause_keeps_amount_and_intent_clarification(path, question):
    frame = DialogueFrame(path, "collecting_debt_context", conversation_paused=True)
    assert frame.expected == path and frame.allowed_field == path
    result = constrain_collection(question, frame)
    assert result.text == question and result.asked_field == path and not result.removed_fields


def frozen_control_state():
    """Business-state projection of frozen simple-debt/control/u01; phone absent.

    Only protective pause and a minimal prior-question history are supplied for
    this offline probe. No external artifact or DB is read at test runtime.
    """
    state = init_state()
    state.update({
        "facts": {"client": {"name": "Ирина", "region": "Москва", "region_class": "moscow_mo"},
                  "debts": {"total": 400000}},
        "fact_statuses": {"client.name": "known", "client.region_class": "known", "debts.total": "known"},
        debt_policy.POLICY_KEY: debt_policy.DebtPolicyState(
            amount_observation=debt_policy.DebtObservation(kind="exact", amount=400000)).as_state(),
        "script": {"asked": ["client.phone"]},
        "debt_script": {"attempts": {"client.phone": 1}},
        "dialog_phase": "collecting_contacts", "conversation_semantics": {"paused": True},
        "chat_history": [{"role": "assistant", "content": "На какой номер можно с вами связаться?"}],
    })
    return state


def actual_turn(monkeypatch, state, draft):
    cfg = AppConfig(openrouter_api_key="synthetic-unused", model="synthetic-speaker",
                    extractor_model="synthetic-analysis", max_tokens=2048, request_timeout=40,
                    policy_voice_enabled=True, policy_semantic_enabled=True)
    policy = debt_policy.parse_policy(state)
    state["_plan_object"] = debt_policy.plan_debt(core.get_matrix(), state["facts"], policy,
        fact_statuses=state["fact_statuses"], attempts=state["debt_script"]["attempts"])
    calls = []
    def open_request(configuration, request):
        body = json.loads(request.data)
        role = "G" if body["model"] == cfg.model else "C"
        calls.append(role)
        # The C request slots are all null: deterministic collection guards
        # must reject the request even when the validator omits its speech act.
        content = draft if role == "G" else json.dumps(wire())
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    before = deepcopy(state)
    latest = "Продолжим обсуждение здесь."
    text = core._policy_conversation(state, latest, cfg)
    spoken = state["_policy_spoken"]
    committed = core.commit_turn(state, latest, text)["current_state"]
    assert committed["facts"] == before["facts"]
    assert committed["questionnaire_policy"] == before["questionnaire_policy"]
    assert committed["conversation_semantics"]["paused"] is True
    assert not committed.get("delivery")
    assert committed["chat_history"][-2:] == [
        {"role": "user", "content": latest}, {"role": "assistant", "content": text}]
    return committed, spoken, calls, before


def test_actual_audit_core_case_does_not_publish_phone_or_count_unvoiced_question(monkeypatch):
    committed, spoken, _, before = actual_turn(monkeypatch, frozen_control_state(), "Оставьте номер телефона.")
    assert spoken.source == "fallback" and spoken.asked_field is None
    assert "Оставьте номер" not in spoken.text
    assert committed["script"]["asked"] == ["client.phone"]
    assert committed["debt_script"] == before["debt_script"]
    assert not committed["facts"]["client"].get("phone")
    assert not committed["questionnaire_policy"]["transfer_declined"]
    assert committed["questionnaire_policy"]["phone_collection"] == "open"


def test_actual_name_question_is_also_blocked_during_pause(monkeypatch):
    state = frozen_control_state()
    state["facts"]["client"].pop("name")
    state["fact_statuses"].pop("client.name")
    state["script"]["asked"] = ["client.name"]
    state["debt_script"]["attempts"] = {"client.name": 1}
    committed, spoken, _, before = actual_turn(monkeypatch, state, "Как к вам обращаться?")
    assert spoken.source == "fallback" and spoken.asked_field is None
    assert committed["script"]["asked"] == ["client.name"]
    assert committed["debt_script"] == before["debt_script"]


def test_actual_amount_question_keeps_its_cursor_and_counts_only_voiced_question(monkeypatch):
    state = frozen_control_state()
    state["facts"]["debts"].pop("total")
    state["fact_statuses"].pop("debts.total")
    state["questionnaire_policy"]["amount_observation"] = {"kind": "unknown", "amount": None, "lower": None, "upper": None}
    state["script"]["asked"] = ["debts.total"]
    state["debt_script"]["attempts"] = {"debts.total": 1}
    committed, spoken, calls, _ = actual_turn(monkeypatch, state, "Назовите общую сумму долга.")
    assert spoken.source == "model" and spoken.asked_field == "debts.total"
    assert calls == ["G", "C"]
    assert committed["script"]["asked"] == ["debts.total"]
    assert committed["debt_script"]["attempts"]["debts.total"] == 2


def test_faq_stays_free_while_contact_pause_remains(monkeypatch):
    answer = "Можем спокойно обсудить ваш вопрос здесь."
    _, spoken, calls, _ = actual_turn(monkeypatch, frozen_control_state(), answer)
    assert spoken.text == answer and spoken.source == "model" and spoken.asked_field is None
    assert calls == ["G", "C"]


def test_durable_no_and_phone_preference_remain_independent(monkeypatch):
    state = frozen_control_state()
    state["questionnaire_policy"]["transfer_declined"] = True
    _, spoken, _, _ = actual_turn(monkeypatch, state, "Оставьте номер телефона.")
    assert spoken.source == "fallback" and spoken.asked_field is None


def test_existing_phone_refusal_does_not_grant_transfer_no(monkeypatch):
    state = frozen_control_state()
    state["questionnaire_policy"]["phone_collection"] = "declined"
    committed, spoken, _, _ = actual_turn(monkeypatch, state, "Можем обсудить здесь.")
    assert not committed["questionnaire_policy"]["transfer_declined"]
    assert spoken.asked_field is None
