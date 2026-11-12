"""Server context authorization and bounded history access; synthetic data, no network."""
from __future__ import annotations

import json
import threading
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pydantic import SecretStr, ValidationError

from customer_context import (
    ApplicationReference, ContextAccessError, ContextCapabilityCodec, CrmLookupClient,
    CurrentApplicationFields, CurrentApplicationSnapshot, CurrentContextService,
    LookupHttpResponse, LookupStatus, SqliteLookupBudget,
)


class Clock:
    def __init__(self) -> None:
        self.now = 2000000000.0

    def __call__(self) -> float:
        return self.now


class Source:
    def __init__(self, snapshot: CurrentApplicationSnapshot | None) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def fetch(self, reference: ApplicationReference) -> CurrentApplicationSnapshot | None:
        self.calls += 1
        return self.snapshot


class Transport:
    def __init__(self, response: LookupHttpResponse | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post(self, **kwargs) -> LookupHttpResponse:
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.fixture
def setup_context():
    clock = Clock()
    reference = ApplicationReference(application_id="synthetic-form-1", subject_id="subject-1", revision="v1")
    snapshot = CurrentApplicationSnapshot(reference=reference, phone=SecretStr("+7 (999) 000-00-01"),
        fields=CurrentApplicationFields(debts_total=400000, region="Москва", goal="bankruptcy"),
        observed_at=datetime.fromtimestamp(clock(), timezone.utc))
    source = Source(snapshot)
    codec = ContextCapabilityCodec(b"synthetic-test-only-key-32-bytes!!", clock=clock)
    service = CurrentContextService(codec, source, enabled=True, clock=clock)
    token = codec.issue(reference, session_id="session-1")
    return clock, reference, snapshot, source, codec, service, token


@pytest.mark.parametrize("token", ["", "a.b", "a.b.c", "http://example.test/form/1", "89990000001"])
def test_unsigned_browser_values_never_fetch(setup_context, token):
    _, _, _, source, _, service, _ = setup_context
    with pytest.raises(ContextAccessError, match="invalid_capability"):
        service.resolve(token, session_id="session-1")
    assert source.calls == 0


def test_signed_capability_is_session_bound_and_tamper_evident(setup_context):
    _, _, _, source, _, service, token = setup_context
    with pytest.raises(ContextAccessError):
        service.resolve(token, session_id="different-session")
    body, signature = token.split(".")
    with pytest.raises(ContextAccessError):
        service.resolve(body[:-1] + ("A" if body[-1] != "A" else "B") + "." + signature,
                        session_id="session-1")
    assert source.calls == 0


def test_capability_expired_at_exact_boundary_before_source(setup_context):
    clock, _, _, source, _, service, token = setup_context
    clock.now += 300
    with pytest.raises(ContextAccessError, match="expired"):
        service.resolve(token, session_id="session-1")
    assert source.calls == 0


def test_context_binding_conflict_rejected_before_source_and_safe_persistence(setup_context):
    _, reference, _, source, codec, service, token = setup_context
    handle = service.resolve(token, session_id="session-1")
    assert service.binding_digest(token, session_id="session-1") == handle.binding_digest
    assert handle.to_anketa() == {"phone": "89990000001", "debts_total": 400000,
                                  "region": "Москва", "goal": "bankruptcy"}
    safe = json.dumps(handle.safe_context_state())
    for forbidden in [reference.application_id, reference.subject_id, "89990000001", token]:
        assert forbidden not in safe
    assert handle.public_metadata() == {"context_status": "ready", "source": "current_application"}
    different = reference.model_copy(update={"application_id": "another-form"})
    new_token = codec.issue(different, session_id="session-1")
    with pytest.raises(ContextAccessError, match="binding_conflict"):
        service.resolve(new_token, session_id="session-1", existing_binding=handle.binding_digest)
    assert source.calls == 1


@pytest.mark.parametrize("field,value,reason", [
    ("active", False, "snapshot_mismatch"),
    ("reference", ApplicationReference(application_id="wrong", subject_id="wrong", revision="wrong"),
     "snapshot_mismatch"),
    ("observed_at", datetime.fromtimestamp(1999999699, timezone.utc), "snapshot_stale"),
    ("observed_at", datetime.fromtimestamp(2000000001, timezone.utc), "snapshot_stale"),
])
def test_backend_identity_active_and_freshness_required(setup_context, field, value, reason):
    _, _, snapshot, source, _, service, token = setup_context
    source.snapshot = snapshot.model_copy(update={field: value})
    with pytest.raises(ContextAccessError, match=reason):
        service.resolve(token, session_id="session-1")
    assert source.calls == 1


def test_default_disabled_missing_source_and_exception_are_safe(setup_context):
    clock, _, _, source, codec, service, token = setup_context
    with pytest.raises(ContextAccessError, match="disabled"):
        CurrentContextService(codec, source).resolve(token, session_id="session-1")
    assert source.calls == 0
    source.snapshot = None
    with pytest.raises(ContextAccessError, match="source_missing"):
        service.resolve(token, session_id="session-1")
    class BrokenSource:
        def fetch(self, reference):
            raise RuntimeError("raw-secret-phone-token")
    with pytest.raises(ContextAccessError) as caught:
        CurrentContextService(codec, BrokenSource(), enabled=True, clock=clock).resolve(token, session_id="session-1")
    assert str(caught.value) == "source_unavailable"
    assert "raw-secret" not in repr(caught.value)


def test_slow_authoritative_fetch_cannot_redeem_expired_capability(setup_context):
    clock, _, snapshot, _, codec, _, token = setup_context
    class SlowSource:
        def fetch(self, reference):
            clock.now += 301
            return snapshot.model_copy(update={"observed_at": datetime.fromtimestamp(clock(), timezone.utc)})
    with pytest.raises(ContextAccessError, match="expired"):
        CurrentContextService(codec, SlowSource(), enabled=True, clock=clock).resolve(token, session_id="session-1")


def test_lookup_preserves_nested_tree_values_and_keeps_history_out_of_projection(setup_context, tmp_path):
    clock, _, _, _, _, service, token = setup_context
    handle = service.resolve(token, session_id="session-1")
    fields = [{"name": "Число", "bitrix_id": "UF_NUMBER", "type": "integer", "value": 0},
              {"name": "Отказ", "bitrix_id": "UF_FLAG", "type": "boolean", "value": False},
              {"name": "Новый код", "bitrix_id": "UF_FUTURE", "type": "money",
               "value": {"amount": 123.5, "currency": "RUB"}}]
    deal = {"name": "Сделка", "bitrix_id": 3, "fields": fields}
    lead = {"name": "Лид", "bitrix_id": 1, "fields": fields,
            "contacts": [{"name": "Контакт", "bitrix_id": 2, "deals": [deal]}], "deals": [deal]}
    transport = Transport(LookupHttpResponse(200, json.dumps({"leads": [lead]}).encode()))
    client = CrmLookupClient(enabled=True, token="synthetic-secret", budget=SqliteLookupBudget(tmp_path / "quota.db"),
                            transport=transport, clock=clock)
    result = client.lookup(handle)
    assert result.status == LookupStatus.FOUND and result.history is not None
    assert result.history.leads[0].fields[0].value == 0
    assert result.history.leads[0].fields[1].value is False
    assert result.history.leads[0].fields[2].value == {"amount": 123.5, "currency": "RUB"}
    assert result.history.leads[0].contacts[0].deals[0].bitrix_id == 3
    assert result.history.leads[0].deals[0].bitrix_id == 3
    assert client.lookup(handle) is result
    assert len(transport.calls) == 1
    assert transport.calls[0]["phone"] == "89990000001"
    assert result.public_metadata() == {"history_status": "found"}
    assert "UF_" not in json.dumps(handle.to_anketa())
    assert "synthetic-secret" not in repr(client) + repr(result)


@pytest.mark.parametrize("response,status", [
    (LookupHttpResponse(200, b'{"leads":[]}'), LookupStatus.NOT_FOUND_OR_OUTSIDE_COVERAGE),
    (LookupHttpResponse(401), LookupStatus.UNAUTHORIZED),
    (LookupHttpResponse(403), LookupStatus.UNAUTHORIZED),
    (LookupHttpResponse(429), LookupStatus.RATE_LIMITED),
    (LookupHttpResponse(502), LookupStatus.UNAVAILABLE),
    (LookupHttpResponse(200, b'{"leads": "wrong"}'), LookupStatus.UNAVAILABLE),
    (LookupHttpResponse(200, b'{"leads": [{"bitrix_id":true}]}'), LookupStatus.UNAVAILABLE),
    (LookupHttpResponse(200, b'null'), LookupStatus.UNAVAILABLE),
    (LookupHttpResponse(200, b'{"leads":[{"name":"Lead","bitrix_id":1,"contacts":[],"fields":'
                            b'[{"name":"f","bitrix_id":"x","type":"number","value":{"nested":NaN}}]}]}'),
     LookupStatus.UNAVAILABLE),
    (TimeoutError("secret body"), LookupStatus.UNAVAILABLE),
])
def test_lookup_semantic_states_and_no_automatic_retries(setup_context, tmp_path, response, status):
    clock, _, _, _, _, service, token = setup_context
    transport = Transport(response)
    client = CrmLookupClient(enabled=True, token="synthetic-only", budget=SqliteLookupBudget(tmp_path / "quota.db"),
                            transport=transport, clock=clock)
    result = client.lookup(service.resolve(token, session_id="session-1"))
    assert result.status == status
    assert len(transport.calls) == 1
    assert "secret body" not in repr(result)


def test_lookup_expiry_blocks_cached_history_and_raw_phone_is_not_authorized(setup_context, tmp_path):
    clock, _, _, _, _, service, token = setup_context
    handle = service.resolve(token, session_id="session-1")
    transport = Transport(LookupHttpResponse(200, b'{"leads":[]}'))
    client = CrmLookupClient(enabled=True, token="synthetic-only", budget=SqliteLookupBudget(tmp_path / "quota.db"),
                            transport=transport, clock=clock)
    client.lookup(handle)
    clock.now += 300
    with pytest.raises(ContextAccessError, match="expired"):
        client.lookup(handle)
    with pytest.raises(TypeError):
        client.lookup("89990000001")
    assert len(transport.calls) == 1


def test_snapshot_freshness_cannot_be_extended_by_a_new_capability(setup_context, tmp_path):
    clock, _, snapshot, source, _, service, token = setup_context
    source.snapshot = snapshot.model_copy(update={"observed_at": datetime.fromtimestamp(clock() - 299, timezone.utc)})
    handle = service.resolve(token, session_id="session-1")
    transport = Transport(LookupHttpResponse(200, b'{"leads":[]}'))
    client = CrmLookupClient(enabled=True, token="synthetic", transport=transport, clock=clock,
                            budget=SqliteLookupBudget(tmp_path / "freshness.sqlite"))
    clock.now += 1
    with pytest.raises(ContextAccessError, match="expired"):
        client.lookup(handle)
    assert transport.calls == []


def test_response_size_budget_and_failed_requests_are_not_refunded(setup_context, tmp_path):
    clock, _, _, _, _, service, token = setup_context
    handle = service.resolve(token, session_id="session-1")
    transport = Transport(TimeoutError("never expose"))
    budget = SqliteLookupBudget(tmp_path / "quota.db", limit=1)
    client = CrmLookupClient(enabled=True, token="synthetic", budget=budget, transport=transport, clock=clock)
    assert client.lookup(handle).status == LookupStatus.UNAVAILABLE
    assert client.lookup(handle).status == LookupStatus.RATE_LIMITED
    assert len(transport.calls) == 1
    assert budget.reserve(clock() + 86400) is True
    size_transport = Transport(LookupHttpResponse(200, b' ' * 20))
    size_client = CrmLookupClient(enabled=True, token="synthetic", budget=SqliteLookupBudget(tmp_path / "size.db"), transport=size_transport,
                                 clock=lambda: clock() + 1, max_bytes=10)
    assert size_client.lookup(handle).status == LookupStatus.UNAVAILABLE
    assert len(size_transport.calls) == 1


def test_default_transport_exact_post_bearer_and_bounded_read(monkeypatch):
    from customer_context import HttpLookupTransport
    calls = []
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, size):
            calls.append(size)
            return b'{"leads":[]}'
    def send(request, *, timeout):
        assert request.full_url == "https://crm.mbk.ru/lara/api/crm-lookup"
        assert request.method == "POST"
        assert json.loads(request.data) == {"phone": "89990000001"}
        assert request.get_header("Authorization") == "Bearer synthetic-server-secret"
        assert timeout == 2
        return Response()
    adapter = HttpLookupTransport()
    monkeypatch.setattr(adapter._opener, "open", send)
    result = adapter.post(phone="89990000001", token="synthetic-server-secret", timeout=2, max_bytes=100)
    assert result.status == 200 and calls == [101]


