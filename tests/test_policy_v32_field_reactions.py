"""Short reactions stay free and accepted brief style does not spend a second pair."""
import pytest

from tests.test_policy_v31_receipts import actual_turn, offline  # noqa: F401


@pytest.mark.parametrize("semantic", [False, True])
@pytest.mark.parametrize("user,service", [
    ("Поняла. Я в Казани.", "Хорошо, Казань."),
    ("Да, Татарстан.", "Спасибо за уточнение."),
    ("Да, Татарстан.", "Угу, Татарстан."),
    ("Да, Татарстан.", "Благодарю за информацию."),
    ("Да, Татарстан.", "Понятно, регион."),
])
def test_a_field_echo_or_gratitude_for_intake_can_be_repaired_as_a_free_short_nod(monkeypatch, semantic, user, service):
    natural = "Понимаю."
    answer, spoken, _, calls, _ = actual_turn(monkeypatch, user, [service, natural], semantic=semantic)
    expected = {"Хорошо, Казань.": "Хорошо.", "Угу, Татарстан.": "Угу.",
                "Понятно, регион.": "Понятно."}.get(service, service)
    assert answer == expected and len(calls) == spoken.critic_calls == 1
    assert spoken.source == "model" and spoken.asked_field is None
    style = ("unrequested_receipt",) if service.startswith(("Спасибо", "Благодарю")) else ()
    assert spoken.diagnostic_assessments[0].code_style_issues == style
    assert all(item.verdict.accept for item in spoken.critic_assessments)


@pytest.mark.parametrize("semantic", [False, True])
@pytest.mark.parametrize("natural", [
    "Угу.", "Понимаю.", "Да.", "Хорошо.", "Спасибо.", "Да, понимаю.",
    "Спасибо за доверие.", "Благодарю за поддержку.", "Да, понимаю, это неприятно.",
])
def test_a_general_short_reaction_or_meaningful_support_has_no_required_wording(monkeypatch, semantic, natural):
    answer, spoken, _, calls, _ = actual_turn(monkeypatch, "Да, Татарстан.", [natural] * 2, semantic=semantic)
    assert answer == natural and len(calls) == 1
    assert not any(item.code_style_issues for item in spoken.diagnostic_assessments)


@pytest.mark.parametrize("semantic", [False, True])
@pytest.mark.parametrize("user,reply", [
    ("Вы правильно поняли, я в Казани?", "Хорошо, Казань."),
    ("Мне страшно, я не знаю, что делать.", "Спасибо за уточнение."),
    ("За что вы благодарите?", "Спасибо за информацию."),
])
def test_questions_emotion_and_requested_confirmation_do_not_become_a_field_role(monkeypatch, semantic, user, reply):
    answer, spoken, _, calls, _ = actual_turn(monkeypatch, user, [reply] * 2, semantic=semantic)
    assert answer == reply and len(calls) == 1
    assert not any(item.code_style_issues for item in spoken.diagnostic_assessments)


@pytest.mark.parametrize("semantic", [False, True])
def test_a_failed_optional_repair_preserves_the_first_service_act_instead_of_forcing_text_or_silence(monkeypatch, semantic):
    first = "Спасибо за уточнение."
    answer, spoken, _, calls, _ = actual_turn(monkeypatch, "Да, Татарстан.", [first, first], semantic=semantic)
    assert answer == first and len(calls) == spoken.critic_calls == 1 and spoken.source == "model"


def test_a_field_echo_beside_meaningful_text_keeps_the_model_body(monkeypatch):
    useful = "Условия реструктуризации обсуждают со своим кредитором."
    answer, spoken, _, calls, _ = actual_turn(monkeypatch, "Да, Татарстан.", ["Угу, Татарстан.\n\n" + useful] * 2)
    assert answer == useful and len(calls) == 1
    assert not any(item.code_style_issues for item in spoken.diagnostic_assessments)
