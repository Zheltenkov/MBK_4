"""«Спасибо» посреди анкеты — это прощание, а не пропущенный вопрос.

Боевая переписка 08.09.2026: среди диалогов, оборвавшихся на вопросе об имени,
последние реплики клиента — «Спасибо», «Спасибо!», «До свидания». В ответ бот
просил представиться ещё раз. Это последнее, что человек запомнил о боте.

`conversation_is_over` умеет распознавать такие реплики, но только после
финала. Здесь то же самое до финала, и с другим исходом: не молчание, а
короткое человеческое прощание без вопроса.
"""
from __future__ import annotations

import pytest

import core
import prompts
from routing import Plan, get_matrix
from routing.planner import PlannedField
from state import init_state


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """prepare_turn ходит в модель и в справочник адресов — тесту это не нужно.

    Тот же приём, что в tests/test_analytics_hooks.py: извлекатель ничего не
    добавляет к фактам, а DaData выключена. Иначе тест на «реплика легла в
    состояние» тратит секунды на реальную сеть.
    """
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *a, **k: {"fact_updates": [], "internal_summary": ""},
    )
    monkeypatch.setenv("MBK_ROUTER_MOCK", "1")
    monkeypatch.delenv("MBK_DADATA_API", raising=False)
    monkeypatch.setenv("MBK_FACT_INTERPRETATION_MODE", "legacy")


@pytest.mark.parametrize(
    "message",
    ["Спасибо", "спасибо!", "Спасибо, до свидания", "До свидания", "Пока",
     "всего доброго", "Благодарю"],
)
def test_a_farewell_is_recognised(message):
    assert core.client_is_saying_goodbye(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "Спасибо, а какая ставка?",
        "спасибо, мне нужно 300000",
        "Спасибо за информацию, продолжим",
        "",
        "Иван",
    ],
)
def test_a_message_with_substance_is_not_a_farewell(message):
    assert core.client_is_saying_goodbye(message) is False


@pytest.mark.parametrize("message", ["До", "до", "Всего", "всего", "Свидания", "Доброго"])
def test_half_a_farewell_phrase_alone_is_not_a_farewell(message):
    """«До» и «Всего» — половинки фразы, обрывок, а не прощание.

    Ревью 09.09.2026 поймало живьём: одиночное «До» (например, пришедшее
    отдельным пузырём — начало «До какого числа...») распознавалось как
    прощание, потому что слово само по себе лежало в словаре. То же верно и
    для «Свидания»/«Доброго» — они тоже только половина фразы.
    """
    assert core.client_is_saying_goodbye(message) is False


@pytest.mark.parametrize(
    "message",
    ["До свидания", "до свидания", "всего доброго", "Всего хорошего"],
)
def test_the_full_two_word_farewell_is_still_recognised(message):
    """Вместе половинки складываются в прощание — регрессия не должна его снять."""
    assert core.client_is_saying_goodbye(message) is True


def test_the_farewell_turn_tells_the_model_to_say_goodbye():
    state = init_state()
    state["_plan_object"] = Plan(action="ask")
    state["_last_user_message"] = "Спасибо"

    assert core.turn_instruction(state) is prompts.FAREWELL_INSTRUCTION


def test_prepare_turn_remembers_the_last_message():
    """turn_instruction получает только state — реплику надо туда положить."""
    state = core.prepare_turn(init_state(), "Спасибо", submit_delivery=False)

    assert state["_last_user_message"] == "Спасибо"


def test_the_farewell_instruction_forbids_asking_anything():
    text = prompts.FAREWELL_INSTRUCTION.lower()

    assert "не задавай" in text or "не спрашивай" in text


def _state(**overrides):
    """state с явными полями хода — без похода в routing/DaData."""
    state = init_state()
    state.update(overrides)
    return state


