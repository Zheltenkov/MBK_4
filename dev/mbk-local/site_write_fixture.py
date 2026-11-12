"""Create one clearly marked synthetic site BFL lead with real CSRF/session."""

from __future__ import annotations

from http.cookiejar import CookieJar
import hashlib
import json
import os
import sys
from urllib.error import HTTPError
from urllib.parse import unquote
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: object, code: int, msg: str,
                         headers: object, newurl: str) -> None:
        return None


def main() -> None:
    run_id = os.environ["MBK_LOCAL_WRITE_RUN_ID"]
    if not run_id.startswith("local-write-") or not run_id[12:].isalnum():
        raise ValueError("A local-write- prefixed alphanumeric run ID is required")
    # Allow only the two reviewed Docker targets; this runner cannot address production.
    base_url = os.environ.get("MBK_LOCAL_SITE_URL", "http://site:8000")
    if base_url not in {"http://site:8000", "http://site-genuine:8000"}:
        raise ValueError("Unsupported local website target")
    debt = int(os.environ.get("MBK_LOCAL_SITE_DEBT", "189999"))
    if debt not in {189999, 400000}:
        raise ValueError("Only the two reviewed artificial debt fixtures are supported")
    # Distinct runs use non-dialable 000 area codes; a repeated run keeps its phone.
    phone = f"7000{int(hashlib.sha256(run_id.encode()).hexdigest()[:8], 16) % 10_000_000:07d}"
    jar = CookieJar()
    opener = build_opener(NoRedirect(), HTTPCookieProcessor(jar))
    try:
        opener.open(base_url + "/bankruptcy", timeout=10).close()
    except HTTPError:
        pass
    token = next((unquote(cookie.value) for cookie in jar
                  if cookie.name == "XSRF-TOKEN" and cookie.value is not None), None)
    if token is None:
        raise RuntimeError("Site did not issue an XSRF cookie")
    data = {"reg_address": "Москва", "fact_address_like_reg": "true", "birthday": "1990-01-01",
            "credit_history": 9257, "duration_delay": "22602", "total_credit": debt,
            "total_credit_pay": 0}
    steps = [
        ("create", "/lead-bfl-create", {"name": "LOCAL SYNTHETIC WRITE", "phone": phone,
                                         "utm_source": "local_write_stage", "utm_campaign": run_id}),
        ("step", "/lead-bfl-step-save", data),
        ("complete", "/lead-bfl-complete", data),
    ]
    results = []
    for label, path, payload in steps:
        request = Request(base_url + path, data=json.dumps(payload).encode(), headers={
            "Content-Type": "application/json", "Accept": "application/json", "X-XSRF-TOKEN": token,
        })
        try:
            with opener.open(request, timeout=15) as response:
                status = response.status
                body = json.load(response)
        except HTTPError as error:
            status = error.code
            body = {}
        passed = status == 200 and body.get("success") is True
        results.append({"step": label, "status": status, "passed": passed})
        if not passed:
            print(json.dumps({"status": "failed", "run_id": run_id, "results": results}))
            sys.exit(1)
        token = next((unquote(cookie.value) for cookie in jar
                      if cookie.name == "XSRF-TOKEN" and cookie.value is not None), token)
    print(json.dumps({"status": "passed", "run_id": run_id, "source": "synthetic_fixture",
                      "debt": debt, "site_target": base_url,
                      "client_targets_local_only": True, "csrf_bypass": False,
                      "worker_executed": False, "results": results}))


if __name__ == "__main__":
    main()
