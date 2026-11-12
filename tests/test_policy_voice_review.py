"""Independent regressions for free speech, factual provenance and evaluation evidence."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json

import pytest

import core
from evaluation import local_style_eval as style_eval
import policy_voice
import policy_voice_critic
from config import AppConfig
from tests.test_policy_voice import debt_state, onloader_state, settings as original_settings

_ACTUAL_ASSESS = policy_voice_critic.assess
pytestmark = pytest.mark.usefixtures("real_policy_voice_critic")


def settings() -> AppConfig:
    return replace(original_settings(), extractor_model="synthetic-critic")


@pytest.fixture(autouse=True)
def isolated_semantic_diagnostic(monkeypatch):
    # These tests isolate provenance/composition. Wire cases restore the real adapter.
    monkeypatch.setattr(policy_voice_critic, "assess", lambda payload, draft, frame, config, **kwargs:
                        policy_voice_critic.CriticAssessment(
                            policy_voice_critic.SemanticVerdict(accept=True, flags=[]),
                            config.extractor_model, "accepted", True,
                        ))


@pytest.mark.parametrize("text", [
    "Я не могу назвать срок, но ваши долги спишут.",
    "Я не могу обещать звонок, но передам ваши данные сегодня.",
])
def test_an_unrelated_negative_clause_does_not_authorize_a_later_claim(text):
    assert not policy_voice.validate_reply(text, None)


@pytest.mark.parametrize("text", [
    "Нельзя гарантировать, что долги спишут.",
    "Я не могу обещать, что долги спишут.",
])
def test_a_qualified_denial_of_guarantees_remains_valid_speech(text):
    assert policy_voice.validate_reply(text, None)


@pytest.mark.parametrize("question", [
    "Какая сумма кредита вам нужна?",
    "На какую сумму кредита рассчитываете?",
    "Сколько хотите получить в кредит?",
    "Какой кредит вы хотите получить?",
])
def test_a_new_loan_question_is_not_observed_as_an_outstanding_debt_request(question):
    requested = policy_voice._requests(question)
    assert "debts.total" not in requested


@pytest.mark.parametrize("wrong_question", [
    "Какая сумма кредита вам нужна?",
    "На какую сумму кредита рассчитываете?",
    "Сколько хотите получить в кредит?",
    "Какой кредит вы хотите получить?",
])
def test_core_cannot_turn_the_answer_to_a_new_loan_question_into_existing_debt(monkeypatch, wrong_question):
    configuration = replace(settings(), debt_policy_enabled=True)
    monkeypatch.setattr(core.llm_agent, "generate_reply", lambda *args, **kwargs: wrong_question)
    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *args, **kwargs: {
        "fact_updates": [], "internal_summary": "",
    })
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *args, **kwargs: deepcopy(facts))
    monkeypatch.setattr(core, "_record_transcript", lambda *args, **kwargs: None)

    def forbidden_delivery(*args, **kwargs):
        pytest.fail("A speech regression attempted delivery")

    monkeypatch.setattr(core.delivery, "submit", forbidden_delivery)
    # Exercise the actual response, cursor commit and next short-answer interpretation.
    state = debt_state()
    reply = "".join(core.stream_reply(state, "Мне нужны деньги", configuration))
    committed = core.commit_turn(state, "Мне нужны деньги", reply)["current_state"]
    prepared = core.prepare_turn(committed, "500000", configuration, submit_delivery=False)
    debt = prepared.get("facts", {}).get("debts", {}).get("total")

    assert not (wrong_question in reply and debt == 500000), (
        "The answer to a desired-loan question became an existing debt balance"
    )
    # Either a safe fallback or an explicitly unbound question prevents false provenance.


@pytest.mark.parametrize("reply,question_was_spoken", [
    ("Сколько примерно осталось выплатить по кредитам и займам?", True),
    ("Хочу понять, сколько всего осталось выплатить.", False),
])
def test_current_debt_question_and_an_answer_only_explanation_keep_the_right_numeric_binding(
    monkeypatch, reply, question_was_spoken,
):
    configuration = replace(settings(), debt_policy_enabled=True)
    monkeypatch.setattr(core.llm_agent, "generate_reply", lambda *args, **kwargs: reply)
    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *args, **kwargs: {
        "fact_updates": [], "internal_summary": "",
    })
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *args, **kwargs: deepcopy(facts))
    monkeypatch.setattr(core, "_record_transcript", lambda *args, **kwargs: None)
    state = debt_state()
    previous_script = deepcopy(state["script"])

    # A real amount request binds the next answer. A pure explanation retains the old request.
    message = "Около 400 тысяч." if question_was_spoken else "А зачем нужна сумма?"
    speech = "".join(core.stream_reply(state, message, configuration))
    assert speech == reply
    observed = state["_policy_spoken"]
    assert observed.asked_field == ("debts.total" if question_was_spoken else None)
    committed = core.commit_turn(state, message, speech)["current_state"]
    if not question_was_spoken:
        assert committed["script"] == previous_script
    prepared = core.prepare_turn(committed, "500000", configuration, submit_delivery=False)
    assert prepared["facts"]["debts"]["total"] == 500000


@pytest.mark.parametrize("provider_failed", [False, True])
def test_style_report_preserves_actual_voice_source_before_commit_discards_metadata(monkeypatch, provider_failed):
    configuration = replace(settings(), debt_policy_enabled=True)
    monkeypatch.setattr(style_eval, "_client_reply", lambda *args, **kwargs: "А зачем вам сумма?")
    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *args, **kwargs: {
        "fact_updates": [], "internal_summary": "",
    })

    def provider(*args, **kwargs):
        if provider_failed:
            raise OSError("synthetic private provider error")
        return "Хочу понять, сколько всего осталось выплатить."

    monkeypatch.setattr(core.llm_agent, "generate_reply", provider)
    with style_eval.service_boundaries(configuration):
        result = style_eval.run_case(style_eval.cases()[2], configuration, max_turns=1)

    row = result["dialog"][1]
    assert row["voice_source"] == ("fallback" if provider_failed else "model")
    assert row["voice_reason"] == ("model_unavailable" if provider_failed else "model_advisory_accepted")
    assert "synthetic private provider error" not in json.dumps(result)


@pytest.mark.parametrize("kind", ["debt", "onloader"])
def test_actual_model_request_keeps_contact_and_private_authority_out_of_the_wire(monkeypatch, kind):
    state = debt_state() if kind == "debt" else onloader_state()
    phone = "+79991234567"
    name = "Ирина Синтетическая Проверочная"
    address = "Секретная улица 88 квартира 99"
    state["facts"].setdefault("client", {}).update(phone=phone, name=name, registration_address=address)
    state["chat_history"] = [{"role": "user", "content": f"Меня зовут {name}. Телефон {phone}. Адрес {address}."}]
    captured = []

    def post(configuration, body, **kwargs):
        assert kwargs["max_attempts"] == 1
        captured.append(body)
        reply = (json.dumps({"accept": True, "flags": []})
                 if configuration.model == configuration.extractor_model else "Номер повторять не нужно.")
        return {"choices": [{"message": {"content": reply}}]}

    # Capture the final provider body after both the core projection and LLM masking.
    monkeypatch.setattr(core.llm_agent, "_post", post)
    monkeypatch.setattr(policy_voice_critic, "assess", _ACTUAL_ASSESS)
    list(core.stream_reply(state, f"Мой номер {phone}", settings()))
    wire = json.dumps(captured, ensure_ascii=False)
    # The partial debt state still needs its typed debt question after the first answer.
    assert len(captured) == (3 if kind == "debt" else 2)
    assert [body["model"] for body in captured] == (
        [settings().model, settings().model, settings().extractor_model]
        if kind == "debt" else [settings().model, settings().extractor_model]
    )
    for private_value in (phone, name, address, "private-handle-synthetic", "private-operation"):
        assert private_value not in wire
    assert "телефон известен" in wire
