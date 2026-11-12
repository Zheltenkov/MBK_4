"""Debt conversation progress through real prepare/commit, with offline boundaries."""
from __future__ import annotations

from copy import deepcopy
import json
import socket

import pytest

import core
import debt_policy
from config import AppConfig
from interpretation import FactCandidateExtraction
from state import init_state


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Dialogue progress tests never use external services")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *a, **k: {"fact_updates": [], "internal_summary": ""})
    monkeypatch.setattr(core.llm_agent, "extract_fact_candidates", lambda *a, **k: FactCandidateExtraction())
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *a, **k: deepcopy(facts))
    monkeypatch.setattr(core.delivery, "submit", forbidden)
    monkeypatch.setattr(core, "_record_transcript", lambda *a, **k: None)


def settings(mode="legacy", *, enabled=True):
    return AppConfig(openrouter_api_key="synthetic-unused", debt_policy_enabled=enabled,
                     entry_routing_enabled=False, fact_interpretation_mode=mode)


def debt_state(*, phone=None, name="Ирина", amount=400000):
    state = init_state()
    state["facts"] = {"client": {"name": name, "region_class": "rf_other"}, "debts": {"total": amount}}
    if phone is not None:
        state["facts"]["client"]["phone"] = phone
    state[debt_policy.POLICY_KEY] = debt_policy.DebtPolicyState().as_state()
    return core.initialize_debt_questionnaire(state)


def prepare(state, message, *, mode="legacy", enabled=True):
    return core.prepare_turn(state, message, settings(mode, enabled=enabled), submit_delivery=False)


@pytest.mark.parametrize("mode", ["legacy", "semantic"])
@pytest.mark.parametrize("message", [
    "Не хочу оставлять телефон.", "Номер не дам.", "Не буду указывать свой номер.",
    "Нет, спасибо.", "Не звоните мне.",
])
def test_phone_refusal_closes_contact_task_without_cancelling_debt(mode, message):
    state = debt_state()
    assert state["script"]["asked"] == ["client.phone"]
    prepared = prepare(state, message, mode=mode)
    policy = debt_policy.parse_policy(prepared)
    assert policy is not None and policy.mode == "debt"
    assert policy.phone_collection == "declined"
    assert prepared["dialog_phase"] == "contact_declined_held"
    assert prepared["plan"]["fields"] == []
    assert "phone" not in prepared["facts"]["client"]
    assert prepared.get("delivery") is None
    committed = core.commit_turn(prepared, message, debt_policy.render_debt_reply(
        policy, prepared["_plan_object"], prepared["facts"]))["current_state"]
    assert committed["script"]["asked"] == []
    assert debt_policy.parse_policy(committed).phone_collection == "declined"


@pytest.mark.parametrize("message", [
    "А какие документы нужны?", "Спасибо.", "А потом что?", "Нет, спасибо.",
    "Можно без банкротства обойтись?",
])
def test_refusal_survives_restore_faq_and_disabled_new_activation(message):
    refused = prepare(debt_state(), "Телефон не дам.")
    restored = json.loads(json.dumps({key: value for key, value in refused.items() if not key.startswith("_")}))
    prepared = prepare(restored, message, enabled=False)
    assert debt_policy.parse_policy(prepared).phone_collection == "declined"
    assert prepared["dialog_phase"] == "contact_declined_held"
    assert prepared["plan"]["fields"] == []
    assert prepared["script"]["asked"] == []


def test_a_bare_refusal_uses_the_actual_phone_question_when_the_old_cursor_is_stale():
    state = debt_state()
    state["script"]["asked"] = ["client.name"]
    state["chat_history"] = [{"role": "assistant", "content": "Можете оставить номер, чтобы с вами связались?"}]
    prepared = prepare(state, "Нет, спасибо.")
    assert debt_policy.parse_policy(prepared).phone_collection == "declined"
    assert prepared["plan"]["fields"] == []
    assert prepared["dialog_phase"] == "contact_declined_held"


