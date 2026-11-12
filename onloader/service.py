"""Authorize an opaque contact, record an explicit decision, then reconcile its signal."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import hmac
import math
import time
from typing import Protocol, TYPE_CHECKING
import uuid

import analytics
from pydantic import ValidationError

from customer_context import ContextAccessError, ContextCapabilityCodec
from customer_context.contracts import ApplicationReference
from storage.sessions import SessionCommit, SessionPersistenceError, session_record_digest

from .contracts import OnloaderCommand, OnloaderDecision, OnloaderError, OnloaderReceipt, OnloaderSnapshot, OnloaderState
from .policy import STATE_KEY, read_state, reply

if TYPE_CHECKING:
    from session_store import Session


class OnloaderBackend(Protocol):
    """Authenticated bounded I/O. All commands address the existing backend application."""

    def fetch(self, reference: ApplicationReference) -> OnloaderSnapshot | None: ...
    def inspect(self, handle: str) -> OnloaderSnapshot | None: ...
    def submit(self, handle: str, command: OnloaderCommand) -> OnloaderReceipt: ...
    def reconcile(self, handle: str, command: OnloaderCommand) -> OnloaderReceipt | None: ...


class OnloaderService:
    """Local-only composition. Caller serializes mutations with the session turn lock."""

    def __init__(self, codec: ContextCapabilityCodec, backend: OnloaderBackend, *,
                 clock: Callable[[], float] = time.time) -> None:
        if codec.audience != "mbk-onloader":
            raise ValueError("onloader requires its dedicated capability audience")
        self._codec, self._backend, self._clock = codec, backend, clock

    def _snapshot(self, snapshot: OnloaderSnapshot | None) -> OnloaderSnapshot:
        if (not isinstance(snapshot, OnloaderSnapshot) or snapshot.active is not True
                or snapshot.contact_known is not True or snapshot.delivery_owner != "site_backend"):
            raise OnloaderError("onloader_forbidden", 403)
        try:
            # Typed adapter objects may have used model_copy, which skips Pydantic validation.
            snapshot = OnloaderSnapshot.model_validate({
                **snapshot.model_dump(), "delivery_handle": snapshot.delivery_handle,
            })
        except ValidationError:
            raise OnloaderError("onloader_forbidden", 403) from None
        now = self._clock()
        age = now - snapshot.observed_at.timestamp()
        if not math.isfinite(now) or not 0 <= age < 300 or now >= snapshot.expires_at.timestamp():
            raise OnloaderError("onloader_stale", 409)
        return snapshot

    def _fetch(self, reference: ApplicationReference) -> OnloaderSnapshot:
        try:
            snapshot = self._snapshot(self._backend.fetch(reference))
        except OnloaderError:
            raise
        except Exception:
            raise OnloaderError("onloader_unavailable", 503) from None
        if snapshot.reference != reference:
            raise OnloaderError("onloader_binding_conflict")
        return snapshot

    def bind(self, session: Session, capability: str, persist: Callable[[Session], SessionCommit]) -> None:
        """Attach metadata to this conversation without replacing its facts or history."""
        previous = read_state(session.state)
        existing = session.state.get(STATE_KEY)
        if STATE_KEY in session.state and previous is None and not (
            isinstance(existing, dict) and set(existing) <= {"status", "mode"}
            and isinstance(existing.get("status"), str)
            and existing.get("status") in {"pending", "stale", "forbidden", "unavailable"}
        ):
            # A corrupt persisted intent cannot be reset into a new commercial operation.
            raise OnloaderError("onloader_not_ready")
        try:
            claims = self._codec.verify(capability, session_id=session.id)
        except ContextAccessError as error:
            # Invalid input cannot downgrade an already authorized binding.
            if previous is None:
                session.state[STATE_KEY] = {"status": "stale" if error.reason == "expired" else "forbidden"}
                self._checkpoint(session, persist)
            raise OnloaderError("onloader_forbidden", 403) from None
        digest = self._codec.binding_digest(claims.reference, session_id=session.id)
        if previous is not None and not hmac.compare_digest(previous.binding_digest, digest):
            raise OnloaderError("onloader_binding_conflict")
        if "customer_context" in session.state:
            # Stage-one context cannot be rebound through a different authority domain.
            raise OnloaderError("onloader_context_conflict")
        lead = session.state.get("crm_lead") or {}
        workflow = session.state.get("crm_workflow") or {}
        if (session.anketa or session.state.get("delivery") or lead.get("status", "not_sent") != "not_sent"
                or workflow.get("status", "not_started") != "not_started"):
            raise OnloaderError("onloader_session_conflict")
        if previous is None:
            session.state[STATE_KEY] = {"status": "pending"}
            self._checkpoint(session, persist)
        try:
            snapshot = self._fetch(claims.reference)
            now = self._clock()
            expires_at = min(float(claims.expires_at), snapshot.expires_at.timestamp(),
                             snapshot.observed_at.timestamp() + 300)
            if now >= expires_at:
                raise OnloaderError("onloader_stale")
            if previous is not None and (snapshot.operation_ref != previous.operation_ref or
                                        (previous.decision is None and snapshot.offer != previous.offer)):
                raise OnloaderError("onloader_offer_conflict")
            if previous is not None and previous.decision is not None:
                # Renew only access to an existing operation. Its command and presentation stay fixed.
                self._save(session, previous.model_copy(update={
                    "delivery_handle": snapshot.delivery_handle,
                    "authorization_expires_at": expires_at,
                }), persist)
                return
            value = OnloaderState(
                status="awaiting_consent", binding_digest=digest, operation_ref=snapshot.operation_ref,
                delivery_handle=snapshot.delivery_handle, authorization_expires_at=expires_at,
                offer_expires_at=snapshot.expires_at.timestamp(), offer=snapshot.offer,
                presentation_id=previous.presentation_id if previous else str(uuid.uuid4()),
                event_id=previous.event_id if previous else str(uuid.uuid4()),
            )
        except OnloaderError as error:
            if previous is None:
                session.state[STATE_KEY] = {"status": "forbidden" if error.status == 403 else
                                          "unavailable" if error.status == 503 else "stale"}
                self._checkpoint(session, persist)
            raise
        messages = ({"role": "assistant", "content": reply({STATE_KEY: value.storage_value()})},) if previous is None else ()
        self._save(session, value, persist, messages=messages)

    def _inspect(self, session: Session, current: OnloaderState, *, sending: bool) -> None:
        # Consent requires current authorization. Reading the already recorded outcome also
        # reauthenticates the opaque backend handle, without sending another signal.
        if self._clock() >= current.authorization_expires_at:
            raise OnloaderError("onloader_stale")
        try:
            snapshot = self._snapshot(self._backend.inspect(current.delivery_handle.get_secret_value()))
        except OnloaderError:
            raise
        except Exception:
            raise OnloaderError("onloader_unavailable", 503) from None
        if (not hmac.compare_digest(self._codec.binding_digest(snapshot.reference, session_id=session.id),
                                    current.binding_digest)
                or snapshot.operation_ref != current.operation_ref):
            raise OnloaderError("onloader_binding_conflict")
        if sending and (snapshot.offer != current.offer or self._clock() >= current.offer_expires_at):
            raise OnloaderError("onloader_offer_conflict")
        # Inspect may itself use a bounded remote adapter in a later integration.
        if self._clock() >= current.authorization_expires_at:
            raise OnloaderError("onloader_stale")

    @staticmethod
    def _command(current: OnloaderState) -> OnloaderCommand:
        if current.decision is None or current.intent_expires_at is None:
            raise OnloaderError("onloader_no_decision")
        return OnloaderCommand(operation_ref=current.operation_ref, event_id=current.event_id,
                               presentation_id=current.presentation_id, offer_id=current.offer.offer_id,
                               offer_version=current.offer.version, consent_version=current.offer.consent_version,
                               decision=current.decision, authorization_expires_at=current.intent_expires_at,
                               offer_digest=current.offer.digest())

    @staticmethod
    def _validated_receipt(command: OnloaderCommand, result: OnloaderReceipt | None) -> OnloaderReceipt:
        if not isinstance(result, OnloaderReceipt):
            raise OnloaderError("onloader_invalid_receipt", 502)
        try:
            result = OnloaderReceipt.model_validate(result.model_dump())
        except ValidationError:
            raise OnloaderError("onloader_invalid_receipt", 502) from None
        if (result.model_dump(exclude={"status", "receipt_id"}) != command.model_dump()
                or (command.decision == "decline" and result.status not in {"declined", "outcome_unknown"})
                or (command.decision == "accept" and result.status == "declined")):
            raise OnloaderError("onloader_invalid_receipt", 502)
        return result

    @staticmethod
    def _checkpoint(session: Session, persist: Callable[[Session], SessionCommit]) -> None:
        expected_revision = session.storage_revision + 1
        try:
            digest = session_record_digest(session.storage_record())
            acknowledgment = persist(session)
            if (not isinstance(acknowledgment, SessionCommit)
                    or type(acknowledgment.session_id) is not str
                    or type(acknowledgment.state_digest) is not str
                    or acknowledgment.session_id != session.id
                    or type(acknowledgment.revision) is not int
                    or acknowledgment.revision != expected_revision
                    or acknowledgment.revision != session.storage_revision
                    or acknowledgment.state_digest != digest):
                raise SessionPersistenceError("invalid_ack")
        except Exception as error:
            session._storage_uncertain = True
            reason = ("onloader_storage_conflict" if isinstance(error, SessionPersistenceError)
                      and error.reason == "conflict" else "onloader_storage_unavailable")
            raise OnloaderError(reason) from None

    @staticmethod
    def _save(session: Session, value: OnloaderState, persist: Callable[[Session], SessionCommit], *,
              messages: tuple[dict[str, str], ...] = ()) -> None:
        previous = read_state(session.state)
        session.state[STATE_KEY] = value.storage_value()
        session.state.setdefault("chat_history", []).extend(messages)
        # The history and its decision become one committed record before any receiver call.
        OnloaderService._checkpoint(session, persist)
        if previous is None or previous.status != value.status:
            # No source reference, handle, phone or recipient name enters technical events.
            analytics.get_recorder().emit(analytics.EVENTS.ONLOADER_STATE_CHANGED, session.id, {
                "status": value.status, "decision": value.decision, "mode": "local", "version": 1,
            })

    def decide(self, session: Session, action: OnloaderDecision, persist: Callable[[Session], SessionCommit]) -> None:
        current = read_state(session.state)
        if current is None:
            raise OnloaderError("onloader_not_ready")
        expected = (current.presentation_id, current.offer.offer_id, current.offer.version, current.offer.consent_version)
        if (action.presentation_id, action.offer_id, action.offer_version, action.consent_version) != expected:
            raise OnloaderError("onloader_offer_conflict")
        if current.decision is not None:
            if current.decision != action.decision:
                raise OnloaderError("onloader_decision_conflict")
            # An uncertain outcome is reconciled only by the separate read operation.
            return
        if current.status != "awaiting_consent":
            raise OnloaderError("onloader_not_ready")
        try:
            self._inspect(session, current, sending=True)
        except OnloaderError as error:
            self._save(session, current.model_copy(update={
                "status": "unavailable" if error.reason == "onloader_unavailable" else "stale",
            }), persist)
            raise
        decided = current.model_copy(update={
            "status": "signal_pending", "decision": action.decision,
            "decided_at": datetime.fromtimestamp(self._clock(), timezone.utc).isoformat(),
            "intent_expires_at": current.authorization_expires_at,
        })
        # The exact intent survives client cancellation and any ambiguous backend result.
        label = "Разрешаю передать сведения заявки" if action.decision == "accept" else "Отказываюсь от передачи заявки"
        decision_message = {
            "role": "user", "content": f"{label} компании «{current.offer.partner_name}». Версия согласия {current.offer.consent_version}."
        }
        self._save(session, decided, persist, messages=(decision_message,))
        command = self._command(decided)
        try:
            # No retry: a timeout can occur after the receiver committed its command.
            result = self._validated_receipt(command, self._backend.submit(current.delivery_handle.get_secret_value(), command))
        except Exception:
            final = decided.model_copy(update={"status": "outcome_unknown"})
        else:
            final = decided.model_copy(update={"status": result.status, "receipt_id": result.receipt_id})
        self._save(session, final, persist, messages=(
            {"role": "assistant", "content": reply({STATE_KEY: final.storage_value()})},
        ))

    def reconcile(self, session: Session, persist: Callable[[Session], SessionCommit]) -> None:
        current = read_state(session.state)
        if current is None or current.decision is None:
            raise OnloaderError("onloader_no_decision")
        if current.status not in {"signal_pending", "outcome_unknown", "queued"}:
            return
        self._inspect(session, current, sending=False)
        try:
            result = self._validated_receipt(self._command(current), self._backend.reconcile(
                current.delivery_handle.get_secret_value(), self._command(current)))
        except Exception:
            # A read outage cannot erase a previously validated queued receipt.
            final = current if current.status == "queued" else current.model_copy(update={"status": "outcome_unknown"})
        else:
            final = current.model_copy(update={"status": result.status, "receipt_id": result.receipt_id})
        if final != current:
            self._save(session, final, persist, messages=(
                {"role": "assistant", "content": reply({STATE_KEY: final.storage_value()})},
            ))
