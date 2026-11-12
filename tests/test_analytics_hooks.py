"""Крючки событий: где именно бот отмечает шаги воронки.

Проверяется не сам SQL, а то, что событие возникает в нужный момент и несёт
поля, по которым дата-инженер соберёт шесть чисел заказчика.
"""
from __future__ import annotations

import pytest

import analytics
import core
from analytics import EVENTS, AnalyticsRecorder, MemorySink
from analytics import EVENTS as E
from state import init_state


def test_event_catalogue_covers_only_what_we_own():
    """Считаем только своё; номер лида — исключение, и оно объяснимо.

    Разделение зон 30.07.2026 (скриншот заказчика): старт чата и количество
    квалификаций — с нашей стороны, собранные номера, «подошедшие по
    категории» и «отправленные по микросервису» — со стороны CRM. Два
    источника для одного числа не сойдутся, и расхождение ищут долго.

    `LEAD_SUBMITTED` появилось 04.09.2026 и правило не отменяет: это не
    счётчик, а ключ связи. Дата-инженер заказчика не мог сопоставить разговор
    с карточкой в Битриксе, потому что `sessions` ему закрыта (там ПД), а в
    переписке от телефона остаётся отпечаток. Backend заказчика в том же
    обсуждении: «достаточно добавить lead id столбец, в базе бота — это
    правильное архитектурное решение».

    `ENTRY_ROUTED` и `EXISTING_CLIENT_REQUEST` появились 10.09.2026 вместе с
    развилкой на входе. Какой сценарий ведёт разговор, CRM не знает — считать
    это больше некому, так что правило «CRM считает своё» не нарушено.

    `QUICK_REPLY_USED` появилось 14.09.2026 вместе с чипсами в приветствии:
    нажатие кнопки происходит в нашем виджете, CRM о нём не знает.

    `CRM_CALL_FAILED` появилось 19.09.2026: сколько раз НАШ вызов CRM не
    дошёл или получил ответ, не прошедший наш контракт, знаем только мы —
    CRM не считает попытки, которые до неё не дошли или которые она сама
    отвергла. Число лидов по-прежнему считает CRM, здесь его нет.
    """
    assert EVENTS.CHAT_STARTED == "chat_started"
    assert EVENTS.QUALIFICATION_COMPLETED == "qualification_completed"
    assert EVENTS.TURN_COMPLETED == "turn_completed"
    assert EVENTS.LEAD_SUBMITTED == "lead_submitted"
    assert EVENTS.ENTRY_ROUTED == "entry_routed"
    assert EVENTS.EXISTING_CLIENT_REQUEST == "existing_client_request"
    assert EVENTS.QUICK_REPLY_USED == "quick_reply_used"
    assert EVENTS.CRM_CALL_FAILED == "crm_call_failed"
    # Эти по-прежнему считает CRM, и константы для них заводить нельзя:
    # неиспользуемое имя рано или поздно попадёт в чей-нибудь отчёт.
    for gone in ("LEAD_REJECTED", "LINK_DELIVERED",
                 "DELIVERY_FAILED", "LINK_OPENED", "GEO_UNRESOLVED"):
        assert not hasattr(EVENTS, gone), gone


def test_global_recorder_defaults_to_disabled():
    """Импорт модуля не должен включать запись сам по себе."""
    analytics.set_recorder(None)

    assert analytics.get_recorder().enabled is False


def test_global_recorder_can_be_swapped_for_tests():
    sink = MemorySink()
    analytics.set_recorder(AnalyticsRecorder(sink))
    try:
        analytics.get_recorder().emit(EVENTS.CHAT_STARTED, "abc123", {})
        assert [e.event for e in sink.events] == ["chat_started"]
    finally:
        analytics.set_recorder(None)


