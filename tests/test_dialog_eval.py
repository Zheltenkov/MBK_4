"""Offline проверки сквозного dialog eval harness."""
from __future__ import annotations

from pathlib import Path

from evaluation.coherence import _has_bad_news
from evaluation.dialogs import (
    DialogTurn,
    _coherence_turns,
    _get_by_path,
    _opening_token,
    load_dialog_scenarios,
)
from prompts import OPENING_MESSAGE


CASES = Path(__file__).resolve().parents[1] / "evaluation" / "dialog_cases.json"
VARIANT_CASES = (
    Path(__file__).resolve().parents[1]
    / "evaluation"
    / "dialog_variation_cases.json"
)


def test_dialog_scenarios_are_strictly_valid():
    scenarios = load_dialog_scenarios(CASES)

    assert len(scenarios) == 15
    # Все 12 маршрутов матрицы имеют живой разговорный сценарий. credit_club (1-3)
    # достижим с тех пор, как у 1-2 и 1-3 появились собственные списки регионов.
    assert {scenario.expected_route_id for scenario in scenarios} == {
        "realty_msk",
        "realty_rf",
        "credit_club",
        "bfl_msk",
        "bfl_rf",
        "bfl_cpa",
        "auto_api_msk",
        "auto_api_rf",
        "auto_msk",
        "auto_rf",
        "consumer",
        "consumer_second",
    }
    assert all(scenario.id for scenario in scenarios)


def test_get_by_path_handles_nested_and_missing_values():
    data = {"client": {"name": "Василий"}}

    assert _get_by_path(data, "client.name") == "Василий"
    assert _get_by_path(data, "client.phone") is None
    assert _get_by_path(data, "client.name.value") is None


def test_opening_token_normalizes_punctuation_and_case():
    assert _opening_token("Понял, давайте разберёмся.") == "понял"
    assert _opening_token("  Ирина! Всё хорошо.") == "ирина"


def _turn(user: str | None, bubbles: list[str]) -> DialogTurn:
    return DialogTurn(
        user=user,
        assistant_bubbles=bubbles,
        assistant_text="\n\n".join(bubbles),
        latency_sec=0.0,
        mikhail_legacy_score=100,
        mikhail_legacy_reasons=[],
        v4_humanity_score=100,
        v4_humanity_reasons=[],
        public_state={},
    )


def test_coherence_turns_skip_the_opening_message():
    """Opening не является реакцией на клиента и не должен попадать в оценку."""
    turns = [
        _turn(None, [OPENING_MESSAGE]),
        _turn("Здравствуйте.", ["Как к вам обращаться?"]),
    ]

    coherence = _coherence_turns(turns)

    assert [item.user for item in coherence] == ["Здравствуйте."]
    assert coherence[0].bubbles == ["Как к вам обращаться?"]


def test_distress_scenarios_guard_their_coherence():
    """Порог стоит там, где клиент сообщает о беде: именно эти четыре сценария
    просели до 50-75 в прогоне 20260728_200727 из-за оценочных зачинов.

    80, а не 100: тональный штраф (-25) порог пробивает, а единичный дословный
    повтор (-15) — нет. Иначе проверка ловила бы нестабильность модели вместо
    регресса.
    """
    scenarios = load_dialog_scenarios(CASES)
    guarded = {
        scenario.id
        for scenario in scenarios
        if scenario.min_coherence_score is not None
    }

    assert guarded == {
        "refuses_exact_debt",
        "emotional_debt_kazan",
        "bfl_cpa_small_debt",
        "consumer_second_small_amount",
    }
    assert all(
        scenario.min_coherence_score == 80
        for scenario in scenarios
        if scenario.id in guarded
    )


def test_every_distress_scenario_is_guarded():
    """Новый сценарий с плохой новостью обязан получить порог — иначе дефект,
    ради которого метрика заводилась, снова окажется неохраняемым."""
    scenarios = load_dialog_scenarios(CASES)

    for scenario in scenarios:
        has_distress = any(_has_bad_news(message) for message in scenario.messages)
        if has_distress:
            assert scenario.min_coherence_score is not None, scenario.id


def test_dialog_variants_are_disjoint_holdout_scenarios():
    main = load_dialog_scenarios(CASES)
    variants = load_dialog_scenarios(VARIANT_CASES)

    assert len(variants) == 4
    assert not {item.id for item in main} & {item.id for item in variants}
    assert {item.expected_route_id for item in variants} == {
        "bfl_msk",
        "auto_msk",
        "realty_msk",
        "consumer",
    }
