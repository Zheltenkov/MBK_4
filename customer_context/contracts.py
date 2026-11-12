"""Private current-application and CRM-history contracts; never browser input facts."""
from __future__ import annotations

import re
import math
from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, field_validator

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_:.-]+$")]


class ContextAccessError(RuntimeError):
    """Safe machine-readable reason; never include a token, phone or backend body."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ApplicationReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    application_id: Identifier = Field(repr=False)
    subject_id: Identifier = Field(repr=False)
    revision: Identifier


class ContextBinding(ApplicationReference):
    """Immutable private session attachment identity, not a browser credential."""


class CurrentApplicationFields(BaseModel):
    """Explicit current-form allowlist. Absence stays unknown, especially assets/consent."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    full_name: str | None = Field(default=None, max_length=256, repr=False)
    birth_date: str | None = Field(default=None, max_length=32, repr=False)
    registration_address: str | None = Field(default=None, max_length=1024, repr=False)
    living_address: str | None = Field(default=None, max_length=1024, repr=False)
    region: str | None = Field(default=None, max_length=256, repr=False)
    desired_amount: int | None = Field(default=None, ge=0)
    debts_total: int | None = Field(default=None, ge=0)
    employment_type: str | None = Field(default=None, max_length=128)
    overdue_duration: str | None = Field(default=None, max_length=128)
    goal: str | None = Field(default=None, max_length=128)


def normalize_phone(value: str) -> str:
    """CRM lookup expects Russian 8XXXXXXXXXX; do not accept arbitrary 11-digit prefixes."""
    if not re.fullmatch(r"[+0-9 ()-]+", value):
        raise ValueError("invalid_phone")
    digits = re.sub(r"\D", "", value)
    if len(digits) != 11 or digits[0] not in "78":
        raise ValueError("invalid_phone")
    return "8" + digits[1:]


class CurrentApplicationSnapshot(BaseModel):
    """Produced by an authenticated server adapter, never parsed from browser anketa."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    reference: ApplicationReference = Field(repr=False)
    phone: SecretStr = Field(repr=False, exclude=True)
    fields: CurrentApplicationFields = Field(default_factory=CurrentApplicationFields, repr=False, exclude=True)
    active: bool = True
    # Timestamp of authenticated authoritative read, not original form creation time.
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone-aware authoritative observation required")
        return value

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, value: SecretStr) -> SecretStr:
        return SecretStr(normalize_phone(value.get_secret_value()))


class LookupStatus(str, Enum):
    DISABLED = "disabled"
    FOUND = "found"
    NOT_FOUND_OR_OUTSIDE_COVERAGE = "not_found_or_outside_coverage"
    UNAUTHORIZED = "unauthorized"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"


class CrmField(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    name: str = Field(max_length=256)
    bitrix_id: str = Field(min_length=1, max_length=128)
    type: str = Field(min_length=1, max_length=64)
    value: JsonValue = Field(repr=False)

    @field_validator("value")
    @classmethod
    def finite_json(cls, value: JsonValue) -> JsonValue:
        # Pydantic accepts nonstandard JSON NaN/Infinity by default, including nested values.
        def check(item: JsonValue) -> None:
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("nonfinite CRM value")
            if isinstance(item, list):
                for child in item:
                    check(child)
            elif isinstance(item, dict):
                for child in item.values():
                    check(child)
        check(value)
        return value


class CrmDeal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    name: str = Field(max_length=128)
    bitrix_id: int = Field(gt=0)
    fields: tuple[CrmField, ...] = Field(repr=False)


class CrmContact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    name: str = Field(max_length=128)
    bitrix_id: int = Field(gt=0)
    deals: tuple[CrmDeal, ...] = Field(repr=False)


class CrmLead(CrmDeal):
    contacts: tuple[CrmContact, ...] = Field(repr=False)
    deals: tuple[CrmDeal, ...] = Field(default=(), repr=False)


class CrmLookupTree(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    leads: tuple[CrmLead, ...] = Field(repr=False)
