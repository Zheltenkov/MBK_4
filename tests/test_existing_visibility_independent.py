"""A truthful limitation in one bubble cannot license another bubble's promise."""
import pytest

from utils import _BRANCH_FALLBACK, guard_bubbles


HONEST = "Статус вашей заявки я отсюда не вижу."
HELP = "Могу пояснить, что обычно происходит после обращения."


@pytest.mark.parametrize("unsafe", [
    "Статус вашей заявки уже одобрен.",
    "Сейчас проверю вашу заявку.",
    "Соединю вас с менеджером.",
    "Заявку отменю, номер удалю.",
    "Проверьте статус по https://example.invalid/status",
    "Кредит вам точно одобрят.",
])
def test_an_honest_neighbor_never_exempts_a_separate_unsafe_bubble(unsafe):
    assert guard_bubbles([HONEST, unsafe, HELP], scenario="existing") == [HONEST, HELP]


@pytest.mark.parametrize("mixed", [
    "Статус вашей заявки я отсюда не вижу. Статус вашей заявки уже одобрен.",
    "Статус вашей заявки я отсюда не вижу, но сейчас проверю вашу заявку.",
    "«Статус вашей заявки я не вижу» — так было раньше. Сейчас проверю вашу заявку.",
    "Не вижу никаких проблем: статус вашей заявки уже одобрен.",
])
def test_a_visibility_word_or_quote_cannot_hide_an_active_claim(mixed):
    # Discarding a false status answer must still leave a truthful answer about
    # the existing application, even when a generic helpful bubble survives.
    result = guard_bubbles([mixed, HELP], scenario="existing")
    assert result == [_BRANCH_FALLBACK["existing"][0], HELP]
    assert mixed not in result and "не вижу" in result[0]


def test_an_honest_existing_reply_keeps_its_meaning_when_another_safe_bubble_survives():
    original = [HONEST, HELP]
    assert guard_bubbles(original, scenario="existing") == original
    assert guard_bubbles(original, conversation_over=True, scenario="existing") == original
