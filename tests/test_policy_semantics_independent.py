"""Independent gold contrasts and deterministic boundaries, without model-quality claims.

The supplied semantic labels are human-authored fixtures. These tests establish
parser/reducer behavior, not a real model's ability to recover those labels.
"""
from __future__ import annotations

from dataclasses import replace
from copy import deepcopy
from io import BytesIO
import json
from typing import get_args

import pytest

from dialogue_frame import DialogueFrame
from policy_semantics import ClaimPredicate, TurnAuthority, evaluate_semantics, parse_semantics
from tests import test_onloader_local_http as existing_http


onload = existing_http.onload


USER_GOLD = (
    ("Ага, тогда без передачи.", "decline_transfer", "client", "current_offer", "asserted", True),
    ("Понял, не буду подтверждать.", "decline_transfer", "client", "current_offer", "asserted", True),
    ("Понятно. Но я не хочу передавать.", "decline_transfer", "client", "current_offer", "asserted", True),
    ("Номер оставлять не хочу. Можно без него?", "decline_phone", "client", "contact_collection", "asserted", True),
    ("Понял. А отказаться тут можно?", "decline_transfer", "client", "current_offer", "question", False),
    ("Если откажусь передавать, что будет?", "decline_transfer", "client", "current_offer", "conditional", False),
    ("Он сказал: «Тогда без передачи».", "decline_transfer", "other", "current_offer", "quoted", False),
    ("Муж не хочет передавать данные.", "decline_transfer", "other", "current_offer", "asserted", False),
    ("Раньше я говорила, что не хочу передавать.", "decline_transfer", "client", "current_offer", "reported", False),
)

# These are calibration labels for the existing V11 claim classes. Supplying an
# empty claims array would miss the expected class even with all four scan flags.
CLAIM_GOLD = (
    ("Чтобы оформить анкету и закрепить за вами ответы, нужен номер телефона.", "phone_required_to_save_chat", True),
    ("Без этого они не смогут с вами связаться.", "recipient_no_other_contact_source", True),
    ("Контакт нужен компании, чтобы связаться с вами и проконсультировать по обращению.", "consultation_purpose", False),
)


def offer_frame() -> DialogueFrame:
    return DialogueFrame(None, "awaiting_consent", mode="onloader", offer_present=True,
                          offer_status="awaiting_consent", offer_recipient_name="Тестовая компания")


def envelope(*, acts: list[dict[str, str]] | None = None,
             claims: list[dict[str, object]] | None = None,
             speech_acts: list[dict[str, str]] | None = None) -> dict[str, object]:
    predicates: dict[str, list[dict[str, object]] | None] = dict.fromkeys(get_args(ClaimPredicate))
    for claim in claims or []:
        predicate = str(claim["predicate"])
        evidence = {key: value for key, value in claim.items() if key != "predicate"}
        predicates[predicate] = [*(predicates[predicate] or []), evidence]
    requests: dict[str, list[dict[str, str]] | None] = {
        "phone_request": None, "transfer_consent_request": None,
    }
    for act in speech_acts or []:
        request = "phone_request" if act["kind"] == "request_phone" else "transfer_consent_request"
        requests[request] = [*(requests[request] or []), {key: value for key, value in act.items() if key != "kind"}]
    return {
        "user": {"acts": acts or [], "information_need": None},
        "claims": {"predicates": predicates, "requests": requests},
        "style": {"accept": True, "flags": []},
    }


@pytest.mark.parametrize("latest,kind,subject,scope,mode,applied", USER_GOLD)
def test_gold_user_acts_reduce_only_the_own_asserted_current_choice(latest, kind, subject, scope, mode, applied):
    frame = offer_frame() if kind == "decline_transfer" else DialogueFrame("client.phone", "collecting_contacts")
    draft = "Можем общаться здесь."
    raw = envelope(acts=[{"kind": kind, "evidence": latest, "subject": subject, "scope": scope, "mode": mode}])
    parsed = parse_semantics(json.dumps(raw, ensure_ascii=False), latest_user_message=latest, draft=draft)
    result = evaluate_semantics(TurnAuthority.from_frame(frame, latest, draft), parsed)
    assert result.user_valid
    assert bool(result.applied_user_acts) is applied
    if kind == "decline_transfer":
        assert result.effective_frame.conversation_declined is applied
        assert not frame.conversation_declined
    else:
        assert ("client.phone" in result.effective_frame.refused_fields) is applied


