"""Exercise real local HTTP apps using manufactured reader fixtures only."""

from __future__ import annotations

import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    """Do not let an application redirect this client to another origin."""

    def redirect_request(self, req: Request, fp: object, code: int, msg: str,
                         headers: object, newurl: str) -> None:
        return None


def main() -> None:
    opener = build_opener(NoRedirect())
    cases = [
        ("partners_unique_sent_counter", "partners", "/api/lead-sent-count/990001", None, 200, {"count": 2}),
        ("partners_unknown_counter", "partners", "/api/lead-sent-count/0", None, 200, {"count": 0}),
        ("partners_fixture_phone_found", "partners", "/api/phone-check?phone=70000000001", None, 200, {"found": True}),
        ("partners_absent_phone", "partners", "/api/phone-check?phone=70000000002", None, 200, {"found": False}),
        ("crm_lookup_missing_local_token", "crm", "/lara/api/crm-lookup", {}, 401, {}),
        ("crm_first_qualification", "crm", "/lara/api/classification/first-qualification", {}, 200, {"success": True}),
        ("site_bfl_csrf_boundary", "site", "/lead-bfl-create", {}, 419, {}),
        ("site_bankruptcy_content", "site", "/bankruptcy", None, 200, {}),
    ]
    for region, high_category in [("Москва", 990300), ("Тверская область", 990301)]:
        for debt, category in [(189999, None), (190000, 990191), (299999, 990191), (300000, high_category)]:
            cases.append((f"crm_user_fixture_{debt}_{high_category}", "crm",
                          "/lara/api/classification/first-qualification",
                          {"UF_DEBT_SUMM": debt, "UF_CRM_1452153725": region, "UF_CREDIT_HISTORY": 9257},
                          200, {"success": True, "result.matched": category is not None,
                                "result.category": category}))
    results: list[dict[str, object]] = []
    for name, service, path, payload, expected_status, expected_values in cases:
        request = Request(f"http://{service}:8000{path}",
                          data=None if payload is None else json.dumps(payload).encode(),
                          headers={"Accept": "application/json", "Content-Type": "application/json"})
        started = time.monotonic()
        status: int | str
        body = b""
        try:
            with opener.open(request, timeout=15) as response:
                status = response.status
                body = response.read(100_000)
        except HTTPError as error:
            status = error.code
            body = error.read(100_000)
        except (URLError, TimeoutError):
            status = "unavailable"
        selected: dict[str, object] = {}
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                for key in expected_values:
                    value = parsed
                    for segment in key.split("."):
                        value = value.get(segment) if isinstance(value, dict) else None
                    selected[key] = value
        except (ValueError, UnicodeError):
            pass
        results.append({"case": name, "service": service, "http_status": status,
                        "expected_http_status": expected_status,
                        "passed": status == expected_status and selected == expected_values,
                        "selected_response": selected,
                        "seconds": round(time.monotonic() - started, 3)})
    passed = all(row["passed"] for row in results)
    print(json.dumps({"status": "passed" if passed else "failed",
                      "source": "synthetic_fixture", "client_targets_local_only": True,
                      "redirects_followed": False,
                      "partner_deliveries_executed": False,
                      "sent_rows_manufactured": 4, "results": results}))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
