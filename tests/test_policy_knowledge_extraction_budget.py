"""Whole general knowledge questions contain no new application facts to extract."""
import pytest

import core
from tests.test_policy_grounded_voice import strict_settings
from tests.test_policy_v31_receipts import held_state, offline  # noqa: F401


@pytest.mark.parametrize("latest", [
    "Чем реструктуризация отличается от списания?",
    "Что такое банкротство?",
    "Объясните разницу между реструктуризацией и банкротством.",
])
def test_whole_knowledge_question_keeps_facts_without_an_extraction_call(monkeypatch, latest):
    def forbidden(*args, **kwargs):
        pytest.fail("A whole knowledge question cannot add an application fact")

    monkeypatch.setattr(core.llm_agent, "extract_facts", forbidden)
    before = held_state()
    prepared = core.prepare_turn(before, latest, strict_settings(), submit_delivery=False)
    assert prepared["facts"] == before["facts"]


@pytest.mark.parametrize("latest", [
    "Чем реструктуризация отличается от списания? Мой долг 500000 рублей.",
    "Что такое банкротство? Никуда не передавайте мои данные.",
    "Вы сказали «Что такое банкротство?»",
    "Если я хочу банкротство, какие у меня варианты?",
])
def test_mixed_or_unproven_input_still_extracts_facts(monkeypatch, latest):
    calls = []

    def extract(*args, **kwargs):
        calls.append(True)
        return {"fact_updates": [], "internal_summary": ""}

    monkeypatch.setattr(core.llm_agent, "extract_facts", extract)
    core.prepare_turn(held_state(), latest, strict_settings(), submit_delivery=False)
    assert len(calls) == 1