def test_an_unrelated_operation_has_no_transfer_act_in_the_calibration_gold():
    latest, draft = "Я не хочу передавать машину.", "Можем обсудить ваши вопросы."
    parsed = parse_semantics(json.dumps(envelope()), latest_user_message=latest, draft=draft)
    result = evaluate_semantics(TurnAuthority.from_frame(offer_frame(), latest, draft), parsed)
    assert result.user_valid and not result.applied_user_acts
    assert not result.effective_frame.conversation_declined


def test_an_assistant_acknowledgement_cannot_supply_evidence_for_the_users_refusal():
    latest, draft = "А отказаться тут можно?", "Хорошо, без передачи."
    raw = envelope(acts=[{"kind": "decline_transfer", "evidence": "без передачи", "subject": "client",
                          "scope": "current_offer", "mode": "asserted"}])
    parsed = parse_semantics(json.dumps(raw), latest_user_message=latest, draft=draft)
    result = evaluate_semantics(TurnAuthority.from_frame(offer_frame(), latest, draft), parsed)
    assert not result.user_valid
    assert result.effective_frame.conversation_paused
    assert not result.effective_frame.conversation_declined


def test_a_valid_user_refusal_survives_an_independently_malformed_claim_channel():
    latest, draft = "Ага, тогда без передачи.", "Можем продолжить общение здесь."
    raw = envelope(acts=[{"kind": "decline_transfer", "evidence": latest, "subject": "client",
                          "scope": "current_offer", "mode": "asserted"}])
    raw["claims"] = {"claims": [{"predicate": "invented_provider_fact"}], "coverage": []}
    parsed = parse_semantics(json.dumps(raw), latest_user_message=latest, draft=draft)
    result = evaluate_semantics(TurnAuthority.from_frame(offer_frame(), latest, draft), parsed)
    assert result.user_valid and result.effective_frame.conversation_declined
    assert not result.claims_valid and not result.safe


@pytest.mark.parametrize("draft,predicate,contradicted", CLAIM_GOLD)
def test_grounded_claim_candidates_are_checked_against_code_owned_capabilities(draft, predicate, contradicted):
    latest = "Зачем нужен контакт?"
    raw = envelope(claims=[{"predicate": predicate, "value": True, "evidence": draft,
                            "modality": "asserted", "scope": "current_offer"}])
    parsed = parse_semantics(json.dumps(raw), latest_user_message=latest, draft=draft)
    result = evaluate_semantics(TurnAuthority.from_frame(offer_frame(), latest, draft), parsed)
    assert result.claims_valid
    assert bool(result.issues) is contradicted
    assert result.safe is not contradicted


def test_a_parse_from_another_latest_message_cannot_bind_to_this_turn():
    draft = "Можем общаться здесь."
    parsed = parse_semantics(json.dumps(envelope()), latest_user_message="Спасибо.", draft=draft)
    result = evaluate_semantics(TurnAuthority.from_frame(offer_frame(), "Ага, тогда без передачи.", draft), parsed)
    assert not result.user_valid and not result.safe
    assert "context_mismatch" in result.issues


@pytest.mark.parametrize("adds_callback_promise", [False, True])
def test_a_conditional_verified_purpose_answers_the_current_need_without_licensing_a_callback(adds_callback_promise):
    latest = "Зачем передавать контакт?"
    purpose = "Если разрешите передачу, контакт будет использован для консультации по обращению."
    callback = "Если разрешите передачу, компания обязательно позвонит."
    draft = purpose + (" " + callback if adds_callback_promise else "")
    claims = [{"predicate": "consultation_purpose", "value": True, "evidence": purpose,
               "modality": "conditional", "scope": "current_offer"}]
    if adds_callback_promise:
        claims.append({"predicate": "recipient_callback", "value": True, "evidence": callback,
                       "modality": "conditional", "scope": "current_offer"})
    raw = envelope(claims=claims)
    # This explicit current information need reaches the required-purpose gate.
    raw["user"] = {"acts": [], "information_need": {"kind": "transfer_purpose", "evidence": latest}}
    parsed = parse_semantics(json.dumps(raw), latest_user_message=latest, draft=draft)
    result = evaluate_semantics(TurnAuthority.from_frame(offer_frame(), latest, draft), parsed)
    assert result.user_valid and result.claims_valid
    if adds_callback_promise:
        assert not result.safe
        assert "unverified_recipient_callback" in result.issues
    else:
        assert result.safe, result.issues
        assert result.effective_frame.question_goal == "offer_information_purpose"


