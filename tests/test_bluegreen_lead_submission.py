"""Во время сине-зелёной выкатки одну сессию ведут два процесса.

deploy/deploy.sh поднимает новый цвет при живом старом и только через
DRAIN_SECONDS=90 останавливает прежний. База сессий у них общая, а
`SessionStore.restore()` на старте поднимает в память все сессии за сутки —
значит сразу оба процесса держат одну и ту же сессию и у обоих тикает sweeper
брошенных лидов. At-most-once автомат при этом жил только в памяти процесса:
оба видели `not_sent`, оба проходили проверку и оба отправляли лид.

Чем это платится: дедуп CRM по телефону (docs/MBK_CRM_BACKEND_CONTRACT.md)
15 минут возвращает тот же leadID, но тело повтора игнорирует целиком — в CRM
остаётся первая заготовка с filled_out=0, а полная анкета пропадает. За
пределами 15 минут это уже второй лид с тем же клиентом.

Поэтому право отправить лид занимается не в памяти, а в общей базе.
"""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from delivery.crm_contracts import LeadIntakeRequest, LeadIntakeResponse
from lead_intake_coordinator import LeadSubmissionStatus, coordinator_for_store
from session_store import SessionStore
from storage.sessions import LEAD_SUBMISSION_STATE_KEY, LeadClaim, SessionRecord


class SharedRepository:
    """Одна таблица `sessions` на оба цвета — как postgres в edge-стеке.

    Повторяет ровно те правила, которые в проде задаёт SQL: JSON-круг при
    записи, неприкосновенность поддерева отправки лида для обычной записи и
    условный захват слота.
    """

    def __init__(self) -> None:
        self.rows: dict[str, SessionRecord] = {}
        self.down = False

    # --- обычная запись: статус отправки принадлежит базе ---------------- #

    def save(self, record: SessionRecord) -> None:
        self._check()
        stored = self._roundtrip(record)
        previous = self.rows.get(record.id)
        if previous is not None and LEAD_SUBMISSION_STATE_KEY in previous.state:
            stored.state[LEAD_SUBMISSION_STATE_KEY] = deepcopy(
                previous.state[LEAD_SUBMISSION_STATE_KEY]
            )
        self.rows[record.id] = stored

    # --- запись владельцем занятого слота ------------------------------- #

    def save_lead_state(self, record: SessionRecord) -> None:
        self._check()
        self.rows[record.id] = self._roundtrip(record)

    # --- захват слота ---------------------------------------------------- #

    def claim_lead_submission(self, record: SessionRecord) -> LeadClaim:
        self._check()
        previous = self.rows.get(record.id)
        stored_lead = (
            (previous.state.get(LEAD_SUBMISSION_STATE_KEY) or {}) if previous else {}
        )
        if stored_lead.get("status", "not_sent") != "not_sent":
            return LeadClaim(granted=False, lead_state=deepcopy(stored_lead))
        self.rows[record.id] = self._roundtrip(record)
        return LeadClaim(granted=True)

    # --- чтение ---------------------------------------------------------- #

    def load(self, session_id: str) -> SessionRecord | None:
        self._check()
        row = self.rows.get(session_id)
        # Копия, а не ссылка: у процессов общая база, а не общая память.
        return self._roundtrip(row) if row is not None else None

    def load_active_since(self, moment: datetime) -> list[SessionRecord]:
        self._check()
        return [
            self._roundtrip(row)
            for row in self.rows.values()
            if row.last_active >= moment
        ]

    def delete_before(self, moment: datetime) -> int:
        self._check()
        stale = [key for key, row in self.rows.items() if row.last_active < moment]
        for key in stale:
            del self.rows[key]
        return len(stale)

    # -------------------------------------------------------------------- #

    def _check(self) -> None:
        if self.down:
            raise RuntimeError("база недоступна")

    @staticmethod
    def _roundtrip(record: SessionRecord) -> SessionRecord:
        return replace(
            record,
            anketa=json.loads(json.dumps(record.anketa, ensure_ascii=False)),
            state=json.loads(json.dumps(record.state, ensure_ascii=False)),
            usage=json.loads(json.dumps(record.usage, ensure_ascii=False)),
        )

    def lead_state(self, session_id: str) -> dict:
        return self.rows[session_id].state[LEAD_SUBMISSION_STATE_KEY]


