"""A degraded debt explanation stays readable without changing the verified facts."""
from dataclasses import replace

import pytest

from dialogue_frame import DialogueFrame
from policy_content import BANKRUPTCY_EXPLANATION, RESTRUCTURING_EXPLANATION, content_issues
import policy_grounded_voice
import policy_voice
import prompts
from tests.test_policy_grounded_voice import install, semantic_raw, strict_settings


def faq_frame() -> DialogueFrame:
    return DialogueFrame("debts.total", "incomplete_held", confirming=True,
                         question_goal="debt_explanation")


@pytest.mark.parametrize("changes", [
    {}, {"refused_fields": frozenset({"client.phone"})},
    {"conversation_declined": True}, {"conversation_paused": True},
])
def test_the_spoken_fallback_is_short_and_preserves_the_existing_choice(changes):
    frame = replace(faq_frame(), **changes)
    result = policy_voice._fallback_reply(frame, "В каком городе вы живёте?", None, "synthetic")
    assert len(result.text) <= 650
    assert len(result.text.split("\n\n")) <= 2
    assert "реструктуризации" in result.text.casefold() and "долг остаётся" in result.text.casefold()
    assert "могут" in result.text and "алименты" in result.text
    assert "жизни или здоровью" in result.text and "кредитор" in result.text
    assert content_issues(result.text, mode="debt", question_goal="debt_explanation") == ()
    assert result.source == "fallback" and result.asked_field is None
    assert frame == replace(faq_frame(), **changes)


def test_the_generation_and_repair_context_keeps_the_full_verified_facts():
    frame = faq_frame()
    assert prompts.verified_policy_purpose(frame) == RESTRUCTURING_EXPLANATION + " " + BANKRUPTCY_EXPLANATION
    assert BANKRUPTCY_EXPLANATION in prompts.policy_turn_instruction(frame)
    assert BANKRUPTCY_EXPLANATION in prompts.policy_regeneration_instruction(frame, ("overbroad_harm_exception",))


def test_two_actual_rejected_drafts_recover_a_short_explanation_without_another_call(monkeypatch):
    first = "Через банкротство спишут всё, кроме алиментов и возмещения вреда."
    second = "Подскажите, есть ли сейчас просрочки по платежам?"
    generations, analyses = install(monkeypatch, [first, second], [semantic_raw(), semantic_raw()])
    result = policy_grounded_voice.render(
        {"latest_user_message": "Чем реструктуризация отличается от списания?"},
        strict_settings(), frame=faq_frame(), fallback="Назовите точную сумму долга.",
    )
    assert len(generations) == len(analyses) == 2
    assert result.spoken.source == "fallback" and result.spoken.asked_field is None
    assert len(result.spoken.text) <= 650
    assert first not in result.spoken.text and second not in result.spoken.text
    assert result.applied_user_acts == ()
