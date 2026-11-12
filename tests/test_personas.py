"""Роли живого прогона: уникальны, и у веток развилки свои правила успеха."""
from __future__ import annotations

from evaluation.personas import PERSONAS, persona_by_id


def test_persona_ids_are_unique():
    ids = [persona.id for persona in PERSONAS]

    assert len(ids) == len(set(ids))


def test_branch_personas_do_not_expect_a_questionnaire():
    """Ветки анкету не ведут: «не дошла до конца» для них — правильное поведение."""
    for persona_id in ("existing_client_status", "off_topic_other_bank"):
        persona = persona_by_id(persona_id)
        assert persona.expects_completion is False
        assert any("обращаться" in phrase for phrase in persona.forbidden_any)


def test_return_to_the_questionnaire_is_checked_to_the_end():
    persona = persona_by_id("off_topic_then_money")

    assert persona.expects_completion is True
    assert persona.facts["request"]["amount"] == 150_000


def test_existing_client_role_forbids_asking_for_a_phone():
    """Роль уже оставляла заявку — не просим номер повторно."""
    persona = persona_by_id("existing_client_status")
    assert "оставьте номер" in persona.forbidden_any
    assert "какой номер телефона" in persona.forbidden_any


def test_greeting_persona_starts_with_a_bare_greeting():
    persona = persona_by_id("greeting_then_existing")

    assert persona.expects_completion is False
    assert "«Здравствуйте»" in persona.story
    assert "как к вам обращаться" in persona.forbidden_any


def test_chip_roles_start_with_the_exact_chip_text():
    """Роль «нажимает кнопку»: первая реплика — ровно текст кнопки."""
    import quick_replies

    for persona_id, text in (
        ("chip_money", "Получить деньги"),
        ("chip_other_question", "Другой вопрос"),
    ):
        persona = persona_by_id(persona_id)
        assert f"ровно «{text}»" in persona.story
        assert quick_replies.chip_choice(text) is not None


def test_money_chip_role_goes_through_the_questionnaire():
    persona = persona_by_id("chip_money")

    assert persona.expects_completion is True
    assert persona.facts["request"]["amount"] == 300_000


def test_other_question_chip_role_is_not_a_questionnaire():
    persona = persona_by_id("chip_other_question")

    assert persona.expects_completion is False
    assert "как к вам обращаться" in persona.forbidden_any
    assert "какая сумма" in persona.forbidden_any
