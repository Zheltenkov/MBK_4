"""Whole recording diagnosis stays intact while brief speech retains one checked pair."""
import pytest

from utils import omit_empty_field_receipt_bubbles
from tests.test_policy_v31_receipts import actual_turn, offline  # noqa: F401


def normalize(candidate, *, user="Да, Татарстан."):
    return omit_empty_field_receipt_bubbles(
        candidate, received_fields=frozenset(), user_message=user, known_region=True,
    )


@pytest.mark.parametrize("candidate", [
    "Спасибо, зафиксировала.", "Записал.", "Записала.", "Я записал.",
    "Зафиксировал.", "Отметила.", "Отметил.", "Спасибо, я уже зафиксировала.",
])
def test_a_whole_recording_act_without_an_object_is_still_only_a_field_receipt(candidate):
    assert normalize(candidate) == ""


@pytest.mark.parametrize("candidate", [
    "Поняла.", "Приняла.", "Вижу.", "Ясно.", "Спасибо.",
    "Спасибо, зафиксировала, что вы хотите обсудить условия.",
    "Зафиксировала вашу жалобу.", "Не зафиксировала.", "Вы записали?",
    "Зафиксировала?", "«Зафиксировала.»", "Она сказала: «Записала.»",
    "Спасибо, зафиксировала. Понимаю, что вам тревожно.",
])
def test_free_reactions_or_meaningful_negated_quoted_questioned_content_stays_exact(candidate):
    assert normalize(candidate) == candidate


@pytest.mark.parametrize("user", ["Мне тревожно.", "Вы записали мой регион?", "Нет, не Татарстан."])
def test_recording_words_without_a_plain_field_response_do_not_create_their_own_proof(user):
    candidate = "Спасибо, зафиксировала."
    assert normalize(candidate, user=user) == candidate


@pytest.mark.parametrize("semantic", [False, True])
def test_both_voice_entries_soft_edit_the_actual_phrase_once_without_forcing_output(monkeypatch, semantic):
    natural = "Поняла."
    answer, spoken, state, calls, _ = actual_turn(
        monkeypatch, "Да, Татарстан.", ["Спасибо, зафиксировала.", natural], semantic=semantic,
    )
    assert answer == "Спасибо." and spoken.source == "model" and spoken.asked_field is None
    assert len(calls) == spoken.critic_calls == 1
    assert spoken.diagnostic_assessments[0].code_style_issues == ()
    assert state["facts"]["client"]["phone"] == "+70000000001" and not state.get("delivery")


@pytest.mark.parametrize("semantic", [False, True])
def test_an_unsuccessful_soft_edit_preserves_safe_model_speech_without_silence_or_more_calls(monkeypatch, semantic):
    first = "Спасибо, зафиксировала."
    answer, spoken, _, calls, _ = actual_turn(
        monkeypatch, "Да, Татарстан.", [first, first], semantic=semantic,
    )
    assert answer == "Спасибо." and spoken.source == "model" and len(calls) == spoken.critic_calls == 1