@pytest.mark.parametrize("unsupported_context", ["global", "no_offer", "false_value"])
def test_a_conditional_purpose_still_requires_the_current_verified_offer(unsupported_context):
    latest, draft = "Продолжим.", "Если разрешите передачу, контакт будет использован для консультации."
    frame = replace(offer_frame(), offer_present=False) if unsupported_context == "no_offer" else offer_frame()
    raw = envelope(claims=[{
        "predicate": "consultation_purpose", "value": unsupported_context != "false_value", "evidence": draft,
        "modality": "conditional", "scope": "global" if unsupported_context == "global" else "current_offer",
    }])
    parsed = parse_semantics(json.dumps(raw), latest_user_message=latest, draft=draft)
    result = evaluate_semantics(TurnAuthority.from_frame(frame, latest, draft), parsed)
    assert not result.safe
    assert "unverified_purpose" in result.issues


@pytest.mark.parametrize("scenario", [
    "two_contradicting_clauses", "hedged_phone_barrier", "misused_not_established",
    "literal_no_callback", "quoted_then_asserted_callback", "unknown_callback_not_established",
])
def test_structured_slots_preserve_independent_claims_and_epistemic_boundaries(scenario):
    latest = "Продолжим."
    if scenario == "two_contradicting_clauses":
        clauses = ["Телефон не нужен для общения здесь.", "Но без телефона общаться здесь нельзя."]
        claims = [{"predicate": "phone_required_to_chat", "value": value, "evidence": clause,
                   "modality": "asserted", "scope": "chat"}
                  for clause, value in zip(clauses, [False, True], strict=True)]
    elif scenario in {"hedged_phone_barrier", "misused_not_established"}:
        clauses = ["Возможно, для общения здесь нужен телефон."]
        claims = [{"predicate": "phone_required_to_chat", "value": True, "evidence": clauses[0],
                   "modality": "conditional" if scenario == "hedged_phone_barrier" else "not_established",
                   "scope": "chat"}]
    elif scenario == "quoted_then_asserted_callback":
        clauses = ["Вы написали: «Компания позвонит».", "Компания точно позвонит завтра."]
        claims = [{"predicate": "recipient_callback", "value": True, "evidence": clause,
                   "modality": modality, "scope": "current_offer"}
                  for clause, modality in zip(clauses, ["quoted", "asserted"], strict=True)]
    else:
        clauses = ["Компания вам не позвонит." if scenario == "literal_no_callback" else
                   "Звонок компании не подтверждён."]
        claims = [{"predicate": "recipient_callback", "value": False, "evidence": clauses[0],
                   "modality": "asserted" if scenario == "literal_no_callback" else "not_established",
                   "scope": "current_offer"}]
    draft = " ".join(clauses)
    parsed = parse_semantics(json.dumps(envelope(claims=claims)), latest_user_message=latest, draft=draft)
    result = evaluate_semantics(TurnAuthority.from_frame(offer_frame(), latest, draft), parsed)
    assert result.claims_valid
    assert parsed.claims is not None
    assert len(parsed.claims.claims) == len(claims)
    assert result.safe is (scenario == "unknown_callback_not_established")


@pytest.mark.parametrize("predicate,status,safe", [
    ("transfer_completed", "signal_pending", True),
    ("transfer_completed", "outcome_unknown", True),
    ("transfer_completed", "awaiting_consent", False),
    ("phone_required_to_chat", "outcome_unknown", False),
])
def test_unknown_transfer_receipt_is_distinguished_from_known_false_capabilities(predicate, status, safe):
    latest = "Какой сейчас результат?"
    draft = ("Результат передачи пока неизвестен." if predicate == "transfer_completed" else
             "Не установлено, нужен ли телефон для общения здесь.")
    frame = replace(offer_frame(), offer_status=status)
    raw = envelope(claims=[{"predicate": predicate, "value": False, "evidence": draft,
                            "modality": "not_established", "scope": "current_offer"}])
    parsed = parse_semantics(json.dumps(raw), latest_user_message=latest, draft=draft)
    result = evaluate_semantics(TurnAuthority.from_frame(frame, latest, draft), parsed)
    assert result.user_valid and result.claims_valid
    assert result.safe is safe


def test_a_stale_frame_digest_does_not_create_current_offer_authority():
    latest, draft = "Ага, тогда без передачи.", "Можем общаться здесь."
    frame = offer_frame()
    authority = TurnAuthority.from_frame(frame, latest, draft)
    authority = replace(authority, frame=replace(frame, offer_recipient_name="Другой получатель"))
    parsed = parse_semantics(json.dumps(envelope()), latest_user_message=latest, draft=draft)
    result = evaluate_semantics(authority, parsed)
    assert not result.user_valid and not result.claims_valid and not result.safe


