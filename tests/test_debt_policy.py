"""Pure local debt policy: no legal eligibility, production CRM or network calls."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest
from pydantic import SecretStr, ValidationError

import debt_policy as debt
from customer_context import (ApplicationReference, ContextCapabilityCodec, CurrentApplicationFields,
    CurrentApplicationSnapshot, CurrentContextService, ServerApplicationAdapter)
from routing import get_matrix
from routing.derive import derive_facts


@pytest.mark.parametrize("message", ["Хочу списать долги", "Хочу списать свои долги.", "Помогите списать мои долги.",
    "Мне нужна реструктуризация", "Готов оформить банкротство", "Мне нужна помощь с банкротством",
    "Хочу списать долги по кредитам", "Хочу списать задолженность", "Мне нужно списание задолженности"])
def test_explicit_self_request_activates_only_when_enabled(message):
    assert debt.select_policy({}, message) is None
    selected = debt.select_policy({}, message, enabled=True)
    assert selected is not None and selected.mode == "debt" and selected.reason == "explicit_request"


@pytest.mark.parametrize("message", ["Хочу кредит, у меня долг400тысяч", "Банкротство не хочу, нужен кредит",
    "Нужен кредит без банкротства", "Хочу получить новый кредит", "Добрый день", "Москва"])
def test_regular_credit_or_no_request_does_not_switch(message):
    assert debt.select_policy({}, message, enabled=True) is None


@pytest.mark.parametrize("message", ["Что такое банкротство?", "Он написал: хочу списать долги",
    "Если я хочу банкротство, что это значит?", "Не уверен, хочу ли я банкротство",
    "У моего друга долги, он хочет банкротство", 'Она сказала "хочу списать долги"',
    "Хочу кредит и хочу списать долги", "У меня задолженность", "банкротство",
    "Хочу списать долги отца", "Хочу банкротство моего брата", "'хочу списать долги' — пример фразы"])
def test_ambiguous_or_other_subject_only_requests_confirmation(message):
    selected = debt.select_policy({}, message, enabled=True)
    assert selected is not None and selected.mode == "clarify"
    assert debt.plan_debt(get_matrix(), {}, selected).field_paths == []


def test_persisted_flow_is_sticky_off_and_refusal_does_not_release_or_route_credit():
    selected = debt.select_policy({}, "Что такое банкротство?", enabled=True)
    assert selected is not None
    state = {debt.POLICY_KEY: selected.as_state()}
    confirmed = debt.select_policy(state, "да", enabled=False)
    assert confirmed is not None and confirmed.mode == "debt"
    cancelled = debt.select_policy({debt.POLICY_KEY: confirmed.as_state()}, "Банкротство мне не нужно", enabled=False)
    assert cancelled is not None and cancelled.mode == "cancelled"
    assert debt.debt_delivery_held({debt.POLICY_KEY: cancelled.as_state()})
    assert debt.select_policy({debt.POLICY_KEY: confirmed.as_state()}, "хочу кредит", enabled=False) == confirmed


@pytest.mark.parametrize("answer,expected_mode", [
    ("да", "debt"), ("да, по моим долгам", "debt"), ("да хочу", "debt"), ("да, хочу", "debt"),
    ("нет", "cancelled"), ("нет спасибо", "cancelled"), ("нет, спасибо", "cancelled"),
])
@pytest.mark.parametrize("ending", ["", ".", "!", "…"])
def test_existing_closed_intent_answers_keep_their_meaning_with_declarative_punctuation(answer, expected_mode, ending):
    policy = debt.DebtPolicyState(mode="clarify", reason="ambiguous_request", phone_collection="declined")
    result = debt.select_policy({debt.POLICY_KEY: policy.as_state()}, answer + ending, enabled=False)
    assert result is not None and result.mode == expected_mode
    assert result.phone_collection == "declined"


@pytest.mark.parametrize("answer", [
    "Да, хочу?", "Нет, спасибо?", '"Да, хочу."', "«Да, хочу.»", "Если да, хочу.",
])
def test_question_quote_or_hypothesis_is_not_a_declarative_closed_intent_answer(answer):
    policy = debt.DebtPolicyState(mode="clarify", reason="ambiguous_request")
    result = debt.select_policy({debt.POLICY_KEY: policy.as_state()}, answer, enabled=True)
    assert result is not None and result.mode == "clarify"


@pytest.mark.parametrize("raw", [None, {}, {"version": 99}, {"mode": "consumer"}, "debt", False])
def test_malformed_policy_presence_is_held_and_paused(raw):
    state = {debt.POLICY_KEY: raw}
    selected = debt.parse_policy(state)
    assert selected is not None and selected.mode == "clarify"
    assert debt.debt_delivery_held(state)


def test_raw_goal_debt_amount_url_and_unsealed_signal_do_not_authorize_entry():
    for state in ({"facts": {"client": {"goal": "debt_relief"}}}, {"facts": {"debts": {"total": 500000}}},
                  {"utm": {"landing": "https://example.test/bankruptcy"}}):
        assert debt.select_policy(state, "", enabled=True) is None
    assert debt.select_policy({}, "", enabled=True, trusted_signal=debt.TrustedDebtSignal(object(), "bad")) is None


def test_trusted_signal_is_live_and_bound_to_the_ready_current_application():
    now = 2000000000.0
    reference = ApplicationReference(application_id="synthetic-1", subject_id="subject-1", revision="v1")
    source = ServerApplicationAdapter(lambda _: CurrentApplicationSnapshot(reference=reference,
        phone=SecretStr("89990000001"), fields=CurrentApplicationFields(goal="debt_relief"),
        observed_at=datetime.fromtimestamp(now, timezone.utc)))
    codec = ContextCapabilityCodec(b"synthetic-local-context-key-32-bytes", clock=lambda: now)
    service = CurrentContextService(codec, source, enabled=True, clock=lambda: now)
    authorized = service.resolve(codec.issue(reference, session_id="s1"), session_id="s1")
    signal = debt.trusted_signal_from_authorized(authorized, now)
    assert signal is not None
    assert debt.select_policy({}, "", enabled=True, trusted_signal=signal) is None
    state = {"customer_context": authorized.safe_context_state()}
    selected = debt.select_policy(state, "", enabled=True, trusted_signal=signal)
    assert selected is not None and selected.reason == "trusted_context"
    other = {"customer_context": {"context_status": "ready", "binding_digest": "different"}}
    assert debt.select_policy(other, "", enabled=True, trusted_signal=signal) is None
    conflicted = debt.select_policy(state, "Нужен кредит, банкротство не нужно", enabled=True, trusted_signal=signal)
    assert conflicted is not None and conflicted.mode == "clarify"
    with pytest.raises(Exception, match="expired"):
        debt.trusted_signal_from_authorized(authorized, now + 300)


@pytest.mark.parametrize("amount,band", [(0,"below_190k"), (189999,"below_190k"), (190000,"190k_to_299999"),
    (299999,"190k_to_299999"), (300000,"300k_plus"), (400000,"300k_plus"),
    (None,"unknown"), (False,"unknown"), (-1,"unknown"), (float("nan"),"unknown"),
    (float("inf"),"unknown"), (10**400,"unknown"), ("400000","unknown"), ({"min":190000,"max":300000},"unknown")])
def test_local_bands_preserve_numeric_boundaries_without_coercion(amount, band):
    assert debt.debt_band(amount) == band


@pytest.mark.parametrize("message,expected,kind,amount,lower,upper", [
    ("Общий долг400тысяч",False,"exact",400000,None,None),
    ("250000 рублей",True,"exact",250000,None,None),
    ("190000",True,"exact",190000,None,None),
    ("300",True,"unknown",None,None,None),
    ("1,5 млн",True,"exact",1500000,None,None),
    ("189999,9 рублей",True,"unknown",None,None,None),
    ("Долг от190 до300тысяч",False,"range",None,190000,300000),
    ("200-250тыс",True,"range",None,200000,250000),
    ("от300тысяч",True,"range",None,300000,None),
    ("до190тыс",True,"range",None,None,190000),
    ("около400тысяч",True,"unknown",None,None,None),
    ("не знаю",True,"unknown",None,None,None),
    ("долгов нет",True,"exact",0,None,None),
    ("У меня есть долг, зарплата400тысяч",True,"unknown",None,None,None),
    ("Долги есть, хочу кредит300тысяч",True,"unknown",None,None,None),
    ("Долг400тысяч долларов",True,"unknown",None,None,None),
    ("Долг300тыс USD",True,"unknown",None,None,None),
    ("400тысяч €",True,"unknown",None,None,None),
    ("400тыс $",True,"unknown",None,None,None),
])
def test_grounded_observations_keep_ranges_unknown_and_other_amounts_distinct(message,expected,kind,amount,lower,upper):
    value = debt.detect_debt_observation(message, expected=expected)
    assert value is not None
    assert (value.kind,value.amount,value.lower,value.upper) == (kind,amount,lower,upper)


@pytest.mark.parametrize("message", ["Нет действующих кредитов", "Мне35лет", "Телефон89990000001", "зарплата400тыс"])
def test_no_first_number_or_no_active_loan_zero_inference(message):
    assert debt.detect_debt_observation(message, expected=True) is None


@pytest.mark.parametrize("message", [
    "Ближе к 400 тысячам.", "Ближе к 280 тысячам.", "Скорее к 300.",
    "Скорее 400000 рублей.", "400 тысяч или 300 тысяч.", "400000 либо 300000.",
    "Пожалуй, 400000 рублей.",
])
def test_comparative_or_alternative_answers_do_not_create_exact_debt(message):
    observed = debt.detect_debt_observation(message, expected=True)
    assert observed is not None and observed.kind == "unknown"
    assert observed.amount is None and observed.lower is None and observed.upper is None


@pytest.mark.parametrize("message", [
    "Вы предложили 400 тысяч рублей.", "Вы спрашивали про 280 тысяч.",
    "В примере было 300 тысяч рублей.",
])
def test_a_reference_to_a_proposed_number_is_not_the_current_debt(message):
    assert debt.detect_debt_observation(message, expected=True) is None


@pytest.mark.parametrize("message", [
    "Пожалуйста, 400000 рублей.", "Мой долг 400000 рублей, можно его урегулировать или нет?",
])
def test_politeness_and_an_unrelated_alternative_question_do_not_make_a_reported_amount_vague(message):
    observation = debt.detect_debt_observation(message, expected=True)
    assert observation is not None and observation.kind == "exact" and observation.amount == 400000


@pytest.mark.parametrize("message", [
    "Где-то 280 тысяч рублей.", "Где-то280тысячрублей.",
    "Где‐то 280 тысяч рублей.", "Где‑то 280 тысяч рублей.",
])
def test_the_recorded_where_approximation_and_standard_hyphens_do_not_create_an_exact_amount(message):
    observation = debt.detect_debt_observation(message, expected=True)
    assert observation is not None and observation.kind == "unknown"
    assert observation.amount is None and observation.lower is None and observation.upper is None


@pytest.mark.parametrize("message", [
    "280000 рублей.", "Не где-то, а ровно 280000 рублей.",
    "Где-то ошибка. Мой долг 280000 рублей.",
])
def test_only_a_where_marker_qualifying_the_amount_changes_its_precision(message):
    observation = debt.detect_debt_observation(message, expected=True)
    assert observation is not None and observation.kind == "exact" and observation.amount == 280000


def test_debt_derivation_and_model_reconciliation_do_not_invent_zero_or_history():
    original = {"debts": {"has_active_credits": False}, "assets": {"has_vehicle": None}}
    assert derive_facts(original)["debts"]["total"] == 0  # Legacy behavior remains separate.
    assert debt.derive_debt_facts(original) == original
    poisoned = {"debts": {"has_active_credits": False, "total": 0, "credit_history": "no_overdue"}}
    assert "total" not in debt.preserve_debt_total(original, poisoned)["debts"]
    assert "credit_history" not in debt.preserve_debt_total(original, poisoned)["debts"]
    known = {"debts": {"total": 400000}}
    assert debt.preserve_debt_total(known, poisoned)["debts"]["total"] == 400000
    corrected = debt.preserve_debt_total(known, {"debts":{"total":250000}}, explicit_debt_update=True)
    assert corrected["debts"]["total"] == 250000
    assert known == {"debts":{"total":400000}}


def test_planner_debt_before_region_before_contacts_and_no_legacy_match():
    matrix, policy = get_matrix(), debt.DebtPolicyState()
    facts = {"debts":{"has_active_credits":False}}
    snapshot = deepcopy(facts)
    plan = debt.plan_debt(matrix, facts, policy)
    assert plan.field_paths == ["debts.total"] and plan.statuses == [] and plan.matched is None
    assert {"request.amount","client.age","assets.has_vehicle"} <= set(plan.forbidden_paths)
    facts["debts"]["total"] = 250000
    assert debt.plan_debt(matrix, facts, policy).field_paths == ["client.region_class"]
    facts["client"] = {"region_class":"moscow_mo"}
    assert debt.plan_debt(matrix, facts, policy).field_paths == ["client.name"]
    facts["client"]["name"] = "Синтетический"
    assert debt.plan_debt(matrix, facts, policy).field_paths == ["client.phone"]
    assert debt.plan_debt(matrix, facts, policy, contact_known=True).field_paths == []
    assert snapshot == {"debts":{"has_active_credits":False}}
    assert "age" not in facts["client"] and "request" not in facts and "assets" not in facts


def test_same_band_interval_always_requests_exact_and_later_exact_can_clear_it():
    facts = {"debts":{"total":250000}, "client":{"region_class":"moscow_mo","name":"Тест","phone":"known"}}
    interval = debt.DebtObservation(kind="range", lower=200000, upper=250000)
    policy = debt.DebtPolicyState().with_observation(interval)
    assert debt.debt_band(interval) == "190k_to_299999"
    plan = debt.plan_debt(get_matrix(), facts, policy)
    assert plan.field_paths == ["debts.total"] and plan.fields[0].needs_confirmation
    updated = policy.with_observation(debt.DebtObservation(kind="exact",amount=250000))
    assert debt.plan_debt(get_matrix(), facts, updated).field_paths == []
    assert policy.amount_observation == interval


def test_contact_attempt_limit_is_truthful_incomplete_held_not_collected():
    facts = {"debts":{"total":400000}, "client":{"region_class":"rf_other"}}
    policy = debt.DebtPolicyState()
    plan = debt.plan_debt(get_matrix(), facts, policy, attempts={"client.name":2,"client.phone":3})
    assert plan.field_paths == [] and debt.policy_phase(policy, plan, facts) == "incomplete_held"
    text = debt.render_debt_reply(policy,plan,facts)
    assert "не хватает данных" in text and "Передача заявки по долгам" in text and "не подключена" in text
    assert "Сведения собраны" not in text


@pytest.mark.parametrize("facts,observation,attempts", [
    ({}, None, {"debts.total":3}),
    ({"debts":{"total":400000}}, debt.DebtObservation(kind="range",lower=200000,upper=400000), {"debts.total":3}),
    ({"debts":{"total":400000}}, None, {"client.region_class":3}),
])
def test_unknown_amount_interval_or_region_has_bounded_questions(facts,observation,attempts):
    policy = debt.DebtPolicyState(amount_observation=observation)
    plan = debt.plan_debt(get_matrix(),facts,policy,attempts=attempts)
    assert plan.fields == [] and plan.matched is None and plan.statuses == []
    assert debt.policy_phase(policy,plan,facts) == "incomplete_held"
    reply = debt.render_debt_reply(policy,plan,facts)
    assert "не хватает данных" in reply and "Передача заявки по долгам" in reply and "не подключена" in reply


@pytest.mark.parametrize("status", ["conflict", "needs_confirmation", {"unrecognized":"value"}, None])
def test_known_region_with_uncertain_or_malformed_status_requires_confirmation(status):
    facts = {"debts":{"total":400000},"client":{"region_class":"moscow_mo"}}
    plan = debt.plan_debt(get_matrix(),facts,debt.DebtPolicyState(),fact_statuses={"client.region_class":status})
    assert plan.field_paths == ["client.region_class"] and plan.fields[0].needs_confirmation


def test_rejecting_one_debt_procedure_and_requesting_another_is_clarified_not_credit():
    message = "Не хочу банкротство, нужна реструктуризация"
    selected = debt.select_policy({},message,enabled=True)
    assert selected is not None and selected.mode == "clarify"
    existing = debt.DebtPolicyState()
    result = debt.select_policy({debt.POLICY_KEY:existing.as_state()},message,enabled=False)
    assert result is not None and result.mode == "clarify"


def test_under_local_floor_does_not_collect_contact_offer_credit_or_claim_legal_rejection():
    policy, facts = debt.DebtPolicyState(), {"debts":{"total":189999}}
    plan = debt.plan_debt(get_matrix(),facts,policy)
    assert plan.field_paths == [] and plan.matched is None and plan.statuses == []
    text = debt.render_debt_reply(policy,plan,facts)
    assert "специалистом отдельно" in text and "Передача заявки по долгам" in text and "не подключена" in text
    assert "кредит" not in text and "http" not in text


@pytest.mark.parametrize("values", [{"kind":"exact","amount":True}, {"kind":"range","lower":300000,"upper":190000},
    {"kind":"unknown","amount":0}, {"kind":"exact","amount":10**400}])
def test_observation_schema_rejects_bool_invalid_range_and_unsafe_amount(values):
    with pytest.raises(ValidationError):
        debt.DebtObservation.model_validate(values)
