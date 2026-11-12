"""Сквозная проверка: настоящий план хода → текст просьбы об имени.

Ревью 09.09.2026 нашло в этой ветке три дефекта, и все три прятались от
тестов по одной причине: тесты контактной ветки строили `Plan` руками и
передавали номер попытки числом — то есть проверяли состояния, которых
рантайм не производит. Имя не спрашивалось ни разу (попытку съедал
приветственный ход), вторая просьба повторялась дословно, первая
контактная была недостижима.

Здесь ничего не строится руками. Диалог идёт от приветствия настоящими
ходами `prepare_turn` → `commit_turn`, план считает планировщик, попытки —
счётчик. Клиент отвечает на то, о чём спросил план прошлого хода, а имени не
называет никогда: так проверяется весь путь просьбы об имени от первой
реплики до конца анкеты.

Офлайн, как tests/test_analytics_hooks.py: извлекатель подменён и отдаёт
ответы клиента на заданные поля, DaData выключена.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

import core
import prompts
from state import init_state


# Что клиент отвечает на вопросы анкеты. Кредитов, недвижимости и машины нет —
# вложенные ветки не открываются, и анкета короткая.
ANSWERS = {
    "request.amount": 600_000,
    "client.age": 40,
    "client.region_class": "moscow_mo",
    "debts.has_active_credits": False,
    "employment.type": "official",
    "employment.income": 150_000,
    "assets.has_realty": False,
    "assets.has_vehicle": False,
}
PHONE = "+7 999 123-45-67"

# Реплика модели на каждом ходу. Не «Понял(а)»: такое начало реакции
# `_safe_contact_reaction` срезает целиком, и от пузыря остаётся голая точка —
# старый дефект вне этой ветки, отмеченный ревью; здесь он бы только мешал.
MODEL_REPLY = "Записала."

# По этим словам узнаём просьбу об имени в пузыре бота.
_NAME_MARKERS = ("обращаться", "зовут", "имя")


@dataclass
class Turn:
    client: str
    action: str
    asked: list[str]
    messages: list[str]
    name_attempts: int

    @property
    def name_requests(self) -> list[str]:
        return [
            bubble
            for bubble in self.messages
            if any(marker in bubble.lower() for marker in _NAME_MARKERS)
        ]


class ScriptedExtractor:
    """Извлекатель, который «слышит» ответ на поля, заданные прошлым ходом."""

    def __init__(self) -> None:
        self.updates: list[dict] = []

    def __call__(self, *args, **kwargs) -> dict:
        return {"fact_updates": list(self.updates), "internal_summary": ""}


@pytest.fixture
def extractor(monkeypatch) -> ScriptedExtractor:
    scripted = ScriptedExtractor()
    monkeypatch.setattr(core.llm_agent, "extract_facts", scripted)
    monkeypatch.setenv("MBK_ROUTER_MOCK", "1")
    monkeypatch.delenv("MBK_DADATA_API", raising=False)
    monkeypatch.setenv("MBK_FACT_INTERPRETATION_MODE", "legacy")
    return scripted


def _talk(
    extractor: ScriptedExtractor,
    *,
    first_message: str,
    answer_to_name: str,
    phone_reply: str = PHONE,
) -> list[Turn]:
    """Диалог от приветствия до конца анкеты. Имени клиент не называет."""
    state = init_state()
    state["session_id"] = "flow00000001"

    prepared = core.prepare_turn(state, "", submit_delivery=False)
    committed = core.commit_turn(prepared, "", prompts.OPENING_MESSAGE)
    turns = [_record("", prepared, committed)]
    state = committed["current_state"]

    message, answered = first_message, []
    for _ in range(40):
        extractor.updates = [
            {"path": path, "value": ANSWERS[path]} for path in answered if path in ANSWERS
        ]
        prepared = core.prepare_turn(state, message, submit_delivery=False)
        for path in answered:
            if path in ANSWERS:
                assert core.get_fact(prepared["facts"], path) == ANSWERS[path], (
                    f"ответ на {path} не лёг в факты — сценарий теста разошёлся"
                )
        committed = core.commit_turn(prepared, message, MODEL_REPLY)
        turn = _record(message, prepared, committed)
        turns.append(turn)
        state = committed["current_state"]
        if turn.action not in {"ask", "contacts"}:
            return turns

        unexpected = set(turn.asked) - set(ANSWERS) - {"client.name", "client.phone"}
        assert not unexpected, f"анкета спросила неожиданное: {unexpected}"
        answered = list(turn.asked)
        if "client.phone" in turn.asked:
            message = phone_reply
        elif turn.asked == ["client.name"]:
            message = answer_to_name
        else:
            message = "Отвечаю на ваш вопрос"
    raise AssertionError("анкета не закончилась за 40 ходов")


def _record(message: str, prepared: dict, committed: dict) -> Turn:
    plan = prepared["_plan_object"]
    attempts = (committed["current_state"].get("script") or {}).get("attempts") or {}
    return Turn(
        client=message,
        action=plan.action,
        asked=list(plan.field_paths),
        messages=list(committed["messages"]),
        name_attempts=int(attempts.get("client.name") or 0),
    )


def _transcript(turns: list[Turn]) -> str:
    return "\n".join(
        f"{index}. клиент: {turn.client!r} | план: {turn.action} {turn.asked} | "
        f"попыток имени: {turn.name_attempts} | бот: {turn.messages}"
        for index, turn in enumerate(turns)
    )


def _all_name_requests(turns: list[Turn]) -> list[str]:
    return [request for turn in turns for request in turn.name_requests]


# --------------------------------------------------------------------------- #
# Клиент не называет ни имени, ни телефона до конца анкеты
# --------------------------------------------------------------------------- #
@pytest.fixture
def without_phone(extractor) -> list[Turn]:
    return _talk(
        extractor,
        first_message="Здравствуйте, нужен кредит",
        answer_to_name="Давайте сразу к делу",
    )


def test_the_greeting_is_just_a_greeting(without_phone):
    greeting = without_phone[0]

    assert greeting.messages == [prompts.OPENING_MESSAGE]
    assert greeting.name_attempts == 0, _transcript(without_phone)


def test_the_name_is_asked_once_right_after_the_first_message(without_phone):
    first = without_phone[1]

    assert first.name_requests == [
        f"{MODEL_REPLY} Подскажите, как к вам обращаться?"
    ], _transcript(without_phone)
    assert first.name_attempts == 1


def test_the_questionnaire_goes_on_without_the_name(without_phone):
    """Имя не шлагбаум: до контактного шага о нём больше не вспоминают."""
    first_contacts = next(
        index for index, turn in enumerate(without_phone) if turn.action == "contacts"
    )
    survey = without_phone[2:first_contacts]

    assert survey, "между знакомством и контактами должна пройти анкета"
    assert all(turn.action == "ask" for turn in survey)
    assert not any(turn.name_requests for turn in survey), _transcript(without_phone)


def test_the_name_comes_back_together_with_the_phone(without_phone):
    first_contacts = next(turn for turn in without_phone if turn.action == "contacts")

    assert set(first_contacts.asked) == {"client.name", "client.phone"}
    (request,) = first_contacts.name_requests
    assert "обращаться" in request and "номер телефона" in request, _transcript(
        without_phone
    )


def test_without_a_phone_no_name_request_repeats_word_for_word(without_phone):
    requests = _all_name_requests(without_phone)

    assert len(requests) == len(set(requests)), _transcript(without_phone)
    assert len(requests) == 2, (
        "имя звучит дважды — при знакомстве и на контактном шаге:\n"
        + _transcript(without_phone)
    )


def test_the_phone_alone_releases_the_application(without_phone):
    """Бой 02.10.2026, сессия 7180dbf55364: на общую просьбу об имени и
    телефоне клиент прислал только номер. Заявка уходит этим же ходом, а не
    ждёт ещё одной реплики под «не хотите называть имя — ничего страшного»."""
    index = next(
        i for i, turn in enumerate(without_phone) if turn.action == "contacts"
    )
    after = without_phone[index + 1]

    assert after.client == PHONE
    assert after.action in {"route_found", "no_fit"}, _transcript(without_phone)
    assert not after.name_requests, _transcript(without_phone)


def test_the_dialogue_reaches_the_end_without_the_name(without_phone):
    assert without_phone[-1].action in {"route_found", "no_fit"}, _transcript(
        without_phone
    )
    assert not without_phone[-1].name_requests


# --------------------------------------------------------------------------- #
# Телефон назван в первой же реплике — имя на контактном шаге спрашивается одно
# --------------------------------------------------------------------------- #
@pytest.fixture
def phone_first(extractor) -> list[Turn]:
    return _talk(
        extractor,
        first_message=f"Здравствуйте, нужен кредит. Мой телефон {PHONE}",
        # Случай ревьюера: на прежнее «Продолжим?» клиент отвечал «да» и видел
        # ту же просьбу символ в символ.
        answer_to_name="да",
    )


def test_the_name_is_asked_once_at_the_contact_step(phone_first):
    requests = _all_name_requests(phone_first)
    contact_turns = [turn for turn in phone_first if turn.action == "contacts"]

    assert [turn.asked for turn in contact_turns] == [["client.name"]], (
        _transcript(phone_first)
    )
    assert [turn.name_attempts for turn in contact_turns] == [2]
    assert len(requests) == 2, _transcript(phone_first)
    assert len(set(requests)) == len(requests), _transcript(phone_first)


def test_a_yes_to_the_second_request_releases_the_application(phone_first):
    """Бой 02.10.2026, сессия 7180dbf55364: телефон есть, имени нет. Бот говорил
    «не хотите называть имя — ничего страшного», а заявка ждала следующей
    реплики клиента — он ушёл, и лид не создался. Теперь ход, на котором
    клиент снова не назвал имя, сам доводит анкету до конца."""
    index = next(
        i for i, turn in enumerate(phone_first) if turn.action == "contacts"
    )
    after = phone_first[index + 1]

    assert after.client == "да"
    assert after.action in {"route_found", "no_fit"}, _transcript(phone_first)
    assert not after.name_requests, _transcript(phone_first)