@pytest.mark.parametrize("mode", ["legacy", "semantic"])
@pytest.mark.parametrize("message", [
    "Телефон не оставлю. Можно без него?",
    "Не хочу оставлять номер. Какие нужны документы?",
    "Не звоните мне! Можно продолжить здесь?",
    "Нет, спасибо. Какие нужны документы?",
])
def test_a_declared_contact_refusal_is_not_lost_when_the_same_turn_contains_a_question(mode, message):
    prepared = prepare(debt_state(), message, mode=mode)
    policy = debt_policy.parse_policy(prepared)
    assert policy is not None and policy.phone_collection == "declined" and policy.mode == "debt"
    assert prepared["plan"]["fields"] == []
    assert prepared["dialog_phase"] == "contact_declined_held"
    assert "phone" not in prepared["facts"]["client"]


@pytest.mark.parametrize("mode", ["legacy", "semantic"])
@pytest.mark.parametrize("statement", [
    "Номер оставлять не хочу.",
    "Телефон указывать не буду.",
    "Контакт сообщать не хочу.",
    "Свой номер оставлять пока не готов.",
])
@pytest.mark.parametrize("followup", ["", " Можно без него?"])
def test_first_person_contact_refusal_supports_object_action_and_negation_word_order(mode, statement, followup):
    prepared = prepare(debt_state(), statement + followup, mode=mode)
    policy = debt_policy.parse_policy(prepared)
    assert policy is not None and policy.phone_collection == "declined" and policy.mode == "debt"
    assert prepared["plan"]["fields"] == []
    assert prepared["dialog_phase"] == "contact_declined_held"
    assert "phone" not in prepared["facts"]["client"]


@pytest.mark.parametrize("mode", ["legacy", "semantic"])
@pytest.mark.parametrize("message", [
    "Не хочу оставлять.", "Не хочу оставлять. Можно без него?",
    "Не оставляю номер.", "Не оставляю номер. Можно без него?",
    "Не даю телефон.", "Не указываю номер.", "Не сообщаю контакт.", "Номер не оставляю.",
])
def test_contact_refusal_supports_contextual_ellipsis_and_explicit_present_tense(mode, message):
    prepared = prepare(debt_state(), message, mode=mode)
    policy = debt_policy.parse_policy(prepared)
    assert policy is not None and policy.phone_collection == "declined" and policy.mode == "debt"
    assert prepared["plan"]["fields"] == []
    assert prepared["dialog_phase"] == "contact_declined_held"
    assert "phone" not in prepared["facts"]["client"]


@pytest.mark.parametrize("asked", ["client.name", "debts.total", "client.region_class", "request.amount"])
def test_an_elliptical_collection_refusal_does_not_bind_to_another_pending_field(asked):
    state = debt_state()
    state["script"]["asked"] = [asked]
    # Keep the actual spoken question consistent with the non-contact cursor.
    questions = {
        "client.name": "Как к вам обращаться?", "debts.total": "Какова сумма долга?",
        "client.region_class": "В каком городе вы живёте?", "request.amount": "Какая сумма кредита вам нужна?",
    }
    state["chat_history"] = [{"role": "assistant", "content": questions[asked]}]
    prepared = prepare(state, "Не хочу оставлять.")
    assert debt_policy.parse_policy(prepared).phone_collection == "open"


def test_a_later_locally_parsed_phone_completes_contact_without_creating_consent():
    refused = prepare(debt_state(), "Телефон не дам.")
    prepared = prepare(refused, "Мой номер +7 000 000-00-01.")
    assert prepared["facts"]["client"]["phone"] == "+70000000001"
    assert debt_policy.parse_policy(prepared).phone_collection == "open"
    assert prepared["dialog_phase"] == "collected_held"
    assert prepared["plan"]["fields"] == []
    assert not prepared.get("site_consent")
    assert prepared.get("delivery") is None


