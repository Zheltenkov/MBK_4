"""Финал разговора: что клиент слышит, когда анкета собрана.

Матрица Андрея от 10.08.2026: `thanks_url` заполнен только у части правил —
залог авто ведёт на info.mbk.ru, Кредит.Клаб на www.mbk.ru. Но пустое поле
означает не «ссылки нет», а дефолтную страницу благодарности: «используй её
как фолбэк, тогда обещание бота „пришлю ссылку“ всегда выполнимо». Он же
17.08.2026, прямо: «перезванивать никто не должен по залогу».

Мы прочли то письмо наоборот и до 17.08.2026 отправляли клиента залога ждать
звонка, которого никто не собирался делать. Отсюда правило: страница есть
всегда, звонок не обещаем никогда.

Обещание ссылки при этом по-прежнему не звучит в вопросе про телефон: там
категория ещё неизвестна, а адрес приходит от CRM.
"""
from __future__ import annotations

import core
import prompts
from state import init_state


def test_link_finale_prints_the_address_itself():
    """Ссылка должна быть видна в любом интерфейсе, а не только в нашем.

    Раньше финал звучал «ваше решение по кнопке ниже». Кнопка есть в нашем
    чате, но на сайте МБК стоит их собственный виджет: он рисует пузыри и о
    кнопке ничего не знает. Клиент читал про кнопку, которой у него нет, — с
    этой жалобы («а когда ссылка появляется?») и начался разбор.
    """
    url = "https://info.mbk.ru/spasibo-zaim-zalog-pts"
    bubbles = prompts.survey_completed_message(thanks_url=url)

    text = " ".join(bubbles)
    assert url in text
    assert "менеджер" not in text.casefold()
    # Адрес по-прежнему не проходит через модель: пузырь собирает backend.
    assert len(bubbles) == 1


def test_nobody_is_promised_a_callback():
    """«Перезванивать никто не должен по залогу» — Андрей, 17.08.2026.

    Раньше здесь стояло обещание звонка менеджера. Клиент уходил из чата ждать,
    а на той стороне никто и не планировал набирать его номер.
    """
    text = " ".join(prompts.survey_completed_message(thanks_url=None)).casefold()

    assert "позвонит" not in text
    assert "менеджер" not in text


def test_finale_without_an_answer_from_crm_promises_nothing():
    """Сюда попадаем, только если ответа CRM нет вовсе.

    При успешной квалификации страница есть всегда — хотя бы дефолтная. Значит
    пустой `thanks_url` тут означает не «такой маршрут», а «мы не дозвонились до
    CRM», и обещать клиенту что-либо от её имени нельзя: ни ссылку, ни звонок,
    ни что «мы свяжемся».
    """
    bubbles = prompts.survey_completed_message(thanks_url=None)

    text = " ".join(bubbles).casefold()
    assert "менеджер" not in text
    assert "свяжемся" not in text
    assert "позвон" not in text


def test_finale_never_names_the_lead_number():
    """Решение заказчика 10.08.2026: номер обращения клиенту не показываем."""
    for thanks_url in (None, "https://www.mbk.ru/thanks-credit-club"):
        bubbles = prompts.survey_completed_message(thanks_url=thanks_url)

        text = " ".join(bubbles).casefold()
        assert "обращени" not in text or "номер" not in text


def test_followup_reads_the_branch_from_the_crm_workflow_state():
    """Ветку выбирает не модель: она не видит ни категории, ни ссылки."""
    state = init_state()
    state["facts"] = {"client": {"phone": "+79990000000"}}
    state["delivery"] = {"status": "completed", "sent": False}
    state["crm_workflow"] = {
        **state["crm_workflow"],
        "status": "submitted",
        "category": 2217,
        "qualified": True,
        "thanks_url": None,
    }

    bubbles = core._delivery_followup(state)

    assert bubbles == prompts.survey_completed_message(
        thanks_url=None,
    )


def test_followup_without_crm_workflow_falls_back_to_the_neutral_promise():
    """Локальный прогон без CRM не должен обещать звонок менеджера."""
    state = init_state()
    state["facts"] = {"client": {"phone": "+79990000000"}}
    state["delivery"] = {"status": "completed", "sent": False}

    bubbles = core._delivery_followup(state)

    assert bubbles == prompts.survey_completed_message(
        thanks_url=None,
    )


def test_phone_request_promises_no_channel_at_all(matrix):
    """Категория ещё не известна, поэтому обещать нечего — ни ссылку, ни звонок.

    Классификация запускается только после телефона, и на этом шаге бот не
    может знать, будет ли у клиента ссылка или ему позвонит менеджер. Раньше
    здесь стояло «Ссылка появится здесь, в чате» — обещание, невыполнимое для
    залоговой ветки.
    """
    from routing import plan as build_plan

    current = build_plan(
        matrix,
        {
            "client": {"name": "Василий", "age": 40, "region_class": "moscow_mo"},
            "request": {"amount": 600_000},
            "debts": {"has_active_credits": False},
            "employment": {"type": "official", "income": 150_000},
            "assets": {"has_realty": False, "has_vehicle": False},
        },
    )
    assert current.action == "contacts"

    lowered = prompts.turn_instruction(current).casefold()

    assert "ссылка появится" not in lowered
    assert "не обещай ни ссылку" in lowered