@pytest.mark.parametrize("phase_budget,elapsed,accepted", [
    (10, 7.649, True), (10, 10.0, False), (3, 7.649, False),
])
def test_actual_analysis_adapter_respects_the_prospective_budget_and_smaller_global_remainder(
    monkeypatch, phase_budget, elapsed, accepted,
):
    import llm_agent
    import policy_semantic_analysis
    from config import AppConfig

    clock = [0.0]
    monkeypatch.setattr(policy_semantic_analysis.time, "monotonic", lambda: clock[0])
    observed_budgets = []

    def open_request(config, request):
        observed_budgets.append(config.request_timeout)
        clock[0] = elapsed
        return BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(envelope())}}]}).encode())

    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    config = AppConfig(openrouter_api_key="synthetic-unused", model="synthetic-speaker",
                       extractor_model="synthetic-distinct-analysis", request_timeout=phase_budget, max_tokens=1000)
    result = policy_semantic_analysis.analyze(
        {"latest_user_message": "Продолжим."}, "Можем общаться здесь.", offer_frame(), config,
    )
    assert observed_budgets == [phase_budget]
    assert result.assessment.attempted
    if accepted:
        assert result.semantics.user is not None and result.semantics.claims is not None
        assert result.assessment.reason == "accepted"
    else:
        assert result.semantics.user is None and result.semantics.claims is None
        assert result.assessment.reason == "deadline_exhausted"


@pytest.mark.parametrize("framing,valid", [
    ("json_fence", True), ("unlabeled_fence", True), ("prose_before", False), ("prose_after", False),
    ("unclosed", False), ("two_fences", False), ("wrong_language", False), ("duplicate_user", False),
])
def test_only_a_complete_single_json_envelope_preserves_strict_semantic_parsing(framing, valid):
    latest, draft = "Продолжим.", "Можно общаться здесь."
    body = json.dumps(envelope())
    wrapped = "```json\n" + body + "\n```"
    if framing == "unlabeled_fence":
        raw = "```\n" + body + "\n```"
    elif framing == "prose_before":
        raw = "Результат ниже.\n" + wrapped
    elif framing == "prose_after":
        raw = wrapped + "\nОбъяснение после ответа."
    elif framing == "unclosed":
        raw = "```json\n" + body
    elif framing == "two_fences":
        raw = wrapped + "\n" + wrapped
    elif framing == "wrong_language":
        raw = "```python\n" + body + "\n```"
    elif framing == "duplicate_user":
        raw = "```json\n" + body[:-1] + ', "user": {"acts": []}}\n```'
    else:
        raw = wrapped
    parsed = parse_semantics(raw, latest_user_message=latest, draft=draft)
    result = evaluate_semantics(TurnAuthority.from_frame(offer_frame(), latest, draft), parsed)
    assert result.safe is valid
    if not valid:
        assert not result.user_valid or not result.claims_valid


@pytest.mark.parametrize("code_goal,expected_safe", [(None, True), ("offer_information_purpose", False)])
def test_an_inferred_information_need_cannot_strengthen_the_original_hard_answer_contract(code_goal, expected_safe):
    latest, draft = "Если откажусь передавать, что будет?", "Можем общаться здесь."
    frame = replace(offer_frame(), question_goal=code_goal)
    raw = envelope()
    raw["user"] = {"acts": [], "information_need": {"kind": "transfer_purpose", "evidence": latest}}
    parsed = parse_semantics(json.dumps(raw), latest_user_message=latest, draft=draft)
    result = evaluate_semantics(TurnAuthority.from_frame(frame, latest, draft), parsed)
    assert result.user_valid and result.claims_valid
    assert not result.applied_user_acts
    assert result.safe is expected_safe
    assert ("consultation_purpose_missing" in result.issues) is (not expected_safe)
    assert frame.question_goal == code_goal


def test_actual_provider_false_information_need_does_not_replace_a_valid_first_reply(monkeypatch):
    import llm_agent
    import policy_grounded_voice
    from config import AppConfig

    latest, first = "Если откажусь передавать, что будет?", "Можем общаться здесь."
    bodies = []
    config = AppConfig(openrouter_api_key="synthetic-unused", model="synthetic-speaker",
                       extractor_model="synthetic-distinct-analysis", request_timeout=40, max_tokens=1000,
                       policy_voice_enabled=True, policy_semantic_enabled=True)

    def open_request(configuration, request):
        body = json.loads(request.data)
        bodies.append(body)
        if body["model"] == config.extractor_model:
            raw = envelope()
            raw["user"] = {"acts": [], "information_need": {"kind": "transfer_purpose", "evidence": latest}}
            content = json.dumps(raw)
        else:
            content = first
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    result = policy_grounded_voice.render(
        {"facts": {}, "latest_user_message": latest}, config, frame=offer_frame(), fallback="Запасной ответ.",
    )
    assert result.spoken.text == first and result.spoken.source == "model"
    assert [body["model"] for body in bodies] == [config.model, config.extractor_model]
    assert not result.applied_user_acts and not result.paused
    assert not result.effective_frame.conversation_declined