@pytest.mark.parametrize("redirect_code", [301, 302, 303, 307, 308])
def test_actual_http_redirect_cannot_forward_bearer(setup_context, tmp_path, monkeypatch, redirect_code):
    """Actual opener+local HTTP, not a stubbed redirect decision. No production request."""
    from customer_context import HttpLookupTransport
    clock, _, _, _, _, service, token = setup_context
    observed = {"origin_posts": 0, "origin_authorized": False, "target_requests": 0}
    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            observed["target_requests"] += 1
            self.send_response(200)
            self.end_headers()
        do_POST = do_GET
        def log_message(self, *args):
            pass
    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    class Origin(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            observed["origin_posts"] += 1
            observed["origin_authorized"] = self.headers.get("Authorization") == "Bearer synthetic-only"
            self.send_response(redirect_code)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/sink")
            self.end_headers()
        def log_message(self, *args):
            pass
    origin = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
    threads = [threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
               for server in (origin, target)]
    for thread in threads:
        thread.start()
    try:
        adapter = HttpLookupTransport()
        actual_open = adapter._opener.open
        def local_only(request, *, timeout):
            if request.full_url == "https://crm.mbk.ru/lara/api/crm-lookup":
                rewritten = urllib.request.Request(f"http://127.0.0.1:{origin.server_port}/lookup",
                    data=request.data, headers=dict(request.header_items()), method=request.method)
                return actual_open(rewritten, timeout=timeout)
            # A redirect invokes opener.open again: allow the actual local target through.
            # Rejecting every follow-up here would hide a broken/missing NoRedirect handler.
            assert request.full_url in {
                f"http://127.0.0.1:{origin.server_port}/lookup",
                f"http://127.0.0.1:{target.server_port}/sink",
            }
            return actual_open(request, timeout=timeout)
        monkeypatch.setattr(adapter._opener, "open", local_only)
        client = CrmLookupClient(enabled=True, token="synthetic-only", transport=adapter, clock=clock,
                                budget=SqliteLookupBudget(tmp_path / "redirect.sqlite"))
        result = client.lookup(service.resolve(token, session_id="session-1"))
        assert result.status == LookupStatus.UNAVAILABLE
        assert observed == {"origin_posts": 1, "origin_authorized": True, "target_requests": 0}
    finally:
        for server in (origin, target):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)