def test_contact_request_does_not_promise_a_link(matrix):
    """Детерминированный пузырь тоже обещал ссылку, которой может не быть.

    11.08.2026 обещание убрали из инструкции модели, но контактный запрос
    core._merge_contact_request собирается отдельно и правку не получил —
    нашлось ролевым прогоном.
    """
    from routing import plan as build_plan

    current = build_plan(
        matrix,
        {
            "client": {"name": "Сергей", "age": 38, "region_class": "moscow_mo"},
            "request": {"amount": 1_200_000},
            "debts": {"has_active_credits": False},
            "employment": {"type": "business", "income": 200_000},
            "assets": {"has_realty": False, "has_vehicle": False},
        },
    )
    assert current.action == "contacts"

    merged = " ".join(core._merge_contact_request(["Хорошо."], current)).casefold()

    assert "пришлю ссылку" not in merged
    assert "телефон" in merged


def test_phone_is_not_asked_forever(matrix):
    """Клиент, не дающий номер, загонял анкету в бесконечный повтор.

    Ролевой прогон 11.08.2026 (роль terse_skeptic): бот 23 хода повторял один и
    тот же контактный запрос дословно. У полей сценария потолок попыток есть, а
    контакты шли мимо него.
    """
    from routing import plan as build_plan
    from routing.planner import MAX_FIELD_ATTEMPTS

    facts = {
        "client": {"name": "Сергей", "age": 38, "region_class": "moscow_mo"},
        "request": {"amount": 1_200_000},
        "debts": {"has_active_credits": False},
        "employment": {"type": "business", "income": 200_000},
        "assets": {"has_realty": False, "has_vehicle": False},
    }

    asking = build_plan(matrix, facts, attempts={"client.phone": 1})
    assert asking.action == "contacts"

    exhausted = build_plan(
        matrix, facts, attempts={"client.phone": MAX_FIELD_ATTEMPTS}
    )
    assert exhausted.action != "contacts"


def test_finale_without_a_phone_does_not_promise_a_call():
    """Обещать звонок менеджера некуда, если номера так и не дали."""
    bubbles = prompts.survey_completed_message(
        thanks_url=None, has_phone=False
    )

    text = " ".join(bubbles).casefold()
    assert "позвонит" not in text
    assert "здесь" in text
    assert "напишите его" not in text


def test_contact_attempts_are_counted(matrix):
    """Потолок попыток бесполезен, если контактные ходы не считать.

    Ролевой прогон 11.08.2026: потолок в планировщике уже стоял, но счётчик
    рос только на ходах `ask`, поэтому attempts["client.phone"] навсегда
    оставался нулём и запрос телефона повторялся снова и снова — до трёх раз
    подряд дословно.
    """
    from routing import plan as build_plan

    facts = {
        "client": {"name": "Дмитрий", "age": 35, "region_class": "rf_other"},
        "request": {"amount": 800_000},
        "debts": {"has_active_credits": False},
        "employment": {"type": "unemployed"},
        "assets": {"has_realty": False, "has_vehicle": False},
    }
    state = init_state()
    state["facts"] = facts

    current = build_plan(matrix, facts)
    assert current.action == "contacts"

    core._count_attempts(state, current)
    core._count_attempts(state, current)

    assert state["script"]["attempts"]["client.phone"] == 2


def test_phone_stops_being_asked_after_repeated_silence(matrix):
    """Сквозная проверка: счётчик и потолок должны работать вместе."""
    from routing import plan as build_plan
    from routing.planner import MAX_FIELD_ATTEMPTS

    facts = {
        "client": {"name": "Дмитрий", "age": 35, "region_class": "rf_other"},
        "request": {"amount": 800_000},
        "debts": {"has_active_credits": False},
        "employment": {"type": "unemployed"},
        "assets": {"has_realty": False, "has_vehicle": False},
    }
    state = init_state()
    state["facts"] = facts

    for _ in range(MAX_FIELD_ATTEMPTS):
        current = build_plan(matrix, facts, attempts=core._field_attempts(state))
        assert current.action == "contacts"
        core._count_attempts(state, current)

    exhausted = build_plan(matrix, facts, attempts=core._field_attempts(state))

    assert exhausted.action != "contacts"


