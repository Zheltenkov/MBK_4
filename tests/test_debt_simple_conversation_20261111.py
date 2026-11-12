"""Debt repairs protect contracts, not advisory preferences about safe wording."""
from dataclasses import replace
from io import BytesIO
import json

import pytest

from config import AppConfig
from dialogue_frame import DialogueFrame
import llm_agent
import policy_grounded_voice as grounded
from tests.test_policy_semantics import wire


SAFE = "Можем обсудить ваш вопрос здесь."
LIVE_G1 = ("Передачу заявки из этого чата сейчас организовать не могу.\n\n"
           "Но продолжить обсуждение вашей ситуации с долгами мы можем прямо здесь.")


def debt_frame():
    return DialogueFrame(None, "collected_held", known_fields=frozenset({
        "intent", "client.name", "client.phone", "client.region_class", "debts.total"}), transfer_blocked=True)


def run(monkeypatch, *, drafts=(SAFE,), flags=(), selected=None, latest="Продолжим.",
        acts=(), hard_claim=False, factual=None, history=()):
    cfg = AppConfig(openrouter_api_key="synthetic-unused", model="synthetic-speaker",
                    extractor_model="synthetic-analysis", request_timeout=40, max_tokens=2048,
                    policy_voice_enabled=True, policy_semantic_enabled=True, policy_factual_audit_enabled=factual is not None)
    calls, raw_c = [], []
    generated = 0
    current = ""
    def open_request(configuration, request):
        nonlocal generated, current
        body = json.loads(request.data)
        if body["model"] == cfg.model:
            role = "G"
            current = drafts[min(generated, len(drafts) - 1)]
            generated += 1
            content = current
        else:
            incoming = json.loads(body["messages"][-1]["content"])
            role = "F" if incoming.get("wire_version", "").startswith("factual-audit") else "C"
            if role == "C":
                claims = [{"predicate": "phone_required_to_save_chat", "value": True, "evidence": current,
                           "modality": "asserted", "scope": "chat"}] if hard_claim and generated == 1 else []
                output = wire(acts=list(acts), claims=claims)
                output["style"] = {"accept": not flags, "flags": list(flags)}
                raw_c.append(output)
                content = json.dumps(output, ensure_ascii=False)
            else:
                content = json.dumps({"rows": [{"unit_id": unit["id"], "relation": factual,
                    "source_ids": [incoming["source_catalog"][0]["id"]] if factual == "supported" else [],
                    "mismatch": "none" if factual == "supported" else "insufficient_source"}
                    for unit in incoming["draft_units"]]})
        calls.append(role)
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    payload = {"latest_user_message": latest, "short_history": list(history),
               "facts": {"debts": {"total": 600000}, "client": {"name": "Сергей",
                   "phone": "+70000000001", "region_class": "moscow_mo"}}}
    result = grounded.render(payload, cfg, frame=selected or debt_frame(), fallback="Можем обсудить здесь.")
    return result, calls, raw_c


@pytest.mark.parametrize("flag", ["unrequested_receipt", "unneeded_question", "contact_barrier",
                                  "wrong_current_turn", "unsupported_claim"])
def test_verified_debt_reply_does_not_regenerate_for_advisory_flags(monkeypatch, flag):
    result, calls, raw = run(monkeypatch, flags=(flag,))
    assert result.spoken.text == SAFE and result.spoken.source == "model"
    assert calls == ["G", "C"]
    assert not raw[0]["style"]["accept"] and raw[0]["style"]["flags"] == [flag]
    assert not result.spoken.critic_assessments[0].verdict.accept
    assert result.channel_diagnostics[0].strict_safe


def test_repetition_does_not_spend_another_checked_debt_pair(monkeypatch):
    result, calls, _ = run(monkeypatch, history=({"role": "assistant", "content": SAFE},))
    assert result.spoken.text == SAFE and calls == ["G", "C"]


def test_receipt_style_diagnostic_is_preserved_without_another_g(monkeypatch):
    selected = replace(debt_frame(), plain_reply_fields=frozenset({"client.region_class"}),
                       received_fields=frozenset({"client.region_class"}))
    result, calls, _ = run(monkeypatch, drafts=("Регион записала.",), flags=("unrequested_receipt",),
                           selected=selected, latest="Казань.")
    assert result.spoken.text == "Записала." and calls == ["G", "C"]
    assert result.spoken.diagnostic_assessments[0].code_style_issues == ("unrequested_receipt",)


def test_exact_live_contact_barrier_advice_keeps_first_safe_g1(monkeypatch):
    result, calls, _ = run(monkeypatch, drafts=(LIVE_G1,), flags=("contact_barrier",))
    assert result.spoken.text == LIVE_G1 and result.spoken.source == "model"
    assert calls == ["G", "C"] and result.channel_diagnostics[0].strict_safe


@pytest.mark.parametrize("verdict", ["supported", "unknown"])
def test_required_f_still_controls_debt_output(monkeypatch, verdict):
    answer = "Закон предусматривает судебное и внесудебное банкротство."
    result, calls, _ = run(monkeypatch, drafts=(answer,), flags=("unrequested_receipt",), factual=verdict)
    if verdict == "supported":
        assert result.spoken.text == answer and result.spoken.source == "model"
        assert calls == ["G", "C", "F"]
    else:
        assert result.spoken.source == "fallback" and result.spoken.text != answer
        assert calls == ["G", "C", "F", "G", "C", "F"]


def test_hard_claim_failure_still_repairs_instead_of_serving_unsafe_g1(monkeypatch):
    bad = "Для сохранения разговора необходим номер."
    result, calls, _ = run(monkeypatch, drafts=(bad, SAFE), hard_claim=True)
    assert result.spoken.text == SAFE and calls == ["G", "C", "G", "C"]


def test_invalid_debt_user_channel_falls_back_once_without_false_no(monkeypatch):
    latest = "Да, согласен на передачу специалисту."
    acts = ({"kind": "decline_transfer", "evidence": latest, "subject": "client",
             "scope": "current_offer", "mode": "asserted"},)
    result, calls, _ = run(monkeypatch, latest=latest, acts=acts)
    assert calls == ["G", "C"] and result.spoken.source == "fallback"
    assert result.paused and not result.applied_user_acts
    assert not result.effective_frame.conversation_declined and not result.effective_frame.refused_fields


def test_code_owned_no_survives_invalid_model_user_channel(monkeypatch):
    wrong = ({"kind": "decline_transfer", "evidence": "NOT_IN_LATEST", "subject": "client",
              "scope": "current_offer", "mode": "asserted"},)
    result, calls, _ = run(monkeypatch, latest="Не передавайте мои данные.", acts=wrong)
    assert calls == ["G", "C"] and result.spoken.source == "fallback"
    assert result.applied_user_acts == ("decline_transfer",)
    assert result.effective_frame.conversation_declined and not result.paused
    assert not result.effective_frame.refused_fields


def test_onloader_keeps_its_existing_optional_style_repair(monkeypatch):
    offered = DialogueFrame(None, "awaiting_consent", mode="onloader", offer_present=True,
                            offer_status="awaiting_consent", offer_recipient_name="Тестовый партнёр")
    result, calls, _ = run(monkeypatch, flags=("unrequested_receipt",), selected=offered)
    assert result.spoken.text == SAFE and calls == ["G", "C", "G", "C"]
