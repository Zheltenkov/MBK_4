"""Avoid auditing discarded G text without dropping incoming or outgoing authority."""
from dataclasses import replace
from io import BytesIO
import json

import pytest

from config import AppConfig
import llm_agent
import policy_grounded_voice as grounded
from tests.test_known_contact_recovery_20261111 import frame, hold, FACTS, LATEST
from tests.test_policy_grounded_voice import semantic_raw, act
from tests.test_policy_recovery_independent import run_provider_sequence


TANGENT = "Телефон уже есть, но отменить заявку или удалить номер я не могу."
ILLEGAL = "Согласие на передачу получено."
GOOD = "Можем обсудить вопросы здесь."


def roles(requests):
    return ["G" if body["model"] == "synthetic-speaker" else "C" for body in requests]


def role_aware_run(monkeypatch, selected, latest, drafts, analysis):
    """The same G/C answers remain valid with and without the skipped request."""
    cfg = AppConfig(openrouter_api_key="synthetic-unused", model="synthetic-speaker",
        extractor_model="synthetic-analysis", request_timeout=40, max_tokens=1000,
        policy_voice_enabled=True, policy_semantic_enabled=True)
    requests, generated = [], iter(drafts)
    def open_request(configuration, request):
        body = json.loads(request.data)
        requests.append(body)
        content = next(generated) if body["model"] == cfg.model else analysis
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    result = grounded.render({"facts": FACTS, "latest_user_message": latest}, cfg,
        frame=selected, fallback=hold())
    return result, requests


def test_whole_known_reference_skips_only_c_for_rejected_g1(monkeypatch):
    result, requests = role_aware_run(monkeypatch, frame(), LATEST,
        [TANGENT, GOOD], semantic_raw())
    assert roles(requests) == ["G", "G", "C"]
    assert result.spoken.text == GOOD and result.spoken.source == "model"
    assert result.spoken.critic_calls == 1 and len(result.spoken.critic_assessments) == 1
    assert not result.applied_user_acts and not result.effective_frame.conversation_declined


@pytest.mark.parametrize("paused", [False, True])
def test_twice_rejected_code_proven_reference_serves_only_code_fallback(monkeypatch, paused):
    selected = replace(frame(), conversation_paused=paused)
    result, requests = run_provider_sequence(monkeypatch, selected, LATEST,
        [TANGENT, TANGENT], facts=FACTS, fallback=hold())
    assert roles(requests) == ["G", "G"]
    assert result.spoken.source == "fallback" and TANGENT not in result.spoken.text
    assert result.spoken.critic_calls == 0 and not result.spoken.critic_assessments
    assert result.paused is paused and result.effective_frame.conversation_paused is paused
    assert not result.applied_user_acts and result.spoken.asked_field is None


def test_valid_c1_incoming_no_survives_without_auditing_invalid_g2(monkeypatch):
    latest = "Не передавайте мои данные. Какие условия?"
    selected = replace(frame(), known_phone_reference=False)
    first = semantic_raw(user_acts=[act("Не передавайте мои данные.")])
    result, requests = role_aware_run(monkeypatch, selected, latest,
        [ILLEGAL, ILLEGAL], first)
    assert roles(requests) == ["G", "C", "G"]
    assert result.effective_frame.conversation_declined
    assert "decline_transfer" in result.applied_user_acts
    assert result.channel_diagnostics[0].user_valid
    assert result.spoken.reason == "code_invalid_repair" and ILLEGAL not in result.spoken.text
    assert result.spoken.critic_calls == 1 and result.spoken.asked_field is None


def test_unknown_input_keeps_c1_and_pauses_on_failure(monkeypatch):
    selected = replace(frame(), known_phone_reference=False)
    result, requests = run_provider_sequence(monkeypatch, selected, "Расскажите об условиях.",
        [ILLEGAL, TimeoutError("synthetic-C-timeout")], facts=FACTS, fallback=hold())
    assert roles(requests) == ["G", "C"]
    assert result.paused and not result.applied_user_acts
    assert result.spoken.critic_assessments[0].failure is not None


def test_valid_repaired_g2_requires_its_own_c2_not_c1_approval(monkeypatch):
    result, requests = run_provider_sequence(monkeypatch, replace(frame(), known_phone_reference=False),
        "Расскажите об условиях.", [ILLEGAL, semantic_raw(), GOOD, semantic_raw()], facts=FACTS, fallback=hold())
    assert roles(requests) == ["G", "C", "G", "C"]
    assert result.spoken.text == GOOD and result.spoken.critic_calls == 2


def test_valid_known_reference_g1_still_requires_c(monkeypatch):
    result, requests = run_provider_sequence(monkeypatch, frame(), LATEST,
        [GOOD, semantic_raw()], facts=FACTS, fallback=hold())
    assert roles(requests) == ["G", "C"]
    assert result.spoken.source == "model" and result.spoken.critic_calls == 1


@pytest.mark.parametrize("selected", [replace(frame(), known_phone_reference=False),
    replace(frame(), mode="onloader"), replace(frame(), known_fields=frozenset({"intent"}))])
def test_missing_proof_or_other_mode_keeps_first_c(monkeypatch, selected):
    _, requests = run_provider_sequence(monkeypatch, selected, LATEST,
        [ILLEGAL, semantic_raw(), GOOD, semantic_raw()], facts=FACTS, fallback=hold())
    assert roles(requests)[:2] == ["G", "C"]
