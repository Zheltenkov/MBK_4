"""Сессии переживают рестарт процесса.

До этого состояние диалога жило только в словаре внутри процесса: рестарт
backend — то есть каждая выкатка — стирал все активные разговоры, и клиент на
середине анкеты получал бота, который его не помнит. Отдельно опасен автомат
отправки лида: его статус лежит в том же состоянии, и потеря записи между
`in_flight` и `submitted` означала повторный лид в CRM.
"""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

import core
from session_store import Session, SessionStore
from state import init_state
from storage.sessions import SessionRecord
from usage_tracker import SessionUsage


class FakeRepository:
    """Хранилище в словаре: тесты проверяют поведение store, а не драйвер БД."""

    def __init__(self) -> None:
        self.rows: dict[str, SessionRecord] = {}
        self.saves = 0
        self.fail = False

    def save(self, record: SessionRecord) -> None:
        self.saves += 1
        if self.fail:
            raise RuntimeError("база недоступна")
        self.rows[record.id] = deepcopy(record)

    def save_lead_state(self, record: SessionRecord) -> None:
        """Тестовый owner-write исхода lead-intake."""
        self.save(record)

    def load(self, session_id: str) -> SessionRecord | None:
        if self.fail:
            raise RuntimeError("база недоступна")
        return self.rows.get(session_id)

    def load_active_since(self, moment: datetime) -> list[SessionRecord]:
        if self.fail:
            raise RuntimeError("база недоступна")
        return [row for row in self.rows.values() if row.last_active >= moment]

    def delete_before(self, moment: datetime) -> int:
        stale = [key for key, row in self.rows.items() if row.last_active < moment]
        for key in stale:
            del self.rows[key]
        return len(stale)


@pytest.fixture
def repository() -> FakeRepository:
    return FakeRepository()


def _new_store(repository) -> SessionStore:
    return SessionStore(max_extract_workers=1, repository=repository)


async def _seeded_session(store: SessionStore):
    session = await store.create(None)
    session.state["facts"] = {"client": {"name": "Ирина", "phone": "+79990001122"}}
    session.state["chat_history"] = [{"role": "user", "content": "нужно 500 тысяч"}]
    session.state["message_count"] = 3
    store.persist(session)
    return session


def test_session_is_restored_after_process_restart(repository):
    async def scenario() -> None:
        first = _new_store(repository)
        session = await _seeded_session(first)

        # Новый store — это новый процесс: разделяемая только база.
        second = _new_store(repository)
        restored = await second.get(session.id)

        assert restored is not None
        assert restored.state["facts"]["client"]["name"] == "Ирина"
        assert restored.state["chat_history"] == [
            {"role": "user", "content": "нужно 500 тысяч"}
        ]
        assert restored.state["message_count"] == 3

    asyncio.run(scenario())


def test_in_flight_lead_is_not_sent_twice_after_restart(repository):
    """Главный риск включения CRM: дубль лида из-за рестарта в середине отправки."""

    async def scenario() -> None:
        first = _new_store(repository)
        session = await _seeded_session(first)
        session.state["crm_lead"]["status"] = "in_flight"
        first.persist(session)

        second = _new_store(repository)
        restored = await second.get(session.id)

        assert restored is not None
        assert restored.state["crm_lead"]["status"] == "in_flight"

    asyncio.run(scenario())


def test_transient_objects_are_not_stored(repository):
    """`_plan_object` — живой объект Plan, в JSON он не сериализуется.

    Ключи с подчёркиванием живут ровно один ход и пересчитываются в
    prepare_turn, поэтому в хранилище им делать нечего.
    """

    async def scenario() -> None:
        store = _new_store(repository)
        session = await store.create(None)
        session.state["_plan_object"] = object()
        store.persist(session)

        assert "_plan_object" not in repository.rows[session.id].state

    asyncio.run(scenario())


def test_usage_survives_restart(repository):
    async def scenario() -> None:
        first = _new_store(repository)
        session = await first.create(None)
        session.usage.add("qwen/qwen3.7-max", "conversation", 1000, 200, 700)
        first.persist(session)

        second = _new_store(repository)
        restored = await second.get(session.id)

        assert restored is not None
        summary = restored.usage.summary()
        assert summary["total_input_tokens"] == 1000
        assert summary["total_output_tokens"] == 200
        assert summary["total_cached_tokens"] == 700
        assert summary["total_cost_usd"] > 0

    asyncio.run(scenario())