def test_actual_second_analysis_cannot_launder_a_model_hint_into_a_code_owned_goal(monkeypatch):
    import llm_agent
    import policy_grounded_voice
    from config import AppConfig

    latest = "Если откажусь передавать, что будет?"
    unsafe = "Телефон нужен, чтобы сохранить ваши ответы."
    final = "Можем общаться здесь."
    bodies, contexts = [], []
    config = AppConfig(openrouter_api_key="synthetic-unused", model="synthetic-speaker",
                       extractor_model="synthetic-distinct-analysis", request_timeout=40, max_tokens=1000,
                       policy_voice_enabled=True, policy_semantic_enabled=True)

    def open_request(configuration, request):
        body = json.loads(request.data)
        bodies.append(body)
        if body["model"] == config.extractor_model:
            data = json.loads(body["messages"][-1]["content"])
            contexts.append(data["context"])
            if len(contexts) == 1:
                raw = envelope(claims=[{"predicate": "phone_required_to_save_chat", "value": True,
                    "evidence": unsafe, "modality": "asserted", "scope": "chat"}])
                raw["user"] = {"acts": [], "information_need": {"kind": "transfer_purpose", "evidence": latest}}
            else:
                raw = envelope()
            content = json.dumps(raw)
        else:
            content = unsafe if len(bodies) == 1 else final
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    result = policy_grounded_voice.render(
        {"facts": {}, "latest_user_message": latest}, config, frame=offer_frame(), fallback="Запасной ответ.",
    )
    assert result.spoken.text == final and result.spoken.source == "model"
    assert [body["model"] for body in bodies] == [config.model, config.extractor_model] * 2
    assert contexts[1]["question_goal"] == "offer_information_purpose"
    assert contexts[1]["question_goal_origin"] == "model_hint"
    assert result.effective_frame.question_goal_origin == "model_hint"
    assert not result.applied_user_acts and not result.effective_frame.conversation_declined


def test_actual_provider_debt_hint_cannot_veto_the_healthy_planned_question(monkeypatch):
    import llm_agent
    import policy_grounded_voice
    from config import AppConfig

    latest, first = "Можно просто продолжить?", "Как к вам можно обращаться?"
    bodies = []
    config = AppConfig(openrouter_api_key="synthetic-unused", model="synthetic-speaker",
                       extractor_model="synthetic-distinct-analysis", request_timeout=40, max_tokens=1000,
                       policy_voice_enabled=True, policy_semantic_enabled=True)

    def open_request(configuration, request):
        body = json.loads(request.data)
        bodies.append(body)
        if body["model"] == config.extractor_model:
            raw = envelope()
            raw["user"] = {"acts": [], "information_need": {"kind": "debt_explanation", "evidence": latest}}
            content = json.dumps(raw)
        else:
            content = first
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    frame = DialogueFrame("client.name", "collecting_contacts", require_question=True)
    result = policy_grounded_voice.render(
        {"facts": {}, "latest_user_message": latest}, config, frame=frame, fallback="Можем продолжить общение.",
    )
    assert result.spoken.text == first and result.spoken.source == "model"
    assert result.spoken.asked_field == "client.name"
    assert [body["model"] for body in bodies] == [config.model, config.extractor_model]
    assert result.effective_frame.allowed_field == "client.name" and result.effective_frame.require_question


def test_a_model_debt_hint_cannot_disable_the_existing_unconfirmed_money_guard():
    import policy_voice

    latest, draft = "Можно просто продолжить?", "Хорошо, 400 тысяч зафиксировала."
    frame = DialogueFrame("debts.total", "confirming_debt", confirming=True, require_question=True)
    raw = envelope()
    raw["user"] = {"acts": [], "information_need": {"kind": "debt_explanation", "evidence": latest}}
    parsed = parse_semantics(json.dumps(raw), latest_user_message=latest, draft=draft)
    result = evaluate_semantics(TurnAuthority.from_frame(frame, latest, draft), parsed)
    assert not policy_voice.validate_reply(draft, frame.allowed_field, frame=frame)
    assert not policy_voice.validate_reply(draft, result.effective_frame.allowed_field, frame=result.effective_frame)


