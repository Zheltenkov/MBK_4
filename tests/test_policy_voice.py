"""The model phrases policy dialogue. It cannot change questions or business state."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json

import pytest
from pydantic import SecretStr

import config
import core
import debt_policy
import llm_agent
import policy_voice as voice
from config import AppConfig
from onloader.contracts import OnloaderOffer, OnloaderState
from state import init_state


def settings():
    return AppConfig(openrouter_api_key="synthetic-unused", policy_voice_enabled=True)


def model_reply(monkeypatch, text="", question=None, field=None):
    calls = []

    def generate(payload, cfg, **kwargs):
        calls.append((payload, cfg, kwargs))
        return "\n\n".join(part for part in (text, question) if part)

    monkeypatch.setattr(llm_agent, "generate_reply", generate)
    return calls


def debt_state(**facts):
    state = init_state()
    state["facts"] = deepcopy(facts)
    policy = debt_policy.select_policy({}, "Хочу списать свои долги", enabled=True)
    assert policy is not None
    state[debt_policy.POLICY_KEY] = policy.as_state()
    state = core.initialize_debt_questionnaire(state)
    state["_plan_object"] = debt_policy.plan_debt(core.get_matrix(), state["facts"], policy)
    return state


def onloader_state():
    binding = OnloaderState(status="awaiting_consent", binding_digest="a" * 64,
        operation_ref="private-operation", delivery_handle=SecretStr("private-handle-synthetic"),
        authorization_expires_at=2000000000.0, offer_expires_at=2000000000.0,
        offer=OnloaderOffer(offer_id="offer", version="1", consent_version="1", partner_name="Тестовый партнёр"),
        presentation_id="private-presentation", event_id="private-event")
    return {"onloader": binding.storage_value(), "facts": {}, "chat_history": []}


def test_a_purpose_question_explains_the_known_debt_without_forcing_the_next_field(monkeypatch):
    candidate = "Размер долга помогает понять, какие варианты имеет смысл обсуждать."
    calls = model_reply(monkeypatch, candidate)
    state = debt_state(debts={"total": 400000})
    original_facts = deepcopy(state["facts"])
    speech = core._policy_conversation(state, "Я уже сказала сумму. Зачем вам её знать?", settings())
    assert speech == candidate and len(calls) == 1
    assert "Сейчас человек спрашивает, зачем нужна сумма долга" in calls[0][0]["turn_instruction"]
    assert state["facts"] == original_facts
    assert state["_policy_spoken"].asked_field is None


def test_purpose_repair_keeps_the_answer_and_resumes_the_pending_region_afterwards(monkeypatch):
    answer = "От размера долга зависит, какой путь имеет смысл обсуждать."
    calls = model_reply(monkeypatch, answer + "\n\nВ каком городе и регионе вы живёте?")
    state = debt_state(client={"name": "Ирина", "phone": "+70000000401"}, debts={"total": 400000})
    state["session_id"] = "synthetic-purpose-same-chat"
    original_facts = deepcopy(state["facts"])
    original_script = deepcopy(state["script"])

    speech = core._policy_conversation(state, "Зачем вам знать сумму долга?", settings())
    committed = core.commit_turn(state, "Зачем вам знать сумму долга?", speech)["current_state"]

    assert speech == answer and len(calls) == 2
    assert state["_policy_spoken"].source == "model"
    assert state["_policy_spoken"].asked_field is None
    # An answer-only FAQ preserves the existing outstanding question without
    # counting another request or moving the questionnaire cursor.
    assert committed["script"] == original_script
    assert committed["facts"] == original_facts
    assert committed["session_id"] == "synthetic-purpose-same-chat"
    resumed = core._policy_conversation(committed, "Хорошо, продолжим", settings())
    assert "В каком городе и регионе вы живёте?" in resumed
    assert committed["_policy_spoken"].asked_field == "client.region_class"
    assert committed["facts"] == original_facts


def test_a_declarative_debt_comparison_request_defers_the_pending_city_question(monkeypatch):
    answer = "Реструктуризация меняет условия выплаты, а банкротство является отдельной процедурой."
    model_reply(monkeypatch, answer + "\n\nВ каком городе и регионе вы живёте?")
    state = debt_state(client={"name": "Ирина", "phone": "+70000000401"}, debts={"total": 400000})
    original_script = deepcopy(state["script"])
    speech = core._policy_conversation(
        state, "Пока не поняла, чем реструктуризация отличается от списания.", settings())
    committed = core.commit_turn(state, "Пока не поняла, чем реструктуризация отличается от списания.",
                                 speech)["current_state"]
    assert speech == answer and state["_policy_spoken"].asked_field is None
    assert committed["script"] == original_script
    assert committed["facts"]["debts"]["total"] == 400000
    assert not committed["facts"]["client"].get("region_class")


def test_runtime_default_and_explicit_offline_config(monkeypatch):
    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "sk-or-synthetic-not-a-real-key")
    monkeypatch.delenv("MBK_POLICY_VOICE_ENABLED", raising=False)
    assert config.load_config().policy_voice_enabled is True
    monkeypatch.setenv("MBK_POLICY_VOICE_ENABLED", "0")
    assert config.load_config().policy_voice_enabled is False
    assert AppConfig(openrouter_api_key="synthetic").policy_voice_enabled is False


@pytest.mark.parametrize("field,text", [
    ("debts.total", "Сколько примерно осталось выплатить по кредитам и займам?"),
    ("debts.total", "Вы имеете в виду все кредиты вместе?"),
    ("client.region_class", "В каком городе вы живёте?"),
    ("client.name", "Как к вам обращаться?"),
    ("client.phone", "На какой номер можно с вами связаться?"),
    ("intent", "Вы хотите разобраться со своими долгами?"),
    ("debts.total", "Сумма поможет понять размер задолженности."),
    ("debts.total", "Я не могу обещать, что долги спишут."),
    ("debts.total", 'Вы написали «скажите честно». По одной сумме результат обещать нельзя.'),
    (None, "Нет, не нужно ещё раз вводить номер."),
    ("client.region_class", "Да.\n\nА в каком городе вы живёте?"),
])
def test_natural_wording_and_clarification_are_allowed(field, text):
    assert voice.validate_reply(text, field)


@pytest.mark.parametrize("text", [
    "Какой у вас телефон?", "Напишите ваш номер.", "Мне нужен ваш номер.",
    "Как к вам обращаться?", "Заявка отправлена партнёру.", "Передала ваши данные.",
    "Согласие получено.", "Вы согласились на передачу.", "Долги спишут.",
    "Ваши долги будут списаны.", "Квартиру у вас не заберут.",
    "Могу помочь передать ваши данные.", "Сколько долгов и какой телефон?",
    "Какая сумма долга? Сколько вам лет?", "Какой у вас доход?",
])
def test_unplanned_contact_or_unverified_action_is_rejected(text):
    assert not voice.validate_reply(text, "debts.total")


@pytest.mark.parametrize("message", ["А зачем нужна сумма", "А зачем нужна сумма?", "Зачем вообще спрашивать"])
def test_answer_does_not_require_a_question_mark_or_a_followup(monkeypatch, message):
    text = "Хочу понять, сколько всего осталось выплатить."
    model_reply(monkeypatch, text)
    result = voice.render({"latest_user_message":message}, settings(), expected="debts.total",
                          status="collecting_debt_context", fallback="Запасная реплика.")
    assert result.source == "model" and result.text == text and result.asked_field is None


@pytest.mark.parametrize("message", ["А зачем нужна сумма?", "400000, правильно понял вопрос?"])
def test_question_mark_does_not_delete_natural_followup(monkeypatch, message):
    model_reply(monkeypatch, "Да, именно общая сумма.", "В каком городе вы живёте?", "client.region_class")
    result = voice.render({"latest_user_message":message}, settings(), expected="client.region_class",
                          status="collecting_debt_context", fallback="Запасная реплика.")
    assert result.source == "model" and result.asked_field == "client.region_class"
    assert result.text == "Да, именно общая сумма.\n\nВ каком городе вы живёте?"


def test_unavailable_provider_exposes_no_private_error(monkeypatch):
    calls = []

    def fail(*args, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("private provider data")

    monkeypatch.setattr(llm_agent, "generate_reply", fail)
    result = voice.render({}, settings(), expected=None, status="unavailable", fallback="Попробуйте чуть позже.")
    assert result.source == "fallback" and result.reason == "model_unavailable"
    assert "private" not in result.text
    assert len(calls) == 1 and calls[0]["max_attempts"] == 1


def test_stream_and_commit_preserve_model_words_and_count_actual_question(monkeypatch):
    calls = model_reply(monkeypatch, "Давайте разберёмся по порядку.",
                        "Какая сейчас общая сумма долга?", "debts.total")
    monkeypatch.setattr(core, "_record_transcript", lambda *a, **k: None)
    state = debt_state(client={"name":"Ирина", "phone":"+70000000401"})
    before = deepcopy(state["facts"])
    text = "".join(core.stream_reply(state, "Я запуталась в платежах", settings()))
    committed = core.commit_turn(state, "Я запуталась в платежах", text)["current_state"]
    assert text == "Давайте разберёмся по порядку.\n\nКакая сейчас общая сумма долга?"
    assert committed["chat_history"][-1]["content"] == text
    assert committed["facts"] == before
    assert "_policy_spoken" not in committed
    assert debt_policy.debt_delivery_held(committed)
    assert calls[0][1].enable_pii_masking is True
    assert calls[0][1].request_timeout <= 20
    assert calls[0][2]["max_attempts"] == 1
    assert "+70000000401" not in json.dumps(calls[0][0])


def test_answer_only_keeps_outstanding_question_without_spending_attempt(monkeypatch):
    model_reply(monkeypatch, "Сумма нужна, чтобы понять масштаб задолженности.")
    monkeypatch.setattr(core, "_record_transcript", lambda *a, **k: None)
    state = debt_state()
    script = deepcopy(state["script"])
    text = "".join(core.stream_reply(state, "А зачем вам сумма?", settings()))
    committed = core.commit_turn(state, "А зачем вам сумма?", text)["current_state"]
    assert committed["script"] == script
    assert committed["chat_history"][-1]["content"] == text


def test_onloader_voice_keeps_known_contact_and_does_not_record_text_consent(monkeypatch):
    calls = model_reply(monkeypatch, "Повторно вводить номер не нужно. Решение можно выбрать под сообщением.")
    state = onloader_state()
    before = deepcopy(state["onloader"])
    text = "".join(core.stream_reply(state, "Да, номер опять вводить?", settings()))
    committed = core.commit_turn(state, "Да, номер опять вводить?", text)["current_state"]
    assert committed["chat_history"][-1]["content"] == text
    assert committed["onloader"] == before
    assert committed["onloader"]["decision"] is None
    assert "private-handle" not in json.dumps(calls[0][0])
    assert "private-operation" not in json.dumps(calls[0][0])
    assert "_policy_spoken" not in committed


def test_false_contact_question_never_reaches_stream_or_history(monkeypatch):
    model_reply(monkeypatch, "", "На какой номер можно связаться?", "client.phone")
    state = onloader_state()
    text = "".join(core.stream_reply(state, "Да", settings()))
    committed = core.commit_turn(state, "Да", text)["current_state"]
    assert "На какой номер" not in text
    assert committed["onloader"]["decision"] is None


def test_offline_fixture_never_calls_model(monkeypatch):
    def forbidden(*a, **k):
        pytest.fail("Offline preview called the model")

    monkeypatch.setattr(llm_agent, "generate_reply", forbidden)
    state = debt_state()
    assert "сумма долга" in "".join(core.stream_reply(state, "Помогите", replace(settings(), policy_voice_enabled=False)))


def test_voice_usage_callback_is_observational(monkeypatch):
    monkeypatch.setattr(llm_agent, "_post", lambda *a, **k: {
        "choices":[{"message":{"content":"ok"}}], "usage":{"prompt_tokens":10,"completion_tokens":2}})

    def failed_accounting(*a):
        raise RuntimeError("Accounting unavailable")

    assert llm_agent.generate_reply({}, settings(), usage_collector=failed_accounting, max_attempts=1) == "ok"


def test_policy_voice_uses_original_conversation_style_and_free_text(monkeypatch):
    import prompts
    captured = []
    def post(cfg, body, **kwargs):
        captured.append(body)
        return {"choices":[{"message":{"content":"Да, номер уже есть."}}]}
    monkeypatch.setattr(llm_agent, "_post", post)
    result = voice.render({"latest_user_message":"Телефон снова нужен?"}, settings(), expected=None,
                          status="awaiting_consent", fallback="Запасная реплика.")
    system = captured[0]["messages"][0]["content"]
    assert prompts.CONVERSATION_SYSTEM_PROMPT in system
    assert prompts.STYLE_EXAMPLES in system
    assert "JSON" not in captured[0]["messages"][2]["content"]
    assert result.text == "Да, номер уже есть." and result.source == "model"


def test_completed_intake_keeps_the_answer_and_consultation_question(monkeypatch):
    model_reply(monkeypatch, "Номер уже есть. Хотите, объясню разницу?", "Какая общая сумма долга?", "debts.total")
    result = voice.render({}, settings(), expected=None, status="awaiting_consent", fallback="Запасной ответ.")
    assert result.source == "model"
    assert result.text == "Номер уже есть. Хотите, объясню разницу?"
    assert result.asked_field is None


@pytest.mark.parametrize("status,text", [
    ("collected_held", "Нажмите кнопку согласия в чате."),
    ("collected_held", "Если передумаете, кнопка останется в чате."),
    ("awaiting_consent", "Отказ сохранён."),
    ("queued", "Передавать не буду."),
])
def test_action_promises_need_the_actual_offer_or_recorded_decision(monkeypatch, status, text):
    model_reply(monkeypatch, text)
    result = voice.render({}, settings(), expected=None, status=status, fallback="Запасной ответ.")
    assert result.source == "fallback" and result.reason == "hard_repair_invalid"


def test_policy_speech_drops_an_invented_name_without_revealing_the_real_name(monkeypatch):
    model_reply(monkeypatch, "Пожалуйста, Елена.")
    state = debt_state(client={"name":"Ирина"})
    text = "".join(core.stream_reply(state, "Спасибо", settings()))
    assert "Елена" not in text and "Пожалуйста" in text


def test_verbal_refusal_can_end_the_conversation_without_a_button_or_a_business_decision(monkeypatch):
    model_reply(monkeypatch, "Хорошо, передавать не буду.")
    state = onloader_state()
    text = "".join(core.stream_reply(state, "Нет, не разрешаю", settings()))
    committed = core.commit_turn(state, "Нет, не разрешаю", text)["current_state"]
    assert text == "Хорошо, передавать не буду."
    assert committed["onloader"]["decision"] is None


def test_an_ambiguous_financial_request_requires_a_real_debt_question_before_rebinding(monkeypatch):
    ambiguous = "Расскажите, на какую сумму рассчитываете."
    model_reply(monkeypatch, ambiguous)
    state = debt_state()
    text = "".join(core.stream_reply(state, "Разберёмся", settings()))
    committed = core.commit_turn(state, "Разберёмся", text)["current_state"]
    # The ambiguous draft itself still cannot bind an answer to debt. The bounded
    # repair now falls back to an actual, typed debt question before committing.
    assert voice._requests(ambiguous) == set()
    assert ambiguous not in text
    assert state["_policy_spoken"].source == "fallback"
    assert state["_policy_spoken"].asked_field == "debts.total"
    assert "сумм" in text.casefold() and "долг" in text.casefold()
    assert committed["script"]["asked"] == ["debts.total"]


@pytest.mark.parametrize("text,valid", [
    ("Передаю ваши данные специалисту.", False),
    ("Сейчас отправляю заявку.", False),
    ("Ничего не передаю без вашего разрешения.", True),
    ("Я не отправляю данные без вашего разрешения.", True),
])
def test_present_action_claim_is_grounded_without_blocking_a_direct_denial(text, valid):
    assert voice.validate_reply(text, None) is valid


def test_the_policy_fallback_uses_its_typed_question_instead_of_reparsing_the_copy(monkeypatch):
    def unavailable(*a, **k):
        raise OSError("synthetic unavailable")
    monkeypatch.setattr(llm_agent, "generate_reply", unavailable)
    result = voice.render({}, settings(), expected="client.phone", status="collecting_contacts",
                          fallback="На какой номер можно с вами связаться?")
    assert result.source == "fallback" and result.asked_field == "client.phone"
