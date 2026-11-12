"""Short-lived session-bound capability. Only the trusted backend owns this signer."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import time
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field, SecretBytes, ValidationError

from .contracts import ApplicationReference, ContextAccessError, Identifier


class CapabilityClaims(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    version: int = Field(ge=1, le=1)
    audience: str
    session_id: Identifier = Field(repr=False)
    reference: ApplicationReference = Field(repr=False)
    issued_at: int
    expires_at: int


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    # Canonical encoding rejects alternate signed byte strings and padding tricks.
    decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if _encode(decoded) != value:
        raise ValueError("noncanonical")
    return decoded


class ContextCapabilityCodec:
    """Not exposed as an HTTP issuance endpoint; key is distinct from session/CRM keys."""

    def __init__(self, key: bytes, *, clock: Callable[[], float] = time.time,
                 audience: str = "mbk-current-application") -> None:
        if len(key) < 32:
            raise ValueError("context capability requires at least 32 random key bytes")
        self._key = SecretBytes(key)
        self._clock = clock
        if audience not in {"mbk-current-application", "mbk-onloader"}:
            raise ValueError("unsupported capability audience")
        self._audience = audience

    @property
    def audience(self) -> str:
        return self._audience

    def issue(self, reference: ApplicationReference, *, session_id: str, ttl_seconds: int = 300) -> str:
        """Trusted server must establish application ownership BEFORE calling issue."""
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 300:
            raise ValueError("invalid capability TTL")
        now = int(self._clock())
        claims = CapabilityClaims(version=1, audience=self._audience, session_id=session_id,
                                  reference=reference, issued_at=now, expires_at=now + ttl_seconds)
        body = _encode(claims.model_dump_json().encode())
        signature = hmac.digest(self._key.get_secret_value(), body.encode("ascii"), "sha256")
        return body + "." + _encode(signature)

    def verify(self, token: str, *, session_id: str) -> CapabilityClaims:
        """Authenticate before decoding claims; all parsing errors have a safe message."""
        try:
            if not isinstance(token, str) or len(token) > 4096:
                raise ValueError("size")
            body, signature = token.split(".")
            expected = hmac.digest(self._key.get_secret_value(), body.encode("ascii"), "sha256")
            if not hmac.compare_digest(_decode(signature), expected):
                raise ValueError("signature")
            claims = CapabilityClaims.model_validate_json(_decode(body))
            now = self._clock()
            if not math.isfinite(now) or claims.audience != self._audience \
                    or not hmac.compare_digest(claims.session_id.encode(), session_id.encode()) \
                    or claims.issued_at > now or not 0 < claims.expires_at - claims.issued_at <= 300:
                raise ValueError("claims")
            if now >= claims.expires_at:
                raise ContextAccessError("expired")
            return claims
        except ContextAccessError:
            raise
        except (ValueError, TypeError, UnicodeError, ValidationError):
            raise ContextAccessError("invalid_capability") from None

    def fingerprint(self, value: str) -> str:
        """Private cache key; a plain hash of a phone is vulnerable to enumeration."""
        return hmac.new(self._key.get_secret_value(), value.encode(), hashlib.sha256).hexdigest()

    def binding_digest(self, reference: ApplicationReference, *, session_id: str) -> str:
        """Persist only this session-bound digest; raw subject/application IDs stay private."""
        value = json.dumps({"session_id": session_id, "reference": reference.model_dump()},
                           sort_keys=True, separators=(",", ":"))
        return self.fingerprint("context-binding-v1:" + value)
