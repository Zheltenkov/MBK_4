"""Attach a verified current form before dialogue, independently of CRM history."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import time
from typing import TYPE_CHECKING

from customer_context import ContextAccessError, CrmLookupClient, CurrentContextService
from context_policy import CONTEXT_STATE_KEY, public_context_metadata
from debt_policy import POLICY_KEY, select_policy, trusted_signal_from_authorized
from state import seed_facts

if TYPE_CHECKING:
    from session_store import Session


class ContextAttachmentError(RuntimeError):
    """Only closed safe reasons escape to HTTP; backend data never enters errors."""

    def __init__(self, reason: str, status: int) -> None:
        self.reason, self.status = reason, status
        super().__init__(reason)


@dataclass(frozen=True)
class AttachmentResult:
    metadata: dict[str, str]
    idempotent: bool


class ContextAttachmentCoordinator:
    def __init__(self, service: CurrentContextService, *, persist: Callable[[Session], None],
                 lookup: CrmLookupClient | None = None, debt_enabled: bool = False) -> None:
        self._service, self._persist, self._lookup = service, persist, lookup
        self._debt_enabled = debt_enabled

    def attach(self, session: Session, capability: str) -> AttachmentResult:
        """Caller holds the owning session's turn_lock throughout this operation."""
        if "onloader" in session.state:
            raise ContextAttachmentError("context_conflict", 409)
        existing = session.state.get(CONTEXT_STATE_KEY)
        digest = existing.get("binding_digest") if isinstance(existing, dict) else None
        if isinstance(digest, str):
            # Authenticate even repeats, but do not refresh fields or consume lookup quota.
            try:
                incoming = self._service.binding_digest(capability, session_id=session.id)
                if incoming != digest:
                    raise ContextAccessError("binding_conflict")
            except ContextAccessError as error:
                raise self._safe_error(error) from None
            return AttachmentResult(public_context_metadata(session.state) or {}, True)

        # An established conversation/submission must not be overwritten by a new form.
        history = session.state.get("chat_history") or []
        has_client_turn = any(isinstance(item, dict) and item.get("role") == "user" for item in history)
        workflow = session.state.get("crm_workflow") or {}
        lead = session.state.get("crm_lead") or {}
        if (has_client_turn or session.state.get("delivery")
                or workflow.get("status", "not_started") != "not_started"
                or lead.get("status", "not_sent") != "not_sent"):
            raise ContextAttachmentError("session_started", 409)

        # Persist the hold BEFORE any backend I/O so existing sweepers cannot submit.
        session.state[CONTEXT_STATE_KEY] = {"context_status": "pending"}
        self._persist(session)
        if session.anketa:
            session.state[CONTEXT_STATE_KEY] = {"context_status": "conflict"}
            self._persist(session)
            raise ContextAttachmentError("browser_form_conflict", 409)
        try:
            authorized = self._service.resolve(capability, session_id=session.id)
        except ContextAccessError as error:
            normalized = self._safe_error(error)
            state = "stale" if error.reason in {"expired", "snapshot_stale"} else (
                "unavailable" if normalized.status == 503 else "forbidden"
            )
            session.state[CONTEXT_STATE_KEY] = {"context_status": state}
            self._persist(session)
            raise normalized from None

        # Seed the existing normalized fact model. No raw snapshot or capability is stored.
        session.state["facts"] = seed_facts(authorized.to_anketa())
        session.state[CONTEXT_STATE_KEY] = authorized.safe_context_state()
        session.anketa = None
        if self._debt_enabled:
            # Only this live authorized handle may create a server-originated entry hint.
            try:
                signal = trusted_signal_from_authorized(authorized, time.time())
            except ContextAccessError:
                # Expiry after the successful source read must not mint a new policy signal.
                signal = None
            policy = select_policy(session.state, "", enabled=True, trusted_signal=signal)
            if policy is not None:
                session.state[POLICY_KEY] = policy.as_state()
        # History is opt-in, private and never merged with current facts or LLM input.
        if self._lookup is not None:
            try:
                result = self._lookup.lookup(authorized)
            except Exception:
                # History is optional; an expired/failed history read cannot erase a valid form.
                session.state[CONTEXT_STATE_KEY]["history_status"] = "unavailable"
            else:
                session.state[CONTEXT_STATE_KEY].update(result.public_metadata())
        self._persist(session)
        return AttachmentResult(authorized.public_metadata(), False)

    @staticmethod
    def _safe_error(error: ContextAccessError) -> ContextAttachmentError:
        if error.reason == "binding_conflict":
            return ContextAttachmentError("context_conflict", 409)
        if error.reason in {"source_unavailable", "source_missing", "disabled"}:
            return ContextAttachmentError("context_unavailable", 503)
        return ContextAttachmentError("context_forbidden", 403)
