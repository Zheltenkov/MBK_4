"""Голое число денежного поля без единицы — бот переспрашивает, а не домножает.

Прод 20.09.2026, лид 3497115: бот спросил сумму долга, клиент ответил «400
общая сумма», а бот записал 400 рублей — для долга это бессмыслица. Решение
человека: не домножать за клиента и не верить голому числу буквально, а
коротко переспросить («400 тысяч?») и по ответу записать то, что клиент
подтвердил. Денежные поля матрицы — request.amount, debts.total,
employment.income (routing/matrix.yaml, kind: money).

Сквозные тесты идут через core.prepare_turn/commit_turn с подменённым
извлекателем — так же, как tests/test_fastpath.py и
tests/test_entry_other_question.py. Состояние собрано вручную (`script.asked`
и, где нужно, `facts`), а не прогоном анкеты с начала: план хода этим тестам
не важен, важно только поведение на конкретном денежном вопросе.
"""
from __future__ import annotations

import pytest

import core
import prompts
from config import AppConfig
from interpretation import FactCandidate, FactCandidateExtraction
from state import init_state
from utils import set_by_path


LEGACY = AppConfig(openrouter_api_key="sk-or-test", fact_interpretation_mode="legacy")
SEMANTIC = AppConfig(openrouter_api_key="sk-or-test", fact_interpretation_mode="semantic")


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.delenv("MBK_DADATA_API", raising=False)


def _extractor_returning(path: str, value: str):
    def _stub(*args, **kwargs):
        return {
            "fact_updates": [{"path": path, "value": value, "confidence": "high"}],
            "internal_summary": "",
        }

    return _stub


def _failing_extractor(*args, **kwargs):
    raise AssertionError("для этого ответа извлекатель звать не нужно")


def _state_asking(asked_paths: list[str]) -> dict:
    """Состояние на ходу, где анкета только что спросила денежное поле.

    Одна прошлая реплика клиента держит развилку на входе закрытой: решение
    уже принято, дальше — обычная анкета (см. `entry_routing.entry_decision_open`).
    """
    state = init_state()
    state["session_id"] = "money-units-test"
    state["chat_history"] = [
        {"role": "assistant", "content": prompts.OPENING_MESSAGE},
        {"role": "user", "content": "Нужны деньги в долг"},
        {"role": "assistant", "content": "…"},
    ]
    state["script"] = {"asked": asked_paths, "attempts": {}}
    return state


def _facts_before_employment_income() -> dict:
    """Факты анкеты по шаг «доход» включительно — чтобы планировщик после

    ответа на доход сам вернулся ровно к employment.income (см. script в
    routing/matrix.yaml): имя, сумма+возраст, регион, кредиты, долг+просрочка,
    трудоустройство уже закрыты.
    """
    facts: dict = {}
    for path, value in [
        ("client.name", "Иван"),
        ("request.amount", 500_000),
        ("client.age", 40),
        ("client.region_class", "moscow_mo"),
        ("debts.has_active_credits", True),
        ("debts.total", 300_000),
        ("debts.overdue_duration", "no_overdue"),
        ("employment.type", "official"),
    ]:
        set_by_path(facts, path, value)
    return facts


@pytest.mark.parametrize(
    "path, asked",
    [
        ("request.amount", ["request.amount", "client.age"]),
        ("debts.total", ["debts.total", "debts.overdue_duration"]),
        ("employment.income", ["employment.income"]),
    ],
)
def test_bare_number_traps_all_three_money_fields(path, asked, monkeypatch, offline):
    monkeypatch.setattr(core.llm_agent, "extract_facts", _extractor_returning(path, "400"))
    state = _state_asking(asked)

    prepared = core.prepare_turn(state, "400 общая сумма", LEGACY, submit_delivery=False)

    assert prepared["script"]["units_pending"] == {"path": path, "raw": 400}
    # Факт не записан: домножать за клиента нельзя, а поверить голому числу — соврать.
    root, leaf = path.split(".")
    assert leaf not in prepared["facts"].get(root, {})
    assert "400" in core.turn_instruction(prepared)


