"""Pure semantic contracts. Model labels are test inputs, not semantic-accuracy evidence."""
from dataclasses import replace
import json

import pytest

from dialogue_frame import DialogueFrame, debt_amount_reply_issue
from policy_semantics import (
    CLAIM_PREDICATES, COVERAGE_CATEGORIES, REQUEST_SLOT_NAMES, TurnAuthority,
    evaluate_semantics, json_envelope_kind, parse_semantics, parse_style_channel,
)


LATEST = "Ага, тогда без передачи."
DRAFT = "Хорошо, можем продолжить разговор здесь."


def offer(**changes):
    return replace(DialogueFrame(None, "onloader", mode="onloader", offer_present=True,
                                 offer_status="awaiting_consent", offer_recipient_name="Тестовая компания"), **changes)


def wire(*, acts=None, claims=None, need=None, speech_acts=None):
    predicates = dict.fromkeys(CLAIM_PREDICATES)
    for item in claims or []:
        name = item["predicate"]
        if predicates[name] is None:
            predicates[name] = []
        predicates[name].append({key: value for key, value in item.items() if key != "predicate"})
    requests = dict.fromkeys(REQUEST_SLOT_NAMES)
    for item in speech_acts or []:
        name = "phone_request" if item["kind"] == "request_phone" else "transfer_consent_request"
        if requests[name] is None:
            requests[name] = []
        requests[name].append({key: value for key, value in item.items() if key != "kind"})
    return {"user": {"acts": [] if acts is None else acts, "information_need": need},
            "claims": {"predicates": predicates, "requests": requests},
            "style": {"accept": True, "flags": []}}


def decline(**changes):
    return {"kind": "decline_transfer", "subject": "client", "scope": "current_offer", "mode": "asserted",
            "evidence": LATEST, **changes}


def claim(predicate, value=True, **changes):
    return {"predicate": predicate, "value": value, "evidence": DRAFT, "modality": "asserted",
            "scope": "current_offer", **changes}


def evaluate(body, *, frame=None, latest=LATEST, draft=DRAFT):
    parsed = parse_semantics(json.dumps(body, ensure_ascii=False), latest_user_message=latest, draft=draft)
    return evaluate_semantics(TurnAuthority.from_frame(frame or offer(), latest, draft), parsed)


def test_latest_own_no_updates_only_the_effective_frame_with_invalid_claims_independently():
    body = wire(acts=[decline()])
    body["claims"] = {"invalid": "synthetic"}
    original = offer()
    result = evaluate(body, frame=original)
    assert result.user_valid and not result.claims_valid and not result.safe
    assert result.applied_user_acts[0].kind == "decline_transfer"
    assert result.effective_frame.conversation_declined
    assert not original.conversation_declined and not hasattr(result, "decision")


@pytest.mark.parametrize("changes", [
    {"subject": "other"}, {"subject": "unknown"}, {"mode": "question"}, {"mode": "conditional"},
    {"mode": "quoted"}, {"mode": "reported"}, {"scope": "contact_collection"},
])
def test_nonasserted_other_subject_or_other_operation_never_applies_a_decline(changes):
    result = evaluate(wire(acts=[decline(**changes)]))
    assert not result.applied_user_acts and not result.effective_frame.conversation_declined


@pytest.mark.parametrize("frame", [offer(offer_present=False), offer(offer_status="queued"), offer(offer_status="stale")])
def test_new_transfer_choice_requires_the_current_awaiting_offer(frame):
    result = evaluate(wire(acts=[decline()]), frame=frame)
    assert not result.applied_user_acts and not result.effective_frame.conversation_declined


def test_a_model_cannot_return_a_formal_permission_grant():
    result = evaluate(wire(acts=[decline(kind="accept_transfer")]))
    assert not result.user_valid and result.claims_valid and not result.safe
    assert not result.applied_user_acts and result.effective_frame.conversation_paused
    assert not result.effective_frame.conversation_declined


@pytest.mark.parametrize("evidence,latest", [("Нет из истории", LATEST), ("ага", LATEST), (" ", "а б"), ("нет", "нет нет")])
def test_user_evidence_must_be_exact_unique_and_present_in_latest_only(evidence, latest):
    result = evaluate(wire(acts=[decline(evidence=evidence)]), latest=latest)
    assert not result.user_valid and not result.applied_user_acts


def test_claim_evidence_does_not_borrow_user_words_or_an_earlier_draft():
    result = evaluate(wire(acts=[decline()], claims=[claim("phone_required_to_chat", evidence=LATEST)]))
    assert result.user_valid and result.applied_user_acts and not result.claims_valid


