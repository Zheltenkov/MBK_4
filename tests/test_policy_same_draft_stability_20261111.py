"""Incoming C noise cannot force a new G or create conversational authority."""
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import json

import pytest

from config import AppConfig
import core
from dialogue_frame import DialogueFrame
import llm_agent
import policy_grounded_voice as grounded
import policy_voice
from tests.test_policy_faq_recovery_20261111 import collected_state
from tests.test_policy_semantics import wire


LATEST = "Да, согласен на передачу специалисту. Телефон я уже оставил выше."
EVIDENCE = "Да, согласен на передачу специалисту."
NEUTRAL = "Передачу заявки из этого чата сейчас оформить не могу."


def wrong_no():
    return {"kind": "decline_transfer", "evidence": EVIDENCE, "subject": "client",
            "scope": "current_offer", "mode": "asserted"}


def selected(*, phone=False, declined=False):
    known = {"intent", "debts.total", "client.name", "client.region_class"}
    if not phone:
        known.add("client.phone")
    return DialogueFrame("client.phone" if phone else None, "collecting_contacts" if phone else "collected_held",
                         known_fields=frozenset(known), require_question=phone,
                         conversation_declined=declined, transfer_blocked=True)


def sequence(monkeypatch, drafts=(NEUTRAL,), *, frame=None, latest=LATEST, acts=None,
             claims=None, speech=None, factual=False, factual_verdict="supported", c_timeout=False, raw_c=None):
    config = AppConfig(openrouter_api_key="synthetic-unused", model="synthetic-speaker",
                       extractor_model="synthetic-analysis", max_tokens=2048, request_timeout=40,
                       policy_voice_enabled=True, policy_semantic_enabled=True, policy_factual_audit_enabled=factual)
    roles, raw, usage = [], [], []
    generation = 0
    def open_request(configuration, request):
        nonlocal generation
        request_body = json.loads(request.data)
        if request_body["model"] == config.model:
            role = "G"
            content = drafts[min(generation, len(drafts) - 1)]
            generation += 1
        else:
            user = json.loads(request_body["messages"][-1]["content"])
            role = "F" if user.get("wire_version", "").startswith("factual-audit") else "C"
            if role == "C":
                content = raw_c or json.dumps(wire(acts=[wrong_no()] if acts is None else acts,
                                                   claims=claims, speech_acts=speech), ensure_ascii=False)
            else:
                source_id = user["source_catalog"][0]["id"]
                content = json.dumps({"rows": [{"unit_id": unit["id"], "relation": factual_verdict,
                    "source_ids": [source_id] if factual_verdict == "supported" else [],
                    "mismatch": "none" if factual_verdict == "supported" else "insufficient_source"}
                    for unit in user["draft_units"]]})
        roles.append(role)
        raw.append(request_body)
        if (role == "C" and c_timeout) or (role == "F" and factual_verdict == "timeout"):
            raise TimeoutError("synthetic-validator-timeout")
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}],
                                  "usage": {"prompt_tokens": 2, "completion_tokens": 3}}).encode())
    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    payload = {"latest_user_message": latest, "facts": {"debts": {"total": 600000},
        "client": {"name": "Сергей", "phone": "+70000000001", "region_class": "moscow_mo"}}}
    result = grounded.render(payload, config, frame=frame or selected(), fallback="Можем обсудить здесь.",
        usage_collector=lambda *args, **kwargs: usage.append("G"),
        critic_usage_collector=lambda *args, **kwargs: usage.append("C"),
        factual_usage_collector=lambda *args, **kwargs: usage.append("F"))
    return result, roles, raw, usage


@pytest.mark.parametrize("phone", [False, True])
def test_wrong_no_on_affirmative_returns_conservative_reply_without_regeneration(monkeypatch, phone):
    result, roles, _, usage = sequence(monkeypatch, frame=selected(phone=phone))
    assert result.spoken.text != NEUTRAL and result.spoken.source == "fallback"
    assert roles == usage == ["G", "C"]
    assert result.paused and not result.applied_user_acts
    assert not result.effective_frame.conversation_declined and not result.effective_frame.refused_fields
    assert result.spoken.asked_field is None
    assert result.spoken.critic_assessments[0].verdict.accept
    diagnostic = result.channel_diagnostics[0]
    assert not diagnostic.user_valid and not diagnostic.strict_safe and diagnostic.outgoing_safe
    assert "user_refusal_unbound" in diagnostic.user_issues and not diagnostic.claims_issues


@pytest.mark.parametrize("draft", [
    "Хорошо, ваш отказ от передачи принят.", "Поняла, вы отказались от передачи.",
    "Я сохранила ваш отказ.", "Хорошо, согласие на передачу принято.",
    "Оставьте номер телефона.", "Как к вам обращаться?", "Вы согласны на передачу данных?",
    "Заявка уже отправлена специалисту.", "Вам обязательно позвонят.",
])
def test_incoming_uncertainty_cannot_retain_decision_collection_or_receipt_even_with_null_c_claims(monkeypatch, draft):
    result, _, _, _ = sequence(monkeypatch, drafts=(draft,), frame=selected(phone=True))
    assert result.spoken.text != draft or result.spoken.source != "model"
    assert not result.applied_user_acts and not result.effective_frame.conversation_declined
    assert not result.effective_frame.refused_fields


