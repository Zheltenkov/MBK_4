"""Contract-store tests; these exercise a simulator, never the real Bitrix core."""

from collections.abc import Iterator
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import base64
from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
from threading import Thread
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest


@pytest.fixture(scope="module")
def receiver() -> Iterator[ModuleType]:
    path = Path(__file__).resolve().parents[1] / "dev/mbk-local/protocol_receiver.py"
    spec = importlib.util.spec_from_file_location("mbk_protocol_receiver_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    del sys.modules[spec.name]


@pytest.fixture(scope="module")
def contract() -> Iterator[ModuleType]:
    path = Path(__file__).resolve().parents[1] / "dev/mbk-local/verify_receiver_contract.py"
    spec = importlib.util.spec_from_file_location("mbk_receiver_contract_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    del sys.modules[spec.name]


def test_client_rejects_http_error_with_success_shaped_body(contract: ModuleType) -> None:
    reply = contract.Reply(500, {"result": {"leadID": "700000001", "created": True},
                                 "evidence_level": "protocol_simulation"})
    with pytest.raises(contract.ContractFailure):
        contract.success(reply)


def test_client_rejects_string_created_false(contract: ModuleType) -> None:
    reply = contract.Reply(200, {"result": {"leadID": "700000001", "created": "false"},
                                 "evidence_level": "protocol_simulation"})
    with pytest.raises(contract.ContractFailure):
        contract.success(reply)


def test_client_rejects_id_bearing_error(contract: ModuleType) -> None:
    reply = contract.Reply(500, {"error": "storage_error", "result": {"leadID": "700000001"},
                                 "evidence_level": "protocol_simulation"})
    with pytest.raises(contract.ContractFailure):
        contract.error(reply, 500, "storage_error")


@pytest.mark.parametrize("response_flag", [True, "1"])
@pytest.mark.parametrize("category", [4707, "4707"])
def test_validator_accepts_actual_laravel_wire_types_without_rewriting(
    receiver: ModuleType, response_flag: object, category: object,
) -> None:
    wire = {**payload("local-write-wireCase"), "RETURN_RESPONSE": response_flag,
            "UF_CRM_1491296976": category}
    checked, phone, operation = receiver.validate_payload(wire)
    assert checked == wire and checked is wire
    assert type(checked["RETURN_RESPONSE"]) is type(response_flag)
    assert type(checked["UF_CRM_1491296976"]) is type(category)
    assert phone == "70000000701" and operation == "local-write-wireCase"


@pytest.mark.parametrize("response_flag", [False, 0, 1, "true", "false", "0", None])
def test_validator_does_not_coerce_arbitrary_boolean_forms(receiver: ModuleType, response_flag: object) -> None:
    with pytest.raises(ValueError):
        receiver.validate_payload({**payload("local-write-wireCase"), "RETURN_RESPONSE": response_flag})


@pytest.mark.parametrize("category", [True, False, "04707", "+4707", "4707.0", "4707 ", "", 4707.0, None])
def test_validator_requires_canonical_category_wire_form(receiver: ModuleType, category: object) -> None:
    with pytest.raises(ValueError):
        receiver.validate_payload({**payload("local-write-wireCase"), "UF_CRM_1491296976": category})


def payload(operation: str, phone: str = "70000000701") -> dict[str, object]:
    return {
        "PHONE": phone, "PHONE_DEAL": phone,
        "UTM_CAMPAIGN": operation, "RETURN_RESPONSE": True,
        "SUMM": "0", "IS_BFL_PRODUCT": True,
        "UF_CRM_1491296976": 4707, "UF_DEBT_SUMM": 189999,
    }


@contextmanager
def local_http(receiver: ModuleType, tmp_path: Path, *, fail_commit: bool = False) -> Iterator[str]:
    def before_commit() -> None:
        if fail_commit:
            raise OSError("synthetic storage failure")

    ledger = receiver.Ledger(tmp_path / "http-ledger.sqlite3", "httpRun", before_commit=before_commit)
    handler = receiver.make_handler(ledger, "/local-fixture", "fixture-user", "fixture-password")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/local-fixture"
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()
        assert not thread.is_alive()


def local_post(url: str, body: bytes, *, authorized: bool = True) -> tuple[int, object]:
    headers = {"Content-Type": "application/json"}
    if authorized:
        headers["Authorization"] = "Basic " + base64.b64encode(b"fixture-user:fixture-password").decode()
    request = Request(url, data=body, headers=headers)
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(request, timeout=3) as response:
            return response.status, json.load(response)
    except HTTPError as response:
        return response.code, json.load(response)


@pytest.mark.parametrize("body, status, code", [
    (b"{not_json", 400, "invalid_json"),
    (b"[]", 400, "invalid_envelope"),
    (b"null", 400, "invalid_envelope"),
    (b"{}", 422, "unsupported_fixture_input"),
    (b'{"SUMM":NaN}', 400, "invalid_json"),
])
def test_http_error_contract(receiver: ModuleType, contract: ModuleType, tmp_path: Path,
                             body: bytes, status: int, code: str) -> None:
    with local_http(receiver, tmp_path) as url:
        actual_status, actual_body = local_post(url, body)
    contract.error(contract.Reply(actual_status, actual_body), status, code)


def test_http_auth_is_checked_before_payload(receiver: ModuleType, contract: ModuleType, tmp_path: Path) -> None:
    with local_http(receiver, tmp_path) as url:
        status, body = local_post(url, b"{not_json", authorized=False)
    contract.error(contract.Reply(status, body), 401, "unauthorized")


def test_http_commit_failure_has_no_id(receiver: ModuleType, contract: ModuleType, tmp_path: Path) -> None:
    with local_http(receiver, tmp_path, fail_commit=True) as url:
        status, body = local_post(url, json.dumps(payload("local-write-commitFailure")).encode())
    contract.error(contract.Reply(status, body), 500, "storage_error")


def test_persistent_receipt_and_replay(receiver: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite3"
    first = receiver.Ledger(path, "runA").accept("opA", payload("opA"), "70000000701")
    assert first.created is True and first.lead_id.isascii() and first.lead_id.isdigit()
    assert int(first.lead_id) >= 700000000
    # Reopening the store exercises restart/retry, without a running HTTP server.
    replay = receiver.Ledger(path, "runA").accept("opA", payload("opA"), "70000000701")
    assert replay.lead_id == first.lead_id and replay.created is False


def test_payload_conflict_never_returns_an_id(receiver: ModuleType, tmp_path: Path) -> None:
    ledger = receiver.Ledger(tmp_path / "ledger.sqlite3", "runA")
    first = ledger.accept("opA", payload("opA"), "70000000701")
    with pytest.raises(receiver.IdempotencyConflict):
        ledger.accept("opA", {**payload("opA"), "UF_DEBT_SUMM": 190001}, "70000000701")
    assert ledger.accept("opA", payload("opA"), "70000000701").lead_id == first.lead_id


def test_concurrent_requests_commit_once(receiver: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite3"
    receiver.Ledger(path, "runA")

    def submit(_: int) -> object:
        return receiver.Ledger(path, "runA").accept("opA", payload("opA"), "70000000701")

    with ThreadPoolExecutor(max_workers=8) as executor:
        receipts = list(executor.map(submit, range(8)))
    assert len({receipt.lead_id for receipt in receipts}) == 1
    assert sum(receipt.created is True for receipt in receipts) == 1


def test_phone_alias_stays_bound_after_ttl(receiver: ModuleType, tmp_path: Path) -> None:
    tick = [1000.0]
    ledger = receiver.Ledger(tmp_path / "ledger.sqlite3", "runA", clock=lambda: tick[0])
    first = ledger.accept("opA", payload("opA"), "70000000701")
    tick[0] = 1899.0
    alias = ledger.accept("opB", payload("opB"), "70000000701")
    assert alias.lead_id == first.lead_id and alias.created is False
    # The alias must not slide the original 900-second phone window.
    tick[0] = 1900.0
    fresh = ledger.accept("opC", payload("opC"), "70000000701")
    assert fresh.created is True and fresh.lead_id != first.lead_id
    replay = ledger.accept("opB", payload("opB"), "70000000701")
    assert replay.created is False and replay.lead_id == first.lead_id


def test_conflict_check_applies_to_phone_dedup_alias(receiver: ModuleType, tmp_path: Path) -> None:
    ledger = receiver.Ledger(tmp_path / "ledger.sqlite3", "runA")
    ledger.accept("opA", payload("opA"), "70000000701")
    alias = ledger.accept("opB", payload("opB"), "70000000701")
    with pytest.raises(receiver.IdempotencyConflict):
        ledger.accept("opB", {**payload("opB"), "SUMM": "1"}, "70000000701")
    assert ledger.accept("opB", payload("opB"), "70000000701").lead_id == alias.lead_id


def test_run_namespace_keeps_independent_fixtures_separate(receiver: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite3"
    first = receiver.Ledger(path, "runA").accept("opA", payload("opA"), "70000000701")
    other = receiver.Ledger(path, "runB").accept("opA", payload("opA"), "70000000701")
    assert other.created is True and other.lead_id != first.lead_id


def test_failed_commit_does_not_leave_a_receipt(receiver: ModuleType, tmp_path: Path) -> None:
    def fail_commit() -> None:
        raise OSError("synthetic commit failure")

    path = tmp_path / "ledger.sqlite3"
    failing = receiver.Ledger(path, "runA", before_commit=fail_commit)
    with pytest.raises(receiver.StorageError):
        failing.accept("opA", payload("opA"), "70000000701")
    clean = receiver.Ledger(path, "runA")
    result = clean.accept("opA", payload("opA"), "70000000701")
    assert result.created is True
    assert clean.accept("opA", payload("opA"), "70000000701").created is False


def test_lost_response_replay_does_not_claim_downstream_effects(receiver: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite3"
    committed = receiver.Ledger(path, "runA").accept("opA", payload("opA"), "70000000701")
    # Dropping a committed response leaves the caller's downstream processing unknown.
    retry = receiver.Ledger(path, "runA").accept("opA", payload("opA"), "70000000701")
    assert retry.created is False and retry.lead_id == committed.lead_id


def test_stats_never_expose_phone_or_payload(receiver: ModuleType, tmp_path: Path) -> None:
    ledger = receiver.Ledger(tmp_path / "ledger.sqlite3", "runA")
    ledger.accept("opA", {**payload("opA"), "FULL_NAME": "PRIVATE_PAYLOAD_SENTINEL"}, "70000000701")
    stats = ledger.stats()
    encoded = json.dumps(stats)
    assert "70000000701" not in encoded and "PRIVATE_PAYLOAD_SENTINEL" not in encoded
    assert stats.get("evidence_level") == "protocol_simulation"