@pytest.mark.parametrize("bad_value", ["true", 1, None])
def test_claim_booleans_are_strict_without_discarding_the_valid_user_channel(bad_value):
    result = evaluate(wire(acts=[decline()], claims=[claim("phone_required_to_chat", bad_value)]))
    assert result.user_valid and result.applied_user_acts and not result.claims_valid


@pytest.mark.parametrize("slot", CLAIM_PREDICATES)
def test_every_predicate_slot_is_required_even_when_no_claim_was_detected(slot):
    body = wire(acts=[decline()])
    del body["claims"]["predicates"][slot]
    result = evaluate(body)
    assert result.user_valid and result.applied_user_acts
    assert not result.claims_valid and not result.safe and "claims_channel_unavailable" in result.issues


@pytest.mark.parametrize("predicate", ["phone_required_to_save_chat", "phone_required_to_chat", "engineering_transfer_purpose"])
def test_closed_capability_contradictions_are_rejected_while_their_denials_are_supported(predicate):
    assert not evaluate(wire(claims=[claim(predicate)])).safe
    assert evaluate(wire(claims=[claim(predicate, False)])).safe


def test_a_condition_does_not_change_the_known_phone_storage_capability():
    result = evaluate(wire(claims=[claim("phone_required_to_save_chat", modality="conditional")]))
    assert not result.safe and "phone_required_to_save_chat" in result.issues


def test_duplicate_acts_do_not_create_multiple_applied_events():
    result = evaluate(wire(acts=[decline(), decline()]))
    assert len(result.applied_user_acts) == 1 and result.effective_frame.conversation_declined


@pytest.mark.parametrize("predicate", ["recipient_callback", "recipient_no_other_contact_source", "exclusive_recipient"])
@pytest.mark.parametrize("value", [True, False])
def test_neither_side_of_an_unverified_recipient_guarantee_becomes_a_fact(predicate, value):
    assert not evaluate(wire(claims=[claim(predicate, value)])).safe


@pytest.mark.parametrize("predicate", ["recipient_callback", "recipient_no_other_contact_source", "exclusive_recipient"])
@pytest.mark.parametrize("value", [True, False])
def test_a_condition_does_not_verify_an_unknown_recipient_capability(predicate, value):
    assert not evaluate(wire(claims=[claim(predicate, value, modality="conditional")])).safe


@pytest.mark.parametrize("predicate", ["phone_required_to_save_chat", "phone_required_to_chat", "engineering_transfer_purpose",
                                       "recipient_callback", "recipient_no_other_contact_source", "exclusive_recipient"])
@pytest.mark.parametrize("modality", ["question", "quoted"])
def test_a_question_or_quoted_example_is_not_an_asserted_dependency(predicate, modality):
    assert evaluate(wire(claims=[claim(predicate, modality=modality)])).safe


def test_a_conversational_refusal_acknowledgment_is_separate_from_a_formal_receipt():
    result = evaluate(wire(acts=[decline()], claims=[claim("conversation_refusal_acknowledged")]))
    assert result.safe and result.effective_frame.conversation_declined
    assert not evaluate(wire(acts=[decline()], claims=[claim("refusal_recorded")])).safe
    assert not evaluate(wire(claims=[claim("conversation_refusal_acknowledged")]),
                        frame=offer(conversation_paused=True)).safe


def test_a_valid_phone_decline_can_close_contact_collection_and_resume_a_paused_frame():
    frame = DialogueFrame("client.phone", "collecting_contacts", conversation_paused=True)
    latest = "Номер сообщать не буду."
    act = decline(kind="decline_phone", scope="contact_collection", evidence=latest)
    result = evaluate(wire(acts=[act]), frame=frame, latest=latest)
    assert result.user_valid and "client.phone" in result.effective_frame.refused_fields
    assert not result.effective_frame.conversation_paused
    assert not result.effective_frame.conversation_declined


@pytest.mark.parametrize("from_need", [False, True])
def test_transfer_purpose_requires_a_supported_asserted_purpose_even_without_model_need(from_need):
    need = {"kind": "transfer_purpose", "evidence": LATEST} if from_need else None
    frame = offer() if from_need else offer(question_goal="offer_information_purpose")
    missing = evaluate(wire(need=need), frame=frame)
    assert missing.safe is from_need
    assert ("consultation_purpose_missing" in missing.issues) is not from_need
    supported = evaluate(wire(need=need, claims=[claim("consultation_purpose")]), frame=frame)
    assert supported.safe and supported.effective_frame.question_goal == "offer_information_purpose"
    assert not supported.effective_frame.require_question


