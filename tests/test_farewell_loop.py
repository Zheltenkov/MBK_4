"""После финала бот замолкает, а не прощается по кругу.

Ролевой прогон 19.08.2026, роль «отвечает не на тот вопрос». Анкета дошла до
конца на 11-м ходу, дальше двенадцать прощаний подряд:

    К: Ага.   Б: Хорошего дня!
    К: Ага.   Б: До связи!
    К: Ага.   Б: Берегите себя!
    К: Ага.   Б: До свидания!

Каждое «Ага» получало новое «всего доброго», пока не упёрся потолок ходов. Эвал
записал это как «анкета не дошла до конца», а в бою это лишние вызовы модели за
наши деньги и странное впечатление у клиента: живой человек так не прощается.

Разговор закончен — значит закончен. Но замолкаем только на пустых поддакиваниях:
вопрос клиента и новые сведения по-прежнему получают ответ.
"""
from __future__ import annotations

import pytest

from core import conversation_is_over
from state import init_state


def _finished(phase: str = "survey_completed") -> dict:
    state = init_state()
    state["facts"] = {"client": {"name": "Марина", "phone": "+79000000006"}}
    state["dialog_phase"] = phase
    return state


@pytest.mark.parametrize(
    "message",
    ["Ага", "ага", "Спасибо.", "спасибо!", "ок", "Хорошо", "понял", "Угу", "Пока", "Всего доброго", "👍"],
)
def test_bare_acknowledgement_after_the_end_gets_no_reply(message):
    assert conversation_is_over(_finished(), message) is True


@pytest.mark.parametrize("phase", ["survey_completed", "no_fit", "link_sent"])
def test_every_terminal_phase_counts(phase):
    assert conversation_is_over(_finished(phase), "ага") is True


@pytest.mark.parametrize(
    "message, why",
    [
        ("А когда придёт ссылка?", "вопрос клиента"),
        ("Спасибо! А если я найду поручителя?", "вопрос под видом благодарности"),
        ("Забыл сказать, у меня ещё машина есть", "новые сведения"),
        ("Мой номер +7 999 123 45 67", "клиент передумал насчёт телефона"),
        ("ага, а что с квартирой", "поддакивание с продолжением"),
    ],
)
def test_a_substantive_message_is_still_answered(message, why):
    assert conversation_is_over(_finished(), message) is False, why


@pytest.mark.parametrize("phase", ["qualification", "awaiting_contacts", "awaiting_site_consent"])
def test_mid_questionnaire_nothing_is_ignored(phase):
    """Посреди анкеты «ага» — это ответ на вопрос, а не прощание."""
    assert conversation_is_over(_finished(phase), "ага") is False


def test_empty_message_is_not_a_farewell():
    assert conversation_is_over(_finished(), "") is False


def test_the_turn_handler_calls_no_model_at_all(monkeypatch):
    """Проверка стоит до разбора: за «спасибо» не должно платиться дважды.

    Раньше каждое поддакивание после финала стоило вызова извлекателя и вызова
    разговорной модели. Здесь оба подменены на взрыв: если бот их тронет, тест
    упадёт.
    """
    import asyncio
    import json

    import main
    from session_store import Session
    from usage_tracker import SessionUsage

    def _boom(*args, **kwargs):
        raise AssertionError("модель не должна вызываться после финала")

    monkeypatch.setattr(main, "prepare_turn", _boom)
    monkeypatch.setattr(main, "stream_reply", _boom)

    class _Store:
        def persist(self, session) -> None:
            pass

    monkeypatch.setattr(main, "_store", lambda: _Store())

    state = init_state()
    state["facts"] = {"client": {"name": "Марина", "phone": "+79000000006"}}
    state["dialog_phase"] = "survey_completed"
    state["chat_history"] = [{"role": "assistant", "content": "Спасибо, заявка принята."}]
    session = Session(
        id="fin123",
        state=state,
        anketa=None,
        usage=SessionUsage(session_id="fin123"),
    )

    class _NeverDisconnects:
        async def is_disconnected(self) -> bool:
            return False

    async def _collect():
        return [
            event
            async for event in main._run_turn_sse_unlocked(
                session, "Ага", False, _NeverDisconnects()
            )
        ]

    events = asyncio.run(_collect())

    # Событие с репликами приходит всегда — иначе виджет заказчика навсегда
    # оставит индикатор набора. Просто список пустой.
    assert [e["event"] for e in events] == ["bubbles", "state", "done"]
    assert json.loads(events[0]["data"])["bubbles"] == []
    # Реплика клиента всё равно записана: следующий содержательный вопрос
    # модель должна увидеть в полном разговоре, а не с дырой.
    assert session.state["chat_history"][-1] == {"role": "user", "content": "Ага"}