def test_zero_is_not_trapped(monkeypatch, offline):
    monkeypatch.setattr(
        core.llm_agent, "extract_facts", _extractor_returning("debts.total", "0")
    )
    state = _state_asking(["debts.total", "debts.overdue_duration"])

    prepared = core.prepare_turn(state, "0", LEGACY, submit_delivery=False)

    assert "units_pending" not in prepared.get("script", {})
    assert prepared["facts"]["debts"]["total"] == 0


def test_explicit_rubles_is_written_as_is_without_a_reask(monkeypatch, offline):
    monkeypatch.setattr(core.llm_agent, "extract_facts", _failing_extractor)
    state = _state_asking(["employment.income"])

    prepared = core.prepare_turn(state, "400 рублей", LEGACY, submit_delivery=False)

    assert prepared["facts"]["employment"]["income"] == 400
    assert "units_pending" not in prepared.get("script", {})


def test_explicit_thousands_is_scaled_without_a_reask(monkeypatch, offline):
    monkeypatch.setattr(core.llm_agent, "extract_facts", _failing_extractor)
    state = _state_asking(["employment.income"])

    prepared = core.prepare_turn(state, "400 тысяч", LEGACY, submit_delivery=False)

    assert prepared["facts"]["employment"]["income"] == 400_000
    assert "units_pending" not in prepared.get("script", {})


def test_confirmation_survives_a_commit_and_agreement_multiplies(monkeypatch, offline):
    """Пометка обязана пережить ход: commit_turn сериализует состояние ровно

    так, как оно уйдёт в session_store, а следующий prepare_turn читает его
    заново.
    """
    monkeypatch.setattr(
        core.llm_agent, "extract_facts", _extractor_returning("employment.income", "400")
    )
    state = init_state()
    state["session_id"] = "money-units-cycle"
    state["chat_history"] = [
        {"role": "assistant", "content": prompts.OPENING_MESSAGE},
        {"role": "user", "content": "Нужны деньги"},
        {"role": "assistant", "content": "…"},
    ]
    state["facts"] = _facts_before_employment_income()
    state["script"] = {"asked": ["employment.income"], "attempts": {}}

    prepared = core.prepare_turn(state, "400", LEGACY, submit_delivery=False)
    assert prepared["script"]["units_pending"] == {"path": "employment.income", "raw": 400}

    committed = core.commit_turn(prepared, "400", "Это 400 тысяч?")["current_state"]
    assert committed["script"]["units_pending"] == {"path": "employment.income", "raw": 400}

    second = core.prepare_turn(committed, "да", LEGACY, submit_delivery=False)

    assert second["facts"]["employment"]["income"] == 400_000
    assert "units_pending" not in second.get("script", {})


def test_confirmation_refusal_keeps_the_raw_value(monkeypatch, offline):
    monkeypatch.setattr(
        core.llm_agent, "extract_facts", _extractor_returning("employment.income", "400")
    )
    state = _state_asking(["employment.income"])
    first = core.prepare_turn(state, "400", LEGACY, submit_delivery=False)
    assert first["script"]["units_pending"] == {"path": "employment.income", "raw": 400}

    second = core.prepare_turn(first, "рублей", LEGACY, submit_delivery=False)

    assert second["facts"]["employment"]["income"] == 400
    assert "units_pending" not in second.get("script", {})


def test_confirmation_with_a_new_number_and_unit_defers_to_the_regular_parse(
    monkeypatch, offline
):
    monkeypatch.setattr(
        core.llm_agent, "extract_facts", _extractor_returning("employment.income", "400")
    )
    state = _state_asking(["employment.income"])
    first = core.prepare_turn(state, "400", LEGACY, submit_delivery=False)
    assert first["script"]["units_pending"] == {"path": "employment.income", "raw": 400}

    second = core.prepare_turn(first, "500 тысяч", LEGACY, submit_delivery=False)

    assert second["facts"]["employment"]["income"] == 500_000
    assert "units_pending" not in second.get("script", {})