def test_information_needs_do_not_create_an_offer_or_switch_an_onloader_to_debt():
    result = evaluate(wire(need={"kind": "debt_explanation", "evidence": LATEST}))
    assert result.information_need is None and result.effective_frame.mode == "onloader"
    assert result.safe and result.need_error == "information_need_scope_mismatch"


@pytest.mark.parametrize("changed", ["latest", "draft", "frame"])
def test_parsed_channels_cannot_be_rebound_to_another_text_or_context(changed):
    parsed = parse_semantics(json.dumps(wire(acts=[decline()])), latest_user_message=LATEST, draft=DRAFT)
    authority = TurnAuthority.from_frame(offer(), LATEST, DRAFT)
    if changed == "latest":
        authority = replace(authority, latest_digest="0" * 64)
    elif changed == "draft":
        authority = replace(authority, draft_digest="0" * 64)
    else:
        authority = replace(authority, frame=offer(offer_recipient_name="Другой получатель"))
    result = evaluate_semantics(authority, parsed)
    assert not result.user_valid and not result.claims_valid and not result.applied_user_acts
    assert not result.effective_frame.conversation_declined


def test_duplicate_or_nonfinite_channels_fail_independently_and_style_has_its_own_parser():
    raw = json.dumps(wire(acts=[decline()]))
    duplicated_user = raw.replace('"user":', '"user": {}, "user":', 1)
    parsed = parse_semantics(duplicated_user, latest_user_message=LATEST, draft=DRAFT)
    assert parsed.user is None and parsed.claims is not None
    bad_style = raw.replace('"accept": true', '"accept": NaN')
    parsed = parse_semantics(bad_style, latest_user_message=LATEST, draft=DRAFT)
    assert parsed.user is not None and parsed.claims is not None
    with pytest.raises(ValueError, match="invalid_style_channel"):
        parse_style_channel(bad_style)
    nested = raw.replace('"kind": "decline_transfer"', '"kind": "decline_transfer", "kind": "decline_phone"')
    parsed = parse_semantics(nested, latest_user_message=LATEST, draft=DRAFT)
    assert parsed.user is None and parsed.claims is not None


def test_schema_limits_and_immutable_arrays_are_enforced():
    parsed = parse_semantics(json.dumps(wire(acts=[decline()] * 5)), latest_user_message=LATEST, draft=DRAFT)
    assert parsed.user is None and parsed.claims is not None
    parsed = parse_semantics(json.dumps(wire(acts=[decline()])), latest_user_message=LATEST, draft=DRAFT)
    assert isinstance(parsed.user.acts, tuple)
    with pytest.raises(ValueError):
        parsed.user.acts[0].kind = "decline_phone"


@pytest.mark.parametrize("raw", [" " * 16385, '{"user":' + "[" * 2000 + "0" + "]" * 2000 + "}"])
def test_oversize_or_excessively_nested_json_fails_closed_without_an_uncaught_parser_error(raw):
    parsed = parse_semantics(raw, latest_user_message=LATEST, draft=DRAFT)
    result = evaluate_semantics(TurnAuthority.from_frame(offer(), LATEST, DRAFT), parsed)
    assert not result.safe and not result.user_valid and not result.claims_valid
    assert not result.applied_user_acts and result.effective_frame.conversation_paused


@pytest.mark.parametrize("kind", ["request_phone", "request_transfer_consent"])
def test_outgoing_requests_respect_the_newly_accepted_no(kind):
    result = evaluate(wire(acts=[decline()], speech_acts=[{"kind": kind, "mode": "requested", "evidence": DRAFT}]))
    assert result.user_valid and result.applied_user_acts
    assert not result.safe and "unallowed_" + ("phone_request" if kind == "request_phone" else "consent_request") in result.issues


@pytest.mark.parametrize("mode", ["mentioned", "quoted"])
def test_a_mention_or_quoted_request_is_not_an_outgoing_action(mode):
    result = evaluate(wire(acts=[decline()], speech_acts=[{"kind": "request_phone", "mode": mode, "evidence": DRAFT}]))
    assert result.safe


