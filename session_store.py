"""
Store сессий: горячая копия в памяти, долговременная — в Postgres.

Хранит:
- state (facts, chat_history, plan, delivery)
- anketa (опционально)
- usage (SessionUsage)

Снимок в logs/sessions/{id}.json остаётся для чтения человеком и потому
маскирует ПД. Восстановление идёт не из него, а из таблицы `sessions`: там
состояние лежит как есть, иначе после рестарта клиент вернулся бы в диалог с
затёртым телефоном.

Без `MBK_SESSIONS_DSN` репозитория нет, и store работает как раньше — только в
памяти. Недоступность базы разговор не роняет: ошибки хранилища логируются и
проглатываются, ровно как в аналитике.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock, RLock
from typing import Any

import analytics
from attribution import clean_utm
from state import init_state
from pii_masking import mask_session_snapshot
from storage.sessions import (
    CommittedSessionRepository, LeadClaim, SessionCommit, SessionPersistenceError,
    SessionRecord, SessionRepository, session_record_digest,
)
from usage_tracker import SessionUsage


logger = logging.getLogger(__name__)

SESSIONS_DIR = Path(__file__).resolve().parent / "logs" / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

# Сколько времени сессия считается живой и подлежит восстановлению после
# рестарта. Сутки — верхняя граница правдоподобного возврата клиента к
# незаконченной анкете; всё, что старше, будить незачем.
DEFAULT_RESTORE_HOURS = 24


@dataclass
class Session:
    id: str
    state: dict[str, Any]
    anketa: dict[str, Any] | None
    usage: SessionUsage
    has_started_opening: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_active: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    storage_revision: int = 0
    _storage_uncertain: bool = field(default=False, repr=False)
    _persistence_lock: RLock = field(default_factory=RLock, repr=False)
    _lock: Lock = field(default_factory=Lock, repr=False)
    # Один диалоговый ход за раз. Без этого два SSE-запроса одной сессии могут
    # подготовить state от одной и той же старой истории и последний затрёт первый.
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def touch(self) -> None:
        self.last_active = datetime.now(timezone.utc)

    def snapshot_dict(self) -> dict[str, Any]:
        safe_state, safe_anketa = mask_session_snapshot(self.state, self.anketa)
        return {
            "id": self.id,
            "anketa": safe_anketa,
            "state": safe_state,
            "usage": self.usage.summary(),
            "has_started_opening": self.has_started_opening,
            "created_at": self.created_at.isoformat(),
            "last_active": self.last_active.isoformat(),
        }

    def storage_record(self) -> SessionRecord:
        """Слепок для базы — без маскирования и без временных объектов."""
        return SessionRecord(
            id=self.id,
            created_at=self.created_at,
            last_active=self.last_active,
            has_started_opening=self.has_started_opening,
            anketa=self.anketa,
            state=_persistable_state(self.state),
            usage=self.usage.export_events(),
            revision=self.storage_revision,
        )

    @classmethod
    def from_record(cls, record: SessionRecord) -> "Session":
        return cls(
            id=record.id,
            state=record.state,
            anketa=record.anketa,
            usage=SessionUsage.restore(record.id, record.usage),
            has_started_opening=record.has_started_opening,
            created_at=record.created_at,
            last_active=record.last_active,
            storage_revision=record.revision,
        )


def _persistable_state(state: dict[str, Any]) -> dict[str, Any]:
    """Отбрасывает служебные ключи хода.

    `_plan_object` — живой объект Plan: в JSON он не сериализуется, а в базе не
    нужен вовсе. Такие ключи существуют один ход и заново считаются в
    prepare_turn, поэтому договорённость простая: ключ с подчёркивания —
    временный.
    """
    return deepcopy({key: value for key, value in state.items() if not key.startswith("_")})


def _delete_stale_snapshots(cutoff: datetime) -> int:
    """Удаляет снимки диалогов, переживших срок хранения.

    Раньше чистка доходила только до памяти и базы, а файлы в logs/sessions
    оставались навсегда — при том, что заказчику обещано «сессия вместе с
    персональными данными удаляется через 72 часа».

    Цена промаха уже известна: снимки копились в контейнере месяцами, и когда
    образ распаковали, одиннадцать файлов уехали в чужой git вместе с телефоном
    живого клиента.

    Возраст берётся по времени изменения файла, а не по списку живых сессий:
    после рестарта в памяти нет ничего, а файлы прошлых запусков лежат.
    """
    threshold = cutoff.timestamp()
    removed = 0
    try:
        entries = list(SESSIONS_DIR.iterdir())
    except OSError:  # noqa: BLE001 — недоступный каталог не должен ронять чистку
        return 0
    for path in entries:
        try:
            if not path.is_file() or path.stat().st_mtime >= threshold:
                continue
            os.unlink(path)
            removed += 1
        except OSError:  # noqa: BLE001 — файл мог исчезнуть сам или быть занят
            continue
    if removed:
        logger.info("sessions: удалено просроченных снимков: %s", removed)
    return removed


class SessionStore:
    """Потокобезопасный store + общий ThreadPoolExecutor.

    Executor нужен, чтобы синхронные вызовы модели (извлечение, стрим) не блокировали
    цикл событий FastAPI.
    """

    def __init__(
        self,
        max_extract_workers: int = 4,
        repository: SessionRepository | None = None,
        *, require_durable: bool = False,
    ):
        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()
        self._repository = repository
        self.require_durable = require_durable
        # Крючок на подъём сессии из базы. Состояние, записанное прежним
        # процессом, может ссылаться на его незаконченные дела: запрос в CRM со
        # статусом in_flight никто уже не завершит. Крючок висит на подъёме, а
        # не на старте, потому что подъёмов два — restore() и ленивый get(), —
        # и разбор, привязанный только к первому, второй пропускает.
        self.on_restore: Callable[[Session], None] | None = None
        self.executor = ThreadPoolExecutor(
            max_workers=max_extract_workers, thread_name_prefix="mbk-extractor"
        )

    async def create(self, anketa: dict | None, utm: dict | None = None) -> Session:
        sid = uuid.uuid4().hex[:12]
        state = init_state(anketa)
        state["session_id"] = sid
        # Метки живут в состоянии, а не в отдельном хранилище: они нужны один
        # раз, при создании лида, и обязаны пережить рестарт вместе с сессией.
        state["attribution"] = clean_utm(utm)
        session = Session(
            id=sid,
            state=state,
            anketa=anketa,
            usage=SessionUsage(session_id=sid),
        )
        async with self._lock:
            self._sessions[sid] = session
        try:
            self.persist(session)
        except SessionPersistenceError:
            # A failed creation has no owner grant and cannot continue as a RAM-only chat.
            async with self._lock:
                self._sessions.pop(sid, None)
            raise
        # Начало чата — знаменатель всей воронки. Крючок стоит здесь, а не в
        # HTTP-обработчике: сессия создаётся одним путём, и эвалы считаются так же,
        # как живой трафик.
        analytics.get_recorder().emit(
            analytics.EVENTS.CHAT_STARTED,
            sid,
            {"has_anketa": bool(anketa)},
            dedup_key=f"{sid}:chat_started",
        )
        return session

    async def get(self, session_id: str) -> Session | None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                if session._storage_uncertain and session.turn_lock.locked():
                    raise SessionPersistenceError("unavailable")
                if session._storage_uncertain or (self.require_durable and not session.turn_lock.locked()):
                    record = self._load_record(session_id, required=True)
                    if record is None:
                        if session._storage_uncertain:
                            raise SessionPersistenceError("unavailable")
                        self._sessions.pop(session_id, None)
                        return None
                    if session._storage_uncertain or record.revision != session.storage_revision:
                        self._refresh_session(session, record)
                return session
            record = self._load_record(session_id)
            if record is None:
                return None
            # Клиент вернулся в диалог, который этот процесс не начинал:
            # после выкатки или падения. Поднимаем состояние и продолжаем.
            session = self._hydrate(record)
            self._sessions[session_id] = session
            return session

    async def read_committed(self, session_id: str) -> tuple[Session | None, bool]:
        """Read a detached committed snapshot without exposing a worker's provisional mutations."""
        if not self.require_durable:
            session = await self.get(session_id)
            return session, bool(session is not None and session.turn_lock.locked())
        async with self._lock:
            active = self._sessions.get(session_id)
            busy = bool(active is not None and active.turn_lock.locked())
            record = self._load_record(session_id, required=True)
            return (Session.from_record(record) if record is not None else None), busy

    def refresh_locked(self, session: Session) -> None:
        """Revalidate after waiting for the turn lock, before executing or publishing a result."""
        if not self.require_durable and not session._storage_uncertain:
            return
        record = self._load_record(session.id, required=True)
        if record is None:
            raise SessionPersistenceError("unavailable")
        if session._storage_uncertain or record.revision != session.storage_revision:
            self._refresh_session(session, record)

    def persist(self, session: Session) -> None:
        """Сохранить сессию. Безопасно вызывать из любых потоков.

        Статус отправки лида этой записью не двигается: им владеет база (см.
        storage.sessions). Иначе процесс с устаревшей копией сессии — а в окне
        сине-зелёной выкатки такой есть всегда — вернул бы уже отправленному
        лиду «ещё не отправлен».
        """
        if self.require_durable:
            self.persist_durable(session)
            return
        if isinstance(self._repository, CommittedSessionRepository):
            try:
                self.persist_durable(session)
            except SessionPersistenceError:
                pass  # The legacy entry point keeps its best-effort contract.
            return
        self._write_snapshot(session)
        self._save_record(session)

    def persist_durable(self, session: Session, *, lead_owner: bool = False) -> SessionCommit:
        """Require a full-record CAS commit. Diagnostic output never acknowledges persistence."""
        with session._persistence_lock:
            repository = self._repository
            if repository is None:
                raise SessionPersistenceError("not_configured")
            if not isinstance(repository, CommittedSessionRepository):
                raise SessionPersistenceError("unsupported_repository")
            try:
                record = session.storage_record()
                acknowledgment = repository.save_committed(record, lead_owner=lead_owner)
                if (not isinstance(acknowledgment, SessionCommit)
                        or type(acknowledgment.session_id) is not str
                        or type(acknowledgment.state_digest) is not str
                        or acknowledgment.session_id != session.id
                        or type(acknowledgment.revision) is not int
                        or acknowledgment.revision != record.revision + 1
                        or acknowledgment.state_digest != session_record_digest(record)):
                    raise SessionPersistenceError("invalid_ack")
            except Exception as error:
                # Commit failure can be ambiguous. A later read must reload the authoritative row.
                session._storage_uncertain = True
                reason = error.reason if isinstance(error, SessionPersistenceError) else "unavailable"
                logger.warning("sessions: required checkpoint failed reason=%s", reason)
                raise SessionPersistenceError(reason) from None
            session.storage_revision = acknowledgment.revision
            session._storage_uncertain = False
        self._write_snapshot(session)
        return acknowledgment

    def commit_state_durable(self, session: Session, state: dict[str, Any], *, opening: bool = False) -> None:
        """Publish a detached conversation state only after its full-record CAS acknowledgement."""
        # The same persistence lock also serializes checkpoints made by background owners.
        with session._persistence_lock:
            candidate = Session(
                id=session.id, state=deepcopy(state), anketa=deepcopy(session.anketa), usage=session.usage,
                has_started_opening=session.has_started_opening or opening,
                created_at=session.created_at, last_active=session.last_active,
                storage_revision=session.storage_revision,
            )
            try:
                self.persist_durable(candidate)
            except SessionPersistenceError:
                # An ambiguous commit must be resolved from the repository on the next read.
                session._storage_uncertain = True
                raise
            with session._lock:
                session.state = candidate.state
                session.has_started_opening = candidate.has_started_opening
                session.storage_revision = candidate.storage_revision
                session._storage_uncertain = False

    def persist_submission(self, session: Session) -> None:
        """Запись процессом, который занял слот отправки: исход POST'а — его."""
        if self.require_durable:
            self.persist_durable(session, lead_owner=True)
            return
        if isinstance(self._repository, CommittedSessionRepository):
            try:
                self.persist_durable(session, lead_owner=True)
            except SessionPersistenceError:
                pass
            return
        self._write_snapshot(session)
        self._save_record(session, lead_owner=True)

    def claim_lead_submission(self, session: Session) -> LeadClaim:
        """Занимает в базе единственное право отправить лид этой сессии.

        Без репозитория процесс ровно один и конкурировать не с кем — тогда
        хватает автомата в памяти, а запись остаётся обычной.
        """
        if self._repository is None:
            if self.require_durable:
                return LeadClaim(granted=False, error="session_storage_unavailable")
            self.persist(session)
            return LeadClaim(granted=True)
        self._write_snapshot(session)
        try:
            result = self._repository.claim_lead_submission(session.storage_record())
            if isinstance(self._repository, CommittedSessionRepository) and result.granted:
                if type(result.revision) is not int or result.revision != session.storage_revision + 1:
                    session._storage_uncertain = True
                    return LeadClaim(granted=False, error="session_storage_invalid_ack")
                session.storage_revision = result.revision
            return result
        except Exception:  # noqa: BLE001 — база не должна ронять разговор
            if self.require_durable or isinstance(self._repository, CommittedSessionRepository):
                session._storage_uncertain = True
                logger.warning("sessions: required submission claim failed")
            else:
                logger.warning(
                    "sessions: не удалось занять слот отправки %s", session.id, exc_info=True,
                )
            # Не «занято», а «неизвестно»: отправлять нельзя, повторить можно.
            return LeadClaim(
                granted=False,
                error="хранилище недоступно: слот отправки не занят",
            )

    async def restore(self, max_age_hours: int = DEFAULT_RESTORE_HOURS) -> int:
        """Поднимает в память недавно активные сессии. Вызывается при старте.

        Без этого шага восстановление работало бы только для клиента, который
        сам постучался: sweeper брошенных лидов ходит по `all_sessions()` и
        после рестарта не увидел бы ни одной анкеты, ожидающей отправки.
        """
        if self._repository is None:
            if self.require_durable:
                raise SessionPersistenceError("not_configured")
            return 0
        moment = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        try:
            records = self._repository.load_active_since(moment)
        except Exception:  # noqa: BLE001 — старт не должен зависеть от базы
            if self.require_durable:
                raise SessionPersistenceError("unavailable") from None
            logger.warning("sessions: восстановление не удалось", exc_info=True)
            return 0
        restored = 0
        async with self._lock:
            for record in records:
                if record.id in self._sessions:
                    continue
                self._sessions[record.id] = self._hydrate(record)
                restored += 1
        if restored:
            logger.info("sessions: восстановлено %s сессий", restored)
        return restored

    async def drop_stale(self, max_age_hours: int = 24) -> int:
        """Чистка сессий старше N часов (вызывать по таймеру из main.py при желании)."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        async with self._lock:
            stale = [
                sid for sid, s in self._sessions.items() if s.last_active < cutoff
            ]
            for sid in stale:
                del self._sessions[sid]
        # Убрать только из памяти мало: при следующем рестарте те же сессии
        # вернулись бы из базы. Здесь же истекает и срок хранения ПД.
        self._delete_before(cutoff)
        _delete_stale_snapshots(cutoff)
        return len(stale)

    def all_sessions(self) -> list[Session]:
        return list(self._sessions.values())

    # ------------------------------------------------------------------ #

    def _hydrate(self, record: SessionRecord) -> Session:
        """Единственное место, где сессия рождается из записи в базе.

        Оба пути подъёма ведут сюда, поэтому крючок восстановления не нужно
        помнить и вешать в каждом из них по отдельности.
        """
        session = Session.from_record(record)
        if not self.require_durable:
            self._restore_known_lead_outcome(session)
        if self.on_restore is not None:
            try:
                self.on_restore(session)
            except Exception:  # noqa: BLE001 — разбор не должен ронять подъём
                if self.require_durable:
                    raise SessionPersistenceError("unavailable") from None
                logger.warning(
                    "sessions: восстановление %s не удалось", session.id, exc_info=True
                )
        return session

    @staticmethod
    def _refresh_session(session: Session, record: SessionRecord) -> None:
        # Preserve the owning lock objects while replacing an uncertain hot copy with stored data.
        restored = Session.from_record(record)
        session.state, session.anketa, session.usage = restored.state, restored.anketa, restored.usage
        session.has_started_opening = restored.has_started_opening
        session.created_at, session.last_active = restored.created_at, restored.last_active
        session.storage_revision = restored.storage_revision
        session._storage_uncertain = False

    def _restore_known_lead_outcome(self, session: Session) -> None:
        """Восстанавливает подтверждённый CRM outcome после сбоя owner-write.

        ``persist_submission`` сначала атомарно пишет локальный snapshot в
        общий production volume и только затем Postgres. Если CRM уже вернула
        leadID, а база в этот момент упала, в БД останется ``in_flight``. Без
        этой сверки следующий процесс превратит известный успех в
        ``outcome_unknown``. Из snapshot читается только технический crm_lead,
        ПД и остальные поля сессии никогда не восстанавливаются из файла.
        """
        db_lead = session.state.get("crm_lead")
        if not isinstance(db_lead, dict) or db_lead.get("status") != "in_flight":
            return
        path = SESSIONS_DIR / f"{session.id}.json"
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            snapshot_state = snapshot.get("state")
            snapshot_lead = (
                snapshot_state.get("crm_lead")
                if isinstance(snapshot_state, dict)
                else None
            )
            if (
                not isinstance(snapshot_lead, dict)
                or snapshot_lead.get("status") != "submitted"
                or not isinstance(snapshot_lead.get("lead_id"), int)
            ):
                return
            # Поддерево не содержит анкетных данных; копируем JSON-значения,
            # чтобы последующая мутация сессии не меняла прочитанный объект.
            session.state["crm_lead"] = deepcopy(snapshot_lead)
            # Сразу самовосстанавливаем Postgres. Если он всё ещё недоступен,
            # owner-write безопасно останется в snapshot и повторится при
            # следующем восстановлении; CRM POST при этом не повторяется.
            self._save_record(session, lead_owner=True)
            logger.warning(
                "sessions: подтверждённый lead outcome %s восстановлен из snapshot",
                session.id,
            )
        except (OSError, ValueError, TypeError):
            return

    def _write_snapshot(self, session: Session) -> None:
        try:
            path = SESSIONS_DIR / f"{session.id}.json"
            tmp = path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(session.snapshot_dict(), f, ensure_ascii=False, indent=2, default=str)
            tmp.replace(path)
        except Exception:  # noqa: BLE001 — снапшот не должен ронять пайплайн
            pass

    def _save_record(self, session: Session, *, lead_owner: bool = False) -> None:

        if self._repository is None:
            return
        try:
            record = session.storage_record()
            if lead_owner:
                self._repository.save_lead_state(record)
            else:
                self._repository.save(record)
        except Exception:  # noqa: BLE001 — база не должна ронять разговор
            logger.warning(
                "sessions: не удалось сохранить %s", session.id, exc_info=True
            )

    def _load_record(self, session_id: str, *, required: bool = False) -> SessionRecord | None:
        if self._repository is None:
            if required or self.require_durable:
                raise SessionPersistenceError("not_configured")
            return None
        try:
            return self._repository.load(session_id)
        except Exception:  # noqa: BLE001
            if required or self.require_durable:
                raise SessionPersistenceError("unavailable") from None
            logger.warning(
                "sessions: не удалось прочитать %s", session_id, exc_info=True
            )
            return None

    def _delete_before(self, cutoff: datetime) -> int:
        if self._repository is None:
            return 0
        try:
            return self._repository.delete_before(cutoff)
        except Exception:  # noqa: BLE001
            logger.warning("sessions: чистка не удалась", exc_info=True)
            return 0
