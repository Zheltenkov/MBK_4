"""Real-model dialogue regressions for known facts and ordinary punctuation."""
from __future__ import annotations

from copy import deepcopy

import pytest

import core
from config import AppConfig
from tests.test_policy_voice import debt_state


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *a, **k: {"fact_updates":[],"internal_summary":""})
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *a, **k: deepcopy(facts))
    monkeypatch.setattr(core, "_record_transcript", lambda *a, **k: None)


@pytest.mark.parametrize("message", ["А зачем это уточнять?", "Чем отличается реструктуризация?", "Спасибо."])
def test_followup_does_not_revoke_region_or_reopen_the_completed_intake(message):
    state = debt_state(client={"name":"Ирина", "phone":"+70000000001", "region_class":"rf_other"},
                       debts={"total":400000})
    state["script"]["asked"] = ["client.region_class"]
    state["fact_statuses"]["client.region_class"] = "known"
    prepared = core.prepare_turn(state, message, AppConfig(openrouter_api_key="unused"), submit_delivery=False)
    assert prepared["facts"]["client"]["region_class"] == "rf_other"
    assert prepared["fact_statuses"]["client.region_class"] == "known"
    assert prepared["dialog_phase"] == "collected_held"
    assert prepared["plan"]["fields"] == []


@pytest.mark.parametrize("name", ["Сергей.", "сергей.", "Антон."])
def test_a_name_with_sentence_punctuation_is_accepted_without_another_question(name):
    state = debt_state(client={"region_class":"moscow_mo"}, debts={"total":280000})
    assert state["script"]["asked"] == ["client.name"]
    prepared = core.prepare_turn(state, name, AppConfig(openrouter_api_key="unused"), submit_delivery=False)
    assert prepared["facts"]["client"]["name"].casefold() == name.rstrip(".").casefold()
    assert prepared["plan"]["fields"] == ["client.phone"]
