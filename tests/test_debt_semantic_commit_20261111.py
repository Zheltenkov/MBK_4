"""Admitted debt preferences remain independent and durable after a new store."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

import core
import debt_policy
from dialogue_frame import DialogueFrame
from policy_grounded_voice import GroundedReply
from policy_semantics import TurnAuthority, evaluate_semantics, parse_semantics
from policy_voice import SpokenPolicyReply
from state import init_state
from storage.sessions import SessionRecord
from storage.sqlite_sessions import SQLiteSessionRepository
from tests.test_policy_semantics import wire


def _state(mode: str = "debt") -> dict:
    state = init_state({"full_name": "Тест", "phone": "+70000000001", "debts_total": 400000})
    state[debt_policy.POLICY_KEY] = debt_policy.DebtPolicyState(mode=mode).as_state()
    return state


def _admitted(*acts: str, paused: bool = False) -> GroundedReply:
    # Admission itself is exercised separately; this boundary only commits its result.
    frame = DialogueFrame(None, "collected_held", known_fields=frozenset({"intent"}),
                          conversation_declined="decline_transfer" in acts)
    return GroundedReply(SpokenPolicyReply("Можем обсудить здесь.", None, "model", "validated"),
                         acts, paused, frame)


@pytest.mark.parametrize("acts,phone", [
    (("decline_transfer",), "open"),
    (("decline_transfer", "decline_phone"), "declined"),
    (("decline_phone", "decline_transfer"), "declined"),
])
def test_transfer_commit_preserves_both_independent_preferences(acts: tuple[str, ...], phone: str) -> None:
    state = _state()
    original = deepcopy(state)
    assert core._apply_conversation_semantics(state, _admitted(*acts), expected_offer=None)
    policy = debt_policy.parse_policy(state)
    assert policy is not None and policy.transfer_declined and policy.phone_collection == phone
    # Negative conversational authority never rewrites contacts or business receipts.
    for key in ("facts", "chat_history", "crm_lead", "crm_workflow", "delivery", "onloader"):
        assert state.get(key) == original.get(key)


@pytest.mark.parametrize("mode", ["clarify", "cancelled"])
def test_transfer_commit_requires_active_debt(mode: str) -> None:
    state = _state(mode)
    assert core._apply_conversation_semantics(state, _admitted("decline_transfer"), expected_offer=None)
    assert not debt_policy.parse_policy(state).transfer_declined


@pytest.mark.parametrize("paused", [False, True])
def test_empty_or_degraded_next_interpretation_cannot_reopen_transfer(paused: bool) -> None:
    state = _state()
    core._apply_conversation_semantics(state, _admitted("decline_transfer"), expected_offer=None)
    core._apply_conversation_semantics(state, _admitted(paused=paused), expected_offer=None)
    assert debt_policy.parse_policy(state).transfer_declined
    assert debt_policy.parse_policy(state).phone_collection == "open"


def test_phone_refusal_alone_does_not_create_transfer_refusal() -> None:
    state = _state()
    core._apply_conversation_semantics(state, _admitted("decline_phone"), expected_offer=None)
    policy = debt_policy.parse_policy(state)
    assert policy.phone_collection == "declined" and not policy.transfer_declined


def test_semantic_transfer_preference_survives_real_sqlite_reload(tmp_path: Path) -> None:
    state = _state()
    core._apply_conversation_semantics(state, _admitted("decline_transfer"), expected_offer=None)
    stamp = datetime.now(timezone.utc)
    record = SessionRecord("local-debt-refusal", stamp, stamp, True, None, state, [])
    path = tmp_path / "sessions.sqlite"
    SQLiteSessionRepository(path).save(record)
    restored = SQLiteSessionRepository(path).load(record.id)
    assert restored is not None
    assert restored.state == state
    # A later FAQ without a new preference cannot reset the restored refusal.
    core._apply_conversation_semantics(restored.state, _admitted(), expected_offer=None)
    policy = debt_policy.parse_policy(restored.state)
    assert policy.transfer_declined and policy.phone_collection == "open"
    assert restored.state["facts"] == state["facts"]


@pytest.mark.parametrize("message", [
    "Я предпочитаю не передавать мои данные сторонним организациям.",
    "Прошу не отправлять мои сведения другим компаниям.",
])
def test_model_candidate_outside_literal_parser_is_admitted_committed_and_reloaded(
    message: str, tmp_path: Path,
) -> None:
    # This is a synthetic C contract, not a claim about the model's accuracy.
    assert not debt_policy.transfer_contact_declined(message)
    frame = DialogueFrame(None, "collected_held", known_fields=frozenset({"intent"}))
    draft = "Можем обсудить здесь."
    body = wire(acts=[{"kind": "decline_transfer", "subject": "client", "scope": "current_offer",
                       "mode": "asserted", "evidence": message}])
    parsed = parse_semantics(json.dumps(body, ensure_ascii=False), latest_user_message=message, draft=draft)
    decision = evaluate_semantics(TurnAuthority.from_frame(frame, message, draft), parsed)
    acts = tuple(act.kind for act in decision.applied_user_acts)
    assert acts == ("decline_transfer",)
    result = GroundedReply(SpokenPolicyReply(draft, None, "model", "validated"),
                           acts, False, decision.effective_frame)
    state = _state()
    assert core._apply_conversation_semantics(state, result, expected_offer=None)
    stamp = datetime.now(timezone.utc)
    record = SessionRecord("semantic-debt-refusal", stamp, stamp, True, None, state, [])
    path = tmp_path / "sessions.sqlite"
    SQLiteSessionRepository(path).save(record)
    restored = SQLiteSessionRepository(path).load(record.id)
    assert restored is not None
    assert debt_policy.parse_policy(restored.state).transfer_declined
    assert debt_policy.parse_policy(restored.state).phone_collection == "open"
    assert restored.state["facts"] == state["facts"]
