"""Verified degraded answers keep contact choices, current card facts and truthful cursors."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

import core
import llm_agent
import policy_voice
import prompts
from config import AppConfig
from dialogue_frame import DialogueFrame, constrain_collection
from onloader import policy as onloader_policy
from policy_content import BANKRUPTCY_EXPLANATION, CONSULTATION_PURPOSE, DEBT_EXPLANATION_FALLBACK, RESTRUCTURING_EXPLANATION
from tests.test_policy_grounded_voice import install, semantic_raw, strict_settings
from tests.test_policy_voice import debt_state, onloader_state


@pytest.fixture(autouse=True)
def no_external_transport(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Fallback recovery tests must not call an external provider or business service")
    monkeypatch.setattr(llm_agent, "_urlopen", forbidden)
    monkeypatch.setattr(llm_agent, "extract_facts", lambda *args, **kwargs: {"fact_updates": [], "internal_summary": ""})


def current_card(**changes):
    return replace(DialogueFrame(None, "awaiting_consent", mode="onloader", offer_present=True,
                                 offer_status="awaiting_consent", known_fields=frozenset({"client.phone"}),
                                 offer_recipient_name="Текущий получатель"), **changes)


def fallback(frame):
    return policy_voice._fallback_reply(frame, "Можем продолжить разговор здесь.", None,
                                        "semantic_unverified_fallback")


@pytest.mark.parametrize("origin", ["code", "model_hint"])
def test_a_current_validated_card_supplies_recipient_and_purpose_without_a_detected_code_goal(origin):
    frame = current_card(question_goal="offer_information_purpose" if origin == "model_hint" else None,
                         question_goal_origin=origin)
    result = fallback(frame)
    assert frame.offer_recipient_name in result.text
    assert CONSULTATION_PURPOSE in result.text
    assert result.source == "fallback" and result.reason == "semantic_unverified_fallback"
    assert result.asked_field is None and policy_voice.validate_reply(result.text, None, frame=frame)
    assert frame.question_goal_origin == origin


@pytest.mark.parametrize("changes", [
    {"conversation_declined": True}, {"conversation_paused": True},
    {"refused_fields": frozenset({"client.phone"})},
])
def test_a_card_alone_does_not_replace_quiet_refusal_or_uncertain_choice_with_a_transfer_explanation(changes):
    frame = current_card(**changes)
    result = fallback(frame)
    assert frame.offer_recipient_name not in result.text
    assert CONSULTATION_PURPOSE not in result.text
    assert result.source == "fallback" and result.asked_field is None


def test_an_explicit_trusted_purpose_question_can_still_be_answered_after_conversational_refusal():
    frame = current_card(conversation_declined=True, question_goal="offer_information_purpose")
    result = fallback(frame)
    assert frame.offer_recipient_name in result.text and CONSULTATION_PURPOSE in result.text
    assert result.asked_field is None and frame.conversation_declined
    assert not onloader_policy.chat_declined({})


@pytest.mark.parametrize("changes", [
    {"offer_present": False}, {"offer_recipient_name": None}, {"offer_recipient_name": ""},
    {"offer_recipient_name": "   "}, {"mode": "debt"},
])
def test_missing_current_card_authority_does_not_invent_a_recipient_or_contact_purpose(changes):
    result = fallback(current_card(**changes))
    assert CONSULTATION_PURPOSE not in result.text
    assert "Текущий получатель" not in result.text
    assert result.asked_field is None and result.source == "fallback"


@pytest.mark.parametrize("refused", [False, True])
def test_a_code_owned_debt_explanation_fallback_answers_the_comparison_without_reopening_intake(refused):
    frame = DialogueFrame("debts.total", "incomplete_held", confirming=True, question_goal="debt_explanation",
                          refused_fields=frozenset({"client.phone"}) if refused else frozenset())
    result = fallback(frame)
    assert result.text == DEBT_EXPLANATION_FALLBACK
    assert "потребительского кредита" in result.text and "долг остаётся" in result.text
    assert "могут освободить от долгов" in result.text and "Есть исключения" in result.text
    assert "жизни или здоровью" in result.text
    assert result.asked_field is None and result.source == "fallback"
    assert policy_voice.validate_reply(result.text, frame.allowed_field, frame=frame)


def test_a_model_hint_alone_does_not_authorize_a_debt_fact_fallback():
    frame = DialogueFrame("client.name", "collecting_contacts", require_question=True,
                          question_goal="debt_explanation", question_goal_origin="model_hint")
    result = fallback(frame)
    assert BANKRUPTCY_EXPLANATION not in result.text and RESTRUCTURING_EXPLANATION not in result.text
    assert DEBT_EXPLANATION_FALLBACK not in result.text
    assert result.asked_field is None and result.source == "fallback"


@pytest.mark.parametrize("changes", [
    {"conversation_declined": True}, {"refused_fields": frozenset({"client.phone"})},
])
def test_a_post_refusal_purpose_hint_selects_public_card_facts_without_authorizing_collection(changes):
    frame = current_card(question_goal="offer_information_purpose", question_goal_origin="model_hint", **changes)
    result = fallback(frame)
    assert frame.offer_recipient_name in result.text and CONSULTATION_PURPOSE in result.text
    assert frame.trusted_question_goal is None and frame.contact_declined
    assert result.source == "fallback" and result.asked_field is None
    assert not constrain_collection(result.text, frame).removed_fields
    assert policy_voice.validate_reply(result.text, None, frame=frame)


def test_a_purpose_hint_does_not_unpause_an_unresolved_contact_choice():
    frame = current_card(conversation_paused=True, question_goal="offer_information_purpose",
                         question_goal_origin="model_hint")
    result = fallback(frame)
    assert frame.offer_recipient_name not in result.text and CONSULTATION_PURPOSE not in result.text
    assert frame.conversation_paused and frame.trusted_question_goal is None
    assert result.asked_field is None


def test_an_actual_refusal_then_who_question_keeps_the_choice_and_explains_the_current_card(monkeypatch):
    initial = onloader_state()
    user = "Не передавать."
    response = "Можем продолжить разговор здесь."
    refusal = {"kind": "decline_transfer", "evidence": user, "subject": "client", "scope": "current_offer",
               "mode": "asserted"}
    install(monkeypatch, [response], [semantic_raw(user_acts=[refusal])])
    prepared = core.prepare_turn(initial, user, strict_settings(), submit_delivery=False)
    text = "".join(core.stream_reply(prepared, user, strict_settings()))
    declined = core.commit_turn(prepared, user, text)["current_state"]
    assert onloader_policy.chat_declined(declined)
    binding = deepcopy(declined["onloader"])
    history = deepcopy(declined["chat_history"])

    question = "А кому именно предназначены эти сведения?"
    unsafe = "Данные получит только эта компания, других получателей нет."
    claim = {"predicate": "exclusive_recipient", "value": True, "evidence": unsafe,
             "modality": "asserted", "scope": "current_offer"}
    envelope = json.loads(semantic_raw(claims=[claim]))
    envelope["user"]["information_need"] = {"kind": "transfer_purpose", "evidence": question}
    generations, analyses = install(monkeypatch, [unsafe] * 2, [json.dumps(envelope, ensure_ascii=False)] * 2)
    prepared = core.prepare_turn(declined, question, strict_settings(), submit_delivery=False)
    text = "".join(core.stream_reply(prepared, question, strict_settings()))
    committed = core.commit_turn(prepared, question, text)["current_state"]
    assert "Тестовый партнёр" in text and CONSULTATION_PURPOSE in text
    assert onloader_policy.chat_declined(committed) and committed["onloader"] == binding
    assert committed["onloader"]["decision"] is None and not committed.get("delivery")
    assert committed["chat_history"][:-2] == history
    assert prepared["_policy_spoken"].source == "fallback" and prepared["_policy_spoken"].asked_field is None
    assert len(generations) == len(analyses) == 2


def test_actual_same_chat_who_question_recovers_the_current_card_after_two_rejected_drafts_and_a_hint(monkeypatch):
    state = onloader_state()
    state["chat_history"] = [{"role": "assistant", "content": "Здесь можно задавать вопросы."}]
    before_binding, before_facts = deepcopy(state["onloader"]), deepcopy(state["facts"])
    user, unsafe = "А кому именно уйдут сведения?", "Данные получит только эта компания, других получателей нет."
    claim = {"predicate": "exclusive_recipient", "value": True, "evidence": unsafe,
             "modality": "asserted", "scope": "current_offer"}
    envelope = json.loads(semantic_raw(claims=[claim]))
    envelope["user"]["information_need"] = {"kind": "transfer_purpose", "evidence": user}
    raw = json.dumps(envelope, ensure_ascii=False)
    generations, analyses = install(monkeypatch, [unsafe] * 2, [raw] * 2)
    prepared = core.prepare_turn(state, user, strict_settings(), submit_delivery=False)
    text = "".join(core.stream_reply(prepared, user, strict_settings()))
    committed = core.commit_turn(prepared, user, text)["current_state"]
    assert "Тестовый партнёр" in text and CONSULTATION_PURPOSE in text
    assert prepared["_policy_spoken"].source == "fallback" and prepared["_policy_spoken"].asked_field is None
    assert len(generations) == len(analyses) == 2
    assert committed["onloader"] == before_binding and committed["onloader"]["decision"] is None
    assert committed["facts"] == before_facts and not onloader_policy.chat_declined(committed)
    assert committed["chat_history"][-2:] == [{"role": "user", "content": user}, {"role": "assistant", "content": text}]
    assert not committed.get("delivery")


def test_actual_debt_faq_fallback_keeps_phone_refusal_history_and_exact_facts(monkeypatch):
    state = debt_state(debts={"total": 280000}, client={"region_class": "moscow_mo", "name": "Клиент"})
    state["questionnaire_policy"]["phone_collection"] = "declined"
    state["chat_history"] = [{"role": "user", "content": "Номер оставлять не хочу."}]
    before_facts = deepcopy(state["facts"])
    generations, analyses = install(monkeypatch, [""] * 2, [semantic_raw()] * 2)
    user = "Чем реструктуризация отличается от списания?"
    prepared = core.prepare_turn(state, user, strict_settings(), submit_delivery=False)
    text = "".join(core.stream_reply(prepared, user, strict_settings()))
    committed = core.commit_turn(prepared, user, text)["current_state"]
    assert DEBT_EXPLANATION_FALLBACK in text
    assert prepared["_policy_spoken"].source == "fallback" and prepared["_policy_spoken"].asked_field is None
    assert len(generations) == len(analyses) == 2
    assert committed["facts"] == before_facts and committed["questionnaire_policy"]["phone_collection"] == "declined"
    assert committed["chat_history"][0] == state["chat_history"][0]
    assert not committed.get("delivery")


def test_the_existing_public_voice_entry_recovers_verified_debt_facts_on_generation_failure(monkeypatch):
    calls = []
    def fail(*args, **kwargs):
        calls.append("generation")
        raise TimeoutError("synthetic generation failure")
    monkeypatch.setattr(llm_agent, "generate_reply", fail)
    frame = DialogueFrame("client.region_class", "collecting_debt_context", question_goal="debt_explanation")
    result = policy_voice.render({}, AppConfig(openrouter_api_key="synthetic-unused"), frame=frame,
                                 expected=frame.expected, status=frame.status,
                                 fallback="В каком городе вы живёте?", fallback_question="client.region_class")
    assert result.text == prompts.verified_policy_purpose(frame, concise=True)
    assert result.source == "fallback" and result.asked_field is None
    assert calls == ["generation"] and result.critic_calls == 0
