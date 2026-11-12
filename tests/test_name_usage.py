"""Имя клиента звучит не чаще раза за ход.

Заказчик 30.07.2026 прислал скриншот: «Антон, сейчас за пару минут соберу
нужные данные…» и следом «Антон, какая сумма вам нужна и сколько вам полных
лет?» — обращение дважды подряд в одном ходу.

Дедупликация существовала, но работала до того, как backend приклеит
переходную фразу, и смотрела только на предыдущий ход. Внутрь текущего она не
заглядывала вообще.
"""
from __future__ import annotations

import pytest

import core
import prompts
from routing.planner import _render_transition
from state import init_state, seed_facts
from utils import client_display_name


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


def _count(name: str, bubbles: list[str]) -> int:
    return sum(1 for b in bubbles if b.lower().startswith(name.lower()))


def _before_assets():
    state = init_state()
    state["session_id"] = "abc123"
    state["facts"] = {
        "client": {"name": "Антон", "age": 42, "region_class": "moscow_mo"},
        "request": {"amount": 800000}, "debts": {"has_active_credits": False},
        "employment": {"type": "official", "income": 150000},
    }
    return state


def test_transition_and_question_do_not_both_open_with_the_name():
    """The remaining assets transition still shares one name budget with the question."""
    state = _before_assets()
    prepared = core.prepare_turn(state, "работаю официально", submit_delivery=False)

    committed = core.commit_turn(
        prepared, "работаю официально", "Антон, есть ли у вас недвижимость?"
    )
    messages = committed["messages"]

    assert len(messages) >= 2, messages
    assert _count("Антон", messages) == 1, messages


def test_name_survives_when_it_is_used_once():
    state = _before_assets()
    prepared = core.prepare_turn(state, "работаю официально", submit_delivery=False)

    committed = core.commit_turn(prepared, "работаю официально", "Есть ли у вас недвижимость?")

    assert _count("Антон", committed["messages"]) == 1


def test_name_is_dropped_if_the_previous_turn_already_used_it():
    """Старое правило про соседние ходы должно остаться."""
    state = init_state()
    state["session_id"] = "abc123"
    state["facts"] = {
        "client": {"name": "Антон", "age": 42},
        "request": {"amount": 600_000},
    }
    state["chat_history"] = [
        {"role": "user", "content": "600 тысяч, 42 года"},
        {"role": "assistant", "content": "Антон, сколько вам полных лет?"},
    ]
    prepared = core.prepare_turn(state, "Москва", submit_delivery=False)

    committed = core.commit_turn(prepared, "Москва", "Антон, в каком городе живёте?")

    assert _count("Антон", committed["messages"]) == 0, committed["messages"]


def test_transition_stays_grammatical_without_the_name():
    """Если обращение уже прозвучало ходом раньше, переход идёт без имени.

    Текст заказчика от этого не ломается: `_render_transition` восстанавливает
    заглавную букву, и получается «Теперь про активы…». Правило
    «имя раз за серию вопросов» важнее дословности обращения внутри фразы.
    """
    state = _before_assets()
    state["chat_history"] = [
        {"role": "user", "content": "нужны деньги"},
        {"role": "assistant", "content": "Антон, слушаю вас."},
    ]
    prepared = core.prepare_turn(state, "давайте", submit_delivery=False)

    committed = core.commit_turn(prepared, "давайте", "Антон, есть ли у вас недвижимость?")

    assert _count("Антон", committed["messages"]) == 0, committed["messages"]
    assert committed["messages"][0].startswith("Теперь про активы")


def test_confirmed_display_name_is_reduced():
    """Правило искало имя целиком, а модель обращается одним словом.

    Ролевой прогон 11.08.2026: в фактах «Валентина Петровна», в репликах
    «Валентина, …» — регулярка не совпадала, и обращение шло пять ходов подряд.
    То же самое было видно на «Тестов Тест Тестович» → «Тестов, …».
    """
    state = {
        "facts": {
            "client": {
                "name": "Валентина Петровна",
                "display_name": "Валентина",
            }
        },
        "chat_history": [
            {"role": "user", "content": "Да, один есть"},
            {"role": "assistant", "content": "Валентина, а просрочки есть?"},
        ],
    }

    adjusted = core._reduce_repeated_name(
        ["Валентина, теперь про доход.", "Сколько выходит в месяц?"], state
    )

    assert not adjusted[0].startswith("Валентина")
    assert adjusted[0].startswith("Теперь про доход")


def test_surname_from_full_name_is_not_treated_as_an_address():
    state = {
        "facts": {"client": {"name": "Тестов Тест Тестович"}},
        "chat_history": [
            {"role": "assistant", "content": "Тестов Тест Тестович, теперь про активы."},
        ],
    }

    adjusted = core._reduce_repeated_name(["Тестов, есть ли недвижимость?"], state)

    assert adjusted == ["Тестов, есть ли недвижимость?"]


def test_a_word_that_merely_starts_with_the_name_is_left_alone():
    """«Тест» — часть имени, но «Тестирование» обращением не является."""
    state = {
        "facts": {"client": {"name": "Тестов Тест Тестович"}},
        "chat_history": [
            {"role": "assistant", "content": "Тестов, добрый день."},
        ],
    }

    adjusted = core._reduce_repeated_name(["Тестирование займа не требуется."], state)

    assert adjusted[0] == "Тестирование займа не требуется."


