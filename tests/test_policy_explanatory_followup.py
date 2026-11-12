"""An answered knowledge question does not need a new preference collection task."""
from dataclasses import replace

import pytest

import core
import dialogue_frame
from dialogue_frame import DialogueFrame
import prompts
from tests.test_policy_v31_receipts import actual_turn, offline  # noqa: F401


BODY = "При реструктуризации меняют условия кредита. Сам долг остаётся."
PREFERENCE = "Что именно из этого вас сейчас больше волнует?"


def explanation_frame(**changes):
    return replace(DialogueFrame(None, "collected_held", question_goal="debt_explanation"), **changes)


def normalize(text, latest="Чем реструктуризация отличается от списания?", **changes):
    normalizer = getattr(dialogue_frame, "omit_explanatory_preference_followup", None)
    assert callable(normalizer)
    return normalizer(text, explanation_frame(**changes), user_message=latest)


@pytest.mark.parametrize("latest", [
    "Чем реструктуризация отличается от списания?",
    "Чем отличается банкротство от реструктуризации?",
    "В чём разница между списанием и реструктуризацией?",
    "Объясните разницу между реструктуризацией и банкротством.",
    "Что такое реструктуризация?",
])
def test_a_complete_knowledge_answer_keeps_its_body_without_a_new_preference_task(latest):
    assert normalize(BODY + "\n\n" + PREFERENCE, latest) == BODY


@pytest.mark.parametrize("latest", [
    "Объясни разницу и спроси, что мне ближе.",
    "Объясни разницу между реструктуризацией и списанием и спроси, что мне ближе.",
    "Чем реструктуризация отличается от списания и что мне выбрать дальше?",
    "Что такое реструктуризация? Я не оставлю номер.",
    "Можно ли мне банкротство?",
    "Мне тревожно из-за банкротства.",
    "Вы сказали «Чем реструктуризация отличается от списания?»",
])
def test_personal_next_steps_refusal_quotes_and_unknown_intents_preserve_the_whole_output(latest):
    text = BODY + "\n\n" + PREFERENCE
    assert normalize(text, latest) == text


@pytest.mark.parametrize("tail", [
    "Речь о банковской или судебной реструктуризации?",
    "Какая у вас точная сумма долга?",
    "На какой номер вам позвонить?",
    "Вы подтверждаете передачу?",
    "Вы спросили: «Что из этого вам ближе?»",
    "Что именно из этого вас волнует, если завтра суд?",
    "Что из этого ближе? Я уже передала вашу заявку.",
])
def test_meaningful_questions_quotes_and_mixed_clauses_are_not_deleted(tail):
    text = BODY + "\n\n" + tail
    assert normalize(text) == text


@pytest.mark.parametrize("changes", [
    {"expected": "debts.total"}, {"offer_present": True}, {"mode": "onloader"},
    {"question_goal_origin": "model_hint"}, {"conversation_declined": True},
    {"conversation_paused": True}, {"status": "collecting_debt_context"},
])
def test_other_roles_do_not_enter_the_answered_faq_cleanup(changes):
    text = BODY + "\n\n" + PREFERENCE
    assert normalize(text, **changes) == text


def test_a_question_only_candidate_never_becomes_empty():
    assert normalize(PREFERENCE) == PREFERENCE


def test_an_unanswered_clarifier_in_the_body_preserves_the_whole_candidate():
    text = "Речь о банковской или судебной реструктуризации?\n\n" + PREFERENCE
    assert normalize(text) == text


@pytest.mark.parametrize("semantic", [False, True])
def test_normal_core_entry_checks_the_cleaned_answer_and_recognizes_the_following_ack(monkeypatch, semantic):
    answer, spoken, state, calls, analyses = actual_turn(
        monkeypatch, "Чем реструктуризация отличается от списания?",
        [BODY + "\n\n" + PREFERENCE], semantic=semantic,
    )
    assert answer == BODY and len(calls) == spoken.critic_calls == 1
    if semantic:
        assert analyses[0][0] == answer
    assert state["facts"]["client"]["phone"] == "+70000000001" and not state.get("delivery")
    assert core.conversation_is_over(state, "Ага.")


@pytest.mark.parametrize("goal", ["debt_explanation", "debt_amount_purpose"])
def test_a_trusted_specific_goal_does_not_receive_a_second_generic_continuation_goal(goal):
    instruction = prompts.policy_turn_instruction(explanation_frame(question_goal=goal))
    assert "Текущая цель — содержательно продолжить обсуждение ситуации с долгами." not in instruction


def test_ordinary_discussion_retains_its_existing_goal():
    instruction = prompts.policy_turn_instruction(explanation_frame(question_goal=None))
    assert "Текущая цель — содержательно продолжить обсуждение ситуации с долгами." in instruction
