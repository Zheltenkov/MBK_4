"""Накопительный replay не маскирует пропуски legacy-состоянием."""

from __future__ import annotations

from copy import deepcopy

from core import _plan_snapshot
from evaluation.shadow_replay import (
    aggregate_shadow_replays,
    replay_dialog_shadow,
)
from interpretation import FactCandidate, FactSource
from routing import plan


def _artifact(matrix):
    first_facts = {
        "client": {
            "name": "Павел",
            "phone": "+79990000000",
            "goal": "cash",
        },
        "request": {"amount": 800_000},
    }
    final_facts = deepcopy(first_facts)
    final_facts["client"].update(
        {"age": 42, "city": "Москва", "region_class": "moscow_mo"}
    )
    # Анкета проходится целиком, поэтому блоки долгов и занятости тоже закрыты.
    final_facts["debts"] = {"has_active_credits": False}
    final_facts["employment"] = {"type": "official", "income": 150_000}
    final_facts["assets"] = {
        "has_realty": True,
        "has_vehicle": False,
        "realty": {
            "city": "Москва",
            "region_class": "moscow_mo",
            "kind": "flat",
            "ownership_form": "private",
            "owners": "client_only",
        },
    }
    first_candidates = [
        FactCandidate(
            path="client.name",
            value="Павел",
            evidence="Павел",
        ),
        FactCandidate(
            path="request.amount",
            value=800_000,
            evidence="800000",
        ),
        FactCandidate(
            path="client.goal",
            value="cash",
            source=FactSource.SYSTEM_DERIVED,
            evidence="800000",
        ),
    ]
    second_candidates = [
        FactCandidate(path="client.age", value=42, evidence="42"),
        FactCandidate(
            path="client.city",
            value="Москва",
            evidence="Москва",
        ),
        FactCandidate(
            path="debts.has_active_credits",
            value=False,
            evidence="кредитов нет",
        ),
        FactCandidate(
            path="employment.type",
            value="official",
            evidence="официально",
        ),
        FactCandidate(
            path="employment.income",
            value=150_000,
            evidence="150000",
        ),
        FactCandidate(
            path="assets.has_realty",
            value=True,
            evidence="Объект",
        ),
        FactCandidate(
            path="assets.realty.city",
            value="Москва",
            evidence="Москва",
        ),
        FactCandidate(
            path="assets.realty.kind",
            value="flat",
            evidence="квартира",
        ),
        FactCandidate(
            path="assets.realty.ownership_form",
            value="private",
            evidence="частная",
        ),
        FactCandidate(
            path="assets.realty.owners",
            value="client_only",
            evidence="только я",
        ),
        FactCandidate(
            path="assets.has_vehicle",
            value=False,
            evidence="машины нет",
        ),
    ]
    return {
        "scenario_id": "replay_realty",
        "turns": [
            {
                "user": None,
                "public_state": {},
            },
            {
                "user": (
                    "Клиент Павел, телефон +79990000000, требуется 800000."
                ),
                "public_state": {
                    "facts": first_facts,
                    "plan": _plan_snapshot(plan(matrix, first_facts)),
                    "interpretation": {
                        "last_result": {
                            "candidates": [
                                item.model_dump(mode="json")
                                for item in first_candidates
                            ]
                        }
                    },
                },
            },
            {
                "user": (
                    "Возраст 42, город Москва, действующих кредитов нет, "
                    "работаю официально, доход 150000. Объект квартира в Москве, "
                    "частная, собственник только я, машины нет."
                ),
                "public_state": {
                    "facts": final_facts,
                    "plan": _plan_snapshot(plan(matrix, final_facts)),
                    "interpretation": {
                        "last_result": {
                            "candidates": [
                                item.model_dump(mode="json")
                                for item in second_candidates
                            ]
                        }
                    },
                },
            },
        ],
    }


def test_replay_accumulates_independent_state_across_turns(matrix):
    result = replay_dialog_shadow(
        _artifact(matrix),
        expected_route_id="realty_msk",
        matrix=matrix,
    )

    assert result.route_matches is True
    assert result.matching_turns == 2
    assert result.mismatches == []


def test_replay_exposes_missing_fact_hidden_by_legacy_state(matrix):
    artifact = _artifact(matrix)
    candidates = artifact["turns"][1]["public_state"]["interpretation"][
        "last_result"
    ]["candidates"]
    artifact["turns"][1]["public_state"]["interpretation"]["last_result"][
        "candidates"
    ] = [
        item
        for item in candidates
        if item["path"] != "request.amount"
    ]

    result = replay_dialog_shadow(
        artifact,
        expected_route_id="realty_msk",
        matrix=matrix,
    )
    summary = aggregate_shadow_replays([result])

    assert result.route_matches is False
    assert result.independent_missing_fields == ["request.amount"]
    assert summary["turn_plan_match_rate"] < 1.0
    assert summary["failed_scenario_ids"] == ["replay_realty"]