def test_refusal_does_not_delete_a_previously_known_phone():
    prepared = prepare(debt_state(phone="+70000000001"), "Не звоните мне.")
    assert prepared["facts"]["client"]["phone"] == "+70000000001"
    assert debt_policy.parse_policy(prepared).phone_collection == "declined"
    assert prepared["dialog_phase"] == "contact_declined_held"


def test_phone_refusal_during_context_collection_does_not_restart_contact_later():
    state = debt_state(name=None, amount=None)
    refused = prepare(state, "Хочу списать долги, но телефон не дам.")
    assert refused["plan"]["fields"] == ["debts.total"]
    answered = prepare(refused, "Долг 400 тысяч рублей.")
    assert answered["plan"]["fields"] == []
    assert answered["dialog_phase"] == "contact_declined_held"
    assert answered["facts"]["client"]["name"] is None


@pytest.mark.parametrize("message", [
    "А телефон обязателен?", "Если не дам телефон, что будет?", "Нет просрочек.",
    "А если не хочу оставлять номер?",
    "Если не буду указывать свой номер. Что тогда?",
    "Почему нужен телефон? Не хочу попасть к мошенникам.",
    'Мне сказали «телефон не оставлю». Что это означает?',
    "Номер оставлять не хочу ли я.",
    "Не уверен, хочу ли я оставлять номер.",
    "А если номер оставлять не хочу.",
    "Муж сказал, номер оставлять не хочу.",
    'Он написал "номер оставлять не хочу". Можно без него?',
    "Допустим, телефон указывать не буду. Что тогда?",
    "Не хочу оставлять?",
    "А если не хочу оставлять?",
    '«Не хочу оставлять». Что это означает?',
    "Если не оставляю номер. Что будет?",
    "Муж сказал, не оставляю номер.",
])
def test_questions_and_unrelated_negative_answers_do_not_forge_a_refusal(message):
    prepared = prepare(debt_state(), message)
    assert debt_policy.parse_policy(prepared).phone_collection == "open"
    assert prepared["plan"]["fields"] == ["client.phone"]


def test_bare_no_to_amount_is_not_a_contact_refusal():
    prepared = prepare(debt_state(amount=None), "Нет.")
    assert debt_policy.parse_policy(prepared).phone_collection == "open"
    assert prepared["plan"]["fields"] == ["debts.total"]


def test_debt_goal_cancellation_remains_a_separate_state():
    refused = prepare(debt_state(), "Телефон не дам.")
    cancelled = prepare(refused, "Банкротство мне не нужно.")
    policy = debt_policy.parse_policy(cancelled)
    assert policy.mode == "cancelled" and policy.phone_collection == "declined"
    assert cancelled["dialog_phase"] == "cancelled_held"
    assert cancelled["plan"]["fields"] == []


def test_older_policy_records_default_to_open_without_weakening_restored_validation():
    record = debt_policy.DebtPolicyState().as_state()
    record.pop("phone_collection")
    assert debt_policy.parse_policy({debt_policy.POLICY_KEY: record}).phone_collection == "open"
    record["phone_collection"] = ["declined"]
    assert debt_policy.parse_policy({debt_policy.POLICY_KEY: record}).mode == "clarify"


@pytest.mark.parametrize("message", [
    "А можно примерно?", "Сколько примерно это стоит?", "А почему нужно знать долг?",
    "Если у меня долг 500 тысяч, что тогда?",
])
def test_amount_faq_never_replaces_a_grounded_exact_debt(message):
    state = debt_state()
    state["questionnaire_policy"]["amount_observation"] = {"kind": "exact", "amount": 400000, "lower": None, "upper": None}
    state["script"]["asked"] = ["debts.total"]
    prepared = prepare(state, message)
    assert prepared["facts"]["debts"]["total"] == 400000
    assert debt_policy.parse_policy(prepared).amount_observation.kind == "exact"
    assert prepared["plan"]["fields"] == ["client.phone"]
    assert prepared["script"]["asked"] == []


