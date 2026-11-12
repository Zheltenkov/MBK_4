"""Счёт попыток и передача статусов из state в планировщик.

Планировщик чист: он получает попытки и статусы как аргументы. Считать их —
работа core, и именно здесь проверяется, что счётчик растёт только когда поле
действительно спрашивали повторно, а не на каждом ходе подряд.

Попытка — это вопрос, который клиент увидел, а не план хода. Ревью 09.09.2026:
счётчик рос в `prepare_turn`, то есть на каждом посчитанном плане, в том числе
на приветственном ходе, где контактная просьба не приклеивается, и на
прощании, где бот вопросов не задаёт. Первое съедало единственную попытку
спросить имя — его не спрашивали ни разу. Второе доводило до потолка поля,
которые никто не называл: три «Спасибо» подряд — и про сумму бот больше не
спросит. Поэтому ходы здесь проходят целиком, `prepare_turn` + `commit_turn`:
попытка засчитывается там, где решается, что вопрос прозвучал.
"""
from __future__ import annotations

import pytest

import core
import prompts
from routing.planner import MAX_FIELD_ATTEMPTS
from state import init_state


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("MBK_ROUTER_MOCK", "1")
    monkeypatch.setenv("MBK_FACT_INTERPRETATION_MODE", "legacy")
    monkeypatch.delenv("MBK_DADATA_API", raising=False)
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *a, **k: {"fact_updates": [], "internal_summary": ""},
    )


BASE = {
    "client": {"name": "Ирина", "age": 38, "region_class": "moscow_mo"},
    "request": {"amount": 300_000},
    "debts": {"has_active_credits": True, "total": 700_000},
    "employment": {"type": "unofficial", "income": 60_000},
    "assets": {"has_realty": False, "has_vehicle": False},
}
STUCK = "debts.overdue_duration"


def _attempts(state):
    return (state.get("script") or {}).get("attempts") or {}


def _asked(state):
    return (state.get("script") or {}).get("asked") or []


def _turn(state, message, reply="Понял вас. Уточните, пожалуйста?"):
    """Полный ход: план считает prepare_turn, прозвучавший вопрос — commit_turn.

    Ответ модели по умолчанию — с вопросом: попытка засчитывается только за
    прозвучавший вопрос (бой 20.10.2026, `core.commit_turn`).

    Возвращает и подготовленное состояние (в нём живой план хода), и
    зафиксированное — его и надо передавать в следующий ход.
    """
    prepared = core.prepare_turn(state, message)
    committed = core.commit_turn(prepared, message, reply)
    return prepared, committed


def _state_at(facts):
    state = init_state()
    state["session_id"] = "abc123"
    state["facts"] = dict(facts)
    return state


def test_repeated_question_increments_the_counter():
    state = _state_at(BASE)

    # «Затрудняюсь сказать» — уклончивый ответ без числа и единицы: ни быстрый
    # разбор, ни разбор срока словами (22.09.2026) его не поймут, а значит
    # поле остаётся незаполненным и попытка засчитывается снова.
    for expected in (1, 2):
        prepared, committed = _turn(state, "затрудняюсь сказать")
        state = committed["current_state"]
        assert prepared["_plan_object"].field_paths == [STUCK]
        assert _attempts(state).get(STUCK) == expected


def test_questionnaire_moves_on_after_the_limit():
    """Инцидент 29.07.2026: без этого курсор стоял на поле шесть ходов подряд."""
    state = _state_at(BASE)

    for _ in range(MAX_FIELD_ATTEMPTS):
        prepared, committed = _turn(state, "затрудняюсь сказать")
        state = committed["current_state"]
        assert prepared["_plan_object"].field_paths == [STUCK]

    prepared = core.prepare_turn(state, "затрудняюсь сказать")

    assert prepared["_plan_object"].action == "contacts"
    assert prepared["_plan_object"].field_paths == ["client.phone"]


