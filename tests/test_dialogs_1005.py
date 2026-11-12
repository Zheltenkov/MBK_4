"""Находки разбора боевых диалогов после выкатки mbk17 (05.10.2026, 35 диалогов).

Каждый блок — один боевой случай:

1. Сессия a4dd48655572: «В каком регионе и городе находится этот участок?» —
   «Краснодар». Город второго объекта не записался, попытки сгорели, лид ушёл
   без региона второго объекта.
2. Сессия 5ad064af05cd: «70 тысяч — это точная сумма долга или примерная?» —
   «80 тыс точно». Сумма долга не записалась: извлекатель отнёс цифру к
   нужной сумме, свод дал конфликт с названной раньше.
3. Сессия ab9b04137d71: «Я не просила помощи!», «Я просто не туда ткнула
   случайно, пока!», «Отстань» — бот пять ходов подряд продолжал анкету и
   сказал «Вы сами оставили заявку на подбор».
4. Сессия 6b4cfc134666: «В каком городе живёте?» — «Калуге». Бот предложил
   выбрать между Калужской, Саратовской, Орловской, Тверской областями и
   Пермским краем.
5. Сессия bf1df88ab543: «150 тысяч?» — «46лет». Бот ответил «150 тысяч —
   поняла», сумму не записал и через ход спросил её заново.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

import core
import prompts
from config import AppConfig
from geo.dadata import AddressCandidate, AddressResolution, DadataClient
from geo.enrichment import enrich_locations
from interpretation import FactCandidate, FactCandidateExtraction
from interpretation.fastpath import parse_expected_answer
from routing import get_matrix
from state import init_state
from utils import guard_bubbles

LEGACY = AppConfig(openrouter_api_key="sk-or-test", fact_interpretation_mode="legacy")
SEMANTIC = AppConfig(openrouter_api_key="sk-or-test", fact_interpretation_mode="semantic")


@pytest.fixture(scope="module")
def matrix():
    return get_matrix()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *a, **k: {"fact_updates": [], "internal_summary": ""},
    )
    monkeypatch.setattr(
        core.llm_agent,
        "extract_fact_candidates",
        lambda *a, **k: FactCandidateExtraction(),
    )
    monkeypatch.setenv("MBK_ROUTER_MOCK", "1")
    monkeypatch.delenv("MBK_DADATA_API", raising=False)


def _asking(paths: list[str], facts: dict | None = None) -> dict:
    state = init_state()
    state["session_id"] = "dialogs1005"
    state["facts"] = facts or {
        "client": {"name": "Иван", "age": 40},
        "request": {"amount": 300_000},
    }
    state["script"] = {"asked": list(paths), "attempts": {}}
    state["entry"] = {"scenario": "questionnaire"}
    return state


# --------------------------------------------------------------------------- #
# 1. Город второго объекта недвижимости
# --------------------------------------------------------------------------- #
TWO_OBJECTS = {
    "client": {"name": "Евгений", "age": 53, "region": "Челябинская обл", "city": "г Миасс", "region_class": "rf_other"},
    "request": {"amount": 3_000_000},
    "debts": {"has_active_credits": True, "total": 3_000_000, "overdue_duration": "no_overdue"},
    "employment": {"type": "official", "income": 130_000},
    "assets": {
        "has_realty": True,
        "realty": {
            "kind": "house",
            "city": "г Миасс",
            "region": "Челябинская обл",
            "region_class": "rf_other",
            "ownership_form": "private",
            "owners": "client_only",
        },
        "has_second_realty": True,
        "realty_2": {"kind": "other"},
    },
}


@pytest.mark.parametrize("answer", ["Краснодар", "краснодар", "Краснодарский край", "г. Сочи"])
def test_a_bare_place_answers_the_question_about_the_second_property(matrix, answer):
    assert parse_expected_answer(answer, ["assets.realty_2.region_class"], matrix) == [
        {"path": "assets.realty_2.region", "value": answer, "confidence": "high"}
    ]


@pytest.mark.parametrize("config", [LEGACY, SEMANTIC], ids=["legacy", "semantic"])
def test_the_city_of_the_second_property_reaches_the_facts(config):
    """Извлекатель на голое «Краснодар» не отвечает ничем (живая проба 2/2)."""
    state = _asking(["assets.realty_2.region_class"], json.loads(json.dumps(TWO_OBJECTS)))

    prepared = core.prepare_turn(state, "Краснодар", config, submit_delivery=False)

    second = prepared["facts"]["assets"]["realty_2"]
    assert "краснодар" in json.dumps(second, ensure_ascii=False).lower(), second
    # Первый объект при этом не тронут.
    assert prepared["facts"]["assets"]["realty"]["city"] == "г Миасс"


def _krasnodar() -> AddressCandidate:
    return AddressCandidate(
        value="г Краснодар",
        unrestricted_value="Краснодарский край, г Краснодар",
        country="Россия",
        country_iso_code="RU",
        region="Краснодарский",
        region_with_type="Краснодарский край",
        region_fias_id="kda",
        region_iso_code="RU-KDA",
        city="Краснодар",
        city_with_type="г Краснодар",
        city_fias_id="kda-city",
        settlement=None,
        settlement_with_type=None,
        settlement_fias_id=None,
        fias_id="kda-city",
        fias_level=4,
    )


def test_the_second_property_gets_its_region_class(matrix):
    def resolve(query: str) -> AddressResolution:
        candidate = _krasnodar()
        return AddressResolution(
            status="resolved", query=query, candidate=candidate, candidates=(candidate,)
        )

    facts = enrich_locations(
        {"assets": {"realty_2": {"kind": "other", "region": "Краснодар"}}},
        "Краснодар",
        matrix,
        resolver=resolve,
        asked_paths=["assets.realty_2.region_class"],
    )

    second = facts["assets"]["realty_2"]
    assert second["region"] == "Краснодарский край"
    assert second["region_class"] is not None


@pytest.mark.parametrize("answer", ["Не помню", "А зачем вам это?", "Квартира в Сочи и дача в Анапе"])
def test_what_is_not_a_place_still_goes_to_the_model(matrix, answer):
    assert parse_expected_answer(answer, ["assets.realty_2.region_class"], matrix) is None


def _resolved_home(matrix) -> dict:
    """Клиент из Краснодара: место жительства уже разобрано."""

    def resolve(query: str) -> AddressResolution:
        candidate = _krasnodar()
        return AddressResolution(
            status="resolved", query=query, candidate=candidate, candidates=(candidate,)
        )

    return enrich_locations({"client": {"city": "Краснодар"}}, "Краснодар", matrix, resolver=resolve)


def _never_called(query: str) -> AddressResolution:
    raise AssertionError(f"DaData не нужна, место уже известно: {query}")


@pytest.mark.parametrize(
    "answer",
    ["Где и живу", "Там же", "там же где живу", "Там, где и я живу", "В том же городе", "Тоже там", "по месту прописки"],
)
def test_same_place_as_home_is_not_asked_again(matrix, answer):
    """Бой 05.10.2026: «В каком городе находится этот дом?» — «Где и живу» —
    «Мне нужен именно населённый пункт с областью или краем»."""
    facts = _resolved_home(matrix)
    facts["assets"] = {"realty": {"kind": "house", "region": answer}}

    facts = enrich_locations(
        facts, answer, matrix, resolver=_never_called, asked_paths=["assets.realty.region_class"]
    )

    realty = facts["assets"]["realty"]
    assert realty["region"] == "Краснодарский край"
    assert realty["city"] == "г Краснодар"
    assert realty["location"]["status"] == "resolved"
    assert realty["region_class"] is not None


def _sochi() -> AddressCandidate:
    from dataclasses import replace

    return replace(
        _krasnodar(),
        value="Краснодарский край, г Сочи",
        city="Сочи",
        city_with_type="г Сочи",
        city_fias_id="sochi",
        fias_id="sochi",
    )


def test_same_place_for_the_second_property_means_the_first_one(matrix):
    """Живёт в Краснодаре, дом в Сочи, участок «там же» — в Сочи."""

    def resolve(query: str) -> AddressResolution:
        candidate = _sochi()
        return AddressResolution(
            status="resolved", query=query, candidate=candidate, candidates=(candidate,)
        )

    facts = _resolved_home(matrix)
    facts["assets"] = {"realty": {"kind": "house", "region": "Сочи"}}
    facts = enrich_locations(facts, "Сочи", matrix, resolver=resolve)
    assert facts["assets"]["realty"]["city"] == "г Сочи"
    facts["assets"]["realty_2"] = {"kind": "other", "region": "там же"}

    facts = enrich_locations(
        facts, "там же", matrix, resolver=_never_called, asked_paths=["assets.realty_2.region_class"]
    )

    assert facts["assets"]["realty_2"]["city"] == "г Сочи"


def test_same_place_falls_back_to_home_when_the_first_property_is_unknown(matrix):
    facts = _resolved_home(matrix)
    facts["assets"] = {"realty": {"kind": "house"}, "realty_2": {"kind": "other", "region": "там же"}}

    facts = enrich_locations(
        facts, "там же", matrix, resolver=_never_called, asked_paths=["assets.realty_2.region_class"]
    )

    assert facts["assets"]["realty_2"]["city"] == "г Краснодар"


def test_same_place_does_not_borrow_an_address_that_was_not_confirmed(matrix):
    """Адрес дома записан, но не подтверждён (остался от старого запроса) —
    ссылаться на него нельзя."""

    def not_found(query: str) -> AddressResolution:
        return AddressResolution(status="not_found", query=query)

    facts = _resolved_home(matrix)
    facts["client"]["location"]["status"] = "ambiguous"
    facts["assets"] = {"realty": {"kind": "house", "region": "Там же"}}

    facts = enrich_locations(
        facts, "Там же", matrix, resolver=not_found, asked_paths=["assets.realty.region_class"]
    )

    assert facts["assets"]["realty"].get("city") != "г Краснодар"


def test_same_place_without_a_known_home_is_still_a_question(matrix):
    """Сослаться не на что — пусть бот спросит, а не выдумывает."""

    def not_found(query: str) -> AddressResolution:
        return AddressResolution(status="not_found", query=query)

    facts = enrich_locations(
        {"assets": {"realty": {"kind": "house", "region": "Там же"}}},
        "Там же",
        matrix,
        resolver=not_found,
        asked_paths=["assets.realty.region_class"],
    )

    assert facts["assets"]["realty"].get("region_class") is None
    assert facts["assets"]["realty"]["location"]["status"] == "not_found"


def test_a_real_place_is_never_taken_for_a_reference(matrix):
    seen: list[str] = []

    def resolve(query: str) -> AddressResolution:
        seen.append(query)
        candidate = _krasnodar()
        return AddressResolution(
            status="resolved", query=query, candidate=candidate, candidates=(candidate,)
        )

    facts = _resolved_home(matrix)
    facts["assets"] = {"realty": {"kind": "house", "region": "Тамбов"}}

    enrich_locations(facts, "Тамбов", matrix, resolver=resolve)

    assert seen == ["Тамбов"]


# --------------------------------------------------------------------------- #
# 2. Сумма со словом-оговоркой
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("answer", "value"),
    [
        ("80 тыс точно", 80_000),
        ("Точно 80 тысяч", 80_000),
        ("ровно 300 тысяч", 300_000),
        ("всего 150000", 150_000),
        ("итого 1,2 млн", 1_200_000),
        ("80000 рублей точно", 80_000),
        ("в сумме 450 тысяч", 450_000),
    ],
)
def test_a_sure_amount_is_taken_as_named(matrix, answer, value):
    assert parse_expected_answer(answer, ["debts.total"], matrix) == [
        {"path": "debts.total", "value": value, "confidence": "high"}
    ]


@pytest.mark.parametrize(
    "answer",
    [
        "точно не знаю",
        "Ну наверно 70 тыс хотела взять на погошения",
        "около 80 тыс",
        "примерно 300 тысяч",
        "80 тыс, но это без ипотеки",
    ],
)
def test_an_unsure_amount_still_goes_to_the_model(matrix, answer):
    """Оговорка «точно» ничего не меняет в числе; сомнение и второй факт —
    меняют, и такие ответы по-прежнему разбирает модель."""
    assert parse_expected_answer(answer, ["debts.total"], matrix) is None


def test_sure_words_are_left_alone_outside_money_questions(matrix):
    """«Точно» на вопрос «есть ли машина?» — это «да», а не слово при числе."""
    assert parse_expected_answer("Точно", ["assets.has_vehicle"], matrix) == [
        {"path": "assets.has_vehicle", "value": True, "confidence": "high"}
    ]


@pytest.mark.parametrize("config", [LEGACY, SEMANTIC], ids=["legacy", "semantic"])
def test_the_debt_named_after_a_clarification_is_written(config, monkeypatch):
    """В бою извлекатель отнёс «80 тыс точно» к нужной сумме: конфликт с уже
    названными 90 тысячами, и не записалось ничего."""
    wrong = FactCandidateExtraction(
        candidates=[FactCandidate(path="request.amount", value=80_000, evidence="80 тыс точно")]
    )
    monkeypatch.setattr(core.llm_agent, "extract_fact_candidates", lambda *a, **k: wrong)
    facts = {
        "client": {"name": "Мария", "age": 33, "region_class": "rf_other"},
        "request": {"amount": 90_000},
        "debts": {"has_active_credits": True},
    }

    prepared = core.prepare_turn(
        _asking(["debts.total"], facts), "80 тыс точно", config, submit_delivery=False
    )

    assert prepared["facts"]["debts"]["total"] == 80_000
    assert prepared["facts"]["request"]["amount"] == 90_000


# --------------------------------------------------------------------------- #
# 3. «Отстань», «я не просила помощи», «не туда ткнула»
# --------------------------------------------------------------------------- #
DISMISSALS = [
    "Отстань",
    "Отстаньте от меня",
    "Отвали",
    "Иди на х",
    "Нахер пошла",
    "Идите вы лесом",
    "Я не просила помощи!",
    "Я просто не туда ткнула случайно,пока!",
    "Случайно нажал",
    "Не туда попал",
    "Мне это не интересно",
    "Не интересует",
]


@pytest.mark.parametrize("message", DISMISSALS)
def test_a_dismissal_ends_the_questionnaire(message):
    assert core.client_is_dismissing(message) is True
    assert core.client_is_saying_goodbye(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "Случайно взяла кредит на 300 тысяч",
        "Деньги ушли не туда",
        "А вы не туда отправили мою заявку?",
        "Не интересует залог, хочу без залога",
        "Я не просила кредит, я просила рефинансирование",
        "Отстали по платежам на месяц",
        "Я случайно нажала, а можно узнать ставку?",
        "Случайно нажал 2 раза, мне нужно 300 тысяч",
        "Нет",
        "Не хочу",
        "Иду на работу",
    ],
)
def test_talk_that_only_resembles_a_dismissal_goes_on(message):
    assert core.client_is_dismissing(message) is False


@pytest.mark.parametrize("message", ["Отстань", "Я не просила помощи!", "Я просто не туда ткнула случайно,пока!"])
def test_the_turn_after_a_dismissal_apologises_and_asks_nothing(message):
    prepared = core.prepare_turn(
        _asking(["assets.has_vehicle"]), message, LEGACY, submit_delivery=False
    )

    assert core.turn_instruction(prepared) is prompts.DISMISSAL_INSTRUCTION

    committed = core.commit_turn(prepared, message, "Извините за беспокойство.")
    assert committed["messages"] == ["Извините за беспокойство."]
    attempts = committed["current_state"]["script"].get("attempts") or {}
    assert not attempts.get("assets.has_vehicle")
    assert core.conversation_is_over(committed["current_state"], "Отстань") is True


def test_an_ordinary_goodbye_keeps_its_own_instruction():
    prepared = core.prepare_turn(
        _asking(["assets.has_vehicle"]), "Спасибо, до свидания", LEGACY, submit_delivery=False
    )

    assert core.turn_instruction(prepared) is prompts.FAREWELL_INSTRUCTION


def test_the_dismissal_instruction_forbids_questions_and_excuses():
    text = prompts.DISMISSAL_INSTRUCTION.lower()

    assert "не задавай" in text
    assert "заявк" in text  # про заявку не говорить
    assert "не оправдывайся" in text or "не объясняй" in text


@pytest.mark.parametrize(
    "claim",
    [
        "Вы сами оставили заявку на подбор, я просто помогаю разобраться.",
        "Вы оставили заявку на подбор — поэтому я и пишу.",
        "Вы сами обратились к нам за помощью.",
    ],
)
def test_the_bot_does_not_claim_the_client_applied(claim):
    """Чат открывается на странице у каждого; «откуда вы взялись?» —
    «вы сами оставили заявку» было неправдой."""
    bubbles = guard_bubbles([claim, "Чем могу помочь?"])
    assert bubbles == ["Чем могу помочь?"]

    alone = guard_bubbles([claim])
    assert len(alone) == 1 and "чат на сайте" in alone[0].lower()
    assert "оставили заявку" not in alone[0].lower()
    assert "обратились" not in alone[0].lower()


def test_the_system_prompt_says_where_the_chat_comes_from():
    text = prompts.CONVERSATION_SYSTEM_PROMPT.lower()

    assert "открывается" in text and "сам оставил" in text


# --------------------------------------------------------------------------- #
# 4. Столица региона в косвенном падеже
# --------------------------------------------------------------------------- #
def _suggestion(value: str, **data: Any) -> dict[str, Any]:
    return {"value": value, "data": {"country_iso_code": "RU", **data}}


KALUGA = [
    _suggestion(
        "г Калуга",
        region="Калужская",
        region_with_type="Калужская обл",
        region_fias_id="klu",
        city="Калуга",
        city_with_type="г Калуга",
        city_fias_id="klu-city",
        fias_id="klu-city",
        fias_level=4,
        capital_marker="2",
    ),
    _suggestion(
        "Саратовская обл, Федоровский р-н, село Калуга",
        region="Саратовская",
        region_with_type="Саратовская обл",
        region_fias_id="sar",
        settlement="Калуга",
        settlement_with_type="село Калуга",
        settlement_fias_id="sar-kaluga",
        fias_id="sar-kaluga",
        fias_level=6,
        capital_marker="0",
    ),
    _suggestion(
        "Орловская обл, Орловский р-н, деревня Калуга",
        region="Орловская",
        region_with_type="Орловская обл",
        region_fias_id="orl",
        settlement="Калуга",
        settlement_with_type="деревня Калуга",
        settlement_fias_id="orl-kaluga",
        fias_id="orl-kaluga",
        fias_level=6,
        capital_marker="0",
    ),
]


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _client(monkeypatch, suggestions: list[dict[str, Any]]) -> DadataClient:
    monkeypatch.setattr(
        "geo.dadata.transport.direct_urlopen",
        lambda request, timeout: _Response({"suggestions": suggestions}),
    )
    return DadataClient(token="test-token", max_attempts=1)


@pytest.mark.parametrize("query", ["Калуге", "калуге", "Калуги", "Калугу", "г. Калуге"])
def test_a_capital_named_in_another_case_is_still_the_capital(monkeypatch, query):
    resolution = _client(monkeypatch, KALUGA).resolve(query)

    assert resolution.status == "resolved"
    assert resolution.candidate.region == "Калужская"
    assert resolution.candidate.city == "Калуга"


def test_a_longer_name_is_not_taken_for_a_case_form(monkeypatch):
    """«Калугино» — другое место, а не падеж Калуги."""
    resolution = _client(monkeypatch, KALUGA).resolve("Калугино")

    assert resolution.status == "ambiguous"


@pytest.mark.parametrize(
    ("spoken", "city"),
    [("Казани", "Казань"), ("Твери", "Тверь"), ("Самаре", "Самара"), ("Орле", "Орёл"), ("Уфе", "Уфа")],
)
def test_case_forms_of_other_capitals(monkeypatch, spoken, city):
    suggestions = [
        _suggestion(
            f"г {city}",
            region="Область",
            region_with_type="Область обл",
            region_fias_id="cap",
            city=city,
            city_with_type=f"г {city}",
            city_fias_id="cap-city",
            fias_id="cap-city",
            fias_level=4,
            capital_marker="2",
        ),
        _suggestion(
            f"Другая обл, деревня {city}",
            region="Другая",
            region_with_type="Другая обл",
            region_fias_id="other",
            settlement=city,
            settlement_with_type=f"деревня {city}",
            settlement_fias_id="other-village",
            fias_id="other-village",
            fias_level=6,
            capital_marker="0",
        ),
    ]

    resolution = _client(monkeypatch, suggestions).resolve(spoken)

    assert resolution.status == "resolved"
    assert resolution.candidate.city == city


# --------------------------------------------------------------------------- #
# 5. На «150 тысяч?» ответили про возраст
# --------------------------------------------------------------------------- #
def _extractor(path: str, value: Any):
    return lambda *a, **k: {
        "fact_updates": [{"path": path, "value": value, "confidence": "high"}],
        "internal_summary": "",
    }


def _asked_units(monkeypatch, raw: int = 150) -> dict:
    monkeypatch.setattr(core.llm_agent, "extract_facts", _extractor("request.amount", str(raw)))
    state = _asking(["request.amount", "client.age"], {"client": {"name": "Наталья"}})
    pending = core.prepare_turn(state, str(raw), LEGACY, submit_delivery=False)
    assert pending["script"]["units_pending"] == {"path": "request.amount", "raw": raw}
    return pending


def test_an_answer_about_something_else_keeps_the_units_question(monkeypatch):
    pending = _asked_units(monkeypatch)
    monkeypatch.setattr(core.llm_agent, "extract_facts", _extractor("client.age", 46))

    after = core.prepare_turn(pending, "46лет", LEGACY, submit_delivery=False)

    assert after["facts"]["client"]["age"] == 46
    assert "amount" not in after["facts"].get("request", {})
    assert after["script"]["units_pending"] == {
        "path": "request.amount",
        "raw": 150,
        "repeated": True,
        "answered_other": True,
    }
    instruction = core.turn_instruction(after)
    assert "«А 150 — это тысяч?»" in instruction
    # Ответ про возраст принят — бот говорит об этом, а не «150 тысяч — поняла».
    assert "ответил про другое" in instruction


def test_the_returned_units_question_gets_its_answer(monkeypatch):
    pending = _asked_units(monkeypatch)
    monkeypatch.setattr(core.llm_agent, "extract_facts", _extractor("client.age", 46))
    after = core.prepare_turn(pending, "46лет", LEGACY, submit_delivery=False)
    monkeypatch.setattr(
        core.llm_agent, "extract_facts", lambda *a, **k: {"fact_updates": [], "internal_summary": ""}
    )

    resolved = core.prepare_turn(after, "да", LEGACY, submit_delivery=False)

    assert resolved["facts"]["request"]["amount"] == 150_000
    assert "units_pending" not in resolved["script"]


def test_an_amount_confirmed_alongside_is_not_asked_again(monkeypatch):
    """«150 тысяч, 46 лет»: и единица названа, и возраст — вопрос закрыт."""
    pending = _asked_units(monkeypatch)
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *a, **k: {
            "fact_updates": [
                {"path": "request.amount", "value": "150 тысяч", "confidence": "high"},
                {"path": "client.age", "value": 46, "confidence": "high"},
            ],
            "internal_summary": "",
        },
    )

    after = core.prepare_turn(pending, "150 тысяч, 46 лет", LEGACY, submit_delivery=False)

    assert after["facts"]["request"]["amount"] == 150_000
    assert after["facts"]["client"]["age"] == 46
    assert "units_pending" not in after["script"]


def test_the_units_question_comes_back_only_once(monkeypatch):
    pending = _asked_units(monkeypatch)
    monkeypatch.setattr(core.llm_agent, "extract_facts", _extractor("client.age", 46))
    after = core.prepare_turn(pending, "46лет", LEGACY, submit_delivery=False)
    monkeypatch.setattr(core.llm_agent, "extract_facts", _extractor("client.region", "Саяногорск"))

    third = core.prepare_turn(after, "Саяногорск", LEGACY, submit_delivery=False)

    assert "units_pending" not in third["script"]


def test_an_empty_answer_does_not_bring_the_question_back(monkeypatch):
    """«Не знаю» ничего не сообщает: сумму спросит анкета, как и раньше."""
    pending = _asked_units(monkeypatch)
    monkeypatch.setattr(
        core.llm_agent, "extract_facts", lambda *a, **k: {"fact_updates": [], "internal_summary": ""}
    )

    after = core.prepare_turn(pending, "не знаю", LEGACY, submit_delivery=False)

    assert "units_pending" not in after["script"]


def test_semantic_mode_keeps_the_units_question_too(monkeypatch):
    monkeypatch.setattr(
        core.llm_agent,
        "extract_fact_candidates",
        lambda *a, **k: FactCandidateExtraction(
            candidates=[FactCandidate(path="request.amount", value=150, evidence="150")]
        ),
    )
    state = _asking(["request.amount", "client.age"], {"client": {"name": "Наталья"}})
    pending = core.prepare_turn(state, "150", SEMANTIC, submit_delivery=False)
    assert pending["script"]["units_pending"]["raw"] == 150
    monkeypatch.setattr(
        core.llm_agent,
        "extract_fact_candidates",
        lambda *a, **k: FactCandidateExtraction(
            candidates=[FactCandidate(path="client.age", value=46, evidence="46лет")]
        ),
    )

    after = core.prepare_turn(pending, "46лет", SEMANTIC, submit_delivery=False)

    assert after["facts"]["client"]["age"] == 46
    assert after["script"]["units_pending"]["answered_other"] is True


# --------------------------------------------------------------------------- #
# Единица у чужого числа (мутационный прогон 05.10.2026)
# --------------------------------------------------------------------------- #
from interpretation.fastpath import bare_money_update  # noqa: E402


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        ("request.amount", 150, "доход 58 тысяч, нужна сумма 150, кредитов нет"),
        ("debts.total", 400, "Долг 400, доход 90 тысяч"),
        ("request.amount", 300, "Мне 300 нужно. Зарплата 50 тыс"),
        ("debts.total", 400, "400 общая сумма"),
        ("request.amount", 150, "нужно 150, тысяч 60 зарабатываю"),
    ],
)
def test_a_unit_named_for_another_number_does_not_count(matrix, path, value, message):
    """«Тысяч» стояло у дохода, а сумма 150 ушла рублями: единицу искали во
    всей реплике, а не у числа."""
    assert bare_money_update({"path": path, "value": value}, message, matrix) == value


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        ("request.amount", 150, "нужна сумма 150 тысяч, доход 58"),
        ("request.amount", 500, "Тысяч 500"),
        ("request.amount", 400, "400к"),
        ("request.amount", 400, "400 ₽"),
        ("debts.total", 400, "долг 400-500 тысяч"),
        ("request.amount", 150, "сто пятьдесят тысяч"),
        ("employment.income", 58, "доход 58 тысяч, нужна сумма 150"),
    ],
)
def test_a_unit_next_to_the_number_still_counts(matrix, path, value, message):
    assert bare_money_update({"path": path, "value": value}, message, matrix) is None


@pytest.mark.parametrize("config", [LEGACY, SEMANTIC], ids=["legacy", "semantic"])
def test_everything_at_once_still_gets_the_units_question(config, monkeypatch):
    message = "я Наталья, 46 лет, доход 58 тысяч, нужна сумма 150, кредитов нет"
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *a, **k: {
            "fact_updates": [
                {"path": "request.amount", "value": "150", "confidence": "high"},
                {"path": "employment.income", "value": "58 тысяч", "confidence": "high"},
            ],
            "internal_summary": "",
        },
    )
    monkeypatch.setattr(
        core.llm_agent,
        "extract_fact_candidates",
        lambda *a, **k: FactCandidateExtraction(
            candidates=[
                FactCandidate(path="request.amount", value=150, evidence="нужна сумма 150"),
                FactCandidate(path="employment.income", value=58_000, evidence="доход 58 тысяч"),
            ]
        ),
    )
    state = _asking(["request.amount", "client.age"], {"client": {"name": "Наталья"}})

    prepared = core.prepare_turn(state, message, config, submit_delivery=False)

    assert prepared["script"]["units_pending"] == {"path": "request.amount", "raw": 150}
    assert "amount" not in prepared["facts"].get("request", {})
    assert prepared["facts"]["employment"]["income"] == 58_000


# --------------------------------------------------------------------------- #
# Адрес из четырёх слов (бой 06.10.2026, сессия 72b931171e8f)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "answer",
    [
        "Челябинская область город Златоуст",
        "Челябинская область, город Златоуст",
        "г. Златоуст Челябинская область",
        "Ростовская область хутор Староротовка",
        "Тульская обл Ефремовский р-н село Шилово",
    ],
)
@pytest.mark.parametrize(
    "path",
    ["client.region_class", "assets.realty.region_class", "assets.realty_2.region_class"],
)
def test_an_address_spelled_out_in_words_is_taken_whole(matrix, answer, path):
    """«В каком регионе и городе находится второй дом?» — «Челябинская область
    город Златоуст». Ответ длиннее трёх слов уходил извлекателю, про второй
    объект тот промолчал, и лид с телефоном не создался."""
    updates = parse_expected_answer(answer, [path], matrix)

    assert updates is not None and len(updates) == 1
    assert updates[0]["path"].endswith(".region")
    assert updates[0]["value"] == answer


@pytest.mark.parametrize(
    "answer",
    [
        "Вам это всё зачем нужно",
        "Сейчас посмотрю в документах и напишу",
        "Челябинская область город Златоуст улица Бушуева дом пятнадцать квартира три",
        "Где-то в области, точно не помню",
        "Дом в Златоусте бушуева 15",
    ],
)
def test_a_long_reply_without_an_address_still_goes_to_the_model(matrix, answer):
    assert parse_expected_answer(answer, ["assets.realty_2.region_class"], matrix) is None


def test_other_fields_keep_the_three_word_limit(matrix):
    assert parse_expected_answer("город область район село", ["assets.has_vehicle"], matrix) is None


@pytest.mark.parametrize("config", [LEGACY, SEMANTIC], ids=["legacy", "semantic"])
def test_the_spelled_out_address_of_the_second_property_reaches_the_facts(config):
    state = _asking(["assets.realty_2.region_class"], json.loads(json.dumps(TWO_OBJECTS)))

    prepared = core.prepare_turn(
        state, "Челябинская область город Златоуст", config, submit_delivery=False
    )

    second = json.dumps(prepared["facts"]["assets"]["realty_2"], ensure_ascii=False).lower()
    assert "златоуст" in second, second
