"""Narrow receipt omission and code-owned conversational closing contracts."""
from dataclasses import replace

import pytest

import core
from dialogue_frame import DialogueFrame
import policy_grounded_voice
import policy_voice
from tests.test_policy_grounded_voice import install, semantic_raw, strict_settings
from utils import omit_empty_field_receipt_bubbles


@pytest.mark.parametrize("receipt", [
    "Город и регион я уже вижу, переспрашивать не буду.",
    "Регион учтён, больше уточнять не нужно.",
    "Регион записала. Повторно уточнять не буду.",
    "Город и регион увидела, снова спрашивать не нужно.",
    "Регион я вижу, больше не буду переспрашивать.",
    "Регион у меня уже есть. Ещё раз уточнять не надо.",
])
def test_a_whole_empty_receipt_may_include_only_a_redundant_question_metacomment(receipt):
    useful = "Если беспокоит график выплат, можем обсудить его здесь."
    assert omit_empty_field_receipt_bubbles(
        receipt + "\n\n" + useful,
        received_fields=frozenset({"client.region_class"}), user_message="Казань.",
    ) == useful


@pytest.mark.parametrize("message", [
    "Казань. Вы правильно поняли?", "Казань, мне страшно.", "Казань, пожалуйста подтвердите.",
    "Не хочу оставлять номер.", "Неизвестное место.",
])
def test_receipt_metacomment_does_not_make_nonplain_input_disposable(message):
    text = "Регион я вижу, больше уточнять не нужно.\n\nМожем обсудить вопрос здесь."
    assert omit_empty_field_receipt_bubbles(
        text, received_fields=frozenset({"client.region_class"}), user_message=message,
    ) == text


@pytest.mark.parametrize("text", [
    "Регион я вижу, уточнять больше не нужно?",
    "Регион я вижу, переспрашивать не буду. От региона зависит порядок обращения.",
    "Регион я вижу, переспрашивать не буду. Понимаю, что вам сейчас тяжело.",
    "Регион я вижу, сведения передавать не буду.",
    "Регион я вижу. Больше не нужно платить по кредитам.",
])
def test_meaningful_or_unrelated_content_is_preserved_byte_for_byte(text):
    assert omit_empty_field_receipt_bubbles(
        text, received_fields=frozenset({"client.region_class"}), user_message="Казань.",
    ) == text


@pytest.mark.parametrize("candidate", [
    "Если надумаете передать заявку, нажмите кнопку под сообщением.",
    "По кредитам или долгам вопросы ещё есть?",
    "Пожалуйста. А чем ещё могу помочь?",
])
def test_a_code_owned_closing_cannot_restart_the_offer_or_ask_a_new_question(candidate):
    frame = DialogueFrame(None, "awaiting_consent", mode="onloader", offer_present=True,
                          offer_status="awaiting_consent", offer_recipient_name="Тестовая компания",
                          closing=True)
    assert policy_voice._model_reply(candidate, frame, "synthetic-closing") is None


def test_a_closing_still_allows_a_short_freely_worded_answer():
    frame = DialogueFrame(None, "collected_held", closing=True)
    candidate = "Пожалуйста, обращайтесь."
    result = policy_voice._model_reply(candidate, frame, "synthetic-closing")
    assert result is not None and result.text == candidate
    assert result.asked_field is None


@pytest.mark.parametrize("message", [
    "Спасибо. А что такое реструктуризация?", "Спасибо, но объясните, кому уйдут данные.",
    "Спасибо, мой долг 280000 рублей.", "Мне страшно, можно без передачи?",
])
def test_a_question_or_new_fact_alongside_thanks_does_not_close_the_conversation(message):
    assert not core.client_is_saying_goodbye(message)
    frame = DialogueFrame(None, "awaiting_consent", mode="onloader", offer_present=True,
                          offer_status="awaiting_consent", closing=core.client_is_saying_goodbye(message))
    assert policy_voice._model_reply("Что именно вас беспокоит?", frame, "synthetic-faq") is not None


def test_a_known_contact_does_not_turn_a_closing_into_a_new_contact_step():
    frame = DialogueFrame(None, "collected_held", known_fields=frozenset({"client.phone"}), closing=True)
    assert policy_voice._model_reply("На какой номер можно с вами связаться?", frame, "synthetic-closing") is None
    assert replace(frame, closing=False).known_fields == frame.known_fields


def test_a_degraded_closing_does_not_repeat_the_card_or_start_a_new_question():
    frame = DialogueFrame(None, "awaiting_consent", mode="onloader", offer_present=True,
                          offer_status="awaiting_consent", offer_recipient_name="Тестовая компания",
                          closing=True)
    reply = policy_voice._fallback_reply(frame, "Нажмите кнопку под сообщением.", None, "synthetic-unavailable")
    assert reply.text == "Всего доброго."
    assert reply.asked_field is None
    assert "компани" not in reply.text.casefold()


def test_an_exact_duplicate_gets_one_contextual_revision_even_if_the_style_model_accepts_it(monkeypatch):
    repeated = "Если есть вопросы по вашей ситуации — спрашивайте, я здесь."
    fresh = "С графиком платежей можно разобраться отдельно, если он сейчас беспокоит."
    generations, analyses = install(monkeypatch, [repeated, fresh], [semantic_raw(), semantic_raw()])
    result = policy_grounded_voice.render(
        {"facts": {}, "latest_user_message": "Казань.",
         "short_history": [{"role": "assistant", "content": repeated}]},
        strict_settings(), frame=DialogueFrame(None, "collected_held"), fallback="Запасной ответ.",
    )
    assert result.spoken.text == fresh
    assert len(generations) == len(analyses) == 2
    assert "repeated_reply" in generations[1]["turn_instruction"]
    assert "Казань." == generations[1]["latest_user_message"]


@pytest.mark.parametrize("latest,closing", [
    ("Повторите, пожалуйста.", False), ("Скажите ещё раз.", False),
    ("А можно это повторить?", False), ("Спасибо.", True),
])
def test_a_requested_repeat_question_or_closing_does_not_force_a_duplicate_retry(monkeypatch, latest, closing):
    repeated = "Обращайтесь."
    generations, analyses = install(monkeypatch, [repeated], [semantic_raw()])
    result = policy_grounded_voice.render(
        {"facts": {}, "latest_user_message": latest,
         "short_history": [{"role": "assistant", "content": repeated}]},
        strict_settings(), frame=DialogueFrame(None, "collected_held", closing=closing), fallback="Запасной ответ.",
    )
    assert result.spoken.text == repeated
    assert len(generations) == len(analyses) == 1


def test_optional_duplicate_revision_cannot_discard_the_verified_first_response(monkeypatch):
    repeated = "Если есть вопросы по вашей ситуации — спрашивайте, я здесь."
    generations, analyses = install(monkeypatch, [repeated, "Ваше согласие сохранено."],
                                    [semantic_raw(), semantic_raw()])
    result = policy_grounded_voice.render(
        {"facts": {}, "latest_user_message": "Казань.",
         "short_history": [{"role": "assistant", "content": repeated}]},
        strict_settings(), frame=DialogueFrame(None, "collected_held"), fallback="Запасной ответ.",
    )
    assert result.spoken.text == repeated
    assert result.spoken.reason == "model_semantic_first_preserved"
    assert len(generations) == len(analyses) == 2
