"""Stage-one read-context sessions never authorize a commercial lead submission."""
from __future__ import annotations

from collections.abc import Mapping

CONTEXT_STATE_KEY = "customer_context"
CONTEXT_OPENING_MESSAGE = "Здравствуйте! Данные текущей анкеты получены. Расскажите, с чем нужна помощь."


def context_delivery_held(state: Mapping[str, object]) -> bool:
    # Presence is intentional: malformed, pending and failed contexts also fail closed.
    # Releasing this hold requires the separate delivery-owner/consent contract.
    return CONTEXT_STATE_KEY in state or "onloader" in state


def public_context_metadata(state: Mapping[str, object]) -> dict[str, str] | None:
    raw = state.get(CONTEXT_STATE_KEY)
    if not isinstance(raw, dict):
        return {"context_status": "held"} if CONTEXT_STATE_KEY in state else None
    allowed = {"pending", "ready", "forbidden", "unavailable", "stale", "conflict"}
    status = raw.get("context_status")
    result = {"context_status": status if isinstance(status, str) and status in allowed else "held"}
    if status == "ready":
        result["source"] = "current_application"
    return result