@pytest.mark.parametrize(
    "full_name",
    [
        "Иванов Иван Иванович",
        "Иван Иванов Иванович",
    ],
)
def test_full_name_order_does_not_change_the_address(full_name):
    """CRM получает ФИО как пришло, а бот находит в нём имя не по позиции,
    а по словарю и отчеству — при любом порядке слов."""
    facts = seed_facts({"full_name": full_name})

    assert facts["client"]["name"] == full_name
    assert client_display_name(facts) == "Иван"
    assert _render_transition("{name}, продолжим.", facts) == "Иван, продолжим."


def test_separate_name_is_used_for_addressing_without_changing_crm_full_name():
    facts = seed_facts(
        {
            "full_name": "Иванов Иван Иванович",
            "name": "Иван",
        }
    )

    assert facts["client"]["name"] == "Иванов Иван Иванович"
    assert facts["client"]["display_name"] == "Иван"
    assert _render_transition("{name}, продолжим.", facts) == "Иван, продолжим."


def test_name_remains_a_fallback_when_full_name_is_empty():
    facts = seed_facts({"full_name": None, "name": "Иван"})

    assert facts["client"]["name"] == "Иван"
    assert client_display_name(facts) == "Иван"


# --------------------------------------------------------------------------- #
# Имя с отчеством и имя из примера промпта (бой 02–04.10.2026)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("name", "address"),
    [
        ("Ольга Владимировна", "Ольга"),
        ("Ираида Николаевна", "Ираида"),
        ("Роман Викторович", "Роман"),
        ("Геннадий Фёдорович", "Геннадий"),
        ("Анна Ильинична", "Анна"),
        ("Пётр Ильич", "Пётр"),
    ],
)
def test_name_with_a_patronymic_is_addressed_by_the_first_name(name, address):
    facts = {"client": {"name": name}}

    assert client_display_name(facts) == address
    assert _render_transition("{name}, продолжим.", facts) == f"{address}, продолжим."


@pytest.mark.parametrize(
    ("name", "address"),
    [
        ("Татьяна Дмитриева", "Татьяна"),
        ("Хамза Амонов", "Хамза"),
        ("Иванов Иван", "Иван"),
        ("Иванов Иван Иванович", "Иван"),
        ("Петрова Анна Викторовна", "Анна"),
        # Бой 19.10.2026: бот обратился к этому клиенту «Иванович».
        ("сергей иванович", "Сергей"),
    ],
)
def test_a_full_name_is_addressed_by_the_first_name_never_the_surname(name, address):
    """Фамилия бывает и первой, и второй: имя определяется словарём имён,
    отчеством и окончанием фамилии, а не позицией слова."""
    assert client_display_name({"client": {"name": name}}) == address


@pytest.mark.parametrize("name", ["Тестэ Аватар Шмыг", "Анна Мария"])
def test_a_full_name_without_reliable_signs_is_not_guessed(name):
    """Ни словаря, ни отчества, ни фамилии — или два имени сразу: не угадываем."""
    assert client_display_name({"client": {"name": name}}) is None


def test_the_patronymic_stays_hidden_from_the_model():
    from pii_masking import Masker

    masker = Masker.from_facts({"client": {"name": "Ольга Владимировна"}})

    assert masker.first_name == "Ольга"
    assert masker.direct["Ольга Владимировна"] == "Ольга [остальная часть ФИО скрыта]"
    assert masker.direct["Владимировна"] == "[часть ФИО скрыта]"


def _state_with_name(name: str | None) -> dict:
    state = init_state()
    if name:
        state["facts"] = {"client": {"name": name}}
    return state


@pytest.mark.parametrize("name", [None, "Татьяна Дмитриева", "Ираида Николаевна"])
def test_the_prompt_example_name_is_not_used_for_another_client(name):
    """«Василий, какая сумма вам нужна?» — строка из примеров промпта."""
    bubbles = core._drop_borrowed_name(
        ["Василий, какая сумма вам нужна?", "И сколько вам полных лет?"],
        _state_with_name(name),
    )

    assert bubbles == ["Какая сумма вам нужна?", "И сколько вам полных лет?"]


@pytest.mark.parametrize("name", ["Василий", "Василий Петрович", "василий"])
def test_a_real_vasily_keeps_his_name(name):
    bubbles = ["Василий, какая сумма вам нужна?"]

    assert core._drop_borrowed_name(bubbles, _state_with_name(name)) == bubbles


def test_the_guard_knows_the_name_the_prompt_actually_uses():
    assert f"Клиент: {prompts.STYLE_EXAMPLE_NAME}\n" in prompts.STYLE_EXAMPLES


def test_borrowed_name_is_removed_from_a_committed_turn():
    state = _state_with_name("Татьяна Дмитриева")
    prepared = core.prepare_turn(state, "Мне нужен кредит", submit_delivery=False)

    committed = core.commit_turn(
        prepared, "Мне нужен кредит", "Василий, какая сумма вам нужна?"
    )

    assert not any("Василий" in bubble for bubble in committed["messages"])