def test_second_reask_in_a_row_is_forbidden(monkeypatch, offline):
    """Второй голый ответ подряд на то же поле не переспрашивается ещё раз —

    иначе цикл. Значение проходит как есть, хоть и без единицы.
    """
    monkeypatch.setattr(
        core.llm_agent, "extract_facts", _extractor_returning("employment.income", "400")
    )
    state = _state_asking(["employment.income"])
    first = core.prepare_turn(state, "400", LEGACY, submit_delivery=False)
    assert first["script"]["units_pending"] == {"path": "employment.income", "raw": 400}

    monkeypatch.setattr(
        core.llm_agent, "extract_facts", _extractor_returning("employment.income", "300")
    )
    second = core.prepare_turn(first, "300", LEGACY, submit_delivery=False)

    assert second["facts"]["employment"]["income"] == 300
    assert "units_pending" not in second.get("script", {})
    assert "переспроси" not in core.turn_instruction(second)


# --------------------------------------------------------------------------- #
# Semantic-режим (живая проверка 21.09.2026, config из .env,
# fact_interpretation_mode=semantic): свод кандидатов заменяет facts целиком
# из `shadow_extraction.candidates` (core.py, `reduce_fact_candidates`), в
# обход `local_updates`/`model_updates` — легаси-тесты выше идут веткой
# `extract_facts` и эту дыру не видят. Подмена здесь — `extract_fact_candidates`,
# как в tests/test_fact_shadow.py.
# --------------------------------------------------------------------------- #
def _semantic_candidates_returning(path: str, value, evidence: str):
    def _stub(*args, **kwargs):
        return FactCandidateExtraction(
            candidates=[FactCandidate(path=path, value=value, evidence=evidence)]
        )

    return _stub


def test_semantic_mode_traps_the_bare_number_too(monkeypatch, offline):
    """Прод 21.09.2026: без этой правки semantic-режим писал 400 в факты, а

    `script.units_pending` оставался пустым — ровно повторение прод-случая
    20.09.2026, но уже с настоящим извлекателем.
    """
    monkeypatch.setattr(
        core.llm_agent,
        "extract_fact_candidates",
        _semantic_candidates_returning("debts.total", 400, "400"),
    )
    state = _state_asking(["debts.total", "debts.overdue_duration"])

    prepared = core.prepare_turn(state, "400 общая сумма", SEMANTIC, submit_delivery=False)

    assert prepared["script"]["units_pending"] == {"path": "debts.total", "raw": 400}
    assert "total" not in prepared["facts"].get("debts", {})


def test_semantic_mode_writes_an_explicit_amount_without_a_reask(monkeypatch, offline):
    """Значение, которое модель уже прислала домноженным, — не подозрительно:

    единицу клиент назвал, интерсепт не должен трогать нормальные ответы.
    """
    monkeypatch.setattr(
        core.llm_agent,
        "extract_fact_candidates",
        _semantic_candidates_returning("debts.total", 400_000, "400 тысяч"),
    )
    state = _state_asking(["debts.total", "debts.overdue_duration"])

    prepared = core.prepare_turn(
        state, "400 тысяч общая сумма", SEMANTIC, submit_delivery=False
    )

    assert prepared["facts"]["debts"]["total"] == 400_000
    assert "units_pending" not in prepared.get("script", {})


def test_four_digit_amount_is_asked_as_a_choice(monkeypatch, offline):
    """Прод 30.09.2026, лид 3508342: «1300» на вопрос о сумме ушло в лид как
    1300 рублей. Разбор 04.10.2026: на странице отказа просят и 5000 рублей,
    «5 млн?» таким клиентам звучит насмешкой. Переспрос называет оба варианта."""
    monkeypatch.setattr(
        core.llm_agent, "extract_facts", _extractor_returning("request.amount", "1300")
    )
    state = _state_asking(["request.amount", "client.age"])

    prepared = core.prepare_turn(state, "1300", LEGACY, submit_delivery=False)

    assert prepared["script"]["units_pending"] == {"path": "request.amount", "raw": 1300}
    assert "amount" not in prepared["facts"].get("request", {})
    instruction = core.turn_instruction(prepared)
    assert "«1300 рублей или 1,3 миллиона?»" in instruction
    assert "оба варианта" in instruction


