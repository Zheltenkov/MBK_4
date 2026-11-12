"""Independent recorded place-echo regression and useful-speech controls."""
import pytest

from utils import omit_empty_field_receipt_bubbles


RECEIPT = "Спасибо, Казань и Татарстан я зафиксировала."
USEFUL = "Если есть вопросы по вашей ситуации — спрашивайте, можем обсудить их здесь."
FIELD = frozenset({"client.region_class"})


def test_the_recorded_affirmative_region_answer_does_not_require_an_empty_place_echo():
    assert omit_empty_field_receipt_bubbles(RECEIPT + "\n\n" + USEFUL,
        received_fields=FIELD, user_message="Да, Татарстан.") == USEFUL


@pytest.mark.parametrize("message", [
    "Да, Татарстан. Вы правильно поняли?",
    "Да, Татарстан. Я очень переживаю.",
])
def test_confirmation_or_emotion_does_not_activate_the_plain_field_receipt_filter(message):
    candidate = RECEIPT + "\n\n" + USEFUL
    assert omit_empty_field_receipt_bubbles(candidate, received_fields=FIELD, user_message=message) == candidate


def test_a_meaningful_explanation_in_the_same_bubble_is_not_rewritten_as_a_receipt():
    candidate = "Казань и Татарстан я зафиксировала, от региона зависит порядок обращения."
    assert omit_empty_field_receipt_bubbles(candidate,
        received_fields=FIELD, user_message="Да, Татарстан.") == candidate