def test_an_explicit_amount_answer_clears_the_finished_cursor_before_a_later_faq():
    state = debt_state(amount=None)
    answered = prepare(state, "400 тысяч рублей.")
    assert answered["facts"]["debts"]["total"] == 400000
    assert answered["script"]["asked"] == []
    followup = prepare(answered, "А можно примерно?")
    assert followup["facts"]["debts"]["total"] == 400000
    assert followup["plan"]["fields"] == ["client.phone"]


def test_a_polite_terminal_aside_does_not_turn_the_actual_region_answer_into_uncertainty():
    state = debt_state(name=None)
    state["facts"]["client"].pop("region_class")
    state = core.initialize_debt_questionnaire(state)
    assert state["script"]["asked"] == ["client.region_class"]
    prepared = prepare(state, "Москва, если это важно.")
    assert prepared["facts"]["client"].get("region_class") == "moscow_mo"
    assert prepared["facts"]["client"]["region"] == "Москва"
    assert prepared["plan"]["fields"] == ["client.name"]


@pytest.mark.parametrize("message", ["Москва, если не ошибаюсь.", "Если бы я жил в Москве.", "Москва, если это важно?"])
def test_an_uncertain_or_questioned_place_is_not_confirmed_by_the_polite_aside_rule(message):
    state = debt_state(name=None)
    state["facts"]["client"].pop("region_class")
    state = core.initialize_debt_questionnaire(state)
    prepared = prepare(state, message)
    assert prepared["facts"]["client"].get("region_class") is None
    assert prepared["plan"]["fields"] == ["client.region_class"]


def test_the_polite_aside_does_not_authorize_an_unasked_place_correction():
    prepared = prepare(debt_state(), "Москва, если это важно.")
    assert prepared["facts"]["client"]["region_class"] == "rf_other"
    assert prepared["plan"]["fields"] == ["client.phone"]


@pytest.mark.parametrize("message", [
    "Москва, регион не знаю.", "Москва, регион не помню.",
    "Москва, НО регион не знаю.", "Москва, А регион не знаю.",
])
def test_an_unknown_secondary_region_does_not_retract_an_explicitly_named_city(message):
    state = debt_state(name=None)
    state["facts"]["client"].pop("region_class")
    state = core.initialize_debt_questionnaire(state)
    prepared = prepare(state, message)
    assert prepared["facts"]["client"].get("region_class") == "moscow_mo"
    assert prepared["facts"]["client"].get("region") == "Москва"
    assert prepared["plan"]["fields"] == ["client.name"]


def test_city_uncertainty_and_unasked_locations_are_not_promoted_by_secondary_region_ignorance():
    for message in ["Не уверен, Москва.", "Москва, не помню точно."]:
        state = debt_state(name=None)
        state["facts"]["client"].pop("region_class")
        state = core.initialize_debt_questionnaire(state)
        prepared = prepare(state, message)
        assert prepared["facts"]["client"].get("region_class") is None
        assert prepared["plan"]["fields"] == ["client.region_class"]
    unasked = prepare(debt_state(), "Москва, регион не знаю.")
    assert unasked["facts"]["client"]["region_class"] == "rf_other"


@pytest.mark.parametrize("mode", ["legacy", "semantic"])
@pytest.mark.parametrize("message", ["Да,400000", "Да, 400000.", "Да, 400 000.", "400000.", "400 000."])
def test_an_exact_scalar_confirmation_answers_the_actual_pending_debt_question(mode, message):
    state = debt_state(amount=None)
    state["questionnaire_policy"]["amount_observation"] = debt_policy.DebtObservation(kind="unknown").model_dump()
    prepared = prepare(state, message, mode=mode)
    observation = debt_policy.parse_policy(prepared).amount_observation
    assert observation is not None and observation.kind == "exact" and observation.amount == 400000
    assert prepared["facts"]["debts"]["total"] == 400000
    assert prepared["plan"]["fields"] == ["client.phone"]
    assert prepared["script"]["asked"] == []


