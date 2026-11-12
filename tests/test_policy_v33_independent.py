"""Independent role checks for proven field replies without an outstanding FAQ."""
from dataclasses import replace
import json
import socket

import pytest

from dialogue_frame import DialogueFrame
import prompts
from tests.test_policy_debt_continuation_independent import held_state
from tests.test_policy_v22_independent import actual_turn


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Independent V33 checks cannot use the network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def brief_frame() -> DialogueFrame:
    return DialogueFrame(
        None, "collected_held",
        known_fields=frozenset({"debts.total", "client.region_class", "client.phone"}),
        plain_reply_fields=frozenset({"client.region_class"}),
    )


def test_a_proven_region_reply_has_a_brief_role_without_a_previous_optional_offer():
    frame = brief_frame()
    assert not frame.defer_discussion_followup
    assert getattr(frame, "brief_field_reply", False)
    instruction = prompts.policy_turn_instruction(frame)
    assert "Текущая цель — содержательно продолжить обсуждение" not in instruction
    assert "Проверенные сведения для обсуждения вариантов" not in instruction
    assert '"new_application_available": false' in instruction
    messages = prompts.policy_critic_messages(
        frame, draft="Угу.", history=[], latest_user_message="Да, Татарстан.",
    )
    wire = json.loads(messages[-1]["content"])
    assert wire["task"]["primary_goal"] == "respond_to_field_update"


@pytest.mark.parametrize("changes", [
    {"plain_reply_fields": frozenset()},
    {"plain_reply_fields": frozenset({"debts.total"})},
    {"question_goal": "debt_explanation"},
    {"expected": "client.name"},
    {"confirming": True},
    {"closing": True},
    {"conversation_declined": True},
    {"mode": "onloader"},
    {"offer_present": True},
])
def test_the_brief_role_cannot_replace_another_authorized_task(changes):
    assert not getattr(replace(brief_frame(), **changes), "brief_field_reply", False)


@pytest.mark.parametrize("latest", ["Поняла. Я в Казани.", "Да, Татарстан."])
def test_normal_entry_sends_a_consistent_brief_job_and_preserves_the_free_reply(monkeypatch, latest):
    state = held_state()
    answer, _, committed, calls = actual_turn(monkeypatch, state, latest, replies=["Угу."])
    assert answer == "Угу."
    speaker = next(call for call in calls if call["model"] == "synthetic-speaker")
    sent = "\n".join(message["content"] for message in speaker["messages"])
    assert "Текущая цель — содержательно продолжить обсуждение" not in sent
    assert "Проверенные сведения для обсуждения вариантов" not in sent
    assert len(calls) == 2 and committed["facts"] == state["facts"]
    assert not committed.get("delivery")
