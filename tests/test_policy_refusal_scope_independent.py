"""Independent operation binding through speaker, analyser, reducer and same-chat commit."""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import json

import pytest

import core
from config import AppConfig
import llm_agent
from onloader import policy as onloader_policy
import policy_semantic_analysis
from policy_semantics import evaluate_semantics
from tests.test_policy_semantic_analysis import configuration, offered_frame
from tests.test_policy_semantics_independent import envelope
from tests.test_policy_voice import debt_state, onloader_state


def render_model_label(monkeypatch, latest, previous, evidence, *, subject="client", mode="asserted",
                       kind="decline_transfer"):
    """A synthetic model may mislabel its input. Application code must bind the act."""
    state = (onloader_state() if kind == "decline_transfer" else
             debt_state(debts={"total": 280000}, client={"region_class": "moscow_mo", "name": "Тест"}))
    state["chat_history"] = [{"role": "assistant", "content": previous}]
    before_binding = deepcopy(state.get("onloader"))
    bodies = []
    config = AppConfig(openrouter_api_key="synthetic-unused", model="synthetic-speaker",
                       extractor_model="synthetic-analysis", request_timeout=40, max_tokens=1000,
                       policy_voice_enabled=True, policy_semantic_enabled=True)
    analysis = envelope(acts=[{"kind": kind, "evidence": evidence, "subject": subject,
                               "scope": "current_offer" if kind == "decline_transfer" else "contact_collection",
                               "mode": mode}])

    def open_request(configuration, request):
        body = json.loads(request.data)
        bodies.append(body)
        content = json.dumps(analysis) if body["model"] == config.extractor_model else "Можем общаться здесь."
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    def no_delivery(*args, **kwargs):
        pytest.fail("Refusal-scope tests cannot submit a lead")

    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    monkeypatch.setattr(core.delivery, "submit", no_delivery)
    text = core._policy_conversation(state, latest, config)
    committed = core.commit_turn(state, latest, text)["current_state"]
    assert committed.get("onloader") == before_binding
    if before_binding is not None:
        assert committed["onloader"]["decision"] is None
    assert not committed.get("delivery")
    assert len(bodies) <= 4
    assert committed["chat_history"][-2:] == [
        {"role": "user", "content": latest}, {"role": "assistant", "content": text},
    ]
    return committed, bodies


@pytest.mark.parametrize("latest,evidence,previous", [
    ("да. Объясните по-человечески, зачем это вообще?", "да.", "Общаться здесь можно и без передачи данных."),
    ("Да, расскажите подробнее.", "Да,", "Хотите пояснение по предложению?"),
    ("Понятно, спасибо.", "Понятно, спасибо.", "Контакт предназначен компании для консультации."),
    ("Разрешаю передачу данных.", "Разрешаю передачу данных.", "Контакт предназначен компании для консультации."),
    ("Да, передавайте сведения.", "Да, передавайте сведения.", "Контакт предназначен компании для консультации."),
    ("Нет, спасибо.", "Нет, спасибо.", "По кредитам или долгам вопросы ещё есть?"),
    ("Нет.", "Нет.", "Если надумаете передать заявку, нажмите кнопку. По долгам вопросы ещё есть?"),
    ("Нет.", "Нет.", "Рассказать о реструктуризации?"),
])
def test_a_model_refusal_label_cannot_turn_an_acknowledgement_or_another_answer_into_transfer_no(
    monkeypatch, latest, evidence, previous,
):
    committed, _ = render_model_label(monkeypatch, latest, previous, evidence)
    assert not onloader_policy.chat_declined(committed)


@pytest.mark.parametrize("latest", [
    "Не передавать.",
    "Ага, тогда без передачи.",
    "Давайте без передачи моих сведений.",
    "Я не хочу передавать данные.",
])
def test_explicit_own_operation_refusals_remain_respected_after_a_general_chat_question(monkeypatch, latest):
    committed, _ = render_model_label(monkeypatch, latest, "По долгам вопросы ещё есть?", latest)
    assert onloader_policy.chat_declined(committed)


def test_a_bare_no_after_the_backend_owned_consent_question_remains_a_valid_conversational_refusal(monkeypatch):
    previous = onloader_policy.reply(onloader_state())
    committed, _ = render_model_label(monkeypatch, "Нет.", previous, "Нет.")
    assert onloader_policy.chat_declined(committed)


@pytest.mark.parametrize("latest,evidence,subject,mode", [
    ("Муж не хочет передавать данные.", "Муж не хочет передавать данные.", "other", "asserted"),
    ("Если откажусь от передачи, что будет?", "Если откажусь от передачи, что будет?", "client", "conditional"),
    ("Можно без передачи?", "Можно без передачи?", "client", "question"),
])
def test_questions_hypotheses_and_another_person_do_not_become_the_clients_own_refusal(
    monkeypatch, latest, evidence, subject, mode,
):
    previous = onloader_policy.reply(onloader_state())
    committed, _ = render_model_label(monkeypatch, latest, previous, evidence, subject=subject, mode=mode)
    assert not onloader_policy.chat_declined(committed)


@pytest.mark.parametrize("latest,previous", [
    ("Да, номер оставлю.", "Оставите номер для оформления анкеты?"),
    ("Сергей.", "Как к вам обращаться?"),
    ("Да, объясните подробнее.", "Хотите пояснение по анкете?"),
    ("Нет, спасибо.", "По долгам вопросы ещё есть?"),
    ("Нет, спасибо.", "Оставите номер для анкеты? По долгам вопросы ещё есть?"),
])
def test_a_model_phone_refusal_label_needs_negative_contact_intent_or_a_bound_phone_question(
    monkeypatch, latest, previous,
):
    committed, _ = render_model_label(monkeypatch, latest, previous, latest, kind="decline_phone")
    assert committed["questionnaire_policy"]["phone_collection"] == "open"


