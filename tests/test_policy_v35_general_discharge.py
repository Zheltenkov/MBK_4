"""A general statutory possibility is distinct from an individual promised result."""
import pytest
from dataclasses import replace

import core
from dialogue_frame import DialogueFrame
import policy_factual_audit
import policy_voice
from tests.test_policy_grounded_voice import install, semantic_raw, strict_settings
from tests.test_policy_v31_receipts import held_state, offline  # noqa: F401


@pytest.mark.parametrize("text", [
    "При соблюдении условий закона кредитный долг может быть списан полностью.",
    "Кредитные долги могут быть списаны по правилам закона.",
    "При реструктуризации сам долг остаётся. Кредитный долг может быть списан полностью, "
    "но алименты и возмещение вреда жизни или здоровью сохраняются. "
    "Доступность процедуры и результат зависят от условий закона.",
])
def test_generic_conditional_explanations_pass_the_outcome_guard(text):
    frame = DialogueFrame(None, "collected_held", question_goal="debt_explanation")
    assert policy_voice.validate_reply(text, None, frame=frame)


@pytest.mark.parametrize("text", [
    "Ваш долг может быть списан полностью.",
    "Долг будет списан полностью.",
    "Долги списаны полностью.",
    "Кредитный долг может быть списан полностью, это гарантировано.",
    "Кредитный долг может быть списан, вам обязательно перезвонят.",
    "Кредитный долг может быть списан, я отправлю ваши данные.",
    "Кредитный долг может быть списан, но возмещение вреда всё равно сохраняется.",
    "Кредитор подтвердил, кредитный долг может быть списан полностью.",
    "Кредитный долг может быть списан только если у человека нет дохода.",
    "Кредитный долг может быть списан после оплаты нашей комиссии.",
    "Все долги могут быть списаны полностью.",
])
def test_possibility_does_not_authorize_personal_outcomes_other_claims_or_bad_scope(text):
    assert not policy_voice.validate_reply(
        text, None, frame=DialogueFrame(None, "collected_held", question_goal="debt_explanation"),
    )


def test_the_general_debt_explanation_exception_does_not_extend_to_onloader():
    assert not policy_voice.validate_reply(
        "Кредитный долг может быть списан полностью.", None,
        frame=DialogueFrame(None, "awaiting_consent", mode="onloader"),
    )


@pytest.mark.parametrize("verdict", ["supported", None])
def test_the_actual_grounded_turn_still_requires_a_factual_decision(monkeypatch, verdict):
    text = "При соблюдении условий закона кредитный долг может быть списан полностью."
    install(monkeypatch, [text, text], [semantic_raw(), semantic_raw()])
    audits = []

    def assess(payload, draft, frame, config, **kwargs):
        audits.append(draft)
        return policy_factual_audit.FactualAssessment(
            verdict, "synthetic-factual", "supported" if verdict else "model_unavailable", True,
        )

    monkeypatch.setattr(policy_factual_audit, "assess", assess)
    config = replace(strict_settings(), policy_factual_audit_enabled=True)
    initial = held_state()
    user = "Что такое списание?"
    prepared = core.prepare_turn(initial, user, config, submit_delivery=False)
    answer = "".join(core.stream_reply(prepared, user, config))
    spoken = prepared["_policy_spoken"]
    committed = core.commit_turn(prepared, user, answer)["current_state"]
    assert audits and all(draft == text for draft in audits)
    assert (answer == text) is (verdict == "supported")
    assert spoken.source == ("model" if verdict == "supported" else "fallback")
    assert committed["facts"] == initial["facts"] and not committed.get("delivery")
