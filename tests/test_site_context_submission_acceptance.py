"""A real intake trigger counter with a positive control, independent of feature-off defaults."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from delivery.crm_contracts import LeadIntakeResponse
from lead_intake_coordinator import LeadIntakeCoordinator
from session_store import Session
from state import init_state
from usage_tracker import SessionUsage


NOW = datetime(2026, 11, 6, tzinfo=timezone.utc)


class IntakeCounter:
    def __init__(self):
        self.calls = 0

    def lead_intake(self, payload):
        self.calls += 1
        return LeadIntakeResponse.model_validate({"success": True, "result": {"leadID": 902001}})


def eligible_session(identifier: str) -> Session:
    state = init_state({"full_name": "Тестовый Клиент", "phone": "+70000000123"})
    return Session(id=identifier, state=state, anketa=None, usage=SessionUsage(identifier),
                   created_at=NOW - timedelta(hours=2), last_active=NOW - timedelta(hours=1))


@pytest.mark.parametrize("mode", ["completed", "abandoned"])
@pytest.mark.parametrize("context", [{"context_status": "pending"}, {"context_status": "ready"},
    {"context_status": "forbidden"}, {"context_status": "unavailable"}, {"context_status": "stale"}, None])
def test_no_second_intake_even_when_other_submission_conditions_are_satisfied(mode, context):
    counter = IntakeCounter()
    coordinator = LeadIntakeCoordinator(counter, persist=lambda _: None, now=lambda: NOW)
    protected = eligible_session("protected-context")
    protected.state["customer_context"] = context

    def submit(session):
        if mode == "completed":
            return coordinator.submit_completed(session, site_consent_shown=True, site_consent_answer=True)
        return coordinator.submit_partial_if_due(session, now=NOW)

    outcome = submit(protected)
    assert outcome.attempted is False
    assert counter.calls == 0, "Current-context reads must not trigger a new lead"
    assert protected.state["crm_lead"]["status"] == "not_sent"

    # This demonstrates that the trigger is live and the context hold caused the zero.
    control = eligible_session("ordinary-control")
    ordinary = submit(control)
    assert ordinary.attempted is True
    assert counter.calls == 1
    assert str(ordinary.lead_id) == "902001"
