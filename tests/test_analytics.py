"""Слой аналитических событий: контракт записи и изоляция отказов.

Таблица читается посторонним (дата-инженер по SSH-туннелю, read-only роль),
поэтому здесь проверяются два свойства, которые нельзя проверить глазами на
проде: в payload не попадают персональные данные, и падение хранилища не роняет
диалог.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from analytics import (
    AnalyticsEvent,
    AnalyticsRecorder,
    MemorySink,
    NullSink,
    build_recorder,
)


def test_event_carries_time_name_session_and_payload():
    recorder = AnalyticsRecorder(MemorySink())

    recorder.emit("chat_started", "abc123", {"source": "web"})

    (event,) = recorder.sink.events
    assert event.event == "chat_started"
    assert event.session_id == "abc123"
    assert event.payload == {"source": "web"}
    assert event.occurred_at.tzinfo is timezone.utc


def test_dedup_key_is_passed_through():
    """At-most-once события (ссылка, лид) ретраятся — ключ защищает от двойного
    счёта на уровне UNIQUE-констрейнта, а не на уровне приложения."""
    recorder = AnalyticsRecorder(MemorySink())

    recorder.emit("link_delivered", "abc123", {}, dedup_key="abc123:link_delivered")

    assert recorder.sink.events[0].dedup_key == "abc123:link_delivered"


def test_payload_with_personal_data_is_rejected():
    """Телефон и имя не должны утечь во вторую систему хранения.

    Проверка жёсткая: не маскируем молча, а падаем на тесте. Молчаливая
    маскировка спрятала бы ошибку разработчика до самого прода.
    """
    recorder = AnalyticsRecorder(MemorySink())

    with pytest.raises(ValueError, match="персональн"):
        recorder.emit("lead_submitted", "abc123", {"phone": "+79990000000"})


def test_nested_personal_data_is_rejected():
    recorder = AnalyticsRecorder(MemorySink())

    with pytest.raises(ValueError, match="персональн"):
        recorder.emit("turn_completed", "abc123", {"client": {"name": "Ирина"}})


def test_personal_data_inside_a_list_is_rejected():
    """Контракт ПД — про весь payload, а не про его верхний ярус словарей.

    Обход, знающий только dict, пропускал `{"clients": [{"phone": ...}]}`
    целиком: json.dumps в PostgresSink сериализует список без вопросов, и
    телефон уезжал в таблицу, которую читает посторонний.
    """
    recorder = AnalyticsRecorder(MemorySink())

    with pytest.raises(ValueError, match="персональн"):
        recorder.emit(
            "lead_submitted", "abc123", {"clients": [{"phone": "+79990000000"}]}
        )

    assert recorder.sink.events == []


def test_personal_data_inside_nested_lists_and_tuples_is_rejected():
    """Проверяется правило «спускаемся в любой контейнер», а не перечисление форм."""
    recorder = AnalyticsRecorder(MemorySink())

    for payload in (
        {"rows": [[{"name": "Ирина"}]]},
        {"rows": ({"passport": "4509 123456"},)},
        {"a": {"b": [{"email": "i@x.ru"}]}},
    ):
        with pytest.raises(ValueError, match="персональн"):
            recorder.emit("turn_completed", "abc123", payload)

    assert recorder.sink.events == []


def test_rejection_message_points_at_the_element_inside_the_list():
    """Разработчику нужен адрес ключа, иначе искать его в чужом payload долго."""
    recorder = AnalyticsRecorder(MemorySink())

    with pytest.raises(ValueError, match=r"clients\[0\]\.phone"):
        recorder.emit(
            "lead_submitted", "abc123", {"clients": [{"phone": "+79990000000"}]}
        )


def test_lists_of_scalars_stay_allowed():
    """Проверка ключевая, а не по значениям: строки не разбираются посимвольно,
    и безобидный список меток обязан проходить."""
    recorder = AnalyticsRecorder(MemorySink())

    recorder.emit(
        "turn_completed",
        "abc123",
        {"tags": ["a", "b"], "steps": [{"step": "income"}], "phones_seen": 2},
    )

    assert recorder.sink.events[0].payload["tags"] == ["a", "b"]


def test_broken_sink_never_breaks_the_dialog():
    """Тот же принцип, что в observability: телеметрия не роняет разговор."""

    class ExplodingSink:
        def write(self, event: AnalyticsEvent) -> None:
            raise RuntimeError("postgres недоступен")

    recorder = AnalyticsRecorder(ExplodingSink())

    recorder.emit("chat_started", "abc123", {})  # не должно выбросить


def test_null_sink_is_the_default_without_configuration():
    """Без DSN приложение работает как раньше и ничего не пишет."""
    recorder = AnalyticsRecorder(NullSink())

    recorder.emit("chat_started", "abc123", {})

    assert recorder.enabled is False


def test_memory_sink_is_enabled():
    assert AnalyticsRecorder(MemorySink()).enabled is True


def test_explicit_timestamp_is_preserved():
    """Отложенные события (abandoned lead) фиксируют своё время, а не время записи."""
    moment = datetime(2026, 7, 29, 9, 12, 3, tzinfo=timezone.utc)
    recorder = AnalyticsRecorder(MemorySink())

    recorder.emit("qualification_abandoned", "abc123", {}, occurred_at=moment)

    assert recorder.sink.events[0].occurred_at == moment


def test_recorder_without_dsn_is_disabled(monkeypatch):
    """Аналитика не должна быть условием запуска приложения."""
    monkeypatch.delenv("MBK_ANALYTICS_DSN", raising=False)

    assert build_recorder().enabled is False


def test_recorder_without_dsn_still_accepts_events(monkeypatch):
    monkeypatch.delenv("MBK_ANALYTICS_DSN", raising=False)
    recorder = build_recorder()

    recorder.emit("chat_started", "abc123", {"source": "web"})  # без исключений


def test_pii_contract_holds_even_when_analytics_is_off(monkeypatch):
    """Иначе ошибку с телефоном в payload заметили бы только на проде с DSN."""
    monkeypatch.delenv("MBK_ANALYTICS_DSN", raising=False)
    recorder = build_recorder()

    with pytest.raises(ValueError, match="персональн"):
        recorder.emit("lead_submitted", "abc123", {"phone": "+79990000000"})


def test_postgres_sink_inserts_with_conflict_guard():
    """Ключ идемпотентности должен работать в SQL, а не в приложении.

    Проверка на подставном соединении: настоящий Postgres тут не нужен, а
    забыть ON CONFLICT легко — и тогда ретрай доставки посчитается дважды.
    """
    from analytics.postgres import PostgresSink

    executed = []

    class _Cursor:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def execute(self, sql, params): executed.append((sql, params))

    class _Connection:
        closed = False
        def cursor(self): return _Cursor()
        def commit(self): executed.append(("COMMIT", None))

    sink = PostgresSink.__new__(PostgresSink)
    sink._connection = _Connection()
    sink._dsn = "postgresql://unused"
    sink._insert(
        AnalyticsEvent(
            event="link_delivered",
            session_id="abc123",
            payload={"route_id": "consumer"},
            dedup_key="abc123:link_delivered",
        )
    )

    sql, params = executed[0]
    assert "ON CONFLICT (dedup_key) DO NOTHING" in sql
    assert params[1] == "link_delivered"
    assert params[2] == "abc123"
    assert params[4] == "abc123:link_delivered"
    assert executed[1][0] == "COMMIT"


def test_postgres_sink_drops_events_when_the_queue_is_full():
    """При долгой недоступности базы теряем события, а не память."""
    from analytics.postgres import PostgresSink

    sink = PostgresSink.__new__(PostgresSink)
    import queue as _queue
    sink._queue = _queue.Queue(maxsize=1)
    sink._queue.put_nowait(AnalyticsEvent(event="x", session_id="s"))

    sink.write(AnalyticsEvent(event="y", session_id="s"))  # без исключения

    assert sink._queue.qsize() == 1
