"""Closed tasks and whole-turn contact references constrain speech, not consent."""
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import json

import pytest

from config import AppConfig
import core
import debt_policy
import debt_workflow
from dialogue_frame import DialogueFrame, constrain_collection, request_fields
import llm_agent
import policy_grounded_voice
import policy_semantics
import policy_voice
import prompts
from tests.test_policy_faq_recovery_20261111 import collected_state
from tests.test_policy_semantics import wire


INTENT = "Вам важно разобраться именно с долговыми вопросами?"
TOTALITY = "Скажите, эти 400 000 ₽ — только кредиты и займы, или есть ещё долги по ЖКХ, налогам или распискам?"
LATEST = "Да, согласен на передачу специалисту. Телефон я уже оставил выше."
TANGENT = "Телефон у меня есть, но отменить заявку или удалить номер из чата я не могу."
GOOD = "Передача заявки из этого чата сейчас недоступна. Можем обсудить вопросы здесь."


def closed_frame():
    return DialogueFrame(None, "collected_held", known_fields=frozenset({"intent", "debts.total"}))


@pytest.mark.parametrize("draft", [INTENT, "Вы хотите решить вопрос именно с вашими долгами?"])
def test_known_intent_is_closed_even_when_no_intent_question_is_pending(draft):
    current = closed_frame()
    assert "intent" in request_fields(draft, current)
    assert "intent" in constrain_collection(draft, current).removed_fields
    assert not policy_voice.validate_reply(draft, None, frame=current)


def test_original_unresolved_intent_question_remains_askable():
    current = DialogueFrame("intent", "clarifying_intent")
    result = constrain_collection("Вы хотите обсудить списание или реструктуризацию именно ваших долгов?", current)
    assert result.asked_field == "intent" and not result.removed_fields


@pytest.mark.parametrize("draft", [TOTALITY,
    "Эта сумма долга включает только кредиты или ещё другие обязательства?",
])
def test_confirmed_total_is_not_reopened_through_a_composition_recheck(draft):
    current = closed_frame()
    assert request_fields(draft, current) & {"debts.total", "debts.composition"}
    assert constrain_collection(draft, current).removed_fields
    assert not policy_voice.validate_reply(draft, None, frame=current)


@pytest.mark.parametrize("draft", [
    "Хотите, я объясню, какие обязательства сохраняются при банкротстве?",
    "Можем обсудить ваш вопрос здесь.",
    "При реструктуризации меняется график платежей. Есть вопросы по этому?",
])
def test_new_explanatory_discussion_is_not_a_closed_task_recheck(draft):
    assert not request_fields(draft, closed_frame())
    assert policy_voice.validate_reply(draft, None, frame=closed_frame())


def test_unknown_total_is_not_claimed_closed_by_known_intent():
    current = DialogueFrame("debts.total", "collecting_debt_context", known_fields=frozenset({"intent"}))
    assert current.allowed_field == "debts.total"
    assert "debts.total" not in request_fields(TOTALITY, current)


def test_quoted_rechecks_are_not_client_data_requests():
    assert not request_fields("В примере написано: «" + INTENT + "»", closed_frame())
    assert not request_fields("Вы спрашиваете: «" + TOTALITY + "»", closed_frame())


