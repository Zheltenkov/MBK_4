"""The legacy voice path audits applicable facts before disclosure or first-safe reuse."""
from dataclasses import replace

import pytest

import core
import llm_agent
import policy_factual_audit as factual
import policy_voice
import policy_voice_critic
from dialogue_frame import DialogueFrame
from tests.test_policy_v31_receipts import held_state, offline  # noqa: F401
from tests.test_policy_grounded_voice import strict_settings


BAD = "Реструктуризация всегда уменьшает итоговую сумму выплат."
GOOD = "При реструктуризации меняют условия действующего договора."


def install_audit(monkeypatch, replies, verdicts):
    generations, audits = [], []
    reply_iter, verdict_iter = iter(replies), iter(verdicts)

    def generate(payload, config, **kwargs):
        generations.append(payload)
        reply = next(reply_iter)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def assess(payload, draft, frame, config, **kwargs):
        audits.append((draft, config, kwargs))
        verdict = next(verdict_iter)
        return factual.FactualAssessment(verdict, "synthetic-factual", verdict or "invalid_schema", True)

    monkeypatch.setattr(llm_agent, "generate_reply", generate)
    monkeypatch.setattr(factual, "assess", assess)
    return generations, audits


def run_legacy(*, enabled=True, factual_collector=None):
    config = replace(strict_settings(), policy_semantic_enabled=False, policy_factual_audit_enabled=enabled)
    state = core.prepare_turn(held_state(), "Что такое реструктуризация?", config, submit_delivery=False)
    text = "".join(core.stream_reply(state, "Что такое реструктуризация?", config,
                                   factual_usage_collector=factual_collector))
    committed = core.commit_turn(state, "Что такое реструктуризация?", text)["current_state"]
    return text, state["_policy_spoken"], committed


def test_an_enabled_supported_candidate_has_a_separate_factual_assessment(monkeypatch):
    generations, audits = install_audit(monkeypatch, [GOOD], ["supported"])

    def collector(**kwargs):
        return None

    answer, spoken, committed = run_legacy(factual_collector=collector)
    assert answer == GOOD and len(generations) == len(audits) == 1
    assert spoken.factual_assessments[0].verdict == "supported"
    assert audits[0][2]["usage_collector"] is collector
    assert spoken.critic_assessments[0].verdict.accept and spoken.critic_calls == 1
    assert not committed.get("delivery") and committed["facts"]["debts"]["total"] == 400000


@pytest.mark.parametrize("verdict", ["unsupported", "unknown", None])
def test_a_nonapproved_first_candidate_gets_one_repair_and_is_never_first_safe(monkeypatch, verdict):
    generations, audits = install_audit(monkeypatch, [BAD, GOOD], [verdict, "supported"])
    answer, spoken, _ = run_legacy()
    assert answer == GOOD and spoken.source == "model"
    assert len(generations) == len(audits) == 2 and spoken.critic_calls <= 2
    assert [item.verdict for item in spoken.factual_assessments] == [verdict, "supported"]
    assert all(item.verdict.accept for item in spoken.critic_assessments)


@pytest.mark.parametrize("verdict", ["unsupported", "unknown", None])
def test_a_nonapproved_repair_uses_code_fallback_instead_of_the_unapproved_first_reply(monkeypatch, verdict):
    generations, audits = install_audit(monkeypatch, [BAD, BAD], [verdict, verdict])
    answer, spoken, _ = run_legacy()
    assert spoken.source == "fallback" and answer != BAD
    assert len(generations) == len(audits) == 2 and len(spoken.factual_assessments) == 2


def test_a_generation_outage_after_factual_rejection_cannot_restore_the_first_candidate(monkeypatch):
    generations, audits = install_audit(monkeypatch, [BAD, TimeoutError("synthetic outage")], ["unsupported"])
    answer, spoken, _ = run_legacy()
    assert spoken.source == "fallback" and answer != BAD
    assert len(generations) == 2 and len(audits) == 1


def test_the_factual_flag_is_default_off_and_preserves_the_previous_path(monkeypatch):
    generations, audits = install_audit(monkeypatch, [GOOD], [])
    answer, spoken, _ = run_legacy(enabled=False)
    assert answer == GOOD and len(generations) == 1 and audits == []
    assert spoken.factual_assessments == ()


def test_an_inapplicable_short_reaction_does_not_claim_factual_coverage(monkeypatch):
    generations, audits = install_audit(monkeypatch, ["Угу."], [])
    config = replace(strict_settings(), policy_semantic_enabled=False, policy_factual_audit_enabled=True)
    frame = DialogueFrame(None, "collected_held")
    spoken = policy_voice.render({"latest_user_message": "Да, Татарстан."}, config,
                                 expected=None, status=frame.status, frame=frame, fallback="Угу.")
    assert spoken.text == "Угу." and len(generations) == 1 and audits == []
    assert spoken.factual_assessments == ()


def test_hard_invalid_collection_is_rejected_before_factual_audit(monkeypatch):
    generations, audits = install_audit(monkeypatch, ["На какой номер вам позвонить?", GOOD], ["supported"])
    answer, spoken, _ = run_legacy()
    assert answer == GOOD and len(generations) == 2 and len(audits) == 1
    assert audits[0][0] == GOOD and spoken.factual_assessments[0].verdict == "supported"


def test_an_approved_first_reply_survives_an_unapproved_optional_style_revision(monkeypatch):
    generations, audits = install_audit(monkeypatch, [GOOD, BAD], ["supported", "unsupported"])
    monkeypatch.setattr(policy_voice_critic, "assess", lambda *args, **kwargs: policy_voice_critic.CriticAssessment(
        policy_voice_critic.SemanticVerdict(accept=False, flags=["wrong_current_turn"]),
        "synthetic-critic", "semantic_rejection", True,
    ))
    answer, spoken, _ = run_legacy()
    assert answer == GOOD and spoken.source == "model"
    assert len(generations) == len(audits) == 2
    assert [item.verdict for item in spoken.factual_assessments] == ["supported", "unsupported"]


def test_a_late_factual_approval_cannot_be_used_after_the_shared_deadline(monkeypatch):
    generations, _ = install_audit(monkeypatch, [GOOD], [])
    clock = [100.0]
    monkeypatch.setattr(policy_voice.time, "monotonic", lambda: clock[0])

    def late_assess(*args, **kwargs):
        clock[0] = 141.0
        return factual.FactualAssessment("supported", "synthetic-factual", "supported", True)

    monkeypatch.setattr(factual, "assess", late_assess)
    answer, spoken, _ = run_legacy()
    assert spoken.source == "fallback" and answer != GOOD and len(generations) == 1
    assert spoken.factual_assessments[0].verdict is None
    assert spoken.factual_assessments[0].reason == "deadline_exhausted"
