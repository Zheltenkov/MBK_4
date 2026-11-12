"""Independent finite regressions from the observed debt-dialogue failures."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

import core
import debt_policy
from dialogue_frame import DialogueFrame
import policy_voice
from config import AppConfig
from session_store import Session
from tests.test_policy_voice import debt_state, onloader_state
from usage_tracker import SessionUsage


@pytest.fixture
def configuration():
    return AppConfig(openrouter_api_key="synthetic-unused", debt_policy_enabled=True,
                     policy_voice_enabled=True)


@pytest.fixture(autouse=True)
def local_boundaries(monkeypatch):
    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *args, **kwargs: {
        "fact_updates": [], "internal_summary": "",
    })
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *args, **kwargs: deepcopy(facts))
    monkeypatch.setattr(core, "_record_transcript", lambda *args, **kwargs: None)

    def forbidden(*args, **kwargs):
        pytest.fail("An independent dialogue test attempted external transport or delivery")

    monkeypatch.setattr(core.llm_agent, "_urlopen", forbidden)
    monkeypatch.setattr(core.delivery, "submit", forbidden)


def actual_turn(monkeypatch, state, message, candidate, configuration):
    monkeypatch.setattr(core.llm_agent, "generate_reply", lambda *args, **kwargs: candidate)
    # Exercise selection, fact reconciliation, speech and cursor commit together.
    prepared = core.prepare_turn(state, message, configuration, submit_delivery=True)
    speech = "".join(core.stream_reply(prepared, message, configuration))
    committed = core.commit_turn(prepared, message, speech)["current_state"]
    return speech, committed


def collected_state():
    return debt_state(client={"name": "Ирина", "phone": "+70000000401", "region_class": "rf_other"},
                      debts={"total": 400000})


@pytest.mark.parametrize("question", [
    "Подскажите, какая у вас сейчас общая сумма долгов по всем кредитам и займам?",
    "Подскажите, сколько месяцев уже есть просрочки?",
    "Сейчас есть активные просрочки по платежам?",
    "В каком городе вы живёте?",
    "Подскажите, как к вам лучше обращаться?",
    "Вы живёте в самой Москве или в области?",
    "Уточните, пожалуйста, где именно вы живёте?",
])
def test_a_completed_plan_does_not_reopen_known_or_unplanned_collection(monkeypatch, configuration, question):
    state = collected_state()
    before_facts = deepcopy(state["facts"])
    before_attempts = deepcopy(state.get("debt_script", {}).get("attempts", {}))
    candidate = "Реструктуризация меняет условия выплат.\n\n" + question
    speech, committed = actual_turn(monkeypatch, state, "Чем отличается реструктуризация?",
                                    candidate, configuration)

    assert question not in speech
    assert committed["facts"] == before_facts
    assert committed["plan"]["fields"] == []
    assert committed.get("debt_script", {}).get("attempts", {}) == before_attempts
    assert debt_policy.debt_delivery_held(committed)


def test_an_out_of_order_known_amount_question_does_not_replace_the_missing_region(monkeypatch, configuration):
    state = debt_state(client={"name": "Ирина", "phone": "+70000000401"}, debts={"total": 400000})
    question = "Какая общая сумма ваших долгов?"
    speech, committed = actual_turn(monkeypatch, state, "А зачем это уточнять?",
                                    "Вы уже сообщили сумму.\n\n" + question, configuration)

    assert question not in speech
    assert committed["facts"]["debts"]["total"] == 400000
    assert committed["plan"]["fields"] == ["client.region_class"]
    assert committed["script"]["asked"] != ["debts.total"]


def test_a_planned_question_keeps_its_natural_explanation(monkeypatch, configuration):
    state = debt_state(client={"name": "Ирина", "phone": "+70000000401"}, debts={"total": 400000})
    candidate = "Сумма нужна для первого разбора.\n\nВ каком городе вы живёте?"
    speech, committed = actual_turn(monkeypatch, state, "Зачем вам эти сведения?", candidate, configuration)

    assert speech == candidate
    assert committed["script"]["asked"] == ["client.region_class"]


def test_a_conversational_question_is_allowed_after_collection(monkeypatch, configuration):
    candidate = "Это разные способы работы с долгом.\n\nХотите, объясню разницу?"
    speech, committed = actual_turn(monkeypatch, collected_state(), "Я не понимаю разницу.",
                                    candidate, configuration)

    assert speech == candidate
    assert committed["plan"]["fields"] == []


def test_an_explicit_amount_correction_can_require_confirmation_of_a_previously_known_value(
    monkeypatch, configuration,
):
    candidate = "Какова точная общая сумма долга?"
    speech, committed = actual_turn(
        monkeypatch, collected_state(),
        "Пересчитал: общая сумма долга от 350000 до 450000 рублей.",
        candidate, configuration,
    )

    # The old scalar remains historical evidence, while the new range needs confirmation.
    policy = debt_policy.parse_policy(committed)
    assert policy is not None and policy.amount_observation is not None
    assert policy.amount_observation.kind == "range"
    assert committed["facts"]["debts"]["total"] == 400000
    assert committed["plan"]["fields"] == ["debts.total"]
    assert speech == candidate


@pytest.mark.parametrize("candidate,answer", [
    ("Это ровно 400 тысяч или больше/меньше?", "400000 рублей."),
    ("К какой сумме ближе — 350 тысяч, 400 или больше?", "400000 рублей."),
    ("400 тысяч — это точная сумма или всё-таки примерно?", "400000 рублей."),
    ("Назовите, пожалуйста, одну конкретную сумму в рублях.", "400000 рублей."),
    ("Сколько именно осталось вернуть?", "Да, 400 000."),
    # The exact-debt parser consumes a stated number, not a Yes/No confirmation.
    ("Напишите точную сумму одним числом в рублях.", "Да, 400 000."),
])
def test_a_planned_amount_confirmation_keeps_its_binding_without_repeating_the_word_debt(
    monkeypatch, configuration, candidate, answer,
):
    speech, committed = actual_turn(
        monkeypatch, collected_state(),
        "Пересчитал: общая сумма долга от 350000 до 450000 рублей.",
        candidate, configuration,
    )
    assert speech == candidate
    assert committed["script"]["asked"] == ["debts.total"]

    # The response is bound to an actually spoken confirmation, not a new credit request.
    prepared = core.prepare_turn(committed, answer, configuration, submit_delivery=False)
    policy = debt_policy.parse_policy(prepared)
    assert policy is not None and policy.amount_observation is not None
    assert policy.amount_observation.kind == "exact"
    assert prepared["facts"]["debts"]["total"] == 400000
    assert prepared["plan"]["fields"] == []


def test_a_pending_debt_question_accepts_the_recorded_affirmative_number_reply(monkeypatch, configuration):
    _, committed = actual_turn(
        monkeypatch, collected_state(),
        "Пересчитал: общая сумма долга от 350000 до 450000 рублей.",
        "Какова точная общая сумма долга?", configuration,
    )
    assert committed["script"]["asked"] == ["debts.total"]
    prepared = core.prepare_turn(committed, "Да, 400 000.", configuration, submit_delivery=False)
    policy = debt_policy.parse_policy(prepared)
    assert policy is not None and policy.amount_observation is not None
    assert policy.amount_observation.kind == "exact"
    assert prepared["facts"]["debts"]["total"] == 400000


def test_the_same_affirmative_number_without_a_spoken_debt_question_is_not_new_debt(configuration):
    state = debt_state()
    state["script"]["asked"] = []
    prepared = core.prepare_turn(state, "Да, 400 000.", configuration, submit_delivery=False)
    assert prepared.get("facts", {}).get("debts", {}).get("total") is None


def test_unknown_amount_fallback_records_no_question_and_accepts_a_later_exact_amount(monkeypatch, configuration):
    _, committed = actual_turn(
        monkeypatch, collected_state(),
        "Пересчитал: общая сумма долга от 350000 до 450000 рублей.",
        "Долги спишут.", configuration,
    )
    # The truthful fallback leaves precision open without pretending to have asked again.
    assert committed["script"]["asked"] == []
    policy = debt_policy.parse_policy(committed)
    assert policy is not None and policy.amount_observation is not None
    assert policy.amount_observation.kind == "range"
    # The pending task still accepts a number the client independently supplies later.
    prepared = core.prepare_turn(committed, "400000 рублей.", configuration, submit_delivery=False)
    policy = debt_policy.parse_policy(prepared)
    assert policy is not None and policy.amount_observation is not None
    assert policy.amount_observation.kind == "exact"


def test_confirming_a_debt_does_not_authorize_a_new_loan_question(monkeypatch, configuration):
    wrong_question = "На какую сумму нового кредита рассчитываете?"
    speech, committed = actual_turn(
        monkeypatch, collected_state(),
        "Пересчитал: общая сумма долга от 350000 до 450000 рублей.",
        wrong_question, configuration,
    )
    assert wrong_question not in speech
    # The existing uncertain observation cannot become a selected new-loan amount.
    policy = debt_policy.parse_policy(committed)
    assert policy is not None and policy.amount_observation is not None
    assert policy.amount_observation.kind == "range"


@pytest.mark.parametrize("refusal", [
    "Не хочу оставлять номер.",
    "Нет.",
    "Номер оставлять не хочу. Можно без него?",
    "Не хочу оставлять.",
    "Не оставляю номер.",
])
def test_a_phone_refusal_closes_collection_without_closing_debt_consultation(monkeypatch, configuration, refusal):
    state = debt_state(client={"name": "Сергей", "region_class": "moscow_mo"}, debts={"total": 280000})
    assert state["script"]["asked"] == ["client.phone"]
    pressure = "Без номера не смогу передать вашу ситуацию специалистам."
    request = "Можете оставить контакт, чтобы с вами просто связались?"
    speech, committed = actual_turn(monkeypatch, state, refusal, pressure + "\n\n" + request, configuration)

    policy = debt_policy.parse_policy(committed)
    assert policy is not None and policy.phone_collection == "declined"
    assert policy.mode == "debt"
    assert committed["plan"]["fields"] == []
    assert pressure not in speech
    assert request not in speech
    assert not committed["facts"].get("client", {}).get("phone")
    assert debt_policy.debt_delivery_held(committed)


def test_phone_refusal_survives_serialization_flag_off_and_a_later_question(monkeypatch, configuration):
    state = debt_state(client={"name": "Сергей", "region_class": "moscow_mo"}, debts={"total": 280000})
    _, state = actual_turn(monkeypatch, state, "Не хочу оставлять номер.",
                           "Можно продолжить разговор здесь.", configuration)
    # This proves record compatibility, while actual process restart has separate coverage.
    session = Session(id="synthetic-dialogue-review", state=state, anketa=None,
                      usage=SessionUsage("synthetic-dialogue-review"))
    restored = Session.from_record(session.storage_record()).state
    disabled = replace(configuration, debt_policy_enabled=False)
    request = "Оставите контакт для связи?"
    speech, committed = actual_turn(monkeypatch, restored, "Можно без банкротства обойтись?",
                                    "Есть разные способы обсудить ситуацию.\n\n" + request, disabled)

    policy = debt_policy.parse_policy(committed)
    assert policy is not None and policy.phone_collection == "declined" and policy.mode == "debt"
    assert request not in speech
    assert committed["plan"]["fields"] == []


def test_a_refusal_does_not_strip_reassurance_that_the_conversation_can_continue_without_a_number(
    monkeypatch, configuration,
):
    state = debt_state(client={"name": "Сергей", "region_class": "moscow_mo"}, debts={"total": 280000})
    candidate = "Без номера можно продолжить разговор здесь."
    speech, committed = actual_turn(monkeypatch, state, "Не хочу оставлять номер.", candidate, configuration)

    assert speech == candidate
    policy = debt_policy.parse_policy(committed)
    assert policy is not None and policy.phone_collection == "declined"


def test_the_recorded_after_no_reply_cannot_make_reconsidering_contact_a_condition_for_help(
    monkeypatch, configuration,
):
    state = debt_state(client={"name": "Сергей", "region_class": "moscow_mo"}, debts={"total": 280000})
    barrier = "Без номера заявку дальше не передать, это обязательное условие."
    reconsideration = "Но настаивать не буду. Если надумаете — напишите, и мы продолжим с того же места."
    speech, committed = actual_turn(
        monkeypatch, state, "Номер оставлять не хочу. Можно без него?",
        barrier + "\n\n" + reconsideration, configuration,
    )

    assert barrier not in speech
    assert "мы продолжим с того же места" not in speech
    policy = debt_policy.parse_policy(committed)
    assert policy is not None and policy.phone_collection == "declined"
    assert committed["plan"]["fields"] == []


@pytest.mark.parametrize("message,collection", [
    ("А зачем вам телефон?", "open"),
    ("Не хочу оставлять номер. Для чего он вообще?", "declined"),
])
def test_explaining_contact_purpose_does_not_become_collection_or_pressure(
    monkeypatch, configuration, message, collection,
):
    state = debt_state(client={"name": "Сергей", "region_class": "moscow_mo"}, debts={"total": 280000})
    explanation = "Телефон используют для связи по обращению. В чате можно продолжать без номера."
    speech, committed = actual_turn(monkeypatch, state, message, explanation, configuration)

    assert speech == explanation
    policy = debt_policy.parse_policy(committed)
    assert policy is not None and policy.phone_collection == collection
    assert not committed["facts"]["client"].get("phone")
    assert not committed.get("delivery")


def test_a_phone_question_cannot_invent_a_specialist_handoff_that_the_frame_cannot_perform(
    monkeypatch, configuration,
):
    state = debt_state(client={"name": "Сергей", "region_class": "moscow_mo"}, debts={"total": 280000})
    candidate = (
        "Чтобы я могла передать вашу ситуацию специалисту, "
        "оставьте, пожалуйста, номер телефона для связи."
    )
    speech, committed = actual_turn(monkeypatch, state, "А зачем номер?", candidate, configuration)

    assert candidate not in speech
    assert "могла передать вашу ситуацию" not in speech
    assert not committed.get("delivery")
    assert debt_policy.debt_delivery_held(committed)


def test_an_explicitly_volunteered_phone_can_reopen_contact_without_authorizing_delivery(monkeypatch, configuration):
    state = debt_state(client={"name": "Сергей", "region_class": "moscow_mo"}, debts={"total": 280000})
    _, state = actual_turn(monkeypatch, state, "Не хочу оставлять номер.",
                           "Можно продолжить разговор здесь.", configuration)
    _, committed = actual_turn(monkeypatch, state, "Я передумал, мой номер +79990000001.",
                               "Можно обсудить ваши вопросы здесь.", configuration)

    policy = debt_policy.parse_policy(committed)
    assert policy is not None and policy.phone_collection == "open"
    assert committed["facts"]["client"].get("phone")
    assert not committed.get("delivery")
    assert debt_policy.debt_delivery_held(committed)


def test_a_negative_region_answer_is_not_a_phone_refusal(monkeypatch, configuration):
    state = debt_state(client={"name": "Сергей"}, debts={"total": 280000})
    assert state["script"]["asked"] == ["client.region_class"]
    _, committed = actual_turn(monkeypatch, state, "Нет.", "Можем уточнить место позже.", configuration)
    policy = debt_policy.parse_policy(committed)
    assert policy is not None and policy.phone_collection == "open"


def test_the_ordinary_credit_conversation_keeps_its_existing_question_surface(monkeypatch, configuration):
    from state import init_state

    candidate = "Какая сумма кредита вам нужна?"
    monkeypatch.setattr(core.llm_agent, "stream_conversation", lambda *args, **kwargs: iter([candidate]))
    speech = "".join(core.stream_reply(init_state(), "Нужен кредит", configuration))
    assert speech == candidate


CONDITIONAL_OFFER_REPLY = (
    "Данные уйдут в компанию «Тестовая компания» для консультации.\n\n"
    "Подтвердите передачу кнопкой под сообщением, чтобы я могла отправить заявку."
)


def test_a_real_pending_offer_can_explain_its_button_gated_action_without_performing_it(
    monkeypatch, configuration,
):
    state = onloader_state()
    before = deepcopy(state["onloader"])
    speech, committed = actual_turn(monkeypatch, state, "Да. А кому именно уйдут данные?",
                                    CONDITIONAL_OFFER_REPLY, configuration)

    assert speech == CONDITIONAL_OFFER_REPLY
    assert committed["onloader"] == before
    assert committed["onloader"]["decision"] is None


@pytest.mark.parametrize("offer_status,present,declined", [
    (None, False, False),
    ("queued", True, False),
    ("outcome_unknown", True, False),
    ("declined", True, False),
    ("awaiting_consent", True, True),
])
def test_the_same_conditional_reply_requires_a_pending_offer_and_an_open_conversation(
    offer_status, present, declined,
):
    frame = DialogueFrame(None, "onloader", mode="onloader", offer_present=present,
                          offer_status=offer_status, conversation_declined=declined)
    assert not policy_voice.validate_reply(CONDITIONAL_OFFER_REPLY, None, frame=frame)


@pytest.mark.parametrize("text", [
    "Подтвердите передачу кнопкой. Передаю ваши данные.",
    "Подтвердите передачу кнопкой, чтобы я могла отправить заявку. Заявка отправлена.",
])
def test_a_button_precondition_never_licenses_a_present_or_completed_delivery_claim(text):
    frame = DialogueFrame(None, "onloader", mode="onloader", offer_present=True,
                          offer_status="awaiting_consent")
    assert not policy_voice.validate_reply(text, None, frame=frame)


@pytest.mark.parametrize("message,declined_before,unsafe", [
    ("Нет, не хочу передавать данные.", False,
     "Хорошо, не настаиваю. Передачу отменяем.\n\nЕсли останутся вопросы по долгам или кредитам — пишите, я на связи."),
    ("Зачем вообще передавать мои сведения?", True,
     "Сведения нужны, чтобы компания могла связаться с вами и предложить решение по вашей ситуации.\n\n"
     "Без передачи контактов консультация невозможна, но это ваше право — решать только вам."),
])
def test_the_recorded_onloader_no_does_not_cancel_an_operation_or_close_chat_help(
    monkeypatch, configuration, message, declined_before, unsafe,
):
    state = onloader_state()
    if declined_before:
        state["onloader_dialogue"] = {
            "presentation_id": state["onloader"]["presentation_id"], "declined": True,
        }
    calls = []
    safe = "Сведения нужны для связи по обращению. Обсуждать ваши вопросы здесь можно без передачи контактов."

    def generate(*args, **kwargs):
        calls.append(args)
        return unsafe if len(calls) == 1 else safe

    monkeypatch.setattr(core.llm_agent, "generate_reply", generate)
    prepared = core.prepare_turn(state, message, configuration, submit_delivery=True)
    speech = "".join(core.stream_reply(prepared, message, configuration))
    committed = core.commit_turn(prepared, message, speech)["current_state"]

    assert speech == safe
    assert len(calls) == 2
    assert committed["onloader"]["decision"] is None
    assert committed["onloader"]["status"] == "awaiting_consent"
    assert committed["onloader_dialogue"]["declined"] is True
    assert committed["plan"]["fields"] == []


def test_a_valid_after_no_explanation_is_preserved_without_repair_or_backend_decision(monkeypatch, configuration):
    state = onloader_state()
    state["onloader_dialogue"] = {
        "presentation_id": state["onloader"]["presentation_id"], "declined": True,
    }
    explanation = "Компания использует сведения для связи по обращению. Здесь можно продолжать без передачи контактов."
    speech, committed = actual_turn(monkeypatch, state, "Зачем передавать сведения?", explanation, configuration)
    assert speech == explanation
    assert committed["onloader"]["decision"] is None
    assert committed["onloader_dialogue"]["declined"] is True


def test_the_recorded_lab_call_denial_repairs_the_purpose_answer_with_at_most_two_calls(monkeypatch, configuration):
    unsafe = (
        "Это тестовая передача, чтобы проверить, как работает чат и карточка предложения. "
        "Реальной заявки или звонков не будет.\n\nНажмите кнопку «Да» под сообщением, "
        "чтобы подтвердить передачу в тестовом режиме."
    )
    explanation = "Сведения нужны компании для связи по вашему обращению. Передача требует отдельного разрешения."
    calls = []

    def generate(*args, **kwargs):
        calls.append(args)
        return unsafe if len(calls) == 1 else explanation

    monkeypatch.setattr(core.llm_agent, "generate_reply", generate)
    state = onloader_state()
    prepared = core.prepare_turn(state, "Да. Объясните по-человечески, зачем это нужно?", configuration)
    speech = "".join(core.stream_reply(prepared, "Да. Объясните по-человечески, зачем это нужно?", configuration))
    committed = core.commit_turn(prepared, "Да. Объясните по-человечески, зачем это нужно?", speech)["current_state"]
    assert speech == explanation
    assert len(calls) == 2
    assert committed["onloader"]["decision"] is None


def test_the_recorded_debt_faq_guard_rejection_repairs_the_answer_instead_of_repeating_the_terminal_state(
    monkeypatch, configuration,
):
    unsafe = (
        "Реструктуризация — это изменение условий долга: срок, ставка или график платежей. "
        "Долг остаётся, просто платить становится удобнее.\n\n"
        "Списание — это полное освобождение от обязательств через процедуру банкротства. "
        "Долг аннулируется, но у процедуры есть свои последствия и ограничения.\n\n"
        "Что из этого вас сейчас больше интересует?"
    )
    comparison = (
        "Реструктуризация меняет условия выплат: срок или график. "
        "Списание связано с процедурой банкротства и её условиями."
    )
    calls = []

    def generate(*args, **kwargs):
        calls.append(args)
        return unsafe if len(calls) == 1 else comparison

    monkeypatch.setattr(core.llm_agent, "generate_reply", generate)
    state = collected_state()
    facts_before = deepcopy(state["facts"])
    message = "Чем реструктуризация отличается от списания?"
    prepared = core.prepare_turn(state, message, configuration, submit_delivery=True)
    speech = "".join(core.stream_reply(prepared, message, configuration))
    committed = core.commit_turn(prepared, message, speech)["current_state"]

    assert speech == comparison
    assert len(calls) == 2
    assert committed["facts"] == facts_before
    assert committed["plan"]["fields"] == []
    assert debt_policy.debt_delivery_held(committed)


@pytest.mark.parametrize("unsafe", [
    "Спасибо, разрешение приняла.\n\nЕсли останутся вопросы по долгам — пишите, я на связи.",
    "Спасибо за согласие. По-человечески: это просто проверка, что чат корректно передаёт заявку и компания её получает.",
    "Никаких реальных звонков или обязательств сейчас не будет — это технический тест.",
])
def test_text_yes_does_not_license_unrecorded_permission_or_call_guarantees(monkeypatch, configuration, unsafe):
    clarification = "Для передачи нужно отдельное действие под предложением. Вопросы можем обсудить здесь."
    calls = []

    def generate(*args, **kwargs):
        calls.append(args)
        return unsafe if len(calls) == 1 else clarification

    monkeypatch.setattr(core.llm_agent, "generate_reply", generate)
    before = onloader_state()
    prepared = core.prepare_turn(before, "Да.", configuration, submit_delivery=True)
    speech = "".join(core.stream_reply(prepared, "Да.", configuration))
    committed = core.commit_turn(prepared, "Да.", speech)["current_state"]

    assert speech == clarification
    assert len(calls) == 2
    assert committed["onloader"] == before["onloader"]
    assert committed["onloader"]["decision"] is None


def test_a_two_bubble_repair_keeps_its_actual_question_within_the_three_bubble_wire_budget(
    monkeypatch, configuration,
):
    state = debt_state(client={"phone": "+70000000401"}, debts={"total": 400000})
    calls = []
    candidates = [
        "Москва и Московская область — это разные регионы.\n\nС регионом теперь разобрались, спасибо.",
        "С регионом разобрались, спасибо.\n\nКак к вам обращаться?",
    ]

    def generate(*args, **kwargs):
        calls.append(args)
        return candidates[min(len(calls) - 1, 1)]

    monkeypatch.setattr(core.llm_agent, "generate_reply", generate)
    message = "Именно в Москве."
    prepared = core.prepare_turn(state, message, configuration, submit_delivery=True)
    speech = "".join(core.stream_reply(prepared, message, configuration))
    committed = core.commit_turn(prepared, message, speech)["current_state"]

    assert len(calls) == 2
    assert "Как к вам обращаться?" in speech
    assert len(core.prompts.split_bubbles(speech)) <= 3
    assert committed["script"]["asked"] == ["client.name"]
    assert committed["plan"]["fields"] == ["client.name"]


@pytest.mark.parametrize("declined,candidates,expected", [
    (True, [
        "Сведения нужны, чтобы компания могла связаться с вами и предложить решение по вашему вопросу.\n\n"
        "Без передачи контактов консультация от партнёра невозможна, но я могу продолжать отвечать на ваши вопросы здесь.",
        "Сведения нужны, чтобы компания могла связаться с вами и предложить помощь по вашему вопросу.\n\n"
        "Без передачи данных это сделать не получится, но консультацию здесь мы можем продолжать и без этого.",
    ], "Сведения нужны, чтобы компания могла связаться с вами и предложить помощь по вашему вопросу."),
    (False, [
        "Это тестовая передача, чтобы проверить, как работает связь между чатом и партнёром.\n\n"
        "Никаких реальных заявок или звонков сейчас не будет, просто проверяем техническую часть.\n\n"
        "Если согласны на этот тест, нажмите кнопку подтверждения под сообщением.",
        "Это тестовая передача, чтобы проверить, как работает связь между чатом и компанией.\n\n"
        "Никаких реальных звонков или предложений по долгам сейчас не будет.\n\n"
        "Если готовы участвовать в проверке, нажмите кнопку подтверждения под сообщением.",
    ], "Это тестовая передача, чтобы проверить, как работает связь между чатом и компанией."),
])
def test_the_recorded_v11_repairs_retain_the_safe_purpose_instead_of_a_state_only_fallback(
    monkeypatch, configuration, declined, candidates, expected,
):
    state = onloader_state()
    if declined:
        state["onloader_dialogue"] = {
            "presentation_id": state["onloader"]["presentation_id"], "declined": True,
        }
    calls = []

    def generate(*args, **kwargs):
        calls.append(args)
        return candidates[min(len(calls) - 1, 1)]

    monkeypatch.setattr(core.llm_agent, "generate_reply", generate)
    message = "Можно просто пояснить, для чего нужны эти сведения?" if declined else "Да, а объясните по-человечески, зачем это вообще?"
    prepared = core.prepare_turn(state, message, configuration, submit_delivery=True)
    speech = "".join(core.stream_reply(prepared, message, configuration))
    committed = core.commit_turn(prepared, message, speech)["current_state"]

    assert len(calls) == 2
    assert expected in speech
    assert "Никаких реальных" not in speech
    assert "не получится" not in speech
    assert "консультация от партнёра невозможна" not in speech
    assert "Если надумаете" not in speech
    assert committed["onloader"]["decision"] is None
    assert committed["plan"]["fields"] == []
    assert core.onloader_policy.chat_declined(committed) is declined
