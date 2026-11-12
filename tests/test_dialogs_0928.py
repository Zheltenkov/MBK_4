"""Находки боевых диалогов 28.09.2026 (первый день после сбоя прокси).

Двадцать один разговор — и в них бот:

- записал «квартира 4 комнатная» комнатой: в CRM ушёл не тот тип объекта, а
  от типа зависит залоговая категория;
- на «живу в Крыму» ответил «вижу, вы в селе Крым Ростовской области»;
- на «менеджер когда позвонит» — «звонков не будет», хотя страница после
  заявки обещает звонок специалиста, и партнёры правда звонят; клиентке,
  которой маршрут нашёлся, сказал, что «подобрать вариант не получилось»;
- пообещал клиентке «заявку аннулировала, звонить не будем», хотя отменять
  заявки и удалять номера из чата нельзя;
- на «вовремя оплачиваю» переспросил про просрочку как про непонятный ответ;
- сказал «про доход уже спрашивала», хотя спрашивал не про доход.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import core
import prompts
from geo import enrichment
from interpretation import fastpath
from routing.normalize import parse_enum, parse_enum_exact
from utils import guard_bubbles


@pytest.fixture(scope="module")
def matrix():
    return core.get_matrix()


# --------------------------------------------------------------------------- #
# 1. «N-комнатная квартира» — квартира, а не комната.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "answer",
    [
        "Квартира 4 комнатная",
        "4-комнатная квартира",
        "2х.комнатная квартира",
        "3 х комнатная квартира",
        "двухкомнатная квартира",
        "трёхкомнатная",
        "квартира однокомнатная",
        "двушка",
        "Двушка и гараж в Яровом",
        "однушка в Туле",
        "трешка",
        "студия",
    ],
)
def test_room_count_describes_a_flat(answer):
    assert parse_enum("assets.realty.kind", answer) == "flat"
    assert parse_enum("assets.realty_2.kind", answer) == "flat"


@pytest.mark.parametrize(
    "answer, expected",
    [
        # Комната в многокомнатной квартире — по-прежнему комната: залог её
        # не принимает, и выдавать её за квартиру — ложные данные в CRM.
        ("комната в трёхкомнатной квартире", "room"),
        ("комната", "room"),
        ("доля в двухкомнатной квартире", "other"),
        ("квартира", "flat"),
    ],
)
def test_a_real_room_or_share_is_not_turned_into_a_flat(answer, expected):
    assert parse_enum("assets.realty.kind", answer) == expected


def test_short_room_count_answer_is_parsed_without_the_model():
    assert parse_enum_exact("assets.realty.kind", "двушка") == "flat"


# --------------------------------------------------------------------------- #
# 2. «Крым» — это Республика Крым, а не село в Ростовской области.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "said",
    ["Крым", "крым", "в Крыму", "Крыму", "Республика Крым", "полуостров Крым", "РК Крым"],
)
def test_crimea_is_looked_up_as_the_republic(said):
    scope = enrichment._SCOPES[0]
    facts = {"client": {"region": said}}

    assert enrichment._location_query(facts, scope) == "Республика Крым"


def test_a_crimean_city_keeps_its_own_name():
    scope = enrichment._SCOPES[0]
    facts = {"client": {"city": "Щелкино", "region": "Крым"}}

    assert enrichment._location_query(facts, scope) == "Щелкино, Республика Крым"


@pytest.mark.parametrize("city", ["Крымск", "Крымская", "Крым Мясниковского района"])
def test_similar_names_are_not_taken_for_the_republic(city):
    """Крымск — город в Краснодарском крае; алиас только на голое «Крым»."""
    scope = enrichment._SCOPES[0]
    facts = {"client": {"city": city}}

    assert enrichment._location_query(facts, scope) == city


def test_village_named_crimea_with_its_region_is_left_alone():
    """«Село Крым, Ростовская область» — редкий, но настоящий адрес."""
    scope = enrichment._SCOPES[0]
    facts = {"client": {"city": "село Крым", "region": "Ростовская область"}}

    assert enrichment._location_query(facts, scope) == "село Крым, Ростовская область"


# --------------------------------------------------------------------------- #
# 3. После заявки бот не отрицает звонок и не выдумывает исход.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bubble",
    [
        "Звонков не будет, всё уже в чате.",
        "Настя, звонки не планируются — всё, что нужно, уже здесь.",
        "Дополнительных звонков не будет, вся информация по ссылке.",
        "Никто звонить не будет.",
        "Это автоматический текст на странице, я на него повлиять не могу.",
        "Это стандартная формулировка на странице.",
    ],
)
def test_denying_the_call_never_reaches_the_client(bubble):
    kept = guard_bubbles([bubble], conversation_over=True)

    assert bubble not in kept
    assert kept, "клиент не должен остаться без ответа"
    for text in kept:
        lowered = text.lower()
        assert "не будет" not in lowered
        assert "автоматическ" not in lowered


def test_post_lead_instructions_neither_promise_nor_deny_the_call():
    for instruction in (
        prompts.POST_LINK_INSTRUCTION,
        prompts.SURVEY_COMPLETED_INSTRUCTION,
    ):
        assert prompts.AFTER_LEAD_QUESTIONS in instruction
    rule = prompts.AFTER_LEAD_QUESTIONS.lower()
    assert "не говори, что звонков не будет" in rule
    assert "не подобран" in rule
    assert "на странице" in rule


# --------------------------------------------------------------------------- #
# 4. Отменить заявку или удалить номер из чата нельзя — и бот так и говорит.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bubble",
    [
        "Заявку на рефинансирование отменю, звонить не будем.",
        "Спасибо, Александра. Заявку по этому номеру аннулирую, звонить не будем.",
        "Точно, Александра. Заявку аннулировала, звонков по ней не будет.",
        "Номер из базы удалю.",
        "Хорошо, уберу ваш номер из базы.",
    ],
)
def test_promise_to_cancel_never_reaches_the_client(bubble):
    kept = guard_bubbles([bubble])

    assert bubble not in kept
    joined = " ".join(kept).lower()
    assert "отменить" in joined, "запасная фраза честно говорит, что отменить отсюда нельзя"
    for claim in ("аннулир", "отменю", "удалю", "уберу"):
        assert claim not in joined


def test_cancel_rule_is_in_the_system_prompt_and_existing_branch():
    for text in (prompts.CONVERSATION_SYSTEM_PROMPT, prompts.EXISTING_CLIENT_INSTRUCTION):
        assert prompts.CANCEL_REQUEST_RULE in text
    rule = prompts.CANCEL_REQUEST_RULE.lower()
    assert "не проси номер" in rule
    assert "отменить" in rule


# --------------------------------------------------------------------------- #
# 5. «Вовремя оплачиваю» — это «просрочки нет».
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "answer",
    [
        "Вовремя оплачиваю",
        "плачу вовремя",
        "плачу без задержек",
        "не просрочиваю",
        "никогда не просрочивала",
        "плачу исправно",
    ],
)
def test_paying_on_time_means_no_overdue(answer, matrix):
    assert parse_enum("debts.overdue_duration", answer) == "no_overdue"
    assert parse_enum("debts.credit_history", answer) == "no_overdue"
    single = fastpath.parse_expected_answer(answer, ["debts.overdue_duration"], matrix)
    assert single == [
        {"path": "debts.overdue_duration", "value": "no_overdue", "confidence": "high"}
    ]
    pair = fastpath.parse_expected_answer(
        answer, ["debts.total", "debts.overdue_duration"], matrix
    )
    assert pair == [
        {"path": "debts.overdue_duration", "value": "no_overdue", "confidence": "high"}
    ]


@pytest.mark.parametrize(
    "answer",
    [
        "не всегда плачу вовремя",
        "не плачу вовремя",
        "раньше были просрочки, сейчас плачу вовремя",
        "плачу вовремя, долг 45000",
    ],
)
def test_on_time_with_a_catch_is_left_to_the_model(answer):
    assert parse_enum("debts.overdue_duration", answer) != "no_overdue"


# --------------------------------------------------------------------------- #
# 6. «Уже спрашивала» — только если вопрос правда звучал.
# --------------------------------------------------------------------------- #
def _plan_with(attempt: int):
    field = SimpleNamespace(label="официальный доход", attempt=attempt, needs_confirmation=False)
    return SimpleNamespace(fields=[field])


def test_repeat_note_asks_the_model_to_check_the_history():
    """Иван 28.09: попытку засчитали за вопрос из плана, а модель спросила
    «оформлены официально?». Следующий ход: «про доход уже спрашивала» —
    неправда. Признавать повтор можно, только сверившись с историей."""
    text = prompts._confirmation_block(_plan_with(1)).lower()

    assert "только если" in text
    assert "в истории" in text
    assert "просто задай вопрос" in text


def test_first_time_question_has_no_repeat_note():
    assert prompts._confirmation_block(_plan_with(0)) == ""


# --------------------------------------------------------------------------- #
# Срок просрочки отдельным вопросом: короткие ответы — без модели.
# Прод 30.09.2026, лид 3508342.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "answer, bucket",
    [
        ("Просрочек нет", "no_overdue"),
        ("просрочки нет", "no_overdue"),
        ("нет просрочек", "no_overdue"),
        ("3 дня", "up_to_1m"),
        ("2 месяца", "from_1m_to_3m"),
        ("1–3 месяца", "from_1m_to_3m"),
        ("1-3 месяца", "from_1m_to_3m"),
        ("3-6 месяцев", "from_3m_to_6m"),
        ("Больше 3 месяцев", "from_3m_to_6m"),
        ("полгода", "from_3m_to_6m"),
        ("год", "over_6m"),
    ],
)
def test_overdue_asked_alone_is_parsed_without_the_model(answer, bucket, matrix):
    assert fastpath.parse_expected_answer(answer, ["debts.overdue_duration"], matrix) == [
        {"path": "debts.overdue_duration", "value": bucket, "confidence": "high"}
    ]


@pytest.mark.parametrize("answer", ["1300000", "Оператор", "3", "не помню"])
def test_not_an_overdue_answer_is_left_to_the_model(answer, matrix):
    """«1300000» на вопрос о просрочке — поправка суммы, а не срок."""
    assert fastpath.parse_expected_answer(answer, ["debts.overdue_duration"], matrix) is None


@pytest.mark.parametrize(
    "answer, bucket",
    [("От 1 до 3 месяцев", "from_1m_to_3m"), ("от 3 до 6 месяцев", "from_3m_to_6m")],
)
def test_long_form_of_the_range_is_still_understood(answer, bucket):
    """Длинная запись — дело модели, но её значение нормализатор знает."""
    assert parse_enum("debts.overdue_duration", answer) == bucket


@pytest.mark.parametrize(
    "answer, bucket",
    [
        ("Да, около полугода.", "from_3m_to_6m"),
        ("да, 3 дня", "up_to_1m"),
        ("Есть, два месяца", "from_1m_to_3m"),
        ("да, больше года", "over_6m"),
    ],
)
def test_yes_with_a_duration_answers_the_overdue_question(answer, bucket, matrix):
    """Вопрос звучит «есть ли просрочка и как давно» — «да, около полугода»
    отвечает на обе половины. Мутационный прогон 01.10.2026: ответ ушёл модели,
    и она сочинила «просрочка непрерывная или с перерывами?»."""
    assert fastpath.parse_expected_answer(answer, ["debts.overdue_duration"], matrix) == [
        {"path": "debts.overdue_duration", "value": bucket, "confidence": "high"}
    ]


def test_bare_yes_to_the_overdue_question_is_not_a_duration(matrix):
    assert fastpath.parse_expected_answer("Да", ["debts.overdue_duration"], matrix) is None


def test_fallback_extractor_knows_a_room():
    """Мутационный прогон 01.10.2026: «комната в коммуналке» в длинной реплике
    записалась как «другое». Новый разборщик на таких репликах отказывает
    (лимиты ответа), ход уходит в запасной, а в его словаре типа «комната» не
    было — хотя матрица и нормализатор его знают с 12.08.2026."""
    assert '"room"' in prompts.EXTRACTION_SYSTEM_PROMPT
    assert "коммуналке" in prompts.EXTRACTION_SYSTEM_PROMPT
