"""The field-update role keeps brief conversational speech available."""
import json

import pytest

from dialogue_frame import DialogueFrame
from policy_content import BANKRUPTCY_EXPLANATION
import prompts


@pytest.mark.parametrize("deferred", [False, True])
def test_completed_field_update_allows_a_short_reply_without_a_required_new_explanation(deferred):
    frame = DialogueFrame(None, "collected_held", defer_discussion_followup=deferred)
    instruction = prompts.policy_turn_instruction(frame)
    assert "Короткий разговорный отклик уместен" in instruction
    assert "Служебный отчёт о принятом поле" in instruction
    assert "Если нового вопроса нет, достаточно одного полезного пояснения" not in instruction
    assert '"new_application_available": false' in instruction


def test_the_critic_field_update_role_does_not_require_a_receipt():
    messages = prompts.policy_critic_messages(
        DialogueFrame(None, "collected_held", defer_discussion_followup=True),
        draft="Да, верно.", history=[], latest_user_message="Да, Татарстан.",
    )
    task = json.loads(messages[-1]["content"])["task"]
    assert task["primary_goal"] == "respond_to_field_update"
    assert task["verified_context"]["bankruptcy_basics"] == BANKRUPTCY_EXPLANATION


def test_receipt_repair_preserves_the_option_of_a_brief_conversational_response():
    instruction = prompts.policy_regeneration_instruction(
        DialogueFrame(None, "collected_held"), ("unrequested_receipt",),
    )
    assert "Короткий естественный отклик допустим" in instruction
    assert "без обязательного нового пояснения или приглашения продолжить" in instruction
