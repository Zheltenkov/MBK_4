"""Keep a model's complete safe entry reaction before checking its visible text."""
from dataclasses import replace

import pytest

import policy_grounded_voice
from tests.test_policy_grounded_voice import install, semantic_raw, strict_settings
from tests.test_policy_proven_debt_repair import debt_frame


@pytest.mark.parametrize("question", [
    "Общая сумма долга у нас 400 000 рублей — это верно?",
    "Подскажите, как к вам обращаться?",
    "Сейчас есть активные просрочки?",
])
def test_complete_entry_retains_its_safe_model_reaction_and_checks_the_whole_visible_text(monkeypatch, question):
    reaction = "Хорошо, давайте разбираться."
    generations, analyses = install(monkeypatch, [reaction + "\n\n" + question, "Можем поговорить здесь."],
                                    [semantic_raw()] * 2)
    result = policy_grounded_voice.render({"latest_user_message": "Разобраться с долгами"}, strict_settings(),
                                          frame=debt_frame(), fallback="Можем поговорить здесь.")
    assert result.spoken.text == reaction and result.spoken.source == "model"
    assert len(generations) == len(analyses) == result.spoken.critic_calls == 1
    assert analyses[0][0] == reaction and result.spoken.factual_assessments == ()


@pytest.mark.parametrize("latest,frame", [
    ("Что такое банкротство?", debt_frame()),
    ("Мои данные никуда не передавайте.", replace(debt_frame(), conversation_declined=True)),
    ("Разобраться с долгами", replace(debt_frame(), expected="client.phone", status="collecting_contacts",
                                    known_fields=debt_frame().known_fields - {"client.phone"})),
    ("Разобраться с долгами", replace(debt_frame(), offer_present=True)),
])
def test_other_tasks_keep_their_generation_and_validation_path(monkeypatch, latest, frame):
    bad = "Хорошо, давайте разбираться. Подскажите, как к вам обращаться?"
    generations, _ = install(monkeypatch, [bad, "Можем поговорить здесь."], [semantic_raw()] * 2)
    result = policy_grounded_voice.render({"latest_user_message": latest}, strict_settings(),
                                          frame=frame, fallback="Можем поговорить здесь.")
    assert len(generations) == 2 and result.spoken.text != "Хорошо, давайте разбираться."


@pytest.mark.parametrize("prefix", ["Передам вашу заявку партнёру.", "«Долг точно спишут»."])
def test_unavailable_claims_and_quotes_cannot_be_salvaged_into_authority(monkeypatch, prefix):
    generations, _ = install(monkeypatch, [prefix + " Подскажите, как к вам обращаться?", "Можем поговорить здесь."],
                               [semantic_raw()] * 2)
    result = policy_grounded_voice.render({"latest_user_message": "Разобраться с долгами"}, strict_settings(),
                                          frame=debt_frame(), fallback="Можем поговорить здесь.")
    assert len(generations) == 2 and result.spoken.text == "Можем поговорить здесь."
