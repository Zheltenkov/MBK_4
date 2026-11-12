"""Аналитические события: единственная таблица, которую читает посторонний.

Дата-инженер заказчика ходит в `analytics_events` напрямую SQL-ем через
SSH-туннель под read-only ролью и забирает данные, когда ему нужно. Отсюда два
свойства, за которые отвечает этот пакет.

Первое: в payload не попадают персональные данные. Именно поэтому доступ к
таблице — техническое решение, а не решение про ПД. Проверка жёсткая: попытка
записать телефон или имя роняет вызов на тестах, а не маскирует молча.

Второе: запись не роняет диалог. Тот же принцип, что в `observability` — если
Postgres недоступен, событие теряется, а разговор продолжается.

Третье, с 20.08.2026: рядом лежит таблица `dialog_messages` — переписка
целиком, по просьбе заказчика («в нашу БД нужно писать все диалоги»). Контракт
у неё другой: свободный текст реплик сохраняется как есть, вычищается только
номер телефона, и то не удалением, а солёным отпечатком. Подробности —
`analytics/transcripts.py`.
"""
from __future__ import annotations

from analytics.events import (
    EVENTS,
    AnalyticsEvent,
    AnalyticsRecorder,
    MemorySink,
    NullSink,
    PERSONAL_DATA_KEYS,
)
from analytics.postgres import (
    PostgresSink,
    PostgresTranscriptSink,
    build_recorder,
    build_transcript_recorder,
    purge_transcripts,
    transcript_retention_days,
)
from analytics.transcripts import (
    MemoryTranscriptSink,
    NullTranscriptSink,
    TranscriptMessage,
    TranscriptRecorder,
)


# Глобальный recorder по образцу observability: модули домена не должны таскать
# зависимость аналитики через все сигнатуры. По умолчанию выключен — импорт
# пакета сам по себе ничего не пишет.
_recorder = AnalyticsRecorder(NullSink())
# Переписка живёт отдельным recorder'ом от событий: у таблиц разный контракт по
# персональным данным, и выключаться они должны независимо.
_transcripts = TranscriptRecorder(NullTranscriptSink())


def get_recorder() -> AnalyticsRecorder:
    return _recorder


def set_recorder(recorder: AnalyticsRecorder | None) -> None:
    """Ставит recorder на старте приложения; None возвращает выключенный."""
    global _recorder
    _recorder = recorder or AnalyticsRecorder(NullSink())


def get_transcripts() -> TranscriptRecorder:
    return _transcripts


def set_transcripts(recorder: TranscriptRecorder | None) -> None:
    """Ставит приёмник переписки; None возвращает выключенный."""
    global _transcripts
    _transcripts = recorder or TranscriptRecorder(NullTranscriptSink())


__all__ = [
    "EVENTS",
    "AnalyticsEvent",
    "AnalyticsRecorder",
    "MemorySink",
    "MemoryTranscriptSink",
    "NullSink",
    "NullTranscriptSink",
    "PERSONAL_DATA_KEYS",
    "PostgresSink",
    "PostgresTranscriptSink",
    "TranscriptMessage",
    "TranscriptRecorder",
    "build_recorder",
    "build_transcript_recorder",
    "get_recorder",
    "get_transcripts",
    "purge_transcripts",
    "set_recorder",
    "set_transcripts",
    "transcript_retention_days",
]
