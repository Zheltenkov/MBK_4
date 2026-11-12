"""Тонкая обёртка над Langfuse v3.

Принципы:
- Полностью НЕОБЯЗАТЕЛЬНА. Если LANGFUSE_PUBLIC_KEY/SECRET_KEY не заданы, или langfuse не
  установлен, или `LANGFUSE_DISABLED=true` — все функции этого модуля становятся no-op.
- Любая ошибка observability глотается. Диалог никогда не падает из-за трейсинга.
- PII (телефон, имя, адрес, ДР) в метаданных трейса РЕДАКТИТСЯ. Полные промпты, которые
  ушли в модель, всё равно содержат факты — если PII критичен, поднимай self-hosted Langfuse
  или ставь LANGFUSE_DISABLED=true.

Использование:
    with observability.turn(session_id, user_message, current_facts, anketa) as span:
        with observability.generation("conversation", model, messages, params) as gen:
            ...
            observability.finalize_generation(gen, output=full_text, usage={...})
        observability.finalize_turn(span, output={"messages": bubbles, ...})
"""
from __future__ import annotations

import contextlib
import os
import sys
from typing import Any, Iterator

_REDACT_KEYS = {"phone", "full_name", "registration_address", "living_address", "birth_date"}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[redacted]" if k in _REDACT_KEYS else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(x) for x in value]
    return value


def _get_client():
    if os.getenv("LANGFUSE_DISABLED", "").lower() in {"1", "true", "yes"}:
        return None
    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        return None
    try:
        from langfuse import get_client  # type: ignore

        return get_client()  # читает env-переменные сам
    except Exception:
        return None


def usage_from_openrouter(raw_usage: dict | None) -> dict:
    """Маппинг {prompt_tokens, completion_tokens, total_tokens} → формат Langfuse."""
    if not raw_usage:
        return {}
    inp = raw_usage.get("prompt_tokens") or raw_usage.get("input_tokens")
    out = raw_usage.get("completion_tokens") or raw_usage.get("output_tokens")
    total = raw_usage.get("total_tokens")
    details: dict[str, Any] = {}
    if inp is not None:
        details["input"] = inp
    if out is not None:
        details["output"] = out
    if total is not None:
        details["total"] = total
    return details


@contextlib.contextmanager
def turn(
    session_id: str | None,
    user_message: str,
    current_facts: dict | None = None,
    anketa: dict | None = None,
) -> Iterator[Any]:
    """Контекст одного хода диалога. Внутри него generation() автоматически подвесятся
    дочерними наблюдениями (через OTel-контекст)."""
    client = _get_client()
    if client is None:
        yield None
        return
    try:
        cm = client.start_as_current_observation(
            as_type="span",
            name="mbk_turn",
            input={
                "user_message": user_message,
                "current_facts_redacted": _redact(current_facts or {}),
            },
        )
        span = cm.__enter__()
    except Exception:
        # Любая поломка трейсинга — диалог продолжается
        yield None
        return

    try:
        try:
            span.update_trace(
                session_id=session_id,
                metadata={"anketa_redacted": _redact(anketa or {})},
            )
        except Exception:
            pass
        yield span
    except BaseException:
        exc_info = sys.exc_info()
        try:
            cm.__exit__(*exc_info)
        except Exception:
            pass
        raise
    else:
        try:
            cm.__exit__(None, None, None)
        except Exception:
            pass
    finally:
        try:
            client.flush()
        except Exception:
            pass


@contextlib.contextmanager
def generation(
    name: str,
    model: str,
    input_messages: list[dict] | None = None,
    model_parameters: dict | None = None,
) -> Iterator[Any]:
    """Один LLM-вызов. Если идёт внутри turn() — автоматически становится его дочерним."""
    client = _get_client()
    if client is None:
        yield None
        return
    try:
        cm = client.start_as_current_observation(
            as_type="generation",
            name=name,
            model=model,
            input=input_messages or [],
            model_parameters=model_parameters or {},
        )
        gen = cm.__enter__()
    except Exception:
        yield None
        return

    try:
        yield gen
    except BaseException:
        exc_info = sys.exc_info()
        try:
            cm.__exit__(*exc_info)
        except Exception:
            pass
        raise
    else:
        try:
            cm.__exit__(None, None, None)
        except Exception:
            pass


def finalize_generation(
    gen: Any,
    output: Any = None,
    usage: dict | None = None,
    error: str | None = None,
) -> None:
    if gen is None:
        return
    try:
        kwargs: dict[str, Any] = {}
        if output is not None:
            kwargs["output"] = output
        if usage:
            kwargs["usage_details"] = usage
        if error:
            kwargs["level"] = "ERROR"
            kwargs["status_message"] = error
        if kwargs:
            gen.update(**kwargs)
    except Exception:
        pass


def finalize_turn(span: Any, output: dict | None = None) -> None:
    if span is None:
        return
    try:
        if output is not None:
            span.update(output=output)
    except Exception:
        pass
