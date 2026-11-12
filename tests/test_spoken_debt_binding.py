"""Observed V5 speech acts keep their meaning and bind to the real debt cursor."""
from copy import deepcopy

import pytest

import core
import debt_policy
import llm_agent
import policy_voice
from config import AppConfig
from dialogue_frame import DialogueFrame, request_fields
from tests.test_policy_voice import debt_state


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(llm_agent, "extract_facts", lambda *args, **kwargs: {"fact_updates": [], "internal_summary": ""})
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *args, **kwargs: deepcopy(facts))
    monkeypatch.setattr(core, "_record_transcript", lambda *args, **kwargs: None)

    def forbidden(*args, **kwargs):
        pytest.fail("Spoken binding controls cannot call a model or deliver a lead")

    monkeypatch.setattr(llm_agent, "_post", forbidden)
    monkeypatch.setattr(core.delivery, "submit", forbidden)


def configuration():
    return AppConfig(openrouter_api_key="synthetic-unused", debt_policy_enabled=True,
                     policy_voice_enabled=True, fact_interpretation_mode="legacy")


@pytest.mark.parametrize("claim", ["Ваш долг списан.", "Ваши долги списаны.", "Долг будет списан."])
def test_actual_passive_discharge_claims_still_fail_the_shared_guard(claim):
    assert not policy_voice.validate_reply(claim, None, frame=DialogueFrame(None, "collected_held"))


def test_the_recorded_nominal_comparison_is_not_a_passive_discharge_claim():
    body = "Реструктуризация — это изменение условий долга, а списание — освобождение от него."
    assert policy_voice.validate_reply(body, None, frame=DialogueFrame(None, "collected_held", question_goal="debt_explanation"))


def test_actual_v5_hard_repair_keeps_the_entire_comparison_through_commit(monkeypatch):
    first = ("Реструктуризация — это изменение условий долга, а списание — полное освобождение от него.\n\n"
             "Алименты или возмещение вреда остаются.")
    final = ("Реструктуризация — это изменение условий долга, а списание — освобождение от него.\n\n"
             "Закон предусматривает судебное и внесудебное банкротство, при которых возможно освобождение "
             "от долгов, но не автоматически: например, алименты или возмещение вреда жизни и здоровью остаются. "
             "Также кредитор может простить долг по закону, но это его решение, а не гарантия.\n\n"
             "Что именно подойдёт в вашей ситуации — станет ясно только после анализа всех деталей.")
    candidates = iter([first, final])
    calls = []

    def generate(payload, config, **kwargs):
        calls.append(payload)
        return next(candidates)

    monkeypatch.setattr(llm_agent, "generate_reply", generate)
    state = debt_state(client={"name": "Ирина", "phone": "+70000000401", "region_class": "rf_other"},
                       debts={"total": 400000})
    state["session_id"] = "synthetic-v5-comparison"
    before = deepcopy(state["facts"])
    message = "Чем реструктуризация отличается от списания?"
    prepared = core.prepare_turn(state, message, configuration(), submit_delivery=False)
    speech = "".join(core.stream_reply(prepared, message, configuration()))
    committed = core.commit_turn(prepared, message, speech)["current_state"]
    assert speech == final and len(calls) == 2
    assert committed["facts"] == before and committed["session_id"] == state["session_id"]
    assert committed["plan"]["fields"] == [] and debt_policy.debt_delivery_held(committed)
    assert committed.get("delivery") is None


@pytest.mark.parametrize("question", [
    "Назовите, пожалуйста, остаток долга одним числом в рублях.",
    "Укажите остаток, который нужно погасить, одним числом.",
    "Сколько осталось погасить?",
])
def test_existing_debt_remainder_requests_bind_to_the_planned_amount(question):
    frame = DialogueFrame("debts.total", "collecting_debt_context", confirming=True)
    assert "debts.total" in request_fields(question, frame)


@pytest.mark.parametrize("question", [
    "Назовите сумму нового кредита, который хотите получить.",
    "Укажите размер будущей выплаты.",
])
def test_future_credit_or_payout_is_not_the_existing_debt_remainder(question):
    frame = DialogueFrame("debts.total", "collecting_debt_context", confirming=True)
    assert "debts.total" not in request_fields(question, frame)


def test_actual_v5_repaired_remainder_question_commits_a_cursor_for_the_next_numeric_answer(monkeypatch):
    first = "Давайте зафиксируем 280000 рублей?"
    final = "Назовите, пожалуйста, остаток долга одним числом в рублях."
    candidates = iter([first, final])
    calls = []

    def generate(payload, config, **kwargs):
        calls.append(payload)
        return next(candidates)

    monkeypatch.setattr(llm_agent, "generate_reply", generate)
    state = debt_state(client={"name": "Ирина", "phone": "+70000000401", "region_class": "rf_other"},
                       debts={"total": None})
    state["session_id"] = "synthetic-v5-remainder"
    message = "Около 280000 рублей."
    prepared = core.prepare_turn(state, message, configuration(), submit_delivery=False)
    speech = "".join(core.stream_reply(prepared, message, configuration()))
    assert speech == final and len(calls) == 2
    assert prepared["_policy_spoken"].asked_field == "debts.total"
    committed = core.commit_turn(prepared, message, speech)["current_state"]
    assert committed["script"]["asked"] == ["debts.total"]
    exact = core.prepare_turn(committed, "280000", configuration(), submit_delivery=False)
    assert exact["facts"]["debts"]["total"] == 280000
    assert debt_policy.parse_policy(exact).amount_observation.kind == "exact"
    assert exact["plan"]["fields"] == [] and exact["session_id"] == state["session_id"]
    assert exact.get("delivery") is None