# Каждый случай — ветка `turn_instruction`, у которой уже есть своя
# инструкция хода (терминальная фаза, статус CRM или отказ от телефона).
# Ревью 09.09.2026: живой прогон поймал `dialog_phase="delivery_error"` —
# ветку, про которую забыл первый вариант списка исключений. Чтобы не
# полагаться на память при следующей правке, здесь перечислены ВСЕ ветки со
# своей инструкцией, которые нашлись в коде `turn_instruction` на эту дату —
# «Спасибо» не должно перебивать ни одну из них.
_DEDICATED_BRANCH_CASES = [
    pytest.param(
        {
            "_plan_object": Plan(action="route_found"),
            "dialog_phase": "link_sent",
            "facts": {"client": {"phone": "+79990000000"}},
        },
        lambda: prompts.POST_LINK_INSTRUCTION,
        id="link_sent",
    ),
    pytest.param(
        {
            "_plan_object": Plan(action="route_found"),
            "dialog_phase": "survey_completed",
            "facts": {"client": {"phone": "+79990000000"}},
        },
        lambda: prompts.SURVEY_COMPLETED_INSTRUCTION,
        id="survey_completed",
    ),
    pytest.param(
        {
            "_plan_object": Plan(action="route_found"),
            "dialog_phase": "delivery_error",
            "facts": {"client": {"phone": "+79990000000"}},
        },
        lambda: prompts.DELIVERY_ERROR_INSTRUCTION,
        id="delivery_error",
    ),
    pytest.param(
        {
            "_plan_object": Plan(action="no_fit"),
            "dialog_phase": "no_fit",
            "facts": {"client": {"phone": "+79990000000"}},
        },
        lambda: prompts.turn_instruction(Plan(action="no_fit")),
        id="no_fit-phase",
    ),
    pytest.param(
        {
            "_plan_object": Plan(action="ask"),
            "dialog_phase": "no_fit",
            "facts": {},
        },
        lambda: prompts.REFUSED_CONTACT_INSTRUCTION,
        id="refused_contact-via_terminal_phase",
    ),
    pytest.param(
        {
            "_plan_object": Plan(action="route_found"),
            "dialog_phase": "qualification",
            "facts": {},
        },
        lambda: prompts.REFUSED_CONTACT_INSTRUCTION,
        id="refused_contact-via_route_found_action",
    ),
    pytest.param(
        {
            "_plan_object": Plan(action="ask"),
            "dialog_phase": "qualification",
            "crm_workflow": {"status": "awaiting_site_consent"},
            "facts": {},
        },
        # По умолчанию (MBK_ASK_SITE_CONSENT_IN_CHAT не выставлен) чат сам
        # согласие не спрашивает — см. tests/test_site_consent_message.py.
        lambda: prompts.SITE_CONSENT_LINK_INSTRUCTION,
        id="crm-awaiting_site_consent",
    ),
    pytest.param(
        {
            "_plan_object": Plan(action="ask"),
            "dialog_phase": "qualification",
            "crm_workflow": {"status": "submitted"},
            "facts": {"client": {"phone": "+79990000000"}},
        },
        lambda: prompts.CRM_SUBMITTED_INSTRUCTION,
        id="crm-submitted",
    ),
    pytest.param(
        {
            "_plan_object": Plan(action="ask"),
            "dialog_phase": "qualification",
            "crm_workflow": {"status": "qualification_retry_pending"},
            "facts": {"client": {"phone": "+79990000000"}},
        },
        lambda: prompts.CRM_RETRY_PENDING_INSTRUCTION,
        id="crm-qualification_retry_pending",
    ),
    pytest.param(
        {
            "_plan_object": Plan(action="ask"),
            "dialog_phase": "qualification",
            "crm_workflow": {"status": "submission_retry_pending"},
            "facts": {"client": {"phone": "+79990000000"}},
        },
        lambda: prompts.CRM_RETRY_PENDING_INSTRUCTION,
        id="crm-submission_retry_pending",
    ),
    pytest.param(
        {
            "_plan_object": Plan(action="ask"),
            "dialog_phase": "qualification",
            "crm_workflow": {"status": "qualification_error"},
            "facts": {"client": {"phone": "+79990000000"}},
        },
        lambda: prompts.CRM_ERROR_INSTRUCTION,
        id="crm-qualification_error",
    ),
    pytest.param(
        {
            "_plan_object": Plan(action="ask"),
            "dialog_phase": "qualification",
            "crm_workflow": {"status": "submission_error"},
            "facts": {"client": {"phone": "+79990000000"}},
        },
        lambda: prompts.CRM_ERROR_INSTRUCTION,
        id="crm-submission_error",
    ),
    pytest.param(
        {
            "_plan_object": Plan(action="ask"),
            "dialog_phase": "qualification",
            "crm_workflow": {"status": "outcome_unknown"},
            "facts": {"client": {"phone": "+79990000000"}},
        },
        lambda: prompts.CRM_ERROR_INSTRUCTION,
        id="crm-outcome_unknown",
    ),
]


