"""Shadow semantic extractor не влияет на рабочий dialog state."""

from __future__ import annotations

import json
from threading import Barrier

import pytest

import core
import llm_agent
from config import AppConfig, load_config
from interpretation import (
    FactCandidate,
    FactCandidateExtraction,
    FactSource,
)
from state import init_state


SHADOW_CONFIG = AppConfig(
    openrouter_api_key="sk-or-test",
    fact_interpretation_mode="shadow",
    fact_shadow_max_calls_per_session=2,
)
SEMANTIC_CONFIG = AppConfig(
    openrouter_api_key="sk-or-test",
    fact_interpretation_mode="semantic",
)


def test_shadow_and_legacy_run_in_parallel_but_legacy_owns_facts(
    monkeypatch,
):
    """Barrier доказывает одновременный старт двух независимых extractor."""
    barrier = Barrier(2)

    def legacy_extract(*args, **kwargs):
        barrier.wait(timeout=2)
        return {
            "fact_updates": [
                {
                    "path": "request.amount",
                    "value": 400_000,
                    "confidence": "high",
                },
                {
                    "path": "client.goal",
                    "value": "cash",
                    "confidence": "high",
                },
            ],
            "internal_summary": "",
        }

    def shadow_extract(*args, **kwargs):
        barrier.wait(timeout=2)
        return FactCandidateExtraction(
            candidates=[
                FactCandidate(
                    path="request.amount",
                    value=700_000,
                    evidence="нужно 700 тысяч",
                )
            ]
        )

    monkeypatch.setattr(core.llm_agent, "extract_facts", legacy_extract)
    monkeypatch.setattr(
        core.llm_agent,
        "extract_fact_candidates",
        shadow_extract,
    )

    prepared = core.prepare_turn(
        init_state(),
        "Мне нужно 700 тысяч.",
        SHADOW_CONFIG,
        submit_delivery=False,
    )

    # Shadow увидел другое значение, но runtime использует только legacy.
    assert prepared["facts"]["request"]["amount"] == 400_000
    metadata = prepared["interpretation"]
    assert metadata["last_status"] == "success"
    assert metadata["shadow_calls"] == 1
    assert metadata["last_result"]["comparison"]["conflict_paths"] == [
        "request.amount"
    ]
    assert metadata["last_result"]["comparison"]["legacy_only_paths"] == [
        "client.goal"
    ]


def test_shadow_failure_is_recorded_and_does_not_break_legacy(monkeypatch):
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *args, **kwargs: {
            "fact_updates": [
                {
                    "path": "client.goal",
                    "value": "cash",
                    "confidence": "high",
                }
            ],
            "internal_summary": "",
        },
    )

    def failed_shadow(*args, **kwargs):
        raise TimeoutError("shadow timeout")

    monkeypatch.setattr(
        core.llm_agent,
        "extract_fact_candidates",
        failed_shadow,
    )

    prepared = core.prepare_turn(
        init_state(),
        "Хочу получить деньги.",
        SHADOW_CONFIG,
        submit_delivery=False,
    )

    assert prepared["facts"]["client"]["goal"] == "cash"
    assert prepared["interpretation"]["last_status"] == "error"
    assert "TimeoutError" in prepared["interpretation"]["last_error"]
    assert prepared["interpretation"]["aggregate"]["errors"] == 1


def test_semantic_mode_owns_runtime_facts(monkeypatch):
    """Semantic mode применяет reducer и не тратит вызов на legacy."""
    legacy_calls = 0

    def tracked_legacy(*args, **kwargs):
        nonlocal legacy_calls
        legacy_calls += 1
        return {"fact_updates": [], "internal_summary": ""}

    monkeypatch.setattr(core.llm_agent, "extract_facts", tracked_legacy)
    monkeypatch.setattr(
        core.llm_agent,
        "extract_fact_candidates",
        lambda *args, **kwargs: FactCandidateExtraction(
            candidates=[
                FactCandidate(
                    path="request.amount",
                    value=700_000,
                    evidence="700 тысяч",
                )
            ]
        ),
    )

    prepared = core.prepare_turn(
        init_state(),
        "Мне нужно 700 тысяч.",
        SEMANTIC_CONFIG,
        submit_delivery=False,
    )

    assert prepared["facts"]["request"]["amount"] == 700_000
    assert legacy_calls == 0
    assert prepared["interpretation"]["runtime_source"] == "semantic"
    assert prepared["interpretation"]["last_result"]["comparison"] is None
    assert prepared["interpretation"]["last_result"]["pre_geo_plan"]["legacy"] is None
    assert prepared["fact_statuses"]["request.amount"] == "known"
    assert prepared["fact_events"][-1]["path"] == "request.amount"


