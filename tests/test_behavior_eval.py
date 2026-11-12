"""Offline contract части behavioral eval — без расходов на модель."""
from __future__ import annotations

from pathlib import Path

import pytest

import core

from evaluation.behavior import (
    BehaviorCase,
    case_coherence_turns,
    evaluate_response,
    load_cases,
    mikhail_legacy_score,
    prepare_case,
    v4_humanity_score,
)
from evaluation.coherence import dialog_coherence_score


CASES = Path(__file__).resolve().parents[1] / "evaluation" / "behavior_cases.json"


def test_behavior_cases_match_deterministic_plan():
    for case in load_cases(CASES):
        _, current_plan = prepare_case(case)
        assert current_plan.action == case.expected_action, case.id
        assert current_plan.field_paths == case.expected_fields, case.id


def test_behavior_hard_checks_reject_internal_terms_and_question_overload():
    case = load_cases(CASES)[0]
    _, failures = evaluate_response(
        case,
        "Смотрю матрицу и маршрут.\n\nКакой регион? Почему? Что ещё?",
    )
    assert any("матриц" in failure for failure in failures)
    assert any("question_count" in failure for failure in failures)


def test_behavior_hard_checks_accept_short_relevant_reply():
    case = load_cases(CASES)[0]
    bubbles, failures = evaluate_response(
        case,
        "Ирина, спокойно разберёмся.\n\nКак долго тянется просрочка по платежам?",
    )
    assert len(bubbles) == 2
    assert failures == []


def test_contact_case_is_checked_after_backend_adds_phone_request():
    case = next(case for case in load_cases(CASES) if case.id == "initial_phone_only")
    state, _ = prepare_case(case)

    committed = core.commit_turn(state, case.latest_user_message, "Давайте разберёмся.")
    response = "\n\n".join(committed["messages"])
    _, failures = evaluate_response(case, response)

    assert "телефон" in response.lower()
    assert "как к вам обращаться" not in response.lower()
    assert failures == []


def test_mikhail_legacy_score_preserves_v3_penalties():
    human_score, human_reasons = mikhail_legacy_score(
        ["Ирина, давайте спокойно разберёмся.", "В каком городе вы живёте?"]
    )
    bot_score, bot_reasons = mikhail_legacy_score(
        ["Понял. Чтобы двигаться дальше, назовите ваш регион."]
    )

    assert human_score > bot_score
    assert human_reasons == ["нет конкретики (цифр/ориентиров/названий)"]
    assert any("понял" in reason for reason in bot_reasons)
    assert any("чтобы двигаться дальше" in reason for reason in bot_reasons)


def test_case_coherence_turns_pair_history_with_the_current_reply():
    """Behavior eval — один ход, но история кейса даёт контекст для связности."""
    case = load_cases(CASES)[0]
    turns = case_coherence_turns(case, ["Ответ бота."])

    assert turns[-1].user == case.latest_user_message
    assert turns[-1].bubbles == ["Ответ бота."]
    assert all(turn.user for turn in turns)


def test_behavior_case_history_catches_tone_mismatch():
    """Тот же дефект ловится и в одноходовом harness, если беда была в истории."""
    original = load_cases(CASES)[0]
    case = BehaviorCase.model_validate(
        {
            **original.model_dump(),
            "history": [
                {"role": "user", "content": "Я уже не справляюсь с долгами."},
                {"role": "assistant", "content": "Понимаю, разберёмся."},
            ],
            "latest_user_message": "Ирина.",
        }
    )
    turns = case_coherence_turns(case, ["Ирина, супер. Идём дальше."])
    score, reasons = dialog_coherence_score(turns)

    assert score < 100
    assert any("супер" in reason.lower() for reason in reasons)


def test_v4_humanity_does_not_require_product_or_bank_specifics():
    score, reasons = v4_humanity_score(
        ["Ирина, давайте спокойно разберёмся.", "В каком городе вы живёте?"]
    )

    assert score == 100
    assert reasons == []


def test_canned_phrase_penalty_targets_the_receipt_not_the_word():
    """Запрет писался против пузыря-квитанции, а срабатывал подстрокой.

    На проде 29.07.2026 бот сказал «Павел, тип жилья я понял, но мне нужно
    именно место нахождения квартиры» — осмысленное признание уже известного,
    ровно то поведение, которого добивались. Скорер снял за него 10 баллов,
    а корпус в тот же момент требовал признания. Две проверки разошлись во
    мнении об одном предложении.
    """
    receipt, receipt_reasons = v4_humanity_score(["Понял. Идём дальше."])
    meaningful, meaningful_reasons = v4_humanity_score(
        ["Павел, тип жилья я понял, но мне нужно именно место нахождения квартиры."]
    )

    assert any("понял" in reason for reason in receipt_reasons)
    assert meaningful_reasons == []
    assert meaningful > receipt


@pytest.mark.parametrize(
    "bubble",
    [
        "Понял.",
        "Понял вас, идём дальше.",
        "Павел, понял.",
        "Я записал.",
        "Отлично, записал ваш ответ.",
        "Зафиксировал, спасибо.",
        # Квитанция с объектом впереди — та же протокольная реакция на ответ.
        "Сумму зафиксировал.",
        "Сумму принял, давайте разберёмся.",
    ],
)
def test_receipt_openings_are_still_penalised(bubble):
    """Квитирование каждого ответа — главная жалоба заказчика на машинный тон."""
    _, reasons = v4_humanity_score([bubble])

    assert any("шаблонная формулировка" in reason for reason in reasons)


@pytest.mark.parametrize(
    "bubble",
    [
        # Признание РАНЕЕ известного, чтобы объяснить повтор вопроса, — это
        # то поведение, которого мы добиваемся, а не квитанция на свежий ответ.
        "Павел, тип жилья я понял, но нужен регион.",
        "Про работу я понял, это не проблема.",
    ],
)
def test_meaningful_use_of_the_same_word_is_free(bubble):
    _, reasons = v4_humanity_score([bubble])

    assert reasons == []


def test_legacy_comparator_keeps_its_original_substring_rule():
    """mikhail_legacy_score сравнивает с эталоном 94.1 и меняться не должен.

    Если унифицировать его с новым правилом, старые числа перестанут быть
    сопоставимыми, и весь смысл comparator'а пропадёт.
    """
    _, reasons = mikhail_legacy_score(
        ["Павел, тип жилья я понял, но нужен регион."]
    )

    assert any("понял" in reason for reason in reasons)
