"""Independent recovery contracts through the existing intercepted model adapters."""
from __future__ import annotations

from dataclasses import replace
from io import BytesIO
import json

import pytest

from config import AppConfig
from dialogue_frame import DialogueFrame
import llm_agent
import policy_grounded_voice
import policy_voice
from policy_content import CONSULTATION_PURPOSE, DEBT_EXPLANATION_FALLBACK
import prompts
from tests.test_policy_semantics_independent import envelope, offer_frame


def run_provider_sequence(monkeypatch, frame, latest, replies, *, fallback="Можем общаться здесь.", facts=None):
    """Intercept only HTTP, retaining generation, analysis, parsing and code checks."""
    config = AppConfig(openrouter_api_key="synthetic-unused", model="synthetic-speaker",
                       extractor_model="synthetic-distinct-analysis", request_timeout=40, max_tokens=1000,
                       policy_voice_enabled=True, policy_semantic_enabled=True)
    bodies = []

    def open_request(configuration, request):
        body = json.loads(request.data)
        bodies.append(body)
        item = replies[min(len(bodies) - 1, len(replies) - 1)]
        if isinstance(item, Exception):
            raise item
        content = item if isinstance(item, str) else json.dumps(item)
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    result = policy_grounded_voice.render(
        {"facts": facts or {}, "latest_user_message": latest}, config, frame=frame, fallback=fallback,
    )
    assert len(bodies) <= 4
    assert sum(body["model"] == config.model for body in bodies) <= 2
    assert sum(body["model"] == config.extractor_model for body in bodies) <= 2
    return result, bodies


@pytest.mark.parametrize("initial_paused,first_valid,second_valid,second_no,expected_paused,expected_calls", [
    (False, False, True, False, True, 2),
    (True, False, True, False, True, 2),
    (False, True, False, False, False, 4),
    (True, False, True, True, False, 2),
])
def test_a_valid_second_interpretation_resolves_only_the_same_turn_pause(
    monkeypatch, initial_paused, first_valid, second_valid, second_no, expected_paused, expected_calls,
):
    latest = "Не передавать." if second_no else "Можно общаться здесь?"
    unsafe = "Телефон нужен, чтобы сохранить ваши ответы."
    first = envelope(claims=[{"predicate": "phone_required_to_save_chat", "value": True,
                              "evidence": unsafe, "modality": "asserted", "scope": "chat"}])
    second = envelope(acts=[{"kind": "decline_transfer", "evidence": latest, "subject": "client",
                             "scope": "current_offer", "mode": "asserted"}] if second_no else [])
    result, bodies = run_provider_sequence(
        monkeypatch, replace(offer_frame(), conversation_paused=initial_paused), latest,
        [unsafe, first if first_valid else TimeoutError("synthetic-timeout"), "Здесь можно общаться.",
         second if second_valid else TimeoutError("synthetic-timeout")],
    )
    # A provider outage ends validation immediately; a second draft cannot
    # repair it. A valid C1 may still preserve its own user interpretation.
    assert len(bodies) == expected_calls
    assert result.paused is expected_paused
    assert result.effective_frame.conversation_paused is expected_paused
    assert result.effective_frame.conversation_declined is second_no
    assert ("decline_transfer" in result.applied_user_acts) is second_no
    assert result.effective_frame.offer_status == "awaiting_consent"
    assert result.spoken.source == "fallback" and result.spoken.reason == "semantic_provider_unavailable"
    assert unsafe not in result.spoken.text


def test_a_concrete_harm_error_reaches_the_second_real_speaker_and_is_repaired(monkeypatch):
    first = "Алименты, возмещение вреда и текущие платежи остаются после банкротства."
    final = "Требования о возмещении вреда жизни или здоровью сохраняются."
    frame = DialogueFrame(None, "incomplete_held", question_goal="debt_explanation")
    result, bodies = run_provider_sequence(
        monkeypatch, frame, "Что остаётся после банкротства?", [first, envelope(), final, envelope()],
    )
    assert len(bodies) == 4
    assert result.spoken.text == final and result.spoken.source == "model"
    repair = "\n".join(item["content"] for item in bodies[2]["messages"] if item["role"] == "system")
    assert "overbroad_harm_exception" in repair
    assert "жизни или здоровью" in repair
    assert "нельзя представлять любое возмещение вреда" in repair.casefold()
    assert result.spoken.asked_field is None


def test_a_trusted_debt_faq_recovers_both_concepts_after_two_unusable_drafts(monkeypatch):
    frame = DialogueFrame("client.phone", "contact_declined_held", refused_fields=frozenset({"client.phone"}),
                          question_goal="debt_explanation")
    result, bodies = run_provider_sequence(
        monkeypatch, frame, "Чем реструктуризация отличается от списания?", ["", envelope(), "", envelope()],
        fallback="Передача заявки сейчас не подключена.",
    )
    assert len(bodies) == 3  # C1 already interpreted the input; empty G2 is discarded by code.
    assert result.spoken.critic_calls == 1
    assert result.spoken.text == DEBT_EXPLANATION_FALLBACK
    assert "долг остаётся" in result.spoken.text and "могут освободить от долгов" in result.spoken.text
    assert "Есть исключения" in result.spoken.text
    assert "сейчас не подключена" not in result.spoken.text
    assert result.spoken.source == "fallback" and result.spoken.asked_field is None
    assert result.effective_frame.refused_fields == frame.refused_fields
    assert policy_voice.validate_reply(result.spoken.text, None, frame=frame)


