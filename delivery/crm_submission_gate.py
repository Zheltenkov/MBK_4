"""Fail-closed policy для вызова боевого ``lead-intake``.

Transport-флаг разрешает сам endpoint, но этого недостаточно для локального
пилота: опечатка в конфигурации не должна превратить обычный ручной разговор в
боевой лид. Поэтому runtime требует второй явный режим:

- ``test`` — только согласованное тестовое ФИО и телефон из allowlist;
- ``production`` — штатные клиенты платформы, включается отдельно при раскатке;
- отсутствие режима — сетевой вызов блокируется.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from routing import get_fact


TEST_FULL_NAME = "Тестов Тест Тестович"


class SubmissionMode(StrEnum):
    """Явный уровень разрешения необратимого CRM-вызова."""

    BLOCKED = "blocked"
    TEST = "test"
    PRODUCTION = "production"


@dataclass(frozen=True)
class SubmissionGateDecision:
    """Результат policy без включения ПД в диагностическую причину."""

    allowed: bool
    reason: str


class SubmissionGate(Protocol):
    """Узкий интерфейс guard, который удобно подменять в unit-тестах."""

    def evaluate(self, facts: dict[str, Any]) -> SubmissionGateDecision: ...


@dataclass(frozen=True)
class LeadSubmissionGate:
    """Разрешает production POST только в явно выбранном режиме."""

    mode: SubmissionMode
    allowed_test_phones: frozenset[str] = frozenset()

    @classmethod
    def from_env(cls) -> "LeadSubmissionGate":
        """Строит fail-closed policy из runtime-конфигурации."""
        raw_mode = (os.getenv("MBK_CRM_SUBMISSION_MODE") or "").strip().lower()
        try:
            mode = SubmissionMode(raw_mode)
        except ValueError:
            mode = SubmissionMode.BLOCKED

        raw_phones = os.getenv("MBK_CRM_TEST_PHONE_ALLOWLIST") or ""
        phones = frozenset(
            normalized
            for item in raw_phones.split(",")
            if (normalized := _normalize_phone(item)) is not None
        )
        return cls(mode=mode, allowed_test_phones=phones)

    def evaluate(self, facts: dict[str, Any]) -> SubmissionGateDecision:
        """Проверяет факты до mapping и до перевода сессии в ``in_flight``."""
        if self.mode == SubmissionMode.PRODUCTION:
            return SubmissionGateDecision(True, "production_mode")
        if self.mode != SubmissionMode.TEST:
            return SubmissionGateDecision(False, "submission_mode_not_enabled")

        full_name = _normalize_name(get_fact(facts, "client.name"))
        if full_name != _normalize_name(TEST_FULL_NAME):
            return SubmissionGateDecision(False, "test_full_name_not_allowed")

        phone = _normalize_phone(get_fact(facts, "client.phone"))
        if phone is None or phone not in self.allowed_test_phones:
            return SubmissionGateDecision(False, "test_phone_not_allowed")

        return SubmissionGateDecision(True, "test_identity_allowed")


class AllowAllSubmissionGate:
    """Явная test double; production runtime её не использует."""

    def evaluate(self, facts: dict[str, Any]) -> SubmissionGateDecision:
        del facts
        return SubmissionGateDecision(True, "test_double_allow_all")


def _normalize_name(value: object) -> str:
    """Сравнивает ФИО устойчиво к регистру и повторным пробелам."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).casefold()


def _normalize_phone(value: object) -> str | None:
    """Приводит российский номер к тому же виду, что CRM mapping."""
    if not isinstance(value, str):
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) == 10:
        digits = f"7{digits}"
    elif len(digits) == 11 and digits.startswith("8"):
        digits = f"7{digits[1:]}"
    if not 10 <= len(digits) <= 15:
        return None
    return digits


__all__ = [
    "AllowAllSubmissionGate",
    "LeadSubmissionGate",
    "SubmissionGate",
    "SubmissionGateDecision",
    "SubmissionMode",
    "TEST_FULL_NAME",
]
