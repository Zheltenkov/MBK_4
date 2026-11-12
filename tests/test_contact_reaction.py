"""Реакция на ходе запроса телефона не должна обрезаться до половины.

Перед просьбой оставить номер бот отвечает на сказанное клиентом, и backend
дописывает к реакции детерминированный запрос контакта. Из ответа модели при
этом бралось РОВНО ОДНО предложение — первое подходящее.

Момент самый чувствительный в анкете: человек решает, отдавать ли телефон, и
часто перед этим признаётся в долгах или просрочках. Сочувствие из двух фраз
теряло вторую — ту, ради которой говорилась первая:

    «Переживать тут не о чем. Ситуация обычная, разберёмся спокойно.»
        превращалось в «Переживать тут не о чем.»

Ограничение существует не зря: модель любит сама начать «посмотрю, что можно
подобрать», и без обрезки клиент читает это дважды подряд — свою фразу и нашу.
Поэтому режем не по счёту предложений, а по смыслу: выбрасываем вопросы и
заходы на контакт, оставляем до двух фраз участия.
"""
from __future__ import annotations

import pytest

from core import _safe_contact_reaction


def test_two_sentences_of_support_survive():
    reaction = _safe_contact_reaction(
        ["Переживать тут не о чем. Ситуация обычная, разберёмся спокойно."]
    )

    assert "Переживать тут не о чем" in reaction
    assert "разберёмся спокойно" in reaction, f"вторая фраза потеряна: {reaction!r}"


def test_support_split_across_bubbles_survives():
    reaction = _safe_contact_reaction(
        ["Спасибо за откровенность.", "Такое бывает чаще, чем кажется."]
    )

    assert "чаще, чем кажется" in reaction


@pytest.mark.parametrize(
    "reply, forbidden",
    [
        ("Понял вас. Посмотрю, что можно подобрать под вашу ситуацию.", "подобрать"),
        ("Ясно. Оставьте, пожалуйста, номер телефона.", "оставьте"),
        ("Хорошо. Пришлю ссылку, как только будет результат.", "ссылк"),
        ("Ситуация понятная. А сколько вам полных лет?", "?"),
    ],
)
def test_the_model_does_not_get_to_ask_for_contacts_itself(reply, forbidden):
    """Запрос контакта дописывает backend — модель повторять его не должна."""
    reaction = _safe_contact_reaction([reply])

    assert forbidden not in reaction.lower(), f"чужой заход на контакт: {reaction!r}"


def test_reaction_never_grows_beyond_two_sentences():
    reaction = _safe_contact_reaction(
        ["Раз. Два. Три. Четыре."]
    )

    assert reaction.count(".") <= 2, f"слишком длинно: {reaction!r}"


def test_nothing_useful_does_not_inject_a_stock_reaction_before_the_question():
    assert _safe_contact_reaction(["А сколько вам лет?"]) == ""


@pytest.mark.parametrize(
    "bubbles",
    [
        ["Понял вас."],
        ["Поняла вас!"],
        ["Хорошо."],
        ["Понял вас.", "Сколько вам лет?"],
    ],
)
def test_ritual_acknowledgement_does_not_shrink_to_a_bare_dot(bubbles):
    """«Понял вас.» после среза ритуала становилось «.», и клиент читал
    «. Прежде чем продолжим, подскажите…» (ревью 09.09.2026)."""
    assert _safe_contact_reaction(bubbles) == ""


@pytest.mark.parametrize("reaction", [
    "Поняла, что вам сейчас тревожно.",
    "Хорошо понимаю, почему вы волнуетесь.",
    "Поняла вас, можем обсудить это спокойно.",
])
def test_meaningful_acknowledgements_are_not_rewritten_by_word(reaction):
    assert _safe_contact_reaction([reaction]) == reaction


def test_ritual_is_dropped_but_the_next_sentence_survives_capitalised():
    reaction = _safe_contact_reaction(
        ["Поняла вас.", "ситуация обычная, разберёмся."]
    )

    assert reaction == "Ситуация обычная, разберёмся."


@pytest.mark.parametrize(
    "reply",
    [
        "Данные собрала. Без него дальше двигаться не получится — напишите, пожалуйста.",
        "Николай, данные собрала. Без него дальше не продвинуться. Напишите, пожалуйста, ваш номер.",
        "По вашей ситуации варианты есть. Без него решение получить не получится — напишите, пожалуйста, номер.",
        "Данные собрала. Напишите, пожалуйста.",
    ],
)
def test_a_request_with_a_dangling_pronoun_is_cut(reply):
    """Мутационный прогон 04.10.2026: «Без него дальше двигаться не получится —
    напишите, пожалуйста. Уточните, пожалуйста, номер телефона…» — «него»
    ни к чему не относится, а просьба звучит дважды."""
    reaction = _safe_contact_reaction([reply]).lower()

    assert "без него" not in reaction
    assert "напишите" not in reaction
    assert "собрала" in reaction or "варианты есть" in reaction
