"""Разбор боевых диалогов 18–20.10.2026 (118 диалогов, 59 лидов).

Что держат тесты ниже:
- после заявки бот не называет страницу «решением» и не выдумывает, что на ней
  «варианты» (шаблон финала и правила хода после лида);
- кнопка согласия на той же странице, где открыт чат, — без ссылки на неё же;
- совет «отключите защиту» не доходит до клиента;
- «кредит не нужен», «отказываюсь» — уход, и отказ помнится, пока клиент сам
  не вернётся к делу;
- попытка поля анкеты засчитывается только за прозвучавший вопрос;
- имя из ФИО в любом порядке и регистре, без обращения по фамилии;
- «СПб», «Питер», «95 регион», «Бахчесарай» с опечаткой, «Санкт-Петербурге»
  среди СНТ и автодорог;
- «Нет, только 500 рублей» и «110000 тыс» в денежных ответах.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

import core
import prompts
from geo.dadata import DadataClient, _canonical_query
from interpretation import fastpath
from names_ru import first_name_from_fio
from pii_masking import extract_local_pii_updates
from routing import Plan, get_matrix
from state import init_state
from utils import guard_bubbles


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *a, **k: {"fact_updates": [], "internal_summary": ""},
    )
    monkeypatch.setenv("MBK_ROUTER_MOCK", "1")
    monkeypatch.delenv("MBK_DADATA_API", raising=False)
    monkeypatch.setenv("MBK_FACT_INTERPRETATION_MODE", "legacy")


def _asking(paths: list[str]) -> dict:
    state = init_state()
    state["facts"] = {"client": {"name": "Ольга", "age": 40}, "request": {"amount": 300_000}}
    state["script"] = {"asked": list(paths), "attempts": {}}
    state["entry"] = {"scenario": "questionnaire"}
    return state


# --------------------------------------------------------------------------- #
# После заявки: страница — не «решение»
# --------------------------------------------------------------------------- #
def test_the_finale_does_not_call_the_thanks_page_a_decision():
    url = "https://www.mbk.ru/thanks?c=0"

    message = prompts.survey_completed_message(thanks_url=url)

    assert message == [f"Заявка принята. Что дальше — на этой странице: {url}"]
    assert "решени" not in message[0]


def test_the_turn_after_the_lead_keeps_to_the_page_and_does_not_deny_a_call():
    instruction = prompts.CRM_SUBMITTED_INSTRUCTION

    assert "никто перезванивать не обязан" not in instruction
    assert prompts.AFTER_LEAD_QUESTIONS in instruction
    assert "Страницу по ссылке ты не видишь" in instruction


@pytest.mark.parametrize(
    "claim",
    [
        "Понимаю, ссылка ведёт на страницу с подобранными вариантами — там всё расписано.",
        "Все доступные варианты уже на той странице — посмотрите их там.",
        "Выбирайте подходящее и оформляйте заявку напрямую.",
        "Это единственное решение, которое подобралось.",
    ],
)
def test_made_up_page_content_is_cut_after_the_lead(claim):
    guarded = guard_bubbles([claim], conversation_over=True)

    assert guarded and claim not in guarded
    assert "свяжется специалист" in guarded[0]


def test_page_markers_do_not_touch_the_questionnaire():
    bubble = "Решение уже близко, осталась пара вопросов. Сколько вам лет?"

    assert guard_bubbles([bubble]) == [bubble]


# --------------------------------------------------------------------------- #
# Кнопка согласия на той же странице
# --------------------------------------------------------------------------- #
def _awaiting_consent(landing: str) -> dict:
    state = init_state()
    state["facts"] = {"client": {"name": "Ольга"}}
    state["attribution"] = {"landing": landing}
    state["crm_workflow"] = {
        "status": "awaiting_site_consent",
        "offer_url": "https://www.mbk.ru/bfl-finish",
    }
    return state


@pytest.mark.parametrize(
    "landing",
    [
        "https://www.mbk.ru/bfl-finish?fio=%D0%9E&form-id=12",
        "https://mbk.ru/bfl-finish/",
    ],
)
def test_the_consent_button_on_the_same_page_is_not_linked_to_itself(landing):
    followup = core._site_consent_followup(_awaiting_consent(landing))

    assert followup == [
        "Ольга, осталось нажать кнопку подтверждения на этой странице — "
        "без этого передать заявку дальше не получится."
    ]


def test_the_consent_button_on_another_page_is_still_linked():
    followup = core._site_consent_followup(_awaiting_consent("https://www.mbk.ru/"))

    assert len(followup) == 1
    assert "https://www.mbk.ru/bfl-finish" in followup[0]


def test_the_consent_turn_on_the_same_page_does_not_promise_a_link(monkeypatch):
    monkeypatch.setattr("crm_dialog_workflow.ask_site_consent_in_chat", lambda: False)
    state = _awaiting_consent("https://www.mbk.ru/bfl-finish?form-id=1")
    state["entry"] = {"scenario": "questionnaire"}
    state["_plan_object"] = Plan(action="route_found")

    assert core.turn_instruction(state) is prompts.SITE_CONSENT_SAME_PAGE_INSTRUCTION


# --------------------------------------------------------------------------- #
# Совет отключить защиту
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "advice",
    [
        "Попробуйте открыть ссылку в другом браузере или отключите защиту на минуту.",
        "Это ложное срабатывание антивируса, сайт безопасный.",
        "Можно временно выключить антивирус и открыть ещё раз.",
    ],
)
def test_advice_to_turn_off_protection_never_reaches_the_client(advice):
    guarded = guard_bubbles([advice])

    assert guarded == [
        "Защиту отключать не нужно. Если страница не открывается, попробуйте "
        "другой браузер или другое устройство."
    ]


def test_the_rule_about_safety_advice_is_in_the_system_prompt():
    assert prompts.SAFETY_ADVICE_RULE in prompts.CONVERSATION_SYSTEM_PROMPT


# --------------------------------------------------------------------------- #
# Отказ от подбора
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "message",
    [
        "Отказываюсь от кредита",
        "Мне не нужен кредит",
        "Кредит не нужен",
        "Мне деньги не нужны",
        "Вы понимаете что деньги не нужны мне",
        "Не нода ничего",
    ],
)
def test_saying_the_credit_is_not_needed_is_leaving(message):
    assert core.client_is_saying_goodbye(message) is True
    assert core.client_declines(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "Кредит не нужен, нужно списать долги",
        "Мне не нужен кредит под залог",
        "Отказываюсь от страховки, а кредит нужен",
        "Деньги нужны",
        "Спасибо",
    ],
)
def test_talking_about_what_is_needed_is_not_declining(message):
    assert core.client_declines(message) is False


def _declined_then(message: str) -> dict:
    state = _asking(["debts.total"])
    prepared = core.prepare_turn(state, "Кредит не нужен", submit_delivery=False)
    state = core.commit_turn(prepared, "Кредит не нужен", "Хорошо, буду на связи.")[
        "current_state"
    ]
    return core.prepare_turn(state, message, submit_delivery=False)


def test_after_declining_the_questionnaire_is_not_resumed():
    """Бой 20.10.2026, сессия e79066285842: между отказами клиентка писала о
    списаниях с карты, а бот заканчивал каждую реплику «какая сумма нужна?»."""
    prepared = _declined_then("С моей карты списали деньги, куда звонить?")

    instruction = core.turn_instruction(prepared)
    assert instruction.startswith("Клиент раньше сказал, что ему не нужны кредит или деньги")

    reply = "С этим лучше в свой банк — номер есть на обороте карты."
    committed = core.commit_turn(prepared, "С моей карты списали деньги, куда звонить?", reply)
    assert committed["messages"] == [reply], "ни переходной фразы, ни просьбы представиться"
    script = committed["current_state"]["script"]
    assert not (script.get("attempts") or {}).get("debts.total")
    assert script["asked"] == []


def test_declining_is_forgotten_once_the_client_answers_the_questionnaire():
    old = _asking(["debts.total"])
    new = _asking(["debts.total"])
    new["script"]["declined"] = True
    new["facts"]["debts"] = {"total": 200_000}

    core._track_decline(old, new, "Ладно, долг 200 тысяч")

    assert "declined" not in new["script"]


def test_the_declined_instruction_keeps_the_question_for_a_return():
    plan = core.build_plan(get_matrix(), {"client": {"name": "Ольга"}})

    instruction = prompts.declined_instruction(plan)

    assert plan.fields[0].ask in instruction


# --------------------------------------------------------------------------- #
# Попытка — только за прозвучавший вопрос
# --------------------------------------------------------------------------- #
def test_an_answer_without_a_question_does_not_burn_an_attempt():
    """Бой 20.10.2026: пока бот отвечал про списания с карты, у шести полей
    анкеты сгорели все три попытки — вопросов он при этом не задавал."""
    prepared = core.prepare_turn(
        _asking(["debts.total"]), "А что такое реструктуризация?", submit_delivery=False
    )
    planned = prepared["_plan_object"].field_paths

    committed = core.commit_turn(
        prepared,
        "А что такое реструктуризация?",
        "Это изменение условий кредита: срока, ставки или графика платежей.",
    )

    attempts = committed["current_state"]["script"].get("attempts") or {}
    assert not any(attempts.get(path) for path in planned)
    assert committed["current_state"]["script"]["asked"] == []


def test_an_asked_question_still_counts():
    prepared = core.prepare_turn(
        _asking(["debts.total"]), "А что такое реструктуризация?", submit_delivery=False
    )
    planned = prepared["_plan_object"].field_paths

    committed = core.commit_turn(
        prepared,
        "А что такое реструктуризация?",
        "Это изменение условий кредита.\n\nА какой у вас сейчас общий долг?",
    )

    attempts = committed["current_state"]["script"]["attempts"]
    assert all(attempts.get(path) == 1 for path in planned)


# --------------------------------------------------------------------------- #
# Имя из ФИО
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("full_name", "first"),
    [
        ("сергей иванович", "Сергей"),
        ("Иванов Сергей", "Сергей"),
        ("Сергей Петрович Иванов", "Сергей"),
        ("петрова анна викторовна", "Анна"),
        ("Колесникова Злата", "Злата"),
        ("Злата Игоревна", "Злата"),
        ("Иван", "Иван"),
    ],
)
def test_the_first_name_is_found_in_any_order_and_case(full_name, first):
    assert first_name_from_fio(full_name) == first


@pytest.mark.parametrize("full_name", ["Анна Мария", "Тестэ Аватар Шмыг", "один два три четыре"])
def test_the_first_name_is_not_guessed_without_signs(full_name):
    assert first_name_from_fio(full_name) is None


def test_a_lowercase_full_name_is_taken_as_the_name():
    """Бой 19.10.2026: «сергей иванович» — и бот обратился «Иванович»."""
    updates = extract_local_pii_updates("сергей иванович", expected_paths=["client.name"])
    by_path = {item["path"]: item["value"] for item in updates}

    assert by_path["client.name"] == "Сергей Иванович"
    assert by_path["client.display_name"] == "Сергей"


def test_a_lowercase_patronymic_does_not_reach_the_model():
    """Ролевой прогон 20.10.2026: «сергей иванович» в истории не маскировалось
    (в фактах — «Сергей Иванович»), и модель написала «Иванович, сколько вам
    лет?»."""
    from pii_masking import Masker

    masker = Masker.from_facts({"client": {"name": "Сергей Иванович"}})

    masked = masker.mask_text("сергей иванович")

    assert "иванович" not in masked.casefold()
    assert masked.startswith("Сергей")


@pytest.mark.parametrize("message", ["не скажу", "привет", "москва"])
def test_lowercase_words_without_a_name_are_not_a_name(message):
    updates = extract_local_pii_updates(message, expected_paths=["client.name"])

    assert not any(item["path"] == "client.name" for item in updates)


# --------------------------------------------------------------------------- #
# Деньги
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("answer", ["Нет, только 500 рублей", "Рублей", "только 500"])
def test_rubles_on_the_units_question_keep_the_number(answer):
    """Бой 18.10.2026, сессия 4ad270d4ba4f: «500» → «500 тысяч?» → «Рублей»."""
    assert fastpath.parse_units_confirmation(500, answer) == 500


@pytest.mark.parametrize("text", ["110000 тыс руб", "110 000 тыс"])
def test_thousands_named_twice_are_asked_about(text):
    """Бой 19.10.2026, сессия e0837cd8a8f9: «110000 тыс руб» записалось 110 млн."""
    raw = fastpath.bare_money_update(
        {"path": "request.amount", "value": 110_000_000}, text, get_matrix()
    )

    assert raw == 110_000
    assert core._units_question(raw) == "110 тысяч или 110 миллионов?"


@pytest.mark.parametrize(("text", "value"), [("2 млн", 2_000_000), ("1500 тыс", 1_500_000)])
def test_ordinary_amounts_are_not_asked_about(text, value):
    assert (
        fastpath.bare_money_update({"path": "request.amount", "value": value}, text, get_matrix())
        is None
    )


# --------------------------------------------------------------------------- #
# Места
# --------------------------------------------------------------------------- #
def _suggestion(value: str, **data: Any) -> dict[str, Any]:
    return {"value": value, "data": {"country_iso_code": "RU", **data}}


def _place(region: str, region_type: str, locality: str, locality_type: str, *, level: int,
           settlement: bool = False, capital: str = "0") -> dict[str, Any]:
    key = f"{region}-{locality}"
    data: dict[str, Any] = {
        "region": region,
        "region_with_type": f"{region_type} {region}",
        "region_fias_id": region,
        "fias_id": key,
        "fias_level": level,
        "capital_marker": capital,
    }
    if settlement:
        data.update(settlement=locality, settlement_with_type=f"{locality_type} {locality}",
                    settlement_fias_id=key)
    else:
        data.update(city=locality, city_with_type=f"{locality_type} {locality}", city_fias_id=key)
    return _suggestion(f"{region}, {locality}", **data)


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


@pytest.fixture
def dadata(monkeypatch):
    state: dict[str, Any] = {"suggestions": [], "queries": []}

    def fake_urlopen(request, timeout):
        state["queries"].append(json.loads(request.data.decode("utf-8"))["query"])
        return _Response({"suggestions": state["suggestions"]})

    monkeypatch.setattr("geo.dadata.transport.direct_urlopen", fake_urlopen)
    client = DadataClient(token="test-token", max_attempts=1)
    client.answer = lambda suggestions: state.update(suggestions=suggestions)  # type: ignore[attr-defined]
    client.queries = state["queries"]  # type: ignore[attr-defined]
    return client


def test_roads_and_allotments_do_not_make_a_city_ambiguous(dadata):
    """«Санкт-Петербурге» — город и «снт Логоваз Санкт-Петербург», автодороги
    «Москва — Санкт-Петербург» (проба DaData 20.10.2026)."""
    dadata.answer(
        [
            _place("Санкт-Петербург", "г", "Санкт-Петербург", "г", level=1),
            _place("Ленинградская", "обл", "Логоваз Санкт-Петербург", "снт", level=65,
                   settlement=True),
            _place("Тверская", "обл", "автодорога Москва - Санкт-Петербург", "тер", level=65,
                   settlement=True),
            _place("Ленинградская", "обл", "47 км шоссе СПб-Пески", "тер", level=4),
        ]
    )

    resolution = dadata.resolve("Санкт-Петербурге")

    assert resolution.status == "resolved"
    assert resolution.candidate.region == "Санкт-Петербург"


@pytest.mark.parametrize(
    ("spoken", "query"),
    [
        ("СПб", "Санкт-Петербург"),
        ("Питер", "Санкт-Петербург"),
        ("в Питере", "в Санкт-Петербург"),
        ("Питер и область", "Санкт-Петербург"),
        ("95 регион", "Чеченская Республика"),
        ("95регион", "Чеченская Республика"),
        ("регион 77", "Москва"),
        ("Чеченская республика 95регион", "Чеченская республика"),
        ("Петергоф", "Петергоф"),
    ],
)
def test_city_nicknames_and_region_codes_are_spelled_out_for_dadata(spoken, query):
    assert _canonical_query(spoken) == query


def test_a_town_with_a_typo_beats_villages_of_the_same_name(dadata):
    """Бой 20.10.2026, сессия 8ddfd08b8edd: «Бахчесарай» — Крым, Татарстан,
    Башкортостан? Город среди них один."""
    dadata.answer(
        [
            _place("Крым", "респ", "Бахчисарай", "г", level=4),
            _place("Татарстан", "респ", "Бахчасарай", "поселок", level=6, settlement=True),
            _place("Башкортостан", "респ", "Бахчисарай", "деревня", level=6, settlement=True),
        ]
    )

    resolution = dadata.resolve("Бахчесарай")

    assert resolution.status == "resolved"
    assert resolution.candidate.region == "Крым"


def test_a_town_is_not_chosen_over_a_moscow_region_village(dadata):
    """Родники — райцентр Ивановской области и посёлок в Подмосковье: от
    региона зависит маршрут, поэтому спрашиваем."""
    dadata.answer(
        [
            _place("Ивановская", "обл", "Родники", "г", level=4),
            _place("Московская", "обл", "Родники", "пгт", level=6, settlement=True),
        ]
    )

    assert dadata.resolve("Родники").status == "ambiguous"
