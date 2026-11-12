"""Модель не объявляет заявку отправленной, пока анкета не закончена.

С 01.10.2026 так потеряно шесть заявок, пять — с телефоном: модель вне плана
просила номер и писала «заявку передала», «подбор завершён», «пришлю ссылку»,
а план ещё ждал ответа анкеты, и лид не создавался. Четыре случая — на
вопросах о собственности и втором объекте, когда ответ клиента не разобрался.
"""
from __future__ import annotations

import pytest

import core
from routing import Plan, get_matrix
from routing.planner import PlannedField
from state import init_state


def _asking(path: str) -> tuple[dict, Plan]:
    spec = get_matrix().fields[path]
    plan = Plan(action="ask", fields=[PlannedField(path=path, spec=spec)])
    state = init_state()
    state["facts"] = {"client": {"name": "Евгений"}}
    state["entry"] = {"scenario": "questionnaire"}
    state["_plan_object"] = plan
    return state, plan


def test_a_premature_finish_becomes_the_question_of_the_plan():
    """Бой 19.10.2026, сессия e0837cd8a8f9: план ждал регион второй квартиры."""
    state, _ = _asking("assets.realty_2.region_class")

    committed = core.commit_turn(
        state,
        "Да",
        "Всё зафиксировала. Сейчас подберу подходящие варианты и пришлю ссылку — "
        "там сможете посмотреть детали.\n\nБудьте на связи!",
    )

    assert committed["messages"] == [
        "Остался ещё вопрос: в каком регионе и городе находится второй объект?"
    ]
    attempts = committed["current_state"]["script"]["attempts"]
    assert attempts["assets.realty_2.region_class"] == 1


def test_an_off_plan_phone_request_is_replaced_by_the_question():
    """Бой 16.10.2026, сессия 554bb65d6b2a: «Подбор завершён, передам данные»."""
    state, _ = _asking("assets.realty.owners")

    committed = core.commit_turn(
        state,
        "Нет",
        "Олег, поняла. Тогда по недвижимости всё ясно.\n\n"
        "Остался последний вопрос: на какой номер телефона вам можно перезвонить?",
    )

    assert committed["messages"] == [
        "Остался ещё вопрос: кто собственник — только вы, вы с супругом(ой) "
        "или есть ещё владельцы?"
    ]


def test_a_real_question_survives_when_only_the_promise_is_cut():
    state, _ = _asking("debts.total")

    committed = core.commit_turn(
        state,
        "Есть кредиты",
        "Заявку передала специалистам.\n\nКакая сейчас общая сумма долга?",
    )

    assert committed["messages"] == ["Какая сейчас общая сумма долга?"]


@pytest.mark.parametrize(
    "reply",
    [
        "Какая сейчас общая сумма долга по кредитам?",
        "Понимаю, ситуация непростая.\n\nКакая сейчас общая сумма долга?",
    ],
)
def test_an_ordinary_question_is_left_alone(reply):
    state, _ = _asking("debts.total")

    committed = core.commit_turn(state, "Есть кредиты", reply)

    assert committed["messages"] == reply.split("\n\n")


@pytest.mark.parametrize("action", ["ask", "contacts"])
def test_a_step_that_asks_for_the_phone_may_mention_it(action):
    spec = get_matrix().fields["client.phone"]
    plan = Plan(action=action, fields=[PlannedField(path="client.phone", spec=spec)])

    bubbles = ["Оставьте, пожалуйста, номер телефона."]

    assert core._hold_premature_finish(bubbles, plan, init_state()) == bubbles


def test_a_client_who_declined_is_not_pushed_back_into_the_questionnaire():
    state, plan = _asking("debts.total")
    state["script"] = {"declined": True}
    bubbles = ["Хорошо, на этом всё — буду на связи."]

    assert core._hold_premature_finish(bubbles, plan, state) == bubbles