@pytest.mark.parametrize(
    ("raw", "question"),
    [
        (400, "400 тысяч?"),
        (1000, "1 тысяча или 1 миллион?"),
        (3000, "3 тысячи или 3 миллиона?"),
        (5000, "5 тысяч или 5 миллионов?"),
        (1300, "1300 рублей или 1,3 миллиона?"),
        (2501, "2501 рубль или 2,501 миллиона?"),
    ],
)
def test_units_question_wording(raw, question):
    assert core._units_question(raw) == question


@pytest.mark.parametrize(
    ("answer", "amount"),
    [("тысяч", 5000), ("5 тысяч", 5000), ("миллионов", 5_000_000), ("5 млн", 5_000_000)],
)
def test_choice_answer_is_written_to_facts(answer, amount, monkeypatch, offline):
    monkeypatch.setattr(
        core.llm_agent, "extract_facts", _extractor_returning("request.amount", "5000")
    )
    state = _state_asking(["request.amount"])
    pending = core.prepare_turn(state, "5000", LEGACY, submit_delivery=False)
    assert pending["script"]["units_pending"]["raw"] == 5000

    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *a, **k: {"updates": []})
    resolved = core.prepare_turn(pending, answer, LEGACY, submit_delivery=False)

    assert resolved["facts"]["request"]["amount"] == amount
    assert "units_pending" not in resolved["script"]


def test_bare_yes_to_a_choice_writes_nothing(monkeypatch, offline):
    """«Да» на «5 тысяч или 5 миллионов?» не выбирает ни то, ни другое: сумма
    остаётся неизвестной, анкета спросит её заново."""
    monkeypatch.setattr(
        core.llm_agent, "extract_facts", _extractor_returning("request.amount", "5000")
    )
    state = _state_asking(["request.amount"])
    pending = core.prepare_turn(state, "5000", LEGACY, submit_delivery=False)

    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *a, **k: {"updates": []})
    resolved = core.prepare_turn(pending, "Да", LEGACY, submit_delivery=False)

    assert "amount" not in resolved["facts"].get("request", {})


def test_units_question_is_asked_alone(monkeypatch, offline):
    """Мутационный прогон 01.10.2026: «1 млн? | В каком городе живёте?» —
    клиент ответил на второй вопрос, переспрос потерялся, а на следующем
    круге его «Нет» про недвижимость записало сумму 1000 рублей. Пока единицы
    не уточнены, в реплике один вопрос — про них."""
    monkeypatch.setattr(
        core.llm_agent, "extract_facts", _extractor_returning("request.amount", "1000")
    )
    state = _state_asking(["request.amount", "client.age"])
    prepared = core.prepare_turn(state, "1000, 38.", LEGACY, submit_delivery=False)

    assert "Больше ничего не спрашивай" in core.turn_instruction(prepared)

    committed = core.commit_turn(
        prepared, "1000, 38.", "1 млн?\n\nВ каком городе и регионе живёте?"
    )

    assert committed["messages"] == ["1 млн?"]
    # Вопрос, который не прозвучал, не считается заданным: иначе короткий ответ
    # на переспрос лёг бы быстрым разбором в чужое поле.
    assert committed["current_state"]["script"]["asked"] == []


# --------------------------------------------------------------------------- #
# Выбор «тысяч или миллионов?» остался без ответа (мутационный прогон 04.10.2026)
# --------------------------------------------------------------------------- #
def _asked_the_choice(monkeypatch, raw: int = 1000):
    monkeypatch.setattr(
        core.llm_agent, "extract_facts", _extractor_returning("request.amount", str(raw))
    )
    pending = core.prepare_turn(
        _state_asking(["request.amount"]), str(raw), LEGACY, submit_delivery=False
    )
    assert pending["script"]["units_pending"] == {"path": "request.amount", "raw": raw}
    return pending