def test_semantic_mode_falls_back_to_legacy_on_extractor_error(monkeypatch):
    legacy_calls = 0

    def legacy_extract(*args, **kwargs):
        nonlocal legacy_calls
        legacy_calls += 1
        return {
            "fact_updates": [
                {
                    "path": "client.goal",
                    "value": "cash",
                    "confidence": "high",
                }
            ],
            "internal_summary": "",
        }

    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        legacy_extract,
    )

    def failed_semantic(*args, **kwargs):
        raise TimeoutError("semantic timeout")

    monkeypatch.setattr(
        core.llm_agent,
        "extract_fact_candidates",
        failed_semantic,
    )

    prepared = core.prepare_turn(
        init_state(),
        "Нужны деньги.",
        SEMANTIC_CONFIG,
        submit_delivery=False,
    )

    assert prepared["facts"]["client"]["goal"] == "cash"
    assert legacy_calls == 1
    assert prepared["interpretation"]["runtime_source"] == "legacy_fallback"
    assert prepared["interpretation"]["last_status"] == "error"


def test_shadow_session_budget_prevents_additional_api_call(monkeypatch):
    state = init_state()
    state["interpretation"]["shadow_calls"] = 1
    config = AppConfig(
        openrouter_api_key="sk-or-test",
        fact_interpretation_mode="shadow",
        fact_shadow_max_calls_per_session=1,
    )
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *args, **kwargs: {
            "fact_updates": [],
            "internal_summary": "",
        },
    )

    def forbidden_shadow(*args, **kwargs):
        raise AssertionError("shadow budget must prevent this call")

    monkeypatch.setattr(
        core.llm_agent,
        "extract_fact_candidates",
        forbidden_shadow,
    )

    prepared = core.prepare_turn(
        state,
        "Продолжим.",
        config,
        submit_delivery=False,
    )

    assert prepared["interpretation"]["last_status"] == "budget_exhausted"
    assert prepared["interpretation"]["shadow_calls"] == 1


def test_legacy_mode_never_calls_shadow(monkeypatch):
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *args, **kwargs: {
            "fact_updates": [],
            "internal_summary": "",
        },
    )

    def forbidden_shadow(*args, **kwargs):
        raise AssertionError("legacy mode must not call shadow")

    monkeypatch.setattr(
        core.llm_agent,
        "extract_fact_candidates",
        forbidden_shadow,
    )

    prepared = core.prepare_turn(
        init_state(),
        "Здравствуйте.",
        AppConfig(openrouter_api_key="sk-or-test"),
        submit_delivery=False,
    )

    assert prepared["interpretation"]["mode"] == "legacy"
    assert prepared["interpretation"]["shadow_calls"] == 0


def test_shadow_transport_forces_untrusted_source_and_masks_pii(monkeypatch):
    captured: dict = {}

    def fake_post(config, body, *, max_attempts=3):
        captured["body"] = body
        captured["max_attempts"] = max_attempts
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "candidates": [
                                    {
                                        "path": "request.amount",
                                        "value": "700000",
                                        "evidence": "700 тысяч",
                                        "source": "dadata",
                                    },
                                    {
                                        "path": "assets.has_realty",
                                        "value": "false",
                                        "evidence": "недвижимости нет",
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr(llm_agent, "_post", fake_post)
    payload = {
        "facts": {"client": {"phone": "+79991234567"}},
        "short_history": [],
        "latest_user_message": "Телефон +79991234567, нужно 700 тысяч.",
        "internal_summary": "",
    }

    extraction = llm_agent.extract_fact_candidates(
        payload,
        SHADOW_CONFIG,
    )
    serialized_messages = json.dumps(
        captured["body"]["messages"],
        ensure_ascii=False,
    )

    assert extraction.candidates[0].source == FactSource.USER_EXPLICIT
    assert extraction.candidates[0].value == 700_000
    assert extraction.candidates[1].value is False
    assert (
        extraction.candidates[2].path,
        extraction.candidates[2].value,
    ) == ("client.goal", "cash")
    assert [issue.code.value for issue in extraction.issues] == [
        "domain_policy_applied"
    ]
    assert captured["max_attempts"] == 1
    assert captured["body"]["max_tokens"] == 1000
    assert "+79991234567" not in serialized_messages
    assert "[телефон]" in serialized_messages


def test_thousands_separated_amount_is_repaired_not_silently_lost(monkeypatch):
    """Модель иногда форматирует сумму как «1 000 000» вместо чистого JSON-числа.

    Раньше `_repair_candidate_json_scalar` чинил только чистые цифровые строки
    ("700000"), а «1 000 000» (обычный для русского текста разделитель разрядов)
    оставался строкой и молча отклонялся reducer'ом на этапе `_canonical_value` —
    без единой записи в issues/events. Разговорная сумма просто исчезала.
    """

    def fake_post(config, body, *, max_attempts=3):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "candidates": [
                                    {
                                        "path": "request.amount",
                                        "value": "1 000 000",
                                        "evidence": "1 000 000",
                                    },
                                ]
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ],
            "usage": {},
        }

    monkeypatch.setattr(llm_agent, "_post", fake_post)

    extraction = llm_agent.extract_fact_candidates(
        {
            "facts": {},
            "short_history": [],
            "latest_user_message": "Нужна сумма 1 000 000.",
        },
        SHADOW_CONFIG,
    )

    assert [issue.code.value for issue in extraction.issues] == [
        "domain_policy_applied"
    ]
    assert extraction.candidates[0].value == 1_000_000


