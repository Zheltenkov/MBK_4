"""Sent factual-audit contract checks, without claiming model accuracy."""
import json
from pathlib import Path

import pytest

import policy_semantic_analysis as analysis
from dialogue_frame import DialogueFrame
from policy_semantics import CLAIM_PREDICATES, REQUEST_SLOT_NAMES, parse_semantics
from tests.test_policy_semantic_analysis import configuration, stub


def test_factual_audit_experiment_is_archived_and_combined_extraction_keeps_its_previous_contract(monkeypatch):
    calls = stub(monkeypatch, analysis._empty_response())
    analysis.analyze({"latest_user_message": "Расскажите об условиях."}, "Условия зависят от договора.",
                     DialogueFrame(None, "collected_held"), configuration())
    system = calls[0][0][0]["content"]
    fixture = json.loads((Path(__file__).parent / "fixtures/v31_factual_audit_experiment.json").read_text(encoding="utf-8"))
    # Balanced user-act examples may grow independently of the archived F experiment.
    assert len(analysis._EXAMPLES) >= 7 and "Проверка фактов" not in system
    affirmative, separate_refusal = analysis._EXAMPLES[:2]
    assert affirmative["result"]["user"]["acts"] == []
    assert [act["kind"] for act in separate_refusal["result"]["user"]["acts"]] == ["decline_transfer"]
    assert "Само отсутствие факта" in fixture["leading_audit"]
    assert "Перефразирование" in fixture["leading_audit"]
    assert "context.verified_context" in system and "unsupported_claim" in system


@pytest.mark.parametrize("draft,accept", [
    ("Гарантия распространяется на производственные дефекты сенсора.", True),
    ("Гарантия распространяется на любые дефекты устройства.", False),
    ("При подтверждённом производственном дефекте сенсора возможна бесплатная замена.", True),
    ("Не всякое обращение одобряют. Вам обязательно бесплатно заменят устройство.", False),
])
def test_cross_domain_examples_teach_scope_and_modality_without_new_claim_slots(draft, accept):
    examples = json.loads((Path(__file__).parent / "fixtures/v31_factual_audit_experiment.json").read_text(encoding="utf-8"))["examples"]
    assert len(examples) == 4
    example = next(example for example in examples if example["input"]["draft"] == draft)
    value = example["result"]
    assert value["style"] == {"accept": accept, "flags": [] if accept else ["unsupported_claim"]}
    assert set(value["claims"]["predicates"]) == set(CLAIM_PREDICATES)
    assert set(value["claims"]["requests"]) == set(REQUEST_SLOT_NAMES)
    assert all(item is None for item in value["claims"]["predicates"].values())
    assert all(item is None for item in value["claims"]["requests"].values())
    parsed = parse_semantics(json.dumps(value, ensure_ascii=False),
                             latest_user_message=example["input"]["latest_user_message"], draft=draft)
    assert parsed.user_error is None and parsed.claims_error is None and parsed.need_error is None


@pytest.mark.parametrize("draft,raw_accept", [
    ("Алименты и возмещение вреда остаются.", False),
    ("Алименты и возмещение вреда жизни или здоровью сохраняются.", True),
    ("Неверно, что любое возмещение вреда всегда сохраняется.", True),
    ("В примере написано: «Любое возмещение вреда остаётся». Это не проверенный вывод.", True),
    ("Кредитный долг может быть списан полностью при соблюдении условий закона.", True),
    ("Банкротство всегда доступно человеку без дохода.", False),
])
def test_polarity_qualified_and_other_legal_mock_opinions_keep_raw_attribution(monkeypatch, draft, raw_accept):
    # These are supplied opinions, not predictions or a classifier accuracy score.
    value = analysis._empty_response()
    value["style"] = {"accept": raw_accept, "flags": [] if raw_accept else ["unsupported_claim"]}
    calls = stub(monkeypatch, value)
    result = analysis.analyze({"latest_user_message": "Что означает списание?"}, draft,
                              DialogueFrame(None, "collected_held", question_goal="debt_explanation"),
                              configuration())
    assert len(calls) == 1
    assert result.assessment.verdict is not None
    assert result.assessment.verdict.accept is raw_accept
    assert result.assessment.verdict.flags == value["style"]["flags"]
