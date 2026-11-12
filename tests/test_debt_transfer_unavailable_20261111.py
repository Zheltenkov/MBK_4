"""Current-chat capability denial is not a historical transfer receipt."""
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import json

import pytest

import core
import llm_agent
import policy_semantic_analysis
import policy_grounded_voice
import prompts
from dialogue_frame import DialogueFrame
from policy_semantics import TurnAuthority, evaluate_semantics, parse_semantics
from tests.test_policy_faq_recovery_20261111 import collected_state
from tests.test_policy_semantics import wire
from config import AppConfig


DRAFT = "Передачу заявки из этого чата сейчас оформить не могу."
LATEST = "А что специалист будет делать дальше и что мне подготовить?"


def frame(blocked=True):
    return DialogueFrame(None, "collected_held", known_fields=frozenset({"intent"}),
                         transfer_blocked=blocked)


def body(evidence=DRAFT, *, value=False, modality="asserted", scope="current_offer"):
    return wire(claims=[{"predicate": "transfer_completed", "value": value,
                         "evidence": evidence, "modality": modality, "scope": scope}])


def evaluate(draft=DRAFT, *, selected=None, evidence=None, **changes):
    current = selected or frame()
    raw = body(evidence or draft, **changes)
    parsed = parse_semantics(json.dumps(raw, ensure_ascii=False), latest_user_message=LATEST, draft=draft)
    return evaluate_semantics(TurnAuthority.from_frame(current, LATEST, draft), parsed)


def test_actual_negative_capability_claim_requires_both_code_and_draft_proof():
    result = evaluate()
    assert result.safe and not result.issues
    assert not result.applied_user_acts
    assert not evaluate(selected=frame(False)).safe
    assert DialogueFrame(None, "collected_held").transfer_blocked is False


@pytest.mark.parametrize("draft,evidence", [
    ("Передачу заявки из этого чата раньше не оформлял.", None),
    ("Заявку из этого чата не передавали. " + DRAFT, "Заявку из этого чата не передавали."),
    ("Я уже передал заявку из этого чата.", None),
    ("Передачу заявки из другого чата сейчас оформить не могу.", None),
    ("Передачу заявки специалисту сейчас оформить не могу.", None),
    ("Не вижу причин, почему передачу заявки из этого чата сейчас оформить не могу.", None),
    ("Передачу заявки из этого чата сейчас оформить не могу не выполнить.", None),
    ("Передачу заявки из этого чата сейчас оформить не могу?", None),
    ("Если передачу заявки из этого чата сейчас оформить не могу, что дальше?", DRAFT[:-1].lower()),
    ("Пользователь сказал: «" + DRAFT + "»", DRAFT),
    ("В примере написано: «Это пример. " + DRAFT + " Это только пример».", DRAFT),
    ("Пользователь сказал: " + DRAFT, DRAFT),
    ("В примере: " + DRAFT, DRAFT),
])
def test_receipts_or_cropped_nonasserted_scope_never_borrow_capability_authority(draft, evidence):
    result = evaluate(draft, evidence=evidence)
    assert not result.safe
    assert "unverified_transfer_completed" in result.issues


@pytest.mark.parametrize("changes", [
    {"value": True}, {"modality": "not_established"}, {"scope": "chat"}, {"scope": "global"},
])
def test_no_new_positive_or_uncertainty_or_other_scope_authority(changes):
    assert not evaluate(**changes).safe


@pytest.mark.parametrize("selected", [
    replace(frame(), mode="onloader"),
    replace(frame(), known_fields=frozenset()),
    replace(frame(), offer_present=True, offer_status="signal_pending"),
])
def test_flag_alone_cannot_override_real_offer_or_nonactive_mode(selected):
    assert not evaluate(selected=selected).safe


def test_private_flag_changes_authority_digest_but_not_public_model_context():
    plain, blocked = frame(False), frame(True)
    assert policy_semantic_analysis._public_context(plain) == policy_semantic_analysis._public_context(blocked)
    assert "transfer_blocked" not in policy_semantic_analysis._public_context(blocked)
    assert prompts.policy_verified_context(plain) == prompts.policy_verified_context(blocked)
    parsed = parse_semantics(json.dumps(body()), latest_user_message=LATEST, draft=DRAFT)
    authority = TurnAuthority.from_frame(plain, LATEST, DRAFT)
    forged = replace(authority, frame=blocked)
    result = evaluate_semantics(forged, parsed)
    assert not result.safe and "context_mismatch" in result.issues


def test_model_boolean_schema_stays_strict_with_blocked_code_authority():
    raw = body()
    raw["claims"]["predicates"]["transfer_completed"][0]["value"] = "false"
    parsed = parse_semantics(json.dumps(raw), latest_user_message=LATEST, draft=DRAFT)
    result = evaluate_semantics(TurnAuthority.from_frame(frame(), LATEST, DRAFT), parsed)
    assert not result.claims_valid and not result.safe


def test_private_flag_does_not_change_actual_first_g_or_c_requests(monkeypatch):
    config = AppConfig(openrouter_api_key="synthetic-unused", model="synthetic-speaker",
                       extractor_model="synthetic-analysis", max_tokens=2048, request_timeout=40,
                       policy_voice_enabled=True, policy_semantic_enabled=True)
    requests = []
    def open_request(configuration, request):
        request_body = json.loads(request.data)
        requests.append(request_body)
        content = DRAFT if request_body["model"] == config.model else json.dumps(body(), ensure_ascii=False)
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    payload = {"facts": {}, "latest_user_message": LATEST}
    policy_grounded_voice.render(payload, config, frame=frame(False), fallback="Можем обсудить здесь.")
    first_requests = requests[:2]
    requests.clear()
    policy_grounded_voice.render(payload, config, frame=frame(True), fallback="Можем обсудить здесь.")
    assert requests == first_requests
    assert "transfer_blocked" not in json.dumps(requests, ensure_ascii=False)


def test_actual_core_turn_keeps_valid_g1_with_one_g_one_c_and_no_permission_changes(monkeypatch):
    state = collected_state()
    before = deepcopy(state)
    calls = []
    config = AppConfig(openrouter_api_key="synthetic-unused", model="synthetic-speaker",
                       extractor_model="synthetic-analysis", max_tokens=2048, request_timeout=40,
                       policy_voice_enabled=True, policy_semantic_enabled=True, policy_factual_audit_enabled=True)
    def open_request(configuration, request):
        request_body = json.loads(request.data)
        role = "G" if request_body["model"] == config.model else "C"
        calls.append((role, request_body))
        content = DRAFT if role == "G" else json.dumps(body(), ensure_ascii=False)
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    text = core._policy_conversation(state, LATEST, config)
    spoken = state["_policy_spoken"]
    committed = core.commit_turn(state, LATEST, text)["current_state"]
    assert text == DRAFT and spoken.source == "model"
    assert [role for role, _ in calls] == ["G", "C"]
    assert "transfer_blocked" not in json.dumps([item for _, item in calls], ensure_ascii=False)
    for key in ("facts", "questionnaire_policy", "delivery", "crm_lead", "onloader"):
        assert committed.get(key) == before.get(key)
    assert committed["chat_history"][-2:] == [
        {"role": "user", "content": LATEST}, {"role": "assistant", "content": DRAFT}]
    assert not spoken.code_user_acts and spoken.critic_assessments[0].verdict.accept