def test_dialog_continues_when_the_database_is_down(repository):
    """Недоступная база не должна ронять разговор — как и в аналитике."""

    async def scenario() -> None:
        store = _new_store(repository)
        session = await store.create(None)
        repository.fail = True

        store.persist(session)  # не выбрасывает
        assert await store.get(session.id) is session

    asyncio.run(scenario())


def test_store_works_without_a_repository():
    async def scenario() -> None:
        store = SessionStore(max_extract_workers=1)
        session = await store.create(None)
        assert await store.get(session.id) is session
        store.persist(session)

    asyncio.run(scenario())


def test_restore_loads_only_recently_active_sessions(repository):
    """Разговор недельной давности возрождать не надо — только свежие."""

    async def scenario() -> None:
        first = _new_store(repository)
        fresh = await _seeded_session(first)
        old = await _seeded_session(first)
        old.last_active = datetime.now(timezone.utc) - timedelta(hours=48)
        first.persist(old)

        second = _new_store(repository)
        loaded = await second.restore(max_age_hours=24)

        assert loaded == 1
        assert {s.id for s in second.all_sessions()} == {fresh.id}

    asyncio.run(scenario())


def test_restored_sessions_are_visible_to_the_abandoned_lead_sweeper(repository):
    """Sweeper читает all_sessions(); после рестарта там должно быть не пусто."""

    async def scenario() -> None:
        first = _new_store(repository)
        session = await _seeded_session(first)

        second = _new_store(repository)
        await second.restore(max_age_hours=24)

        assert [s.id for s in second.all_sessions()] == [session.id]

    asyncio.run(scenario())


@pytest.fixture
def offline(monkeypatch):
    """Настоящий ход без сети — чтобы проверять реальное состояние, а не макет."""
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *a, **k: {"fact_updates": [], "internal_summary": ""},
    )
    monkeypatch.setenv("MBK_ROUTER_MOCK", "1")
    monkeypatch.delenv("MBK_DADATA_API", raising=False)
    monkeypatch.setenv("MBK_FACT_INTERPRETATION_MODE", "legacy")


def test_state_after_a_real_turn_is_strictly_json_serialisable(matrix, offline):
    """Дисковый снапшот пишется с `default=str` и прощает что угодно.

    В базу так нельзя: объект, молча превращённый в свой repr, вернулся бы
    после рестарта строкой и сломал ход. Поэтому запись строгая — и вот тест,
    который ловит появление в state чего-то несериализуемого.
    """
    state = init_state()
    state["session_id"] = "abc123"
    state["facts"] = {"client": {"name": "Василий"}}
    prepared = core.prepare_turn(state, "нужны деньги", submit_delivery=False)
    result = core.commit_turn(prepared, "нужны деньги", "Василий, какая сумма нужна?")

    session = Session(
        id="abc123",
        state=result["current_state"],
        anketa=None,
        usage=SessionUsage(session_id="abc123"),
    )

    json.dumps(session.storage_record().state, ensure_ascii=False)


def test_dropping_stale_sessions_removes_them_from_storage(repository):
    """Иначе они вернутся при следующем рестарте и снова займут память."""

    async def scenario() -> None:
        store = _new_store(repository)
        session = await _seeded_session(store)
        session.last_active = datetime.now(timezone.utc) - timedelta(hours=48)
        store.persist(session)

        dropped = await store.drop_stale(max_age_hours=24)

        assert dropped == 1
        assert repository.rows == {}

    asyncio.run(scenario())


def test_restored_in_flight_submission_is_marked_unknown(repository):
    """Иначе лид, прерванный рестартом, зависает навсегда и молча теряется.

    Аудит 12.08.2026: recover_interrupted_submission существует ровно для этого
    случая, но не вызывался нигде. До появления таблицы сессий это было
    неважно — восстанавливать было нечего. Теперь сессия возвращается со
    статусом in_flight, submit её больше не трогает (статус не NOT_SENT), и
    обращение исчезает без следа.
    """

    async def scenario() -> None:
        first = _new_store(repository)
        session = await _seeded_session(first)
        session.state["crm_lead"]["status"] = "in_flight"
        first.persist(session)

        second = _new_store(repository)
        await second.restore(max_age_hours=24)

        import main

        main._recover_interrupted_submissions(second, _coordinator(second))

        (recovered,) = second.all_sessions()
        assert recovered.state["crm_lead"]["status"] == "outcome_unknown"
        assert "retry" in recovered.state["crm_lead"]["last_error"]

    asyncio.run(scenario())


