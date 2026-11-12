"""At-most-once и inactivity policy lead-intake без сетевых вызовов."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import analytics
from analytics import AnalyticsRecorder, MemorySink
from delivery.crm import CrmApiError, LeadIntakeDisabledError
from delivery.crm_contracts import LeadIntakeRequest, LeadIntakeResponse
from delivery.crm_submission_gate import LeadSubmissionGate, SubmissionMode
from lead_intake_coordinator import (
    LeadIntakeCoordinator,
    LeadSubmissionStatus,
)
from session_store import Session
from state import init_state
from usage_tracker import SessionUsage


NOW = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)


class FakeLeadClient:
    def __init__(self, result: object | None = None) -> None:
        self.requests: list[LeadIntakeRequest] = []
        self.result = result or LeadIntakeResponse.model_validate(
            {"result": {"leadID": 3_429_999}, "success": True}
        )

    def lead_intake(self, payload: LeadIntakeRequest) -> LeadIntakeResponse:
        self.requests.append(payload)
        if isinstance(self.result, Exception):
            raise self.result
        assert isinstance(self.result, LeadIntakeResponse)
        return self.result


def _session(*, phone: str | None = "+79991234567") -> Session:
    state = init_state()
    if phone is not None:
        state["facts"] = {"client": {"phone": phone, "name": "Тест"}}
    return Session(
        id="session-test",
        state=state,
        anketa=None,
        usage=SessionUsage(session_id="session-test"),
        created_at=NOW - timedelta(minutes=20),
        last_active=NOW - timedelta(minutes=15),
    )


def _coordinator(
    client: FakeLeadClient,
    persisted: list[str],
    *,
    submission_gate: LeadSubmissionGate | None = None,
) -> LeadIntakeCoordinator:
    return LeadIntakeCoordinator(
        client,
        persist=lambda session: persisted.append(session.id),
        inactivity=timedelta(minutes=15),
        now=lambda: NOW,
        submission_gate=submission_gate,
    )


def test_partial_lead_waits_full_15_minutes_and_requires_phone():
    persisted: list[str] = []
    client = FakeLeadClient()
    coordinator = _coordinator(client, persisted)
    session = _session()
    session.last_active = NOW - timedelta(minutes=14, seconds=59)

    assert coordinator.partial_is_due(session, now=NOW) is False
    assert coordinator.partial_is_due(_session(phone=None), now=NOW) is False
    assert client.requests == []


def test_user_message_resets_inactivity_timer():
    persisted: list[str] = []
    coordinator = _coordinator(FakeLeadClient(), persisted)
    session = _session()

    assert coordinator.partial_is_due(session, now=NOW) is True
    coordinator.note_user_activity(session)

    assert session.last_active == NOW
    assert coordinator.partial_is_due(session, now=NOW) is False
    assert persisted == ["session-test"]


def test_partial_lead_duplicates_phone_and_omits_partner_consent():
    persisted: list[str] = []
    client = FakeLeadClient()
    coordinator = _coordinator(client, persisted)
    session = _session()

    outcome = coordinator.submit_partial_if_due(session, now=NOW)

    assert outcome.status == LeadSubmissionStatus.SUBMITTED
    assert outcome.lead_id == 3_429_999
    assert len(client.requests) == 1
    payload = client.requests[0].as_api_dict(exclude_none=True)
    assert payload["PHONE"] == "79991234567"
    assert payload["PHONE_DEAL"] == "79991234567"
    assert payload["UF_FROM_SITE_FILLED_OUT"] == 0
    assert "UF_SITE_AGREE_PARTNERS" not in payload
    assert session.state["crm_lead"]["lead_id"] == 3_429_999


def test_partial_submission_blocks_later_full_resubmission():
    persisted: list[str] = []
    client = FakeLeadClient()
    coordinator = _coordinator(client, persisted)
    session = _session()

    partial = coordinator.submit_partial_if_due(session, now=NOW)
    coordinator.note_user_activity(session)
    completed = coordinator.submit_completed(
        session,
        site_consent_shown=False,
    )

    assert partial.status == LeadSubmissionStatus.SUBMITTED
    assert completed.attempted is False
    assert completed.lead_id == 3_429_999
    assert len(client.requests) == 1
    assert session.state["crm_lead"]["resumed_after_submission"] is True


def test_timeout_marks_unknown_outcome_and_never_retries():
    persisted: list[str] = []
    client = FakeLeadClient(CrmApiError("timeout", retryable=True))
    coordinator = _coordinator(client, persisted)
    session = _session()

    first = coordinator.submit_partial_if_due(session, now=NOW)
    second = coordinator.submit_completed(
        session,
        site_consent_shown=False,
    )

    assert first.status == LeadSubmissionStatus.OUTCOME_UNKNOWN
    assert second.attempted is False
    assert second.status == LeadSubmissionStatus.OUTCOME_UNKNOWN
    assert len(client.requests) == 1


def test_interrupted_in_flight_state_recovers_as_unknown():
    persisted: list[str] = []
    coordinator = _coordinator(FakeLeadClient(), persisted)
    session = _session()
    session.state["crm_lead"]["status"] = "in_flight"

    coordinator.recover_interrupted_submission(session)

    assert session.state["crm_lead"]["status"] == "outcome_unknown"
    assert "retry запрещён" in session.state["crm_lead"]["last_error"]


def test_disabled_transport_does_not_consume_session_attempt():
    persisted: list[str] = []
    client = FakeLeadClient(
        LeadIntakeDisabledError("lead intake disabled"),
    )
    coordinator = _coordinator(client, persisted)
    session = _session()

    outcome = coordinator.submit_partial_if_due(session, now=NOW)

    assert outcome.attempted is False
    assert outcome.status == LeadSubmissionStatus.NOT_SENT
    assert session.state["crm_lead"]["status"] == "not_sent"


def test_runtime_gate_blocks_when_submission_mode_is_not_explicit():
    persisted: list[str] = []
    client = FakeLeadClient()
    coordinator = _coordinator(
        client,
        persisted,
        submission_gate=LeadSubmissionGate(mode=SubmissionMode.BLOCKED),
    )
    session = _session()
    session.state["facts"]["client"]["name"] = "Тестов Тест Тестович"

    outcome = coordinator.submit_completed(
        session,
        site_consent_shown=False,
    )

    assert outcome.attempted is False
    assert outcome.reason == "submission_mode_not_enabled"
    assert session.state["crm_lead"]["status"] == "not_sent"
    assert client.requests == []


def test_runtime_test_gate_requires_exact_test_full_name():
    persisted: list[str] = []
    client = FakeLeadClient()
    gate = LeadSubmissionGate(
        mode=SubmissionMode.TEST,
        allowed_test_phones=frozenset({"79991234567"}),
    )
    coordinator = _coordinator(client, persisted, submission_gate=gate)
    session = _session()
    session.state["facts"]["client"]["name"] = "Тест Тестович"

    outcome = coordinator.submit_completed(
        session,
        site_consent_shown=False,
    )

    assert outcome.attempted is False
    assert outcome.reason == "test_full_name_not_allowed"
    assert client.requests == []


def test_runtime_test_gate_requires_allowlisted_phone():
    persisted: list[str] = []
    client = FakeLeadClient()
    gate = LeadSubmissionGate(
        mode=SubmissionMode.TEST,
        allowed_test_phones=frozenset({"79990000000"}),
    )
    coordinator = _coordinator(client, persisted, submission_gate=gate)
    session = _session()
    session.state["facts"]["client"]["name"] = "Тестов Тест Тестович"

    outcome = coordinator.submit_completed(
        session,
        site_consent_shown=False,
    )

    assert outcome.attempted is False
    assert outcome.reason == "test_phone_not_allowed"
    assert client.requests == []


def test_runtime_test_gate_allows_only_agreed_identity():
    persisted: list[str] = []
    client = FakeLeadClient()
    gate = LeadSubmissionGate(
        mode=SubmissionMode.TEST,
        allowed_test_phones=frozenset({"79991234567"}),
    )
    coordinator = _coordinator(client, persisted, submission_gate=gate)
    session = _session()
    session.state["facts"]["client"]["name"] = "  ТЕСТОВ   Тест Тестович "

    outcome = coordinator.submit_completed(
        session,
        site_consent_shown=False,
    )

    assert outcome.status == LeadSubmissionStatus.SUBMITTED
    assert outcome.lead_id == 3_429_999
    assert len(client.requests) == 1


# --------------------------------------------------------------------------- #
# Ключ связи с карточкой в Битриксе
# --------------------------------------------------------------------------- #
# Просьба дата-инженера заказчика 04.09.2026: связать разговор бота с лидом в
# CRM. Номер лида хранится у нас с первого дня, но в `sessions` — а туда роль
# `analytics_ro` не пускают, там персональные данные. В переписке от телефона
# остаётся только солёный отпечаток. Поэтому номер лида выносится в аналитику
# отдельным событием: один ключ связи и ничего больше.
def _recording():
    sink = MemorySink()
    analytics.set_recorder(AnalyticsRecorder(sink))
    return sink


def test_a_created_lead_puts_its_number_into_analytics():
    sink = _recording()
    try:
        client = FakeLeadClient()
        session = _session()

        _coordinator(client, []).submit_completed(session, site_consent_shown=False)

        events = [e for e in sink.events if e.event == "lead_submitted"]
        assert len(events) == 1
        assert events[0].session_id == session.id
        assert events[0].payload == {"lead_id": 3_429_999}
    finally:
        analytics.set_recorder(None)


def test_the_number_is_recorded_once_per_session():
    """Лид создаётся не более одного раза — строк в аналитике тоже одна."""
    sink = _recording()
    try:
        client = FakeLeadClient()
        session = _session()
        coordinator = _coordinator(client, [])

        coordinator.submit_completed(session, site_consent_shown=False)
        coordinator.submit_completed(session, site_consent_shown=False)

        assert sum(e.event == "lead_submitted" for e in sink.events) == 1
    finally:
        analytics.set_recorder(None)


def test_a_failed_submission_records_nothing():
    """Иначе дата-инженер увидит номер там, где карточки в CRM нет."""
    sink = _recording()
    try:
        client = FakeLeadClient(result=CrmApiError("сеть", retryable=True))

        _coordinator(client, []).submit_completed(_session(), site_consent_shown=False)

        assert not any(e.event == "lead_submitted" for e in sink.events)
    finally:
        analytics.set_recorder(None)


def test_the_number_carries_no_personal_data():
    """Аналитика — вторая система хранения, ПД туда не уезжают по контракту."""
    sink = _recording()
    try:
        _coordinator(FakeLeadClient(), []).submit_completed(_session(), site_consent_shown=False)

        payload = next(
            e.payload for e in sink.events if e.event == "lead_submitted"
        )
        assert set(payload) == {"lead_id"}
    finally:
        analytics.set_recorder(None)


# --------------------------------------------------------------------------- #
# `crm_call_failed` — видимость сбоев обмена с CRM (дизайн 07.09.2026, сделано
# 19.09.2026). Источник правды для stage="lead_intake": повод — дефект
# widget_denis, три дня ронявший каждую квалификацию незаметно для нас, пока
# аналитика видела только успех.
# --------------------------------------------------------------------------- #
def test_gate_rejection_is_recorded_as_crm_call_failed():
    sink = _recording()
    try:
        client = FakeLeadClient()
        coordinator = _coordinator(
            client,
            [],
            submission_gate=LeadSubmissionGate(mode=SubmissionMode.BLOCKED),
        )
        session = _session()
        session.state["facts"]["client"]["name"] = "Тестов Тест Тестович"

        coordinator.submit_completed(session, site_consent_shown=False)

        (event,) = [e for e in sink.events if e.event == "crm_call_failed"]
        assert event.session_id == session.id
        assert event.payload == {
            "stage": "lead_intake",
            "reason": "gate_blocked",
            "retryable": False,
        }
        assert client.requests == []
    finally:
        analytics.set_recorder(None)


def test_disabled_transport_is_recorded_as_gate_blocked():
    """`LeadIntakeDisabledError` — тот же смысловой бакет, что и отказ гейта."""
    sink = _recording()
    try:
        client = FakeLeadClient(LeadIntakeDisabledError("lead intake disabled"))
        coordinator = _coordinator(client, [])
        session = _session()

        coordinator.submit_partial_if_due(session, now=NOW)

        (event,) = [e for e in sink.events if e.event == "crm_call_failed"]
        assert event.payload == {
            "stage": "lead_intake",
            "reason": "gate_blocked",
            "retryable": False,
            "error_class": "LeadIntakeDisabledError",
        }
    finally:
        analytics.set_recorder(None)


def test_lead_intake_call_failure_is_recorded_with_its_reason():
    """OUTCOME_UNKNOWN тоже отказ: лид не создан, ретраить его нельзя."""
    sink = _recording()
    try:
        client = FakeLeadClient(CrmApiError("сеть", retryable=True, reason="timeout"))
        coordinator = _coordinator(client, [])
        session = _session()

        outcome = coordinator.submit_completed(session, site_consent_shown=False)

        assert outcome.status == LeadSubmissionStatus.OUTCOME_UNKNOWN
        (event,) = [e for e in sink.events if e.event == "crm_call_failed"]
        assert event.payload == {
            "stage": "lead_intake",
            "reason": "timeout",
            "retryable": True,
            "error_class": "CrmApiError",
        }
    finally:
        analytics.set_recorder(None)


def test_unexpected_error_during_submission_is_recorded():
    """Ненормализованный сбой (не CrmApiError) — тоже неудавшийся вызов."""
    sink = _recording()
    try:
        client = FakeLeadClient(KeyError("phone"))
        coordinator = _coordinator(client, [])
        session = _session()

        coordinator.submit_completed(session, site_consent_shown=False)

        (event,) = [e for e in sink.events if e.event == "crm_call_failed"]
        assert event.payload == {
            "stage": "lead_intake",
            "reason": "http_error",
            "retryable": False,
            "error_class": "KeyError",
        }
    finally:
        analytics.set_recorder(None)


def test_successful_submission_records_no_crm_call_failed():
    sink = _recording()
    try:
        _coordinator(FakeLeadClient(), []).submit_completed(
            _session(), site_consent_shown=False
        )

        assert not any(e.event == "crm_call_failed" for e in sink.events)
    finally:
        analytics.set_recorder(None)


@pytest.mark.parametrize("scenario", ["existing", "off_topic"])
def test_branch_is_never_swept_into_a_partial_lead(scenario):
    """Телефон, названный в ветке «уже оставлял заявку» или «не наш профиль»,
    не делает сессию брошенной анкетой: через 15 минут тишины ушёл бы второй
    лид."""
    persisted: list[str] = []
    client = FakeLeadClient()
    coordinator = _coordinator(client, persisted)
    session = _session()
    session.state["entry"] = {
        "scenario": scenario,
        "intent": scenario,
        "source": "model",
    }

    assert coordinator.partial_is_due(session, now=NOW) is False
    assert coordinator.sweep_abandoned([session], now=NOW) == []
    assert client.requests == []