def test_known_phone_and_paused_offer_cannot_be_requested_again():
    speech = {"kind": "request_phone", "mode": "requested", "evidence": DRAFT}
    assert not evaluate(wire(speech_acts=[speech]), frame=offer(known_fields=frozenset({"client.phone"}))).safe
    speech["kind"] = "request_transfer_consent"
    assert not evaluate(wire(speech_acts=[speech]), frame=offer(conversation_paused=True)).safe
    assert evaluate(wire(speech_acts=[speech])).safe


def test_speech_act_scan_cannot_be_omitted_or_borrow_evidence():
    body = wire(acts=[decline()])
    del body["claims"]["requests"]
    assert not evaluate(body).claims_valid
    body = wire(acts=[decline()], speech_acts=[{"kind": "request_phone", "mode": "requested", "evidence": LATEST}])
    result = evaluate(body)
    assert result.user_valid and result.applied_user_acts and not result.claims_valid


def test_explicit_null_slots_establish_structure_without_claiming_model_scan_completeness():
    parsed = parse_semantics(json.dumps(wire()), latest_user_message=LATEST, draft=DRAFT)
    assert parsed.claims is not None
    assert parsed.claims.claims == parsed.claims.speech_acts == ()
    assert {item.category for item in parsed.claims.coverage} == COVERAGE_CATEGORIES
    assert all(item.completed for item in parsed.claims.coverage)
    assert "coverage" not in parsed.claims.model_dump()


@pytest.mark.parametrize("slot", REQUEST_SLOT_NAMES)
def test_each_outgoing_action_slot_is_required_even_when_it_is_null(slot):
    body = wire(acts=[decline()])
    del body["claims"]["requests"][slot]
    result = evaluate(body)
    assert result.applied_user_acts and result.user_valid and not result.claims_valid


@pytest.mark.parametrize("count", [0, 4])
@pytest.mark.parametrize("group,slot", [("predicates", "recipient_callback"), ("requests", "phone_request")])
def test_a_present_slot_contains_between_one_and_three_evidence_items(group, slot, count):
    body = wire()
    item = ({"value": True, "evidence": DRAFT, "modality": "asserted", "scope": "current_offer"}
            if group == "predicates" else {"mode": "requested", "evidence": DRAFT})
    body["claims"][group][slot] = [item] * count
    assert not evaluate(body).claims_valid


def test_repeated_predicate_clauses_are_checked_separately_without_losing_a_contradiction():
    denial = "Телефон не нужен для общения."
    assertion = "Но без телефона общение невозможно."
    draft = denial + " " + assertion
    body = wire(claims=[claim("phone_required_to_chat", False, evidence=denial),
                        claim("phone_required_to_chat", True, evidence=assertion)])
    result = evaluate(body, draft=draft)
    assert result.claims_valid and not result.safe and "phone_required_to_chat" in result.issues
    parsed = parse_semantics(json.dumps(body), latest_user_message=LATEST, draft=draft)
    assert [item.value for item in parsed.claims.claims] == [False, True]
    assert isinstance(parsed.claims.predicates.phone_required_to_chat, tuple)


def test_a_quoted_claim_does_not_hide_an_independent_assertion_of_the_same_predicate():
    quote, assertion = "«Компания позвонит»", "Компания точно позвонит."
    result = evaluate(wire(claims=[claim("recipient_callback", modality="quoted", evidence=quote),
                                   claim("recipient_callback", evidence=assertion)]), draft=quote + " " + assertion)
    assert result.claims_valid and not result.safe and "unverified_recipient_callback" in result.issues


@pytest.mark.parametrize("predicate", ["recipient_callback", "recipient_no_other_contact_source", "exclusive_recipient"])
@pytest.mark.parametrize("value", [True, False])
def test_unestablished_recipient_capability_never_becomes_a_positive_or_negative_event_fact(predicate, value):
    result = evaluate(wire(claims=[claim(predicate, value, modality="not_established")]))
    assert result.safe and not result.issues


@pytest.mark.parametrize("predicate", sorted(set(CLAIM_PREDICATES) - {
    "recipient_callback", "recipient_no_other_contact_source", "exclusive_recipient",
}))
def test_unknown_modality_cannot_hedge_code_owned_capabilities_receipts_or_purpose(predicate):
    result = evaluate(wire(claims=[claim(predicate, modality="not_established")]))
    assert result.claims_valid and not result.safe and "unsupported_uncertainty_" + predicate in result.issues


