"""Приёмник Postgres: запись в фоне, чтобы диалог не ждал базу.

Вставка идёт из отдельного потока через ограниченную очередь. Это не
преждевременная оптимизация: SSE-ход и так тратит секунды на модель, и добавлять
к нему синхронный сетевой вызов к БД, которая может быть недоступна, нельзя.
Очередь ограничена намеренно — при затыке теряем события, а не память.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
from typing import Any

from analytics.events import AnalyticsEvent, AnalyticsRecorder, NullSink
from analytics.transcripts import (
    NullTranscriptSink,
    TranscriptMessage,
    TranscriptRecorder,
    transcripts_enabled,
)


logger = logging.getLogger(__name__)

# Тысяча событий — это порядка сотни диалогов. Если очередь переполнилась,
# значит база недоступна давно, и копить дальше бессмысленно.
DEFAULT_QUEUE_SIZE = 1000

# Реплик на ход до четырёх, а диалогов одновременно — десятки.
DEFAULT_TRANSCRIPT_QUEUE = 5000

_INSERT = """
INSERT INTO analytics_events (occurred_at, event, session_id, payload, dedup_key)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (dedup_key) DO NOTHING
"""

# Переигранный или повторённый ход не должен раздваивать переписку.
_INSERT_MESSAGE = """
INSERT INTO dialog_messages
    (occurred_at, session_id, turn, position, role, message)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (session_id, turn, position) DO NOTHING
