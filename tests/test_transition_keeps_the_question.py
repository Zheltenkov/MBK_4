"""Переходная фраза не должна съедать вопрос.

Ролевой прогон 19.08.2026, многословная пенсионерка: клиент дважды спросил «а
зачем это нужно?» и дважды не получил ответа — связность 70 при пороге 90. На
одном из ходов бот выдал вообще одно утверждение без вопроса.

Причина в бюджете пузырей. Переход занимает один из трёх, и от реплики модели
брались ПЕРВЫЕ два: `[transition, *bubbles[:2]]`. Модель отвечает в порядке
«реакция → пояснение → вопрос» (так её учит STYLE_EXAMPLES), поэтому под нож
попадал именно вопрос — самое ценное в ходе анкеты.

Оставшийся переход «теперь про активы» должен сохранять последний вопрос.
"""
from __future__ import annotations

import core
from routing.planner import Plan
from state import init_state


def _state() -> dict:
    state = init_state()
    state["facts"] = {"client": {"name": "Валентина"}}
    return state


def _plan(transition: str = "Валентина, теперь про активы — они заметно расширяют возможности.") -> Plan:
    return Plan(action="ask", transition=transition, step=7)


def test_question_survives_the_transition():
    state = _state()
    state["_plan_object"] = _plan()

    committed = core.commit_turn(
        state,
        "Здравствуйте, мне бы кредит",
        "Переживать тут не о чем, ситуация обычная.\n\n"
        "От этого зависит, что я смогу для вас найти.\n\n"
        "Какая сумма вам нужна?",
    )

    assert any("?" in bubble for bubble in committed["messages"]), (
        "вопрос выпал вместе с бюджетом пузырей: " + " | ".join(committed["messages"])
    )
    assert committed["messages"][-1] == "Какая сумма вам нужна?", (
        "вопрос должен остаться последним, чтобы клиент видел его в конце"
    )


def test_transition_still_goes_first():
    state = _state()
    state["_plan_object"] = _plan()

    committed = core.commit_turn(state, "здравствуйте", "Понял вас.\n\nСколько нужно?")

    assert committed["messages"][0].startswith("Валентина, теперь про активы")


def test_bubble_budget_is_respected():
    """Больше трёх сообщений подряд клиент видеть не должен."""
    state = _state()
    state["_plan_object"] = _plan()

    committed = core.commit_turn(
        state,
        "здравствуйте",
        "Раз.\n\nДва.\n\nТри.\n\nЧетыре?",
    )

    assert len(committed["messages"]) <= 3


def test_reply_without_a_question_is_not_mangled():
    state = _state()
    state["_plan_object"] = _plan()

    committed = core.commit_turn(state, "здравствуйте", "Понял вас.\n\nСейчас посмотрим.")

    # Вопроса нет — нет и переходной фразы: она вводит вопрос анкеты, а
    # перед ответом на реплику клиента звучит невпопад (бой 20.10.2026:
    # «Теперь про активы…» посреди разговора о списаниях с карты).
    assert committed["messages"] == ["Понял вас.", "Сейчас посмотрим."]