@pytest.mark.parametrize("message", ["Да,400000", "Да, 400 000.", "400000."])
def test_an_unbound_scalar_confirmation_does_not_invent_a_debt_amount(message):
    assert debt_policy.detect_debt_observation(message, expected=False) is None
    state = debt_state(amount=None)
    state["script"]["asked"] = ["request.amount"]
    prepared = prepare(state, message)
    assert prepared["facts"]["debts"]["total"] is None
    assert prepared["plan"]["fields"] == ["debts.total"]


@pytest.mark.parametrize("message", [
    "Да, около 400000.", "Скорее 400000.", "Да, от 350000 до 450000.",
    "Да,400000?", "Да,400.", "Да,400000 в месяц.", "Да, зарплата 400000.",
    "Да, доход 400000.", "Да,400000 долларов.", "Да, 400000 и ещё 50000.",
])
def test_scalar_confirmation_syntax_does_not_promote_approximate_or_unrelated_numbers(message):
    observation = debt_policy.detect_debt_observation(message, expected=True)
    assert observation is None or observation.kind != "exact"


def test_approximate_debt_keeps_uncertainty_and_cannot_become_exact_from_a_faq():
    answered = prepare(debt_state(amount=None), "Примерно 400 тысяч рублей.")
    assert debt_policy.parse_policy(answered).amount_observation.kind == "unknown"
    followup = prepare(answered, "А какие нужны документы?")
    assert followup["plan"]["fields"] == ["debts.total"]
    assert followup["facts"]["debts"]["total"] is None


def test_an_explicit_correction_can_include_a_question_after_the_reported_amount():
    prepared = prepare(debt_state(), "У меня долг 500 тысяч рублей, это подходит?")
    assert prepared["facts"]["debts"]["total"] == 500000
    assert debt_policy.parse_policy(prepared).amount_observation.kind == "exact"


def test_normal_chat_enters_debt_without_losing_a_contact_choice_or_existing_history():
    state = init_state()
    state["facts"] = {"client": {"name": "Ирина", "region_class": "rf_other"}, "debts": {"total": 400000}}
    state["chat_history"] = [{"role": "user", "content": "Нужен кредит."}, {"role": "assistant", "content": "Какую сумму рассматриваете?"}]
    previous_history = deepcopy(state["chat_history"])
    prepared = prepare(state, "Хочу списать свои долги, но телефон не дам.")
    assert debt_policy.parse_policy(prepared).phone_collection == "declined"
    assert prepared["dialog_phase"] == "contact_declined_held"
    assert prepared["chat_history"] == previous_history
    assert prepared["facts"]["client"]["name"] == "Ирина"


@pytest.mark.parametrize("mode", ["legacy", "semantic"])
@pytest.mark.parametrize("message", ["Поняла. Я в Казани.", "Я живу в Казани."])
def test_the_recorded_first_person_oblique_city_advances_the_real_debt_turn(mode, message):
    state = debt_state(name=None)
    state["facts"]["client"].pop("region_class")
    state = core.initialize_debt_questionnaire(state)
    prepared = prepare(state, message, mode=mode)
    assert prepared["facts"]["client"].get("region_class") == "rf_other"
    assert prepared["facts"]["client"].get("region") == "Казань"
    assert "client.region_class" in prepared["_policy_received_fields"]
    assert prepared["plan"]["fields"] == ["client.name"]
    committed = core.commit_turn(prepared, message, "Как к вам обращаться?")["current_state"]
    assert committed["facts"]["client"]["region"] == "Казань"
    assert committed["script"]["asked"] == ["client.name"]
    assert committed.get("delivery") is None