@pytest.mark.parametrize("answer", ["1000, да.", "Да", "1000", "ну да, 1000"])
def test_an_answer_that_picks_nothing_is_asked_once_more(answer, monkeypatch, offline):
    """«1 тысяча или 1 миллион?» — «1000, да». Клиент имел в виду миллион, а в
    лид ушла тысяча рублей. Число не пишется, вопрос звучит ещё раз иначе."""
    pending = _asked_the_choice(monkeypatch)
    monkeypatch.setattr(
        core.llm_agent, "extract_facts", _extractor_returning("request.amount", "1000")
    )

    again = core.prepare_turn(pending, answer, LEGACY, submit_delivery=False)

    assert "amount" not in again["facts"].get("request", {})
    assert again["script"]["units_pending"] == {
        "path": "request.amount",
        "raw": 1000,
        "repeated": True,
    }
    instruction = core.turn_instruction(again)
    assert "это 1000 рублей или 1 миллион рублей?" in instruction
    assert "1 тысяча или 1 миллион?" not in instruction


@pytest.mark.parametrize(
    ("answer", "amount"),
    [("миллион", 1_000_000), ("1 миллион рублей", 1_000_000), ("тысяча рублей", 1000), ("1000 рублей", 1000)],
)
def test_the_second_question_gets_its_answer(answer, amount, monkeypatch, offline):
    pending = _asked_the_choice(monkeypatch)
    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *a, **k: {"updates": []})
    again = core.prepare_turn(pending, "1000, да", LEGACY, submit_delivery=False)

    resolved = core.prepare_turn(again, answer, LEGACY, submit_delivery=False)

    assert resolved["facts"]["request"]["amount"] == amount
    assert "units_pending" not in resolved["script"]


def test_there_is_no_third_question(monkeypatch, offline):
    """Дважды не выбрал — число принимается как названо, цикла нет."""
    pending = _asked_the_choice(monkeypatch)
    again = core.prepare_turn(pending, "1000", LEGACY, submit_delivery=False)
    assert again["script"]["units_pending"]["repeated"] is True

    final = core.prepare_turn(again, "1000", LEGACY, submit_delivery=False)

    assert final["facts"]["request"]["amount"] == 1000
    assert "units_pending" not in final["script"]


def test_semantic_mode_also_asks_once_more(monkeypatch, offline):
    monkeypatch.setattr(
        core.llm_agent,
        "extract_fact_candidates",
        _semantic_candidates_returning("request.amount", 1000, "1000"),
    )
    state = _state_asking(["request.amount"])
    pending = core.prepare_turn(state, "1000", SEMANTIC, submit_delivery=False)
    assert pending["script"]["units_pending"]["raw"] == 1000

    again = core.prepare_turn(pending, "1000, да", SEMANTIC, submit_delivery=False)

    assert "amount" not in again["facts"].get("request", {})
    assert again["script"]["units_pending"]["repeated"] is True


def test_a_three_digit_guess_is_never_repeated(monkeypatch, offline):
    """«400 тысяч?» — вопрос «да/нет», второго захода у него по-прежнему нет."""
    monkeypatch.setattr(
        core.llm_agent, "extract_facts", _extractor_returning("debts.total", "400")
    )
    pending = core.prepare_turn(
        _state_asking(["debts.total"]), "400", LEGACY, submit_delivery=False
    )
    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *a, **k: {"updates": []})

    after = core.prepare_turn(pending, "не знаю", LEGACY, submit_delivery=False)

    assert "units_pending" not in after["script"]


@pytest.mark.parametrize(
    "answer",
    ["тысяч, конечно, пять тысяч рублей", "пять тысяч", "пять тысяч рублей, не миллионов же"],
)
def test_a_choice_made_in_own_words_is_written_at_once(answer, monkeypatch, offline):
    """Мутационный прогон 04.10.2026: «тысяч, конечно, пять тысяч рублей» сочли
    невыбранным — сумма не записалась, бот спросил её ещё дважды. Ответ словами
    уходит обычному разбору, и переспроса больше нет."""
    pending = _asked_the_choice(monkeypatch, raw=5000)
    monkeypatch.setattr(
        core.llm_agent, "extract_facts", _extractor_returning("request.amount", "5000")
    )

    resolved = core.prepare_turn(pending, answer, LEGACY, submit_delivery=False)

    assert resolved["facts"]["request"]["amount"] == 5000
    assert "units_pending" not in resolved["script"]