class CountingCrmClient:
    """Один CRM на оба цвета: считает POST'ы, как их посчитал бы брокер."""

    def __init__(self) -> None:
        self.requests: list[LeadIntakeRequest] = []

    def lead_intake(self, payload: LeadIntakeRequest) -> LeadIntakeResponse:
        self.requests.append(payload)
        return LeadIntakeResponse.model_validate(
            {"result": {"leadID": 5_000 + len(self.requests)}, "success": True}
        )


@pytest.fixture
def repository() -> SharedRepository:
    return SharedRepository()


def _store(repository: SharedRepository) -> SessionStore:
    return SessionStore(max_extract_workers=1, repository=repository)


async def _abandoned_session(store: SessionStore):
    """Клиент оставил телефон и замолчал — ровно случай брошенного лида."""
    session = await store.create(None)
    session.state["facts"] = {"client": {"name": "Ирина", "phone": "+79990001122"}}
    session.last_active = datetime.now(timezone.utc) - timedelta(minutes=20)
    store.persist(session)
    return session


def test_two_processes_send_the_abandoned_lead_only_once(repository):
    """Оба цвета в окне drain видят одну сессию — POST должен быть один."""

    async def scenario() -> None:
        crm = CountingCrmClient()

        blue = _store(repository)
        session = await _abandoned_session(blue)

        # Новый цвет поднялся при живом старом и поднял ту же сессию.
        green = _store(repository)
        assert await green.restore(max_age_hours=24) == 1

        blue_outcomes = coordinator_for_store(crm, blue).sweep_abandoned(
            blue.all_sessions()
        )
        green_outcomes = coordinator_for_store(crm, green).sweep_abandoned(
            green.all_sessions()
        )

        assert len(crm.requests) == 1
        statuses = {
            outcome.status for outcome in (*blue_outcomes, *green_outcomes)
        }
        assert statuses == {LeadSubmissionStatus.SUBMITTED}
        lead_ids = {outcome.lead_id for outcome in (*blue_outcomes, *green_outcomes)}
        assert lead_ids == {5_001}
        assert repository.lead_state(session.id)["status"] == "submitted"

    asyncio.run(scenario())


def test_stale_copy_cannot_reopen_the_slot_after_submission(repository):
    """Обычная запись старого цвета не имеет права вернуть лид в not_sent."""

    async def scenario() -> None:
        crm = CountingCrmClient()

        blue = _store(repository)
        session = await _abandoned_session(blue)
        green = _store(repository)
        await green.restore(max_age_hours=24)

        coordinator_for_store(crm, green).sweep_abandoned(green.all_sessions())
        assert len(crm.requests) == 1

        # Старый цвет дописывает свою копию сессии: в его памяти лид ещё не
        # отправлен. Это не должно снимать чужой захват.
        blue.persist(session)
        assert repository.lead_state(session.id)["status"] == "submitted"

        coordinator_for_store(crm, blue).sweep_abandoned(blue.all_sessions())

        assert len(crm.requests) == 1

    asyncio.run(scenario())


def test_loser_of_the_race_reports_the_outcome_of_the_winner(repository):
    """Проигравший процесс не должен показывать клиенту ошибку доставки."""

    async def scenario() -> None:
        crm = CountingCrmClient()

        blue = _store(repository)
        session = await _abandoned_session(blue)
        green = _store(repository)
        await green.restore(max_age_hours=24)

        coordinator_for_store(crm, green).sweep_abandoned(green.all_sessions())

        outcome = coordinator_for_store(crm, blue).submit_completed(
            session,
            site_consent_shown=True,
            site_consent_answer=True,
        )

        assert outcome.attempted is False
        assert outcome.status == LeadSubmissionStatus.SUBMITTED
        assert outcome.lead_id == 5_001
        assert session.state["crm_lead"]["lead_id"] == 5_001
        assert len(crm.requests) == 1

    asyncio.run(scenario())


def test_unreachable_storage_keeps_the_lead_unsent_and_retryable(repository):
    """Слот не занят — значит и POST'а нет: повторить можно, дубль — нельзя."""

    async def scenario() -> None:
        crm = CountingCrmClient()
        blue = _store(repository)
        session = await _abandoned_session(blue)
        coordinator = coordinator_for_store(crm, blue)

        repository.down = True
        blocked = coordinator.submit_partial_if_due(session)

        assert crm.requests == []
        assert blocked.attempted is False
        assert blocked.status == LeadSubmissionStatus.NOT_SENT

        repository.down = False
        recovered = coordinator.submit_partial_if_due(session)

        assert recovered.status == LeadSubmissionStatus.SUBMITTED
        assert len(crm.requests) == 1

    asyncio.run(scenario())
