"""Бот не обещает того, чего не делает.

Боевая переписка 08.09.2026: «Посмотрю, что у нас есть по вашей заявке» —
заявок бот не видит ни одной; «С менеджером свяжу» — не связывает. Правило
«не обещать невыполнимого» уже действует для одобрения и ставок; здесь оно
распространяется на заявки и менеджеров.
"""
from __future__ import annotations

import pytest

import prompts
from utils import (
    _PROMISE_FALLBACK,
    _PROMISE_FALLBACK_AFTER_FINISH,
    guard_bubbles,
)


@pytest.mark.parametrize(
    "bubble",
    [
        "Посмотрю, что у нас есть по вашей заявке.",
        "посмотрю вашу заявку прямо сейчас",
        "Сейчас проверю статус вашей заявки.",
        "С менеджером свяжу, но сначала пара вопросов.",
        "Соединю вас с менеджером.",
        "Передам менеджеру, он перезвонит вам сегодня.",
    ],
)
def test_a_promise_the_bot_cannot_keep_never_reaches_the_client(bubble):
    assert guard_bubbles([bubble, "Какая сумма вам нужна?"]) == [
        "Какая сумма вам нужна?"
    ]


@pytest.mark.parametrize(
    "bubble",
    [
        "Заявку оформим здесь, в чате.",
        "Ситуация понятная, не переживайте — разберёмся.",
        "Данных достаточно, могу переходить к подбору.",
    ],
)
def test_honest_phrases_are_not_touched(bubble):
    assert guard_bubbles([bubble]) == [bubble]


def test_the_prompt_forbids_promising_to_look_up_an_application():
    text = prompts.CONVERSATION_SYSTEM_PROMPT.lower()

    assert "заявк" in text
    assert "не обещай" in text or "не говори" in text


@pytest.mark.parametrize(
    "bubble",
    [
        "Отсюда я не вижу вашей заявки, но давайте продолжу подбор.",
        "Заявку отсюда не вижу, но помочь смогу — продолжим подбор.",
        "Её не видно отсюда, это делается потом. А сейчас давайте разберёмся.",
    ],
)
def test_honest_answer_about_application_passes_through_guard(bubble):
    # Честный ответ про то, что заявку не видно отсюда, должен пройти через гард
    # целиком, а не быть съеденным фильтром на "систем" или другим фильтром.
    result = guard_bubbles([bubble])
    assert result == [bubble]


# --------------------------------------------------------------------------- #
# Вырезали всё — что тогда слышит клиент
# --------------------------------------------------------------------------- #
# Ревью 09.09.2026: у фильтра обещаний не было своего запасного ответа, и
# клиент, спросивший про заявку, получал запасную фразу гарантийного
# фильтра — «Гарантировать одобрение или точную ставку заранее не могу». Тест
# выше этого не видел: второй пузырь с вопросом там переживает фильтр всегда.

_BOTH_FALLBACKS = [
    pytest.param(False, _PROMISE_FALLBACK, id="посреди-анкеты"),
    pytest.param(True, _PROMISE_FALLBACK_AFTER_FINISH, id="после-финала"),
]


@pytest.mark.parametrize(
    "bubble",
    [
        # Буквально то, что предписывает правило 1a промпта, — и всё равно
        # вырезается по подстроке «статус вашей заявки». Случай ревьюера.
        "Статус вашей заявки я отсюда не вижу — это делается уже после передачи.",
        "Посмотрю, что у нас есть по вашей заявке.",
        "С менеджером свяжу.",
    ],
)
@pytest.mark.parametrize("conversation_over, expected", _BOTH_FALLBACKS)
def test_an_application_question_gets_an_answer_about_the_application(
    bubble, conversation_over, expected
):
    result = guard_bubbles([bubble], conversation_over=conversation_over)

    assert result == list(expected)
    text = " ".join(result).lower()
    assert "одобрен" not in text and "ставк" not in text, (
        "человек спросил про заявку — ответ про ставку не по делу"
    )


@pytest.mark.parametrize("conversation_over, fallback", _BOTH_FALLBACKS)
def test_the_promise_fallback_is_honest_and_offers_something_real(
    conversation_over, fallback
):
    """Честно: заявку отсюда не видно. И сразу — что бот правда может."""
    text = " ".join(fallback).lower()

    assert "заявк" in text and "не вижу" in text
    if conversation_over:
        # После финала подбор уже закончен — предлагать его снова нельзя.
        assert "вопрос" in text
        assert "подбор" not in text
    else:
        assert "подбор" in text


@pytest.mark.parametrize("conversation_over, fallback", _BOTH_FALLBACKS)
def test_the_promise_fallback_survives_every_filter_itself(conversation_over, fallback):
    """Иначе замкнутый круг: запасная фраза сама вырезается фильтром.

    Проверяется самим `guard_bubbles` — всеми фильтрами разом, включая
    гарантии, ссылки, внутренние термины и маркеры маскировки. Новый маркер,
    случайно совпавший с этой фразой, уронит тест, а не ответ клиенту.
    """
    assert guard_bubbles(list(fallback), conversation_over=conversation_over) == list(
        fallback
    )
    assert guard_bubbles(list(fallback), conversation_over=not conversation_over) == list(
        fallback
    )


def test_a_cut_guarantee_still_gets_the_guarantee_answer():
    """Своя запасная фраза у обещаний не должна перехватить чужой случай."""
    result = guard_bubbles(["Мы гарантируем одобрение, не переживайте."])

    assert "Гарантировать одобрение" in result[0]


def test_a_guarantee_outweighs_a_promise_when_both_are_cut():
    """Обе фразы вырезаны — отвечаем про гарантию.

    Обещание одобрения опаснее: после него человек ждёт денег. Про заявку
    клиент при необходимости переспросит, и тогда ответ придёт свой.
    """
    result = guard_bubbles(
        ["Кредит вам точно одобрят.", "Проверю вашу заявку."]
    )

    assert "Гарантировать одобрение" in result[0]
