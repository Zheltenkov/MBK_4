"""Strict onloader contracts. Browser input never supplies a contact or recipient."""
from __future__ import annotations

from datetime import datetime
import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from customer_context.contracts import ApplicationReference, Identifier

Decision = Literal["accept", "decline"]
Outcome = Literal["queued", "declined", "rejected", "outcome_unknown"]
Status = Literal["pending", "awaiting_consent", "declined", "signal_pending", "queued",
                 "rejected", "outcome_unknown", "stale", "forbidden", "unavailable"]


class OnloaderError(RuntimeError):
    """Only bounded machine reasons cross the HTTP boundary."""

    def __init__(self, reason: str, status: int = 409) -> None:
        codes = {
            "onloader_forbidden": 403, "onloader_stale": 409, "onloader_binding_conflict": 409,
            "onloader_unavailable": 503, "onloader_context_conflict": 409,
            "onloader_session_conflict": 409, "onloader_offer_conflict": 409,
            "onloader_operation_conflict": 409, "onloader_no_decision": 409,
            "onloader_invalid_receipt": 502, "onloader_not_ready": 409,
            "onloader_decision_conflict": 409,
            "onloader_storage_unavailable": 503, "onloader_storage_conflict": 409,
        }
        self.reason = reason if reason in codes else "onloader_unavailable"
        self.status = codes[self.reason]
        super().__init__(self.reason)


class OnloaderOffer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    offer_id: Identifier
    version: Identifier
    consent_version: Identifier
    partner_name: str = Field(min_length=1, max_length=160)

    @field_validator("partner_name")
    @classmethod
    def display_text(cls, value: str) -> str:
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("invalid offer display text")
        return value

    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()


class OnloaderSnapshot(BaseModel):
    """Authenticated backend result with an opaque contact and stable operation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    reference: ApplicationReference = Field(repr=False)
    operation_ref: Identifier = Field(repr=False)
    delivery_handle: SecretStr = Field(repr=False, exclude=True)
    contact_known: bool
    delivery_owner: Literal["site_backend"] = "site_backend"
    active: bool = True
    offer: OnloaderOffer
    observed_at: datetime
    expires_at: datetime

    @field_validator("observed_at", "expires_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone-aware time required")
        return value

    @field_validator("delivery_handle")
    @classmethod
    def bounded_handle(cls, value: SecretStr) -> SecretStr:
        if not 16 <= len(value.get_secret_value()) <= 512:
            raise ValueError("invalid delivery handle")
        return value


class OnloaderDecision(BaseModel):
    """An explicit action on the exact server presentation, separate from chat text."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    presentation_id: Identifier
    offer_id: Identifier
    offer_version: Identifier
    consent_version: Identifier
    decision: Decision


class OnloaderCommand(OnloaderDecision):
    operation_ref: Identifier = Field(repr=False)
    event_id: Identifier
    authorization_expires_at: float = Field(gt=0, allow_inf_nan=False, repr=False)
    offer_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class OnloaderReceipt(OnloaderCommand):
    status: Outcome
    receipt_id: Identifier


class OnloaderState(BaseModel):
    """Private persisted binding. All public/model projections are explicit allowlists."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    status: Status
    mode: Literal["local"] = "local"
    binding_digest: str = Field(min_length=64, max_length=64, repr=False)
    operation_ref: Identifier = Field(repr=False)
    delivery_handle: SecretStr = Field(repr=False)
    authorization_expires_at: float = Field(gt=0, allow_inf_nan=False)
    offer_expires_at: float = Field(gt=0, allow_inf_nan=False)
    offer: OnloaderOffer
    presentation_id: Identifier
    event_id: Identifier
    decision: Decision | None = None
    receipt_id: Identifier | None = None
    decided_at: str | None = None
    intent_expires_at: float | None = Field(default=None, gt=0, allow_inf_nan=False, repr=False)

    def storage_value(self) -> dict[str, object]:
        result = self.model_dump(mode="json")
        result["delivery_handle"] = self.delivery_handle.get_secret_value()
        return result
