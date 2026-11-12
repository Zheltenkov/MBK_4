"""A proven neutral field response needs no model to approve a claim-free ACK."""
from dataclasses import replace

import pytest

from dialogue_frame import DialogueFrame
import policy_semantics
import policy_voice
from tests.test_policy_v31_receipts import actual_turn, offline  # noqa: F401
from tests.test_policy_v34_brief_render import http_turn


def brief_frame():
    return DialogueFrame(None, "collected_held", known_fields=frozenset({"client.region_class"}),
                         plain_reply_fields=frozenset({"client.region_class"}))


@pytest.mark.parametrize("word", ["Хорошо.", "Угу.", "Понимаю.", "Ладно!", "Поняла.", "Ясно.", "Да."])
def test_only_whole_claim_free_model_acknowledgements_can_receive_code_approval(word):
    assert policy_voice.is_code_checked_brief_reply(word, brief_frame())


@pytest.mark.parametrize("text", [
    "Хорошо?", "«Хорошо»", "Не хорошо.", "Хорошо, заявку передала.",
    "Понимаю. Вам перезвонят.", "Приняла.", "Записала.", "Готово.",
    "Согласна.", "Одобрено.", "При банкротстве действуют условия закона.",
])
def test_unknown_questions_recording_consent_and_factual_content_keep_model_validation(text):
    assert not policy_voice.is_code_checked_brief_reply(text, brief_frame())


@pytest.mark.parametrize("changes", [
    {"plain_reply_fields": frozenset()}, {"known_fields": frozenset()},
    {"mode": "onloader", "offer_present": True}, {"confirming": True},
    {"conversation_paused": True}, {"conversation_declined": True},
    {"refused_fields": frozenset({"client.phone"})}, {"closing": True},
    {"question_goal": "debt_explanation"},
    {"question_goal": "debt_explanation", "question_goal_origin": "model_hint"},
])
def test_a_trusted_neutral_field_role_is_required(changes):
    assert not policy_voice.is_code_checked_brief_reply("Хорошо.", replace(brief_frame(), **changes))


@pytest.mark.usefixtures("real_policy_voice_critic")
def test_actual_masked_http_input_uses_one_generation_and_zero_fabricated_critics(monkeypatch):
    result, generations, requests = http_turn(
        monkeypatch, semantic=True, first_region=True, replies=("Хорошо, Казань.",),
    )
    assert result.bubbles == ["Хорошо."]
    assert len(generations) == len(requests) == 1
    assert result.voice["reason"] == "model_code_checked_brief"
    assert result.voice["critic_calls"] == 0
    assert result.voice["critic_assessments"] == result.voice["diagnostic_assessments"] == []
    assert result.voice["semantic_analyses"] == [] and not result.state.get("delivery")


@pytest.mark.usefixtures("real_policy_voice_critic")
def test_hard_discarded_g1_can_repair_to_a_code_checked_ack_without_c(monkeypatch):
    result, generations, requests = http_turn(
        monkeypatch, semantic=True, first_region=True,
        replies=("Сколько у вас кредиторов?", "Хорошо."),
    )
    assert result.bubbles == ["Хорошо."] and len(generations) == len(requests) == 2
    assert result.voice["critic_calls"] == 0 and result.voice["critic_assessments"] == []


@pytest.mark.parametrize("latest", ["Мне страшно.", "Татарстан правильно записали?", "Номер не дам."])
def test_actual_core_does_not_derive_a_neutral_role_from_a_known_region_alone(monkeypatch, latest):
    _, spoken, state, _, analyses = actual_turn(monkeypatch, latest, ["Понимаю."] * 2)
    assert spoken.critic_calls >= 1 and analyses
    assert spoken.reason != "model_code_checked_brief" and not state.get("delivery")


@pytest.mark.parametrize("quote", ['"', '“'])
def test_unclosed_quotes_do_not_supply_a_code_owned_phone_refusal(quote):
    latest = f"Менеджер написал: {quote}Это пример. Телефон не дам. Это чужие слова."
    assert policy_semantics.explicit_conversational_declines(latest, brief_frame()) == ()
