"""Independent stage-one acceptance: observable rights, I/O, facts and privacy boundaries."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import socket

import pytest
from pydantic import SecretStr, ValidationError

from customer_context import (
    ApplicationReference, AuthorizedApplication, ContextAccessError, ContextCapabilityCodec,
    CrmLookupClient, CurrentApplicationFields, CurrentApplicationSnapshot, CurrentContextService,
    LookupHttpResponse, LookupStatus, SqliteLookupBudget,
)
from state import init_state
from routing import get_matrix, plan


KEY = b"independent-stage-one-synthetic-key-32-bytes"
PHONE = "70000000123"
PRIVATE_HISTORY = "UNTRUSTED_CRM_NOTE_DO_NOT_PUT_IN_LLM"
REFERENCE = ApplicationReference(application_id="case-A", subject_id="person-A", revision="v3")


@dataclass
class Clock:
    now: float = 1800000000.0

    def __call__(self) -> float:
        return self.now


class Source:
    """Read-only source spy: accidental fallback creation is observable, not silently mocked."""

    def __init__(self, value: object) -> None:
        self.value = value
        self.calls: list[ApplicationReference] = []
        self.writes = 0

    def fetch(self, reference: ApplicationReference):
        self.calls.append(reference)
        if isinstance(self.value, Exception):
            raise self.value
        return self.value

    def create(self, reference: ApplicationReference):
        self.writes += 1
        raise AssertionError("A missing current application must never create a replacement")


class Transport:
    def __init__(self, response: LookupHttpResponse | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post(self, **arguments) -> LookupHttpResponse:
        self.calls.append(arguments)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.fixture(autouse=True)
def forbid_connections(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Acceptance tests must not access a live service")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


def snapshot(clock: Clock, **updates) -> CurrentApplicationSnapshot:
    values = dict(reference=REFERENCE, phone=SecretStr(PHONE),
                  observed_at=datetime.fromtimestamp(clock(), timezone.utc))
    values.update(updates)
    return CurrentApplicationSnapshot(**values)


def authorized(clock: Clock, *, fields: CurrentApplicationFields | None = None,
               session_id: str = "chat-A") -> AuthorizedApplication:
    codec = ContextCapabilityCodec(KEY, clock=clock)
    source = Source(snapshot(clock, fields=fields or CurrentApplicationFields()))
    service = CurrentContextService(codec, source, enabled=True, clock=clock)
    token = codec.issue(REFERENCE, session_id=session_id)
    return service.resolve(token, session_id=session_id)


def history_body() -> bytes:
    # An arbitrary upstream name/value is data, never an instruction or a current fact.
    return json.dumps({"leads": [{"name": PRIVATE_HISTORY, "bitrix_id": 712,
        "fields": [{"name": PRIVATE_HISTORY, "bitrix_id": "UF_DEBT_SUMM", "type": "double", "value": 999000}],
        "contacts": [{"name": "Контакт", "bitrix_id": 88, "deals": []}]}]}).encode()


@pytest.mark.parametrize("mode", ["wrong_session", "bad_signature", "expired", "future"])
def test_rejected_authority_never_reads_source_or_creates_replacement(mode):
    clock = Clock()
    codec = ContextCapabilityCodec(KEY, clock=clock)
    source = Source(snapshot(clock))
    service = CurrentContextService(codec, source, enabled=True, clock=clock)
    token = codec.issue(REFERENCE, session_id="chat-A")
    session = "chat-B" if mode == "wrong_session" else "chat-A"
    if mode == "bad_signature":
        body, signature = token.split(".")
        token = body + "." + ("A" if signature[0] != "A" else "B") + signature[1:]
    elif mode == "expired":
        clock.now += 300
    elif mode == "future":
        clock.now -= 1
    with pytest.raises(ContextAccessError):
        service.resolve(token, session_id=session)
    assert source.calls == [], "Reject before spending even a source read"
    assert source.writes == 0


def test_context_disabled_by_default_does_not_contact_an_application_source():
    clock = Clock()
    codec = ContextCapabilityCodec(KEY, clock=clock)
    source = Source(snapshot(clock))
    with pytest.raises(ContextAccessError, match="disabled"):
        CurrentContextService(codec, source, clock=clock).resolve("anything", session_id="chat-A")
    assert source.calls == []


@pytest.mark.parametrize("change", [{"application_id": "case-B"}, {"subject_id": "person-B"}, {"revision": "v2"}])
def test_authenticated_response_must_match_every_reference_component(change):
    clock = Clock()
    codec = ContextCapabilityCodec(KEY, clock=clock)
    wrong = REFERENCE.model_copy(update=change)
    source = Source(snapshot(clock, reference=wrong))
    service = CurrentContextService(codec, source, enabled=True, clock=clock)
    with pytest.raises(ContextAccessError, match="snapshot_mismatch"):
        service.resolve(codec.issue(REFERENCE, session_id="chat-A"), session_id="chat-A")
    assert source.calls == [REFERENCE]
    assert source.writes == 0


@pytest.mark.parametrize("value,reason", [(None, "source_missing"), (TimeoutError("private-note"), "source_unavailable"),
                                         (PermissionError("private-note"), "source_unavailable")])
def test_missing_or_failed_source_is_not_a_new_application(value, reason):
    clock = Clock()
    codec = ContextCapabilityCodec(KEY, clock=clock)
    source = Source(value)
    service = CurrentContextService(codec, source, enabled=True, clock=clock)
    with pytest.raises(ContextAccessError, match=reason) as captured:
        service.resolve(codec.issue(REFERENCE, session_id="chat-A"), session_id="chat-A")
    assert "private-note" not in str(captured.value)
    assert source.writes == 0


def test_inactive_application_is_not_reused_even_with_valid_authority():
    clock = Clock()
    codec = ContextCapabilityCodec(KEY, clock=clock)
    service = CurrentContextService(codec, Source(snapshot(clock, active=False)), enabled=True, clock=clock)
    with pytest.raises(ContextAccessError, match="snapshot_mismatch"):
        service.resolve(codec.issue(REFERENCE, session_id="chat-A"), session_id="chat-A")


@pytest.mark.parametrize("age", [300, 300.001, -0.001])
def test_observation_is_fresh_not_merely_covered_by_a_fresh_signature(age):
    clock = Clock()
    codec = ContextCapabilityCodec(KEY, clock=clock)
    source = Source(snapshot(clock, observed_at=datetime.fromtimestamp(clock() - age, timezone.utc)))
    with pytest.raises(ContextAccessError, match="snapshot_stale"):
        CurrentContextService(codec, source, enabled=True, clock=clock).resolve(
            codec.issue(REFERENCE, session_id="chat-A"), session_id="chat-A")
    assert source.writes == 0


def test_valid_binding_cannot_be_swapped_or_downgraded_by_another_capability():
    clock = Clock()
    codec = ContextCapabilityCodec(KEY, clock=clock)
    source = Source(snapshot(clock))
    service = CurrentContextService(codec, source, enabled=True, clock=clock)
    first = service.resolve(codec.issue(REFERENCE, session_id="chat-A"), session_id="chat-A")
    other = REFERENCE.model_copy(update={"application_id": "case-B"})
    with pytest.raises(ContextAccessError, match="binding_conflict"):
        service.resolve(codec.issue(other, session_id="chat-A"), session_id="chat-A", existing_binding=first.binding_digest)
    with pytest.raises(ContextAccessError):
        service.resolve("not-a-capability", session_id="chat-A", existing_binding=first.binding_digest)
    assert source.calls == [REFERENCE], "A bound-session swap must fail before fetching another application"
    assert first.safe_context_state()["binding_digest"] == first.binding_digest


def test_source_latency_cannot_extend_the_authority_deadline():
    clock = Clock()
    codec = ContextCapabilityCodec(KEY, clock=clock)

    class SlowSource:
        def fetch(self, reference):
            clock.now += 301
            return snapshot(clock)

    service = CurrentContextService(codec, SlowSource(), enabled=True, clock=clock)
    with pytest.raises(ContextAccessError, match="expired"):
        service.resolve(codec.issue(REFERENCE, session_id="chat-A"), session_id="chat-A")


def test_known_current_facts_are_reused_and_loan_amount_does_not_invent_debt():
    clock = Clock()
    fields = CurrentApplicationFields(full_name="Анна Тестовая", desired_amount=250000, birth_date="01.01.1990")
    handle = authorized(clock, fields=fields)
    anketa = handle.to_anketa()
    facts = init_state(anketa)["facts"]
    assert facts["client"]["name"] == "Анна Тестовая"
    assert facts["client"]["phone"]
    assert facts.get("request", {}).get("amount") == 250000
    assert "debts_total" not in anketa
    assert "total" not in facts.get("debts", {})
    assert "assets" not in facts, "Unasked assets must not become false"
    assert not {"consent", "delivered", "accepted", "has_car"}.intersection(anketa)
    assert facts["client"]["age"] >= 18
    next_fields = set(plan(get_matrix(), facts).field_paths)
    assert not {"client.name", "client.phone", "client.age", "request.amount"}.intersection(next_fields)
    # The questionnaire is active; seeding must skip known facts rather than
    # making every question disappear through a disabled feature flag.
    assert next_fields
    assert "client.name" in plan(get_matrix(), {}).field_paths


def test_verified_current_zero_and_missing_remain_different():
    clock = Clock()
    zero = authorized(clock, fields=CurrentApplicationFields(debts_total=0)).to_anketa()
    missing = authorized(clock).to_anketa()
    assert zero["debts_total"] == 0
    assert "debts_total" not in missing


def test_history_is_not_current_form_or_a_public_llm_instruction(tmp_path):
    clock = Clock()
    handle = authorized(clock, fields=CurrentApplicationFields(desired_amount=250000))
    original = handle.to_anketa()
    transport = Transport(LookupHttpResponse(200, history_body()))
    client = CrmLookupClient(enabled=True, token="synthetic-token", budget=SqliteLookupBudget(tmp_path / "budget.sqlite"),
                             transport=transport, clock=clock)
    result = client.lookup(handle)
    assert result.status is LookupStatus.FOUND
    assert result.history is not None
    assert handle.to_anketa() == original
    assert set(result.public_metadata()) == {"history_status"}
    projections = json.dumps([handle.public_metadata(), handle.safe_context_state(), result.public_metadata()])
    assert PRIVATE_HISTORY not in projections and PHONE not in projections and "person-A" not in projections
    assert PRIVATE_HISTORY not in repr(result)
    assert len(transport.calls) == 1


@pytest.mark.parametrize("http,body,status", [(200, b'{"leads":[]}', LookupStatus.NOT_FOUND_OR_OUTSIDE_COVERAGE),
    (401, b"private", LookupStatus.UNAUTHORIZED), (403, b"private", LookupStatus.UNAUTHORIZED),
    (429, b"private", LookupStatus.RATE_LIMITED), (500, b"private", LookupStatus.UNAVAILABLE),
    (200, b'{"leads":"invalid"}', LookupStatus.UNAVAILABLE)])
def test_read_outcomes_are_distinct_and_never_retry_internally(tmp_path, http, body, status):
    clock = Clock()
    handle = authorized(clock)
    transport = Transport(LookupHttpResponse(http, body))
    client = CrmLookupClient(enabled=True, token="synthetic-token", budget=SqliteLookupBudget(tmp_path / "quota.sqlite"),
                             transport=transport, clock=clock)
    result = client.lookup(handle)
    assert result.status is status
    assert len(transport.calls) == 1, "One logical request must not silently consume upstream quota twice"
    assert "private" not in str(result.public_metadata())


def test_timeout_does_not_mean_not_found_and_spent_reservations_are_not_refunded(tmp_path):
    clock = Clock()
    handle = authorized(clock)
    transport = Transport(TimeoutError(PRIVATE_HISTORY))
    client = CrmLookupClient(enabled=True, token="synthetic-token", budget=SqliteLookupBudget(tmp_path / "quota.sqlite", limit=2),
                             transport=transport, clock=clock)
    assert client.lookup(handle).status is LookupStatus.UNAVAILABLE
    assert client.lookup(handle).status is LookupStatus.UNAVAILABLE
    assert client.lookup(handle).status is LookupStatus.RATE_LIMITED
    assert len(transport.calls) == 2


def test_expired_handle_cannot_redeem_even_a_cached_history(tmp_path):
    clock = Clock()
    handle = authorized(clock)
    transport = Transport(LookupHttpResponse(200, history_body()))
    client = CrmLookupClient(enabled=True, token="synthetic-token", budget=SqliteLookupBudget(tmp_path / "quota.sqlite"),
                             transport=transport, cache_ttl=300, clock=clock)
    assert client.lookup(handle).status is LookupStatus.FOUND
    clock.now += 300
    with pytest.raises(ContextAccessError, match="expired"):
        client.lookup(handle)
    assert len(transport.calls) == 1


def test_snapshot_deadline_still_applies_after_a_fresh_capability_was_issued(tmp_path):
    """A 299s-old observation cannot acquire another 300s of lookup authority."""
    clock = Clock()
    codec = ContextCapabilityCodec(KEY, clock=clock)
    source = Source(snapshot(clock, observed_at=datetime.fromtimestamp(clock() - 299, timezone.utc)))
    handle = CurrentContextService(codec, source, enabled=True, clock=clock).resolve(
        codec.issue(REFERENCE, session_id="chat-A"), session_id="chat-A")
    transport = Transport(LookupHttpResponse(200, history_body()))
    budget = SqliteLookupBudget(tmp_path / "quota.sqlite", limit=1)
    client = CrmLookupClient(enabled=True, token="synthetic-token", budget=budget, transport=transport, clock=clock)
    clock.now += 1
    with pytest.raises(ContextAccessError):
        client.lookup(handle)
    assert transport.calls == []
    assert budget.reserve(clock()) is True, "Expired observation must not consume even one quota reservation"


def test_no_unsealed_or_browser_shaped_handle_can_spend_a_lookup(tmp_path):
    clock = Clock()
    legitimate = authorized(clock)
    transport = Transport(LookupHttpResponse(200, b'{"leads":[]}'))
    client = CrmLookupClient(enabled=True, token="synthetic-token", budget=SqliteLookupBudget(tmp_path / "quota.sqlite"),
                             transport=transport, clock=clock)
    with pytest.raises(TypeError):
        client.lookup({"phone": PHONE, "form_id": "case-A"})
    with pytest.raises(ContextAccessError):
        client.lookup(replace(legitimate, _seal=object()))
    assert transport.calls == []


def test_budget_is_shared_between_instances_and_atomic_at_the_boundary(tmp_path):
    path = tmp_path / "shared-budget.sqlite"
    SqliteLookupBudget(path, limit=7)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: SqliteLookupBudget(path, limit=7).reserve(100000.0), range(24)))
    assert sum(results) == 7
    assert SqliteLookupBudget(path, limit=7).reserve(100000.0 + 86400) is True


def test_browser_fields_cannot_change_the_strict_authoritative_allowlist():
    with pytest.raises(ValidationError):
        CurrentApplicationFields.model_validate({"desired_amount": 250000, "history": {"name": PRIVATE_HISTORY}})
    with pytest.raises(ValidationError):
        CurrentApplicationFields(debts_total=True)