def test_answered_field_does_not_accumulate_attempts():
    """Счётчик не должен наказывать нормальный ход анкеты."""
    state = _state_at(BASE)
    state["facts"]["debts"] = {**BASE["debts"], "overdue_duration": "over_6m"}

    _, committed = _turn(state, "больше полугода")

    assert _attempts(committed["current_state"]).get(STUCK) is None


def test_held_back_fact_reaches_the_planner_as_confirmation():
    state = _state_at(BASE)
    state["fact_statuses"] = {STUCK: "needs_confirmation"}

    state = core.prepare_turn(state, "около двух месяцев")

    assert state["_plan_object"].fields[0].needs_confirmation is True


def test_the_greeting_turn_does_not_spend_the_attempt_on_the_name():
    """На приветствии просьба об имени не звучит — значит, и попытки нет.

    Ревью 09.09.2026: план приветственного хода уже спрашивает имя, счётчик
    ставил ему единицу, а планировщик после первой попытки убирает имя из
    знакомства навсегда. Итог — имя не спрашивали ни разу, хотя спека
    обещала «спросили один раз — не получили — идём дальше».
    """
    state = init_state()
    state["session_id"] = "abc123"

    prepared, committed = _turn(state, "", prompts.OPENING_MESSAGE)
    assert prepared["_plan_object"].field_paths == ["client.name"], "предпосылка"
    assert committed["messages"] == [prompts.OPENING_MESSAGE], "предпосылка"
    greeted = committed["current_state"]

    assert _attempts(greeted).get("client.name") is None
    assert _asked(greeted) == [], (
        "бот ещё ничего не спросил — первая реплика клиента не должна "
        "разбираться как ответ на вопрос об имени"
    )

    prepared, committed = _turn(greeted, "Здравствуйте, нужен кредит", "Поняла.")
    assert prepared["_plan_object"].field_paths == ["client.name"], (
        "первая реплика клиента обязана получить вопрос об имени"
    )
    assert "как к вам обращаться" in " ".join(committed["messages"]).lower()
    assert _attempts(committed["current_state"]).get("client.name") == 1
    assert _asked(committed["current_state"]) == ["client.name"]


def test_a_farewell_does_not_spend_attempts_on_questions_nobody_asked():
    """Три «Спасибо» подряд не должны закрыть сумму и возраст.

    Ревью 09.09.2026: на прощании модель отвечает прощанием без вопроса, а
    счётчик всё равно рос. Три таких хода — и `request.amount` с
    `client.age` упирались в потолок: вернувшийся клиент про сумму уже не
    услышал бы, и в CRM сумма уехала бы пустой.
    """
    state = _state_at({"client": {"name": "Ирина"}})

    for _ in range(MAX_FIELD_ATTEMPTS):
        prepared, committed = _turn(state, "Спасибо", "Спасибо вам! Я на связи.")
        state = committed["current_state"]
        assert set(prepared["_plan_object"].field_paths) == {
            "request.amount",
            "client.age",
        }, "предпосылка"

    assert _attempts(state).get("request.amount") is None
    assert _attempts(state).get("client.age") is None
    replanned = core.prepare_turn(state, "Вернулась, давайте продолжим")
    assert "request.amount" in replanned["_plan_object"].field_paths


def test_a_farewell_keeps_the_memory_of_the_last_real_question():
    """Память о заданном вопросе тоже только про прозвучавшее.

    По ней быстрый разбор кладёт короткий ответ прямо в поле, минуя модель.
    Прощание вопроса не задаёт, значит, и переписывать память нечем: клиент,
    вернувшись, отвечает на тот вопрос, который видел последним.
    """
    state = _state_at(BASE)
    _, committed = _turn(state, "около двух месяцев")
    state = committed["current_state"]
    assert _asked(state) == [STUCK], "предпосылка"
    before = dict(_attempts(state))

    _, committed = _turn(state, "Спасибо, до свидания", "И вам всего доброго!")

    assert _asked(committed["current_state"]) == [STUCK]
    assert _attempts(committed["current_state"]) == before
