"""Incoming authority and outgoing claims stay separate; supplied C is fallible."""
from __future__ import annotations

from dataclasses import replace
import json

import pytest

from dialogue_frame import DialogueFrame
import policy_semantics as semantics
from tests.test_policy_semantics import wire, claim


YES = "Да, согласен на передачу специалисту. Телефон я уже оставил выше."
WRONG_NO = {"kind": "decline_transfer", "evidence": "Да, согласен на передачу специалисту.",
            "subject": "client", "scope": "current_offer", "mode": "asserted"}


def debt_frame(**changes: object) -> DialogueFrame:
    return replace(DialogueFrame(None, "collected_held", known_fields=frozenset({
        "intent", "client.name", "client.phone", "client.region_class", "debts.total"}),
        transfer_blocked=True), **changes)


def evaluate(body: dict[str, object], draft: str, *, frame: DialogueFrame | None = None,
             latest: str = YES) -> semantics.SemanticEvaluation:
    selected = frame or debt_frame()
    parsed = semantics.parse_semantics(json.dumps(body, ensure_ascii=False), latest_user_message=latest, draft=draft)
    return semantics.evaluate_semantics(semantics.TurnAuthority.from_frame(selected, latest, draft), parsed)


@pytest.mark.parametrize("draft", [
    "Можем продолжить разговор здесь.",
    "Передача заявки по долгам из этого чата пока не подключена.",
    "При реструктуризации меняют условия договора.",
])
def test_wrong_No_on_yes_pauses_incoming_but_preserves_independent_safe_information(draft: str) -> None:
    original = debt_frame()
    raw = wire(acts=[{**WRONG_NO}])
    parsed = semantics.parse_semantics(json.dumps(raw), latest_user_message=YES, draft=draft)
    result = semantics.evaluate_semantics(semantics.TurnAuthority.from_frame(original, YES, draft), parsed)
    assert not result.safe and not result.user_valid and result.claims_valid
    assert result.outgoing_safe and result.claims_issues == ()
    assert result.user_issues == ("user_refusal_unbound",)
    assert result.issues == ("user_refusal_unbound",)
    assert result.effective_frame.conversation_paused
    assert not result.applied_user_acts and not result.effective_frame.conversation_declined
    assert not original.conversation_paused
    assert parsed.user is not None and parsed.user.acts[0].kind == "decline_transfer"
    # Outgoing semantic safety does not replace code/content/style/F validation.
    assert parsed.claims is not None


@pytest.mark.parametrize("fault", ["missing_user", "invented_affirmative_act", "history_evidence"])
def test_unavailable_incoming_authority_does_not_invent_permission_or_discard_valid_claims(fault: str) -> None:
    body = wire(acts=[{**WRONG_NO}])
    if fault == "missing_user":
        del body["user"]
    elif fault == "invented_affirmative_act":
        body["user"]["acts"][0]["kind"] = "accept_transfer"
    else:
        body["user"]["acts"][0]["evidence"] = "Нет из старой истории"
    result = evaluate(body, "Можем обсудить вопросы здесь.")
    assert result.outgoing_safe and not result.safe and not result.user_valid
    assert result.effective_frame.conversation_paused and not result.effective_frame.conversation_declined
    assert not result.applied_user_acts and result.claims_issues == ()
    assert result.user_issues == ("user_channel_unavailable",)


@pytest.mark.parametrize("predicate,scope", [
    ("conversation_refusal_acknowledged", "chat"),
    ("conversation_refusal_acknowledged", "current_offer"),
    ("consent_recorded", "current_offer"),
    ("refusal_recorded", "current_offer"),
    ("transfer_completed", "current_offer"),
    ("recipient_callback", "current_offer"),
])
def test_invalid_incoming_cannot_justify_acknowledgment_receipt_or_callback(predicate: str, scope: str) -> None:
    draft = "Этот результат уже подтверждён."
    result = evaluate(wire(acts=[{**WRONG_NO}], claims=[claim(predicate, evidence=draft, scope=scope)]), draft)
    assert not result.safe and not result.outgoing_safe
    assert result.claims_valid and result.claims_issues
    assert result.user_issues == ("user_refusal_unbound",)
    assert not result.applied_user_acts and not result.effective_frame.conversation_declined


