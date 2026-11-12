"""Categorical eligibility shortcuts and partial-only discharge are not verified facts."""
import pytest

from dialogue_frame import DialogueFrame
from policy_content import BANKRUPTCY_EXPLANATION, content_issues, corrective_facts
import policy_voice
import prompts


@pytest.mark.parametrize("text", [
    "Банкротство — когда платить совсем нечем.",
    "Банкротство — это когда нет дохода.",
    "Банкротство возможно только когда нечем платить.",
])
def test_zero_income_is_not_a_complete_bankruptcy_definition(text):
    assert content_issues(text, mode="debt", question_goal=None) == ("bankruptcy_income_shortcut",)


@pytest.mark.parametrize("text", [
    "Списание возможно через банкротство, но оно не автоматическое и не полное.",
    "Списание долгов бывает только частичным.",
    "Освобождение от долгов всегда частичное.",
])
def test_a_blanket_partial_only_discharge_claim_requires_repair(text):
    assert content_issues(text, mode="debt", question_goal="debt_explanation") == ("categorical_partial_discharge",)


@pytest.mark.parametrize("text", [
    "Когда нечем платить, можно обсудить банкротство.",
    "Банкротство не сводится к отсутствию дохода.",
    "Можно ли обсуждать банкротство, когда платить совсем нечем?",
    "Фразу «Банкротство — когда платить совсем нечем» нужно уточнить.",
    "Нельзя утверждать, что списание долгов бывает только частичным.",
    "Списание не означает автоматического освобождения от всех обязательств.",
    "При банкротстве можно освободиться от кредитного долга, но алименты сохраняются.",
    "Кредитный долг может быть списан полностью при соблюдении условий закона.",
    "План реструктуризации может предусматривать частичное погашение требований.",
    "Если списание в конкретном деле оказалось неполным, причины нужно разбирать отдельно.",
    "Списание не всегда частичное.",
    "Списание нельзя считать неполным во всех случаях.",
    "Списание может быть неполным.",
    "Иногда списание неполное, важны обстоятельства конкретного дела.",
    "Банкротство — когда платить совсем нечем?",
])
def test_qualified_questions_denials_and_other_legal_procedures_remain_available(text):
    assert content_issues(text, mode="debt", question_goal="debt_explanation") == ()


@pytest.mark.parametrize("issue", ["bankruptcy_income_shortcut", "categorical_partial_discharge"])
def test_new_content_repairs_share_the_verified_basis_without_a_scripted_reply(issue):
    assert corrective_facts((issue,)) == (BANKRUPTCY_EXPLANATION,)
    instruction = prompts.policy_regeneration_instruction(
        DialogueFrame(None, "collected_held"), (issue,),
    )
    assert BANKRUPTCY_EXPLANATION in instruction


def test_a_categorical_discharge_draft_is_not_hard_valid_even_when_the_style_critic_agrees():
    frame = DialogueFrame(None, "collected_held", question_goal="debt_explanation")
    assert policy_voice._model_reply("Списание долгов всегда частичное.", frame, "synthetic") is None


def test_held_debt_chat_receives_facts_even_without_a_new_legal_question():
    frame = DialogueFrame(None, "collected_held")
    assert BANKRUPTCY_EXPLANATION in prompts.policy_turn_instruction(frame)
    assert prompts.policy_verified_context(frame)["bankruptcy_basics"] == BANKRUPTCY_EXPLANATION


@pytest.mark.parametrize("text,issue", [
    ("Банкротство — когда платить совсем нечем, хотите обсудить?", "bankruptcy_income_shortcut"),
    ("Списание долгов всегда частичное, готовы поговорить?", "categorical_partial_discharge"),
])
def test_a_following_invitation_does_not_turn_a_categorical_claim_into_a_question(text, issue):
    assert content_issues(text, mode="debt", question_goal=None) == (issue,)