def test_invalid_candidate_does_not_discard_valid_batch(monkeypatch):
    """Один сломанный элемент становится issue, валидные факты сохраняются."""

    def fake_post(config, body, *, max_attempts=3):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "candidates": [
                                    {
                                        "path": "assets.has_realty",
                                        "value": True,
                                        "evidence": "апартаменты",
                                    },
                                    {
                                        "path": "assets.realty.kind",
                                        "value": "flat",
                                        "operation": "clear",
                                        "evidence": "не квартира",
                                    },
                                    {
                                        "path": "assets.realty.kind",
                                        "value": "apartments",
                                        "evidence": "апартаменты",
                                    },
                                ]
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ],
            "usage": {},
        }

    monkeypatch.setattr(llm_agent, "_post", fake_post)

    extraction = llm_agent.extract_fact_candidates(
        {
            "facts": {},
            "short_history": [],
            "latest_user_message": "В собственности апартаменты, не квартира.",
        },
        SHADOW_CONFIG,
    )

    assert [
        (candidate.path, candidate.value)
        for candidate in extraction.candidates
    ] == [
        ("assets.has_realty", True),
        ("assets.realty.kind", "apartments"),
    ]
    assert len(extraction.issues) == 1
    assert extraction.issues[0].code.value == "invalid_schema"
    assert extraction.issues[0].raw_index == 1


def test_null_optional_candidate_attributes_use_contract_defaults(monkeypatch):
    """Провайдерский null в optional-полях не уничтожает валидный факт."""

    def fake_post(config, body, *, max_attempts=3):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "boundaries": {
                                    "payment_status": "no_current_overdue",
                                    "new_borrowing": "not_mentioned",
                                    "alternatives": "not_mentioned",
                                    "collateral": "not_mentioned",
                                },
                                "candidates": [
                                    {
                                        "path": "debts.credit_history",
                                        "value": "no_overdue",
                                        "evidence": "просрочек сейчас нет",
                                        "operation": None,
                                        "explicitness": None,
                                        "precision": None,
                                        "evidence_basis": None,
                                        "subject": None,
                                    }
                                ],
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ],
            "usage": {},
        }

    monkeypatch.setattr(llm_agent, "_post", fake_post)
    extraction = llm_agent.extract_fact_candidates(
        {
            "facts": {},
            "short_history": [],
            "latest_user_message": "Просрочек сейчас нет.",
        },
        SHADOW_CONFIG,
    )

    assert [
        (candidate.path, candidate.value)
        for candidate in extraction.candidates
    ] == [("debts.credit_history", "no_overdue")]
    assert extraction.issues == []


def test_retired_policy_drops_only_same_turn_unemployed(monkeypatch):
    """CRM enum retired побеждает только точную пару retired/unemployed."""

    def fake_post(config, body, *, max_attempts=3):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "candidates": [
                                    {
                                        "path": "employment.type",
                                        "value": "retired",
                                        "evidence": "пенсионер",
                                    },
                                    {
                                        "path": "employment.type",
                                        "value": "unemployed",
                                        "evidence": "не работаю",
                                    },
                                ]
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ],
            "usage": {},
        }

    monkeypatch.setattr(llm_agent, "_post", fake_post)

    extraction = llm_agent.extract_fact_candidates(
        {
            "facts": {},
            "short_history": [],
            "latest_user_message": "Я пенсионер, сейчас не работаю.",
        },
        SHADOW_CONFIG,
    )

    assert [candidate.value for candidate in extraction.candidates] == [
        "retired"
    ]
    assert [issue.code.value for issue in extraction.issues] == [
        "domain_policy_superseded"
    ]


def test_invalid_shadow_json_is_not_retried(monkeypatch):
    calls = 0

    def fake_post(config, body, *, max_attempts=3):
        nonlocal calls
        calls += 1
        return {
            "choices": [{"message": {"content": "не json"}}],
            "usage": {},
        }

    monkeypatch.setattr(llm_agent, "_post", fake_post)

    with pytest.raises(json.JSONDecodeError):
        llm_agent.extract_fact_candidates(
            {
                "facts": {},
                "short_history": [],
                "latest_user_message": "Нужны деньги.",
            },
            SHADOW_CONFIG,
        )

    assert calls == 1


def test_invalid_interpretation_mode_fails_config_load(monkeypatch):
    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("MBK_FACT_INTERPRETATION_MODE", "magic")

    with pytest.raises(ValueError, match="legacy, shadow или semantic"):
        load_config()
