"""An explicitly named administrative region is enough for the debt branch."""
from __future__ import annotations

from copy import deepcopy
import socket

import pytest

import core
import debt_policy
import debt_workflow
from config import AppConfig
from state import init_state


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Named-region regressions cannot use external adapters")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(core.delivery, "submit", forbidden)
    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *a, **k: {"fact_updates": []})
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *a, **k: deepcopy(facts))
    monkeypatch.setattr(core, "_record_transcript", lambda *a, **k: None)


@pytest.mark.parametrize("message,asked,expected", [
    ("Я живу в Московской области.", False, "moscow_mo"),
    ("Проживаю в Московской области.", False, "moscow_mo"),
    ("Московской области", True, "moscow_mo"),
    ("В Московской области.", True, "moscow_mo"),
    ("Я живу в Ленинградской области.", False, "rf_other"),
    ("Я живу в Тверской области.", False, "rf_other"),
    ("Краснодарском крае", True, "rf_other"),
    ("Я в Московской области.", False, "moscow_mo"),
    ("Московская область, а можно без телефона?", True, "moscow_mo"),
    ("Я живу в Московской области. Можно без телефона?", False, "moscow_mo"),
    ("Я в Тверской области, а можно примерно указать долг?", False, "rf_other"),
])
def test_own_named_region_in_natural_case_does_not_require_a_city(message, asked, expected):
    resolved = debt_workflow._region_answer(message, asked=asked)
    assert resolved is not None and resolved[0] == expected


@pytest.mark.parametrize("message", [
    "Мама живёт в Московской области.",
    "Квартира находится в Московской области.",
    "Если я живу в Московской области, что будет?",
    "Я не живу в Московской области.",
    "Я живу в Московской области?",
    "Я живу в неизвестной области.",
    "Он написал: «Это пример. Я живу в Московской области. Это цитата».",
    "Он написал: «Это пример. Я живу в Московской области.",
    "Я живу в Московской области, точнее в Тверской области.",
])
def test_nonown_uncertain_quoted_or_conflicting_region_is_not_promoted(message):
    assert debt_workflow._region_answer(message, asked=True) is None


def test_unasked_bare_region_cannot_replace_a_known_own_location():
    assert debt_workflow._region_answer("Московской области", asked=False) is None


def test_named_region_completes_only_missing_region_and_preserves_refusal_and_facts():
    state = init_state({"full_name": "Ирина", "debts_total": 400000})
    state[debt_policy.POLICY_KEY] = debt_policy.DebtPolicyState(transfer_declined=True).as_state()
    state = core.initialize_debt_questionnaire(state)
    config = AppConfig(openrouter_api_key="synthetic-unused", debt_policy_enabled=True)
    prepared = core.prepare_turn(state, "Я живу в Московской области.", config, submit_delivery=False)
    assert prepared["facts"]["client"]["region_class"] == "moscow_mo"
    assert prepared["fact_statuses"]["client.region_class"] == "known"
    assert "client.region_class" not in prepared["plan"]["fields"]
    assert prepared["facts"]["client"]["name"] == "Ирина"
    assert prepared["facts"]["debts"]["total"] == 400000
    assert debt_policy.parse_policy(prepared).transfer_declined
    assert not prepared.get("delivery") and not prepared.get("onloader")
