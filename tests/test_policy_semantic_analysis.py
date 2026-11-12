"""Combined semantic transport preserves independent grounded channels without real IO."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import io
import json
from urllib.error import HTTPError

import pytest

from config import AppConfig
from dialogue_frame import DialogueFrame
import llm_agent
import policy_semantic_analysis as analysis
from policy_semantics import (
    CLAIM_PREDICATES, REQUEST_SLOT_NAMES, TurnAuthority, evaluate_semantics, parse_semantics,
)


@pytest.fixture(autouse=True)
def no_real_transport(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Semantic analysis unit tests cannot call a model or business service")
    monkeypatch.setattr(llm_agent, "_urlopen", forbidden)


def configuration(**kwargs):
    return replace(AppConfig(openrouter_api_key="synthetic-unused", model="synthetic-speaker",
                             extractor_model="synthetic-extractor", max_tokens=2000), **kwargs)


def offered_frame():
    return DialogueFrame(None, "awaiting_consent", mode="onloader", offer_present=True,
                         offer_status="awaiting_consent", offer_recipient_name="Тестовая компания")


def response(*, latest="Не передавать.", draft="Можем общаться здесь."):
    return {
        "user": {"acts": [{"kind": "decline_transfer", "evidence": latest, "subject": "client",
                           "scope": "current_offer", "mode": "asserted"}], "information_need": None},
        "claims": {"predicates": {name: None for name in CLAIM_PREDICATES},
                   "requests": {name: None for name in REQUEST_SLOT_NAMES}},
        "style": {"accept": True, "flags": []},
    }


def stub(monkeypatch, value):
    calls = []
    def generate(messages, config, **kwargs):
        calls.append((deepcopy(messages), config, kwargs))
        return json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value
    monkeypatch.setattr(llm_agent, "generate_policy_critique", generate)
    return calls


def test_combined_channels_bind_exact_inputs_and_use_the_existing_bounded_transport(monkeypatch):
    calls = stub(monkeypatch, response())
    payload = {"facts": {}, "latest_user_message": "Не передавать."}
    original = deepcopy(payload)
    def collector(*args):
        pass
    result = analysis.analyze(payload, "Можем общаться здесь.", offered_frame(), configuration(),
                              usage_collector=collector)
    assert result.assessment.verdict is not None and result.assessment.verdict.accept
    assert result.assessment.reason == "accepted" and result.assessment.attempted
    assert result.semantics.user is not None and result.semantics.claims is not None
    assert result.authority is not None
    assert result.authority.latest_digest == sha256(payload["latest_user_message"].encode()).hexdigest()
    assert result.authority.draft_digest == sha256("Можем общаться здесь.".encode()).hexdigest()
    evaluated = evaluate_semantics(result.authority, result.semantics)
    assert evaluated.safe and evaluated.effective_frame.conversation_declined
    assert payload == original
    assert calls[0][1].model == "synthetic-extractor" and calls[0][1].temperature == 0
    assert calls[0][1].request_timeout == 10 and calls[0][1].max_tokens == 1000
    assert calls[0][2] == {"usage_collector": collector, "token_limit": 1000}


def test_a_bad_claim_does_not_erase_a_valid_user_refusal(monkeypatch):
    draft = "Номер нужен, чтобы закрепить ответы."
    value = response()
    value["claims"]["predicates"]["phone_required_to_save_chat"] = [{"value": True,
                                  "evidence": draft, "modality": "asserted", "scope": "chat"}]
    stub(monkeypatch, value)
    result = analysis.analyze({"latest_user_message": "Не передавать."}, draft, offered_frame(), configuration())
    assert result.authority is not None
    evaluation = evaluate_semantics(result.authority, result.semantics)
    assert evaluation.applied_user_acts and evaluation.effective_frame.conversation_declined
    assert not evaluation.safe and "phone_required_to_save_chat" in evaluation.issues
    assert result.assessment.verdict is not None and result.assessment.verdict.accept


@pytest.mark.parametrize("kind", ["request_phone", "request_transfer_consent"])
def test_grounded_speech_requests_do_not_become_authorized_by_a_style_acceptance(monkeypatch, kind):
    draft = "Подтвердите передачу." if kind == "request_transfer_consent" else "Оставьте номер телефона."
    value = response()
    slot = "transfer_consent_request" if kind == "request_transfer_consent" else "phone_request"
    value["claims"]["requests"][slot] = [{"mode": "requested", "evidence": draft}]
    frame = offered_frame() if kind == "request_transfer_consent" else DialogueFrame(
        "client.phone", "collecting_contacts", known_fields=frozenset({"client.phone"}),
    )
    stub(monkeypatch, value)
    result = analysis.analyze({"latest_user_message": "Не передавать."}, draft, frame, configuration())
    assert result.authority is not None and result.semantics.claims is not None
    assert result.assessment.verdict is not None and result.assessment.verdict.accept
    assert not evaluate_semantics(result.authority, result.semantics).safe


@pytest.mark.parametrize("style", [{"accept": "true", "flags": []},
                                     {"accept": True, "flags": ["unneeded_question"]},
                                     {"accept": False, "flags": ["foreign_private_flag"]}])
def test_style_schema_failure_keeps_usable_domain_channels(monkeypatch, style):
    value = response()
    value["style"] = style
    stub(monkeypatch, value)
    result = analysis.analyze({"latest_user_message": "Не передавать."}, "Можем общаться здесь.",
                              offered_frame(), configuration())
    assert result.assessment.reason == "invalid_schema" and result.assessment.verdict is None
    assert result.semantics.user is not None and result.semantics.claims is not None
    assert result.authority is not None
    assert evaluate_semantics(result.authority, result.semantics).effective_frame.conversation_declined
    assert "foreign_private_flag" not in repr(result)


@pytest.mark.parametrize("channel", ["user", "claims"])
def test_wrong_source_evidence_invalidates_only_its_own_channel(monkeypatch, channel):
    latest, draft = "Не передавать.", "Можем общаться здесь."
    value = response()
    if channel == "user":
        value["user"]["acts"][0]["evidence"] = draft
    else:
        value["claims"]["predicates"]["recipient_callback"] = [{"value": True,
                                      "evidence": latest, "modality": "asserted", "scope": "current_offer"}]
    stub(monkeypatch, value)
    result = analysis.analyze({"latest_user_message": latest}, draft, offered_frame(), configuration())
    assert getattr(result.semantics, channel) is None
    assert getattr(result.semantics, channel + "_error") == "invalid_evidence"
    assert getattr(result.semantics, "claims" if channel == "user" else "user") is not None


@pytest.mark.parametrize("kind", ["duplicate_style", "nonfinite_style", "duplicate_user"])
def test_duplicate_and_nonfinite_channels_do_not_poison_valid_neighbors(monkeypatch, kind):
    value = response()
    raw = json.dumps(value)
    if kind == "duplicate_style":
        raw = raw.replace('"style": {"accept": true,', '"style": {"accept": false, "accept": true,')
    elif kind == "nonfinite_style":
        raw = raw.replace('"style": {"accept": true,', '"style": {"accept": NaN,')
    else:
        raw = raw[:-1] + ', "user":' + json.dumps(value["user"]) + '}'
    stub(monkeypatch, raw)
    result = analysis.analyze({"latest_user_message": "Не передавать."}, "Можем общаться здесь.",
                              offered_frame(), configuration())
    assert result.semantics.claims is not None
    if kind == "duplicate_user":
        assert result.semantics.user is None and result.assessment.verdict is not None
    else:
        assert result.semantics.user is not None and result.assessment.verdict is None


@pytest.mark.parametrize("raw", ['[]', 'not json', '```json\n{}\n```', '{"foreign":{}}',
                                  'я' * 8193, '\ud800'],
                         ids=["array", "text", "fence", "foreign_root", "oversized_utf8", "unpaired_surrogate"])
def test_invalid_or_oversized_utf8_response_never_creates_usable_channels(monkeypatch, raw):
    stub(monkeypatch, raw)
    result = analysis.analyze({}, "Можем общаться здесь.", offered_frame(), configuration())
    assert result.assessment.verdict is None and result.assessment.reason == "invalid_schema"
    assert result.semantics.user is None and result.semantics.claims is None


@pytest.mark.parametrize("model", [None, "", " SYNTHETIC-SPEAKER "])
def test_an_absent_or_same_model_never_silently_uses_the_speaker(monkeypatch, model):
    calls = stub(monkeypatch, response())
    result = analysis.analyze({}, "Можем общаться здесь.", offered_frame(), configuration(extractor_model=model))
    assert result.assessment.reason == "model_not_distinct" and not result.assessment.attempted
    assert calls == [] and result.semantics.user is None


def test_latest_draft_and_metadata_projection_hide_known_private_values(monkeypatch):
    calls = stub(monkeypatch, {**response(), "user": {"acts": [], "information_need": None}})
    payload = {"facts": {"client": {"name": "Ирина Синтетическая", "phone": "+79991234567",
                                      "city": "Казань", "registration_address": "PRIVATE_ADDRESS"}},
               "latest_user_message": "Ирина Синтетическая, +79991234567, PRIVATE_ADDRESS, Казань.",
               "onloader": {"delivery_handle": "PRIVATE_HANDLE"},
               "customer_context": {"binding_digest": "PRIVATE_BINDING"},
               "chat_history": [{"role": "user", "content": "PRIVATE_HISTORY"}]}
    before = deepcopy(payload)
    result = analysis.analyze(payload, payload["latest_user_message"], offered_frame(), configuration(),
                              draft_redactor=lambda text: text)
    wire = json.dumps(calls[0][0], ensure_ascii=False)
    assert all(value not in wire for value in ["Ирина", "Синтетическая", "+79991234567", "PRIVATE_ADDRESS",
                                               "Казань", "PRIVATE_HANDLE", "PRIVATE_BINDING", "PRIVATE_HISTORY"])
    data = json.loads(calls[0][0][1]["content"])
    assert set(data) == {"latest_user_message", "draft", "context", "previous_assistant_message"}
    assert data["previous_assistant_message"] == ""
    assert "[имя скрыто]" in data["latest_user_message"] and "[телефон]" in data["draft"]
    assert result.authority is not None
    assert result.authority.latest_digest == sha256(data["latest_user_message"].encode()).hexdigest()
    assert result.authority.draft_digest == sha256(data["draft"].encode()).hexdigest()
    assert payload == before


def test_a_stale_authority_cannot_apply_a_valid_looking_refusal(monkeypatch):
    stub(monkeypatch, response())
    result = analysis.analyze({"latest_user_message": "Не передавать."}, "Можем общаться здесь.",
                              offered_frame(), configuration())
    assert result.authority is not None
    stale = TurnAuthority.from_frame(offered_frame(), "Другой ход.", "Можем общаться здесь.")
    evaluation = evaluate_semantics(stale, result.semantics)
    assert not evaluation.safe and not evaluation.applied_user_acts
    assert "context_mismatch" in evaluation.issues


def test_transport_failure_diagnostics_never_contain_private_provider_data(monkeypatch):
    def fail(*args, **kwargs):
        raise HTTPError("https://private.invalid", 429, "PRIVATE_PROVIDER_MESSAGE",
                        {"X-Private": "PRIVATE_HEADER"}, io.BytesIO(b"PRIVATE_BODY"))
    monkeypatch.setattr(llm_agent, "generate_policy_critique", fail)
    result = analysis.analyze({}, "Можем общаться здесь.", offered_frame(), configuration())
    assert result.assessment.failure is not None and result.assessment.failure.http_status == 429
    assert result.assessment.reason == "model_unavailable" and result.assessment.attempted
    assert all(value not in repr(result) for value in ["private.invalid", "PRIVATE_PROVIDER_MESSAGE",
                                                      "PRIVATE_HEADER", "PRIVATE_BODY"])


def test_context_failure_has_no_fabricated_authority_or_model_call(monkeypatch):
    calls = stub(monkeypatch, response())
    def fail(text):
        raise RuntimeError("PRIVATE_CONTEXT_FAILURE")
    result = analysis.analyze({}, "Можем общаться здесь.", offered_frame(), configuration(), draft_redactor=fail)
    assert result.authority is None and not result.assessment.attempted and calls == []
    assert result.semantics.user is None and "PRIVATE_CONTEXT_FAILURE" not in repr(result)


def test_a_late_phase_result_drops_every_channel(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(analysis.time, "monotonic", lambda: clock[0])
    def generate(*args, **kwargs):
        clock[0] = 10.0
        return json.dumps(response())
    monkeypatch.setattr(llm_agent, "generate_policy_critique", generate)
    result = analysis.analyze({"latest_user_message": "Не передавать."}, "Можем общаться здесь.",
                              offered_frame(), configuration())
    assert result.assessment.reason == "deadline_exhausted"
    assert result.semantics.user is None and result.semantics.claims is None


def test_actual_provider_body_uses_one_existing_json_request_and_records_usage(monkeypatch):
    bodies, usage = [], []
    def post(config, body, *, max_attempts):
        bodies.append((config, deepcopy(body), max_attempts))
        return {"choices": [{"message": {"content": json.dumps(response())}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 30,
                          "prompt_tokens_details": {"cached_tokens": 4}}}
    monkeypatch.setattr(llm_agent, "_post", post)
    result = analysis.analyze({"latest_user_message": "Не передавать."}, "Можем общаться здесь.",
                              offered_frame(), configuration(), usage_collector=lambda *args: usage.append(args))
    assert result.assessment.verdict is not None and len(bodies) == 1
    config, body, attempts = bodies[0]
    assert attempts == 1 and config.request_timeout == 10 and body["max_tokens"] == 1000
    assert body["model"] == "synthetic-extractor" and body["response_format"] == {"type": "json_object"}
    assert usage == [("synthetic-extractor", 20, 30, 4)]



def test_the_embedded_empty_example_is_complete_valid_json_with_no_model_coverage_flags():
    # Exercise the text actually sent to the provider, rather than only its Python builder.
    marker = "Полный компактный образец ФОРМАТА с пустыми значениями (не желаемый результат классификации):\n"
    raw = analysis._SYSTEM.split(marker, 1)[1].split("\nКлассификационные примеры;", 1)[0]
    value = json.loads(raw)
    assert set(value["claims"]["predicates"]) == set(CLAIM_PREDICATES)
    assert set(value["claims"]["requests"]) == set(REQUEST_SLOT_NAMES)
    assert all(item is None for item in value["claims"]["predicates"].values())
    assert all(item is None for item in value["claims"]["requests"].values())
    assert set(value["claims"]) == {"predicates", "requests"}
    parsed = parse_semantics(raw, latest_user_message="", draft="")
    assert parsed.user is not None and parsed.claims is not None
    assert parsed.claims.claims == () and parsed.claims.speech_acts == ()


@pytest.mark.parametrize("example", analysis._EXAMPLES, ids=[f"example_{i}" for i in range(len(analysis._EXAMPLES))])
def test_every_shipped_prompt_example_has_all_required_slots_and_grounded_clause_evidence(example):
    raw = json.dumps(example["result"], ensure_ascii=False)
    parsed = parse_semantics(raw, latest_user_message=example["input"]["latest_user_message"],
                             draft=example["input"]["draft"])
    assert parsed.user is not None and parsed.claims is not None
    assert parsed.user_error is None and parsed.claims_error is None and parsed.need_error is None
    assert analysis.SemanticVerdict.model_validate(example["result"]["style"]) is not None


@pytest.mark.parametrize("container,slot", [("predicates", name) for name in CLAIM_PREDICATES]
                         + [("requests", name) for name in REQUEST_SLOT_NAMES])
def test_missing_required_nullable_slot_fails_only_claims_even_when_style_accepts(monkeypatch, container, slot):
    value = response()
    del value["claims"][container][slot]
    stub(monkeypatch, value)
    result = analysis.analyze({"latest_user_message": "Не передавать."}, "Можем общаться здесь.",
                              offered_frame(), configuration())
    assert result.semantics.claims is None and result.semantics.claims_error == "invalid_schema"
    assert result.assessment.verdict is not None and result.assessment.verdict.accept
    assert result.authority is not None
    evaluated = evaluate_semantics(result.authority, result.semantics)
    assert evaluated.effective_frame.conversation_declined and not evaluated.safe


@pytest.mark.parametrize("need", [{"kind": "foreign", "evidence": "Для чего сведения?"},
                                 {"kind": "transfer_purpose", "evidence": "WRONG_SOURCE"}])
def test_bad_following_information_need_cannot_discard_an_own_refusal_clause(monkeypatch, need):
    latest = "Не передавать. Для чего сведения?"
    value = response(latest="Не передавать.")
    value["user"]["information_need"] = need
    stub(monkeypatch, value)
    result = analysis.analyze({"latest_user_message": latest}, "Можем общаться здесь.",
                              offered_frame(), configuration())
    assert result.semantics.user is not None and result.semantics.user.information_need is None
    assert result.semantics.need_error is not None
    assert result.authority is not None
    assert evaluate_semantics(result.authority, result.semantics).effective_frame.conversation_declined


def test_uncertainty_and_negation_remain_different_in_the_transported_response(monkeypatch):
    draft = "Звонок не подтверждён. Компания не позвонит."
    value = response()
    value["claims"]["predicates"]["recipient_callback"] = [
        {"value": True, "evidence": "Звонок не подтверждён.", "modality": "not_established", "scope": "current_offer"},
        {"value": False, "evidence": "Компания не позвонит.", "modality": "asserted", "scope": "current_offer"},
    ]
    stub(monkeypatch, value)
    result = analysis.analyze({"latest_user_message": "Не передавать."}, draft, offered_frame(), configuration())
    assert result.semantics.claims is not None
    assert [(claim.value, claim.modality) for claim in result.semantics.claims.claims] == [
        (True, "not_established"), (False, "asserted")]


def test_a_quoted_proposition_and_outer_uncertainty_keep_separate_evidence(monkeypatch):
    draft = "В письме: «Других источников контакта нет». Это утверждение не подтверждено."
    value = response()
    value["claims"]["predicates"]["recipient_no_other_contact_source"] = [
        {"value": True, "evidence": "Других источников контакта нет", "modality": "quoted", "scope": "current_offer"},
        {"value": True, "evidence": "Это утверждение не подтверждено.", "modality": "not_established", "scope": "current_offer"},
    ]
    stub(monkeypatch, value)
    result = analysis.analyze({"latest_user_message": "Не передавать."}, draft, offered_frame(), configuration())
    assert result.semantics.claims is not None
    assert len(result.semantics.claims.claims) == 2
    assert all(claim.value for claim in result.semantics.claims.claims)
    assert result.semantics.claims.claims[0].modality == "quoted"
    assert result.semantics.claims.claims[1].modality == "not_established"



@pytest.mark.parametrize("elapsed,configured_timeout,expected_timeout,accepted", [
    (7.649, 120, 10, True), (10.001, 120, 10, False), (10.0, 120, 10, False),
    (2.9, 3, 3, True), (3.0, 3, 3, False)])
def test_v14_phase_budget_preserves_smaller_deadlines_and_rejects_late_authority(
        monkeypatch, elapsed, configured_timeout, expected_timeout, accepted):
    clock = [0.0]
    monkeypatch.setattr(analysis.time, "monotonic", lambda: clock[0])
    seen = []
    def generate(messages, config, **kwargs):
        seen.append(config)
        clock[0] = elapsed
        return json.dumps(response())
    monkeypatch.setattr(llm_agent, "generate_policy_critique", generate)
    result = analysis.analyze({"latest_user_message": "Не передавать."}, "Можем общаться здесь.",
                              offered_frame(), configuration(request_timeout=configured_timeout))
    assert len(seen) == 1 and seen[0].request_timeout == expected_timeout
    assert seen[0].max_tokens == 1000
    if accepted:
        assert result.assessment.reason == "accepted" and result.semantics.user is not None
        assert result.authority is not None
        assert evaluate_semantics(result.authority, result.semantics).effective_frame.conversation_declined
    else:
        assert result.assessment.reason == "deadline_exhausted"
        assert result.semantics.user is None and result.semantics.claims is None


def test_the_callback_example_keeps_the_quoted_and_own_asserted_propositions_separate():
    examples = [example for example in analysis._EXAMPLES if len(
        example["result"]["claims"]["predicates"].get("recipient_callback") or []) == 2]
    assert len(examples) == 1
    example = examples[0]
    entries = example["result"]["claims"]["predicates"]["recipient_callback"]
    assert [entry["modality"] for entry in entries] == ["quoted", "asserted"]
    assert all(entry["value"] is True for entry in entries)
    evidence = [entry["evidence"] for entry in entries]
    assert all(example["input"]["draft"].count(span) == 1 for span in evidence)
    assert len(set(evidence)) == 2


def test_the_v14_phone_refusal_example_does_not_turn_a_chat_question_into_a_debt_need():
    examples = [example for example in analysis._EXAMPLES
                if example["input"]["context"].get("mode") == "debt"
                and any(act["kind"] == "decline_phone" for act in example["result"]["user"]["acts"])]
    assert len(examples) == 1
    example = examples[0]
    context = example["input"]["context"]
    assert context["expected"] == "client.phone" and context["offer_present"] is False
    assert example["result"]["user"]["information_need"] is None
    acts = example["result"]["user"]["acts"]
    assert len(acts) == 1 and acts[0]["kind"] == "decline_phone" and acts[0]["mode"] == "asserted"
    assert example["input"]["latest_user_message"].count(acts[0]["evidence"]) == 1



@pytest.mark.parametrize("language", ["json", "JSON", ""])
def test_one_complete_json_fence_preserves_all_independent_channels_and_unmodified_raw(monkeypatch, language):
    latest, draft = "Не передавать.", "Не буду передавать."
    value = response()
    value["claims"]["predicates"]["conversation_refusal_acknowledged"] = [
        {"value": True, "evidence": draft, "modality": "asserted", "scope": "current_offer"}]
    raw = "```" + language + "\n" + json.dumps(value, ensure_ascii=False) + "\n```"
    stub(monkeypatch, raw)
    seen = []
    parse_domain, parse_style = analysis.parse_semantics, analysis.parse_style_channel
    def domain(text, **kwargs):
        seen.append(("domain", text is raw))
        return parse_domain(text, **kwargs)
    def style(text):
        seen.append(("style", text is raw))
        return parse_style(text)
    monkeypatch.setattr(analysis, "parse_semantics", domain)
    monkeypatch.setattr(analysis, "parse_style_channel", style)
    result = analysis.analyze({"latest_user_message": latest}, draft, offered_frame(), configuration())
    assert result.assessment.reason == "accepted" and result.assessment.verdict is not None
    assert result.assessment.verdict.accept and result.semantics.user is not None and result.semantics.claims is not None
    assert result.authority is not None and evaluate_semantics(result.authority, result.semantics).safe
    assert evaluate_semantics(result.authority, result.semantics).effective_frame.conversation_declined
    assert result.semantics.claims.claims[0].predicate == "conversation_refusal_acknowledged"
    assert seen == [("domain", True), ("style", True)]


def test_the_v15_balanced_examples_distinguish_call_and_contactability_quoted_propositions():
    callbacks = [example for example in analysis._EXAMPLES if len(
        example["result"]["claims"]["predicates"].get("recipient_callback") or []) == 2]
    contactability = [example for example in analysis._EXAMPLES if len(
        example["result"]["claims"]["predicates"].get("recipient_no_other_contact_source") or []) == 2]
    assert len(callbacks) == len(contactability) == 1
    callback_entries = callbacks[0]["result"]["claims"]["predicates"]["recipient_callback"]
    contact_entries = contactability[0]["result"]["claims"]["predicates"]["recipient_no_other_contact_source"]
    assert [item["modality"] for item in callback_entries] == ["quoted", "asserted"]
    assert [item["modality"] for item in contact_entries] == ["quoted", "not_established"]
    assert all(item["value"] is True for item in [*callback_entries, *contact_entries])
    assert contactability[0]["result"]["claims"]["predicates"]["recipient_callback"] is None


def test_model_information_need_is_an_advisory_hint_not_a_new_authoritative_purpose_requirement(monkeypatch):
    latest, draft = "Если откажусь передавать, что будет?", "Можем общаться здесь."
    value = response()
    value["user"] = {"acts": [], "information_need": {"kind": "transfer_purpose", "evidence": latest}}
    calls = stub(monkeypatch, value)
    result = analysis.analyze({"latest_user_message": latest}, draft, offered_frame(), configuration())
    assert result.authority is not None and result.authority.frame.question_goal is None
    evaluated = evaluate_semantics(result.authority, result.semantics)
    assert evaluated.safe and "consultation_purpose_missing" not in evaluated.issues
    data = json.loads(calls[0][0][1]["content"])
    assert data["context"]["question_goal"] is None
    assert not evaluated.applied_user_acts


def test_a_code_owned_purpose_goal_still_requires_grounded_purpose_despite_a_missing_model_hint(monkeypatch):
    frame = replace(offered_frame(), question_goal="offer_information_purpose")
    value = response()
    value["user"] = {"acts": [], "information_need": None}
    calls = stub(monkeypatch, value)
    result = analysis.analyze({"latest_user_message": "Продолжим."}, "Можем общаться здесь.", frame, configuration())
    assert result.authority is not None
    evaluated = evaluate_semantics(result.authority, result.semantics)
    assert not evaluated.safe and "consultation_purpose_missing" in evaluated.issues
    data = json.loads(calls[0][0][1]["content"])
    assert data["context"]["question_goal"] == frame.question_goal



def test_an_already_bound_model_hint_goal_is_visible_but_cannot_become_a_hard_purpose_requirement(monkeypatch):
    frame = replace(offered_frame(), question_goal="offer_information_purpose", question_goal_origin="model_hint")
    value = response()
    value["user"] = {"acts": [], "information_need": None}
    calls = stub(monkeypatch, value)
    result = analysis.analyze({"latest_user_message": "Продолжим."}, "Можем общаться здесь.", frame, configuration())
    assert result.authority is not None and result.authority.frame.question_goal_origin == "model_hint"
    evaluated = evaluate_semantics(result.authority, result.semantics)
    assert evaluated.safe and "consultation_purpose_missing" not in evaluated.issues
    data = json.loads(calls[0][0][1]["content"])
    assert data["context"]["question_goal_origin"] == "model_hint"
    assert data["context"]["question_goal"] == "offer_information_purpose"


def test_authoritative_goal_origin_remains_code_even_if_the_model_proposes_another_need(monkeypatch):
    latest = "Что означает реструктуризация?"
    frame = replace(offered_frame(), question_goal="offer_information_purpose", question_goal_origin="code")
    value = response()
    value["user"] = {"acts": [], "information_need": {"kind": "debt_explanation", "evidence": latest}}
    calls = stub(monkeypatch, value)
    result = analysis.analyze({"latest_user_message": latest}, "Можем общаться здесь.", frame, configuration())
    assert result.authority is not None
    evaluated = evaluate_semantics(result.authority, result.semantics)
    assert not evaluated.safe and "consultation_purpose_missing" in evaluated.issues
    assert evaluated.effective_frame.question_goal == "offer_information_purpose"
    assert evaluated.effective_frame.question_goal_origin == "code"
    data = json.loads(calls[0][0][1]["content"])
    assert data["context"]["question_goal_origin"] == "code"
