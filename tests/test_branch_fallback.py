"""Запасные фразы фильтра в ветках развилки не зовут в анкету и не обещают."""
from __future__ import annotations

import pytest

import core
import entry_routing as er
from state import init_state
from utils import _BRANCH_FALLBACK, _PROMISE_FALLBACK, guard_bubbles


def test_branch_fallbacks_cover_exactly_the_branches():
    """Ключи — строки: utils ниже по слоям, чем entry_routing. Сверяем здесь."""
    assert set(_BRANCH_FALLBACK) == {er.EXISTING, er.OFF_TOPIC}


def test_honest_answer_in_the_existing_branch_is_preserved():
    """A truthful model statement keeps its own words instead of becoming a fallback."""
    result = guard_bubbles(
        ["Статус вашей заявки я отсюда не вижу."], scenario=er.EXISTING
    )

    assert result == ["Статус вашей заявки я отсюда не вижу."]
    assert "подбор" not in " ".join(result)


def test_link_cut_in_off_topic_does_not_become_a_guarantee_disclaimer():
    result = guard_bubbles(
        ["Зайдите на www.sberbank.ru и напишите в поддержку."], scenario=er.OFF_TOPIC
    )

    assert result == list(_BRANCH_FALLBACK[er.OFF_TOPIC])


@pytest.mark.parametrize("scenario", [er.EXISTING, er.OFF_TOPIC])
def test_branch_fallback_passes_its_own_filters(scenario):
    fallback = list(_BRANCH_FALLBACK[scenario])

    assert guard_bubbles(fallback, scenario=scenario) == fallback


def test_questionnaire_keeps_its_fallbacks():
    assert guard_bubbles(["Посмотрю вашу заявку."], scenario=er.QUESTIONNAIRE) == list(
        _PROMISE_FALLBACK
    )
    assert guard_bubbles(["Посмотрю вашу заявку."]) == list(_PROMISE_FALLBACK)


def test_commit_turn_preserves_the_honest_branch_answer():
    state = init_state()
    state["entry"] = {"scenario": er.EXISTING, "intent": "existing", "source": "model"}

    committed = core.commit_turn(
        state, "Что с моей заявкой?", "Статус вашей заявки я отсюда не вижу."
    )

    assert committed["messages"] == ["Статус вашей заявки я отсюда не вижу."]


def test_branch_fallback_phrases_make_no_false_promises():
    """Ветка — это не создание лида. Запасные фразы не должны обещать то, чего
    не будет: продолжение подбора, передачу данных, звонок менеджера, тон «мы
    работаем с вашей заявкой». Для ветки «уже оставлял» это критично: человек
    спрашивает о ЧУЖОЙ заявке, и бот ничего о ней не знает и не обещает."""
    forbidden_stems = {"подбор", "работают", "передан", "перезвон", "свяж", "менеджер"}

    for scenario, phrases in _BRANCH_FALLBACK.items():
        text = " ".join(phrases).lower()
        for stem in forbidden_stems:
            assert (
                stem not in text
            ), f"{scenario} fallback contains forbidden stem '{stem}': {phrases}"
