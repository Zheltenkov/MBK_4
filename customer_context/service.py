"""Authorize current application, then project only its explicit current-form facts."""
from __future__ import annotations

import time
import math
import hmac
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from .capability import ContextCapabilityCodec
from .contracts import ApplicationReference, ContextAccessError, ContextBinding, CurrentApplicationSnapshot


class ApplicationSource(Protocol):
    def fetch(self, reference: ApplicationReference) -> CurrentApplicationSnapshot | None: ...


class ServerApplicationAdapter:
    """Bridge to an independently authenticated backend resolver; no formID→phone URL is invented.

    Supplying an arbitrary browser dict here is not an authenticated resolver. Deployment
    requires the actual MBK server ownership/subject API and a separately provisioned issuer.
    """

    def __init__(self, resolver: Callable[[ApplicationReference], CurrentApplicationSnapshot | None]) -> None:
        self._resolver = resolver

    def fetch(self, reference: ApplicationReference) -> CurrentApplicationSnapshot | None:
        return self._resolver(reference)


_AUTHORIZATION_SEAL = object()


@dataclass(frozen=True, repr=False)
class AuthorizedApplication:
    """In-process result of authorization, deliberately not a request/Pydantic model."""

    binding: ContextBinding
    _snapshot: CurrentApplicationSnapshot = field(repr=False)
    _expires_at: float = field(repr=False)
    _seal: object = field(repr=False)
    binding_digest: str

    def assert_authorized(self, now: float) -> None:
        if self._seal is not _AUTHORIZATION_SEAL or not math.isfinite(now) or now >= self._expires_at:
            raise ContextAccessError("expired")

    def to_anketa(self) -> dict[str, object]:
        """Only current authoritative fields, never historical CRM data or implicit consent."""
        values: dict[str, object] = self._snapshot.fields.model_dump(exclude_none=True)
        values["phone"] = self._snapshot.phone.get_secret_value()
        return values

    def public_metadata(self) -> dict[str, str]:
        return {"context_status": "ready", "source": "current_application"}

    def safe_context_state(self) -> dict[str, str]:
        """Private persisted state contains an opaque binding, no reference/phone/history."""
        return {**self.public_metadata(), "binding_digest": self.binding_digest}

    def _phone_for_lookup(self, now: float) -> str:
        self.assert_authorized(now)
        return self._snapshot.phone.get_secret_value()


class CurrentContextService:
    def __init__(self, codec: ContextCapabilityCodec, source: ApplicationSource, *, enabled: bool = False,
                 clock: Callable[[], float] = time.time, max_snapshot_age_seconds: float = 300) -> None:
        if not 0 < max_snapshot_age_seconds <= 300:
            raise ValueError("snapshot freshness must be bounded1..300 seconds")
        self._codec, self._source, self._enabled, self._clock = codec, source, enabled, clock
        self._max_snapshot_age = max_snapshot_age_seconds

    def resolve(self, capability: str, *, session_id: str,
                existing_binding: ContextBinding | str | None = None) -> AuthorizedApplication:
        if not self._enabled:
            raise ContextAccessError("disabled")
        claims = self._codec.verify(capability, session_id=session_id)
        binding = ContextBinding(**claims.reference.model_dump())
        digest = self._codec.binding_digest(claims.reference, session_id=session_id)
        # Reject context swaps before a source/lookup call can leak or consume quota.
        if existing_binding is not None and not (
            hmac.compare_digest(existing_binding, digest) if isinstance(existing_binding, str)
            else existing_binding == binding
        ):
            raise ContextAccessError("binding_conflict")
        try:
            snapshot = self._source.fetch(claims.reference)
        except Exception:
            raise ContextAccessError("source_unavailable") from None
        if snapshot is None:
            raise ContextAccessError("source_missing")
        if not isinstance(snapshot, CurrentApplicationSnapshot) or not snapshot.active \
                or snapshot.reference != claims.reference:
            raise ContextAccessError("snapshot_mismatch")
        try:
            age = self._clock() - snapshot.observed_at.timestamp()
        except (OSError, OverflowError, ValueError):
            raise ContextAccessError("snapshot_stale") from None
        if not math.isfinite(age) or age < 0 or age >= self._max_snapshot_age:
            raise ContextAccessError("snapshot_stale")
        # The source can be older than the newly issued capability. Never extend its freshness.
        expires_at = min(claims.expires_at, snapshot.observed_at.timestamp() + self._max_snapshot_age)
        result = AuthorizedApplication(binding, snapshot, expires_at, _AUTHORIZATION_SEAL, digest)
        # A slow source cannot redeem an authorization that expired while fetching.
        result.assert_authorized(self._clock())
        return result

    def binding_digest(self, capability: str, *, session_id: str) -> str:
        """Verify a repeated capability without a backend/lookup call; not a rights bypass."""
        if not self._enabled:
            raise ContextAccessError("disabled")
        claims = self._codec.verify(capability, session_id=session_id)
        return self._codec.binding_digest(claims.reference, session_id=session_id)