def test_crm_call_failed_payload_never_exceeds_the_documented_keys():
    """Пять полей и ничего больше.

    Свободный текст ответа CRM сюда попасть в принципе не должен — вызывающий
    код кладёт только `stage`/`reason`/`retryable`/`http_status`/`error_class`,
    — но это ручной контракт по обе стороны двух модулей (crm_dialog_workflow,
    lead_intake_coordinator), и лишний ключ, который однажды расползётся из
    чьего-то except-блока, обязан падать на тесте, а не расширять таблицу
    молча.
    """
    from crm_dialog_workflow import _record_crm_call_failed as workflow_emit
    from lead_intake_coordinator import _record_crm_call_failed as coordinator_emit

    sink = MemorySink()
    analytics.set_recorder(AnalyticsRecorder(sink))
    try:
        workflow_emit(
            "s1",
            stage="qualification",
            reason="contract_mismatch",
            retryable=False,
            http_status=500,
            error_class="CrmApiError",
        )
        workflow_emit("s2", stage="qualification", reason="mapping_rejected", retryable=False)
        coordinator_emit(
            "s3",
            stage="lead_intake",
            reason="gate_blocked",
            retryable=False,
            error_class="LeadIntakeDisabledError",
        )

        allowed = {"stage", "reason", "retryable", "http_status", "error_class"}
        assert len(sink.events) == 3
        for event in sink.events:
            assert event.event == EVENTS.CRM_CALL_FAILED
            assert set(event.payload) <= allowed, event.payload
            # Обязательные три поля присутствуют всегда, опциональные — только
            # когда их явно передали.
            assert {"stage", "reason", "retryable"} <= set(event.payload)
        assert "http_status" not in sink.events[1].payload
        assert "error_class" not in sink.events[1].payload
    finally:
        analytics.set_recorder(None)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Крючки аналитики проверяются без сети: извлекатель ничего не добавляет.

    DaData-токен лежит в локальном .env и подхватывается config.py на импорте,
    поэтому его надо снять явно — иначе тест уходит в реальный справочник и
    тратит секунды на каждый ход.
    """
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *a, **k: {"fact_updates": [], "internal_summary": ""},
    )
    monkeypatch.setenv("MBK_ROUTER_MOCK", "1")
    monkeypatch.delenv("MBK_DADATA_API", raising=False)
    # В локальном .env стоит semantic — это отдельный вызов модели, который
    # мок extract_facts не перехватывает.
    monkeypatch.setenv("MBK_FACT_INTERPRETATION_MODE", "legacy")


@pytest.fixture
def sink():
    memory = MemorySink()
    analytics.set_recorder(AnalyticsRecorder(memory))
    yield memory
    analytics.set_recorder(None)


def _turn(state, message, reply="Понял вас."):
    """prepare_turn возвращает НОВЫЙ state — коммитить надо именно его."""
    prepared = core.prepare_turn(state, message, submit_delivery=False)
    return core.commit_turn(prepared, message, reply)


def _names(sink):
    return [event.event for event in sink.events]


def _by_name(sink, name):
    return [event for event in sink.events if event.event == name]


def test_every_turn_is_recorded_with_its_script_step(matrix, sink):
    """Воронка по шагам — то, чем заказчик мерит «80% доходят до конца»."""
    state = init_state()
    state["session_id"] = "abc123"
    state["facts"] = {"client": {"name": "Василий"}}
    _turn(state, "нужны деньги", "Василий, какая сумма нужна?")

    (event,) = _by_name(sink, E.TURN_COMPLETED)
    assert event.payload["step"] == 0
    assert event.payload["action"] == "ask"
    assert event.payload["field"] == "request.amount"
    assert event.dedup_key is None


def test_completed_questionnaire_is_recorded_once_with_the_route(matrix, sink):
    """Пункты 2 и 3 заказчика: событие одно, различие в поле matched."""
    state = init_state()
    state["session_id"] = "abc123"
    state["facts"] = FULL_CONSUMER_FACTS
    result = _turn(state, "машины нет", "Понятно.")

    (event,) = _by_name(sink, E.QUALIFICATION_COMPLETED)
    assert event.payload["matched"] is True
    assert event.payload["route_id"] == "consumer"
    assert event.dedup_key.endswith(":qualification_completed")

    # Повторный ход не должен посчитать ту же анкету второй раз.
    _turn(result["current_state"], "а когда ссылка?", "Сейчас пришлю.")
    assert len(_by_name(sink, E.QUALIFICATION_COMPLETED)) == 1


def test_questionnaire_without_a_route_is_still_completed(matrix, sink):
    """Иначе знаменатель воронки потеряет тех, кому решения не нашлось."""
    state = init_state()
    state["session_id"] = "abc123"
    state["facts"] = DEAD_END_FACTS
    _turn(state, "машины нет", "Понятно.")

    (event,) = _by_name(sink, E.QUALIFICATION_COMPLETED)
    assert event.payload["matched"] is False
    assert event.payload["route_id"] is None


def test_analytics_payload_never_carries_the_phone(matrix, sink):
    """Контракт ПД проверяется на настоящем ходе, а не только на юнит-уровне."""
    state = init_state()
    state["session_id"] = "abc123"
    state["facts"] = {**FULL_CONSUMER_FACTS}
    state["facts"]["client"] = {**FULL_CONSUMER_FACTS["client"], "phone": "+79990000000"}
    _turn(state, "вот телефон", "Спасибо.")

    for event in sink.events:
        assert "phone" not in str(event.payload)
        assert "Василий" not in str(event.payload)


FULL_CONSUMER_FACTS = {
    "client": {"name": "Василий", "age": 40, "region_class": "moscow_mo"},
    "request": {"amount": 600_000},
    "debts": {"has_active_credits": False},
    "employment": {"type": "official", "income": 150_000},
    "assets": {"has_realty": False, "has_vehicle": False},
}

DEAD_END_FACTS = {
    "client": {"name": "Василий", "age": 70, "region_class": "rf_other"},
    "request": {"amount": 100_000},
    "debts": {"has_active_credits": False},
    "employment": {"type": "unemployed"},
    "assets": {"has_realty": False, "has_vehicle": False},
}


def test_chat_started_is_recorded_on_session_create(sink):
    """Пункт 1 заказчика — знаменатель всей воронки."""
    import asyncio

    from session_store import SessionStore

    store = SessionStore()
    session = asyncio.run(store.create(anketa=None))

    (event,) = _by_name(sink, E.CHAT_STARTED)
    assert event.session_id == session.id
    assert event.payload["has_anketa"] is False
    assert event.dedup_key == f"{session.id}:chat_started"


def test_chat_started_marks_sessions_opened_with_an_anketa(sink):
    import asyncio

    from session_store import SessionStore

    store = SessionStore()
    asyncio.run(store.create(anketa={"name": "Василий"}))

    (event,) = _by_name(sink, E.CHAT_STARTED)
    assert event.payload["has_anketa"] is True