def test_shared_sqlite_budget_is_atomic_and_contains_no_identifiers(tmp_path):
    path = tmp_path / "shared-quota.sqlite"
    budgets = [SqliteLookupBudget(path, limit=7) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        values = list(pool.map(lambda index: budgets[index % 4].reserve(2000000000.0), range(30)))
    assert sum(values) == 7
    raw = path.read_bytes()
    assert b"phone" not in raw and b"token" not in raw


def test_budget_reservations_coordinate_independent_worker_processes(tmp_path):
    path = tmp_path / "multiprocess-quota.sqlite"
    SqliteLookupBudget(path, limit=7)
    code = ("import sys; from customer_context import SqliteLookupBudget; "
            "b=SqliteLookupBudget(sys.argv[1],limit=7); "
            "print(sum(b.reserve(2000000000.0) for _ in range(4)))")
    def reserve_in_worker(_):
        result = subprocess.run([sys.executable, "-c", code, str(path)], check=True,
                                capture_output=True, text=True, timeout=15)
        return int(result.stdout.strip())
    with ThreadPoolExecutor(max_workers=6) as executor:
        counts = list(executor.map(reserve_in_worker, range(6)))
    assert sum(counts) == 7
    assert SqliteLookupBudget(path, limit=7).reserve(2000000000.0) is False


@pytest.mark.parametrize("phone", ["19990000001", "8999000000", "899900000012", "http://89990000001", ""])
def test_snapshot_phone_validation_does_not_accept_arbitrary_prefix(setup_context, phone):
    _, reference, _, _, _, _, _ = setup_context
    with pytest.raises(ValidationError):
        CurrentApplicationSnapshot(reference=reference, phone=SecretStr(phone),
                                   observed_at=datetime.now(timezone.utc))


def test_disabled_lookup_and_missing_credentials_never_call_transport(setup_context):
    clock, _, _, _, _, service, token = setup_context
    transport = Transport(LookupHttpResponse(200, b'{"leads":[]}'))
    client = CrmLookupClient(transport=transport, clock=clock)
    assert client.lookup(service.resolve(token, session_id="session-1")).status == LookupStatus.DISABLED
    assert transport.calls == []
    with pytest.raises(ValueError):
        CrmLookupClient(enabled=True)


def test_snapshot_contract_excludes_private_data_from_accidental_dump(setup_context):
    _, _, snapshot, _, _, _, _ = setup_context
    exported = snapshot.model_dump_json()
    assert "89990000001" not in exported and "400000" not in exported
    with pytest.raises(ValidationError):
        CurrentApplicationFields(assets_car=False)
    with pytest.raises(ValidationError):
        CurrentApplicationFields(debts_total=True)