@pytest.mark.parametrize("mode", ["debt", "onloader"])
@pytest.mark.parametrize("request_kind", ["request_phone", "request_transfer_consent"])
def test_incoming_pause_disables_collection_even_if_original_frame_was_awaiting(mode: str, request_kind: str) -> None:
    frame = DialogueFrame("client.phone", "collecting_contacts", mode=mode, offer_present=mode == "onloader",
                          offer_status="awaiting_consent" if mode == "onloader" else None,
                          reply_target="phone" if request_kind == "request_phone" else "transfer")
    draft = "Оставьте номер." if request_kind == "request_phone" else "Подтвердите передачу."
    body = wire(acts=[{**WRONG_NO}], speech_acts=[{"kind": request_kind, "mode": "requested", "evidence": draft}])
    result = evaluate(body, draft, frame=frame)
    assert result.effective_frame.conversation_paused
    assert not result.outgoing_safe and not result.safe
    expected = "unallowed_phone_request" if request_kind == "request_phone" else "unallowed_consent_request"
    assert expected in result.claims_issues
    assert not frame.conversation_paused


@pytest.mark.parametrize("known,refused", [(True, False), (False, True)])
def test_outgoing_phone_request_remains_blocked_for_known_or_refused_contact(known: bool, refused: bool) -> None:
    frame = DialogueFrame("client.phone", "collecting_contacts",
                          known_fields=frozenset({"client.phone"}) if known else frozenset(),
                          refused_fields=frozenset({"client.phone"}) if refused else frozenset())
    draft = "Оставьте номер."
    result = evaluate(wire(speech_acts=[{"kind": "request_phone", "mode": "requested", "evidence": draft}]),
                      draft, frame=frame)
    assert result.user_valid and not result.outgoing_safe
    assert result.user_issues == () and "unallowed_phone_request" in result.claims_issues


def test_valid_open_collection_keeps_legacy_strict_safety() -> None:
    frame = DialogueFrame("client.phone", "collecting_contacts")
    draft = "Оставьте номер."
    result = evaluate(wire(speech_acts=[{"kind": "request_phone", "mode": "requested", "evidence": draft}]),
                      draft, frame=frame)
    assert result.safe and result.outgoing_safe and not result.user_issues and not result.claims_issues


@pytest.mark.parametrize("latest,evidence", [
    ("Если не передавайте мои данные, что будет?", "не передавайте мои данные"),
    ("Он сказал: «Не передавайте мои данные». Позже решу.", "Не передавайте мои данные"),
    ("Да, согласен на передачу специалисту.", "Да, согласен на передачу специалисту."),
])
def test_positive_hypothetical_and_quoted_No_labels_do_not_become_decisions(latest: str, evidence: str) -> None:
    body = wire(acts=[{**WRONG_NO, "evidence": evidence}])
    result = evaluate(body, "Можем общаться здесь.", latest=latest)
    assert not result.user_valid and result.effective_frame.conversation_paused
    assert result.outgoing_safe and not result.safe
    assert not result.applied_user_acts and not result.effective_frame.conversation_declined


def test_grounded_own_No_remains_authoritative_and_allows_correct_acknowledgment() -> None:
    latest, draft = "Не передавайте мои данные.", "Хорошо, обсуждаем без передачи."
    body = wire(acts=[{**WRONG_NO, "evidence": latest}],
                claims=[claim("conversation_refusal_acknowledged", evidence=draft, scope="chat")])
    result = evaluate(body, draft, latest=latest)
    assert result.user_valid and result.safe and result.outgoing_safe
    assert result.effective_frame.conversation_declined and not result.effective_frame.conversation_paused
    assert len(result.applied_user_acts) == 1