def test_interrupted_submission_recovery_survives_a_second_restart(repository):
    """Owner-write обязан убрать in_flight не только из памяти, но и из БД."""

    async def scenario() -> None:
        first = _new_store(repository)
        session = await _seeded_session(first)
        session.state["crm_lead"]["status"] = "in_flight"
        first.persist(session)

        second = _new_store(repository)
        recovered = await second.get(session.id)
        assert recovered is not None
        from lead_intake_coordinator import LeadIntakeCoordinator

        coordinator = LeadIntakeCoordinator(
            object(),
            persist=second.persist,
            persist_submission=second.persist_submission,
        )
        coordinator.recover_interrupted_submission(recovered)

        third = _new_store(repository)
        restored_again = await third.get(session.id)
        assert restored_again is not None
        assert restored_again.state["crm_lead"]["status"] == "outcome_unknown"

    asyncio.run(scenario())


def test_known_success_survives_db_failure_after_crm_response(repository):
    """LeadID в общем snapshot сильнее устаревшего in_flight из Postgres."""

    async def scenario() -> None:
        first = _new_store(repository)
        session = await _seeded_session(first)
        session.state["crm_lead"]["status"] = "in_flight"
        first.persist(session)

        session.state["crm_lead"].update(
            {"status": "submitted", "lead_id": 777}
        )
        repository.fail = True
        first.persist_submission(session)
        repository.fail = False
        assert repository.rows[session.id].state["crm_lead"]["status"] == "in_flight"

        second = _new_store(repository)
        restored = await second.get(session.id)

        assert restored is not None
        assert restored.state["crm_lead"]["status"] == "submitted"
        assert restored.state["crm_lead"]["lead_id"] == 777
        assert repository.rows[session.id].state["crm_lead"]["status"] == "submitted"

    asyncio.run(scenario())


def _coordinator(store: SessionStore):
    from lead_intake_coordinator import LeadIntakeCoordinator

    class _NeverCalled:
        def lead_intake(self, payload):  # pragma: no cover — не должен вызываться
            raise AssertionError("восстановление не отправляет лид повторно")

    return LeadIntakeCoordinator(_NeverCalled(), persist=store.persist)


def test_interrupted_submission_is_recovered_when_the_client_returns_later(repository):
    """Сессия живёт 72 часа, стартовый restore() поднимает сутки.

    Клиент, чья отправка прервалась позавчера, приходит не через restore(), а
    через ленивый store.get(). Раньше на этом пути восстановление не звалось
    вовсе, и автомат считал запрос летящим вечно: submit его не трогает (статус
    не NOT_SENT), лид не уходит, разбирать нечего — потеря молчаливая.
    """

    async def scenario() -> None:
        first = _new_store(repository)
        session = await _seeded_session(first)
        session.state["crm_lead"]["status"] = "in_flight"
        session.last_active = datetime.now(timezone.utc) - timedelta(hours=30)
        first.persist(session)

        import main

        second = _new_store(repository)
        main._recover_interrupted_submissions(second, _coordinator(second))
        # Окно restore осознанно уже срока хранения — эта сессия в него не попала.
        assert await second.restore(max_age_hours=24) == 0

        returned = await second.get(session.id)

        assert returned is not None
        assert returned.state["crm_lead"]["status"] == "outcome_unknown"
        assert "retry" in returned.state["crm_lead"]["last_error"]
        # И в базе тоже: следующий процесс не должен заново видеть in_flight.
        assert repository.rows[session.id].state["crm_lead"]["status"] == "outcome_unknown"

    asyncio.run(scenario())


def test_recovery_touches_only_interrupted_submissions(repository):
    """Уже отправленный лид подъёмом из базы не переписывается."""

    async def scenario() -> None:
        first = _new_store(repository)
        session = await _seeded_session(first)
        session.state["crm_lead"]["status"] = "submitted"
        session.state["crm_lead"]["lead_id"] = 4242
        first.persist(session)

        import main

        second = _new_store(repository)
        main._recover_interrupted_submissions(second, _coordinator(second))
        returned = await second.get(session.id)

        assert returned is not None
        assert returned.state["crm_lead"]["status"] == "submitted"
        assert returned.state["crm_lead"]["lead_id"] == 4242

    asyncio.run(scenario())
