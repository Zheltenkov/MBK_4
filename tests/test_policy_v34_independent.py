"""Independent wire contracts for one brief job inside the existing conversation."""
from hashlib import sha256
import socket

import pytest

import llm_agent
import prompts
from tests.test_policy_debt_continuation_independent import held_state
from tests.test_policy_v22_independent import actual_turn


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Independent V34 checks cannot use the network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


@pytest.mark.parametrize("latest", ["Поняла. Я в Казани.", "Да, Татарстан."])
def test_the_actual_brief_turn_has_one_small_job_without_credit_collection_examples(monkeypatch, latest):
    state = held_state()
    answer, _, committed, calls = actual_turn(monkeypatch, state, latest, replies=["Угу."])
    speaker = next(call for call in calls if call["model"] == "synthetic-speaker")
    messages = speaker["messages"]
    system = "\n".join(message["content"] for message in messages if message["role"] == "system")
    assert prompts.STYLE_EXAMPLES not in system
    assert "Твоя работа — разобраться в ситуации и в" not in system
    assert prompts.CONVERSATION_VOICE in system
    assert messages[-1]["role"] == "user"
    assert answer == "Угу." and len(calls) == 2
    assert committed["facts"] == state["facts"] and not committed.get("delivery")


def test_the_ordinary_conversation_keeps_its_original_system_bytes_and_three_system_messages():
    payload = {"latest_user_message": "Что такое банкротство?", "turn_instruction": "answer",
               "short_history": [{"role": "assistant", "content": "Чем помочь?"}]}
    messages = llm_agent._conversation_messages(payload)
    assert sha256(messages[0]["content"].encode()).hexdigest() == (
        "e0c713640e04aba84eaa649ee921acbd1fc7116a7985d3395095a25fa68d1669"
    )
    assert [message["role"] for message in messages[:3]] == ["system"] * 3
    assert messages[-2:] == [{"role": "assistant", "content": "Чем помочь?"},
                            {"role": "user", "content": "Что такое банкротство?"}]


@pytest.mark.parametrize("latest", ["Мне страшно.", "Казань записали?"])
def test_a_question_or_emotion_keeps_the_ordinary_context_in_the_same_bot(monkeypatch, latest):
    answer, _, _, calls = actual_turn(monkeypatch, held_state(), latest, replies=["Понимаю."])
    speaker = next(call for call in calls if call["model"] == "synthetic-speaker")
    assert speaker["messages"][0]["content"] == prompts.CONVERSATION_SYSTEM_PROMPT + "\n\n" + prompts.STYLE_EXAMPLES
    assert answer == "Понимаю."
