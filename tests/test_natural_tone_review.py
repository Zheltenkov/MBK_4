"""Independent finite tone controls that keep useful acknowledgement and business state."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json

import pytest

import core
import debt_policy
import policy_voice
import policy_voice_critic
import prompts
from pii_masking import mask_payload_for_llm
from config import AppConfig
from dialogue_frame import DialogueFrame
from routing import Plan, get_matrix
from routing.planner import PlannedField
from state import init_state
from tests.test_policy_voice import debt_state, onloader_state

_ACTUAL_ASSESS = policy_voice_critic.assess
_ACTUAL_GENERATE = core.llm_agent.generate_reply
pytestmark = pytest.mark.usefixtures("real_policy_voice_critic")


@pytest.fixture
def settings():
    return AppConfig(openrouter_api_key="synthetic-unused", extractor_model="synthetic-critic", debt_policy_enabled=True,
                     policy_voice_enabled=True)


@pytest.fixture(autouse=True)
def isolated_boundaries(monkeypatch):
    # Composition tests inject a diagnostic adapter, while transport cases below restore it.
    monkeypatch.setattr(policy_voice_critic, "assess", lambda payload, draft, frame, config, **kwargs:
                        policy_voice_critic.CriticAssessment(
                            policy_voice_critic.SemanticVerdict(accept=True, flags=[]),
                            config.extractor_model, "accepted", True,
                        ))
    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *args, **kwargs: {
        "fact_updates": [], "internal_summary": "",
    })
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *args, **kwargs: deepcopy(facts))
    monkeypatch.setattr(core, "_record_transcript", lambda *args, **kwargs: None)

    def forbidden(*args, **kwargs):
        pytest.fail("Tone controls cannot call an external model or submit a lead")

    monkeypatch.setattr(core.llm_agent, "_urlopen", forbidden)
    monkeypatch.setattr(core.delivery, "submit", forbidden)


def run_turn(monkeypatch, state, message, candidate, settings):
    monkeypatch.setattr(core.llm_agent, "generate_reply", lambda *args, **kwargs: candidate)
    prepared = core.prepare_turn(state, message, settings, submit_delivery=True)
    speech = "".join(core.stream_reply(prepared, message, settings))
    committed = core.commit_turn(prepared, message, speech)["current_state"]
    return speech, committed


def complete_debt_state():
    return debt_state(client={"name": "Ирина", "phone": "+70000000401", "region_class": "rf_other"},
                      debts={"total": 400000})


def test_an_ordinary_fact_can_advance_without_an_injected_receipt(monkeypatch, settings):
    state = debt_state(client={"name": "Ирина", "phone": "+70000000401"})
    question = "В каком городе вы живёте?"
    speech, committed = run_turn(monkeypatch, state, "400 тысяч рублей.", question, settings)
    assert speech == question
    assert committed["script"]["asked"] == ["client.region_class"]
    assert committed["facts"]["debts"]["total"] == 400000
    assert debt_policy.debt_delivery_held(committed)


@pytest.mark.parametrize("candidate", [
    "Понимаю, жить с таким напряжением тяжело. Что сейчас больше всего тревожит?",
    "Спасибо, что объяснили. Можно спокойно обсудить ваши вопросы здесь.",
])
def test_a_meaningful_acknowledgement_is_preserved_without_a_word_ban(monkeypatch, settings, candidate):
    state = complete_debt_state()
    before = deepcopy(state["facts"])
    speech, committed = run_turn(monkeypatch, state, "Долги давят, очень переживаю.", candidate, settings)
    assert speech == candidate
    assert committed["facts"] == before
    assert committed["plan"]["fields"] == []


def test_a_useful_clarification_may_echo_an_uncertain_amount(monkeypatch, settings):
    candidate = "Поняла, речь об остатке долга. Это ровно 400 тысяч или приблизительная сумма?"
    monkeypatch.setattr(core.llm_agent, "generate_reply", lambda *args, **kwargs: candidate)
    frame = DialogueFrame("debts.total", "collecting_debt_context", confirming=True)
    spoken = policy_voice.render({"facts": {}, "chat_history": []}, settings,
                                expected="debts.total", status=frame.status,
                                fallback="Какова точная сумма долга?", frame=frame)
    assert spoken.text == candidate
    assert spoken.asked_field == "debts.total"


def test_a_conversational_acceptance_of_refusal_is_not_deleted_or_made_formal(monkeypatch, settings):
    state = onloader_state()
    candidate = "Приняла, уговаривать не буду. Если остались вопросы — можно обсудить их здесь."
    speech, committed = run_turn(monkeypatch, state, "Нет, не разрешаю передачу.", candidate, settings)
    assert speech == candidate
    assert core.onloader_policy.chat_declined(committed)
    assert committed["onloader"]["decision"] is None
    assert committed["onloader"]["status"] == "awaiting_consent"


def test_a_full_revision_does_not_concatenate_the_original_and_revised_acknowledgements(monkeypatch, settings):
    state = debt_state(client={"phone": "+70000000401"}, debts={"total": 400000})
    candidates = ["Спасибо за уточнение.", "Приняла, спасибо.\n\nКак к вам обращаться?"]
    calls = []

    def generate(*args, **kwargs):
        calls.append(args)
        return candidates[min(len(calls) - 1, 1)]

    monkeypatch.setattr(core.llm_agent, "generate_reply", generate)
    message = "Москва."
    prepared = core.prepare_turn(state, message, settings, submit_delivery=True)
    speech = "".join(core.stream_reply(prepared, message, settings))
    committed = core.commit_turn(prepared, message, speech)["current_state"]
    assert len(calls) == 2
    assert speech == candidates[1]
    assert "Спасибо за уточнение" not in speech
    assert committed["script"]["asked"] == ["client.name"]


def test_a_phone_invitation_can_leave_the_conversation_available_before_any_refusal(monkeypatch, settings):
    state = debt_state(client={"name": "Сергей", "region_class": "moscow_mo"}, debts={"total": 280000})
    candidate = "Оставите номер для связи? Если не хотите, можем продолжить разговор здесь."
    speech, committed = run_turn(monkeypatch, state, "Сергей.", candidate, settings)
    assert speech == candidate
    policy = debt_policy.parse_policy(committed)
    assert policy is not None and policy.phone_collection == "open"
    assert not committed["facts"]["client"].get("phone")
    assert not committed.get("delivery")


@pytest.mark.parametrize("candidate", [
    "Для отправки заявки нужен номер. Сам вопрос можно обсудить здесь и без него.",
    "Для оформления заявки номер обязателен. А вопросы можно обсудить здесь.",
    "Чтобы продолжить оформление заявки, нужен номер. В чате можем общаться и без него.",
])
def test_contact_requirement_for_an_application_does_not_forbid_chat_explanations(monkeypatch, settings, candidate):
    state = debt_state(client={"name": "Сергей", "region_class": "moscow_mo"}, debts={"total": 280000})
    speech, committed = run_turn(monkeypatch, state, "Зачем нужен телефон?", candidate, settings)
    assert speech == candidate
    policy = debt_policy.parse_policy(committed)
    assert policy is not None and policy.phone_collection == "open"
    assert not committed.get("delivery")


@pytest.mark.parametrize("pressure,barrier", [
    ("Чтобы я могла продолжить работу по вашей ситуации, оставьте, пожалуйста, номер телефона для связи.",
     "продолжить работу"),
    ("Спасибо. Чтобы продолжить, мне нужен ваш номер телефона — он понадобится для оформления анкеты.\n\n"
     "Можете оставить его здесь?", "Чтобы продолжить,"),
])
def test_the_recorded_v14_phone_request_cannot_make_general_help_conditional_before_refusal(
    monkeypatch, settings, pressure, barrier,
):
    state = debt_state(client={"name": "Сергей", "region_class": "moscow_mo"}, debts={"total": 280000})
    optional_request = "Оставите номер для связи?"
    calls = []

    def generate(*args, **kwargs):
        calls.append(args)
        return pressure if len(calls) == 1 else optional_request

    monkeypatch.setattr(core.llm_agent, "generate_reply", generate)
    message = "Сергей."
    prepared = core.prepare_turn(state, message, settings, submit_delivery=True)
    speech = "".join(core.stream_reply(prepared, message, settings))
    committed = core.commit_turn(prepared, message, speech)["current_state"]
    assert pressure not in speech
    assert barrier not in speech
    assert 1 <= len(calls) <= 2
    policy = debt_policy.parse_policy(committed)
    assert policy is not None and policy.phone_collection == "open"
    assert not committed["facts"]["client"].get("phone")
    assert not committed.get("delivery")


def test_an_empty_normal_reaction_does_not_inject_another_ritual_confirmation():
    field = PlannedField(path="client.name", spec=get_matrix().fields["client.name"])
    plan = Plan(action="ask", fields=[field])
    bubbles = core._merge_contact_request([], plan, user_message="Нужен кредит")
    assert "Давайте разберёмся" not in " ".join(bubbles)
    assert "Прежде чем продолжим" not in " ".join(bubbles)
    assert any("обращаться" in bubble.lower() for bubble in bubbles)


def test_meaningful_normal_support_is_retained_when_the_contact_request_is_added():
    field = PlannedField(path="client.name", spec=get_matrix().fields["client.name"])
    plan = Plan(action="ask", fields=[field])
    support = "Переживать тут не о чем. Ситуация обычная, разберёмся спокойно."
    bubbles = core._merge_contact_request([support], plan, user_message="Тревожно из-за долгов")
    assert support in " ".join(bubbles)
    assert any("обращаться" in bubble.lower() for bubble in bubbles)


def test_a_negated_phone_condition_does_not_block_the_actual_chat_explanation(monkeypatch, settings):
    state = debt_state(client={"name": "Сергей", "region_class": "moscow_mo"}, debts={"total": 280000})
    candidate = "Чтобы продолжить разговор, телефон не нужен. Можем обсудить вопрос здесь."
    speech, committed = run_turn(monkeypatch, state, "Можно общаться без номера?", candidate, settings)
    assert speech == candidate
    policy = debt_policy.parse_policy(committed)
    assert policy is not None and policy.phone_collection == "open"
    assert not committed.get("delivery")


def test_legacy_contact_reaction_preserves_an_explicit_offer_to_continue_without_contact():
    candidate = "Без номера можем обсудить ваш вопрос здесь."
    assert core._safe_contact_reaction([candidate]) == candidate


def test_the_actual_normal_question_batch_has_no_injected_formal_introduction(monkeypatch, settings):
    state = init_state()
    state["facts"] = {"client": {"name": "Ирина"}}
    body = "Какая сумма вам нужна?\n\nИ сколько вам полных лет?"
    prepared = core.prepare_turn(state, "Ирина.", settings, submit_delivery=False)

    # Exercise the matrix transition through the ordinary commit entry point.
    result = core.commit_turn(prepared, "Ирина.", body)

    assert result["messages"] == prompts.split_bubbles(body)
    assert result["current_state"]["script"]["asked"] == ["request.amount", "client.age"]
    assert len(result["messages"]) <= 3
    assert result["current_state"]["facts"]["client"]["name"] == "Ирина"
    assert not result["current_state"].get("delivery")


def test_removing_the_formal_introduction_preserves_a_meaningful_normal_reaction(monkeypatch, settings):
    state = init_state()
    state["facts"] = {"client": {"name": "Ирина"}}
    body = "Понимаю, что хочется разобраться без лишней суеты.\n\nКакая сумма вам нужна и сколько вам полных лет?"
    prepared = core.prepare_turn(state, "Переживаю из-за кредита.", settings, submit_delivery=False)

    result = core.commit_turn(prepared, "Переживаю из-за кредита.", body)

    assert result["messages"] == prompts.split_bubbles(body)
    assert result["current_state"]["script"]["asked"] == ["request.amount", "client.age"]
    assert not result["current_state"].get("delivery")


def test_a_requested_confirmation_can_still_confirm_a_known_fact(monkeypatch, settings):
    state = complete_debt_state()
    confirmation = "Да, регион учтён. Дополнительные сведения для этой анкеты сейчас не нужны."
    before = deepcopy(state["facts"])

    speech, committed = run_turn(monkeypatch, state, "Мой регион уже учли?", confirmation, settings)

    assert speech == confirmation
    assert committed["facts"] == before
    assert committed["script"]["asked"] == []
    assert debt_policy.debt_delivery_held(committed)


@pytest.mark.parametrize("draft", ["Эти данные у меня уже есть.", "Город и регион увидела, спасибо."])
def test_a_recorded_status_receipt_can_be_revised_without_an_injected_replacement(monkeypatch, settings, draft):
    state = debt_state(client={"name": "Ирина", "phone": "+70000000401"}, debts={"total": 400000})
    final = "Если хотите, можем обсудить, чем реструктуризация отличается от списания."
    responses = iter([draft, final])
    calls = []

    def generate(*args, **kwargs):
        calls.append(args)
        return next(responses)

    monkeypatch.setattr(core.llm_agent, "generate_reply", generate)
    verdicts = iter([
        policy_voice_critic.SemanticVerdict(accept=False, flags=["unrequested_receipt"]),
        policy_voice_critic.SemanticVerdict(accept=True, flags=[]),
    ])
    monkeypatch.setattr(policy_voice_critic, "assess", lambda payload, draft, frame, config, **kwargs:
                        policy_voice_critic.CriticAssessment(next(verdicts), config.extractor_model, "accepted", True))
    prepared = core.prepare_turn(state, "Казань, Татарстан.", settings, submit_delivery=False)
    speech = "".join(core.stream_reply(prepared, "Казань, Татарстан.", settings))
    committed = core.commit_turn(prepared, "Казань, Татарстан.", speech)["current_state"]

    # This proves composition and state preservation, not the live model's tone.
    assert len(calls) == 2
    assert speech == final
    assert committed["facts"]["client"]["region_class"] == "rf_other"
    assert committed["facts"]["client"]["phone"] == "+70000000401"
    assert committed["facts"]["debts"]["total"] == 400000
    assert committed["script"]["asked"] == []
    assert debt_policy.debt_delivery_held(committed)
    assert not committed.get("delivery")


def trace_current_turn(monkeypatch, state, message, settings, *, reply="Если есть вопросы, можно обсудить их здесь."):
    frames = []
    payloads = []
    original_render = policy_voice.render

    def render(payload, configuration, **kwargs):
        frames.append(kwargs["frame"])
        return original_render(payload, configuration, **kwargs)

    def generate(payload, configuration, **kwargs):
        payloads.append(deepcopy(payload))
        return reply

    # Scope the spies so a later turn observes the real renderer once, not a nested spy.
    with monkeypatch.context() as scoped:
        scoped.setattr(policy_voice, "render", render)
        scoped.setattr(core.llm_agent, "generate_reply", generate)
        prepared = core.prepare_turn(state, message, settings, submit_delivery=True)
        speech = "".join(core.stream_reply(prepared, message, settings))
        committed = core.commit_turn(prepared, message, speech)["current_state"]
    return committed, frames[-1], payloads


def test_the_recorded_kazan_answer_anchors_this_turn_to_region_without_exposing_its_value(monkeypatch, settings):
    state = debt_state(client={"name": "Ирина", "phone": "+70000000401"}, debts={"total": 400000})
    state["chat_history"] = [
        {"role": "user", "content": "Я уже сказала сумму. Зачем вам её знать?"},
        {"role": "assistant", "content": "От суммы зависит, какие варианты стоит обсуждать. В каком городе вы живёте?"},
    ]
    _, frame, payloads = trace_current_turn(monkeypatch, state, "Казань, Татарстан.", settings)
    primary = payloads[0]
    assert frame.received_fields == frozenset({"client.region_class"})
    assert frame.question_goal is None
    assert frame.expected is None
    assert primary["turn_instruction"] == prompts.policy_turn_instruction(frame)
    assert primary["turn_instruction"] != prompts.policy_turn_instruction(replace(frame, received_fields=frozenset()))
    wire = json.dumps(payloads, ensure_ascii=False)
    assert all(raw not in wire for raw in ("Казань", "Татарстан", "Ирина", "+70000000401"))


def test_the_next_faq_does_not_inherit_the_previous_regions_received_field(monkeypatch, settings):
    state = debt_state(client={"name": "Ирина", "phone": "+70000000401"}, debts={"total": 400000})
    state, previous, _ = trace_current_turn(monkeypatch, state, "Казань, Татарстан.", settings)
    assert previous.received_fields == frozenset({"client.region_class"})
    _, current, _ = trace_current_turn(monkeypatch, state, "Чем реструктуризация отличается от списания?", settings)
    assert current.received_fields == frozenset()
    assert current.question_goal == "debt_explanation"


def test_an_unresolved_location_does_not_invent_a_received_field(monkeypatch, settings):
    state = debt_state(client={"name": "Ирина", "phone": "+70000000401"}, debts={"total": 400000})
    committed, frame, _ = trace_current_turn(monkeypatch, state, "Не знаю.", settings)
    assert frame.received_fields == frozenset()
    assert not committed["facts"].get("client", {}).get("region_class")


def test_the_recorded_exact_rovno_reply_closes_only_the_actual_debt_confirmation(monkeypatch, settings):
    state = debt_state(client={"name": "Ирина", "phone": "+70000000401"})
    _, state = run_turn(monkeypatch, state, "Около 400 тысяч.",
                        "Какова точная общая сумма долга?", settings)
    assert state["script"]["asked"] == ["debts.total"]
    question = "В каком городе и регионе вы живёте?"
    committed, frame, _ = trace_current_turn(monkeypatch, state, "Ровно 400 000.", settings, reply=question)
    assert committed["facts"].get("debts", {}).get("total") == 400000
    assert frame.received_fields == frozenset({"debts.total"})
    assert frame.expected == "client.region_class"
    assert committed["script"]["asked"] == ["client.region_class"]
    assert committed["chat_history"][-1]["content"] == question

    # The modifier does not supply authorization, erase uncertainty or turn a question into evidence.
    assert debt_policy.detect_debt_observation("Ровно 400 000.", expected=False) is None
    questioned = debt_policy.detect_debt_observation("Ровно 400 000?", expected=True)
    approximate = debt_policy.detect_debt_observation("Примерно 400 000.", expected=True)
    assert questioned is None or questioned.kind != "exact"
    assert approximate is not None and approximate.kind == "unknown"


def test_the_recorded_generic_amount_request_retains_its_actual_pending_debt_topic(monkeypatch, settings):
    state = debt_state(client={"name": "Ирина", "phone": "+70000000401"})
    request = "Понимаю, но мне всё же нужно одно конкретное число для работы.\n\nНазовите, пожалуйста, сумму, от которой будем отталкиваться."
    _, state = run_turn(monkeypatch, state, "Примерно, не точно.", request, settings)
    assert state["script"]["asked"] == ["debts.total"]
    next_question = "В каком городе и регионе вы живёте?"
    committed, frame, _ = trace_current_turn(monkeypatch, state, "280 тысяч.", settings, reply=next_question)
    assert committed["facts"].get("debts", {}).get("total") == 280000
    assert frame.received_fields == frozenset({"debts.total"})
    assert committed["script"]["asked"] == ["client.region_class"]


@pytest.mark.parametrize("message,declined", [
    ("Лучше без телефона, можно так?", True),
    ("Можно без телефона?", False),
])
def test_a_stated_contact_preference_with_a_followup_question_is_distinct_from_a_pure_query(
    monkeypatch, settings, message, declined,
):
    state = debt_state(client={"name": "Сергей", "region_class": "moscow_mo"}, debts={"total": 280000})
    _, committed = run_turn(monkeypatch, state, message, "Можем продолжить разговор здесь.", settings)
    policy = debt_policy.parse_policy(committed)
    assert policy is not None and (policy.phone_collection == "declined") is declined
    assert not committed["facts"]["client"].get("phone")
    assert not committed.get("delivery")


def test_the_recorded_infinitive_call_guarantee_is_rejected_even_when_a_review_repeats_it(monkeypatch, settings):
    state = debt_state(client={"name": "Сергей", "region_class": "moscow_mo"}, debts={"total": 280000})
    unsafe = (
        "Чтобы сохранить эти сведения в анкете, нужен номер телефона. Это просто фиксация данных, "
        "звонить по нему сейчас не будут.\n\nМожете оставить контакт, или продолжим без него — как вам удобнее?"
    )
    speech, committed = run_turn(monkeypatch, state, "Сергей.", unsafe, settings)
    assert "звонить по нему сейчас не будут" not in speech
    assert not committed.get("delivery")


def test_calling_is_not_required_for_chat_is_allowed_without_a_future_call_guarantee(monkeypatch, settings):
    state = debt_state(client={"name": "Сергей", "region_class": "moscow_mo"}, debts={"total": 280000})
    explanation = "Чтобы обсудить вопрос здесь, звонок не требуется. Можем продолжить в чате."
    speech, _ = run_turn(monkeypatch, state, "Нужен ли звонок для разговора здесь?", explanation, settings)
    assert speech == explanation


@pytest.mark.parametrize("kind", ["debt", "onloader"])
@pytest.mark.parametrize("separator", [" ", "\n\n"], ids=["same-line", "separate-paragraph"])
def test_both_actual_provider_bodies_redact_private_facts_even_inside_the_untrusted_draft(
    monkeypatch, settings, kind, separator,
):
    state = complete_debt_state() if kind == "debt" else onloader_state()
    phone = "+79991234567"
    name = "Ирина Синтетическая Проверочная"
    address = "Секретная улица 88 квартира 99"
    state["facts"].setdefault("client", {}).update(phone=phone, name=name, registration_address=address)
    state["chat_history"] = [{"role": "user", "content": f"Меня зовут {name}. Телефон {phone}. Адрес {address}."}]
    draft = f"{name}, ваш номер {phone}, адрес {address}.{separator}Можно обсудить вопросы здесь."
    final = "Можно обсудить ваши вопросы здесь."
    bodies = []
    critic_calls = 0

    def post(configuration, body, **kwargs):
        nonlocal critic_calls
        assert configuration.enable_pii_masking is True
        assert kwargs["max_attempts"] == 1
        bodies.append(deepcopy(body))
        if configuration.model == configuration.extractor_model:
            critic_calls += 1
            reply = json.dumps({"accept": critic_calls == 2,
                                "flags": [] if critic_calls == 2 else ["unrequested_receipt"]})
        else:
            reply = draft if len(bodies) == 1 else final
        return {"choices": [{"message": {"content": reply}}]}

    # Observe the final provider request body, after projection and every redaction step.
    monkeypatch.setattr(core.llm_agent, "_post", post)
    monkeypatch.setattr(policy_voice_critic, "assess", _ACTUAL_ASSESS)
    speech = "".join(core.stream_reply(state, "Можем продолжить?", settings))
    assert len(bodies) == 4 and critic_calls == 2
    assert speech == final
    wire = json.dumps(bodies, ensure_ascii=False)
    for private in (phone, "Ирина", name, address, "Секретная", "private-handle-synthetic", "private-operation"):
        assert private not in wire
    assert "Можно обсудить вопросы здесь" in json.dumps(bodies[1], ensure_ascii=False)
    assert bodies[0]["messages"][0] == bodies[2]["messages"][0]
    assert bodies[0]["messages"][-1] == bodies[2]["messages"][-1]
    assert [body["model"] for body in bodies] == [settings.model, settings.extractor_model] * 2
    assert json.loads(bodies[3]["messages"][-1]["content"])["draft"] == speech
    for index in (1, 3):
        diagnostic = json.loads(bodies[index]["messages"][-1]["content"])
        assert diagnostic["assessment_scope"] == "current_draft_only"
        assert isinstance(diagnostic["task"], dict)
        assert prompts.CONVERSATION_VOICE not in json.dumps(diagnostic["task"], ensure_ascii=False)
    if kind == "onloader":
        assert state["onloader"]["decision"] is None


def test_both_provider_requests_preserve_the_latest_answers_type_at_its_actual_role(monkeypatch, settings):
    state = debt_state(client={"name": "Ирина Синтетическая", "phone": "+70000000401"},
                       debts={"total": 400000})
    state["facts"]["client"].update(city="Казань", region="Татарстан")
    state["chat_history"] = [
        {"role": "user", "content": "Меня зовут Ирина Синтетическая."},
        {"role": "assistant", "content": "В каком городе и регионе вы живёте?"},
    ]
    bodies = []

    def post(configuration, body, **kwargs):
        assert kwargs["max_attempts"] == 1
        bodies.append(deepcopy(body))
        reply = (json.dumps({"accept": True, "flags": []})
                 if configuration.model == configuration.extractor_model else "Можем обсудить ваши вопросы здесь.")
        return {"choices": [{"message": {"content": reply}}]}

    monkeypatch.setattr(core.llm_agent, "_post", post)
    monkeypatch.setattr(policy_voice_critic, "assess", _ACTUAL_ASSESS)
    prepared = core.prepare_turn(state, "Казань, Татарстан.", settings, submit_delivery=False)
    speech = "".join(core.stream_reply(prepared, "Казань, Татарстан.", settings))
    committed = core.commit_turn(prepared, "Казань, Татарстан.", speech)["current_state"]

    assert len(bodies) == 2
    diagnostic = json.loads(bodies[1]["messages"][-1]["content"])
    assert "[город или регион скрыт]" in bodies[0]["messages"][-1]["content"]
    assert diagnostic["latest_user_message"] == bodies[0]["messages"][-1]["content"]
    assert any("[имя скрыто]" in row["content"] for row in diagnostic["history"] if row["role"] == "user")
    for body in bodies:
        wire = json.dumps(body, ensure_ascii=False)
        assert all(raw not in wire for raw in ("Ирина", "Синтетическая", "Казань", "Татарстан", "+70000000401"))
    assert [body["model"] for body in bodies] == [settings.model, settings.extractor_model]
    assert diagnostic["draft"] == speech
    assert committed["facts"]["client"]["region_class"] == "rf_other"
    assert not committed.get("delivery")


def test_private_aliases_are_idempotent_and_preserve_types_without_values():
    payload = {
        "current_context_readonly": True,
        "facts": {"client": {"name": "Ирина Синтетическая", "city": "Казань", "birthdate": "10.05.1985",
                              "registration_address": "Секретная улица 88 квартира 99"}},
        "latest_user_message": "Ирина Синтетическая, Казань, 10.05.1985, Секретная улица 88 квартира 99.",
    }
    masked = mask_payload_for_llm(payload, True)
    text = masked["latest_user_message"]
    assert all(alias in text for alias in (
        "[имя скрыто]", "[город или регион скрыт]", "[дата рождения скрыта]", "[точный адрес скрыт]",
    ))
    assert mask_payload_for_llm(masked, True) == masked
    assert all(raw not in json.dumps(masked, ensure_ascii=False)
               for raw in ("Ирина", "Синтетическая", "Казань", "10.05.1985", "Секретная"))


def test_a_casefold_value_collision_hides_the_value_without_inventing_its_type():
    payload = {"current_context_readonly": True,
               "facts": {"client": {"name": "Синтетическое", "city": "синтетическое"}},
               "latest_user_message": "СИНТЕТИЧЕСКОЕ."}
    masked = mask_payload_for_llm(payload, True)
    assert masked["latest_user_message"] == "[сведения анкеты]."
    assert "[имя скрыто]" not in masked["latest_user_message"]
    assert "[город или регион скрыт]" not in masked["latest_user_message"]


def test_an_unknown_word_is_not_assigned_a_private_type_and_legacy_keeps_the_given_name():
    ordinary = {"facts": {"client": {"name": "Ирина Синтетическая"}},
                "latest_user_message": "Меня зовут Ирина Синтетическая."}
    legacy = mask_payload_for_llm(ordinary, True)
    assert "Ирина" in legacy["latest_user_message"]
    assert "[имя скрыто]" not in legacy["latest_user_message"]
    private = mask_payload_for_llm({"facts": {}, "current_context_readonly": True,
                                    "latest_user_message": "Синтетическое."}, True)
    assert private["latest_user_message"] == "Синтетическое."


def test_the_recorded_phone_refusal_with_an_alternatives_question_preserves_both_meanings(monkeypatch, settings):
    state = debt_state(client={"name": "Сергей", "region_class": "moscow_mo"}, debts={"total": 280000})
    request = ("Чтобы подготовить анкету, мне нужен ваш номер телефона.\n\n"
               "Продолжить разговор и задать вопросы можно и без него — контакт нужен только для оформления.")
    _, state = run_turn(monkeypatch, state, "Сергей.", request, settings)
    assert state["script"]["asked"] == ["client.phone"]

    _, committed = run_turn(monkeypatch, state, "Не хочу оставлять. Можно без банкротства?",
                            "Можно обсудить варианты здесь без номера.", settings)

    policy = debt_policy.parse_policy(committed)
    assert policy is not None and policy.mode == "debt"
    assert policy.phone_collection == "declined"
    assert committed["facts"]["debts"]["total"] == 280000
    assert committed["facts"]["client"]["region_class"] == "moscow_mo"
    assert committed["script"]["asked"] == []
    assert debt_policy.debt_delivery_held(committed)
    assert not committed.get("delivery")


def test_the_recorded_no_ne_hochu_after_an_actual_contact_request_stops_collection(monkeypatch, settings):
    state = debt_state(client={"name": "Сергей", "region_class": "moscow_mo"}, debts={"total": 280000})
    _, state = run_turn(monkeypatch, state, "Сергей.", "Оставите номер для оформления анкеты?", settings)
    assert state["script"]["asked"] == ["client.phone"]

    _, committed = run_turn(monkeypatch, state, "Нет, не хочу.", "Можем продолжить разговор без номера.", settings)

    policy = debt_policy.parse_policy(committed)
    assert policy is not None and policy.mode == "debt"
    assert policy.phone_collection == "declined"
    assert committed["script"]["asked"] == []
    assert committed["facts"]["debts"]["total"] == 280000
    assert not committed["facts"]["client"].get("phone")
    assert debt_policy.debt_delivery_held(committed)
    assert not committed.get("delivery")


def test_the_actual_critic_approves_the_cleaned_and_committed_body_not_the_pre_cleanup_draft(monkeypatch, settings):
    state = complete_debt_state()
    bodies = []

    def post(configuration, body, **kwargs):
        bodies.append(deepcopy(body))
        reply = (json.dumps({"accept": True, "flags": []})
                 if configuration.model == configuration.extractor_model else "Елена, можем обсудить вопросы здесь.")
        return {"choices": [{"message": {"content": reply}}]}

    monkeypatch.setattr(core.llm_agent, "_post", post)
    monkeypatch.setattr(policy_voice_critic, "assess", _ACTUAL_ASSESS)
    speech = "".join(core.stream_reply(state, "Что можно обсудить?", settings))
    committed = core.commit_turn(state, "Что можно обсудить?", speech)

    assert len(bodies) == 2
    assert "Елена" not in speech
    assert json.loads(bodies[1]["messages"][-1]["content"])["draft"] == speech
    assert committed["messages"] == [speech]
    assert committed["current_state"]["chat_history"][-1]["content"] == speech


def test_a_second_critic_outage_keeps_only_the_new_valid_body_as_explicitly_degraded(monkeypatch, settings):
    state = complete_debt_state()
    first = "Можно спокойно обсудить варианты здесь."
    second = "Можем обсудить вопросы здесь без передачи данных."
    bodies = []
    critic_calls = 0

    def post(configuration, body, **kwargs):
        nonlocal critic_calls
        assert kwargs["max_attempts"] == 1
        bodies.append(deepcopy(body))
        if configuration.model == configuration.extractor_model:
            critic_calls += 1
            if critic_calls == 2:
                raise TimeoutError("synthetic diagnostic outage")
            reply = json.dumps({"accept": False, "flags": ["wrong_current_turn"]})
        else:
            reply = first if len(bodies) == 1 else second
        return {"choices": [{"message": {"content": reply}}]}

    monkeypatch.setattr(core.llm_agent, "_post", post)
    monkeypatch.setattr(policy_voice_critic, "assess", _ACTUAL_ASSESS)
    speech = "".join(core.stream_reply(state, "Что можно обсудить?", settings))
    spoken = state["_policy_spoken"]

    assert len(bodies) == 4 and critic_calls == 2
    assert speech == second and first not in speech
    assert spoken.reason == "model_advisory_unavailable"
    assert spoken.critic_assessments[0].verdict.accept is False
    assert spoken.critic_assessments[1].verdict is None
    assert spoken.critic_calls == 2
    assert not state.get("delivery")


def test_a_string_accept_is_unavailable_diagnostics_and_never_a_semantic_approval(monkeypatch, settings):
    state = complete_debt_state()
    body_text = "Можно продолжить разговор здесь."
    bodies = []

    def post(configuration, body, **kwargs):
        bodies.append(deepcopy(body))
        reply = ('{"accept":"true","flags":[]}'
                 if configuration.model == configuration.extractor_model else body_text)
        return {"choices": [{"message": {"content": reply}}]}

    monkeypatch.setattr(core.llm_agent, "_post", post)
    monkeypatch.setattr(policy_voice_critic, "assess", _ACTUAL_ASSESS)
    speech = "".join(core.stream_reply(state, "Что можно обсудить?", settings))
    spoken = state["_policy_spoken"]

    assert len(bodies) == 2 and speech == body_text
    assert spoken.reason == "model_advisory_unavailable"
    assert spoken.critic_assessments[0].reason == "invalid_schema"
    assert spoken.critic_assessments[0].verdict is None


def test_a_diagnostic_response_after_the_turn_budget_cannot_be_a_completed_approval(monkeypatch, settings):
    state = complete_debt_state()
    clock = [0.0]
    bodies = []

    def post(configuration, body, **kwargs):
        assert kwargs["max_attempts"] == 1
        bodies.append(deepcopy(body))
        if configuration.model == configuration.extractor_model:
            # A socket timeout is not a hard wall-clock deadline for a slow response.
            clock[0] = 41.0
            reply = json.dumps({"accept": True, "flags": []})
        else:
            reply = "Можно продолжить разговор здесь."
        return {"choices": [{"message": {"content": reply}}]}

    monkeypatch.setattr(policy_voice.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(core.llm_agent, "_post", post)
    monkeypatch.setattr(policy_voice_critic, "assess", _ACTUAL_ASSESS)
    list(core.stream_reply(state, "Что можно обсудить?", replace(settings, request_timeout=40)))
    spoken = state["_policy_spoken"]

    assert len(bodies) == 2 and spoken.critic_calls == 1
    assert spoken.reason == "model_advisory_unavailable"
    assert spoken.critic_assessments[-1].reason == "deadline_exhausted"
    assert spoken.critic_assessments[-1].verdict is None
    assert not state.get("delivery")


@pytest.mark.parametrize("flag,rejected", [
    ("wrong_current_turn", "Прошу прощения, что переспросила про сумму — она у меня уже есть."),
    ("unrequested_receipt", "Город и регион увидела, спасибо."),
    ("contact_barrier", "Без номера продолжить работу по вашему вопросу не получится."),
    ("unneeded_question", "В каком городе и регионе вы живёте?"),
    ("unsupported_claim", "Данные увидят только в Тестовой компании, больше никому они не уйдут."),
])
def test_semantic_recovery_does_not_reattach_the_rejected_candidate_to_the_speakers_next_input(
    monkeypatch, settings, flag, rejected,
):
    state = complete_debt_state()
    final = "Можем обсудить, какие способы решения долгов в принципе бывают."
    bodies = []
    critic_calls = 0

    def post(configuration, body, **kwargs):
        nonlocal critic_calls
        assert kwargs["max_attempts"] == 1
        bodies.append(deepcopy(body))
        if configuration.model == configuration.extractor_model:
            critic_calls += 1
            candidate = json.loads(body["messages"][-1]["content"])["draft"]
            reply = json.dumps({"accept": candidate == final, "flags": [] if candidate == final else [flag]})
        else:
            reply = rejected if len(bodies) == 1 else final
        return {"choices": [{"message": {"content": reply}}]}

    monkeypatch.setattr(core.llm_agent, "_post", post)
    monkeypatch.setattr(policy_voice_critic, "assess", _ACTUAL_ASSESS)
    speech = "".join(core.stream_reply(state, "Какие есть варианты?", settings))

    hard_repair_first = flag in {"contact_barrier", "unneeded_question"}
    assert len(bodies) == (3 if hard_repair_first else 4)
    assert critic_calls == (1 if hard_repair_first else 2)
    assert speech == final
    if not hard_repair_first:
        assert rejected not in json.dumps(bodies[2], ensure_ascii=False)
        assert bodies[0]["messages"][0] == bodies[2]["messages"][0]
        assert bodies[0]["messages"][-1] == bodies[2]["messages"][-1]
        assert state["_policy_spoken"].critic_assessments[0].verdict.flags == [flag]
    else:
        # Known hard failures consume their repair before the first diagnostic call.
        assert [body["model"] for body in bodies] == [settings.model, settings.model, settings.extractor_model]
        assert state["_policy_spoken"].critic_assessments[0].verdict.flags == []
    assert json.loads(bodies[-1]["messages"][-1]["content"])["draft"] == speech
    assert state["_policy_spoken"].reason == "model_advisory_revised"
    assert not state.get("delivery")


def test_the_actual_why_answer_keeps_its_explanation_when_the_pending_city_question_is_deferred(monkeypatch, settings):
    state = debt_state(client={"name": "Ирина", "phone": "+70000000401"}, debts={"total": 400000})
    _, state = run_turn(monkeypatch, state, "400 тысяч.", "В каком городе и регионе вы живёте?", settings)
    assert state["script"]["asked"] == ["client.region_class"]
    answer = "От размера долга зависит, какой путь решения имеет смысл обсуждать."
    raw = answer + "\n\nПодскажите, в каком городе и регионе вы живёте?"
    bodies = []
    critic_calls = 0

    def post(configuration, body, **kwargs):
        nonlocal critic_calls
        bodies.append(deepcopy(body))
        if configuration.model == configuration.extractor_model:
            critic_calls += 1
            reply = json.dumps({"accept": critic_calls == 2,
                                "flags": [] if critic_calls == 2 else ["unneeded_question"]})
        else:
            reply = raw
        return {"choices": [{"message": {"content": reply}}]}

    monkeypatch.setattr(core.llm_agent, "generate_reply", _ACTUAL_GENERATE)
    monkeypatch.setattr(core.llm_agent, "_post", post)
    monkeypatch.setattr(policy_voice_critic, "assess", _ACTUAL_ASSESS)
    message = "Я уже сказала сумму. Зачем вам её знать?"
    prepared = core.prepare_turn(state, message, settings, submit_delivery=False)
    speech = "".join(core.stream_reply(prepared, message, settings))
    committed = core.commit_turn(prepared, message, speech)["current_state"]

    assert len(bodies) == 3 and critic_calls == 1
    assert speech == answer
    diagnostic = json.loads(bodies[-1]["messages"][-1]["content"])
    assert diagnostic["draft"] == speech
    assert diagnostic["frame"]["expected"] is None
    assert diagnostic["frame"]["pending_field"] == "client.region_class"
    assert prepared["_policy_spoken"].reason == "model_advisory_disputed"
    assert committed["script"]["asked"] == ["client.region_class"]
    assert committed["facts"]["debts"]["total"] == 400000
    assert not committed["facts"]["client"].get("region_class")
    assert not committed.get("delivery")


def test_the_recorded_private_name_vocative_never_reaches_the_client_or_external_requests(monkeypatch, settings):
    name = "Ирина Синтетическая Проверочная"
    state = debt_state(client={"name": name, "region_class": "moscow_mo"}, debts={"total": 280000})
    body_text = ("[имя скрыто], чтобы оформить анкету, нужен ваш номер телефона. "
                 "Продолжить разговор можно и без него.\n\nОставите контакт сейчас?")
    bodies = []

    def post(configuration, body, **kwargs):
        assert kwargs["max_attempts"] == 1
        bodies.append(deepcopy(body))
        reply = (json.dumps({"accept": True, "flags": []})
                 if configuration.model == configuration.extractor_model else body_text)
        return {"choices": [{"message": {"content": reply}}]}

    monkeypatch.setattr(core.llm_agent, "_post", post)
    monkeypatch.setattr(policy_voice_critic, "assess", _ACTUAL_ASSESS)
    prepared = core.prepare_turn(state, "Ирина.", settings, submit_delivery=False)
    speech = "".join(core.stream_reply(prepared, "Ирина.", settings))
    committed = core.commit_turn(prepared, "Ирина.", speech)["current_state"]

    assert len(bodies) == 2
    assert "[имя скрыто]" not in speech and "[" not in speech
    assert speech.startswith("Чтобы оформить анкету")
    assert "Оставите контакт сейчас?" in speech
    assert json.loads(bodies[-1]["messages"][-1]["content"])["draft"] == speech
    assert committed["script"]["asked"] == ["client.phone"]
    assert not committed["facts"]["client"].get("phone")
    assert all(raw not in json.dumps(bodies, ensure_ascii=False) for raw in (name, "Ирина"))
    assert not committed.get("delivery")


def test_private_control_tokens_inside_an_answer_remain_a_hard_boundary():
    text = "Ваш [точный адрес скрыт] уже записан."
    assert not policy_voice.validate_reply(text, None, frame=DialogueFrame(None, "collected_held"))


@pytest.mark.parametrize("kind,flag", [
    ("faq", "unneeded_question"), ("phone", "unneeded_question"), ("refusal", "unrequested_receipt"),
])
def test_a_false_semantic_objection_cannot_replace_a_valid_current_answer_with_a_template(
    monkeypatch, settings, kind, flag,
):
    if kind == "refusal":
        state = onloader_state()
        message = "Нет, не разрешаю передачу."
        candidate = "Хорошо, не передаю. Если есть вопросы по долгам — можем обсудить их здесь."
    elif kind == "phone":
        state = debt_state(client={"name": "Сергей", "region_class": "moscow_mo"}, debts={"total": 280000})
        message = "Сергей."
        candidate = "Оставите номер для оформления анкеты? Обсудить вопросы можно и без него."
    else:
        state = debt_state(client={"name": "Ирина", "phone": "+70000000401"}, debts={"total": 400000})
        message = "Я уже сказала сумму. Зачем вам её знать?"
        candidate = "От суммы долга зависит, какой путь решения имеет смысл обсуждать."
    calls = []

    def generate(*args, **kwargs):
        calls.append(args)
        return candidate

    def false_objection(payload, draft, frame, configuration, **kwargs):
        return policy_voice_critic.CriticAssessment(
            policy_voice_critic.SemanticVerdict(accept=False, flags=[flag]),
            configuration.extractor_model, "semantic_rejection", True,
        )

    monkeypatch.setattr(core.llm_agent, "generate_reply", generate)
    monkeypatch.setattr(policy_voice_critic, "assess", false_objection)
    previous_script = deepcopy(state.get("script", {}))
    prepared = core.prepare_turn(state, message, settings, submit_delivery=False)
    speech = "".join(core.stream_reply(prepared, message, settings))
    spoken = prepared["_policy_spoken"]
    committed = core.commit_turn(prepared, message, speech)["current_state"]

    assert len(calls) == 2
    assert speech == candidate
    assert spoken.source == "model" and spoken.reason == "model_advisory_disputed"
    assert all(assessment.verdict.accept is False for assessment in spoken.critic_assessments)
    assert not committed.get("delivery")
    if kind == "refusal":
        assert core.onloader_policy.chat_declined(committed)
        assert committed["onloader"]["decision"] is None
    elif kind == "phone":
        assert committed["script"]["asked"] == ["client.phone"]
        assert not committed["facts"]["client"].get("phone")
    else:
        assert committed["script"] == previous_script
        assert committed["facts"]["debts"]["total"] == 400000


@pytest.mark.parametrize("receipt", ["Спасибо, регион я вижу.", "Спасибо, это я уже вижу."])
def test_an_empty_received_field_receipt_is_removed_without_replacing_the_useful_continuation(
    monkeypatch, settings, receipt,
):
    state = debt_state(client={"name": "Ирина", "phone": "+70000000401"}, debts={"total": 400000})
    continuation = "Если хотите, можем обсудить вашу ситуацию подробнее."
    speech, committed = run_turn(monkeypatch, state, "Казань, Татарстан.", receipt + "\n\n" + continuation, settings)
    assert speech == continuation
    assert committed["facts"]["client"]["region_class"] == "rf_other"
    assert committed["facts"]["debts"]["total"] == 400000
    assert debt_policy.debt_delivery_held(committed)


def test_a_meaningful_region_clarification_is_not_an_empty_receipt(monkeypatch, settings):
    state = debt_state(client={"phone": "+70000000401"}, debts={"total": 400000})
    candidate = "Москва — это и город, и регион, так что всё в порядке.\n\nПодскажите, как к вам обращаться?"
    speech, committed = run_turn(monkeypatch, state, "Москва, регион не знаю.", candidate, settings)
    assert speech == candidate
    assert committed["script"]["asked"] == ["client.name"]


def test_an_explicit_confirmation_request_keeps_its_meaningful_confirmation(monkeypatch, settings):
    state = complete_debt_state()
    # Model the presentation boundary after the current field was accepted locally.
    state["_policy_received_fields"] = ["client.region_class"]
    candidate = "Да, регион я вижу."
    monkeypatch.setattr(core.llm_agent, "generate_reply", lambda *args, **kwargs: candidate)
    speech = "".join(core.stream_reply(state, "Мой регион вы правильно поняли?", settings))
    assert speech == candidate


def test_the_current_known_contact_purpose_survives_no_without_becoming_a_request(monkeypatch, settings):
    state = onloader_state()
    _, state = run_turn(monkeypatch, state, "Нет, не разрешаю передачу.", "Хорошо, не передаю.", settings)
    candidate = ("Контакт нужен компании, чтобы связаться с вами и проконсультировать по обращению.\n\n"
                 "Общаться здесь можно и без передачи данных, так что выбор за вами.")
    bodies = []

    def post(configuration, body, **kwargs):
        bodies.append(deepcopy(body))
        reply = (json.dumps({"accept": True, "flags": []})
                 if configuration.model == configuration.extractor_model else candidate)
        return {"choices": [{"message": {"content": reply}}]}

    monkeypatch.setattr(core.llm_agent, "generate_reply", _ACTUAL_GENERATE)
    monkeypatch.setattr(core.llm_agent, "_post", post)
    monkeypatch.setattr(policy_voice_critic, "assess", _ACTUAL_ASSESS)
    message = "Можно просто пояснить, для чего нужны эти сведения?"
    prepared = core.prepare_turn(state, message, settings, submit_delivery=False)
    speech = "".join(core.stream_reply(prepared, message, settings))
    committed = core.commit_turn(prepared, message, speech)["current_state"]
    assert speech == candidate
    assert len(bodies) == 2
    diagnostic = json.loads(bodies[-1]["messages"][-1]["content"])
    assert diagnostic["draft"] == speech
    assert diagnostic["frame"]["contact_declined"] is True
    assert diagnostic["frame"]["expected"] is None
    assert core.onloader_policy.chat_declined(committed)
    assert committed["onloader"]["decision"] is None
    assert not committed.get("delivery")


@pytest.mark.parametrize("unsafe", [
    "Контакт нужен компании для консультации. Оставьте ваш номер телефона.",
    "Контакт нужен компании для консультации. Без номера продолжить общение здесь не получится.",
    "Контакт нужен компании для консультации. Если передумаете, оставьте номер.",
])
def test_a_purpose_explanation_never_authorizes_renewed_collection_or_a_chat_barrier(unsafe):
    frame = DialogueFrame(None, "onloader", known_fields=frozenset({"client.phone"}), mode="onloader",
                          conversation_declined=True, offer_present=True, offer_status="awaiting_consent",
                          question_goal="offer_information_purpose")
    assert not policy_voice.validate_reply(unsafe, None, frame=frame)


def test_an_advisory_revision_cannot_replace_a_valid_phone_invitation_with_a_false_storage_requirement(
    monkeypatch, settings,
):
    state = debt_state(client={"name": "Ирина Синтетическая Проверочная", "region_class": "moscow_mo"},
                       debts={"total": 280000})
    first = "Оставите номер для оформления анкеты? Обсудить вопросы можно и без него."
    invalid_revision = "Чтобы сохранить ваши ответы в анкете, нужен номер телефона."
    bodies = []
    speaker_calls = 0

    def post(configuration, body, **kwargs):
        nonlocal speaker_calls
        assert kwargs["max_attempts"] == 1
        bodies.append(deepcopy(body))
        if configuration.model == configuration.extractor_model:
            reply = json.dumps({"accept": False, "flags": ["unneeded_question"]})
        else:
            speaker_calls += 1
            reply = first if speaker_calls == 1 else invalid_revision
        return {"choices": [{"message": {"content": reply}}]}

    # Exercise the real transport composition and schema boundary, with every
    # actual provider request intercepted before external I/O.
    monkeypatch.setattr(core.llm_agent, "generate_reply", _ACTUAL_GENERATE)
    monkeypatch.setattr(core.llm_agent, "_post", post)
    monkeypatch.setattr(policy_voice_critic, "assess", _ACTUAL_ASSESS)
    message = "Ирина."
    prepared = core.prepare_turn(state, message, settings, submit_delivery=False)
    speech = "".join(core.stream_reply(prepared, message, settings))
    committed = core.commit_turn(prepared, message, speech)["current_state"]

    assert [body["model"] for body in bodies] == [settings.model, settings.extractor_model, settings.model]
    assert speech == first
    spoken = prepared["_policy_spoken"]
    assert spoken.source == "model" and spoken.reason == "model_advisory_disputed_repair_failed"
    assert spoken.critic_calls == 1
    assert json.loads(bodies[1]["messages"][-1]["content"])["draft"] == speech
    assert first not in json.dumps(bodies[2], ensure_ascii=False)
    assert all(value not in json.dumps(bodies, ensure_ascii=False)
               for value in ("Ирина", "Синтетическая", "Проверочная"))
    assert committed["script"]["asked"] == ["client.phone"]
    assert not committed["facts"]["client"].get("phone")
    assert debt_policy.debt_delivery_held(committed)
    assert not committed.get("delivery")


@pytest.mark.parametrize("mode,goal,candidate", [
    ("debt", "debt_explanation",
     "Списание — это полное прекращение обязательств через суд, обычно когда платить объективно нечем."),
    ("onloader", "offer_information_purpose",
     "Телефон повторно вводить не нужно, он уже есть. Данные уйдут компании «Тестовая компания» — "
     "это тестовая передача для проверки связи."),
])
def test_the_recorded_v7_content_errors_are_not_validated_as_safe_speech(mode, goal, candidate):
    frame = DialogueFrame(None, "onloader" if mode == "onloader" else "collected_held", mode=mode,
                          question_goal=goal, known_fields=frozenset({"client.phone"}),
                          offer_present=mode == "onloader", offer_status="awaiting_consent")
    assert not policy_voice.validate_reply(candidate, None, frame=frame)


@pytest.mark.parametrize("mode,goal,candidate", [
    ("debt", "debt_explanation",
     "При банкротстве возможно освобождение от части долгов по решению суда. "
     "Это не гарантирует освобождение от всех обязательств: например, алименты сохраняются."),
    ("onloader", "offer_information_purpose",
     "Сведения нужны компании для связи с вами и консультации по обращению. "
     "Здесь используется тестовая передача. Продолжить разговор можно без неё."),
])
def test_qualified_debt_explanations_and_a_truthful_test_marker_remain_valid(mode, goal, candidate):
    frame = DialogueFrame(None, "onloader" if mode == "onloader" else "collected_held", mode=mode,
                          question_goal=goal, known_fields=frozenset({"client.phone"}),
                          offer_present=mode == "onloader", offer_status="awaiting_consent")
    assert policy_voice.validate_reply(candidate, None, frame=frame)


@pytest.mark.parametrize("kind", ["bankruptcy", "transfer"])
def test_a_recorded_content_failure_is_repaired_before_a_diagnostic_call_with_state_and_privacy_intact(
    monkeypatch, settings, kind,
):
    if kind == "bankruptcy":
        state = complete_debt_state()
        message = "Чем реструктуризация отличается от списания?"
        rejected = "Списание — это полное прекращение обязательств через суд, обычно когда платить объективно нечем."
        final = ("Реструктуризация меняет порядок выплаты долга. При банкротстве возможно освобождение "
                 "от части требований по решению суда, но не от всех обязательств: например, алименты сохраняются.")
    else:
        state = onloader_state()
        message = "А зачем снова телефон и кому уйдут данные? Поясните по-человечески."
        rejected = ("Телефон повторно вводить не нужно, он уже есть. Данные уйдут компании «Тестовая компания» — "
                    "это тестовая передача для проверки связи.")
        final = ("Номер повторять не нужно. Сведения нужны компании «Тестовая компания» для связи с вами "
                 "и консультации по обращению. Здесь используется тестовая передача. Общаться можно и без неё.")
    before_facts = deepcopy(state["facts"])
    bodies = []
    speakers = 0

    def post(configuration, body, **kwargs):
        nonlocal speakers
        assert kwargs["max_attempts"] == 1
        bodies.append(deepcopy(body))
        if configuration.model == configuration.extractor_model:
            reply = json.dumps({"accept": True, "flags": []})
        else:
            speakers += 1
            reply = rejected if speakers == 1 else final
        return {"choices": [{"message": {"content": reply}}]}

    monkeypatch.setattr(core.llm_agent, "generate_reply", _ACTUAL_GENERATE)
    monkeypatch.setattr(core.llm_agent, "_post", post)
    monkeypatch.setattr(policy_voice_critic, "assess", _ACTUAL_ASSESS)
    prepared = core.prepare_turn(state, message, settings, submit_delivery=False)
    speech = "".join(core.stream_reply(prepared, message, settings))
    committed = core.commit_turn(prepared, message, speech)["current_state"]

    assert speech == final
    assert [body["model"] for body in bodies] == [settings.model, settings.model, settings.extractor_model]
    assert json.loads(bodies[-1]["messages"][-1]["content"])["draft"] == speech
    assert all(value not in json.dumps(bodies, ensure_ascii=False) for value in ("Ирина", "+70000000401"))
    assert committed["facts"] == before_facts
    assert not committed.get("delivery")
    if kind == "transfer":
        assert committed["onloader"]["decision"] is None
    else:
        assert debt_policy.debt_delivery_held(committed)


def test_the_recorded_comparison_noun_is_not_a_promised_writeoff_predicate():
    frame = DialogueFrame(None, "collected_held", question_goal="debt_explanation")
    comparison = "Реструктуризация — это изменение условий долга, а списание — освобождение от него."
    assert policy_voice.validate_reply(comparison, None, frame=frame)
    assert not policy_voice.validate_reply("Ваши долги будут списаны.", None, frame=frame)


@pytest.mark.parametrize("kind", ["comparison", "remaining_amount"])
def test_the_recorded_second_draft_keeps_its_comparison_or_positive_remaining_amount_question(
    monkeypatch, settings, kind,
):
    if kind == "comparison":
        state = complete_debt_state()
        message = "Чем реструктуризация отличается от списания?"
        first = "Списание — это полное прекращение обязательств через суд."
        final = ("Реструктуризация — это изменение условий долга, а списание — освобождение от него.\n\n"
                 "Оно не автоматически и не от всех обязательств: например, алименты сохраняются.")
    else:
        state = debt_state()
        message = "Примерно 280 тысяч рублей."
        first = "Давайте зафиксируем точнее: ровно 280 000 рублей?"
        final = ("Для подбора вариантов нужна точная цифра, а не примерная.\n\n"
                 "Назовите, пожалуйста, остаток долга одним числом в рублях.")
    bodies = []
    speakers = 0

    def post(configuration, body, **kwargs):
        nonlocal speakers
        assert kwargs["max_attempts"] == 1
        bodies.append(deepcopy(body))
        if configuration.model == configuration.extractor_model:
            content = json.dumps({"accept": True, "flags": []})
        else:
            speakers += 1
            content = first if speakers == 1 else final
        return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(core.llm_agent, "generate_reply", _ACTUAL_GENERATE)
    monkeypatch.setattr(core.llm_agent, "_post", post)
    monkeypatch.setattr(policy_voice_critic, "assess", _ACTUAL_ASSESS)
    prepared = core.prepare_turn(state, message, settings, submit_delivery=False)
    speech = "".join(core.stream_reply(prepared, message, settings))
    committed = core.commit_turn(prepared, message, speech)["current_state"]

    assert speech == final
    assert [body["model"] for body in bodies] == [settings.model, settings.model, settings.extractor_model]
    assert json.loads(bodies[-1]["messages"][-1]["content"])["draft"] == speech
    assert all(private not in json.dumps(bodies, ensure_ascii=False) for private in ("Ирина", "+70000000401"))
    assert not committed.get("delivery")
    if kind == "remaining_amount":
        assert committed["script"]["asked"] == ["debts.total"]
        policy = debt_policy.parse_policy(committed)
        assert policy is not None and policy.amount_observation is not None
        assert policy.amount_observation.kind == "unknown"
        assert not committed["facts"].get("debts", {}).get("total")
        explicit = core.prepare_turn(committed, "280000", settings, submit_delivery=False)
        assert explicit["facts"]["debts"]["total"] == 280000


@pytest.mark.parametrize("affirmative", ["Да, по моим долгам.", "Да, хочу."])
def test_the_recorded_declarative_intent_answer_leaves_clarification_without_creating_a_debt_amount(
    monkeypatch, settings, affirmative,
):
    state = init_state()
    _, state = run_turn(monkeypatch, state, "Здравствуйте. Мне бы с долгами помочь.",
                        "Вы хотите обсудить реструктуризацию именно ваших долгов?", settings)
    policy = debt_policy.parse_policy(state)
    assert policy is not None and policy.mode == "clarify"
    question = "Какова общая сумма вашего долга?"
    speech, committed = run_turn(monkeypatch, state, affirmative, question, settings)
    policy = debt_policy.parse_policy(committed)
    assert policy is not None and policy.mode == "debt"
    assert speech == question
    assert committed["script"]["asked"] == ["debts.total"]
    assert not committed["facts"].get("debts", {}).get("total")
    assert debt_policy.debt_delivery_held(committed)
    assert not committed.get("delivery")


def test_the_recorded_number_before_bookkeeping_verb_does_not_claim_unknown_debt_is_recorded():
    frame = DialogueFrame("debts.total", "collecting_debt_context", confirming=True)
    assert not policy_voice.validate_reply("Хорошо, 400 тысяч зафиксировала.", frame.allowed_field, frame=frame)


def test_the_recorded_bookkeeping_claim_is_removed_while_the_actual_debt_fact_stays_unknown(monkeypatch, settings):
    state = debt_state(client={"name": "Ирина", "phone": "+70000000401"})
    claim = "Хорошо, 400 тысяч зафиксировала."
    candidate = claim + "\n\nУточните, пожалуйста, общую сумму долга одним числом в рублях, без диапазона."
    speech, committed = run_turn(monkeypatch, state, "Ближе к 400 тысячам.", candidate, settings)
    assert claim not in speech
    assert committed["script"]["asked"] == ["debts.total"]
    policy = debt_policy.parse_policy(committed)
    assert policy is not None and policy.amount_observation is not None
    assert policy.amount_observation.kind == "unknown"
    assert not committed["facts"].get("debts", {}).get("total")
    assert not committed.get("delivery")


@pytest.mark.parametrize("candidate,known", [
    ("Вы назвали примерно 400 тысяч, точная сумма пока неизвестна.", False),
    ("Примерно 400.5 тысяч зафиксировала.", False),
    ("400 тысяч не зафиксировала как точную сумму.", False),
    ("Фраза «400 тысяч зафиксировала» не подтверждает точную сумму.", False),
    ("Хорошо, 400 тысяч зафиксировала.", True),
])
def test_bookkeeping_scope_preserves_qualified_report_denial_quotation_and_a_known_amount(candidate, known):
    frame = DialogueFrame("debts.total", "collecting_debt_context", confirming=True,
                          known_fields=frozenset({"debts.total"}) if known else frozenset())
    assert policy_voice.validate_reply(candidate, frame.allowed_field, frame=frame)
