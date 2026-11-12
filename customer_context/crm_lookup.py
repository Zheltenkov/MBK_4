"""Bounded backend-only CRM history reader, separate from current application facts."""
from __future__ import annotations

import json
import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import SecretStr, ValidationError

from .budget import SqliteLookupBudget
from .contracts import CrmLookupTree, LookupStatus
from .service import AuthorizedApplication


@dataclass(frozen=True)
class LookupHttpResponse:
    status: int
    body: bytes = field(default=b"", repr=False)


class LookupTransport(Protocol):
    def post(self, *, phone: str, token: str, timeout: float, max_bytes: int) -> LookupHttpResponse: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # urllib's default handler can forward Authorization to the redirect target.
        return None


class HttpLookupTransport:
    """One direct POST to the confirmed endpoint; no redirect/proxy/retry policy here."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def post(self, *, phone: str, token: str, timeout: float, max_bytes: int) -> LookupHttpResponse:
        request = urllib.request.Request("https://crm.mbk.ru/lara/api/crm-lookup",
            data=json.dumps({"phone": phone}).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
        try:
            with self._opener.open(request, timeout=timeout) as response:
                return LookupHttpResponse(response.status, response.read(max_bytes + 1))
        except urllib.error.HTTPError as error:
            # Error bodies may echo PII/secrets. Do not read or propagate them.
            error.close()
            return LookupHttpResponse(error.code)


@dataclass(frozen=True)
class LookupResult:
    status: LookupStatus
    history: CrmLookupTree | None = field(default=None, repr=False)
    reason: str | None = None

    def public_metadata(self) -> dict[str, str]:
        """No record IDs/fields/counts or history data enters browser/LLM projection."""
        return {"history_status": self.status.value}


class CrmLookupClient:
    """No automatic retries, including429/5xx/timeout. A budget reservation is final."""

    def __init__(self, *, enabled: bool = False, token: str = "",
                 budget: SqliteLookupBudget | None = None, transport: LookupTransport | None = None,
                 timeout: float = 3.0, max_bytes: int = 262144, cache_ttl: float = 60,
                 clock: Callable[[], float] = time.time) -> None:
        if not 0 < timeout <= 10 or not 1 <= max_bytes <= 1048576 or not 0 <= cache_ttl <= 300:
            raise ValueError("invalid bounded lookup transport configuration")
        if enabled and (not token or budget is None):
            raise ValueError("enabled lookup needs server token and shared persistent budget")
        self._enabled, self._token, self._budget = enabled, SecretStr(token), budget
        self._transport, self._timeout, self._max_bytes = transport or HttpLookupTransport(), timeout, max_bytes
        self._cache_ttl, self._clock = cache_ttl, clock
        self._cache: dict[tuple[str, str, str, str], tuple[float, LookupResult]] = {}
        self._cache_key = secrets.token_bytes(32)
        # Coalesces identical concurrent requests within this worker; SQLite bounds all workers.
        self._lock = threading.Lock()

    def lookup(self, authorized: AuthorizedApplication) -> LookupResult:
        # Validate before touching cache or quota; a raw number/reference is not a handle.
        if not isinstance(authorized, AuthorizedApplication):
            raise TypeError("authorized application required")
        phone = authorized._phone_for_lookup(self._clock())
        if not self._enabled:
            return LookupResult(LookupStatus.DISABLED)
        binding = authorized.binding
        key = (binding.subject_id, binding.application_id, binding.revision,
               hmac.new(self._cache_key, phone.encode(), hashlib.sha256).hexdigest())
        with self._lock:
            now = self._clock()
            authorized.assert_authorized(now)
            # Purge expired private history rather than retaining it for the process lifetime.
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
            if key in self._cache:
                return self._cache[key][1]
            try:
                assert self._budget is not None
                if not self._budget.reserve(now):
                    return LookupResult(LookupStatus.RATE_LIMITED, reason="local_budget_exhausted")
            except (OSError, RuntimeError, sqlite3.Error):
                return LookupResult(LookupStatus.UNAVAILABLE, reason="budget_unavailable")
            try:
                response = self._transport.post(phone=phone, token=self._token.get_secret_value(),
                                                timeout=self._timeout, max_bytes=self._max_bytes)
            except Exception:
                return LookupResult(LookupStatus.UNAVAILABLE, reason="transport_unavailable")
            if response.status == 401 or response.status == 403:
                return LookupResult(LookupStatus.UNAUTHORIZED, reason="upstream_authorization")
            if response.status == 429:
                return LookupResult(LookupStatus.RATE_LIMITED, reason="upstream_quota")
            if response.status != 200 or len(response.body) > self._max_bytes:
                return LookupResult(LookupStatus.UNAVAILABLE, reason="upstream_response")
            try:
                tree = CrmLookupTree.model_validate_json(response.body)
            except (ValueError, ValidationError):
                return LookupResult(LookupStatus.UNAVAILABLE, reason="invalid_response")
            result = LookupResult(LookupStatus.FOUND if tree.leads else LookupStatus.NOT_FOUND_OR_OUTSIDE_COVERAGE,
                                  tree)
            # History is never copied into facts. Only successful history/coverage states cache.
            expiry = min(now + self._cache_ttl, authorized._expires_at)
            self._cache[key] = (expiry, result)
            return result
