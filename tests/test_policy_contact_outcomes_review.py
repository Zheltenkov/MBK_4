"""Finite V8 provider-outcome and direct-negation controls from recorded replies."""
from __future__ import annotations

from io import BytesIO
import json

import pytest

from config import AppConfig
from dialogue_frame import DialogueFrame
import llm_agent
import policy_voice


pytestmark = pytest.mark.usefixtures("real_policy_voice_critic")


def offer_frame(*, declined: bool = False) -> DialogueFrame:
    return DialogueFrame(
        None, "awaiting_consent", known_fields=frozenset({"client.phone"}), mode="onloader",
        offer_present=True, offer_status="awaiting_consent", conversation_declined=declined,
        question_goal="offer_information_purpose", offer_recipient_name="Тестовая компания",
    )


@pytest.mark.parametrize("candidate", [
    "Данные пойдут в компанию «Тестовая компания» — именно она свяжется с вами для консультации по обращению.",
    "Без этого шага они просто не увидят ваш номер и не смогут связаться.",
])
def test_the_recorded_future_contact_or_global_no_access_outcome_is_unverified(candidate):
    assert not policy_voice.validate_reply(candidate, None, frame=offer_frame())


@pytest.mark.parametrize("candidate,declined", [
    ("Контакт нужен компании, чтобы связаться с вами и проконсультировать по обращению.", False),
    ("Без вашего разрешения эту передачу не выполним. Общаться здесь можно и без передачи.", False),
    ("Хорошо, не передаю. Можем обсудить вопросы здесь.", True),
    ("Не могу гарантировать, что именно она свяжется с вами.", False),
])
def test_purpose_current_operation_refusal_and_explicit_uncertainty_remain_sayable(candidate, declined):
    assert policy_voice.validate_reply(candidate, None, frame=offer_frame(declined=declined))


@pytest.mark.parametrize("quoted_claim", [
    "именно она свяжется с вами для консультации по обращению",
    "Без этого шага они просто не увидят ваш номер и не смогут связаться",
])
def test_quoting_the_recorded_outcome_does_not_assert_it(quoted_claim):
    candidate = f"Фраза «{quoted_claim}» не подтверждена сведениями карточки."
    assert policy_voice.validate_reply(candidate, None, frame=offer_frame())


_QUALIFIED_FORGIVENESS = "Прощение долга кредитором — отдельная история, согласие банка на неё не гарантировано."


def test_the_recorded_immediate_guarantee_negation_is_a_valid_qualification():
    frame = DialogueFrame(None, "collected_held", question_goal="debt_explanation")
    assert policy_voice.validate_reply(_QUALIFIED_FORGIVENESS, None, frame=frame)


@pytest.mark.parametrize("additional_claim", [
    "Ваши долги будут списаны.",
    "Результат гарантирован.",
])
def test_a_negated_guarantee_does_not_authorize_an_independent_outcome_claim(additional_claim):
    frame = DialogueFrame(None, "collected_held", question_goal="debt_explanation")
    assert not policy_voice.validate_reply(_QUALIFIED_FORGIVENESS + " " + additional_claim, None, frame=frame)


def test_the_real_provider_keeps_the_qualified_comparison_without_an_unnecessary_second_speaker(monkeypatch):
    candidate = (
        "Реструктуризация — это изменение условий долга: срок, график, ставка. Долг остаётся.\n\n"
        "При банкротстве возможно освобождение от требований кредиторов, но алименты сохраняются.\n\n"
        + _QUALIFIED_FORGIVENESS
    )
    bodies = []

    def open_request(config, request):
        body = json.loads(request.data)
        bodies.append(body)
        content = (json.dumps({"accept": True, "flags": []})
                   if body["model"] == config.extractor_model else candidate)
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    config = AppConfig(openrouter_api_key="synthetic-unused", extractor_model="synthetic-distinct-critic")
    frame = DialogueFrame(None, "collected_held", question_goal="debt_explanation")
    speech = policy_voice.render(
        {"facts": {"client": {"name": "Ирина Синтетическая", "phone": "+70000000401"}},
         "chat_history": [], "latest_user_message": "Чем реструктуризация отличается от списания?",
         "current_context_readonly": True},
        config, expected=None, status=frame.status, frame=frame, fallback="Можем обсудить вопросы здесь.",
    )
    assert speech.text == candidate
    assert [body["model"] for body in bodies] == [config.model, config.extractor_model]
    assert json.loads(bodies[-1]["messages"][-1]["content"])["draft"] == candidate
    assert all(private not in json.dumps(bodies, ensure_ascii=False)
               for private in ("Ирина", "Синтетическая", "+70000000401"))