@pytest.mark.parametrize("verdict", ["supported", "unknown", "timeout"])
def test_required_f_remains_independent_and_no_usage_is_duplicated(monkeypatch, verdict):
    answer = "Закон предусматривает судебное и внесудебное банкротство."
    result, roles, _, usage = sequence(monkeypatch, drafts=(answer,), acts=[], factual=True, factual_verdict=verdict)
    if verdict == "supported":
        assert result.spoken.text == answer and result.spoken.source == "model"
        assert roles == usage == ["G", "C", "F"]
    else:
        assert result.spoken.source == "fallback"
        assert result.spoken.text != answer
        assert roles.count("G") <= 2 and roles.count("C") <= 2
        assert usage.count("G") == roles.count("G") and usage.count("C") == roles.count("C")
        assert usage.count("F") == (0 if verdict == "timeout" else roles.count("F"))
    assert not result.applied_user_acts


def test_c_timeout_still_has_no_independent_outgoing_proof(monkeypatch):
    result, roles, _, _ = sequence(monkeypatch, c_timeout=True)
    assert result.spoken.source == "fallback" and result.spoken.text != NEUTRAL
    assert roles == ["G", "C"]


def test_body_changed_after_effective_frame_cannot_reuse_c_proof(monkeypatch):
    original = policy_voice._model_reply
    def changed(text, frame, reason):
        reply = original(text, frame, reason)
        return replace(reply, text=reply.text + " Другой текст.") if reply and frame.conversation_paused else reply
    monkeypatch.setattr(policy_voice, "_model_reply", changed)
    result, roles, _, _ = sequence(monkeypatch)
    assert not (result.spoken.source == "model" and "Другой текст" in result.spoken.text)
    assert roles.count("G") <= 2


def test_actual_core_wrong_no_never_commits_a_negative_or_formal_permission(monkeypatch):
    # Reuse the real HTTP model boundary while core constructs the full frame.
    state = collected_state()
    before = deepcopy(state)
    calls = []
    config = AppConfig(openrouter_api_key="synthetic-unused", model="synthetic-speaker",
                       extractor_model="synthetic-analysis", max_tokens=2048, request_timeout=40,
                       policy_voice_enabled=True, policy_semantic_enabled=True)
    def open_request(configuration, request):
        request_body = json.loads(request.data)
        role = "G" if request_body["model"] == config.model else "C"
        calls.append(role)
        content = NEUTRAL if role == "G" else json.dumps(wire(acts=[wrong_no()]))
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    text = core._policy_conversation(state, LATEST, config)
    committed = core.commit_turn(state, LATEST, text)["current_state"]
    assert text != NEUTRAL and calls == ["G", "C"]
    for key in ("facts", "questionnaire_policy", "delivery", "crm_lead", "onloader"):
        assert committed.get(key) == before.get(key)
    assert committed["conversation_semantics"]["paused"] is True


# Exact synthetic/anonymized R3 C output, call b4059f81379f4469; not relabelled.
R3_C_RAW = '{\n  "user": {\n    "acts": [\n      {\n        "kind": "decline_transfer",\n        "evidence": "Да, согласен на передачу специалисту.",\n        "subject": "client",\n        "scope": "current_offer",\n        "mode": "asserted"\n      }\n    ],\n    "information_need": null\n  },\n  "claims": {\n    "predicates": {\n      "phone_required_to_save_chat": null,\n      "phone_required_to_chat": null,\n      "consultation_purpose": null,\n      "engineering_transfer_purpose": null,\n      "recipient_callback": null,\n      "recipient_no_other_contact_source": null,\n      "exclusive_recipient": null,\n      "transfer_completed": null,\n      "consent_recorded": null,\n      "refusal_recorded": null,\n      "conversation_refusal_acknowledged": null\n    },\n    "requests": {\n      "phone_request": null,\n      "transfer_consent_request": null\n    }\n  },\n  "style": {\n    "accept": true,\n    "flags": []\n  }\n}'
R3_G1 = "Передачу заявки отсюда оформить не могу, но продолжить разбор вашей ситуации в чате — пожалуйста.\n\nХотите уточнить что-то по процедурам списания или уменьшения платежей?"


@pytest.mark.parametrize("verdict", ["supported", "unknown", "timeout"])
def test_exact_r3_wrong_incoming_abstains_without_a_second_generation(monkeypatch, verdict):
    result, roles, _, _ = sequence(monkeypatch, drafts=(R3_G1,), raw_c=R3_C_RAW,
                                   factual=True, factual_verdict=verdict)
    assert result.spoken.critic_assessments[0].verdict.accept
    assert result.channel_diagnostics[0].user_issues == ("user_refusal_unbound",)
    assert result.paused and not result.applied_user_acts
    assert not result.effective_frame.conversation_declined and not result.effective_frame.refused_fields
    # Incoming uncertainty now returns a conservative answer directly; neither
    # a different G nor a successful factual audit supplies decision authority.
    assert result.spoken.source == "fallback" and result.spoken.text != R3_G1
    assert roles == ["G", "C"]


@pytest.mark.parametrize("flag", ["wrong_current_turn", "unsupported_claim"])
def test_outgoing_only_proof_does_not_override_independent_style_veto(monkeypatch, flag):
    raw = wire(acts=[wrong_no()])
    raw["style"] = {"accept": False, "flags": [flag]}
    result, roles, _, _ = sequence(monkeypatch, raw_c=json.dumps(raw))
    assert result.spoken.source == "fallback" and result.spoken.text != NEUTRAL
    assert roles.count("G") <= 2
