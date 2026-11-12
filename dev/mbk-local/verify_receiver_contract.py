"""Check the isolated receiver simulator over HTTP; never a real Bitrix endpoint."""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import os
import re
import secrets
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


EVIDENCE = "protocol_simulation"


class ContractFailure(Exception):
    """A simulator reply violates the test contract; messages contain no payload."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: object, code: int, msg: str,
                         headers: object, newurl: str) -> None:
        return None


@dataclass(frozen=True)
class Reply:
    status: int
    body: object


def success(reply: Reply) -> tuple[str, bool]:
    """Require both transport success and strict JSON types, independently of CRM."""
    body = reply.body
    if reply.status != 200 or not isinstance(body, dict) or body.get("evidence_level") != EVIDENCE:
        raise ContractFailure("Success requires HTTP200 and simulation evidence")
    result = body.get("result")
    if not isinstance(result, dict) or set(result) != {"leadID", "created"}:
        raise ContractFailure("Malformed success envelope")
    lead_id, created = result["leadID"], result["created"]
    if not isinstance(lead_id, str) or re.fullmatch(r"[0-9]+", lead_id) is None:
        raise ContractFailure("A decimal-string simulated ID is required")
    if int(lead_id) < 700000000 or type(created) is not bool:
        raise ContractFailure("Invalid simulated ID namespace or created type")
    return lead_id, created


def error(reply: Reply, expected_status: int, expected_code: str) -> None:
    body = reply.body
    if reply.status != expected_status or not isinstance(body, dict):
        raise ContractFailure("Wrong error status or envelope")
    if set(body) != {"error", "evidence_level"} or body.get("evidence_level") != EVIDENCE:
        raise ContractFailure("Error must not include a result or raw diagnostics")
    if body.get("error") != expected_code:
        raise ContractFailure("Unexpected error code")


class Client:
    def __init__(self) -> None:
        self.base = os.environ.get("MBK_PROTOCOL_BASE_URL", "http://protocol-receiver:8000")
        parsed = urlsplit(self.base)
        if (parsed.scheme != "http" or parsed.hostname not in {"protocol-receiver", "receiver-simulator"}
                or parsed.port != 8000 or parsed.username or parsed.password
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ContractFailure("Only the configured internal simulator host is allowed")
        self.base = self.base.rstrip("/")
        self.path = os.environ["MBK_PROTOCOL_PATH"]
        if (not self.path.startswith("/") or self.path.startswith("//")
                or any(character in self.path for character in "\r\n?#")):
            raise ContractFailure("Invalid configured receiver path")
        user, password = os.environ["MBK_PROTOCOL_USER"], os.environ["MBK_PROTOCOL_PASSWORD"]
        if not user or not password:
            raise ContractFailure("Synthetic local credentials are required")
        self.authorization = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode("ascii")

    def request(self, body: bytes | None, *, authorized: bool = True, path: str | None = None) -> Reply:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if authorized:
            headers["Authorization"] = self.authorization
        request = Request(self.base + (path or self.path), data=body, headers=headers)
        # Disable proxy discovery and redirects; each thread gets its own opener.
        opener = build_opener(ProxyHandler({}), NoRedirect())
        try:
            with opener.open(request, timeout=10) as response:
                status, raw = response.status, response.read(100001)
        except HTTPError as response:
            status, raw = response.code, response.read(100001)
        except (URLError, TimeoutError, OSError) as exc:
            raise ContractFailure("Local simulator request failed") from exc
        if len(raw) > 100000:
            raise ContractFailure("Simulator response exceeds the contract limit")
        try:
            return Reply(status, json.loads(raw))
        except (ValueError, UnicodeError) as exc:
            raise ContractFailure("Simulator did not return JSON") from exc

    def post(self, payload: dict[str, object]) -> Reply:
        return self.request(json.dumps(payload).encode("utf-8"))


def main() -> None:
    results: list[dict[str, object]] = []
    current_case = "configuration"
    try:
        client = Client()
        nonce = secrets.token_hex(8)
        phone_suffix = int(nonce, 16) % 10000
        phone_a = f"7000000{phone_suffix:04d}"
        phone_b = f"7000000{(phone_suffix + 1) % 10000:04d}"
        operation = "local-write-httpProbe" + nonce
        payload: dict[str, object] = {
            "PHONE": phone_a, "PHONE_DEAL": phone_a, "UTM_CAMPAIGN": operation,
            "RETURN_RESPONSE": True, "SUMM": "0", "IS_BFL_PRODUCT": True,
            "UF_CRM_1491296976": 4707, "UF_DEBT_SUMM": 189999,
            "UF_SITE_AGREE_PARTNERS": 23000,
        }
        current_case = "unauthorized"
        error(client.request(json.dumps(payload).encode(), authorized=False), 401, "unauthorized")
        results.append({"case": current_case, "passed": True})
        for name, raw, status, code in [
            ("invalid_json", b"{not_json", 400, "invalid_json"),
            ("invalid_envelope", b"[]", 400, "invalid_envelope"),
            ("unsupported_fixture", b"{}", 422, "unsupported_fixture_input"),
        ]:
            current_case = name
            error(client.request(raw), status, code)
            results.append({"case": name, "passed": True})
        current_case = "durable_receipt"
        first_id, created = success(client.post(payload))
        if not created:
            raise ContractFailure("Fresh probe phone already exists; use a new probe fixture")
        results.append({"case": current_case, "passed": True, "simulated_id": first_id})
        current_case = "reordered_replay"
        repeat_id, repeat_created = success(client.post(dict(reversed(list(payload.items())))))
        if repeat_id != first_id or repeat_created:
            raise ContractFailure("Replay did not preserve its committed identity")
        results.append({"case": current_case, "passed": True})
        current_case = "payload_conflict"
        error(client.post({**payload, "UF_DEBT_SUMM": 190001}), 409, "idempotency_conflict")
        results.append({"case": current_case, "passed": True})
        current_case = "phone_alias"
        alias_id, alias_created = success(client.post({**payload, "UTM_CAMPAIGN": operation + "Alias"}))
        if alias_id != first_id or alias_created:
            raise ContractFailure("Phone alias did not reference the original committed identity")
        results.append({"case": current_case, "passed": True})
        current_case = "concurrent_receipt"
        concurrent = {**payload, "PHONE": phone_b, "PHONE_DEAL": phone_b,
                      "UTM_CAMPAIGN": operation + "Concurrent"}
        with ThreadPoolExecutor(max_workers=6) as executor:
            receipts = list(executor.map(lambda _: success(client.post(concurrent)), range(6)))
        if len({item[0] for item in receipts}) != 1 or sum(item[1] for item in receipts) != 1:
            raise ContractFailure("Concurrent requests did not commit one identity")
        results.append({"case": current_case, "passed": True, "requests": len(receipts)})
        current_case = "safe_ledger_stats"
        stats = client.request(None, path="/lab/ledger-stats")
        if stats.status != 200 or not isinstance(stats.body, dict) or stats.body.get("evidence_level") != EVIDENCE:
            raise ContractFailure("Ledger stats did not include the simulation evidence")
        serialized = json.dumps(stats.body)
        if phone_a in serialized or phone_b in serialized:
            raise ContractFailure("Ledger stats expose a raw fixture phone")
        results.append({"case": current_case, "passed": True})
    except (ContractFailure, KeyError, ValueError) as exc:
        # Report only controlled messages, never URL/auth/body/traceback.
        message = str(exc) if isinstance(exc, ContractFailure) else "Invalid local probe configuration"
        results.append({"case": current_case, "passed": False, "reason": message})
        print(json.dumps({"status": "failed", "evidence_level": EVIDENCE, "results": results}))
        sys.exit(1)
    print(json.dumps({"status": "passed", "evidence_level": EVIDENCE,
                      "genuine_bitrix_executed": False, "results": results}))


if __name__ == "__main__":
    main()