@pytest.mark.parametrize("need", [
    {"kind": "unbounded_private_kind", "evidence": LATEST},
    {"kind": "transfer_purpose", "evidence": "from another turn"},
    {"kind": "transfer_purpose", "evidence": LATEST, "extra": True},
    ["malformed"],
])
def test_a_malformed_or_ungrounded_information_need_does_not_erase_the_independent_user_choice(need):
    result = evaluate(wire(acts=[decline()], need=need))
    assert result.safe and result.user_valid and result.effective_frame.conversation_declined
    assert result.applied_user_acts and result.information_need is None and result.need_error is not None


def test_a_wrong_scope_need_does_not_replace_a_valid_refusal_or_the_current_frame_goal():
    frame = offer(question_goal="offer_information_purpose")
    result = evaluate(wire(acts=[decline()], need={"kind": "debt_explanation", "evidence": LATEST},
                           claims=[claim("consultation_purpose")]), frame=frame)
    assert result.safe and result.effective_frame.conversation_declined and result.applied_user_acts
    assert result.effective_frame.question_goal == "offer_information_purpose"
    assert result.need_error == "information_need_scope_mismatch" and result.information_need is None


def test_duplicate_or_nonfinite_need_is_quarantined_while_duplicate_claim_slots_fail_the_claim_channel():
    raw = json.dumps(wire(acts=[decline()]))
    for malformed in [raw.replace('"information_need": null', '"information_need": null, "information_need": {}'),
                      raw.replace('"information_need": null', '"information_need": NaN')]:
        parsed = parse_semantics(malformed, latest_user_message=LATEST, draft=DRAFT)
        result = evaluate_semantics(TurnAuthority.from_frame(offer(), LATEST, DRAFT), parsed)
        assert result.safe and result.applied_user_acts and parsed.need_error == "invalid_schema"
    malformed = raw.replace('"recipient_callback": null', '"recipient_callback": null, "recipient_callback": null')
    parsed = parse_semantics(malformed, latest_user_message=LATEST, draft=DRAFT)
    result = evaluate_semantics(TurnAuthority.from_frame(offer(), LATEST, DRAFT), parsed)
    assert result.user_valid and result.applied_user_acts and not result.claims_valid


@pytest.mark.parametrize("language", ["json", "JSON", "JsOn", ""])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_a_complete_single_json_fence_preserves_all_three_strict_channels(language, newline):
    raw = json.dumps(wire(acts=[decline()]), ensure_ascii=False)
    fenced = " \t" + "```" + language + newline + raw + newline + "```" + "\n "
    parsed = parse_semantics(fenced, latest_user_message=LATEST, draft=DRAFT)
    result = evaluate_semantics(TurnAuthority.from_frame(offer(), LATEST, DRAFT), parsed)
    assert result.safe and result.applied_user_acts and result.effective_frame.conversation_declined
    assert parse_style_channel(fenced) == {"accept": True, "flags": []}


@pytest.mark.parametrize("kind", ["unclosed", "prose_before", "prose_after", "multiple", "nested",
                                  "wrong_language", "inline", "comment", "nonobject"])
def test_envelope_handling_does_not_salvage_partial_embedded_or_commented_json(kind):
    raw = json.dumps(wire(acts=[decline()]))
    fenced = "```json\n" + raw + "\n```"
    cases = {
        "unclosed": "```json\n" + raw,
        "prose_before": "Result\n" + fenced,
        "prose_after": fenced + "\nDone",
        "multiple": fenced + "\n" + fenced,
        "nested": "```json\n" + fenced + "\n```",
        "wrong_language": "```python\n" + raw + "\n```",
        "inline": "```json" + raw + "```",
        "comment": "```json\n// comment\n" + raw + "\n```",
        "nonobject": "```json\n[]\n```",
    }
    parsed = parse_semantics(cases[kind], latest_user_message=LATEST, draft=DRAFT)
    assert parsed.user is None and parsed.claims is None
    with pytest.raises(ValueError):
        parse_style_channel(cases[kind])


@pytest.mark.parametrize("kind", ["duplicate_user", "duplicate_slot", "nonfinite_claim", "unknown_root"])
def test_complete_fences_keep_existing_duplicate_nonfinite_and_extra_key_rejection(kind):
    raw = json.dumps(wire(acts=[decline()], claims=[claim("recipient_callback")]))
    damaged = {
        "duplicate_user": raw.replace('"user":', '"user": {}, "user":', 1),
        "duplicate_slot": raw.replace('"recipient_callback":', '"recipient_callback": null, "recipient_callback":', 1),
        "nonfinite_claim": raw.replace('"value": true', '"value": NaN', 1),
        "unknown_root": raw[:-1] + ', "unexpected": true}',
    }[kind]
    plain = parse_semantics(damaged, latest_user_message=LATEST, draft=DRAFT)
    fenced = parse_semantics("```json\n" + damaged + "\n```", latest_user_message=LATEST, draft=DRAFT)
    assert fenced == plain
    assert fenced.user is None or fenced.claims is None


