"""События развилки: разрез по сценариям и выгрузка «кто просил связаться»."""
from __future__ import annotations

import pytest

import analytics
import entry_routing as er
from analytics import EVENTS, AnalyticsRecorder, MemorySink


@pytest.fixture
def sink():
    memory = MemorySink()
    analytics.set_recorder(AnalyticsRecorder(memory))
    yield memory
    analytics.set_recorder(None)


def _state(**extra):
    return {"session_id": "entry-analytics", "message_count": 2, **extra}


def test_every_decision_is_recorded_with_its_scenario(sink):
    er.record_transition(
        _state(), {"scenario": er.OFF_TOPIC, "intent": "off_topic", "source": "model"}
    )

    (event,) = sink.events
    assert event.event == EVENTS.ENTRY_ROUTED == "entry_routed"
    assert event.payload == {
        "scenario": "off_topic",
        "intent": "off_topic",
        "source": "model",
        "turn": 2,
    }


def test_existing_request_is_recorded_once_without_personal_data(sink):
    state = _state(facts={"client": {"phone": "+79991234567", "name": "Иван"}})

    er.record_transition(
        state, {"scenario": er.EXISTING, "intent": "existing", "source": "dictionary"}
    )

    (request,) = [e for e in sink.events if e.event == EVENTS.EXISTING_CLIENT_REQUEST]
    assert EVENTS.EXISTING_CLIENT_REQUEST == "existing_client_request"
    assert request.payload == {"phone_given": True, "turn": 2}
    assert request.dedup_key == "entry-analytics:existing_client_request"


def test_phone_not_given_is_recorded_as_false(sink):
    er.record_transition(
        _state(), {"scenario": er.EXISTING, "intent": "existing", "source": "model"}
    )

    (request,) = [e for e in sink.events if e.event == EVENTS.EXISTING_CLIENT_REQUEST]
    assert request.payload["phone_given"] is False


def test_nothing_is_recorded_without_a_transition_or_a_session(sink):
    er.record_transition(_state(), None)
    er.record_transition(
        {"message_count": 1},
        {"scenario": er.EXISTING, "intent": "existing", "source": "model"},
    )

    assert sink.events == []
