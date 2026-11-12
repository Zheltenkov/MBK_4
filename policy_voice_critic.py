"""Closed semantic diagnostics through the existing, distinct extractor model."""
from __future__ import annotations

from collections.abc import Callable
from collections import Counter
from dataclasses import dataclass, replace
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, model_validator

from config import AppConfig
from dialogue_frame import DialogueFrame
import llm_agent
from pii_masking import mask_payload_for_llm
import prompts


SemanticFlag = Literal[
    "wrong_current_turn", "unrequested_receipt", "contact_barrier", "unneeded_question", "unsupported_claim",
]


class SemanticVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accept: StrictBool
    flags: list[SemanticFlag] = Field(max_length=5)

    @model_validator(mode="after")
    def consistent_decision(self) -> SemanticVerdict:
        if len(set(self.flags)) != len(self.flags) or self.accept != (not self.flags):
            raise ValueError("inconsistent semantic verdict")
        return self


CriticReason = Literal[
    "accepted", "semantic_rejection", "model_not_distinct", "model_unavailable", "invalid_schema", "deadline_exhausted",
]
SchemaErrorCode = Literal[
    "bool_type", "missing", "literal_error", "extra_forbidden", "value_error", "list_type",
    "model_type", "model_attributes_type", "json_invalid", "too_long", "other",
]
SchemaFieldClass = Literal["accept", "flags", "flags_item", "root", "unknown"]
_SCHEMA_ERROR_CODES: dict[str, SchemaErrorCode] = {
    "bool_type": "bool_type", "missing": "missing", "literal_error": "literal_error",
    "extra_forbidden": "extra_forbidden", "value_error": "value_error", "list_type": "list_type",
    "model_type": "model_type", "model_attributes_type": "model_attributes_type",
    "json_invalid": "json_invalid", "too_long": "too_long",
}


@dataclass(frozen=True)
class CriticSchemaIssue:
    code: SchemaErrorCode
    field: SchemaFieldClass
    count: int


def _schema_issue_summary(error: BaseException) -> tuple[CriticSchemaIssue, ...]:
    """Keep bounded error classes, without inputs, messages, context or raw paths."""
    if not isinstance(error, ValidationError):
        return (CriticSchemaIssue("other", "root", 1),)
    counts: Counter[tuple[SchemaErrorCode, SchemaFieldClass]] = Counter()
    for detail in error.errors(include_url=False, include_context=False, include_input=False)[:32]:
        location = detail["loc"]
        field: SchemaFieldClass = "unknown"
        if not location:
            field = "root"
        elif location == ("accept",):
            field = "accept"
        elif location == ("flags",):
            field = "flags"
        elif len(location) == 2 and location[0] == "flags" and type(location[1]) is int:
            field = "flags_item"
        counts[(_SCHEMA_ERROR_CODES.get(detail["type"], "other"), field)] += 1
    return tuple(CriticSchemaIssue(code, field, min(count, 16))
                 for (code, field), count in sorted(counts.items())[:8])


@dataclass(frozen=True)
class CriticAssessment:
    verdict: SemanticVerdict | None
    model: str | None
    reason: CriticReason
    attempted: bool
    failure: llm_agent.ModelFailure | None = None
    elapsed_ms: int = 0
    schema_issues: tuple[CriticSchemaIssue, ...] = ()


def assess(payload: dict[str, Any], draft: str, frame: DialogueFrame, config: AppConfig, *,
           usage_collector: Callable[..., None] | None = None,
           draft_redactor: Callable[[str], str] | None = None) -> CriticAssessment:
    """The diagnostic model can reject speech but cannot alter application state."""
    started = time.monotonic()
    model = (config.extractor_model or "").strip()

    def finish(verdict: SemanticVerdict | None, reason: CriticReason, attempted: bool,
               failure: llm_agent.ModelFailure | None = None,
               schema_issues: tuple[CriticSchemaIssue, ...] = ()) -> CriticAssessment:
        elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
        return CriticAssessment(verdict, model or None, reason, attempted, failure, elapsed_ms, schema_issues)

    if not model or model.casefold() == config.model.strip().casefold():
        return finish(None, "model_not_distinct", False,
                      llm_agent.ModelFailure("configuration", "configuration", "ModelNotDistinct"))
    try:
        # Redact before embedding a draft into diagnostic DATA. The caller can use
        # original private facts locally while the renderer receives no raw context.
        safe_draft = draft_redactor(draft) if draft_redactor is not None else str(mask_payload_for_llm(
            {**payload, "latest_user_message": draft, "current_context_readonly": True}, True,
        ).get("latest_user_message") or "")
        safe = mask_payload_for_llm({**payload, "current_context_readonly": True}, True)
        messages = prompts.policy_critic_messages(
            frame, draft=safe_draft, history=llm_agent._history_messages(safe),
            latest_user_message=str(safe.get("latest_user_message") or ""),
        )
    except Exception as exc:
        return finish(None, "model_unavailable", False, llm_agent.model_failure(exc, phase="context"))
    critic_config = replace(config, model=model, enable_pii_masking=True, temperature=0.0,
                            max_tokens=min(config.max_tokens, 300), request_timeout=min(config.request_timeout, 6))
    try:
        raw = llm_agent.generate_policy_critique(messages, critic_config, usage_collector=usage_collector)
    except Exception as exc:
        return finish(None, "model_unavailable", True, llm_agent.model_failure(exc))
    try:
        if len(raw) > 4096:
            raise ValueError("oversized semantic verdict")
        verdict = SemanticVerdict.model_validate_json(raw)
    except (TypeError, ValueError) as exc:
        return finish(None, "invalid_schema", True, llm_agent.model_failure(exc, phase="schema"),
                      _schema_issue_summary(exc))
    return finish(verdict, "accepted" if verdict.accept else "semantic_rejection", True)
