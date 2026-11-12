"""Effective content diagnostics and turn roles stay explicit at the normal entry."""
import json

from dialogue_frame import DialogueFrame
from policy_content import BANKRUPTCY_EXPLANATION
import policy_voice
import prompts
from tests import test_policy_v22_independent as entry
from tests.test_policy_voice import debt_state


def test_actual_turn_exports_code_rejection_without_relabelling_the_model(monkeypatch):
    observed = []
    execute = entry.execute_core_turn

    def observe(*args, **kwargs):
        result = execute(*args, **kwargs)
        observed.append(result.voice)
        return result

    monkeypatch.setattr(entry, "execute_core_turn", observe)
    state = debt_state(debts={"total": 400000}, client={
        "region_class": "rf_other", "name": "Тест", "phone": "+70000000401",
    })
    bad = "Алименты и возмещение вреда остаются после банкротства."
    good = "Например, сохраняются алименты и возмещение вреда жизни или здоровью."
    answer, prepared, committed, _ = entry.actual_turn(
        monkeypatch, state, "Чем реструктуризация отличается от списания?", replies=[bad, good],
    )
    assert answer == good and prepared["_policy_spoken"].source == "model"
    voice = observed[0]
    assert voice["critic_assessments"][0]["verdict"] == {"accept": True, "flags": []}
    assert voice["critic_assessments"][0]["reason"] == "accepted"
    assert voice["diagnostic_assessments"][0] == {
        "code_content_issues": ["overbroad_harm_exception"],
        "effective_verdict": {"accept": False, "flags": ["unsupported_claim"]},
    }
    assert voice["semantic_analyses"][0]["code_content_issues"] == ["overbroad_harm_exception"]
    assert voice["semantic_analyses"][0]["need_schema_issues"] == []
    assert voice["diagnostic_assessments"][1]["effective_verdict"]["accept"] is True
    assert committed["facts"] == state["facts"] and not committed.get("delivery")


def test_legacy_spoken_reply_keeps_its_existing_constructor_and_empty_diagnostic_default():
    reply = policy_voice.SpokenPolicyReply("Можем обсудить вопрос здесь.", None, "model", "synthetic")
    assert reply.diagnostic_assessments == ()


def test_deferred_field_reply_has_one_generation_goal_and_keeps_backend_capability_facts():
    instruction = prompts.policy_turn_instruction(DialogueFrame(None, "collected_held", defer_discussion_followup=True))
    assert "Последняя реплика человека уточняет анкетные сведения" in instruction
    assert "Текущая цель — содержательно продолжить обсуждение" not in instruction
    assert BANKRUPTCY_EXPLANATION not in instruction
    assert '"new_application_available": false' in instruction
    assert '"offer_link_available": false' in instruction


def test_the_critic_uses_the_same_deferred_field_reply_goal_with_the_full_factual_basis():
    messages = prompts.policy_critic_messages(
        DialogueFrame(None, "collected_held", defer_discussion_followup=True),
        draft="Хорошо.", history=[], latest_user_message="Да, Татарстан.",
    )
    task = json.loads(messages[-1]["content"])["task"]
    assert task["primary_goal"] == "respond_to_field_update"
    assert task["verified_context"]["bankruptcy_basics"] == BANKRUPTCY_EXPLANATION