@pytest.mark.parametrize("latest,previous", [
    ("Номер оставлять не хочу.", "По долгам вопросы ещё есть?"),
    ("Давайте без телефона.", "Хотите пояснение по анкете?"),
    ("Нет.", "На какой номер можно с вами связаться?"),
])
def test_explicit_phone_refusals_and_a_bare_no_to_the_actual_phone_question_remain_respected(
    monkeypatch, latest, previous,
):
    committed, _ = render_model_label(monkeypatch, latest, previous, latest, kind="decline_phone")
    assert committed["questionnaire_policy"]["phone_collection"] == "declined"


def test_the_analyser_receives_only_the_last_masked_bounded_assistant_referent(monkeypatch):
    bodies = []

    def open_request(config, request):
        bodies.append(json.loads(request.data))
        return BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(envelope())}}]}).encode())

    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    previous = ("PREFIX_ONLY_SENTINEL" + "а" * 2200
                + " Ирина Синтетическая, +79991234567, PRIVATE_ADDRESS, Казань. LAST_ASSISTANT_END")
    payload = {
        "facts": {"client": {"name": "Ирина Синтетическая", "phone": "+79991234567",
                              "city": "Казань", "registration_address": "PRIVATE_ADDRESS"}},
        "latest_user_message": "Расскажите подробнее.",
        "short_history": [{"role": "assistant", "content": "OLDER_ASSISTANT_SENTINEL"},
                          {"role": "user", "content": "USER_ONLY_SENTINEL"},
                          {"role": "assistant", "content": previous},
                          {"role": "user", "content": "LATEST_HISTORY_USER_SENTINEL"}],
        "onloader": {"delivery_handle": "PRIVATE_HANDLE"},
    }
    before = deepcopy(payload)
    result = policy_semantic_analysis.analyze(payload, "Можем общаться здесь.", offered_frame(), configuration())
    assert result.authority is not None
    assert len(bodies) == 1
    data = json.loads(bodies[0]["messages"][-1]["content"])
    assert set(data) == {"latest_user_message", "draft", "context", "previous_assistant_message"}
    assert len(data["previous_assistant_message"]) <= 2000
    assert data["previous_assistant_message"].endswith("LAST_ASSISTANT_END")
    wire = json.dumps(data, ensure_ascii=False)
    assert all(value not in wire for value in (
        "Ирина", "Синтетическая", "+79991234567", "PRIVATE_ADDRESS", "Казань", "PRIVATE_HANDLE",
        "OLDER_ASSISTANT_SENTINEL", "USER_ONLY_SENTINEL", "LATEST_HISTORY_USER_SENTINEL", "PREFIX_ONLY_SENTINEL",
    ))
    assert "[имя скрыто]" in data["previous_assistant_message"]
    assert "[телефон]" in data["previous_assistant_message"]
    assert payload == before


def test_a_refusal_evidence_from_the_previous_assistant_cannot_become_a_new_user_act(monkeypatch):
    def open_request(config, request):
        analysis = envelope(acts=[{"kind": "decline_transfer", "evidence": "Не передавать.",
                                   "subject": "client", "scope": "current_offer", "mode": "asserted"}])
        return BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(analysis)}}]}).encode())

    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    result = policy_semantic_analysis.analyze(
        {"latest_user_message": "Расскажите подробнее.",
         "short_history": [{"role": "assistant", "content": "Не передавать."}]},
        "Можем общаться здесь.", offered_frame(), configuration(),
    )
    assert result.authority is not None
    decision = evaluate_semantics(result.authority, result.semantics)
    assert not decision.applied_user_acts
    assert not decision.effective_frame.conversation_declined
    assert not decision.user_valid


@pytest.mark.parametrize("latest", [
    "Я не против передачи.",
    "Я не отказываюсь от передачи.",
    "Не возражаю против отправки сведений.",
])
def test_negating_an_objection_or_a_refusal_does_not_become_a_transfer_refusal(monkeypatch, latest):
    previous = onloader_policy.reply(onloader_state())
    committed, _ = render_model_label(monkeypatch, latest, previous, latest)
    assert not onloader_policy.chat_declined(committed)


@pytest.mark.parametrize("latest", [
    "Я не против телефона.",
    "Я не отказываюсь от телефона.",
    "Номер оставить не отказываюсь.",
])
def test_negating_a_phone_objection_does_not_close_collection(monkeypatch, latest):
    committed, _ = render_model_label(
        monkeypatch, latest, "На какой номер можно с вами связаться?", latest, kind="decline_phone",
    )
    assert committed["questionnaire_policy"]["phone_collection"] == "open"


def test_a_phone_refusal_in_another_clause_cannot_override_a_negated_transfer_objection(monkeypatch):
    latest = "Я не против передачи. Но номер не оставлю."
    previous = onloader_policy.reply(onloader_state())
    committed, _ = render_model_label(monkeypatch, latest, previous, latest)
    assert not onloader_policy.chat_declined(committed)


def test_the_same_mixed_clauses_still_preserve_the_actual_phone_refusal(monkeypatch):
    latest = "Я не против передачи. Но номер не оставлю."
    committed, _ = render_model_label(
        monkeypatch, latest, "На какой номер можно с вами связаться?", latest, kind="decline_phone",
    )
    assert committed["questionnaire_policy"]["phone_collection"] == "declined"
