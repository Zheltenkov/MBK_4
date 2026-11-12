"""Independent durable checkpoint and fail-closed onloader HTTP contracts."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import logging
import json

import pytest

import main
from onloader.contracts import OnloaderState
from session_store import SessionStore
from storage.sessions import SessionCommit, SessionPersistenceError, SessionRecord, session_record_digest
from storage.sqlite_sessions import SQLiteSessionRepository
from tests import test_onloader_local_http as existing_http
from tests.test_onloader_local_http import action, bind, create, decide, live, public


onload = existing_http.onload

PRIVATE_ERROR = "synthetic-private-handle-and-phone-89990000000"


def test_commit_ack_describes_a_full_record_visible_from_an_independent_connection(onload):
    sid, _ = bind(onload)
    current = live(onload, sid)
    current.state["checkpoint_test"] = {"purpose": "full-record binding"}
    old_revision = current.storage_revision
    ack = onload.store.persist_durable(current)
    stored = SQLiteSessionRepository(onload.session_path).load(sid)
    assert stored is not None
    assert isinstance(ack, SessionCommit)
    assert ack.session_id == sid
    assert ack.revision == stored.revision == current.storage_revision == old_revision + 1
    assert ack.state_digest == session_record_digest(stored)
    assert stored.state["checkpoint_test"] == current.state["checkpoint_test"]


def test_receiver_observes_committed_intent_before_any_operation(onload):
    sid, _ = bind(onload)
    original = onload.backend.submit
    observed = []
    def receiver(handle, command):
        stored = SQLiteSessionRepository(onload.session_path).load(sid)
        assert stored is not None
        checkpoint = OnloaderState.model_validate(stored.state["onloader"])
        assert checkpoint.status == "signal_pending"
        assert checkpoint.decision == command.decision == "accept"
        assert checkpoint.event_id == command.event_id
        assert checkpoint.presentation_id == command.presentation_id
        assert stored.revision == live(onload, sid).storage_revision
        observed.append(stored.revision)
        return original(handle, command)
    onload.monkeypatch.setattr(onload.backend, "submit", receiver)
    assert decide(onload, sid).status_code == 200
    assert len(observed) == onload.backend.operation_count() == 1


@pytest.mark.parametrize("failure", ["unavailable", "conflict"])
def test_failed_storage_before_intent_ack_forbids_receiver_operation(onload, failure, caplog):
    sid, _ = bind(onload)
    body = action(onload, sid)
    before = onload.repository.load(sid)
    def failed_commit(*args, **kwargs):
        if failure == "conflict":
            raise SessionPersistenceError("conflict")
        raise OSError(PRIVATE_ERROR)
    onload.monkeypatch.setattr(onload.repository, "save_committed", failed_commit)
    with caplog.at_level(logging.WARNING):
        response = decide(onload, sid, body)
    assert response.status_code == (409 if failure == "conflict" else 503)
    assert PRIVATE_ERROR not in response.text
    assert PRIVATE_ERROR not in caplog.text
    assert onload.backend.calls["submit"] == onload.backend.operation_count() == 0
    stored = SQLiteSessionRepository(onload.session_path).load(sid)
    assert stored is not None and before is not None
    assert stored.revision == before.revision
    assert stored.state["onloader"]["decision"] is None


@pytest.mark.parametrize("invalid", ["none", "dict", "wrong_sid", "same_revision", "wrong_digest", "bool_revision"])
def test_invalid_storage_ack_never_authorizes_a_backend_operation(onload, invalid):
    sid, _ = bind(onload)
    body = action(onload, sid)
    revision = live(onload, sid).storage_revision
    def false_ack(record, **kwargs):
        if invalid == "none":
            return None
        if invalid == "dict":
            return {"session_id": sid, "revision": revision + 1, "state_digest": session_record_digest(record)}
        return SessionCommit(session_id="foreign" if invalid == "wrong_sid" else sid,
            revision=revision if invalid == "same_revision" else True if invalid == "bool_revision" else revision + 1,
            state_digest="0" * 64 if invalid == "wrong_digest" else session_record_digest(record))
    onload.monkeypatch.setattr(onload.repository, "save_committed", false_ack)
    response = decide(onload, sid, body)
    assert response.status_code == 503, response.text
    assert onload.backend.calls["submit"] == onload.backend.operation_count() == 0


def test_a_failed_final_checkpoint_is_recovered_by_read_without_resubmission(onload):
    sid, _ = bind(onload)
    original_submit = onload.backend.submit
    original_commit = onload.repository.save_committed
    def receiver_then_storage_failure(handle, command):
        result = original_submit(handle, command)
        def fail_final(*args, **kwargs):
            raise OSError(PRIVATE_ERROR)
        onload.monkeypatch.setattr(onload.repository, "save_committed", fail_final)
        return result
    onload.monkeypatch.setattr(onload.backend, "submit", receiver_then_storage_failure)
    response = decide(onload, sid)
    assert response.status_code == 503
    assert onload.backend.calls["submit"] == onload.backend.operation_count() == 1
    record = SQLiteSessionRepository(onload.session_path).load(sid)
    assert record is not None
    assert record.state["onloader"]["status"] == "signal_pending"
    onload.monkeypatch.setattr(onload.repository, "save_committed", original_commit)
    recovered = SessionStore(max_extract_workers=1, repository=SQLiteSessionRepository(onload.session_path), require_durable=True)
    try:
        with onload.monkeypatch.context() as patch:
            patch.setattr(main, "store", recovered)
            assert public(onload, sid)["status"] in {"signal_pending", "outcome_unknown"}
            assert onload.backend.calls["submit"] == 1
            result = onload.client.post(f"/api/session/{sid}/onloader/reconcile", json={})
            assert result.status_code == 200, result.text
            assert public(onload, sid)["status"] == "queued"
            assert onload.backend.calls["submit"] == 1
    finally:
        recovered.executor.shutdown(wait=True)


def test_stale_full_session_save_cannot_erase_an_acknowledged_decision(onload):
    sid, _ = bind(onload)
    stale = onload.repository.load(sid)
    assert stale is not None
    assert decide(onload, sid).status_code == 200
    failure = None
    try:
        SQLiteSessionRepository(onload.session_path).save_committed(stale)
    except SessionPersistenceError as error:
        failure = error.reason
    latest = onload.repository.load(sid)
    assert latest is not None and latest.state["onloader"]["status"] == "queued"
    assert latest.state["onloader"]["decision"] == "accept"
    assert latest.revision > stale.revision
    assert failure == "conflict"
    assert onload.backend.operation_count() == 1


def test_missing_durable_repository_never_returns_a_commit_receipt():
    store = SessionStore(max_extract_workers=1)
    try:
        session = asyncio.run(store.create(None))
        failure = None
        try:
            store.persist_durable(session)
        except SessionPersistenceError as error:
            failure = error.reason
        assert failure == "not_configured"
    finally:
        store.executor.shutdown(wait=True)


@pytest.mark.parametrize("kind", ["normal", "debt", "context"])
def test_legacy_record_without_revision_or_new_metadata_remains_restorable(tmp_path, kind):
    now = datetime.now(timezone.utc)
    old = SessionRecord(id="legacy-session", created_at=now, last_active=now,
        has_started_opening=True, anketa=None,
        state={"facts": {"client": {"name": "Синтетический клиент"}},
               "chat_history": [{"role": "user", "content": "Нужен кредит"}], "message_count": 2}, usage=[])
    old.state["facts"]["client"]["phone"] = "+70000000534"
    if kind == "debt":
        from debt_policy import DebtPolicyState
        old.state["questionnaire_policy"] = DebtPolicyState().as_state()
    elif kind == "context":
        old.state["customer_context"] = {"context_status": "ready", "binding_digest": "0" * 64}
    assert old.revision == 0
    repository = SQLiteSessionRepository(tmp_path / "legacy.sqlite")
    ack = repository.save_committed(old)
    store = SessionStore(max_extract_workers=1, repository=repository, require_durable=True)
    try:
        session = asyncio.run(store.get(old.id))
        assert session is not None and session.storage_revision == ack.revision == 1
        assert session.state["facts"] == old.state["facts"]
        assert session.state["chat_history"] == old.state["chat_history"]
        assert "onloader" not in session.state
        assert "+70000000534" not in json.dumps(session.snapshot_dict())
        if kind == "context":
            from context_policy import context_delivery_held
            assert context_delivery_held(session.state)
        elif kind == "debt":
            from debt_policy import debt_delivery_held
            assert debt_delivery_held(session.state)
    finally:
        store.executor.shutdown(wait=True)


def test_digest_binds_entire_record_but_not_the_database_revision(onload):
    sid = create(onload)
    record = live(onload, sid).storage_record()
    baseline = session_record_digest(record)
    assert session_record_digest(replace(record, revision=record.revision + 1)) == baseline
    for update in ({"state": {**deepcopy(record.state), "message_count": 999}},
                   {"anketa": {"desired_amount": 1}}, {"usage": [{"role": "conversation"}]},
                   {"has_started_opening": not record.has_started_opening}):
        assert session_record_digest(replace(record, **update)) != baseline


def test_get_during_a_blocked_checkpoint_returns_only_the_previous_committed_state(onload):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    sid, _ = bind(onload)
    body = action(onload, sid)
    before = onload.client.get(f"/api/session/{sid}").json()["state"]
    entered, release = threading.Event(), threading.Event()
    original = onload.repository.save_committed
    def blocked_checkpoint(record, **kwargs):
        if record.state.get("onloader", {}).get("status") == "signal_pending":
            entered.set()
            assert release.wait(3), "Synthetic checkpoint was not released"
        return original(record, **kwargs)
    onload.monkeypatch.setattr(onload.repository, "save_committed", blocked_checkpoint)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(decide, onload, sid, body)
        try:
            assert entered.wait(3)
            response = onload.client.get(f"/api/session/{sid}")
            assert response.status_code == 200
            value = response.json()
            assert value["turn_in_progress"] is True
            assert value["state"]["onloader"]["status"] == "awaiting_consent"
            assert value["state"]["onloader"]["decision"] is None
            assert value["state"]["chat_history"] == before["chat_history"]
            assert onload.backend.calls["submit"] == 0
        finally:
            release.set()
        assert pending.result(timeout=3).status_code == 200
    assert public(onload, sid)["status"] == "queued"
    assert onload.backend.operation_count() == 1


def test_storage_read_failure_returns_unavailable_without_falling_back_to_cached_authority(onload, caplog):
    sid, _ = bind(onload)
    def unreadable(*args):
        raise OSError(PRIVATE_ERROR)
    onload.monkeypatch.setattr(onload.repository, "load", unreadable)
    with caplog.at_level(logging.WARNING):
        response = onload.client.get(f"/api/session/{sid}")
    assert response.status_code == 503
    assert response.json() == {"detail": "session_storage_unavailable"}
    assert PRIVATE_ERROR not in response.text + caplog.text
    assert onload.backend.calls["submit"] == 0


def test_real_sqlite_write_lock_blocks_the_signal_until_storage_is_available(onload):
    import sqlite3

    sid, _ = bind(onload)
    body = action(onload, sid)
    with sqlite3.connect(onload.session_path, isolation_level=None) as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        try:
            response = decide(onload, sid, body)
            assert response.status_code == 503
            assert onload.backend.calls["submit"] == onload.backend.operation_count() == 0
        finally:
            blocker.rollback()
    # The rejected checkpoint created no operation. The same explicit action is safe now.
    assert decide(onload, sid, body).status_code == 200
    assert onload.backend.operation_count() == 1


@pytest.mark.parametrize("callback", ["none", "matching_but_uncommitted"])
def test_the_onloader_service_itself_requires_a_committed_callback_ack(onload, callback):
    from onloader.contracts import OnloaderDecision, OnloaderError

    sid, _ = bind(onload)
    current = live(onload, sid)
    command = OnloaderDecision.model_validate(action(onload, sid))
    def false_callback(session):
        if callback == "none":
            return None
        return SessionCommit(session_id=sid, revision=session.storage_revision + 1,
                             state_digest=session_record_digest(session.storage_record()))
    assert main.onloader_service is not None
    failure = None
    try:
        main.onloader_service.decide(current, command, false_callback)
    except OnloaderError as error:
        failure = error.reason
    assert failure == "onloader_storage_unavailable"
    assert onload.backend.calls["submit"] == onload.backend.operation_count() == 0


@pytest.mark.parametrize("invalid", ["none", "dict", "wrong_sid", "same_revision", "wrong_digest", "bool_revision"])
def test_the_store_itself_never_accepts_a_foreign_or_uncommitted_receipt(onload, invalid):
    sid, _ = bind(onload)
    current = live(onload, sid)
    before = current.storage_revision
    def invalid_receipt(record, **kwargs):
        if invalid == "none":
            return None
        if invalid == "dict":
            return {"session_id": sid, "revision": before + 1, "state_digest": session_record_digest(record)}
        return SessionCommit(session_id="wrong" if invalid == "wrong_sid" else sid,
            revision=before if invalid == "same_revision" else True if invalid == "bool_revision" else before + 1,
            state_digest="0" * 64 if invalid == "wrong_digest" else session_record_digest(record))
    onload.monkeypatch.setattr(onload.repository, "save_committed", invalid_receipt)
    failure = None
    try:
        onload.store.persist_durable(current)
    except SessionPersistenceError as error:
        failure = error.reason
    assert failure == "invalid_ack"
    assert current.storage_revision == before
    assert SQLiteSessionRepository(onload.session_path).load(sid).revision == before


@pytest.mark.parametrize("waiting_operation", ["decision", "empty_stream"])
def test_a_waiting_request_rechecks_committed_state_after_the_first_checkpoint_fails(onload, waiting_operation):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    sid, _ = bind(onload)
    body = action(onload, sid)
    before = onload.client.get(f"/api/session/{sid}").json()["state"]
    first_entered, second_loaded, release = threading.Event(), threading.Event(), threading.Event()
    original_commit = onload.repository.save_committed
    original_get = onload.store.get
    def failing_checkpoint(record, **kwargs):
        if record.state.get("onloader", {}).get("status") == "signal_pending":
            first_entered.set()
            assert release.wait(3), "Synthetic failed checkpoint was not released"
            raise OSError(PRIVATE_ERROR)
        return original_commit(record, **kwargs)
    async def observe_loaded(session_id):
        result = await original_get(session_id)
        if first_entered.is_set() and not release.is_set():
            second_loaded.set()
        return result
    onload.monkeypatch.setattr(onload.repository, "save_committed", failing_checkpoint)
    onload.monkeypatch.setattr(onload.store, "get", observe_loaded)
    def waiting_request():
        if waiting_operation == "decision":
            return decide(onload, sid, body)
        return onload.client.get(f"/api/session/{sid}/stream")
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(decide, onload, sid, body)
        try:
            assert first_entered.wait(3)
            second = executor.submit(waiting_request)
            assert second_loaded.wait(3)
        finally:
            release.set()
        assert first.result(timeout=3).status_code == 503
        response = second.result(timeout=3)
    assert onload.backend.calls["submit"] == onload.backend.operation_count() == 0
    if waiting_operation == "decision":
        assert response.status_code == 503
    else:
        assert response.status_code == 200
        # An empty opening synchronizes the existing conversation, using committed data only.
        normalized = response.text.replace("\r\n", "\n")
        states = [json.loads(part.split("data: ", 1)[1]) for part in normalized.split("\n\n")
                  if part.startswith("event: state\n")]
        assert len(states) == 1
        assert states[0]["state"]["onloader"]["status"] == "awaiting_consent"
        assert states[0]["state"]["onloader"]["decision"] is None
        assert states[0]["state"]["chat_history"] == before["chat_history"]
