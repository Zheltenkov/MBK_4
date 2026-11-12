"""An object-free request to explain the active offer keeps the purpose question open."""
import pytest

import core
from config import AppConfig
from policy_voice import SpokenPolicyReply
from tests.test_policy_voice import onloader_state


@pytest.mark.parametrize("message,expected", [
    ("Да. Объясните по-человечески, пожалуйста.", "offer_information_purpose"),
    ("Объясните проще, пожалуйста.", "offer_information_purpose"),
    ("Нет. Поясните простыми словами.", "offer_information_purpose"),
    ("Объясните, что такое реструктуризация.", None),
    ("Объясните тариф компании.", None),
])
def test_actual_onloader_turn_resolves_only_topic_free_explanation_to_the_current_offer(monkeypatch, message, expected):
    frames = []

    def capture(payload, configuration, **kwargs):
        frames.append(kwargs["frame"])
        return SpokenPolicyReply("Можем обсудить вопросы здесь.", None, "model", "model_advisory_accepted")

    def forbidden(*args, **kwargs):
        pytest.fail("A response-goal regression cannot use a model or submit an application")

    monkeypatch.setattr(core.policy_voice, "render", capture)
    monkeypatch.setattr(core.llm_agent, "_urlopen", forbidden)
    monkeypatch.setattr(core.delivery, "submit", forbidden)
    monkeypatch.setattr(core, "_record_transcript", lambda *args, **kwargs: None)
    config = AppConfig(openrouter_api_key="synthetic-unused", policy_voice_enabled=True)
    prepared = core.prepare_turn(onloader_state(), message, config, submit_delivery=False)
    list(core.stream_reply(prepared, message, config))
    assert len(frames) == 1
    assert frames[0].question_goal == expected
    assert prepared["onloader"]["decision"] is None
    assert not prepared.get("delivery")
