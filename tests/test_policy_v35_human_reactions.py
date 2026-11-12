"""Brief reactions address the person while receipt recognition stays whole-act and bounded."""
import pytest

import prompts
from tests.test_policy_v31_receipts import actual_turn, offline  # noqa: F401
from tests.test_policy_v34_brief_render import http_turn


@pytest.mark.parametrize("semantic", [False, True])
@pytest.mark.parametrize("receipt", [
    "Приняла, Татарстан.",
    "Татарстан, приняла.",
    "Записала — Татарстан.",
])
def test_receipt_punctuation_is_symmetric_for_both_word_orders(monkeypatch, semantic, receipt):
    answer, spoken, _, calls, _ = actual_turn(
        monkeypatch, "Да, Татарстан.", [receipt, "Понимаю."], semantic=semantic,
    )
    expected = "Записала." if receipt.startswith("Записала") else "Приняла."
    assert answer == expected and len(calls) == spoken.critic_calls == 1
    expected_style = ("unrequested_receipt",) if expected == "Записала." else ()
    assert spoken.diagnostic_assessments[0].code_style_issues == expected_style
    assert spoken.asked_field is None and spoken.source == "model"


@pytest.mark.parametrize("meaningful", [
    "Приняла решение сначала разобраться в ситуации.",
    "Понимаю, что это непросто.",
    "Приняла, что вы пока не хотите звонков.",
    "Татарстан — понимаю, это непросто.",
    "Спасибо за доверие.",
    "Угу.",
])
def test_meaningful_support_unknown_objects_and_free_short_nods_are_preserved(monkeypatch, meaningful):
    answer, spoken, _, calls, _ = actual_turn(monkeypatch, "Да, Татарстан.", [meaningful] * 2)
    assert answer == meaningful and len(calls) == 1
    assert not any(item.code_style_issues for item in spoken.diagnostic_assessments)


def test_a_requested_confirmation_keeps_the_same_punctuated_response(monkeypatch):
    receipt = "Приняла, Татарстан."
    answer, _, _, calls, _ = actual_turn(monkeypatch, "Татарстан правильно записали?", [receipt] * 2)
    assert answer == receipt and len(calls) == 1


@pytest.mark.usefixtures("real_policy_voice_critic")
def test_the_actual_brief_http_job_addresses_mutual_understanding_in_the_existing_conversation(monkeypatch):
    result, generations, _ = http_turn(monkeypatch, semantic=True, first_region=True, replies=("Понимаю.",))
    job = prompts.policy_brief_reaction_prompt().rsplit("\n\n", 1)[-1]
    assert "регион" not in job.casefold()
    assert "поняли друг друга" in job
    system = generations[0]["messages"][0]["content"]
    assert job in system and prompts.CONVERSATION_VOICE in system and prompts.CONVERSATION_IDENTITY in system
    assert "Казань" not in generations[0]["messages"][-1]["content"]
    assert result.bubbles == ["Понимаю."] and not result.state.get("delivery")
