"""Развилка внутри хода: откуда берётся намерение и когда решение принимается."""
from __future__ import annotations

import pytest

import core
import entry_routing as er
from config import AppConfig
from interpretation import FactCandidateExtraction
from prompts import OPENING_MESSAGE
from routing.entry_intent import EntryIntent
from state import init_state


def _config(mode: str = "semantic", *, enabled: bool = True) -> AppConfig:
    return AppConfig(
        openrouter_api_key="sk-or-test",
        fact_interpretation_mode=mode,
        entry_routing_enabled=enabled,
    )


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("MBK_DADATA_API", raising=False)
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *a, **k: {"fact_updates": [], "internal_summary": ""},
    )


def _model_says(monkeypatch, intent):
    monkeypatch.setattr(
        core.llm_agent,
        "extract_fact_candidates",
        lambda *a, **k: FactCandidateExtraction(entry_intent=intent),
    )


def _fresh_state() -> dict:
    state = init_state()
    state["session_id"] = "entry-turn"
    state["chat_history"] = [{"role": "assistant", "content": OPENING_MESSAGE}]
    return state


def _in_branch(scenario: str) -> dict:
    state = _fresh_state()
    state["chat_history"].append({"role": "user", "content": "первая реплика"})
    state["chat_history"].append({"role": "assistant", "content": "ответ ветки"})
    state["entry"] = {"scenario": scenario, "intent": scenario, "source": "model"}
    return state


def _prepare(state, message, config):
    return core.prepare_turn(state, message, config, submit_delivery=False)


@pytest.mark.parametrize(
    ("intent", "scenario"),
    [
        (EntryIntent.EXISTING, er.EXISTING),
        (EntryIntent.OFF_TOPIC, er.OFF_TOPIC),
        (EntryIntent.MONEY, er.QUESTIONNAIRE),
        (EntryIntent.UNCLEAR, er.QUESTIONNAIRE),
    ],
)
def test_first_message_is_routed_by_the_model(monkeypatch, intent, scenario):
    _model_says(monkeypatch, intent)

    prepared = _prepare(_fresh_state(), "Добрый день, у меня вопрос", _config())

    assert prepared["entry"] == {
        "scenario": scenario,
        "intent": intent.value,
        "source": "model",
    }
    assert prepared[er.TRANSITION_KEY]["scenario"] == scenario


def test_silent_model_leaves_the_decision_to_the_dictionary(monkeypatch):
    _model_says(monkeypatch, None)

    prepared = _prepare(
        _fresh_state(), "Оставлял заявку, когда позвонит менеджер?", _config()
    )

    assert prepared["entry"]["scenario"] == er.EXISTING
    assert prepared["entry"]["source"] == "dictionary"


def test_failed_model_leaves_the_decision_to_the_dictionary(monkeypatch):
    def broken(*args, **kwargs):
        raise TimeoutError("модель не ответила")

    monkeypatch.setattr(core.llm_agent, "extract_fact_candidates", broken)

    prepared = _prepare(_fresh_state(), "Что по моей заявке?", _config())

    assert prepared["entry"] == {
        "scenario": er.EXISTING,
        "intent": "existing",
        "source": "dictionary",
    }


def test_shadow_extractor_does_not_route_the_live_conversation(monkeypatch):
    """В shadow новый извлекатель диагностический: ни факты, ни намерение
    из него не влияют на разговор."""
    _model_says(monkeypatch, EntryIntent.OFF_TOPIC)

    prepared = _prepare(_fresh_state(), "Добрый день, у меня вопрос", _config("shadow"))

    assert prepared["entry"] == {
        "scenario": er.QUESTIONNAIRE,
        "intent": "unclear",
        "source": "dictionary",
    }


