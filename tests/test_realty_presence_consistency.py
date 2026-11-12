"""Owned, confirmed property attributes cannot leave presence missing."""
from copy import deepcopy

import pytest

import core
from config import AppConfig
from routing.derive import derive_facts
from routing.engine import get_fact
from state import init_state


def owned_property():
    return {"assets": {"realty": {"ownership_form": "private", "owners": "client_and_spouse"}}}


def test_confirmed_owned_attributes_fill_only_missing_presence_without_mutating_input():
    facts = owned_property()
    original = deepcopy(facts)
    result = derive_facts(facts)
    assert get_fact(result, "assets.has_realty") is True
    assert get_fact(result, "assets.has_vehicle") is None
    assert facts == original


def test_the_owners_fact_proves_presence_without_guessing_the_missing_ownership_form():
    facts = {"assets": {"realty": {"owners": "client_and_spouse"}}}
    result = derive_facts(facts)
    assert get_fact(result, "assets.has_realty") is True
    assert get_fact(result, "assets.realty.ownership_form") is None


@pytest.mark.parametrize("facts,statuses", [
    ({"assets": {"has_realty": False, "realty": {"ownership_form": "private", "owners": "client_only"}}}, {}),
    ({"assets": {"realty": {"ownership_form": "private", "owners": "others"}}}, {}),
    ({"assets": {"realty": {"ownership_form": "municipal", "owners": "client_only"}}}, {}),
    ({"assets": {"realty": {"ownership_form": "private"}}}, {}),
    (owned_property(), {"assets.realty.owners": "needs_confirmation"}),
    (owned_property(), {"assets.realty.ownership_form": "unknown"}),
    (owned_property(), {"assets.has_realty": "conflict"}),
    (owned_property(), {"assets.realty.owners": {"status": "unknown"}}),
])
def test_negative_or_unsettled_evidence_does_not_invent_presence(facts, statuses):
    original = deepcopy(facts)
    result = derive_facts(facts, fact_statuses=statuses)
    assert get_fact(result, "assets.has_realty") == get_fact(original, "assets.has_realty")
    assert facts == original


def test_the_normal_prepare_entry_point_closes_the_missing_parent_boolean(monkeypatch):
    state = init_state()
    state["facts"] = owned_property()
    state["fact_statuses"] = {"assets.realty.ownership_form": "known", "assets.realty.owners": "confirmed"}
    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *args, **kwargs: {"fact_updates": [], "internal_summary": ""})
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *args, **kwargs: deepcopy(facts))
    def forbidden(*args, **kwargs):
        pytest.fail("Presence consistency tests cannot call a model or deliver a lead")
    monkeypatch.setattr(core.llm_agent, "_urlopen", forbidden)
    monkeypatch.setattr(core.delivery, "submit", forbidden)
    prepared = core.prepare_turn(state, "Дальше.", AppConfig(openrouter_api_key="unused", entry_routing_enabled=False),
                                 submit_delivery=False)
    assert get_fact(prepared["facts"], "assets.has_realty") is True
    assert "assets.has_realty" not in prepared["plan"]["fields"]
    assert get_fact(state["facts"], "assets.has_realty") is None