def test_terminal_turn_after_a_refusal_tells_the_model_to_stop_asking(matrix):
    """На ходе, где сработал потолок, модель всё ещё просила номер.

    Ролевой прогон 11.08.2026 (роль phone_refusal): «Уточните, пожалуйста,
    номер телефона — и я сразу вернусь с результатом», а следом
    детерминированное «Без номера телефона заявку дальше не передать». Бот
    просит и сдаётся в одной реплике. Инструкция терминального хода про отказ
    ничего не знала, а история разговора состоит из просьб — модель их и
    повторяла, доходя до «технически невозможно».
    """
    from routing import Plan

    state = init_state()
    state["facts"] = {"client": {"name": "Олег"}}
    state["_plan_object"] = Plan(action="route_found")

    instruction = core.turn_instruction(state)

    lowered = instruction.casefold()
    assert "не проси" in lowered
    assert "отказ" in lowered


def test_terminal_turn_with_a_phone_is_unchanged(matrix):
    from routing import Plan

    state = init_state()
    state["facts"] = {"client": {"name": "Олег", "phone": "+79990000000"}}
    state["_plan_object"] = Plan(action="route_found")

    instruction = core.turn_instruction(state)

    assert "не проси" not in instruction.casefold()


def test_refusal_beats_the_completed_phase(matrix):
    """Фаза «опрос завершён» перехватывала ход раньше проверки отказа.

    Именно поэтому первая правка не подействовала: к моменту инструкции
    prepare_turn уже проставил survey_completed, ветка фазы возвращала общий
    текст «поблагодари и скажи, что данные собраны», и модель, видя историю из
    сплошных просьб, просила номер ещё раз.
    """
    from routing import Plan

    state = init_state()
    state["facts"] = {"client": {"name": "Олег"}}
    state["dialog_phase"] = "survey_completed"
    state["delivery"] = {"status": "completed", "sent": False}
    state["_plan_object"] = Plan(action="route_found")

    instruction = core.turn_instruction(state)

    assert "не проси" in instruction.casefold()


def test_model_does_not_repeat_the_deterministic_closing():
    """Модель проговаривала то же, что через строку скажет backend.

    Ролевой прогон 11.08.2026: «Без него заявку дальше передать не получится» и
    сразу за ним «Без номера телефона заявку дальше не передать». Смысл верный,
    сказано дважды.
    """
    state = init_state()
    state["facts"] = {"client": {"name": "Олег"}}
    state["delivery"] = {"status": "completed", "sent": False}

    committed = core.commit_turn(
        state,
        "Не буду оставлять номер.",
        "Принято, без номера так без номера.\n\n"
        "Без него заявку дальше передать не получится.",
    )

    assert committed["messages"] == [
        "Принято, без номера так без номера.",
        *prompts.survey_completed_message(
            thanks_url=None, has_phone=False
        ),
    ]


def test_a_reaction_that_adds_meaning_survives():
    """Снимаем только дубль, а не всё, что упомянуло телефон."""
    state = init_state()
    state["facts"] = {"client": {"name": "Олег"}}
    state["delivery"] = {"status": "completed", "sent": False}

    committed = core.commit_turn(
        state,
        "Не буду оставлять номер.",
        "Понимаю, дело ваше.",
    )

    assert committed["messages"][0] == "Понимаю, дело ваше."


def test_consent_question_survives_the_terminal_guard():
    """Вопрос согласия исчезал, и лид не уходил в CRM.

    Живой диалог 19.08.2026, Казань, клиент под БФЛ. CRM ответила
    `need_site_consent`, бот должен был спросить «хотите, чтобы специалист
    проконсультировал по этому варианту?» — и не спросил. Анкета к тому моменту
    уже пройдена, план стоит в `route_found`, а терминальный guard вырезает из
    ответа всё с вопросительным знаком: он защищает от того, что модель
    продолжит анкету после решения движка, и не знает, что этот вопрос
    санкционирован backend.

    Клиент дважды переспросил «где оно?» и оба раза получил «Спасибо, данные
    собраны». Согласие не получено — значит `map_lead_intake` возвращает issue
    по `consent.site_offer`, лид не создаётся вовсе. В CRM не появилось ничего,
    ссылки клиент не увидел.
    """
    from routing.planner import Plan

    state = init_state()
    state["facts"] = {"client": {"name": "Тестик", "phone": "+79213456789"}}
    state["dialog_phase"] = "awaiting_site_consent"
    state["crm_workflow"] = {**state["crm_workflow"], "status": "awaiting_site_consent"}
    state["_plan_object"] = Plan(action="route_found")

    committed = core.commit_turn(
        state,
        "и?",
        "По вашей ситуации есть дополнительный вариант.\n\n"
        "Хотите, чтобы специалист проконсультировал вас по нему?",
    )

    assert "Хотите, чтобы специалист проконсультировал вас по нему?" in committed["messages"]
    assert "Спасибо, данные собраны." not in committed["messages"]
