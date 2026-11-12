"""Run the real site BFL session flow; leave its CRM jobs queued and unexecuted."""

from __future__ import annotations

from http.cookiejar import CookieJar
import json
import sys
from urllib.error import HTTPError
from urllib.parse import unquote
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: object, code: int, msg: str,
                         headers: object, newurl: str) -> None:
        return None


def main() -> None:
    results: list[dict[str, object]] = []
    for index, (debt, monthly, category) in enumerate([
        (190000, 0, 4707), (299999, 0, 4707), (300000, 0, 3895), (190000, 110000, 3895)
    ]):
        jar = CookieJar()
        opener = build_opener(NoRedirect(), HTTPCookieProcessor(jar))
        # A real web route establishes the session and encrypted XSRF cookie.
        try:
            opener.open("http://site:8000/bankruptcy", timeout=10).close()
        except HTTPError:
            pass
        token = next((unquote(cookie.value) for cookie in jar
                      if cookie.name == "XSRF-TOKEN" and cookie.value is not None), None)
        if token is None:
            results.append({"case": index, "passed": False, "blocker": "No XSRF cookie from web route"})
            continue
        steps = [
            ("create", "/lead-bfl-create", {"name": "LOCAL SYNTHETIC BFL", "phone": f"7000000010{index}",
                                             "utm_source": "local_synthetic_fixture"}),
            ("step", "/lead-bfl-step-save", {"reg_address": "Москва", "fact_address_like_reg": "true",
                                                "birthday": "1990-01-01", "credit_history": 9257,
                                                "total_credit": debt, "total_credit_pay": monthly}),
            ("complete", "/lead-bfl-complete", {"reg_address": "Москва", "fact_address_like_reg": "true",
                                                   "birthday": "1990-01-01", "credit_history": 9257,
                                                   "total_credit": debt, "total_credit_pay": monthly}),
        ]
        completed: list[str] = []
        for label, path, payload in steps:
            request = Request("http://site:8000" + path, data=json.dumps(payload).encode(), headers={
                "Content-Type": "application/json", "Accept": "application/json", "X-XSRF-TOKEN": token,
            })
            try:
                with opener.open(request, timeout=15) as response:
                    status = response.status
                    body = json.load(response)
            except HTTPError as error:
                status = error.code
                body = {}
            if status != 200 or body.get("success") is not True:
                results.append({"case": index, "passed": False, "failed_step": label,
                                "http_status": status, "server_success": body.get("success")})
                break
            completed.append(label)
            # The middleware may rotate the encrypted cookie on every response.
            token = next((unquote(cookie.value) for cookie in jar
                          if cookie.name == "XSRF-TOKEN" and cookie.value is not None), token)
        else:
            results.append({"case": index, "passed": True, "steps": completed,
                            "debt": debt, "monthly": monthly, "expected_category": category})
    passed = all(result["passed"] for result in results)
    print(json.dumps({"status": "passed" if passed else "failed", "source": "synthetic_fixture",
                      "csrf_bypass": False, "worker_executed": False, "results": results}))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
