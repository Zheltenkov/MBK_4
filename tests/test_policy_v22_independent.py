"""Independent full-entry regressions for three observed V21 presentation defects."""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import json
import re

import pytest

from config import AppConfig
import core
from dialogue_frame import DialogueFrame
from evaluation.local_style_eval import execute_core_turn
import llm_agent
from policy_content import content_issues
import policy_voice
from tests.test_policy_semantics_independent import envelope
from tests.test_policy_voice import debt_state
from usage_tracker import SessionUsage


def actual_turn(monkeypatch, state, latest, *, replies=(), updates=(), unavailable=False):
    """Use the existing main-equivalent lifecycle gate and intercept only model HTTP."""
    config = AppConfig(openrouter_api_key="synthetic-unused", model="synthetic-speaker",
                       extractor_model="synthetic-analysis", request_timeout=40, max_tokens=1000,
                       policy_voice_enabled=True, policy_semantic_enabled=True, debt_policy_enabled=True)
    drafts = iter(replies)
    calls = []

    def open_request(configuration, request):
        body = json.loads(request.data)
        calls.append(body)
        if unavailable:
            raise TimeoutError("Synthetic unavailability")
        text = json.dumps(envelope()) if body["model"] == config.extractor_model else next(drafts)
        return BytesIO(json.dumps({"choices": [{"message": {"content": text}}]}).encode())

    def no_delivery(*args, **kwargs):
        pytest.fail("Independent V22 checks must not deliver leads")

    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    monkeypatch.setattr(llm_agent, "extract_facts", lambda *args, **kwargs: {
        "fact_updates": list(updates), "internal_summary": "",
    })
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *args, **kwargs: deepcopy(facts))
    monkeypatch.setattr(core.delivery, "submit", no_delivery)
    monkeypatch.setattr(core, "_record_transcript", lambda *args, **kwargs: None)
    execution = execute_core_turn(state, latest, config, SessionUsage("independent-v22-local"))
    prepared, committed = execution.prepared, execution.state
    answer = "\n\n".join(execution.bubbles)
    assert not committed.get("delivery")
    if answer:
        assert committed["chat_history"][-2:] == [
            {"role": "user", "content": latest}, {"role": "assistant", "content": answer},
        ]
    else:
        # Main and the evaluation orchestrator persist only the silent user turn.
        assert committed["chat_history"][-1] == {"role": "user", "content": latest}
    assert len(calls) <= 4
    return answer, prepared, committed, calls


def test_a_newly_resolved_city_does_not_trigger_the_saved_geographic_confirmation(monkeypatch):
    state = debt_state(debts={"total": 400000}, client={"name": "Тест", "phone": "+70000000401"})
    state["chat_history"] = [{"role": "assistant", "content": "В каком городе и регионе вы живёте?"}]
    bad = "Казань — это Татарстан, верно? Уточняю регион для точности."
    safe = "Если график выплат стал неудобным, условия можно обсудить с кредитором."
    answer, prepared, committed, _ = actual_turn(
        monkeypatch, state, "Казань.", replies=[bad, safe],
        updates=[{"path": "client.region_class", "value": "rf_other"}],
    )
    assert "верно?" not in answer and "Уточняю регион" not in answer
    assert prepared["_policy_spoken"].asked_field is None
    assert committed["facts"]["client"]["region_class"] == "rf_other"
    assert committed["facts"]["client"]["phone"] == state["facts"]["client"]["phone"]
    assert committed["facts"]["debts"]["total"] == 400000


def test_an_unresolved_region_keeps_its_actual_collection_question():
    frame = DialogueFrame("client.region_class", "collecting_debt_context")
    text = "В каком городе и регионе вы живёте?"
    reply = policy_voice._model_reply(text, frame, "independent_unresolved")
    assert reply is not None and reply.text == text and reply.asked_field == "client.region_class"


def test_a_repeated_availability_tail_is_omitted_before_the_final_critic_reads_the_answer(monkeypatch):
    state = debt_state(debts={"total": 400000}, client={
        "region_class": "rf_other", "name": "Тест", "phone": "+70000000401",
    })
    tail = "Если появятся вопросы — пишите, я на связи."
    state["chat_history"] = [{"role": "assistant", "content": tail}]
    useful = "При реструктуризации меняют срок или график платежей. Сам долг остаётся."
    answer, _, committed, calls = actual_turn(
        monkeypatch, state, "Что такое реструктуризация?", replies=[useful + "\n\n" + tail] * 2,
    )
    assert answer == useful
    analysis_drafts = [json.loads(call["messages"][-1]["content"])["draft"]
                       for call in calls if call["model"] == "synthetic-analysis"]
    assert analysis_drafts and analysis_drafts[-1] == answer
    assert committed["facts"] == state["facts"]


