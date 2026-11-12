"""Generation retains source scope before its separate factual assessment."""
import pytest

from dialogue_frame import DialogueFrame
from policy_content import BANKRUPTCY_EXPLANATION
import prompts


@pytest.mark.parametrize("goal", [None, "debt_explanation"])
def test_explicit_and_spontaneous_legal_explanations_keep_qualifiers_when_shortened(goal):
    instruction = prompts.policy_turn_instruction(
        DialogueFrame(None, "collected_held", question_goal=goal),
    )
    assert BANKRUPTCY_EXPLANATION in instruction
    assert "Сохраняй объект утверждения, условия и уточнения вида обязательства" in instruction
    assert "Выбери из источника сведения, отвечающие на текущий вопрос" in instruction


def test_a_plain_completed_region_reply_does_not_start_a_legal_explanation():
    frame = DialogueFrame(None, "collected_held",
                          known_fields=frozenset({"client.region_class"}),
                          plain_reply_fields=frozenset({"client.region_class"}))
    instruction = prompts.policy_turn_instruction(frame)
    assert BANKRUPTCY_EXPLANATION not in instruction
    assert "Сохраняй объект утверждения, условия и уточнения вида обязательства" not in instruction
