"""Closed model projection for authenticated, but still user-authored, form data."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from context_policy import CONTEXT_STATE_KEY
from debt_policy import POLICY_KEY, parse_policy
from routing import get_fact, get_matrix
from routing.normalize import normalize_fact
from utils import set_by_path


def project_context_state(state: dict[str, Any]) -> dict[str, Any]:
    """Routing retains original facts; model/public copies omit every free-text field."""
    safe = deepcopy(state)
    # An opaque delivery handle and consent event belong only to server orchestration.
    safe.pop("onloader", None)
    facts = state.get("facts") or {}
    projected: dict[str, Any] = {}
    matrix = get_matrix()
    for path, spec in matrix.fields.items():
        if spec.kind not in {"money", "int", "bool", "enum"}:
            continue
        try:
            value = normalize_fact(path, get_fact(facts, path), matrix)
        except (ValueError, TypeError, OverflowError):
            continue
        if value is None:
            continue
        # Enum values must belong to the matrix, never an arbitrary backend string.
        if spec.kind == "enum" and value not in (spec.values or []):
            continue
        set_by_path(projected, path, value)
    if get_fact(facts, "client.phone"):
        # This is a model-only presence marker, never an outbound contact value.
        set_by_path(projected, "client.phone", "[телефон известен]")
    safe["facts"] = projected
    if (policy := parse_policy(state)) is not None:
        # Corrupt/extra state metadata must not smuggle free text into diagnostic snapshots.
        safe[POLICY_KEY] = policy.as_state()
    safe[CONTEXT_STATE_KEY] = {"context_status": "ready"}
    return safe
