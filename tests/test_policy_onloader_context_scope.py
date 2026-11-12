"""Current onloader context narrows the legacy blanket description of capabilities."""
import pytest

from dialogue_frame import DialogueFrame
import llm_agent
import prompts


@pytest.mark.parametrize("present,status", [(True, "awaiting_consent"), (False, "pending")])
def test_current_context_instruction_is_scoped_to_explicitly_available_fields_and_card(present, status):
    frame = DialogueFrame(None, status, mode="onloader", offer_present=present,
                          offer_status=status, known_fields=frozenset({"client.phone"}))
    instruction = prompts.policy_turn_instruction(frame)
    assert "доступны лишь явно переданные сведения и карточка, когда она есть" in instruction
    assert "Не отрицай доступные сведения" in instruction
    assert "Другие заявки, полная история CRM и статусы рассмотрения" in instruction
    messages = llm_agent._conversation_messages({"facts": {}, "turn_instruction": instruction,
                                                 "latest_user_message": "Зачем нужны сведения?"})
    assert messages[2] == {"role": "system", "content": instruction}
    assert messages[0]["content"] == prompts.CONVERSATION_SYSTEM_PROMPT + "\n\n" + prompts.STYLE_EXAMPLES
