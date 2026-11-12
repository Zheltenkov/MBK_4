"""Контракт offline-набора для интерпретации разговорных фактов."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from interpretation import (
    CandidateDisposition,
    FactCandidate,
    FactStatus,
    FactSubject,
)


DEFAULT_CASES_PATH = Path(__file__).with_name("fact_interpretation_cases.json")


class CaseHistoryMessage(BaseModel):
    """Минимальная история для контекстных «да/нет»."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class FactInterpretationCase(BaseModel):
    """Один воспроизводимый пример речи и ожидаемого решения reducer."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    phenomenon: str = Field(min_length=1)
    utterance: str = Field(min_length=1)
    history: list[CaseHistoryMessage] = Field(default_factory=list)
    initial_facts: dict[str, Any] = Field(default_factory=dict)
    candidates: list[FactCandidate] = Field(default_factory=list)
    # Допустимые, но необязательные производные, когда backend принимает оба
    # представления. Пример: overdue_duration=no_overdue можно передать или null.
    optional_candidates: list[FactCandidate] = Field(default_factory=list)
    expected_values: dict[str, str | int | float | bool] = Field(
        default_factory=dict
    )
    expected_absent_paths: list[str] = Field(default_factory=list)
    expected_statuses: dict[str, FactStatus] = Field(default_factory=dict)
    expected_dispositions: list[CandidateDisposition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_expected_decisions(self) -> "FactInterpretationCase":
        """Ожидание должно объяснять решение по каждому переданному кандидату."""
        if len(self.candidates) != len(self.expected_dispositions):
            raise ValueError(
                "число candidates должно совпадать с expected_dispositions"
            )
        keys = [_candidate_key(candidate) for candidate in self.candidates]
        if len(keys) != len(set(keys)):
            raise ValueError(
                "ground truth не должен дублировать пару path/subject"
            )
        optional_keys = [
            _candidate_key(candidate) for candidate in self.optional_candidates
        ]
        if len(optional_keys) != len(set(optional_keys)):
            raise ValueError(
                "optional ground truth не должен дублировать пару path/subject"
            )
        overlap = sorted(
            set(keys) & set(optional_keys),
            key=lambda item: (item[0], item[1].value),
        )
        if overlap:
            raise ValueError(
                f"required и optional candidates пересекаются: {overlap}"
            )
        return self


def _candidate_key(candidate: FactCandidate) -> tuple[str, FactSubject]:
    """Субъект является частью семантической идентичности утверждения."""
    return candidate.path, candidate.subject


def load_fact_interpretation_cases(
    path: Path = DEFAULT_CASES_PATH,
) -> list[FactInterpretationCase]:
    """Загружает и строго валидирует versioned offline-корпус."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("fact_interpretation_cases.json должен содержать список")
    return [FactInterpretationCase.model_validate(item) for item in raw]
