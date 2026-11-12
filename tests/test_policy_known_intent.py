"""Only confirmed debt mode projects an already known intent into the actual speech frame."""
from copy import deepcopy
import json

import pytest

import core
import debt_policy
import llm_agent
import policy_voice
from config import AppConfig
from tests.test_policy_voice import debt_state


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(llm_agent, "extract_facts", lambda *args, **kwargs: {"fact_updates": [], "internal_summary": ""})
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *args, **kwargs: deepcopy(facts))
    monkeypatch.setattr(core, "_record_transcript", lambda *args, **kwargs: None)

    def forbidden(*args, **kwargs):
        pytest.fail("Known-intent frame controls cannot call a model or submit a lead")

    monkeypatch.setattr(llm_agent, "_post", forbidden)
    monkeypatch.setattr(core.delivery, "submit", forbidden)


def configuration():
    return AppConfig(openrouter_api_key="synthetic-unused", policy_voice_enabled=True,
                     debt_policy_enabled=True, entry_routing_enabled=False, fact_interpretation_mode="legacy")


def capture_turn(monkeypatch, state, message):
    frames = []

    def render(payload, config, *, frame, **kwargs):
        frames.append(frame)
        return policy_voice.SpokenPolicyReply("Можно обсудить вопрос здесь.", None, "model", "synthetic-frame-control")

    monkeypatch.setattr(policy_voice, "render", render)
    prepared = core.prepare_turn(state, message, configuration(), submit_delivery=False)
    speech = "".join(core.stream_reply(prepared, message, configuration()))
    assert len(frames) == 1
    committed = core.commit_turn(prepared, message, speech)["current_state"]
    return frames[0], committed


@pytest.mark.parametrize("mode", ["debt", "clarify", "cancelled"])
def test_only_confirmed_debt_projects_known_intent_without_changing_contacts_or_history(monkeypatch, mode):
    state = debt_state(client={"name": "Ирина", "phone": "+70000000401", "region_class": "rf_other"},
                       debts={"total": 400000})
    state["session_id"] = "synthetic-known-intent"
    state[debt_policy.POLICY_KEY] = debt_policy.DebtPolicyState(mode=mode, reason="explicit_request").as_state()
    state["chat_history"] = [{"role": "user", "content": "Хочу обсудить свои долги."},
                             {"role": "assistant", "content": "Можно обсудить ситуацию здесь."}]
    state = core.initialize_debt_questionnaire(state)
    previous_facts = deepcopy(state["facts"])
    previous_history = deepcopy(state["chat_history"])
    frame, committed = capture_turn(monkeypatch, state, "Какие есть варианты?")
    assert ("intent" in frame.known_fields) is (mode == "debt")
    assert {"client.name", "client.phone", "client.region_class"} <= frame.known_fields
    assert committed["facts"] == previous_facts
    assert committed["chat_history"][:len(previous_history)] == previous_history
    assert committed["session_id"] == state["session_id"] and committed.get("delivery") is None


def test_the_confirming_answer_projects_known_intent_immediately_in_the_same_restored_chat(monkeypatch):
    state = debt_state(client={"name": "Ирина", "phone": "+70000000401", "region_class": "rf_other"},
                       debts={"total": None})
    state["session_id"] = "synthetic-restored-known-intent"
    state[debt_policy.POLICY_KEY] = debt_policy.DebtPolicyState(mode="clarify", reason="ambiguous_request").as_state()
    state = core.initialize_debt_questionnaire(state)
    state = json.loads(json.dumps(state))
    original = deepcopy(state["facts"])
    frame, committed = capture_turn(monkeypatch, state, "Да, хочу.")
    assert "intent" in frame.known_fields and frame.expected == "debts.total"
    assert debt_policy.parse_policy(committed).mode == "debt"
    assert committed["facts"] == original and committed["session_id"] == state["session_id"]
    assert committed.get("delivery") is None