def test_an_inferred_who_question_uses_only_the_public_current_card_in_recovery(monkeypatch):
    latest = "Какой получатель указан в предложении?"
    first = "Сведения увидит только эта компания."
    analysis = envelope(claims=[{"predicate": "exclusive_recipient", "value": True,
                                 "evidence": first, "modality": "asserted", "scope": "current_offer"}])
    analysis["user"]["information_need"] = {"kind": "transfer_purpose", "evidence": latest}
    frame = replace(offer_frame(), known_fields=frozenset({"client.phone"}))
    result, bodies = run_provider_sequence(
        monkeypatch, frame, latest, [first, analysis, first, analysis],
        facts={"client": {"phone": "+70000000111", "name": "Тест Секретович"}},
    )
    assert len(bodies) == 4
    assert result.effective_frame.question_goal_origin == "model_hint"
    assert result.effective_frame.trusted_question_goal is None
    assert frame.offer_recipient_name in result.spoken.text
    assert CONSULTATION_PURPOSE in result.spoken.text
    assert result.spoken.asked_field is None and result.spoken.source == "fallback"
    assert not result.applied_user_acts and not result.effective_frame.conversation_declined
    assert all(value not in json.dumps(bodies, ensure_ascii=False) + result.spoken.text
               for value in ("+70000000111", "Секретович"))
    assert all(value not in result.spoken.text.casefold()
               for value in ("нажмите", "подтвердите", "только эта", "уже переданы", "позвонит"))


@pytest.mark.parametrize("changes", [
    {"conversation_declined": True},
    {"conversation_paused": True},
    {"offer_present": False},
    {"offer_recipient_name": None},
])
def test_automatic_card_recovery_cannot_reopen_choice_or_invent_a_missing_recipient(monkeypatch, changes):
    frame = replace(offer_frame(), **changes)
    result, _ = run_provider_sequence(
        monkeypatch, frame, "Можно общаться здесь?", ["", envelope(), "", envelope()],
    )
    assert "Тестовая компания" not in result.spoken.text
    assert CONSULTATION_PURPOSE not in result.spoken.text
    assert result.spoken.source == "fallback" and result.spoken.asked_field is None
    assert result.effective_frame.conversation_declined is frame.conversation_declined
    assert result.paused is frame.conversation_paused


def test_the_actual_speaker_system_describes_current_context_without_invented_handoff_capabilities(monkeypatch):
    final = "Здесь можно обсудить вопрос."
    _, bodies = run_provider_sequence(monkeypatch, offer_frame(), "Что вы видите по обращению?", [final, envelope()])
    system = bodies[0]["messages"][0]["content"]
    assert "Используй явно переданные сведения о текущем" in system
    assert "Передача обращения" in system
    assert "сама по себе не подтверждает проверку статуса" in system
    assert "Ты не видишь заявок клиента" not in system
    assert "всё это делается после того" not in system
    assert prompts.STYLE_EXAMPLES in system


@pytest.mark.parametrize("incoming_paused", [False, True])
def test_a_current_recipient_faq_after_an_actual_no_uses_a_hint_only_to_select_public_facts(
    monkeypatch, incoming_paused,
):
    no = "Не передавать."
    first, _ = run_provider_sequence(
        monkeypatch, offer_frame(), no,
        ["Можем общаться здесь.", envelope(acts=[{
            "kind": "decline_transfer", "evidence": no, "subject": "client",
            "scope": "current_offer", "mode": "asserted",
        }])],
    )
    assert first.effective_frame.conversation_declined
    frame = replace(first.effective_frame, conversation_paused=incoming_paused)
    latest = "Какой получатель указан в предложении?"
    unsafe = "Сведения увидит только эта компания."
    analysis = envelope(claims=[{"predicate": "exclusive_recipient", "value": True,
                                 "evidence": unsafe, "modality": "asserted", "scope": "current_offer"}])
    analysis["user"]["information_need"] = {"kind": "transfer_purpose", "evidence": latest}
    result, bodies = run_provider_sequence(monkeypatch, frame, latest, [unsafe, analysis, unsafe, analysis])
    assert len(bodies) == 4
    assert result.effective_frame.conversation_declined
    assert result.effective_frame.question_goal_origin == "model_hint"
    assert result.effective_frame.trusted_question_goal is None
    assert result.paused is incoming_paused
    assert not result.applied_user_acts
    assert result.spoken.source == "fallback" and result.spoken.asked_field is None
    assert (frame.offer_recipient_name in result.spoken.text) is not incoming_paused
    assert (CONSULTATION_PURPOSE in result.spoken.text) is not incoming_paused
    assert all(value not in result.spoken.text.casefold()
               for value in ("нажмите", "подтвердите", "согласие сохранено", "уже переданы", "позвонит"))