def test_actual_quiet_hint_fallback_does_not_claim_an_unspoken_planned_field(monkeypatch):
    import llm_agent
    import policy_grounded_voice
    from config import AppConfig

    latest = "Можно просто продолжить?"
    unsafe = "Телефон нужен, чтобы сохранить ваши ответы."
    bodies, analysis_count = [], [0]
    config = AppConfig(openrouter_api_key="synthetic-unused", model="synthetic-speaker",
                       extractor_model="synthetic-distinct-analysis", request_timeout=40, max_tokens=1000,
                       policy_voice_enabled=True, policy_semantic_enabled=True)

    def open_request(configuration, request):
        body = json.loads(request.data)
        bodies.append(body)
        if body["model"] == config.extractor_model:
            analysis_count[0] += 1
            raw = envelope(claims=[{"predicate": "phone_required_to_save_chat", "value": True,
                "evidence": unsafe, "modality": "asserted", "scope": "chat"}])
            if analysis_count[0] == 1:
                raw["user"] = {"acts": [], "information_need": {"kind": "debt_explanation", "evidence": latest}}
            content = json.dumps(raw)
        else:
            content = unsafe
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    result = policy_grounded_voice.render(
        {"facts": {}, "latest_user_message": latest}, config,
        frame=DialogueFrame("client.name", "collecting_contacts", require_question=True),
        fallback="Как к вам обращаться?",
    )
    assert result.spoken.source == "fallback" and result.spoken.text == "Можем продолжить разговор здесь."
    assert [body["model"] for body in bodies] == [config.model, config.extractor_model] * 2
    assert result.spoken.asked_field is None


def install_real_adapter(onload, latest: str, *, timeout: bool = False):
    """Intercept only the existing HTTP opener, preserving both real model adapters."""
    import llm_agent
    import main

    config = replace(main.load_config(), policy_voice_enabled=True, policy_semantic_enabled=True,
                     extractor_model="synthetic-distinct-analysis")
    onload.monkeypatch.setattr(main, "load_config", lambda: config)
    bodies = []

    def open_request(configuration, request):
        body = json.loads(request.data)
        bodies.append(body)
        if body["model"] == config.extractor_model:
            if timeout:
                raise TimeoutError("synthetic-private-provider-detail")
            content = json.dumps(envelope(acts=[{
                "kind": "decline_transfer", "evidence": latest, "subject": "client",
                "scope": "current_offer", "mode": "asserted",
            }]), ensure_ascii=False)
        else:
            content = "Можем спокойно продолжить общение здесь."
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    onload.monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    return config, bodies


@pytest.mark.parametrize("write_fails", [False, True])
def test_actual_http_preserves_or_withholds_the_grounded_refusal_at_the_commit_boundary(onload, write_fails):
    from onloader import policy as onloader_policy
    from storage.sqlite_sessions import SQLiteSessionRepository

    sid, _ = existing_http.bind(onload)
    session = existing_http.live(onload, sid)
    before = deepcopy(session.state)
    user = "Ага, тогда без передачи."
    config, bodies = install_real_adapter(onload, user)
    if write_fails:
        def fail(*args, **kwargs):
            raise OSError("synthetic-private-storage-detail")
        onload.monkeypatch.setattr(onload.repository, "save_committed", fail)
    response = onload.client.get(f"/api/session/{sid}/stream", params={"message": user})
    assert response.status_code == 200
    assert [body["model"] for body in bodies] == [config.model, config.extractor_model]
    saved = SQLiteSessionRepository(onload.session_path).load(sid)
    assert saved is not None and saved.state["onloader"]["decision"] is None
    if write_fails:
        assert "event: chunk" not in response.text and "event: bubbles" not in response.text
        assert "event: error" in response.text
        assert session.state == before and session._storage_uncertain
        assert not onloader_policy.chat_declined(saved.state)
        assert not any(row.get("content") == user for row in saved.state["chat_history"])
    else:
        assert "event: error" not in response.text and "event: chunk" in response.text
        assert onloader_policy.chat_declined(saved.state)
        assert saved.state["chat_history"][-2:][0] == {"role": "user", "content": user}
        assert saved.state["chat_history"][-1]["content"] == "Можем спокойно продолжить общение здесь."
    assert not session.turn_lock.locked()
    assert onload.backend.calls["submit"] == 0
    assert all(private not in json.dumps(bodies, ensure_ascii=False) for private in (
        "private-contact-handle", "private-application", "private-subject", "private-operation",
    ))
    assert "synthetic-private" not in response.text


