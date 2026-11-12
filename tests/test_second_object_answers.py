"""Ответ на вопрос о втором объекте недвижимости — это ответ о втором объекте.

Бой 22–24.10.2026 (сессии 29aa94b4a6aa, adf61fafade0): бот спрашивал о форме
собственности и собственниках второго объекта, клиент отвечал «Да частная
собстенность», «Мой», «Только я», а извлекатель клал ответ в поля первого
объекта (`assets.realty.*`). Второй объект оставался пустым, план повторял
вопрос, модель считала его отвеченным, уходила с плана и объявляла заявку
переданной — лид не создавался. Проба 24.10.2026 на боевых ответах: так 6
ответов из 8.
"""
from __future__ import annotations

from typing import Any

import pytest

import core
from config import AppConfig
from interpretation import FactCandidate, FactCandidateExtraction
from state import init_state

LEGACY = AppConfig(openrouter_api_key="sk-or-test", fact_interpretation_mode="legacy")
SEMANTIC = AppConfig(openrouter_api_key="sk-or-test", fact_interpretation_mode="semantic")

TWO_OBJECTS = {
    "client": {"name": "Оксана", "age": 45, "region_class": "rf_other"},
    "request": {"amount": 500_000},
    "assets": {
        "has_realty": True,
        "realty": {
            "kind": "flat",
            "ownership_form": "municipal",
            "owners": "client_and_spouse",
            "region_class": "rf_other",
            "needs_second": True,
        },
        "has_second_realty": True,
        "realty_2": {"kind": "house", "region": "Курская область"},
    },
}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *a, **k: {"fact_updates": [], "internal_summary": ""},
    )
    monkeypatch.setattr(
        core.llm_agent,
        "extract_fact_candidates",
        lambda *a, **k: FactCandidateExtraction(),
    )
    monkeypatch.setenv("MBK_ROUTER_MOCK", "1")
    monkeypatch.delenv("MBK_DADATA_API", raising=False)


def _asking(paths: list[str]) -> dict:
    import copy

    state = init_state()
    state["session_id"] = "second-object"
    state["facts"] = copy.deepcopy(TWO_OBJECTS)
    state["script"] = {"asked": list(paths), "attempts": {}}
    state["entry"] = {"scenario": "questionnaire"}
    return state


def _extractor_answers(
    monkeypatch, config: AppConfig, updates: list[tuple[str, Any]], evidence: str
) -> None:
    """Подменяет извлекатель нужного режима: он «видит» ответ, но не тот объект."""
    if config.fact_interpretation_mode == "semantic":
        extraction = FactCandidateExtraction(
            candidates=[FactCandidate(path=p, value=v, evidence=evidence) for p, v in updates]
        )
        monkeypatch.setattr(core.llm_agent, "extract_fact_candidates", lambda *a, **k: extraction)
    else:
        legacy = {
            "fact_updates": [{"path": p, "value": v, "confidence": "high"} for p, v in updates],
            "internal_summary": "",
        }
        monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *a, **k: legacy)


@pytest.mark.parametrize("config", [LEGACY, SEMANTIC], ids=["legacy", "semantic"])
@pytest.mark.parametrize(
    ("asked", "answer", "updates", "expected"),
    [
        (
            ["assets.realty_2.ownership_form"],
            "Да частная собстенность",
            [("assets.realty.ownership_form", "private")],
            {"ownership_form": "private"},
        ),
        (
            ["assets.realty_2.ownership_form", "assets.realty_2.owners"],
            "Квартира приватизировать, я единственный собственник",
            [("assets.realty.ownership_form", "private"), ("assets.realty.owners", "client_only")],
            {"ownership_form": "private", "owners": "client_only"},
        ),
        (
            ["assets.realty_2.owners"],
            "Да один владелец я",
            [("assets.realty.owners", "client_only"), ("assets.has_realty", True)],
            {"owners": "client_only"},
        ),
    ],
)
def test_an_answer_about_the_second_object_lands_on_the_second_object(
    config, asked, answer, updates, expected, monkeypatch
):
    _extractor_answers(monkeypatch, config, updates, answer)

    prepared = core.prepare_turn(_asking(asked), answer, config, submit_delivery=False)

    second = prepared["facts"]["assets"]["realty_2"]
    for name, value in expected.items():
        assert second[name] == value
    first = prepared["facts"]["assets"]["realty"]
    assert first["ownership_form"] == "municipal"
    assert first["owners"] == "client_and_spouse"


