"""Generation, diagnosis and repair share the completed-intake capability facts."""
import json

from dialogue_frame import DialogueFrame
import prompts


def test_completed_debt_intake_exposes_only_the_existing_chat_continuation():
    frame = DialogueFrame(None, "collected_held")
    instruction = prompts.policy_turn_instruction(frame)
    encoded = instruction.split("Подтверждённое состояние текущего обращения\n", 1)[1]
    capabilities, _ = json.JSONDecoder().raw_decode(encoded)
    assert capabilities == {
        "intake_complete": True,
        "available_actions": ["discuss_debt_in_this_chat"],
        "new_application_available": False,
        "offer_link_available": False,
        "application_required_for_chat_explanation": False,
    }


def test_a_real_offer_and_pending_intake_keep_their_own_capabilities():
    frames = [DialogueFrame("client.phone", "collecting_contacts"),
              DialogueFrame(None, "awaiting_consent", mode="onloader", offer_present=True,
                            offer_status="awaiting_consent")]
    for frame in frames:
        assert "Подтверждённое состояние текущего обращения" not in prompts.policy_turn_instruction(frame)


def test_the_critic_receives_the_same_current_capability_limits():
    messages = prompts.policy_critic_messages(
        DialogueFrame(None, "collected_held"), draft="Вопросы можно обсудить здесь.",
        history=[], latest_user_message="Как оформить заявку?",
    )
    task = json.loads(messages[-1]["content"])["task"]
    assert task["primary_goal"] == "continue_debt_discussion"
    assert "current_debt_continuation" in task["verified_context"]
    assert "уже заполнена" in task["verified_context"]["current_debt_continuation"]


def test_repair_instructions_use_the_actual_capability_failure_without_a_response_template():
    frame = DialogueFrame(None, "collected_held")
    repair = prompts.policy_regeneration_instruction(frame, ("unavailable_debt_continuation",))
    assert "unavailable_debt_continuation" in repair
    assert "Сведения для текущей анкеты уже собраны" in repair
    assert "new_application_available" in repair