def test_a_recorded_recipient_outcome_regenerates_without_its_bad_draft_and_keeps_the_current_recipient(monkeypatch):
    rejected = ("Данные уйдут компании «Тестовая компания» — именно она свяжется с вами "
                "для консультации по обращению.")
    final = ("Сведения предназначены компании «Тестовая компания» для консультации по обращению.\n\n"
             "Общаться здесь можно и без передачи данных.")
    bodies = []
    speakers = 0

    def open_request(config, request):
        nonlocal speakers
        body = json.loads(request.data)
        bodies.append(body)
        if body["model"] == config.extractor_model:
            content = json.dumps({"accept": True, "flags": []})
        else:
            speakers += 1
            content = rejected if speakers == 1 else final
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    config = AppConfig(openrouter_api_key="synthetic-unused", extractor_model="synthetic-distinct-critic")
    payload = {
        "facts": {"client": {"name": "Ирина Синтетическая", "phone": "+70000000401"}},
        "chat_history": [{"role": "user", "content": "Я Ирина Синтетическая, номер +70000000401."}],
        "latest_user_message": "А зачем снова телефон и кому уйдут данные?",
        "current_context_readonly": True,
    }
    speech = policy_voice.render(payload, config, expected=None, status="awaiting_consent",
                                 frame=offer_frame(), fallback="Можем обсудить вопросы здесь.")

    assert speech.text == final
    assert [body["model"] for body in bodies] == [config.model, config.model, config.extractor_model]
    instruction = [item["content"] for item in bodies[1]["messages"] if item["role"] == "system"][-1]
    assert rejected not in instruction
    assert "unsupported_recipient_contact_outcome" in instruction
    assert "Тестовая компания" in instruction
    assert [item for item in bodies[0]["messages"] if item["role"] != "system"] == [
        item for item in bodies[1]["messages"] if item["role"] != "system"
    ]
    assert bodies[0]["messages"][0] == bodies[1]["messages"][0]
    assert json.loads(bodies[-1]["messages"][-1]["content"])["draft"] == final
    assert all(private not in json.dumps(bodies, ensure_ascii=False)
               for private in ("Ирина", "Синтетическая", "+70000000401"))


@pytest.mark.parametrize("mixed", [False, True])
def test_two_unsafe_recipient_candidates_preserve_the_verified_recipient_and_purpose_in_a_fallback(monkeypatch, mixed):
    rejected = ("Данные уйдут компании «Тестовая компания» — именно она свяжется с вами "
                "для консультации по обращению.")
    if mixed:
        rejected = ("Телефон повторно вводить не нужно, он уже есть. " + rejected
                    + "\n\nОбщаться здесь можно и без передачи данных.")
    bodies = []

    def open_request(config, request):
        body = json.loads(request.data)
        bodies.append(body)
        content = json.dumps({"accept": True, "flags": []}) if body["model"] == config.extractor_model else rejected
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    config = AppConfig(openrouter_api_key="synthetic-unused", extractor_model="synthetic-distinct-critic")
    speech = policy_voice.render(
        {"facts": {"client": {"name": "Ирина Синтетическая", "phone": "+70000000401"}},
         "latest_user_message": "Кому уйдут данные и зачем?", "current_context_readonly": True},
        config, expected=None, status="awaiting_consent", frame=offer_frame(),
        fallback="Можем обсудить вопросы здесь.",
    )
    assert speech.source == "fallback"
    assert speech.reason == "hard_repair_invalid"
    assert "Тестовая компания" in speech.text
    assert "консультации" in speech.text
    assert "Общаться здесь можно и без передачи данных" in speech.text
    assert "свяжется" not in speech.text
    assert [body["model"] for body in bodies] == [config.model, config.model]
    assert speech.critic_calls == 0
    assert all(private not in json.dumps(bodies, ensure_ascii=False)
               for private in ("Ирина", "Синтетическая", "+70000000401"))


def test_a_fallback_does_not_promote_a_stale_label_without_a_current_offer():
    frame = DialogueFrame(None, "unavailable", mode="onloader", question_goal="offer_information_purpose",
                          offer_present=False, offer_recipient_name="Старый получатель")
    speech = policy_voice._fallback_reply(frame, "Можем обсудить вопросы здесь.", None, "hard_repair_invalid")
    assert speech.source == "fallback"
    assert "Старый получатель" not in speech.text
    assert speech.asked_field is None