@pytest.mark.parametrize("message", [
    "Я в Казани?", "Если бы я был в Казани.", "Мой брат в Казани.",
    "Моя квартира в Казани.", "Я не в Казани.", "Я в неизвестном городе.",
    "Я в Казани или Москве.", "Я в Казани, но живу в Москве.",
])
def test_elliptical_location_does_not_authorize_other_contexts_or_conflicting_places(message):
    state = debt_state(name=None)
    state["facts"]["client"].pop("region_class")
    state = core.initialize_debt_questionnaire(state)
    prepared = prepare(state, message)
    assert prepared["facts"]["client"].get("region_class") is None
    assert prepared["plan"]["fields"] == ["client.region_class"]


def test_a_grounded_volunteered_city_preserves_contacts_and_is_not_asked_again():
    state = debt_state(phone="+70000000401")
    previous_contacts = deepcopy(state["facts"]["client"])
    prepared = prepare(state, "Поняла. Я в Казани.")
    assert prepared["facts"]["client"].get("region") == "Казань"
    assert prepared["facts"]["client"]["phone"] == previous_contacts["phone"]
    assert prepared["facts"]["client"]["name"] == previous_contacts["name"]
    assert prepared["plan"]["fields"] == []
    committed = core.commit_turn(prepared, "Поняла. Я в Казани.", "Можно обсудить ваши вопросы здесь.")["current_state"]
    following = prepare(committed, "Да, Татарстан.")
    assert following["facts"]["client"]["region"] == "Казань"
    assert following["plan"]["fields"] == []


@pytest.mark.parametrize("message", ["Я в Казани или Москве.", "Я в неизвестном городе."])
def test_an_unresolved_elliptical_place_does_not_overwrite_the_known_region(message):
    state = debt_state(phone="+70000000401")
    state["facts"]["client"]["region_class"] = "moscow_mo"
    previous = deepcopy(state["facts"]["client"])
    prepared = prepare(state, message)
    assert prepared["facts"]["client"] == previous
    assert prepared["plan"]["fields"] == []


@pytest.mark.parametrize("mode", ["legacy", "semantic"])
def test_the_recorded_city_assertion_survives_the_separate_restructuring_question(mode):
    state = debt_state(phone="+70000000401")
    state["facts"]["client"].pop("region_class")
    state = core.initialize_debt_questionnaire(state)
    message = "Казань. Что значит реструктуризация?"
    prepared = prepare(state, message, mode=mode)
    assert prepared["facts"]["client"].get("region") == "Казань"
    assert prepared["facts"]["client"].get("region_class") == "rf_other"
    assert "client.region_class" in prepared["_policy_received_fields"]
    assert prepared["script"]["asked"] == []
    assert prepared["plan"]["fields"] == []
    committed = core.commit_turn(prepared, message, "Реструктуризация меняет условия выплаты долга.")["current_state"]
    next_turn = prepare(committed, "Да, верно. Спасибо.", mode=mode)
    assert next_turn["facts"]["client"]["region"] == "Казань"
    assert next_turn["plan"]["fields"] == []
    assert next_turn["facts"]["client"]["phone"] == "+70000000401"


@pytest.mark.parametrize("message", [
    "Казань? Что значит реструктуризация?", "Не Казань. Что значит реструктуризация?",
    "Если бы Казань. Что значит реструктуризация?", "Брат в Казани. Что значит реструктуризация?",
    "Квартира в Казани. Что значит реструктуризация?", "Казань или Москва. Что значит реструктуризация?",
    "Казань. Или всё же Москва?", "Казань. Но я не уверен?",
])
def test_the_question_clause_rule_preserves_place_uncertainty_and_other_contexts(message):
    state = debt_state(name=None)
    state["facts"]["client"].pop("region_class")
    state = core.initialize_debt_questionnaire(state)
    prepared = prepare(state, message)
    assert prepared["facts"]["client"].get("region_class") is None
    assert prepared["plan"]["fields"] == ["client.region_class"]


