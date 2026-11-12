"""Сквозной прогон диалога без сети.

Модель заменена сценарием: извлекатель возвращает заранее заданные факты, разговорный
слой — заглушку. Проверяется склейка ядра: факты → движок → план → инструкция → ссылка.
"""
from __future__ import annotations

import pytest

import core
import prompts
from prompts import OPENING_MESSAGE
from config import AppConfig
from routing import Plan
from state import init_state


CONFIG = AppConfig(openrouter_api_key="sk-or-test")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Ни один тест в этом файле не должен ходить в сеть."""
    monkeypatch.setattr(core.llm_agent, "extract_facts", _unscripted)
    monkeypatch.setenv("MBK_ROUTER_MOCK", "1")


def _unscripted(*args, **kwargs):
    raise AssertionError("извлекатель вызван без сценария")


def script(monkeypatch, updates: list[dict]):
    """Подменяет извлекатель на заранее заданный набор фактов."""
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *a, **k: {"fact_updates": updates, "internal_summary": ""},
    )


def turn(state, message, monkeypatch, updates, reply="Понял вас.\n\nЕщё пара вопросов?"):
    script(monkeypatch, updates)
    prepared = core.prepare_turn(state, message, CONFIG)
    committed = core.commit_turn(prepared, message, reply)
    return committed["current_state"], committed["messages"], prepared


# --------------------------------------------------------------------------- #
def test_opening_is_exact_and_does_not_call_model(monkeypatch):
    monkeypatch.setattr(core.llm_agent, "_stream", _unscripted)

    state = core.prepare_turn(init_state(), "", CONFIG)
    chunks = list(core.stream_opening(state, CONFIG))
    committed = core.commit_turn(state, "", "".join(chunks))

    assert chunks == [OPENING_MESSAGE]
    assert committed["messages"] == chunks


def test_new_credit_refusal_does_not_open_alternative_families(monkeypatch):
    state = init_state()
    script(
        monkeypatch,
        [
            {
                "path": "client.goal",
                "value": "debt_relief",
                "confidence": "high",
            },
            {
                "path": "preferences.explore_alternatives",
                "value": True,
                "confidence": "high",
            },
        ],
    )

    prepared = core.prepare_turn(
        state,
        "У меня долги, новый кредит точно не хочу",
        CONFIG,
    )

    assert prepared["facts"]["client"]["goal"] == "debt_relief"
    assert (
        prepared["facts"].get("preferences", {}).get("explore_alternatives")
        is None
    )


def test_explicit_flat_is_extracted_locally_when_model_misses_it(monkeypatch):
    state = init_state()
    state["facts"] = {"assets": {"has_realty": True}}
    script(monkeypatch, [])

    prepared = core.prepare_turn(
        state,
        "Квартира есть, она в Москве и находится в частной собственности",
        CONFIG,
    )

    assert prepared["facts"]["assets"]["realty"]["kind"] == "flat"


def test_first_reply_contains_contact_request_in_same_bubble(monkeypatch):
    state = init_state()
    script(monkeypatch, [
        {"path": "client.goal", "value": "cash", "confidence": "high"},
        {"path": "request.amount", "value": 800_000, "confidence": "high"},
    ])

    prepared = core.prepare_turn(state, "Мне нужно 800 тысяч", CONFIG)
    committed = core.commit_turn(
        prepared,
        "Мне нужно 800 тысяч",
        "Понял, давайте разберёмся.",
    )

    assert prepared["plan"]["fields"] == ["client.name"]
    assert committed["messages"] == [
        "Понял, давайте разберёмся. Подскажите, как к вам обращаться?"
    ]


@pytest.mark.parametrize(
    "reply",
    [
        "Здравствуйте, давайте разберёмся.",
        "Добрый день! Давайте посмотрим, какие есть варианты.",
        "Приветствую. Понял вас, разберёмся.",
    ],
)
def test_first_reply_never_repeats_the_opening_greeting(monkeypatch, reply):
    state = init_state()
    state["chat_history"] = [
        {
            "role": "assistant",
            "content": prompts.OPENING_MESSAGE,
        }
    ]
    script(
        monkeypatch,
        [{"path": "client.goal", "value": "cash", "confidence": "high"}],
    )

    prepared = core.prepare_turn(state, "Хочу взять кредит", CONFIG)
    committed = core.commit_turn(prepared, "Хочу взять кредит", reply)

    assert committed["current_state"]["chat_history"][0]["content"] == prompts.OPENING_MESSAGE
    response = committed["messages"][0].lower()
    assert not response.startswith(("здрав", "добрый день", "привет"))
    assert "как к вам обращаться" in response


def test_neighboring_assistant_replies_do_not_both_start_with_client_name():
    state = init_state()
    state["facts"] = {"client": {"name": "Василий"}}
    state["chat_history"] = [
        {"role": "assistant", "content": "Василий, сколько вам лет?"}
    ]

    committed = core.commit_turn(
        state,
        "Мне 42",
        "Василий, а в каком городе вы живёте?",
    )

    assert committed["messages"] == ["А в каком городе вы живёте?"]


def test_name_repetition_is_detected_across_all_bubbles_of_previous_turn():
    state = init_state()
    state["facts"] = {"client": {"name": "Василий"}}
    state["chat_history"] = [
        {"role": "user", "content": "Мне нужен кредит"},
        {"role": "assistant", "content": "Василий, сколько вам лет?"},
        {"role": "assistant", "content": "От возраста зависит набор вариантов."},
    ]

    committed = core.commit_turn(
        state,
        "Мне 42",
        "Василий, а в каком городе вы живёте?",
    )

    assert committed["messages"] == ["А в каком городе вы живёте?"]


def test_model_contact_question_is_removed_before_backend_adds_its_own(monkeypatch):
    state = init_state()
    script(monkeypatch, [
        {"path": "client.goal", "value": "cash", "confidence": "high"},
        {"path": "request.amount", "value": 800_000, "confidence": "high"},
    ])
    prepared = core.prepare_turn(state, "Мне нужно 800 тысяч", CONFIG)

    committed = core.commit_turn(
        prepared,
        "Мне нужно 800 тысяч",
        (
            "Понял, с суммой разобрались. Прежде чем продолжим, подскажите, "
            "как к вам обращаться и какой номер телефона оставить для связи?"
        ),
    )

    response = committed["messages"][0]
    assert len(committed["messages"]) == 1
    assert "Прежде чем продолжим" not in response
    assert response.count("Подскажите, как к вам обращаться?") == 1
    # Телефон на этом ходу не нужен — только имя. Backend подменяет реплику модели
    # своей детерминированной версией, а не оставляет её самовольный вопрос про телефон.
    assert "номер телефона" not in response
    assert "как к вам обращаться" in response


def test_mine_does_not_prove_private_or_sole_ownership(monkeypatch):
    state = init_state()
    state["facts"] = {
        "client": {
            "name": "Василий",
            "phone": "+79584563214",
            "goal": "cash",
            "age": 42,
            "region_class": "moscow_mo",
        },
        "request": {"amount": 800_000},
    }
    updates = [
        {"path": "assets.has_realty", "value": True, "confidence": "high"},
        {"path": "assets.realty.kind", "value": "flat", "confidence": "high"},
        {
            "path": "assets.realty.ownership_form",
            "value": "private",
            "confidence": "high",
        },
        {
            "path": "assets.realty.owners",
            "value": "client_only",
            "confidence": "high",
        },
        {
            "path": "assets.realty.restriction",
            "value": "none",
            "confidence": "high",
        },
    ]

    state, _, prepared = turn(
        monkeypatch=monkeypatch,
        state=state,
        message="Да, квартира в Москве. Моя и без обременений",
        updates=updates,
    )

    realty = state["facts"]["assets"]["realty"]
    assert realty.get("ownership_form") is None
    assert realty.get("owners") is None
    assert realty["restriction"] == "none"
    assert prepared["plan"]["matched"] is None


def test_facts_are_normalized_on_the_way_in(monkeypatch):
    state = init_state()
    state, _, _ = turn(monkeypatch=monkeypatch, state=state, message="нужно 1,5 млн", updates=[
        {"path": "request.amount", "value": "1,5 млн", "confidence": "high"},
    ])
    assert state["facts"]["request"]["amount"] == 1_500_000


def test_low_confidence_facts_are_dropped(monkeypatch):
    """«Тысяч триста-четыреста» не должно стать фактом — иначе клиент уедет не туда."""
    state = init_state()
    state, _, _ = turn(monkeypatch=monkeypatch, state=state, message="тысяч 300-400", updates=[
        {"path": "debts.total", "value": 350000, "confidence": "low"},
    ])
    assert "debts" not in state["facts"]


def test_local_phone_cannot_be_overwritten_by_mask_marker(monkeypatch):
    state = init_state()
    state, _, _ = turn(
        monkeypatch=monkeypatch,
        state=state,
        message="мой номер +7 999 123-45-67",
        updates=[
            {"path": "client.phone", "value": "[телефон]", "confidence": "high"},
        ],
    )
    assert state["facts"]["client"]["phone"] == "+79991234567"


def test_invented_field_is_ignored(monkeypatch):
    """Модель придумала путь, которого нет в матрице, — факт не записывается."""
    state = init_state()
    state, _, _ = turn(monkeypatch=monkeypatch, state=state, message="у меня трое детей", updates=[
        {"path": "household.children", "value": 3, "confidence": "high"},
    ])
    assert "household" not in state["facts"]


def test_instruction_follows_the_engine(monkeypatch):
    state = init_state()
    state, _, prepared = turn(monkeypatch=monkeypatch, state=state, message="нужно 500 тысяч", updates=[
        {"path": "client.name", "value": "Василий", "confidence": "high"},
        {"path": "client.phone", "value": "+79990000000", "confidence": "high"},
        {"path": "client.goal", "value": "cash", "confidence": "high"},
        {"path": "request.amount", "value": 500000, "confidence": "high"},
    ])
    instruction = core.turn_instruction(prepared)

    assert prepared["plan"]["action"] == "ask"
    # Сумма уже известна — про неё бот больше не спрашивает.
    assert "какая сумма нужна" not in instruction
    assert "сколько лет клиенту" in instruction


def test_ambiguous_city_instruction_requires_region_confirmation():
    state = init_state()
    state["facts"] = {
        "request": {"amount": 500_000},
        "client": {
            "goal": "cash",
            "age": 40,
            "name": "Василий",
            "phone": "+79990000000",
            "city": "Советск",
            "location": {
                "status": "ambiguous",
                "query": "Советск",
                "confirmation_required": True,
                "candidates": [
                    {"display_name": "Калининградская обл, г Советск"},
                    {"display_name": "Кировская обл, г Советск"},
                ],
            },
        },
    }

    prepared = core.prepare_turn(state, "", CONFIG)
    instruction = core.turn_instruction(prepared)

    assert "одноимённые населённые пункты" in instruction
    assert "Калининградская обл, г Советск" in instruction
    assert "назвать нужный регион" in instruction
    assert "DaData" not in instruction
    assert "для поля" not in instruction
    assert "не упоминай базу" in instruction.lower()


def test_dead_end_keeps_the_phone_question_visible():
    """Страж терминальных реплик вырезает вопросы — но в тупике телефон спросить нужно."""
    from routing import plan as build_plan
    from routing import get_matrix

    facts = {
        "request": {"amount": 100_000},
        "client": {"name": "Иван", "goal": "cash", "age": 70, "region_class": "rf_other"},
        "assets": {"has_realty": False, "has_vehicle": False},
        "debts": {"has_active_credits": False},
        "employment": {"type": "unemployed"},
    }
    current_plan = build_plan(get_matrix(), facts)
    # Анкета пройдена, маршрут не найден — телефон всё равно просим, но мягко.
    assert current_plan.action == "contacts"
    assert current_plan.field_paths == ["client.phone"]
    assert current_plan.matched is None

    state = init_state()
    state["facts"] = facts
    state["_plan_object"] = current_plan

    committed = core.commit_turn(
        state,
        "Понятно",
        "К сожалению, сейчас подходящего решения нет.\n\nОставите номер телефона?",
    )
    joined = " ".join(committed["messages"])

    assert "телефон" in joined.lower()
    # Решения нет — значит и ссылку обещать нельзя.
    assert "пришлю ссылку" not in joined
    assert "заявк" in joined.casefold()
    assert "обязательн" not in joined.casefold()


def test_hopeless_ambiguity_hands_off_to_an_operator_instead_of_asking_forever():
    """Предел попыток: после трёх неудачных уточнений бот перестаёт спрашивать.

    Решение заказчика 27.07.2026 — передача оператору (пока заглушка). Гадать
    регион нельзя: он меняет маршрут.
    """
    state = init_state()
    state["facts"] = {
        "request": {"amount": 500_000},
        "client": {
            "goal": "cash",
            "age": 40,
            "name": "Василий",
            "phone": "+79990000000",
            "city": "Советск",
            "location": {
                "status": "ambiguous",
                "query": "Советск",
                "confirmation_required": True,
                "attempts": 3,
                "handoff_required": True,
                "candidates": [
                    {"display_name": "Калининградская обл, г Советск"},
                    {"display_name": "Кировская обл, г Советск"},
                ],
            },
        },
    }

    prepared = core.prepare_turn(state, "", CONFIG)
    instruction = core.turn_instruction(prepared)

    assert "специалист" in instruction.lower()
    assert "не спрашивай" in instruction.lower()
    # Оператор — не обещание одобрения и не выдача продукта.
    assert "не обещай" in instruction.lower()
    # Анкета при этом не встаёт: следующий вопрос задаётся в том же сообщении.
    assert "следующий вопрос анкеты" in instruction.lower()
    assert prepared["plan"]["fields"] == ["debts.has_active_credits"]


def test_repeated_ambiguity_asks_differently_instead_of_the_same_text():
    """Повтор дословно того же вопроса — это и есть ощущение «бот запетлял».

    На втором заходе инструкция обязана звучать иначе и дать клиенту простой
    способ ответить, а не зачитывать тот же список ещё раз.
    """
    state = init_state()
    state["facts"] = {
        "request": {"amount": 500_000},
        "client": {
            "goal": "cash",
            "age": 40,
            "name": "Василий",
            "phone": "+79990000000",
            "city": "Советск",
            "location": {
                "status": "ambiguous",
                "query": "Советск",
                "confirmation_required": True,
                "attempts": 1,
                "candidates": [
                    {"display_name": "Калининградская обл, г Советск"},
                    {"display_name": "Кировская обл, г Советск"},
                ],
            },
        },
    }

    prepared = core.prepare_turn(state, "", CONFIG)
    instruction = core.turn_instruction(prepared)

    assert "не повторяй прошлую формулировку" in instruction.lower()
    assert "первый" in instruction.lower()
    assert "не упоминай базу" in instruction.lower()


def test_full_path_to_completed_json(monkeypatch):
    """В mock-режиме клиент завершает опрос и получает итоговый JSON без фиктивной ссылки."""
    state = init_state()

    state, _, _ = turn(monkeypatch=monkeypatch, state=state, message="Василий, +79990000000", updates=[
        {"path": "client.name", "value": "Василий", "confidence": "high"},
        {"path": "client.phone", "value": "+79990000000", "confidence": "high"},
    ])
    state, _, _ = turn(monkeypatch=monkeypatch, state=state, message="нужно 800 тысяч", updates=[
        {"path": "client.goal", "value": "cash", "confidence": "high"},
        {"path": "request.amount", "value": 800000, "confidence": "high"},
    ])
    state, _, _ = turn(monkeypatch=monkeypatch, state=state, message="42 года, Москва", updates=[
        {"path": "client.age", "value": 42, "confidence": "high"},
        {"path": "client.region_class", "value": "moscow_mo", "confidence": "high"},
    ])
    state, _, _ = turn(monkeypatch=monkeypatch, state=state, message="кредитов нет", updates=[
        {"path": "debts.has_active_credits", "value": False, "confidence": "high"},
    ])
    state, _, _ = turn(monkeypatch=monkeypatch, state=state, message="работаю официально, 150 тысяч", updates=[
        {"path": "employment.type", "value": "official", "confidence": "high"},
        {"path": "employment.income", "value": 150000, "confidence": "high"},
    ])
    state, _, _ = turn(monkeypatch=monkeypatch, state=state, message="есть квартира в Москве", updates=[
        {"path": "assets.has_realty", "value": True, "confidence": "high"},
        {"path": "assets.realty.region_class", "value": "moscow_mo", "confidence": "high"},
        {"path": "assets.realty.kind", "value": "flat", "confidence": "high"},
    ])
    state, _, _ = turn(monkeypatch=monkeypatch, state=state, message="в частной, только на мне", updates=[
        {"path": "assets.realty.ownership_form", "value": "private", "confidence": "high"},
        {"path": "assets.realty.owners", "value": "client_only", "confidence": "high"},
    ])
    state, messages, _ = turn(monkeypatch=monkeypatch, state=state, message="машины нет", updates=[
        {"path": "assets.has_vehicle", "value": False, "confidence": "high"},
    ])

    # Контакты уже собраны в начале — после квалификации сразу завершаем mock-опрос.
    assert state["plan"]["matched"] == "realty_msk"
    assert state["delivery"]["status"] == "completed"
    assert state["delivery"]["request"]["schema_version"] == "mbk.lead.v1"
    closing = prompts.survey_completed_message(thanks_url=None)
    assert messages[-len(closing):] == closing
    assert not any(message.startswith("http") for message in messages)


def test_completion_followup_is_sent_once(monkeypatch):
    """Повторный ход не должен второй раз добавлять системное завершение."""
    state = init_state()
    state["facts"] = {
        "request": {"amount": 800_000},
        "client": {"goal": "cash", "age": 42, "region_class": "moscow_mo", "name": "Василий", "phone": "+79990000000"},
        "debts": {"has_active_credits": False},
        "employment": {"type": "official", "income": 150_000},
        "assets": {"has_realty": True, "has_vehicle": False, "realty": {
            "region_class": "moscow_mo", "kind": "flat",
            "ownership_form": "private", "owners": "client_only"}},
    }

    state, first, _ = turn(monkeypatch=monkeypatch, state=state, message="ок", updates=[])
    state, second, _ = turn(monkeypatch=monkeypatch, state=state, message="спасибо", updates=[])

    closing = prompts.survey_completed_message(thanks_url=None)[-1]
    assert sum(closing in message for message in first) == 1
    assert not any(closing in message for message in second)
    assert state["dialog_phase"] == "survey_completed"


def test_post_link_instruction_does_not_promise_link_again(monkeypatch):
    state = init_state()
    state["facts"] = {
        "request": {"amount": 800_000},
        "client": {"goal": "cash", "age": 42, "region_class": "moscow_mo",
                   "name": "Василий", "phone": "+79990000000"},
        "assets": {"has_realty": True, "realty": {
            "region_class": "moscow_mo", "kind": "flat",
            "ownership_form": "private", "owners": "client_only"}},
    }
    state["delivery"] = {
        "status": "link", "url": "https://mbk.ru/result", "sent": True
    }

    script(monkeypatch, [])
    prepared = core.prepare_turn(state, "спасибо", CONFIG)
    instruction = core.turn_instruction(prepared)

    assert prepared["dialog_phase"] == "link_sent"
    assert "Ссылка уже отправлена" in instruction
    assert "сейчас пришлёшь" not in instruction


def test_delivery_error_is_not_presented_as_no_fit(monkeypatch):
    state = init_state()
    state["facts"] = {
        "request": {"amount": 800_000},
        "client": {"goal": "cash", "age": 42, "region_class": "moscow_mo",
                   "name": "Василий", "phone": "+79990000000"},
        "debts": {"has_active_credits": False},
        "employment": {"type": "official", "income": 150_000},
        "assets": {"has_realty": True, "has_vehicle": False, "realty": {
            "region_class": "moscow_mo", "kind": "flat",
            "ownership_form": "private", "owners": "client_only"}},
    }
    script(monkeypatch, [])
    monkeypatch.setattr(
        core.delivery,
        "submit",
        lambda *a, **k: core.delivery.DeliveryResult(status="error", message="timeout"),
    )

    prepared = core.prepare_turn(state, "готово", CONFIG)
    instruction = core.turn_instruction(prepared)

    assert prepared["dialog_phase"] == "delivery_error"
    assert "технически недоступен" in instruction
    assert "РАБОЧЕГО ВАРИАНТА СЕЙЧАС НЕТ" not in instruction


def test_no_fit_ends_honestly(monkeypatch):
    state = init_state()
    state, _, prepared = turn(monkeypatch=monkeypatch, state=state, message="мне 70, нужно 100 тысяч", updates=[
        {"path": "client.name", "value": "Василий", "confidence": "high"},
        {"path": "client.phone", "value": "+79990000000", "confidence": "high"},
        {"path": "client.goal", "value": "cash", "confidence": "high"},
        {"path": "client.age", "value": 70, "confidence": "high"},
        {"path": "request.amount", "value": 100000, "confidence": "high"},
        {"path": "client.region_class", "value": "rf_other", "confidence": "high"},
        {"path": "assets.has_realty", "value": False, "confidence": "high"},
        {"path": "assets.has_vehicle", "value": False, "confidence": "high"},
        {"path": "debts.has_active_credits", "value": False, "confidence": "high"},
        {"path": "employment.type", "value": "unemployed", "confidence": "high"},
    ])

    assert prepared["plan"]["action"] == "no_fit"
    assert "РАБОЧЕГО ВАРИАНТА СЕЙЧАС НЕТ" in core.turn_instruction(prepared)
    assert prepared["delivery"] is None


def test_extractor_failure_does_not_break_the_turn(monkeypatch):
    """Сбой разбора — не повод ронять диалог: факты просто не обновились."""
    state = init_state()
    monkeypatch.setattr(core.llm_agent, "extract_facts", _boom)

    prepared = core.prepare_turn(state, "привет", CONFIG)
    assert prepared["plan"]["action"] == "ask"


def _boom(*args, **kwargs):
    raise RuntimeError("OpenRouter HTTP 500")


def test_anketa_seeds_normalized_facts():
    state = init_state({
        "full_name": "Василий",
        "desired_amount": "1,5 млн",
        "birth_date": "1984-01-20",
        "registration_address": "Москва, ул. Тверская, д. 1",
        "living_address": "Москва, Тверская",
        "age": 42,
    })
    assert state["facts"]["request"]["amount"] == 1_500_000
    assert state["facts"]["client"]["region_class"] == "moscow_mo"
    assert state["facts"]["client"]["age"] == 42
    assert state["facts"]["client"]["birthdate"] == "1984-01-20"
    assert (
        state["facts"]["client"]["registration_address"]
        == "Москва, ул. Тверская, д. 1"
    )


def test_link_message_never_passes_through_the_model():
    """Ссылка собирается шаблоном — модель не участвует и не может её исказить."""
    assert prompts.link_message("Василий", "https://mbk.ru/x")[-1] == "https://mbk.ru/x"


def test_unverified_model_link_is_removed_before_client_sees_it():
    """Регрессия: модель завершала незакрытый опрос текстом «[ссылка]»."""
    state = init_state()
    state["facts"] = {
        "client": {"goal": "cash"},
        "request": {"amount": 800_000},
    }

    committed = core.commit_turn(
        state,
        "автомобиля нет",
        "Понял, автомобиля нет.\n\nПодобрал варианты: [ссылка]",
    )

    assert committed["messages"] == ["Понял, автомобиля нет."]
    assert committed["current_state"]["delivery"] is None


def test_route_found_guard_removes_model_followup_question():
    """После terminal plan модель не может самовольно продолжить анкету."""
    state = init_state()
    state["_plan_object"] = Plan(action="route_found")
    state["delivery"] = {"status": "completed", "sent": False}

    committed = core.commit_turn(
        state,
        "Все данные сообщил.",
        (
            "Спасибо за подробности.\n\n"
            "Есть ли у вас сейчас активные просрочки?"
        ),
    )

    assert committed["messages"] == [
        "Спасибо за подробности.",
        *prompts.survey_completed_message(
            thanks_url=None, has_phone=False
        ),
    ]


def test_local_no_fit_guard_removes_model_followup_question():
    """Локально исчерпанные маршруты перед CRM также являются terminal-состоянием."""
    state = init_state()
    state["_plan_object"] = Plan(action="no_fit")
    state["delivery"] = {"status": "completed", "sent": False}

    committed = core.commit_turn(
        state,
        "Больше добавить нечего.",
        "Спасибо.\n\nА автомобиль у вас всё-таки есть?",
    )

    assert committed["messages"] == [
        "Спасибо.",
        *prompts.survey_completed_message(
            thanks_url=None, has_phone=False
        ),
    ]


def test_first_script_question_does_not_prepend_the_formal_introduction(monkeypatch):
    """The first actual question can proceed without an unconditional sales preamble."""
    state = init_state()
    state["facts"] = {"client": {"name": "Василий"}}

    state, first, _ = turn(
        monkeypatch=monkeypatch,
        state=state,
        message="нужны деньги",
        updates=[],
        reply="Какая сумма кредита вам нужна и сколько вам лет?",
    )
    joined = " ".join(first)
    assert "сейчас за пару минут" not in joined
    assert "найду лучшее решение" not in joined
    assert "важно отвечать точно и честно" not in joined
    assert "Какая сумма кредита вам нужна и сколько вам лет?" in joined
    assert state["script"]["asked"] == ["request.amount", "client.age"]

    # Клиент не ответил на вопрос — шаг тот же, но переход уже звучал.
    state, second, _ = turn(
        monkeypatch=monkeypatch,
        state=state,
        message="а зачем это всё?",
        updates=[],
    )
    assert "Василий, сейчас за пару минут" not in " ".join(second)


def test_assets_transition_fires_before_the_property_block(monkeypatch):
    state = init_state()
    state["facts"] = {
        "client": {"name": "Василий", "age": 42, "region_class": "moscow_mo"},
        "request": {"amount": 800_000},
        "debts": {"has_active_credits": False},
        "employment": {"type": "official", "income": 150_000},
    }

    _, messages, _ = turn(
        monkeypatch=monkeypatch,
        state=state,
        message="работаю официально",
        updates=[],
    )

    joined = " ".join(messages)
    assert "теперь про активы" in joined
    assert "почти закончили" in joined