@pytest.mark.parametrize("overrides, expected", _DEDICATED_BRANCH_CASES)
def test_a_dedicated_branch_keeps_its_own_instruction_despite_a_farewell(overrides, expected):
    state = _state(**overrides, _last_user_message="Спасибо")

    instruction = core.turn_instruction(state)

    assert instruction != prompts.FAREWELL_INSTRUCTION
    assert instruction == expected()


def test_the_location_confirmation_is_not_glued_to_a_farewell():
    """Уточнение одноимённого города — единственная ветка со своей инструкцией,
    которая держится не на порядке кода в `_DEDICATED_BRANCH_CASES`, а на
    физическом месте `_location_confirmation_instruction` в `turn_instruction`:
    та стоит раньше проверки прощания. Ревью 10.09.2026: список выше её не
    покрывал, и передвинь кто-нибудь эту ветку ниже прощания — тест этого не
    заметит. Состояние — то же, что в
    `test_dialog_flow.py::test_ambiguous_city_instruction_requires_region_confirmation`.
    """
    spec = get_matrix().fields["client.region_class"]
    plan = Plan(action="ask", fields=[PlannedField(path="client.region_class", spec=spec)])
    state = init_state()
    state["_plan_object"] = plan
    state["facts"] = {
        "client": {
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
    state["_last_user_message"] = "Спасибо"

    instruction = core.turn_instruction(state)

    assert instruction != prompts.FAREWELL_INSTRUCTION
    assert "одноимённые населённые пункты" in instruction


def test_the_contact_request_is_not_glued_to_a_farewell():
    """Иначе «Спасибо» получает в ответ просьбу представиться."""
    spec = get_matrix().fields["client.name"]
    plan = Plan(action="ask", fields=[PlannedField(path="client.name", spec=spec)])

    bubbles = core._merge_contact_request(
        ["Спасибо вам! Если понадоблюсь — я здесь."],
        plan,
        user_message="Спасибо",
    )

    assert "обращаться" not in " ".join(bubbles).lower()


# Анкета пройдена до блока активов: следующий шаг открывается дословной
# переходной фразой заказчика «…теперь про активы…».
_BEFORE_ASSETS = {
    "client": {"name": "Василий", "age": 40, "region_class": "moscow_mo"},
    "request": {"amount": 600_000},
    "debts": {"has_active_credits": False},
    "employment": {"type": "official", "income": 150_000},
}


def _turn(state, message, reply):
    prepared = core.prepare_turn(state, message, submit_delivery=False)
    return prepared, core.commit_turn(prepared, message, reply)


def test_the_transition_is_not_glued_to_a_farewell_and_waits_for_the_client():
    """«Да, спасибо» на шаге с переходом получает прощание, а не два пузыря.

    Ревью 09.09.2026: проверка прощания стояла только в контактной просьбе, а
    переходную фразу ставил отдельный код. Клиент получал подряд «Василий,
    теперь про активы — они заметно расширяют возможности. Мы почти
    закончили» и «Спасибо вам! Если что — я на связи». Хуже того, шаг
    помечался отправленным, и вернувшемуся клиенту дословная фраза
    заказчика уже не звучала никогда.
    """
    state = init_state()
    state["facts"] = {key: dict(value) for key, value in _BEFORE_ASSETS.items()}

    prepared, committed = _turn(
        state, "Да, спасибо", "Спасибо вам! Если что — я на связи."
    )
    assert prepared["_plan_object"].transition, "предпосылка: шаг с переходом"
    assert "про активы" in prepared["_plan_object"].transition, "предпосылка"

    assert committed["messages"] == ["Спасибо вам! Если что — я на связи."]
    step = prepared["_plan_object"].step
    sent = (committed["current_state"].get("script") or {}).get("transitions_sent") or []
    assert step not in sent, "шаг помечен отправленным, хотя фраза не прозвучала"

    # Клиент вернулся — вот теперь фраза заказчика звучит, один раз.
    _, committed = _turn(
        committed["current_state"],
        "Я вернулся, давайте продолжим",
        "Есть ли у вас недвижимость в собственности?",
    )
    assert "про активы" in committed["messages"][0]
