"""Policy HTTP publication follows a real SQLite commit, including storage faults."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from threading import Event

import pytest

import main
from onloader import policy as onloader_policy
from session_store import Session
from storage.sessions import SessionPersistenceError
from storage.sqlite_sessions import SQLiteSessionRepository
from tests import test_onloader_local_http as existing_http
from tests.test_onloader_local_http import bind, live
from tests.test_policy_grounded_voice import act, install, semantic_raw

onload = existing_http.onload


class ConnectedRequest:
    async def is_disconnected(self):
        return False


def configure(onload):
    cfg = replace(main.load_config(), policy_voice_enabled=True, policy_semantic_enabled=True,
                  extractor_model="synthetic-extractor")
    onload.monkeypatch.setattr(main, "load_config", lambda: cfg)
    return cfg


def test_first_visible_chunk_already_has_durable_no_and_full_history(onload):
    configure(onload)
    sid, _ = bind(onload)
    user = "Ага, тогда без передачи."
    answer = "Можем спокойно поговорить здесь."
    session = live(onload, sid)
    install(onload.monkeypatch, [answer], [semantic_raw(user_acts=[act(user)])])
    observed = []

    async def run():
        async for event in main._run_turn_sse(session, user, ConnectedRequest()):
            if event["event"] == "chunk":
                stored = SQLiteSessionRepository(onload.session_path).load(sid)
                assert stored is not None
                assert onloader_policy.chat_declined(stored.state)
                assert stored.state["onloader"]["decision"] is None
                assert stored.state["chat_history"][-1]["content"] == answer
            observed.append(event)

    asyncio.run(run())
    assert any(event["event"] == "chunk" for event in observed)
    assert onload.backend.calls["submit"] == 0
    restored = Session.from_record(SQLiteSessionRepository(onload.session_path).load(sid))
    assert onloader_policy.chat_declined(restored.state)
    assert restored.state["chat_history"][-2]["content"] == user


def test_failed_commit_never_publishes_text_or_mutates_hot_history(onload):
    configure(onload)
    sid, _ = bind(onload)
    session = live(onload, sid)
    original = deepcopy(session.state)
    user = "Ага, тогда без передачи."
    install(onload.monkeypatch, ["Можем поговорить здесь."], [semantic_raw(user_acts=[act(user)])])

    def fail(*args, **kwargs):
        raise OSError("synthetic-private-storage-error")

    onload.monkeypatch.setattr(onload.repository, "save_committed", fail)

    async def run():
        return [event async for event in main._run_turn_sse(session, user, ConnectedRequest())]

    events = asyncio.run(run())
    assert not any(event["event"] in {"chunk", "bubbles"} for event in events)
    assert [event["event"] for event in events] == ["error", "done"]
    assert "synthetic-private" not in json.dumps(events)
    assert session.state == original
    assert session._storage_uncertain
    assert not session.turn_lock.locked()
    assert onload.backend.calls["submit"] == 0


def test_detached_state_is_not_published_on_cas_conflict(onload):
    sid, _ = bind(onload)
    session = live(onload, sid)
    old_state = deepcopy(session.state)
    old_revision = session.storage_revision
    competitor = Session.from_record(onload.repository.load(sid))
    competitor.state["competitor"] = True
    onload.store.persist_durable(competitor)
    with pytest.raises(SessionPersistenceError):
        onload.store.commit_state_durable(session, {**old_state, "provisional": True})
    assert session.state == old_state and session.storage_revision == old_revision
    assert session._storage_uncertain
    assert onload.repository.load(sid).state.get("competitor") is True


def test_ambiguous_commit_is_read_back_without_publishing_unacknowledged_text(onload):
    configure(onload)
    sid, _ = bind(onload)
    session = live(onload, sid)
    before = deepcopy(session.state)
    user = "Ага, тогда без передачи."
    install(onload.monkeypatch, ["Можем поговорить здесь."], [semantic_raw(user_acts=[act(user)])])
    save = onload.repository.save_committed

    def commit_then_disconnect(*args, **kwargs):
        save(*args, **kwargs)
        raise OSError("synthetic loss of acknowledgement")

    onload.monkeypatch.setattr(onload.repository, "save_committed", commit_then_disconnect)

    async def run():
        return [event async for event in main._run_turn_sse(session, user, ConnectedRequest())]

    events = asyncio.run(run())
    assert [event["event"] for event in events] == ["error", "done"]
    assert session.state == before and session._storage_uncertain
    authoritative = asyncio.run(onload.store.get(sid))
    assert authoritative is not None and onloader_policy.chat_declined(authoritative.state)
    assert not authoritative._storage_uncertain
    assert onload.backend.calls["submit"] == 0


def test_disconnected_request_retains_lock_until_background_commit_finishes(onload):
    configure(onload)
    sid, _ = bind(onload)
    session = live(onload, sid)
    entered, allow_finish, finished = Event(), Event(), Event()
    original = main._commit_grounded_policy_turn

    def held(*args, **kwargs):
        entered.set()
        assert allow_finish.wait(5)
        try:
            return original(*args, **kwargs)
        finally:
            finished.set()

    user = "Ага, тогда без передачи."
    install(onload.monkeypatch, ["Можем поговорить здесь."], [semantic_raw(user_acts=[act(user)])])
    onload.monkeypatch.setattr(main, "_commit_grounded_policy_turn", held)

    async def run():
        stream = main._run_turn_sse(session, user, ConnectedRequest())
        task = asyncio.create_task(anext(stream))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert session.turn_lock.locked()
        allow_finish.set()
        assert await asyncio.to_thread(finished.wait, 5)
        # The callback is scheduled after the worker returns to this loop.
        await asyncio.wait_for(session.turn_lock.acquire(), 5)
        session.turn_lock.release()
        await stream.aclose()

    asyncio.run(run())
    assert onloader_policy.chat_declined(onload.repository.load(sid).state)
    assert not session.turn_lock.locked()
    assert onload.backend.calls["submit"] == 0
