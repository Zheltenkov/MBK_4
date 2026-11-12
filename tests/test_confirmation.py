"""Незавершаемая анкета: статус needs_confirmation и страховка по попыткам.

Инцидент 29.07.2026 (прогон 20260729_123832, refuses_exact_debt). Клиент сказал
«Просрочка около двух месяцев». Экстрактор выдал верное `from_1m_to_3m`, но
пометил оценку приблизительной; политика понизила её до ambiguous, reducer
поставил `needs_confirmation` и факт не записал.

Дальше выяснилось главное: этот статус никто не отрабатывал. План шесть ходов
подряд просил одно и то же поле, модель видела ответ в истории и вела разговор
сама. Анкета не завершилась — маршрута нет, лида нет, клиент ушёл ни с чем.

Здесь проверяется, что статус превращается в уточняющий вопрос, а если и он не
помог — анкета всё равно доходит до конца.
"""
from __future__ import annotations

import pytest

from routing import plan
from routing.planner import MAX_FIELD_ATTEMPTS


BASE = {
    "client": {"name": "Ирина", "age": 38, "region_class": "moscow_mo"},
    "request": {"amount": 300_000},
    "debts": {"has_active_credits": True, "total": 700_000},
}

STUCK = "debts.overdue_duration"


def test_without_statuses_behaviour_is_unchanged(matrix):
    """Совместимость: старые вызовы plan(matrix, facts) работают как раньше."""
    result = plan(matrix, BASE)

    assert result.field_paths == [STUCK]
    assert result.fields[0].needs_confirmation is False


def test_ambiguous_answer_turns_into_a_confirming_question(matrix):
    """Тот же вопрос, но помеченный как уточнение — не глухой повтор."""
    result = plan(
        matrix,
        BASE,
        fact_statuses={STUCK: "needs_confirmation"},
    )

    assert result.field_paths == [STUCK]
    assert result.fields[0].needs_confirmation is True


def test_confirmed_status_is_not_treated_as_ambiguous(matrix):
    result = plan(matrix, BASE, fact_statuses={STUCK: "confirmed"})

    assert result.fields[0].needs_confirmation is False


def test_field_asked_too_many_times_is_skipped(matrix):
    """Страховка: анкета не должна вставать намертво на одном поле."""
    result = plan(
        matrix,
        BASE,
        fact_statuses={STUCK: "needs_confirmation"},
        attempts={STUCK: MAX_FIELD_ATTEMPTS},
    )

    assert STUCK not in result.field_paths


def test_questionnaire_reaches_the_phone_even_if_a_field_never_lands(matrix):
    """Главное следствие инцидента: клиент обязан дойти до телефона.

    Иначе обращение не попадает ни в маршрутизацию, ни в CRM — человек просто
    исчезает, и мы об этом даже не узнаём.
    """
    facts = {
        **BASE,
        "employment": {"type": "unofficial", "income": 60_000},
        "assets": {"has_realty": False, "has_vehicle": False},
    }
    result = plan(matrix, facts, attempts={STUCK: MAX_FIELD_ATTEMPTS})

    assert result.action == "contacts"
    assert result.field_paths == ["client.phone"]


def test_attempts_below_the_limit_keep_asking(matrix):
    result = plan(matrix, BASE, attempts={STUCK: MAX_FIELD_ATTEMPTS - 1})

    assert result.field_paths == [STUCK]


@pytest.mark.parametrize("attempts", [{}, None])
def test_missing_attempts_never_skip_a_field(matrix, attempts):
    result = plan(matrix, BASE, attempts=attempts)

    assert result.field_paths == [STUCK]


def test_instruction_asks_to_narrow_down_instead_of_repeating(matrix):
    """Клиент уже ответил — повторить тот же вопрос значит не услышать его."""
    from prompts import turn_instruction

    text = turn_instruction(
        plan(matrix, BASE, fact_statuses={STUCK: "needs_confirmation"})
    )

    assert "УТОЧНИ" in text
    assert "ответил приблизительно" in text
    assert "не задавай тот же вопрос заново" in text.lower()


def test_ordinary_field_instruction_has_no_confirmation_block(matrix):
    from prompts import turn_instruction

    text = turn_instruction(plan(matrix, BASE))

    assert "УТОЧНИ" not in text


def test_repeated_question_tells_the_model_it_already_asked(matrix):
    """Клиент ответил не по делу: кандидата не было, статуса нет.

    Эвал 29.07.2026 показал, что без подсказки модель то признаёт уже
    спрошенное, то сухо повторяет вопрос раздражённому клиенту. Второе и есть
    «потупее в плане коммуникации».
    """
    from prompts import turn_instruction

    text = turn_instruction(plan(matrix, BASE, attempts={STUCK: 1}))

    assert "СПРАШИВАЕШЬ НЕ ВПЕРВЫЕ" in text
    assert "признай, что уже спрашивал" in text.lower()


def test_first_ask_has_no_repeat_block(matrix):
    from prompts import turn_instruction

    text = turn_instruction(plan(matrix, BASE))

    assert "СПРАШИВАЕШЬ НЕ ВПЕРВЫЕ" not in text
    assert "УТОЧНИ" not in text


def test_held_back_fact_keeps_its_own_wording(matrix):
    """У придержанного ответа своя формулировка: там есть что подтверждать."""
    from prompts import turn_instruction

    text = turn_instruction(
        plan(matrix, BASE, fact_statuses={STUCK: "needs_confirmation"},
             attempts={STUCK: 1})
    )

    assert "УТОЧНИ" in text
    assert "СПРАШИВАЕШЬ НЕ ВПЕРВЫЕ" not in text


def test_planned_field_carries_the_attempt_count(matrix):
    result = plan(matrix, BASE, attempts={STUCK: 2})

    assert result.fields[0].attempt == 2
