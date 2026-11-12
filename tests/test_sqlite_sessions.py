"""Provider tests use real SQLite transactions and independent connections."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest

from storage.sessions import SessionRecord, session_record_digest
from storage.sqlite_sessions import SessionRevisionConflict, SQLiteSessionRepository


def record(session_id: str = "synthetic-session", **changes: object) -> SessionRecord:
    now = datetime.now(timezone.utc)
    initial = SessionRecord(id=session_id, created_at=now, last_active=now, has_started_opening=True,
                            anketa=None, state={"facts": {"debts": {"total": 400000}},
                                                "chat_history": [{"role": "user", "content": "Тестовый вопрос"}],
                                                "crm_lead": {"status": "not_sent"}}, usage=[])
    return replace(initial, **changes)


def test_reopen_preserves_full_record_and_fingerprint(tmp_path: Path) -> None:
    path = tmp_path / "sessions.sqlite"
    value = record()
    ack = SQLiteSessionRepository(path).save_committed(value)
    restored = SQLiteSessionRepository(path).load(value.id)
    assert restored == replace(value, revision=1)
    assert ack.session_id == value.id and ack.revision == 1
    assert ack.state_digest == session_record_digest(value) == session_record_digest(restored)


def test_successive_commits_require_the_acknowledged_revision(tmp_path: Path) -> None:
    repository = SQLiteSessionRepository(tmp_path / "sessions.sqlite")
    value = record()
    ack = repository.save_committed(value)
    updated = replace(value, revision=ack.revision, has_started_opening=False)
    second = repository.save_committed(updated)
    assert second.revision == 2
    with pytest.raises(SessionRevisionConflict):
        repository.save_committed(updated)
    assert repository.load(value.id) == replace(updated, revision=2)


def test_competing_connections_cannot_overwrite_a_committed_decision(tmp_path: Path) -> None:
    path = tmp_path / "sessions.sqlite"
    repository = SQLiteSessionRepository(path)
    value = record()
    repository.save_committed(value)
    candidate = replace(value, revision=1, state={**value.state, "onloader": {"status": "signal_pending", "decision": "decline"}})

    def commit(_: int) -> bool:
        try:
            SQLiteSessionRepository(path).save_committed(candidate)
        except SessionRevisionConflict:
            return False
        return True

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(commit, range(6))) == 1
    assert repository.load(value.id) == replace(candidate, revision=2)


def test_db_owned_lead_change_is_rejected_before_commit(tmp_path: Path) -> None:
    repository = SQLiteSessionRepository(tmp_path / "sessions.sqlite")
    value = record()
    repository.save_committed(value)
    changed = replace(value, revision=1, state={**value.state, "crm_lead": {"status": "submitted", "lead_id": 1}})
    with pytest.raises(SessionRevisionConflict):
        repository.save_committed(changed)
    assert repository.load(value.id) == replace(value, revision=1)
    ack = repository.save_committed(changed, lead_owner=True)
    assert ack.revision == 2
    assert repository.load(value.id) == replace(changed, revision=2)


@pytest.mark.parametrize("hold", ["onloader", "questionnaire_policy", "customer_context"])
def test_authoritative_hold_cannot_be_removed_by_a_stale_lead_claim(tmp_path: Path, hold: str) -> None:
    repository = SQLiteSessionRepository(tmp_path / "sessions.sqlite")
    value = record(state={hold: {}, "crm_lead": {"status": "not_sent"}})
    repository.save_committed(value)
    stale = replace(value, state={"crm_lead": {"status": "in_flight"}})
    assert repository.claim_lead_submission(stale).granted is False
    assert repository.load(value.id) == replace(value, revision=1)


def test_only_one_ordinary_claim_can_commit(tmp_path: Path) -> None:
    path = tmp_path / "sessions.sqlite"
    repository = SQLiteSessionRepository(path)
    value = record()
    repository.save_committed(value)
    candidate = replace(value, revision=1, state={**value.state, "crm_lead": {"status": "in_flight"}})
    first = repository.claim_lead_submission(candidate)
    second = SQLiteSessionRepository(path).claim_lead_submission(candidate)
    assert first.granted is True and first.revision == 2
    assert second.granted is False
    assert second.lead_state == {"status": "in_flight"}
    assert repository.load(value.id).revision == 2


def test_serialization_failure_commits_nothing(tmp_path: Path) -> None:
    repository = SQLiteSessionRepository(tmp_path / "sessions.sqlite")
    value = record()
    repository.save_committed(value)
    invalid = replace(value, revision=1, state={**value.state, "invalid": float("nan")})
    with pytest.raises(ValueError):
        repository.save_committed(invalid)
    assert repository.load(value.id) == replace(value, revision=1)


def test_active_window_and_retention_are_consistent(tmp_path: Path) -> None:
    repository = SQLiteSessionRepository(tmp_path / "sessions.sqlite")
    fresh = record("fresh")
    old = record("old", last_active=fresh.last_active - timedelta(days=3))
    repository.save_committed(fresh)
    repository.save_committed(old)
    cutoff = fresh.last_active - timedelta(days=1)
    assert [value.id for value in repository.load_active_since(cutoff)] == [fresh.id]
    assert repository.delete_before(cutoff) == 1
    assert repository.load(old.id) is None
    assert repository.load(fresh.id) is not None


def test_timezone_representation_and_microsecond_retention_boundary_survive(tmp_path: Path) -> None:
    repository = SQLiteSessionRepository(tmp_path / "sessions.sqlite")
    local = datetime(2026, 11, 7, 12, 0, 0, 1, tzinfo=timezone(timedelta(hours=3)))
    value = record(created_at=local, last_active=local)
    ack = repository.save_committed(value)
    restored = repository.load(value.id)
    assert ack.state_digest == session_record_digest(restored) == session_record_digest(value)
    assert restored.last_active.isoformat() == local.isoformat()
    cutoff = local.astimezone(timezone.utc)
    assert len(repository.load_active_since(cutoff)) == 1
    assert repository.delete_before(cutoff) == 0
    assert repository.delete_before(cutoff + timedelta(microseconds=1)) == 1


@pytest.mark.parametrize("revision", ["corrupt", 1.5, 0, -1])
def test_corrupt_stored_revision_is_not_normalized_into_a_usable_session(tmp_path: Path, revision: object) -> None:
    path = tmp_path / "sessions.sqlite"
    repository = SQLiteSessionRepository(path)
    value = record()
    repository.save_committed(value)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA ignore_check_constraints=ON")
        db.execute("UPDATE sessions SET revision=?", (revision,))
    with pytest.raises(ValueError):
        repository.load(value.id)