@pytest.mark.parametrize("mode", ["legacy", "semantic"])
@pytest.mark.parametrize("message", ["Ближе к 400 тысячам.", "Ближе к 280 тысячам."])
def test_the_recorded_comparative_answer_keeps_unknown_provenance_through_commit(mode, message):
    state = debt_state(phone="+70000000401", amount=None)
    prepared = prepare(state, message, mode=mode)
    observation = debt_policy.parse_policy(prepared).amount_observation
    assert observation is not None and observation.kind == "unknown"
    assert prepared["facts"]["debts"]["total"] is None
    assert prepared["plan"]["fields"] == ["debts.total"]
    committed = core.commit_turn(prepared, message, "Назовите точную сумму остатка в рублях.")["current_state"]
    assert committed["facts"]["debts"]["total"] is None
    assert committed["script"]["asked"] == ["debts.total"]
    assert debt_policy.parse_policy(committed).amount_observation.kind == "unknown"
    exact = prepare(committed, "400000 рублей.", mode=mode)
    assert exact["facts"]["debts"]["total"] == 400000
    assert debt_policy.parse_policy(exact).amount_observation.kind == "exact"


@pytest.mark.parametrize("mode", ["legacy", "semantic"])
def test_the_recorded_punctuated_yes_leaves_clarification_in_the_same_restored_chat(mode):
    state = init_state()
    state["session_id"] = "synthetic-intent-punctuation"
    state["facts"] = {"client": {"name": "Ирина", "phone": "+70000000401", "region_class": "rf_other"}}
    state["chat_history"] = [{"role": "user", "content": "Хочу кредит."},
                             {"role": "assistant", "content": "Какую сумму рассматриваете?"}]
    original_contacts = deepcopy(state["facts"]["client"])
    ambiguous = prepare(state, "Здравствуйте. Мне бы с долгами помочь.", mode=mode)
    assert debt_policy.parse_policy(ambiguous).mode == "clarify"
    question = debt_policy.render_debt_reply(debt_policy.parse_policy(ambiguous),
                                             ambiguous["_plan_object"], ambiguous["facts"])
    committed = core.commit_turn(ambiguous, "Здравствуйте. Мне бы с долгами помочь.", question)["current_state"]
    restored = json.loads(json.dumps(committed))
    previous_history = deepcopy(restored["chat_history"])
    answered = prepare(restored, "Да, хочу.", mode=mode, enabled=False)
    assert debt_policy.parse_policy(answered).mode == "debt"
    assert answered["plan"]["fields"] == ["debts.total"]
    assert answered["session_id"] == state["session_id"]
    assert answered["facts"]["client"] == original_contacts
    assert answered["chat_history"] == previous_history
    assert answered.get("delivery") is None


@pytest.mark.parametrize("mode", ["legacy", "semantic"])
def test_the_recorded_where_approximation_keeps_unknown_provenance_and_metadata_after_restore(mode):
    state = debt_state(phone="+70000000401", amount=None)
    original_client = deepcopy(state["facts"]["client"])
    message = "Где-то 280 тысяч рублей."
    prepared = prepare(state, message, mode=mode)
    observation = debt_policy.parse_policy(prepared).amount_observation
    assert observation is not None and observation.kind == "unknown" and observation.amount is None
    assert prepared["facts"]["debts"]["total"] is None
    assert prepared["fact_statuses"]["debts.total"] == "needs_confirmation"
    assert "debts.total" not in prepared["_policy_received_fields"]
    assert prepared["plan"]["fields"] == ["debts.total"]
    committed = core.commit_turn(prepared, message, "Назовите точную сумму остатка в рублях.")["current_state"]
    restored = json.loads(json.dumps(committed))
    assert debt_policy.parse_policy(restored).amount_observation.kind == "unknown"
    assert restored["facts"]["debts"]["total"] is None
    exact = prepare(restored, "280000", mode=mode)
    assert exact["facts"]["debts"]["total"] == 280000
    assert debt_policy.parse_policy(exact).amount_observation.kind == "exact"
    assert exact["facts"]["client"] == original_client
    assert exact["plan"]["fields"] == [] and exact.get("delivery") is None