def test_actual_analysis_timeouts_pause_without_forging_a_refusal_or_a_formal_decision(onload):
    from onloader import policy as onloader_policy
    from storage.sqlite_sessions import SQLiteSessionRepository

    sid, _ = existing_http.bind(onload)
    # A question supplies no decision. Explicit own refusals have a separate
    # deterministic path and remain durable even when the analysis is unavailable.
    user = "А можно без передачи?"
    config, bodies = install_real_adapter(onload, user, timeout=True)
    response = onload.client.get(f"/api/session/{sid}/stream", params={"message": user})
    assert response.status_code == 200 and "event: error" not in response.text
    # A provider timeout ends the check. Regenerating cannot restore its service.
    assert [body["model"] for body in bodies] == [config.model, config.extractor_model]
    saved = SQLiteSessionRepository(onload.session_path).load(sid)
    assert saved is not None and saved.state["onloader"]["decision"] is None
    assert not onloader_policy.chat_declined(saved.state)
    assert saved.state[onloader_policy.CHAT_KEY]["paused"] is True
    assert onload.backend.calls["submit"] == 0
    assert "synthetic-private" not in response.text


@pytest.mark.parametrize("case", ["storage", "consent_after_no"])
def test_actual_four_provider_bodies_repair_a_closed_violation_and_commit_the_same_refusal(onload, case):
    import llm_agent
    import main
    from onloader import policy as onloader_policy

    sid, _ = existing_http.bind(onload)
    session = existing_http.live(onload, sid)
    session.state["facts"]["client"] = {"name": "Ирина Синтетическая", "phone": "+70000000401"}
    onload.store.persist_durable(session)
    before = deepcopy(session.state["facts"])
    user = "Ага, тогда без передачи."
    final = "Можем спокойно продолжить общение здесь без передачи данных."
    if case == "storage":
        violation = "Чтобы закрепить за вами ответы, нужен номер телефона."
        corrective_fact = "Ответы и история сохраняются независимо от телефона."
    else:
        violation = "Подтвердите передачу кнопкой под сообщением."
        corrective_fact = "Сейчас просьба подтвердить передачу закрыта."
    first = violation + " Ирина Синтетическая, ваш номер +70000000401."
    config = replace(main.load_config(), policy_voice_enabled=True, policy_semantic_enabled=True,
                     extractor_model="synthetic-distinct-analysis")
    onload.monkeypatch.setattr(main, "load_config", lambda: config)
    bodies = []
    generations = analyses = 0

    def open_request(configuration, request):
        nonlocal generations, analyses
        body = json.loads(request.data)
        bodies.append(body)
        if body["model"] == config.extractor_model:
            analyses += 1
            claims = ([{"predicate": "phone_required_to_save_chat", "value": True, "evidence": violation,
                        "modality": "asserted", "scope": "chat"}]
                      if analyses == 1 and case == "storage" else [])
            requests = ([{"kind": "request_transfer_consent", "mode": "requested", "evidence": violation}]
                        if analyses == 1 and case == "consent_after_no" else [])
            content = json.dumps(envelope(acts=[{
                "kind": "decline_transfer", "evidence": user, "subject": "client",
                "scope": "current_offer", "mode": "asserted",
            }], claims=claims, speech_acts=requests), ensure_ascii=False)
        else:
            generations += 1
            content = first if generations == 1 else final
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    onload.monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    response = onload.client.get(f"/api/session/{sid}/stream", params={"message": user})
    assert response.status_code == 200 and "event: error" not in response.text
    assert [body["model"] for body in bodies] == [config.model, config.extractor_model] * 2
    assert all(body["max_tokens"] == 1000 for body in bodies if body["model"] == config.extractor_model)
    second_instruction = [item["content"] for item in bodies[2]["messages"] if item["role"] == "system"][-1]
    assert corrective_fact in second_instruction
    assert first not in second_instruction
    assert json.loads(bodies[-1]["messages"][-1]["content"])["draft"] == final
    saved = onload.repository.load(sid)
    assert saved is not None and onloader_policy.chat_declined(saved.state)
    assert saved.state["onloader"]["decision"] is None and saved.state["facts"] == before
    assert saved.state["chat_history"][-1]["content"] == final
    assert onload.backend.calls["submit"] == 0
    assert all(private not in json.dumps(bodies, ensure_ascii=False) for private in (
        "Ирина", "Синтетическая", "+70000000401", "private-contact-handle",
        "private-application", "private-subject", "private-operation",
    ))


