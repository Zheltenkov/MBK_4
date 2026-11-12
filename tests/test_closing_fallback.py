"""После финала запасная фраза не должна обещать продолжение анкеты.

Когда предохранители вырезают из ответа модели всё — внутренние термины,
обещание одобрения, самодельную ссылку, — вместо пустоты подставляется
нейтральная фраза: «Давайте продолжим — мне нужно ещё немного данных по вашей
ситуации».

В середине анкеты это верно. После отправки лида — ложь: данных больше не
нужно, разговор закончен, и клиент уходит ждать вопросов, которых не будет.
Попадает сюда частый финальный вопрос «а куда уходят мои данные»: модель
отвечает про базу и передачу партнёру, фильтр внутренних терминов вырезает
ответ целиком, и остаётся обещание продолжить.
"""
from __future__ import annotations

import pytest

from utils import guard_bubbles


# Реплика, которую предохранитель вырезает целиком: внутренние термины.
INTERNAL = ["Ваши данные ушли в нашу базу и в маршрут партнёра по матрице."]


def test_mid_questionnaire_fallback_keeps_the_conversation_going():
    bubbles = guard_bubbles(INTERNAL)

    assert bubbles, "молчать нельзя — клиент останется без ответа"
    assert "продолжим" in bubbles[0].lower()


def test_after_the_finale_nothing_is_promised():
    bubbles = guard_bubbles(INTERNAL, conversation_over=True)

    assert bubbles, "молчать нельзя и здесь"
    text = bubbles[0].lower()
    assert "продолжим" not in text, f"обещание продолжить анкету после финала: {bubbles[0]}"
    assert "данных" not in text or "нужно" not in text, (
        f"обещание собрать ещё данные после финала: {bubbles[0]}"
    )


def test_clean_reply_is_untouched_in_both_modes():
    clean = ["Спасибо, всё передал.", "Хорошего дня!"]

    assert guard_bubbles(clean) == clean
    assert guard_bubbles(clean, conversation_over=True) == clean


@pytest.mark.parametrize("phase", ["survey_completed", "no_fit", "link_sent"])
def test_the_turn_passes_the_phase_down(phase):
    """Правка бесполезна, если признак не доедет из хода до предохранителя."""
    import core
    from state import init_state

    state = init_state()
    state["dialog_phase"] = phase
    state["facts"] = {"client": {"name": "Олег", "phone": "+79990000000"}}

    committed = core.commit_turn(state, "а куда уходят мои данные?", INTERNAL[0])

    assert "продолжим" not in " ".join(committed["messages"]).lower()
