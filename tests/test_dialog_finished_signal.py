"""Завершённый разговор должен быть виден снаружи.

Ролевой прогон считал диалог законченным только по созданному лиду или
подобранному маршруту. Клиент, которому решения не нашлось, ни того ни другого
не получает: маршрута нет, доставка не заводится — и прогон крутил ходы в
тишину до потолка, записывая «анкета не дошла до конца» вполне отработавшему
диалогу.

С 19.08.2026 стало нагляднее: после финала бот молчит на поддакивания, поэтому
хвост таких прогонов — это буквально пустые ходы.

Признак завершения у продукта уже есть — фаза диалога. Наружу она не отдавалась,
хотя это ровно тот вопрос, который задаёт любой клиент чата: разговор ещё идёт
или уже кончился.
"""
from __future__ import annotations

import pytest

from evaluation.roleplay import _is_finished


@pytest.mark.parametrize("phase", ["survey_completed", "no_fit", "link_sent"])
def test_terminal_phase_means_the_dialogue_is_over(phase):
    assert _is_finished({"dialog_phase": phase}) is True


@pytest.mark.parametrize("phase", ["qualification", "awaiting_contacts", "awaiting_site_consent"])
def test_mid_dialogue_phase_is_not_the_end(phase):
    assert _is_finished({"dialog_phase": phase}) is False


def test_created_lead_still_counts():
    assert _is_finished({"crm_lead": {"status": "submitted"}}) is True


def test_delivered_link_still_counts():
    assert _is_finished({"delivery": {"status": "link"}}) is True


def test_empty_state_is_not_the_end():
    assert _is_finished({}) is False


def test_public_state_exposes_the_phase():
    """Без этого проверка выше не сработает: эвал читает публичный снимок."""
    import main
    from session_store import Session
    from state import init_state
    from usage_tracker import SessionUsage

    state = init_state()
    state["dialog_phase"] = "survey_completed"
    session = Session(id="p1", state=state, anketa=None, usage=SessionUsage(session_id="p1"))

    assert main._public_state(session)["dialog_phase"] == "survey_completed"