def test_legacy_mode_routes_by_the_dictionary(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("в legacy новый извлекатель не зовут")

    monkeypatch.setattr(core.llm_agent, "extract_fact_candidates", unexpected)

    prepared = _prepare(_fresh_state(), "Подскажите статус заявки", _config("legacy"))

    assert prepared["entry"]["scenario"] == er.EXISTING
    assert prepared["entry"]["source"] == "dictionary"


def test_session_mid_questionnaire_is_not_reclassified(monkeypatch):
    _model_says(monkeypatch, EntryIntent.EXISTING)
    state = _fresh_state()
    state["chat_history"].append({"role": "user", "content": "Нужны деньги"})
    state["chat_history"].append({"role": "assistant", "content": "Какая сумма нужна?"})

    prepared = _prepare(state, "Я же заявку уже оставлял", _config())

    assert "entry" not in prepared
    assert er.TRANSITION_KEY not in prepared


def test_money_in_a_branch_returns_to_the_questionnaire(monkeypatch):
    _model_says(monkeypatch, EntryIntent.MONEY)

    prepared = _prepare(_in_branch(er.OFF_TOPIC), "а вообще нужно 200 тысяч", _config())

    assert prepared["entry"]["scenario"] == er.QUESTIONNAIRE
    assert prepared[er.TRANSITION_KEY]["scenario"] == er.QUESTIONNAIRE


def test_unclear_reply_keeps_the_branch(monkeypatch):
    _model_says(monkeypatch, EntryIntent.UNCLEAR)

    prepared = _prepare(_in_branch(er.EXISTING), "ну ладно", _config())

    assert prepared["entry"]["scenario"] == er.EXISTING
    assert er.TRANSITION_KEY not in prepared


def test_switching_the_flag_off_drops_the_branch(monkeypatch):
    """Выключенный флаг — поведение без развилки целиком, в том числе для
    сессии, попавшей в ветку, пока флаг был включён."""
    _model_says(monkeypatch, EntryIntent.EXISTING)

    prepared = _prepare(_in_branch(er.EXISTING), "ну ладно", _config(enabled=False))

    assert "entry" not in prepared
    assert er.TRANSITION_KEY not in prepared


def _route_found_everywhere(monkeypatch) -> list:
    """План всегда «маршрут найден», доставка записывает вызовы вместо сети."""
    from types import SimpleNamespace

    from routing import Plan, Route, RouteStatus

    route = Route(
        id="realty_msk",
        priority="1-1",
        product="Залог недвижимости",
        segment="Мск/МО",
        family="realty",
    )
    matched = RouteStatus(route=route, state="PASS")
    found = Plan(action="route_found", statuses=[matched], matched=matched)
    monkeypatch.setattr(core, "build_plan", lambda *a, **k: found)
    calls: list = []

    def fake_submit(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(as_dict=lambda: {"status": "completed"})

    monkeypatch.setattr(core.delivery, "submit", fake_submit)
    return calls


@pytest.mark.parametrize("scenario", [er.EXISTING, er.OFF_TOPIC])
def test_local_delivery_is_not_submitted_in_a_branch(monkeypatch, scenario):
    """Ни в «уже оставлял заявку», ни в «не наш профиль» локальная доставка
    не срабатывает."""
    calls = _route_found_everywhere(monkeypatch)
    _model_says(monkeypatch, EntryIntent.UNCLEAR)

    core.prepare_turn(_in_branch(scenario), "ок", _config(), submit_delivery=True)

    assert calls == []


def test_local_delivery_still_works_in_the_questionnaire(monkeypatch):
    calls = _route_found_everywhere(monkeypatch)
    _model_says(monkeypatch, EntryIntent.UNCLEAR)

    core.prepare_turn(
        _in_branch(er.QUESTIONNAIRE), "ок", _config(), submit_delivery=True
    )

    assert len(calls) == 1


def test_off_topic_with_money_facts_in_the_same_answer_keeps_the_questionnaire(
    monkeypatch,
):
    from interpretation.contracts import SemanticBoundarySignals

    monkeypatch.setattr(
        core.llm_agent,
        "extract_fact_candidates",
        lambda *a, **k: FactCandidateExtraction(
            entry_intent=EntryIntent.OFF_TOPIC,
            boundaries=SemanticBoundarySignals(goal="debt_relief"),
        ),
    )

    prepared = _prepare(
        _fresh_state(), "Хочу досрочно погасить кредит в другом банке", _config()
    )

    assert prepared["entry"] == {
        "scenario": er.QUESTIONNAIRE,
        "intent": "unclear",
        "source": "model",
    }
