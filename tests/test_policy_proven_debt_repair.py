"""Code-rejected drafts do not need semantic analysis for a proven no-fact input."""
from dataclasses import replace

import pytest

from dialogue_frame import DialogueFrame
import policy_grounded_voice
from tests.test_policy_grounded_voice import install, semantic_raw, strict_settings


def debt_frame():
    return DialogueFrame(None, "collected_held", known_fields=frozenset({
        "intent", "debts.total", "client.region_class", "client.phone", "client.name",
    }))


@pytest.mark.parametrize("latest,declined,bad", [
    ("Разобраться с долгами", False, "Общая сумма у нас 400 000 рублей — это верно?"),
    ("Мои данные никуда не передавайте.", True, "Передам вашу заявку партнёру."),
])
def test_proven_entry_or_refusal_discards_bad_g1_before_one_actual_final_c(monkeypatch, latest, declined, bad):
    calls, analyses = install(monkeypatch, [bad, "Можем поговорить здесь."], [semantic_raw()] * 2)
    result = policy_grounded_voice.render(
        {"latest_user_message": latest}, strict_settings(),
        frame=replace(debt_frame(), conversation_declined=declined), fallback="Можем поговорить здесь.",
    )
    assert result.spoken.text == "Можем поговорить здесь."
    assert len(calls) == 2 and len(analyses) == result.spoken.critic_calls == 1
    assert analyses[0][0] == result.spoken.text


@pytest.mark.parametrize("latest", [
    "Мои данные никуда не передавайте. Долг 500000 рублей.",
    "Если я скажу не передавайте мои данные?", "Что такое банкротство?",
])
def test_mixed_unknown_and_factual_inputs_keep_the_actual_user_interpretation(monkeypatch, latest):
    calls, analyses = install(monkeypatch, ["На какой номер позвонить?", "Можем поговорить здесь."],
                               [semantic_raw()] * 2)
    result = policy_grounded_voice.render({"latest_user_message": latest}, strict_settings(),
                                          frame=debt_frame(), fallback="Можем поговорить здесь.")
    assert len(calls) == len(analyses) == result.spoken.critic_calls == 2