"""


class _BackgroundWriter:
    """Очередь, поток и переоткрытие соединения — общее для всех таблиц.

    Выделено из приёмника событий, когда рядом появилась переписка: две копии
    той же логики означали бы два разных поведения при обрыве базы.
    """

    enabled = True
    _thread_name = "mbk-postgres"
    _what = "строка"

    def __init__(self, dsn: str, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self._dsn = dsn
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=queue_size)
        self._connection: Any = None
        self._stopped = threading.Event()
        self._worker = threading.Thread(
            target=self._run, name=self._thread_name, daemon=True
        )
        self._worker.start()

    def write(self, item: Any) -> None:
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            logger.warning(
                "%s: очередь переполнена, %s отброшена",
                self._thread_name,
                self._what,
            )

    def close(self, timeout: float = 5.0) -> None:
        """Досылает накопленное при остановке приложения."""
        self._stopped.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._worker.join(timeout=timeout)
        self._close_connection()

    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            try:
                self._insert(item)
            except Exception:  # noqa: BLE001 — фоновый поток не умирает
                logger.warning(
                    "%s: вставка не удалась", self._thread_name, exc_info=True
                )
                # Соединение могло порваться — следующая попытка переоткроет.
                self._close_connection()

    def _insert(self, item: Any) -> None:  # pragma: no cover - переопределяется
        raise NotImplementedError

    def _execute(self, statement: str, params: tuple[Any, ...]) -> None:
        connection = self._ensure_connection()
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
        connection.commit()

    def _ensure_connection(self) -> Any:
        if self._connection is None or getattr(self._connection, "closed", False):
            import psycopg  # локальный импорт: без DSN пакет не нужен вовсе

            self._connection = psycopg.connect(self._dsn)
        return self._connection

    def _close_connection(self) -> None:
        connection, self._connection = self._connection, None
        if connection is None:
            return
        try:
            connection.close()
        except Exception:  # noqa: BLE001
            pass


class PostgresSink(_BackgroundWriter):
    """Пишет события в `analytics_events` фоновым потоком."""

    _thread_name = "mbk-analytics"
    _what = "событие"

    def write(self, event: AnalyticsEvent) -> None:
        super().write(event)

    def _insert(self, item: AnalyticsEvent) -> None:
        import json

        self._execute(
            _INSERT,
            (
                item.occurred_at,
                item.event,
                item.session_id,
                json.dumps(item.payload, ensure_ascii=False),
                item.dedup_key,
            ),
        )


class PostgresTranscriptSink(_BackgroundWriter):
    """Пишет реплики диалога в `dialog_messages` фоновым потоком.

    Очередь больше, чем у событий: на один ход приходится до четырёх реплик, а
    не одно событие, и при коротком сбое базы терять переписку жальче — именно
    её заказчик собирается читать.
    """

    _thread_name = "mbk-transcripts"
    _what = "реплика"

    def __init__(self, dsn: str, queue_size: int = DEFAULT_TRANSCRIPT_QUEUE) -> None:
        super().__init__(dsn, queue_size=queue_size)

    def write(self, message: TranscriptMessage) -> None:
        super().write(message)

    def _insert(self, item: TranscriptMessage) -> None:
        self._execute(
            _INSERT_MESSAGE,
            (
                item.occurred_at,
                item.session_id,
                item.turn,
                item.position,
                item.role,
                item.message,
            ),
        )


_PURGE_MESSAGES = """
DELETE FROM dialog_messages WHERE occurred_at < now() - make_interval(days => %s)
"""


def purge_transcripts(days: int, dsn: str | None = None) -> int:
    """Удаляет переписку старше указанного срока. 0 — хранить бессрочно.

    Отдельным соединением, а не через приёмник: чистка идёт раз в час из
    задачи срока хранения, а приёмник занят своим потоком записи. Отказ базы
    гасится здесь же — срок хранения не должен ронять приложение.
    """
    if days <= 0:
        return 0
    resolved = dsn if dsn is not None else (os.getenv("MBK_ANALYTICS_DSN") or "").strip()
    if not resolved:
        return 0
    try:
        import psycopg

        with psycopg.connect(resolved) as connection:
            with connection.cursor() as cursor:
                cursor.execute(_PURGE_MESSAGES, (days,))
                removed = cursor.rowcount or 0
            connection.commit()
    except Exception:  # noqa: BLE001 — чистка не роняет приложение
        logger.warning("transcripts: чистка по сроку хранения не удалась", exc_info=True)
        return 0
    return removed


def transcript_retention_days() -> int:
    """Срок хранения переписки в днях. Пусто или 0 — хранить бессрочно."""
    raw = (os.getenv("MBK_DIALOG_RETENTION_DAYS") or "").strip()
    try:
        return max(0, int(raw)) if raw else 0
    except ValueError:
        logger.warning(
            "transcripts: MBK_DIALOG_RETENTION_DAYS=%r не число — храним бессрочно",
            raw,
        )
        return 0


def build_transcript_recorder(dsn: str | None = None) -> TranscriptRecorder:
    """Собирает приёмник переписки из окружения.

    DSN тот же, что у аналитики: база у заказчика одна, и заводить вторую
    настройку ради той же строки подключения — лишний способ ошибиться.
    Выключается `MBK_DIALOG_TRANSCRIPTS_ENABLED=0`.
    """
    resolved = dsn if dsn is not None else (os.getenv("MBK_ANALYTICS_DSN") or "").strip()
    if not resolved or not transcripts_enabled():
        return TranscriptRecorder(NullTranscriptSink())
    try:
        import psycopg  # noqa: F401
    except ImportError:
        logger.warning(
            "transcripts: MBK_ANALYTICS_DSN задан, но psycopg не установлен — "
            "переписка не пишется"
        )
        return TranscriptRecorder(NullTranscriptSink())
    return TranscriptRecorder(PostgresTranscriptSink(resolved))


def build_recorder(dsn: str | None = None) -> AnalyticsRecorder:
    """Собирает recorder из окружения.

    Без `MBK_ANALYTICS_DSN` возвращается NullSink: приложение поднимается и
    работает как раньше. Аналитика не должна быть условием запуска.
    """
    resolved = dsn if dsn is not None else (os.getenv("MBK_ANALYTICS_DSN") or "").strip()
    if not resolved:
        return AnalyticsRecorder(NullSink())
    try:
        import psycopg  # noqa: F401
    except ImportError:
        logger.warning(
            "analytics: MBK_ANALYTICS_DSN задан, но psycopg не установлен — "
            "события не пишутся"
        )
        return AnalyticsRecorder(NullSink())
    return AnalyticsRecorder(PostgresSink(resolved))
