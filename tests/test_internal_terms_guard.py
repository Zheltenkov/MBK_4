"""Внутренняя кухня не должна доезжать до клиента.

30.07.2026 в живом диалоге бот написал: «без номера телефона система не
сформирует решение». Промпт запрещает это слово в трёх местах, но на рантайме
запрет не проверялся вообще — `GLOBAL_FORBIDDEN` живёт только в эвале, то есть
ловит нарушение уже после того, как клиент его увидел.
"""
from __future__ import annotations

import pytest

from utils import guard_bubbles


def test_the_leak_from_production_is_cut():
    bubbles = guard_bubbles([
        "К сожалению, нет — без номера телефона система не сформирует решение.",
        "Уточните, пожалуйста, номер телефона для обязательной проверки.",
    ])

    assert not any("систем" in b.lower() for b in bubbles)
    assert "Уточните, пожалуйста, номер телефона для обязательной проверки." in bubbles


@pytest.mark.parametrize(
    "bubble",
    [
        "Смотрю по матрице маршрутов.",
        "У этого маршрута выше приоритет.",
        "Система подберёт варианты.",
        "Заполняю поле в json.",
    ],
)
def test_internal_terms_never_reach_the_client(bubble):
    assert guard_bubbles([bubble, "В каком городе живёте?"]) == [
        "В каком городе живёте?"
    ]


def test_reply_made_only_of_internal_terms_falls_back_to_something_sayable():
    """Вырезать всё и промолчать нельзя: клиент останется без ответа."""
    bubbles = guard_bubbles(["Система не сформирует решение без телефона."])

    assert bubbles
    assert not any("систем" in b.lower() for b in bubbles)


def test_ordinary_reply_is_untouched():
    original = ["Иван, какая сумма нужна?", "Это последний вопрос."]

    assert guard_bubbles(original) == original


def test_guarantee_guard_still_works():
    """Старая защита не должна пострадать от новой."""
    bubbles = guard_bubbles(["Мы гарантированно одобрим вам кредит."])

    assert not any("гарантированно одобр" in b.lower() for b in bubbles)


def test_backend_bubbles_never_mention_internal_terms():
    """Пузыри, которые backend дописывает сам, минуют guard_bubbles.

    Фильтр внутренних терминов стоит на выходе модели (core.py), а финальные
    сообщения о доставке добавляются после него. 10.08.2026 на тестовом лиде
    клиент из-за этого получил «Опрос завершён. Ниже показан итоговый JSON с
    собранными данными» — строку демо-режима, где слово JSON фильтр поймал бы
    сразу, будь оно на его стороне.
    """
    import prompts
    from utils import _INTERNAL_TERMS

    backend_bubbles = [
        *prompts.survey_completed_message(thanks_url=None),
        *prompts.survey_completed_message(thanks_url=None),
        *prompts.survey_completed_message(
            thanks_url="https://info.mbk.ru/spasibo-zaim-zalog-pts",
        ),
        *prompts.link_message("Василий", "https://example.com/offer"),
    ]

    for bubble in backend_bubbles:
        lowered = bubble.casefold()
        found = [term for term in _INTERNAL_TERMS if term in lowered]
        assert not found, f"{found} в пузыре: {bubble}"
