"""Имя и пол ассистента — настройка заказчика, а не константа кода.

Заказчик просил женское имя «Ника» и спросил, как менять пол. Пол по-русски —
это не одно слово в приветствии: о себе бот говорит глаголами прошедшего
времени, и женское имя при «понял» читается как сбой. Поэтому тесты проверяют
не только подпись, но и род во всех местах, где промпт говорит от первого лица.
"""
from __future__ import annotations

import importlib

import pytest

import prompts


@pytest.fixture
def reloaded(monkeypatch):
    def _load(name: str | None, gender: str | None):
        for key, value in (
            ("MBK_ASSISTANT_NAME", name),
            ("MBK_ASSISTANT_GENDER", gender),
        ):
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)
        return importlib.reload(prompts)

    yield _load
    # Модуль общий для всего прогона: возвращаем настройку по умолчанию.
    monkeypatch.undo()
    importlib.reload(prompts)


def test_default_assistant_is_nika_and_female(reloaded):
    module = reloaded(None, None)

    assert module.ASSISTANT_NAME == "Ника"
    assert module.ASSISTANT_GENDER == "female"
    assert "Я Ника, специалист МБК" in module.OPENING_MESSAGE


def test_female_persona_speaks_of_itself_in_the_feminine(reloaded):
    module = reloaded(None, None)

    assert "женском роде" in module.CONVERSATION_SYSTEM_PROMPT
    assert "«поняла»" in module.CONVERSATION_SYSTEM_PROMPT
    assert "«Поняла»" in module.CONVERSATION_FREEDOM
    assert "зафиксировала" in module.SURVEY_COMPLETED_INSTRUCTION


def test_gender_switches_every_self_reference(reloaded):
    module = reloaded("Олег", "male")

    assert module.ASSISTANT_GENDER == "male"
    assert "мужском роде" in module.CONVERSATION_SYSTEM_PROMPT
    assert "«понял»" in module.CONVERSATION_SYSTEM_PROMPT
    assert "«Понял»" in module.CONVERSATION_FREEDOM
    assert "зафиксировал/" in module.SURVEY_COMPLETED_INSTRUCTION
    # Женских форм не остаётся ни одной: смешанный род хуже любого из двух.
    assert "поняла" not in module.CONVERSATION_SYSTEM_PROMPT.lower()


def test_name_reaches_the_greeting_and_the_style_examples(reloaded):
    module = reloaded("Марина", None)

    assert module.OPENING_MESSAGE.startswith("Добрый день! Я Марина, специалист МБК")
    assert "\nМарина: " in module.STYLE_EXAMPLES
    assert "Ника" not in module.STYLE_EXAMPLES
    assert "{assistant}" not in module.STYLE_EXAMPLES
    assert "Ты — Марина, живой специалист МБК" in module.CONVERSATION_SYSTEM_PROMPT


def test_unknown_gender_falls_back_to_the_default(reloaded):
    module = reloaded(None, "чтототакое")

    assert module.ASSISTANT_GENDER == "female"


def test_blank_name_falls_back_to_the_default(reloaded):
    module = reloaded("   ", None)

    assert module.ASSISTANT_NAME == "Ника"


def test_the_greeting_never_declines_the_name(reloaded, matrix):
    """Родительный падеж по имени не выводится — в инструкции его и нет."""
    module = reloaded("Ника", None)
    from routing import plan

    instruction = module.turn_instruction(plan(matrix, {}))

    assert "приветствие уже было" in instruction
    assert "Ники" not in instruction