def test_raw_utf8_size_limit_precedes_fence_and_whitespace_normalization():
    raw = json.dumps(wire(acts=[decline()]))
    for prefix in [" " * 16385, "\u2003" * 5500]:
        parsed = parse_semantics(prefix + "```json\n" + raw + "\n```", latest_user_message=LATEST, draft=DRAFT)
        assert parsed.user is None and parsed.claims is None


def test_a_model_need_guides_planning_without_adding_a_hard_purpose_veto_or_overriding_no():
    body = wire(acts=[decline()], need={"kind": "transfer_purpose", "evidence": LATEST})
    result = evaluate(body)
    assert result.safe and result.applied_user_acts and result.effective_frame.conversation_declined
    assert result.effective_frame.question_goal == "offer_information_purpose"
    assert result.effective_frame.question_goal_origin == "model_hint"
    assert "consultation_purpose_missing" not in result.issues


def test_a_planning_hint_does_not_become_a_code_requirement_when_authority_is_rebuilt_for_c2():
    first = evaluate(wire(acts=[decline()], need={"kind": "transfer_purpose", "evidence": LATEST}))
    assert first.safe and first.effective_frame.conversation_declined
    parsed = parse_semantics(json.dumps(wire()), latest_user_message=LATEST, draft=DRAFT)
    next_authority = TurnAuthority.from_frame(first.effective_frame, LATEST, DRAFT)
    second = evaluate_semantics(next_authority, parsed)
    assert second.safe and second.effective_frame.conversation_declined
    assert second.effective_frame.question_goal_origin == "model_hint"
    assert "consultation_purpose_missing" not in second.issues


def test_a_model_hint_cannot_overwrite_or_downgrade_an_existing_code_goal():
    frame = DialogueFrame(None, "collected_held", question_goal="debt_explanation")
    result = evaluate(wire(need={"kind": "debt_explanation", "evidence": LATEST}), frame=frame)
    assert result.safe and result.effective_frame.question_goal == "debt_explanation"
    assert result.effective_frame.question_goal_origin == "code"
    required = offer(question_goal="offer_information_purpose")
    result = evaluate(wire(acts=[decline()], need={"kind": "debt_explanation", "evidence": LATEST}), frame=required)
    assert result.applied_user_acts and result.effective_frame.conversation_declined
    assert not result.safe and "consultation_purpose_missing" in result.issues
    assert result.effective_frame.question_goal_origin == "code"


@pytest.mark.parametrize("raw,kind", [
    (' {"unexpected": true} ', "plain"),
    ('```json\n{}\n```', "json_fence"),
    ('```JSON\n{}\n```', "json_fence"),
    ('```\n{}\n```', "unlabeled_fence"),
    ('```json\n{}', "invalid"),
    ('Text ```json\n{}\n```', "invalid"),
])
def test_envelope_diagnostics_are_closed_format_labels_and_do_not_certify_schema(raw, kind):
    assert json_envelope_kind(raw) == kind


def test_a_model_hint_preserves_the_required_amount_field_and_precision_guard_across_two_analyses():
    frame = DialogueFrame("debts.total", "collecting_debt_context", confirming=True, require_question=True,
                          known_fields=frozenset({"client.name", "client.phone"}))
    first = evaluate(wire(need={"kind": "debt_explanation", "evidence": LATEST}), frame=frame)
    second = evaluate(wire(), frame=first.effective_frame)
    for result in (first, second):
        assert result.safe and result.effective_frame.question_goal_origin == "model_hint"
        assert result.effective_frame.require_question
        assert result.effective_frame.allowed_field == "debts.total"
        assert result.effective_frame.known_fields == frame.known_fields
        assert debt_amount_reply_issue("280 000 рублей верно?", result.effective_frame) == "unbound_amount_confirmation"


def test_only_a_code_owned_explanation_defers_the_pending_amount_question():
    frame = DialogueFrame("debts.total", "collecting_debt_context", confirming=True,
                          question_goal="debt_explanation", question_goal_origin="code")
    result = evaluate(wire(need={"kind": "debt_explanation", "evidence": LATEST}), frame=frame)
    assert result.safe and result.effective_frame.question_goal_origin == "code"
    assert result.effective_frame.allowed_field is None
    assert not result.effective_frame.require_question
