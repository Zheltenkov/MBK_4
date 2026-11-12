"""Намерение приходит тем же вызовом извлекателя — ни лишнего вызова, ни секунды."""
from __future__ import annotations

import json

import llm_agent
from config import AppConfig
from interpretation.contracts import (
    FactCandidate,
    FactCandidateExtraction,
    SemanticBoundarySignals,
)
from interpretation.prompt import build_interpretation_system_prompt
from routing.entry_intent import EntryIntent


PAYLOAD = {
    "facts": {},
    "short_history": [],
    "latest_user_message": "Что по моей заявке?",
    "internal_summary": "",
    "turn_instruction": "",
    "expected_paths": [],
}


def test_contract_field_is_optional():
    assert FactCandidateExtraction().entry_intent is None
    assert (
        FactCandidateExtraction.model_validate({"entry_intent": "existing"}).entry_intent
        is EntryIntent.EXISTING
    )


def test_parser_reads_the_intent(matrix):
    extraction = llm_agent._parse_candidate_extraction(
        {"candidates": [], "entry_intent": " Off_Topic "}, matrix
    )

    assert extraction.entry_intent is EntryIntent.OFF_TOPIC


def test_missing_intent_is_none(matrix):
    extraction = llm_agent._parse_candidate_extraction({"candidates": []}, matrix)

    assert extraction.entry_intent is None


def test_garbage_intent_is_none_and_costs_no_facts(matrix):
    """Поле добавочное: сломанное намерение не должно стоить хода фактов."""
    extraction = llm_agent._parse_candidate_extraction(
        {
            "candidates": [
                {"path": "request.amount", "value": 300000, "evidence": "300 тысяч"}
            ],
            "entry_intent": "дайте денег",
        },
        matrix,
    )

    assert extraction.entry_intent is None
    assert any(c.path == "request.amount" for c in extraction.candidates)


def test_prompt_without_the_flag_is_todays_prompt(matrix):
    assert "entry_intent" not in build_interpretation_system_prompt(matrix)


def test_prompt_with_the_flag_asks_for_the_intent_by_meaning(matrix):
    prompt = build_interpretation_system_prompt(matrix, with_entry_intent=True)

    assert '"entry_intent"' in prompt
    for value in ("money", "existing", "off_topic", "unclear"):
        assert f'"{value}"' in prompt


def _fake_model(monkeypatch, answer: dict) -> list[dict]:
    bodies: list[dict] = []

    def fake_post(config, body, *, max_attempts=3):
        bodies.append(body)
        return {
            "choices": [
                {"message": {"content": json.dumps(answer, ensure_ascii=False)}}
            ]
        }

    monkeypatch.setattr(llm_agent, "_post", fake_post)
    monkeypatch.setattr(llm_agent.observability, "_get_client", lambda: None)
    return bodies


def test_flag_on_asks_the_model_for_the_intent(monkeypatch, matrix):
    bodies = _fake_model(
        monkeypatch, {"boundaries": {}, "candidates": [], "entry_intent": "existing"}
    )
    config = AppConfig(openrouter_api_key="sk-or-test", entry_routing_enabled=True)

    extraction = llm_agent.extract_fact_candidates(PAYLOAD, config, matrix=matrix)

    assert extraction.entry_intent is EntryIntent.EXISTING
    assert len(bodies) == 1
    system, user = bodies[0]["messages"]
    assert '"entry_intent"' in system["content"]
    assert "entry_intent" in user["content"]


def test_flag_off_keeps_todays_request(monkeypatch, matrix):
    bodies = _fake_model(monkeypatch, {"boundaries": {}, "candidates": []})
    # Поведение без развилки: флаг выключен явно.
    config = AppConfig(openrouter_api_key="sk-or-test", entry_routing_enabled=False)

    extraction = llm_agent.extract_fact_candidates(PAYLOAD, config, matrix=matrix)

    assert extraction.entry_intent is None
    system, user = bodies[0]["messages"]
    assert "entry_intent" not in system["content"]
    assert "entry_intent" not in user["content"]


def test_answer_with_a_goal_mentions_money():
    extraction = FactCandidateExtraction(
        boundaries=SemanticBoundarySignals(goal="debt_relief")
    )

    assert extraction.mentions_money() is True


def test_answer_with_a_payment_status_mentions_money():
    extraction = FactCandidateExtraction(
        boundaries=SemanticBoundarySignals(payment_status="current_overdue")
    )

    assert extraction.mentions_money() is True


def test_answer_with_debt_or_amount_facts_mentions_money():
    for path, value in (
        ("request.amount", 300000),
        ("debts.has_active_credits", True),
        ("client.goal", "cash"),
    ):
        extraction = FactCandidateExtraction(
            candidates=[FactCandidate(path=path, value=value, evidence="x")]
        )
        assert extraction.mentions_money() is True, path


def test_answer_without_money_facts_does_not_mention_money():
    extraction = FactCandidateExtraction(
        candidates=[FactCandidate(path="client.name", value="Иван", evidence="Иван")]
    )

    assert extraction.mentions_money() is False
    assert FactCandidateExtraction().mentions_money() is False