def test_an_answer_about_returning_to_the_chat_is_preserved_even_after_a_similar_closing(monkeypatch):
    state = debt_state(debts={"total": 400000}, client={
        "region_class": "rf_other", "name": "Тест", "phone": "+70000000401",
    })
    state["chat_history"] = [{
        "role": "assistant", "content": "Если появятся вопросы — пишите, я на связи.",
    }]
    direct_answer = "Да, можно вернуться к разговору позже и написать здесь."
    answer, _, committed, _ = actual_turn(
        monkeypatch, state, "Можно потом вернуться к разговору?", replies=[direct_answer] * 2,
    )
    assert answer == direct_answer
    assert committed["facts"] == state["facts"]


def test_a_bare_acknowledgement_does_not_silence_the_actual_pending_name_question(monkeypatch):
    state = debt_state(debts={"total": 280000}, client={"region_class": "moscow_mo"})
    question = "Как к вам обращаться?"
    state["chat_history"] = [{"role": "assistant", "content": question}]
    answer, prepared, committed, _ = actual_turn(monkeypatch, state, "Ага.", replies=[question] * 2)
    assert answer
    assert prepared["_policy_spoken"].asked_field == "client.name"
    assert committed["facts"] == state["facts"]
    assert committed["questionnaire_policy"]["phone_collection"] == "open"


def test_serial_quiet_acknowledgements_keep_the_answered_faq_available_but_a_new_question_reopens_it(monkeypatch):
    state = debt_state()
    state["chat_history"] = [
        {"role": "user", "content": "Что такое реструктуризация?"},
        {"role": "assistant", "content": "При реструктуризации меняют график. Сам долг остаётся."},
    ]
    facts = deepcopy(state["facts"])
    for _ in range(2):
        answer, _, state, calls = actual_turn(monkeypatch, state, "Ага.")
        assert answer == "" and calls == []
        assert state["facts"] == facts
    restored_legacy = deepcopy(state)
    restored_legacy["chat_history"].append({"role": "assistant", "content": ""})
    assert core.conversation_is_over(restored_legacy, "Ага.")
    explanation = "При реструктуризации меняют условия кредита. Списание возможно по правилам банкротства."
    answer, _, committed, calls = actual_turn(
        monkeypatch, state, "А чем реструктуризация отличается от списания?", replies=[explanation] * 2,
    )
    assert answer == explanation and calls
    assert committed["facts"] == facts


@pytest.mark.parametrize("declined_phone", [False, True])
def test_a_total_model_outage_uses_a_short_factual_faq_without_reopening_contacts(monkeypatch, declined_phone):
    state = debt_state(debts={"total": 280000}, client={
        "region_class": "moscow_mo", "name": "Тест",
    })
    if declined_phone:
        state["questionnaire_policy"]["phone_collection"] = "declined"
    else:
        state["facts"]["client"]["phone"] = "+70000000401"
    original_facts = deepcopy(state["facts"])
    answer, prepared, committed, calls = actual_turn(
        monkeypatch, state, "Чем реструктуризация отличается от списания?", unavailable=True,
    )
    assert len(answer) <= 650 and len(answer.split("\n\n")) <= 2
    assert re.search(r"реструктур\w*", answer, re.I)
    assert re.search(r"банкрот\w*", answer, re.I)
    assert re.search(r"алим\w*", answer, re.I) and re.search(r"жизн\w*|здоров\w*", answer, re.I)
    assert re.search(r"кредитор\w*", answer, re.I)
    assert not content_issues(answer, mode="debt", question_goal="debt_explanation")
    assert not re.search(r"нажми|подтверди|номер|телефон", answer, re.I)
    assert prepared["_policy_spoken"].asked_field is None
    assert committed["facts"] == original_facts
    assert committed["questionnaire_policy"]["phone_collection"] == ("declined" if declined_phone else "open")
    assert calls
