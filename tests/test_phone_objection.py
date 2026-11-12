"""«Зачем вам мой номер?» — возражение, а не повод повторить ту же фразу.

Бой 19–28.09.2026: около 13% законченных анкет обрывались на просьбе о
телефоне. На «зачем номер?», «какой проверки?» бот отвечал теми же словами
— «уточните номер для обязательной проверки» — до трёх раз подряд, и клиент
читал это как давление («вы людей обманываете»).

Ответ бизнеса 30.09.2026: чтобы получить решение, нужен телефон — без него
решение не показывается. Вопрос о том, что будет дальше, заданный до номера, —
тоже возражение. Отсюда: повторная просьба называет причину и звучит иначе.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import core
import prompts
from utils import guard_bubbles


def _plan(found: bool) -> SimpleNamespace:
    return SimpleNamespace(
        action="contacts",
        field_paths=["client.phone"],
        matched=object() if found else None,
    )


def _request(found: bool, attempt: int) -> str:
    bubbles = core._merge_contact_request(
        ["Понимаю вас."],
        _plan(found),
        user_message="а зачем вам мой номер?",
        phone_attempts=attempt,
    )
    assert len(bubbles) == 1
    return bubbles[0].removeprefix("Понимаю вас.").strip()


@pytest.mark.parametrize("found", [True, False])
def test_every_phone_request_is_worded_differently(found):
    requests = [_request(found, attempt) for attempt in (1, 2, 3)]

    assert len(set(requests)) == 3


def test_the_first_request_names_the_application_without_mandatory_verification():
    for found in (True, False):
        text = _request(found, 1).casefold()
        assert "номер телефона" in text and "заявк" in text
        assert "обязательн" not in text


@pytest.mark.parametrize("found", [True, False])
def test_the_second_request_says_why_the_number_is_needed(found):
    text = _request(found, 2).lower()

    assert "заявк" in text
    assert "без номера" not in text
    assert "обязательной проверки" not in text


@pytest.mark.parametrize("found", [True, False])
@pytest.mark.parametrize("attempt", [1, 2, 3])
def test_no_request_is_cut_by_the_runtime_guards(found, attempt):
    text = _request(found, attempt)

    assert guard_bubbles([text]) == [text]


@pytest.mark.parametrize("found", [True, False])
def test_the_last_request_does_not_press(found):
    text = _request(found, 3).lower()

    assert "если" in text, "третья просьба оставляет выбор за человеком"


def test_objection_rule_reaches_the_model_on_the_contact_step():
    instruction = prompts.turn_instruction(
        SimpleNamespace(action="contacts", fields=[SimpleNamespace(path="client.phone")], matched=None)
    )

    assert prompts.PHONE_OBJECTION_RULE in instruction
    rule = prompts.PHONE_OBJECTION_RULE.lower()
    assert "возражение" in rule
    assert "зачем" in rule


def test_model_echo_of_the_reason_is_not_said_twice():
    """Живая проба 01.10.2026: «Без номера не получится передать заявку. Без
    номера решение получить не выйдет…» — причина прозвучала дважды подряд."""
    bubbles = core._merge_contact_request(
        ["Проверки данных для подбора. Без номера не получится передать заявку на проверку."],
        _plan(True),
        user_message="какой проверки?",
        phone_attempts=2,
    )

    assert len(bubbles) == 1
    assert "без номера" not in bubbles[0].casefold()
    assert bubbles[0].startswith("Проверки данных"), "ответ на сам вопрос клиента остаётся"