def test_a_rotated_private_presentation_cannot_receive_the_old_rendered_refusal(monkeypatch):
    import core
    import policy_grounded_voice
    import policy_voice
    from config import AppConfig
    from onloader import policy as onloader_policy
    from onloader.contracts import OnloaderState
    from tests.test_policy_voice import onloader_state

    state = onloader_state()
    user = "Ага, тогда без передачи."
    rejected_ack = "Хорошо, передавать не буду."

    def rotate_while_rendering(payload, config, *, frame, **kwargs):
        # The public name/status stay identical while the actual current card changes.
        current = OnloaderState.model_validate(state["onloader"])
        state["onloader"] = current.model_copy(update={"presentation_id": "private-replacement"}).storage_value()
        return policy_grounded_voice.GroundedReply(
            policy_voice.SpokenPolicyReply(rejected_ack, None, "model", "model_semantic_checked"),
            ("decline_transfer",), False, replace(frame, conversation_declined=True),
        )

    monkeypatch.setattr(policy_grounded_voice, "render", rotate_while_rendering)
    config = AppConfig(openrouter_api_key="synthetic-unused", policy_voice_enabled=True, policy_semantic_enabled=True)
    speech = core._policy_conversation(state, user, config)
    committed = core.commit_turn(state, user, speech)["current_state"]
    assert speech != rejected_ack
    assert state["_policy_spoken"].reason == "semantic_offer_changed"
    assert OnloaderState.model_validate(committed["onloader"]).presentation_id == "private-replacement"
    assert not onloader_policy.chat_declined(committed)
    assert committed["onloader"]["decision"] is None


def test_an_optional_style_analysis_timeout_does_not_erase_the_already_checked_first_reply(monkeypatch):
    import llm_agent
    import policy_grounded_voice
    from config import AppConfig

    first, revised = "Можем поговорить здесь.", "Можем обсудить вопросы здесь."
    bodies = []
    generations = analyses = 0
    config = AppConfig(openrouter_api_key="synthetic-unused", policy_semantic_enabled=True,
                       extractor_model="synthetic-distinct-analysis")

    def open_request(configuration, request):
        nonlocal generations, analyses
        body = json.loads(request.data)
        bodies.append(body)
        if body["model"] == config.extractor_model:
            analyses += 1
            if analyses == 2:
                raise TimeoutError("synthetic-private-provider-detail")
            raw = envelope()
            raw["style"] = {"accept": False, "flags": ["unrequested_receipt"]}
            content = json.dumps(raw)
        else:
            generations += 1
            content = first if generations == 1 else revised
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    result = policy_grounded_voice.render(
        {"facts": {}, "latest_user_message": "Можно просто поговорить?"}, config,
        frame=offer_frame(), fallback="Можем продолжить разговор здесь.",
    )
    assert [body["model"] for body in bodies] == [config.model, config.extractor_model] * 2
    assert result.spoken.text == first
    assert result.spoken.source == "model"
    assert result.spoken.reason == "model_semantic_first_preserved"
    assert not result.paused and not result.effective_frame.conversation_declined
    failure = result.spoken.critic_assessments[-1].failure
    assert failure is not None and failure.kind == "timeout"


def test_normal_to_debt_publishes_its_hold_before_generation_but_not_provisional_history(onload):
    import debt_policy
    import llm_agent
    import main

    sid = existing_http.create(onload)
    session = existing_http.live(onload, sid)
    history = [{"role": "assistant", "content": "Здравствуйте, чем помочь?"},
               {"role": "user", "content": "У меня есть вопрос."}]
    session.state["chat_history"] = deepcopy(history)
    session.has_started_opening = True
    onload.store.persist_durable(session)
    config = replace(main.load_config(), policy_voice_enabled=True, policy_semantic_enabled=True,
                     extractor_model="synthetic-distinct-analysis")
    onload.monkeypatch.setattr(main, "load_config", lambda: config)
    observed = []
    user = "Хочу списать свои долги. Общая сумма долга 400000 рублей."

    def open_request(configuration, request):
        body = json.loads(request.data)
        if body["model"] == config.extractor_model:
            content = json.dumps(envelope())
        else:
            saved = onload.repository.load(sid)
            assert saved is not None
            # Observe before returning the generated text. Assertions below cannot
            # be swallowed by the model transport's ordinary failure handling.
            observed.append({"hot_hold": debt_policy.debt_delivery_held(session.state),
                             "durable_hold": debt_policy.debt_delivery_held(saved.state),
                             "hot_history": deepcopy(session.state["chat_history"]),
                             "durable_history": deepcopy(saved.state["chat_history"])})
            content = "В каком городе и регионе вы живёте?"
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    onload.monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    response = onload.client.get(f"/api/session/{sid}/stream", params={"message": user})
    assert response.status_code == 200 and "event: error" not in response.text
    assert observed and observed[0]["hot_hold"] and observed[0]["durable_hold"]
    assert observed[0]["hot_history"] == observed[0]["durable_history"] == history
    saved = onload.repository.load(sid)
    assert saved is not None and saved.state["chat_history"][-2]["content"] == user
    assert onload.backend.calls["submit"] == 0