def actual_core_turn(monkeypatch, latest=LATEST, drafts=(TANGENT, GOOD)):
    cfg = AppConfig(openrouter_api_key="synthetic-unused", model="synthetic-speaker",
                    extractor_model="synthetic-analysis", max_tokens=2048, request_timeout=40,
                    policy_voice_enabled=True, policy_semantic_enabled=True)
    state = collected_state()
    state["conversation_semantics"] = {"paused": False}
    policy = debt_policy.parse_policy(state)
    assert policy is not None
    policy = policy.model_copy(update={"amount_observation": debt_policy.DebtObservation(kind="exact", amount=600000)})
    state[debt_policy.POLICY_KEY] = policy.as_state()
    state["_plan_object"] = debt_policy.plan_debt(core.get_matrix(), state["facts"], policy,
                                                fact_statuses=state.get("fact_statuses"))
    before = deepcopy(state)
    calls, frames, requests = [], [], []
    generated = 0
    def transport(configuration, request):
        nonlocal generated
        body = json.loads(request.data)
        requests.append(body)
        if body["model"] == cfg.model:
            calls.append("G")
            content = drafts[min(generated, len(drafts) - 1)]
            generated += 1
        else:
            calls.append("C")
            content = json.dumps(wire())  # Deliberately null outgoing slots.
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
    original = policy_grounded_voice.render
    def capture(payload, config, **kwargs):
        frames.append(kwargs["frame"])
        return original(payload, config, **kwargs)
    monkeypatch.setattr(llm_agent, "_urlopen", transport)
    monkeypatch.setattr(policy_grounded_voice, "render", capture)
    text = core._policy_conversation(state, latest, cfg)
    assert state["facts"] == before["facts"]
    assert state[debt_policy.POLICY_KEY] == before[debt_policy.POLICY_KEY]
    assert not state.get("delivery") and state["conversation_semantics"]["paused"] is False
    return text, calls, frames[0], requests, state


def test_actual_core_whole_known_phone_reference_cannot_turn_into_cancel_topic(monkeypatch):
    text, calls, frame, requests, state = actual_core_turn(monkeypatch)
    assert text == GOOD and calls == ["G", "G", "C"]
    assert getattr(frame, "known_phone_reference", False)
    assert debt_workflow.known_phone_reference(state, LATEST)
    assert "known_phone_reference" not in json.dumps(requests, ensure_ascii=False)


@pytest.mark.parametrize("latest", [
    "Как отменить заявку и удалить номер?",
    "Не удаляйте мой номер. Телефон я уже оставил выше.",
    "Да, но не передавайте данные. Телефон я уже оставил выше.",
    "Он сказал: «Телефон я уже оставил выше». Как удалить номер?",
])
def test_real_cancel_questions_negation_mixed_no_and_quote_do_not_gain_reference_proof(monkeypatch, latest):
    state = collected_state()
    assert not debt_workflow.known_phone_reference(state, latest)
    # The code-owned proof is false; existing safety/No handling remains the
    # authority for a genuinely different latest turn.
    assert not getattr(closed_frame(), "known_phone_reference", False)


def test_private_reference_proof_is_digest_bound_without_new_verified_facts():
    current = closed_frame()
    proven = deepcopy(current)
    object.__setattr__(proven, "known_phone_reference", True)
    assert policy_semantics.TurnAuthority.from_frame(current, LATEST, GOOD).context_digest != (
        policy_semantics.TurnAuthority.from_frame(proven, LATEST, GOOD).context_digest)
    assert prompts.policy_verified_context(current) == prompts.policy_verified_context(proven)


def test_actual_core_answers_real_cancellation_question_without_reference_clamp(monkeypatch):
    text, calls, frame, _, _ = actual_core_turn(monkeypatch, "Как отменить заявку или удалить номер?", (TANGENT,))
    assert text == TANGENT and calls == ["G", "C"]
    assert not getattr(frame, "known_phone_reference", False)


@pytest.mark.parametrize("draft", ["Согласие получено.", "Согласие на передачу получено.",
                                  "Разрешение на передачу данных записано.", "Заявка уже передана."])
def test_known_phone_reference_does_not_authorize_consent_or_delivery_claims(draft):
    current = replace(closed_frame(), known_fields=closed_frame().known_fields | {"client.phone"})
    object.__setattr__(current, "known_phone_reference", True)
    assert not policy_voice.validate_reply(draft, None, frame=current)


@pytest.mark.parametrize("draft", ["Согласие на передачу не получено.",
                                  "Нужно ли согласие на передачу данных?"])
def test_negated_receipt_and_real_consent_question_are_not_positive_receipts(draft):
    assert policy_voice.validate_reply(draft, None, frame=closed_frame())


def test_onloader_goal_and_actual_cancellation_limit_remain_unchanged():
    offered = replace(closed_frame(), mode="onloader", offer_present=True, offer_status="awaiting_consent")
    assert "intent" not in request_fields(INTENT, offered)
    assert policy_voice.validate_reply(TANGENT, None, frame=offered)
