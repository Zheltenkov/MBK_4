"""Independent V18 saved-reply regressions through the same local conversation engine."""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import json
import re

import pytest

from config import AppConfig
import core
from dialogue_frame import DialogueFrame, request_fields
import llm_agent
from tests.test_policy_semantics_independent import envelope
from tests.test_policy_v18_independent import full_turn
from tests.test_policy_voice import debt_state


FALSE_ADVANCE = (
    "Хорошо, так и оставим — около 400 тысяч.\n\n"
    "Если точнее не знаете, этого достаточно для продолжения. "
    "Есть ещё вопросы по ситуации или можем двигаться дальше?"
)


def uncertain_debt_state():
    """Keep a known contact while the pending debt amount remains genuinely unknown."""
    state = debt_state(client={"name": "Тест", "phone": "+70000000401"})
    state["chat_history"] = [{
        "role": "assistant", "content": "Знаете точную общую сумму долга?",
    }]
    return state


@pytest.mark.parametrize("receipt", [
    "Хорошо, так и оставим — около 400 тысяч.",
    "Оставим сумму примерной: 400 тысяч рублей.",
])
def test_an_amount_receipt_does_not_become_a_collection_question(receipt):
    frame = DialogueFrame("debts.total", "collecting_debt_context", confirming=True,
                          require_question=True)
    assert "debts.total" not in request_fields(receipt, frame)


def test_the_saved_false_advance_is_rejected_before_a_full_turn_commits(monkeypatch):
    config = AppConfig(openrouter_api_key="synthetic-unused", model="synthetic-speaker",
                       extractor_model="synthetic-analysis", request_timeout=40, max_tokens=1000,
                       policy_voice_enabled=True, policy_semantic_enabled=True, debt_policy_enabled=True)
    state = uncertain_debt_state()
    previous_facts = deepcopy(state["facts"])
    calls = []

    def open_request(configuration, request):
        # Retain the real generation, parser, validation and bounded repair path.
        body = json.loads(request.data)
        calls.append(body)
        content = json.dumps(envelope()) if body["model"] == config.extractor_model else FALSE_ADVANCE
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    def no_delivery(*args, **kwargs):
        pytest.fail("Independent V19 regressions must not deliver leads")

    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    monkeypatch.setattr(llm_agent, "extract_facts", lambda *args, **kwargs: {
        "fact_updates": [], "internal_summary": "",
    })
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *args, **kwargs: deepcopy(facts))
    monkeypatch.setattr(core.delivery, "submit", no_delivery)
    monkeypatch.setattr(core, "_record_transcript", lambda *args, **kwargs: None)
    latest = "400 тысяч примерно."
    prepared = core.prepare_turn(state, latest, config, submit_delivery=False)
    answer = "".join(core.stream_reply(prepared, latest, config))
    committed = core.commit_turn(prepared, latest, answer)["current_state"]

    assert answer != FALSE_ADVANCE
    assert "достаточно для продолжения" not in answer.casefold()
    assert committed["facts"] == previous_facts
    assert committed["questionnaire_policy"]["amount_observation"]["kind"] == "unknown"
    assert prepared["_policy_spoken"].asked_field is None
    assert not committed.get("delivery")
    assert len(calls) <= 4


@pytest.mark.parametrize("latest", [
    "400 тысяч примерно.",
    "Примерно 400 тысяч, точную сумму не знаю.",
])
def test_an_outage_preserves_unknown_debt_without_pressuring_a_made_up_number(monkeypatch, latest):
    state = uncertain_debt_state()
    original_facts = deepcopy(state["facts"])
    prepared, committed, _ = full_turn(monkeypatch, state, latest, unavailable=True)
    answer = committed["chat_history"][-1]["content"]

    # Completion still requires an exact amount, while explanatory chat stays available.
    assert re.search(r"точн\w*", answer, re.I)
    assert re.search(r"анкет\w*|заявк\w*|заверш\w*", answer, re.I)
    assert re.search(r"здесь|чат\w*|обсуж\w*|поговор\w*", answer, re.I)
    assert not re.search(r"уточни(?:те)?|назови(?:те)?|напиши(?:те)?|укажи(?:те)?", answer, re.I)
    assert prepared["_policy_spoken"].asked_field is None
    assert committed["facts"] == original_facts
    assert committed["questionnaire_policy"]["amount_observation"]["kind"] == "unknown"
    assert committed["questionnaire_policy"]["phone_collection"] == "open"


@pytest.mark.parametrize("latest", ["Ага.", "Да, хочу."])
def test_the_actual_collected_turn_instructs_fulfilment_instead_of_another_invitation(monkeypatch, latest):
    state = debt_state(debts={"total": 400000}, client={
        "name": "Тест", "phone": "+70000000401", "region_class": "rf_other",
    })
    state["chat_history"] = [{
        "role": "assistant", "content": "Хотите обсудить возможные варианты решения долгов?",
    }]
    original_facts = deepcopy(state["facts"])
    prepared, committed, calls = full_turn(monkeypatch, state, latest)
    speech = next(call for call in calls if call["model"] == "synthetic-speaker")
    instruction = speech["messages"][2]["content"]

    # This verifies delivered prompt intent only. Fresh model dialogue review
    # separately establishes whether the model actually follows that direction.
    assert "без нового приглашения обсудить долги" in instruction
    assert "выполни предложенное" in instruction
    assert "готовую фразу" not in instruction
    assert committed["facts"] == original_facts
    assert prepared["questionnaire_policy"]["phone_collection"] == "open"
    assert prepared["_policy_spoken"].asked_field is None

