"""Репозиторий сессий в Postgres.

Запись синхронная и в том же потоке, что и вызов `persist`: в отличие от
аналитики, здесь нельзя терять события при затыке очереди. Ровно эта запись
делает автомат отправки лида устойчивым к рестарту, а отложенная на фоновый
поток запись «почти сохранила» — это и есть дубль лида в CRM.

Цена вопроса — единицы миллисекунд к ходу, который и без того идёт секунды.
Недоступность базы не должна ронять разговор, поэтому наверху (SessionStore)
ошибки перехватываются, а здесь они честно поднимаются.
"""
from __future__ import annotations

import json
import hashlib
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable


logger = logging.getLogger(__name__)


# Поддерево state, которым владеет база, а не процесс: статус отправки лида.
# Во время сине-зелёной выкатки старый и новый контейнер держат одну и ту же
# сессию, и обычная запись любого из них не должна двигать этот статус —
# устаревшая копия вернула бы уже отправленному лиду «ещё не отправлен».
LEAD_SUBMISSION_STATE_KEY = "crm_lead"

# Статус, при котором право отправить лид ещё свободно. Значение сознательно
# продублировано строкой: SQL ниже сравнивает его внутри jsonb, а тянуть сюда
# enum отправки — это импорт прикладного слоя в хранилище.
_LEAD_SLOT_FREE = "not_sent"


@dataclass(frozen=True)
class LeadClaim:
    """Ответ базы на попытку занять единственное право отправить лид сессии.

    `lead_state` заполняется, когда слот занят другим процессом: его исход
    забирает себе проигравший, иначе клиент увидел бы ошибку доставки там, где
    лид на самом деле уже уехал. `error` означает «неизвестно, свободен ли
    слот» — запрос при этом точно не уходил, и попытку можно повторить.
    """

    granted: bool
    lead_state: dict[str, Any] | None = None
    error: str | None = None
    revision: int | None = None


@dataclass(frozen=True)
class SessionRecord:
    """Сериализуемый слепок сессии — ровно то, что переживает рестарт."""

    id: str
    created_at: datetime
    last_active: datetime
    has_started_opening: bool
    anketa: dict[str, Any] | None
    state: dict[str, Any]
    usage: list[dict[str, Any]]
    revision: int = 0


class SessionPersistenceError(RuntimeError):
    """Closed persistence failure. Driver text and private values never cross this boundary."""

    reason: Literal["unavailable", "conflict", "invalid_ack", "not_configured", "unsupported_repository"]

    def __init__(self, reason: Literal["unavailable", "conflict", "invalid_ack", "not_configured", "unsupported_repository"]) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class SessionCommit:
    """Acknowledgment returned only after the entire private record committed."""

    session_id: str
    revision: int
    state_digest: str