def test_semantic_mode_moves_a_bare_mine_to_the_second_object(monkeypatch):
    """«Мой» в бою semantic записывает как частную — теперь во второй объект."""
    _extractor_answers(monkeypatch, SEMANTIC, [("assets.realty.ownership_form", "private")], "Мой")

    prepared = core.prepare_turn(
        _asking(["assets.realty_2.ownership_form"]), "Мой", SEMANTIC, submit_delivery=False
    )

    assert prepared["facts"]["assets"]["realty_2"]["ownership_form"] == "private"
    assert prepared["facts"]["assets"]["realty"]["ownership_form"] == "municipal"


def test_legacy_still_does_not_read_mine_as_private_on_the_second_object(monkeypatch):
    """Перенос не обходит фильтр догадок: «Мой» не значит «частная» ни у
    первого объекта, ни у второго."""
    _extractor_answers(monkeypatch, LEGACY, [("assets.realty.ownership_form", "private")], "Мой")

    prepared = core.prepare_turn(
        _asking(["assets.realty_2.ownership_form"]), "Мой", LEGACY, submit_delivery=False
    )

    assert "ownership_form" not in prepared["facts"]["assets"]["realty_2"]
    assert prepared["facts"]["assets"]["realty"]["ownership_form"] == "municipal"


@pytest.mark.parametrize("config", [LEGACY, SEMANTIC], ids=["legacy", "semantic"])
def test_a_field_the_model_already_put_on_the_second_object_wins(config, monkeypatch):
    """Клиент сам развёл объекты: «первая муниципальная, а эта частная»."""
    answer = "Первая муниципальная, а эта частная"
    _extractor_answers(
        monkeypatch,
        config,
        [("assets.realty.ownership_form", "municipal"), ("assets.realty_2.ownership_form", "private")],
        answer,
    )

    prepared = core.prepare_turn(
        _asking(["assets.realty_2.ownership_form"]), answer, config, submit_delivery=False
    )

    assert prepared["facts"]["assets"]["realty_2"]["ownership_form"] == "private"
    assert prepared["facts"]["assets"]["realty"]["ownership_form"] == "municipal"


@pytest.mark.parametrize("config", [LEGACY, SEMANTIC], ids=["legacy", "semantic"])
def test_a_question_about_the_first_object_is_not_redirected(config, monkeypatch):
    # «Да частная» быстрый разбор не берёт — проверяется путь модели.
    answer = "Да частная"
    state = _asking(["assets.realty.ownership_form"])
    del state["facts"]["assets"]["realty"]["ownership_form"]
    _extractor_answers(monkeypatch, config, [("assets.realty.ownership_form", "private")], answer)

    prepared = core.prepare_turn(state, answer, config, submit_delivery=False)

    assert prepared["facts"]["assets"]["realty"]["ownership_form"] == "private"
    assert "ownership_form" not in prepared["facts"]["assets"]["realty_2"]


@pytest.mark.parametrize("config", [LEGACY, SEMANTIC], ids=["legacy", "semantic"])
def test_a_client_who_names_the_first_object_is_not_redirected(config, monkeypatch):
    """«Первая тоже частная» на вопросе о втором объекте — поправка первого."""
    answer = "Первая тоже частная, а эту не знаю"
    _extractor_answers(monkeypatch, config, [("assets.realty.ownership_form", "private")], answer)

    prepared = core.prepare_turn(
        _asking(["assets.realty_2.ownership_form"]), answer, config, submit_delivery=False
    )

    assert "ownership_form" not in prepared["facts"]["assets"]["realty_2"]
