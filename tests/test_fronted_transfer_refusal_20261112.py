"""An own No survives natural word order; adversarial model labels are not authority."""
import pytest

import core
import debt_policy
import llm_agent
import policy_grounded_voice
import policy_semantics
import policy_voice
from config import AppConfig
from dialogue_frame import DialogueFrame
from tests.test_policy_semantics import decline, evaluate, wire
from tests.test_policy_voice import debt_state


NO = "Передавать мои сведения консультанту не разрешаю."
FAQ = "А чем банкротство отличается от реструктуризации?"
DRAFT = "Можем обсудить вопрос здесь."


def frame() -> DialogueFrame:
    return DialogueFrame(None, "collected_held", mode="debt", known_fields=frozenset({"intent", "debts.total"}))


@pytest.mark.parametrize("message", [NO, NO + " " + FAQ,
    "Отправлять мои данные специалисту я не разрешаю.",
    "Передать свои контакты третьим лицам не разрешаю.",
    "Сейчас передавать мои контакты не разрешаю."])
def test_fronted_own_refusal_keeps_phone_and_faq_independent(message: str) -> None:
    evidence = message.split(". ")[0].rstrip(".") + "."
    body = wire(acts=[decline(evidence=evidence)],
                need={"kind": "debt_explanation", "evidence": FAQ} if FAQ in message else None)
    result = evaluate(body, frame=frame(), latest=message, draft=DRAFT)
    # A conversational No blocks transfer only, without inventing a phone refusal.
    assert result.user_valid and result.safe and result.effective_frame.conversation_declined
    assert not result.effective_frame.conversation_paused and not result.effective_frame.refused_fields
    assert [act.kind for act in result.applied_user_acts] == ["decline_transfer"]
    assert (result.information_need is not None) == (FAQ in message)
    assert policy_semantics.explicit_conversational_declines(message, frame()) == ("decline_transfer",)


@pytest.mark.parametrize("message,evidence", [
    (f"Он сказал: «{NO}»", NO),
    (f"«{NO} {FAQ}»", NO),
    ("Если передавать мои сведения консультанту не разрешаю, что будет?", "передавать мои сведения консультанту не разрешаю"),
    ("Клиент передавать мои сведения консультанту не разрешаю.", NO.removesuffix(".")),
    ("Передавать мои сведения консультанту не разрешаю?", NO.removesuffix(".") ),
    ("Передавать чужие сведения консультанту не разрешаю.", "Передавать чужие сведения консультанту не разрешаю."),
    ("Передавать мои сведения консультанту не запрещаю.", "Передавать мои сведения консультанту не запрещаю."),
    ("Передавать мои сведения консультанту не хочу запрещать.", "Передавать мои сведения консультанту не хочу запрещать."),
    ("Передавать мои сведения консультанту разрешаю, не звоните вечером.", "Передавать мои сведения консультанту разрешаю, не звоните вечером."),
])
def test_even_asserted_client_model_label_cannot_trim_context_into_own_no(message: str, evidence: str) -> None:
    result = evaluate(wire(acts=[decline(evidence=evidence)]), frame=frame(), latest=message, draft=DRAFT)
    assert not result.applied_user_acts and not result.effective_frame.conversation_declined
    assert policy_semantics.explicit_conversational_declines(message, frame()) == ()


def test_admitted_no_commits_without_consent_or_phone_refusal() -> None:
    result = evaluate(wire(acts=[decline(evidence=NO)]), frame=frame(), latest=NO, draft=DRAFT)
    spoken = policy_voice._model_reply(DRAFT, result.effective_frame, "model_semantic_checked")
    assert spoken is not None
    reply = policy_grounded_voice.GroundedReply(
        spoken=spoken, effective_frame=result.effective_frame,
        applied_user_acts=tuple(act.kind for act in result.applied_user_acts), paused=False,
    )
    state = debt_state(debts={"total": 400000})
    assert core._apply_conversation_semantics(state, reply, expected_offer=None)
    policy = debt_policy.parse_policy(state)
    assert policy is not None and policy.transfer_declined and policy.phone_collection == "open"
    assert not state.get("onloader") and state["facts"]["debts"]["total"] == 400000


def test_c_timeout_cannot_erase_fronted_no_or_turn_it_into_phone_refusal(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise TimeoutError("synthetic-C-timeout")

    monkeypatch.setattr(llm_agent, "generate_reply", lambda *args, **kwargs: DRAFT)
    monkeypatch.setattr(llm_agent, "generate_policy_critique", fail)
    config = AppConfig("synthetic-unused", model="speaker", extractor_model="checker",
                       request_timeout=40, max_tokens=2048, policy_voice_enabled=True,
                       policy_semantic_enabled=True)
    reply = policy_grounded_voice.render({"latest_user_message": NO + " " + FAQ}, config,
                                        frame=frame(), fallback=DRAFT)
    assert reply.applied_user_acts == ("decline_transfer",) and not reply.paused
    assert not reply.effective_frame.refused_fields and reply.spoken.asked_field is None
    state = debt_state(debts={"total": 400000})
    assert core._apply_conversation_semantics(state, reply, expected_offer=None)
    policy = debt_policy.parse_policy(state)
    assert policy is not None and policy.transfer_declined and policy.phone_collection == "open"