def session_record_digest(record: SessionRecord) -> str:
    """Bind the ACK to the full payload, excluding the monotonically increasing revision."""
    payload = {
        "id": record.id, "created_at": record.created_at.isoformat(),
        "last_active": record.last_active.isoformat(), "has_started_opening": record.has_started_opening,
        "anketa": record.anketa, "state": record.state, "usage": record.usage,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@runtime_checkable
class CommittedSessionRepository(Protocol):
    """Optional local reliability contract. Legacy repository save semantics are unchanged."""

    def save_committed(self, record: SessionRecord, *, lead_owner: bool = False) -> SessionCommit: ...


class SessionRepository(Protocol):
    def save(self, record: SessionRecord) -> None: ...

    def save_lead_state(self, record: SessionRecord) -> None: ...

    def claim_lead_submission(self, record: SessionRecord) -> LeadClaim: ...

    def load(self, session_id: str) -> SessionRecord | None: ...

    def load_active_since(self, moment: datetime) -> list[SessionRecord]: ...

    def delete_before(self, moment: datetime) -> int: ...


_INSERT = """
INSERT INTO sessions (
    id, created_at, last_active, has_started_opening, anketa, state, usage
) VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
    last_active         = EXCLUDED.last_active,
    has_started_opening = EXCLUDED.has_started_opening,
    anketa              = EXCLUDED.anketa,
"""

# Обычная запись хода: state пишется целиком, кроме поддерева отправки лида —
# оно остаётся тем, что уже лежит в базе. Иначе процесс, который проиграл гонку
# или просто держит устаревшую копию сессии, снял бы чужой захват слота.
_UPSERT = _INSERT + f"""
    state               = CASE
        WHEN sessions.state -> '{LEAD_SUBMISSION_STATE_KEY}' IS NULL
            THEN EXCLUDED.state
        ELSE jsonb_set(
            EXCLUDED.state,
            ARRAY['{LEAD_SUBMISSION_STATE_KEY}'],
            sessions.state -> '{LEAD_SUBMISSION_STATE_KEY}'
        )
    END,
    usage               = EXCLUDED.usage
"""

# Запись владельцем занятого слота: исход POST'а (submitted, ошибка, возврат
# слота) обязан доехать до базы целиком.
_UPSERT_WITH_LEAD_STATE = _INSERT + """
    state               = EXCLUDED.state,
    usage               = EXCLUDED.usage
"""

# Захват слота. Условие — единственное место, где at-most-once перестаёт быть
# памятью процесса: два контейнера в окне выкатки видят одну строку, и UPDATE
# достаётся ровно одному из них.
_CLAIM_LEAD = _UPSERT_WITH_LEAD_STATE + f"""
WHERE COALESCE(
    sessions.state -> '{LEAD_SUBMISSION_STATE_KEY}' ->> 'status',
    '{_LEAD_SLOT_FREE}'
) = '{_LEAD_SLOT_FREE}'
"""

_SELECT_LEAD_STATE = (
    f"SELECT state -> '{LEAD_SUBMISSION_STATE_KEY}' FROM sessions WHERE id = %s"
)

_SELECT_COLUMNS = (
    "id, created_at, last_active, has_started_opening, anketa, state, usage"
)


class PostgresSessionRepository:
    """Соединение на поток: `persist` вызывается и из executor'а, и из цикла."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._local = threading.local()

    def save(self, record: SessionRecord) -> None:
        self._execute(_UPSERT, _row(record))

    def save_lead_state(self, record: SessionRecord) -> None:
        self._execute(_UPSERT_WITH_LEAD_STATE, _row(record))

    def claim_lead_submission(self, record: SessionRecord) -> LeadClaim:
        """Занимает право отправить лид: строка обновится ровно у одного из них.

        Проигравшему возвращается состояние победителя — своего исхода у него
        нет и не будет, а придумывать его нельзя: лид уже уехал или уезжает.
        """
        if self._execute(_CLAIM_LEAD, _row(record)) == 1:
            return LeadClaim(granted=True)
        rows = self._query(_SELECT_LEAD_STATE, (record.id,))
        return LeadClaim(granted=False, lead_state=rows[0][0] if rows else None)

    def load(self, session_id: str) -> SessionRecord | None:
        rows = self._query(
            f"SELECT {_SELECT_COLUMNS} FROM sessions WHERE id = %s", (session_id,)
        )
        return _to_record(rows[0]) if rows else None

    def load_active_since(self, moment: datetime) -> list[SessionRecord]:
        rows = self._query(
            f"SELECT {_SELECT_COLUMNS} FROM sessions WHERE last_active >= %s"
            " ORDER BY last_active",
            (moment,),
        )
        return [_to_record(row) for row in rows]

    def delete_before(self, moment: datetime) -> int:
        return self._execute("DELETE FROM sessions WHERE last_active < %s", (moment,))

    # ------------------------------------------------------------------ #

    def _execute(self, sql: str, params: tuple) -> int:
        connection = self._connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                affected = cursor.rowcount
            connection.commit()
            return affected
        except Exception:
            # Порванное соединение переоткроется на следующем вызове; иначе
            # первая же сетевая ошибка отключила бы хранилище до рестарта.
            self._forget_connection()
            raise

    def _query(self, sql: str, params: tuple) -> list[tuple]:
        connection = self._connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchall()
        except Exception:
            self._forget_connection()
            raise

    def _connection(self) -> Any:
        connection = getattr(self._local, "connection", None)
        if connection is None or getattr(connection, "closed", False):
            import psycopg  # локальный импорт: без DSN пакет не нужен вовсе

            connection = psycopg.connect(self._dsn)
            self._local.connection = connection
        return connection

    def _forget_connection(self) -> None:
        connection = getattr(self._local, "connection", None)
        self._local.connection = None
        if connection is None:
            return
        try:
            connection.close()
        except Exception:  # noqa: BLE001
            pass


def _row(record: SessionRecord) -> tuple:
    """Параметры INSERT'а — общие для обычной записи, владельца и захвата."""
    return (
        record.id,
        record.created_at,
        record.last_active,
        record.has_started_opening,
        _dump(record.anketa),
        _dump(record.state),
        _dump(record.usage),
    )


def _dump(value: Any) -> str:
    """Строгий JSON: `default=str` тихо превратил бы объект в его repr.

    Дисковый снапшот такое прощает — он для чтения человеком. Здесь же
    испорченное значение вернулось бы в диалог после рестарта.
    """
    return json.dumps(value, ensure_ascii=False)


def _to_record(row: tuple) -> SessionRecord:
    return SessionRecord(
        id=row[0],
        created_at=row[1],
        last_active=row[2],
        has_started_opening=row[3],
        anketa=row[4],
        state=row[5],
        usage=row[6] or [],
    )


def build_session_repository(dsn: str | None = None) -> SessionRepository | None:
    """Без `MBK_SESSIONS_DSN` возвращает None: сессии остаются только в памяти.

    Переменная отдельная от аналитической намеренно. Базы физически одна, но
    включение аналитики не должно молча включать хранение персональных данных.
    """
    resolved = dsn if dsn is not None else (os.getenv("MBK_SESSIONS_DSN") or "").strip()
    if not resolved:
        return None
    try:
        import psycopg  # noqa: F401
    except ImportError:
        logger.warning(
            "sessions: MBK_SESSIONS_DSN задан, но psycopg не установлен — "
            "состояние остаётся только в памяти"
        )
        return None
    return PostgresSessionRepository(resolved)