def test_code_known_No_survives_bad_incoming_without_fabricating_a_new_act() -> None:
    draft = "Хорошо, обсуждаем без передачи."
    result = evaluate(wire(acts=[{**WRONG_NO}], claims=[claim("conversation_refusal_acknowledged", evidence=draft, scope="chat")]),
                      draft, frame=debt_frame(conversation_declined=True))
    assert not result.safe and result.outgoing_safe
    assert result.effective_frame.conversation_declined and result.effective_frame.conversation_paused
    assert not result.applied_user_acts


def test_private_bound_capability_denial_does_not_need_user_decision_authority() -> None:
    draft = "Передачу заявки из этого чата сейчас оформить не могу."
    body = wire(acts=[{**WRONG_NO}], claims=[claim("transfer_completed", False, evidence=draft)])
    result = evaluate(body, draft)
    assert result.outgoing_safe and not result.safe and result.effective_frame.conversation_paused
    missing_capability = evaluate(body, draft, frame=debt_frame(transfer_blocked=False))
    assert not missing_capability.outgoing_safe
    assert "unverified_transfer_completed" in missing_capability.claims_issues


@pytest.mark.parametrize("component", ["context_digest", "latest_digest", "draft_digest", "private_frame"])
def test_binding_mismatch_never_isolated_away_as_an_incoming_problem(component: str) -> None:
    frame, draft = debt_frame(), "Можем общаться здесь."
    parsed = semantics.parse_semantics(json.dumps(wire(acts=[{**WRONG_NO}])), latest_user_message=YES, draft=draft)
    authority = semantics.TurnAuthority.from_frame(frame, YES, draft)
    authority = replace(authority, frame=replace(frame, transfer_blocked=False)) if component == "private_frame" \
        else replace(authority, **{component: "not-bound"})
    result = semantics.evaluate_semantics(authority, parsed)
    assert not result.safe and not result.outgoing_safe and not result.claims_valid
    assert "context_mismatch" in result.claims_issues and "context_mismatch" in result.user_issues


@pytest.mark.parametrize("fault", ["missing_claims", "missing_slot", "wrong_evidence", "wrong_bool"])
def test_claim_schema_or_evidence_failure_still_rejects_outgoing(fault: str) -> None:
    draft = "Можем общаться здесь."
    body = wire(acts=[{**WRONG_NO}])
    if fault == "missing_claims":
        del body["claims"]
    elif fault == "missing_slot":
        del body["claims"]["predicates"]["transfer_completed"]
    else:
        body = wire(acts=[{**WRONG_NO}], claims=[claim("phone_required_to_chat", False,
                    evidence="Другой текст" if fault == "wrong_evidence" else draft)])
        if fault == "wrong_bool":
            body["claims"]["predicates"]["phone_required_to_chat"][0]["value"] = "false"
    result = evaluate(body, draft)
    assert not result.claims_valid and not result.safe and not result.outgoing_safe
    assert "claims_channel_unavailable" in result.claims_issues


def test_valid_user_No_can_commit_even_when_outgoing_claim_channel_is_missing() -> None:
    latest = "Не передавайте мои данные."
    body = wire(acts=[{**WRONG_NO, "evidence": latest}])
    del body["claims"]
    result = evaluate(body, "Можем общаться здесь.", latest=latest)
    assert result.user_valid and result.applied_user_acts and result.effective_frame.conversation_declined
    assert not result.safe and not result.outgoing_safe and result.user_issues == ()


@pytest.mark.parametrize("code_goal", [None, "debt_amount_purpose"])
def test_bad_user_channel_cannot_install_a_new_advisory_hint_or_erase_code_goal(code_goal) -> None:
    frame = debt_frame(question_goal=code_goal)
    result = evaluate(wire(acts=[{**WRONG_NO}], need={"kind": "debt_explanation", "evidence": YES}),
                      "Можем общаться здесь.", frame=frame)
    assert result.information_need is None
    assert result.effective_frame.question_goal == code_goal
    assert result.effective_frame.question_goal_origin == "code"
    assert result.effective_frame.conversation_paused
    assert not result.applied_user_acts
