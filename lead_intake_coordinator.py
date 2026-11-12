"""Application policy отправки завершённых и брошенных анкет в CRM.

CRM не поддерживает idempotency key или внешний ID. Дедуп по телефону в течение
15 минут возвращает старый leadID, но игнорирует новое тело. Поэтому coordinator
гарантирует application-level at-most-once на сессию и никогда автоматически не
повторяет запрос с неизвестным исходом.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from delivery.crm import (
    CrmApiError,
    CrmClassificationClient,
    LeadIntakeDisabledError,
)
from delivery.crm_contracts import LeadIntakeRequest, LeadIntakeResponse
from delivery.crm_mapping import dialog_transcript, MappingIssue, map_lead_intake
from delivery.crm_submission_gate import (
    AllowAllSubmissionGate,
    LeadSubmissionGate,
    SubmissionGate,
)
import analytics
from entry_routing import lead_blocked
from context_policy import context_delivery_held
from debt_policy import debt_delivery_held
from session_store import Session
from storage.sessions import LEAD_SUBMISSION_STATE_KEY, LeadClaim


# Ключ один на прикладной слой и на хранилище: база владеет этим поддеревом
# state, и разъехавшиеся строки означали бы дубль лида.
CRM_LEAD_STATE_KEY = LEAD_SUBMISSION_STATE_KEY
DEFAULT_INACTIVITY_SECONDS = 15 * 60


class LeadSubmissionStatus(StrEnum):
    """Устойчивые состояния at-most-once автомата одной сессии."""

    NOT_SENT = "not_sent"
    IN_FLIGHT = "in_flight"
    SUBMITTED = "submitted"
    OUTCOME_UNKNOWN = "outcome_unknown"
    REJECTED = "rejected"


class LeadSubmissionState(BaseModel):
    """Сериализуемая часть session state без анкетных ПД."""

    model_config = ConfigDict(extra="forbid")

    status: LeadSubmissionStatus = LeadSubmissionStatus.NOT_SENT
    lead_id: int | None = None
    form_filled_out: bool | None = None
    last_attempt_at: datetime | None = None
    submitted_at: datetime | None = None
    last_error: str | None = None
    resumed_after_submission: bool = False


class LeadIntakeClient(Protocol):
    """Узкий интерфейс, позволяющий тестировать policy без сети."""

    def lead_intake(self, payload: LeadIntakeRequest) -> LeadIntakeResponse: ...


class SubmissionStore(Protocol):
    """Часть SessionStore, от которой зависит at-most-once между процессами."""

    def persist(self, session: Session) -> None: ...

    def persist_submission(self, session: Session) -> None: ...

    def claim_lead_submission(self, session: Session) -> LeadClaim: ...


@dataclass(frozen=True)
class SubmissionOutcome:
    """Результат одного решения coordinator, включая no-op."""

    status: LeadSubmissionStatus
    attempted: bool
    lead_id: int | None = None
    issues: tuple[MappingIssue, ...] = ()
    reason: str | None = None


class LeadIntakeCoordinator:
    """Оркестрирует mapping, at-most-once и фиксацию результата в Session."""

    def __init__(
        self,
        client: LeadIntakeClient,
        *,
        persist: Callable[[Session], None],
        claim_submission: Callable[[Session], LeadClaim] | None = None,
        persist_submission: Callable[[Session], None] | None = None,
        inactivity: timedelta | None = None,
        now: Callable[[], datetime] | None = None,
        submission_gate: SubmissionGate | None = None,
    ) -> None:
        self._client = client
        self._persist = persist
        # Право на отправку занимается в общем хранилище, а не в памяти: без
        # этой проводки два процесса над одной базой отправят лид дважды.
        # Умолчание — одиночный процесс, каким coordinator и был в unit-тестах.
        self._claim = claim_submission or self._claim_alone
        self._persist_submission = persist_submission or persist
        self._inactivity = inactivity or timedelta(
            seconds=int(
                os.getenv(
                    "MBK_CRM_ABANDONED_INACTIVITY_SECONDS",
                    str(DEFAULT_INACTIVITY_SECONDS),
                )
            )
        )
        self._now = now or (lambda: datetime.now(timezone.utc))
        # Unit-тесты coordinator по умолчанию проверяют сам at-most-once автомат.
        # Боевой runtime всегда передаёт fail-closed LeadSubmissionGate.from_env().
        self._submission_gate = submission_gate or AllowAllSubmissionGate()

    @property
    def inactivity(self) -> timedelta:
        return self._inactivity

    def note_user_activity(self, session: Session) -> None:
        """Сбрасывает inactivity clock только реальным сообщением клиента."""
        with session._lock:
            session.last_active = self._now()
            state = _read_submission_state(session)
            if state.status == LeadSubmissionStatus.SUBMITTED:
                state.resumed_after_submission = True
                _write_submission_state(session, state)
        self._persist(session)

    def partial_is_due(
        self,
        session: Session,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Проверяет 15 минут тишины, телефон и отсутствие terminal submission."""
        checked_at = now or self._now()
        with session._lock:
            state = _read_submission_state(session)
            if state.status != LeadSubmissionStatus.NOT_SENT:
                return False
            # Человек из ветки «уже оставлял заявку» мог назвать телефон сам.
            # Через 15 минут тишины такая сессия ушла бы вторым лидом — ровно
            # тем дублем, который ветка и убирает.
            if lead_blocked(session.state):
                return False
            phone = (
                ((session.state.get("facts") or {}).get("client") or {}).get("phone")
            )
            if not isinstance(phone, str) or not phone.strip():
                return False
            # Пройденная анкета обычно означает, что лид отправит основной
            # путь. Кроме одного случая: он встал на вопросе о согласии и ждёт
            # ответа без всякого потолка. Тогда не отправит никто, и выходило
            # наоборот — бросившему анкету на середине телефон уезжает, а
            # дошедшему до конца нет.
            if _form_is_complete(session.state) and not _awaits_site_consent(
                session.state
            ):
                return False
            return checked_at - session.last_active >= self._inactivity

    def submit_partial_if_due(
        self,
        session: Session,
        *,
        now: datetime | None = None,
    ) -> SubmissionOutcome:
        """Отправляет filled_out=0 только после inactivity; иначе делает no-op."""
        checked_at = now or self._now()
        if not self.partial_is_due(session, now=checked_at):
            return _existing_outcome(session, reason="partial_not_due")
        # Анкета пройдена целиком, оборвалось только согласие — значит и лид
        # уходит как полный, а не как огрызок с одним телефоном.
        abandoned_consent = _awaits_site_consent(session.state)
        return self._submit(
            session,
            form_filled_out=abandoned_consent,
            site_consent_shown=abandoned_consent,
            site_consent_answer=None,
            consent_abandoned=abandoned_consent,
            attempted_at=checked_at,
        )

    def submit_completed(
        self,
        session: Session,
        *,
        site_consent_shown: bool,
        site_consent_answer: bool | None = None,
    ) -> SubmissionOutcome:
        """Отправляет полную анкету один раз после завершения consent workflow."""
        return self._submit(
            session,
            form_filled_out=True,
            site_consent_shown=site_consent_shown,
            site_consent_answer=site_consent_answer,
            attempted_at=self._now(),
        )

    def recover_interrupted_submission(self, session: Session) -> None:
        """После рестарта запрещает retry запроса, чей результат мог потеряться."""
        with session._lock:
            state = _read_submission_state(session)
            if state.status != LeadSubmissionStatus.IN_FLIGHT:
                return
            state.status = LeadSubmissionStatus.OUTCOME_UNKNOWN
            state.last_error = (
                "Процесс прерван во время lead-intake; автоматический retry запрещён."
            )
            _write_submission_state(session, state)
        # Обычная запись намеренно не владеет crm_lead и в общей базе оставит
        # прежний in_flight. Восстановление — это переход именно автомата лида,
        # поэтому его обязан зафиксировать тот же owner-write, что и исход POST.
        self._persist_submission(session)

    def sweep_abandoned(
        self,
        sessions: Iterable[Session],
        *,
        now: datetime | None = None,
    ) -> list[SubmissionOutcome]:
        """Один детерминированный проход scheduler без внутренних sleep/retry."""
        checked_at = now or self._now()
        outcomes: list[SubmissionOutcome] = []
        for session in sessions:
            if self.partial_is_due(session, now=checked_at):
                outcomes.append(
                    self.submit_partial_if_due(session, now=checked_at)
                )
        return outcomes

    def _submit(
        self,
        session: Session,
        *,
        form_filled_out: bool,
        site_consent_shown: bool,
        site_consent_answer: bool | None,
        consent_abandoned: bool = False,
        attempted_at: datetime,
    ) -> SubmissionOutcome:
        # Guard проверяется до mapping, сохранения IN_FLIGHT и сетевого вызова.
        # Отказ policy не расходует единственную разрешённую попытку сессии.
        with session._lock:
            if context_delivery_held(session.state) or debt_delivery_held(session.state):
                current = _read_submission_state(session)
                return SubmissionOutcome(current.status, attempted=False, reason="context_delivery_held")
            facts_for_guard = deepcopy(session.state.get("facts") or {})
        gate_decision = self._submission_gate.evaluate(facts_for_guard)
        if not gate_decision.allowed:
            with session._lock:
                blocked = _read_submission_state(session)
                blocked.last_error = gate_decision.reason
                _write_submission_state(session, blocked)
            self._persist(session)
            _record_crm_call_failed(
                session.id,
                stage="lead_intake",
                reason="gate_blocked",
                retryable=False,
            )
            return SubmissionOutcome(
                status=blocked.status,
                attempted=False,
                reason=gate_decision.reason,
            )

        # Mapping делается по консистентной копии фактов под session lock.
        with session._lock:
            current = _read_submission_state(session)
            # A context could have been attached while a submission gate evaluated.
            if context_delivery_held(session.state) or debt_delivery_held(session.state):
                return SubmissionOutcome(current.status, attempted=False, reason="context_delivery_held")
            if current.status != LeadSubmissionStatus.NOT_SENT:
                return SubmissionOutcome(
                    status=current.status,
                    attempted=False,
                    lead_id=current.lead_id,
                    reason="session_already_has_submission_outcome",
                )
            facts = deepcopy(session.state.get("facts") or {})
            mapping = map_lead_intake(
                facts,
                form_filled_out=form_filled_out,
                site_consent_shown=site_consent_shown,
                site_consent_answer=site_consent_answer,
                consent_abandoned=consent_abandoned,
                attribution=session.state.get("attribution") or None,
                dialog=dialog_transcript(session.state.get("chat_history")),
            )
            if not mapping.ready or mapping.request is None:
                return SubmissionOutcome(
                    status=LeadSubmissionStatus.NOT_SENT,
                    attempted=False,
                    issues=mapping.issues,
                    reason="lead_mapping_not_ready",
                )

            # IN_FLIGHT сохраняется до внешнего вызова. Если процесс умрёт после
            # отправки, восстановление переведёт его в OUTCOME_UNKNOWN без retry.
            current.status = LeadSubmissionStatus.IN_FLIGHT
            current.form_filled_out = form_filled_out
            current.last_attempt_at = attempted_at
            current.last_error = None
            _write_submission_state(session, current)

        # Проверка выше смотрит в память своего процесса, а сессию во время
        # сине-зелёной выкатки ведут два: оба поднимают её restore'ом на старте
        # и у обоих тикает sweeper брошенных лидов. Слот отправки поэтому
        # занимается в общей базе, и этот же вызов сохраняет IN_FLIGHT.
        claim = self._claim(session)
        if not claim.granted:
            return self._yield_to_claim_holder(session, claim)

        try:
            response = self._client.lead_intake(mapping.request)
        except LeadIntakeDisabledError as exc:
            # Отключённый transport означает, что запрос гарантированно не уходил.
            # Причина — та же, что и у отказа LeadSubmissionGate выше: осознанный
            # локальный запрет, а не сбой транспорта.
            return self._finish_without_attempt(
                session,
                str(exc),
                reason="gate_blocked",
                error_class=type(exc).__name__,
            )
        except CrmApiError as exc:
            status = (
                LeadSubmissionStatus.OUTCOME_UNKNOWN
                if exc.retryable
                else LeadSubmissionStatus.REJECTED
            )
            return self._finish_error(
                session,
                status,
                str(exc),
                reason=exc.reason,
                retryable=exc.retryable,
                http_status=exc.http_status,
                error_class=type(exc).__name__,
            )
        except Exception as exc:  # noqa: BLE001 — неизвестный исход нельзя ретраить
            return self._finish_error(
                session,
                LeadSubmissionStatus.OUTCOME_UNKNOWN,
                f"{type(exc).__name__}: {exc}",
                reason="http_error",
                retryable=False,
                error_class=type(exc).__name__,
            )

        with session._lock:
            completed = _read_submission_state(session)
            completed.status = LeadSubmissionStatus.SUBMITTED
            completed.lead_id = response.result.lead_id
            completed.submitted_at = self._now()
            completed.last_error = None
            _write_submission_state(session, completed)
        self._persist_submission(session)
        _record_lead_analytics(session.id, response.result.lead_id)
        return SubmissionOutcome(
            status=LeadSubmissionStatus.SUBMITTED,
            attempted=True,
            lead_id=response.result.lead_id,
        )

    def _claim_alone(self, session: Session) -> LeadClaim:
        """Одиночный процесс: занимать слот не у кого, но IN_FLIGHT сохраняем."""
        self._persist(session)
        return LeadClaim(granted=True)

    def _yield_to_claim_holder(
        self,
        session: Session,
        claim: LeadClaim,
    ) -> SubmissionOutcome:
        """Слот занят другим процессом — POST не делаем и берём его исход."""
        if claim.error is not None:
            # Свободен слот или нет — неизвестно, но запрос точно не уходил.
            # Возврат в NOT_SENT оставляет лид следующему проходу sweeper'а:
            # молча потерять анкету хуже, чем отправить её на 30 секунд позже.
            with session._lock:
                unknown = _read_submission_state(session)
                unknown.status = LeadSubmissionStatus.NOT_SENT
                unknown.form_filled_out = None
                unknown.last_attempt_at = None
                unknown.last_error = claim.error
                _write_submission_state(session, unknown)
            self._persist(session)
            return SubmissionOutcome(
                status=LeadSubmissionStatus.NOT_SENT,
                attempted=False,
                reason="submission_slot_unavailable",
            )

        # Исход победителя забирается целиком: придумывать свой нельзя, а
        # ошибка доставки на этом месте показала бы клиенту сбой там, где лид
        # уже уехал.
        holder = _claim_holder_state(claim.lead_state)
        with session._lock:
            _write_submission_state(session, holder)
        self._persist(session)
        return SubmissionOutcome(
            status=holder.status,
            attempted=False,
            lead_id=holder.lead_id,
            reason="session_submission_claimed_by_another_process",
        )

    def _finish_without_attempt(
        self,
        session: Session,
        error: str,
        *,
        reason: str,
        error_class: str | None = None,
    ) -> SubmissionOutcome:
        with session._lock:
            state = _read_submission_state(session)
            state.status = LeadSubmissionStatus.NOT_SENT
            state.form_filled_out = None
            state.last_attempt_at = None
            state.last_error = error
            _write_submission_state(session, state)
        # Слот занят этим процессом, и вернуть его в базу обязан он же.
        self._persist_submission(session)
        _record_crm_call_failed(
            session.id,
            stage="lead_intake",
            reason=reason,
            retryable=False,
            error_class=error_class,
        )
        return SubmissionOutcome(
            status=LeadSubmissionStatus.NOT_SENT,
            attempted=False,
            reason="lead_intake_disabled",
        )

    def _finish_error(
        self,
        session: Session,
        status: LeadSubmissionStatus,
        error: str,
        *,
        reason: str,
        retryable: bool,
        http_status: int | None = None,
        error_class: str | None = None,
    ) -> SubmissionOutcome:
        with session._lock:
            state = _read_submission_state(session)
            state.status = status
            state.last_error = error[:500]
            _write_submission_state(session, state)
        self._persist_submission(session)
        _record_crm_call_failed(
            session.id,
            stage="lead_intake",
            reason=reason,
            retryable=retryable,
            http_status=http_status,
            error_class=error_class,
        )
        return SubmissionOutcome(
            status=status,
            attempted=True,
            reason=error[:500],
        )


def coordinator_for_store(
    client: LeadIntakeClient,
    store: SubmissionStore,
    *,
    inactivity: timedelta | None = None,
    now: Callable[[], datetime] | None = None,
    submission_gate: SubmissionGate | None = None,
) -> LeadIntakeCoordinator:
    """Единственная проводка coordinator к хранилищу.

    Собрана отдельной функцией, чтобы забыть про захват слота было негде:
    coordinator без него отправит лид повторно, как только рядом окажется
    второй процесс над той же базой.
    """
    return LeadIntakeCoordinator(
        client,
        persist=store.persist,
        claim_submission=store.claim_lead_submission,
        persist_submission=store.persist_submission,
        inactivity=inactivity,
        now=now,
        submission_gate=submission_gate,
    )


def create_runtime_coordinator(store: SubmissionStore) -> LeadIntakeCoordinator:
    """Production wiring с transport safety-флагом из окружения."""
    return coordinator_for_store(
        CrmClassificationClient(),
        store,
        submission_gate=LeadSubmissionGate.from_env(),
    )


def _awaits_site_consent(state: dict[str, object]) -> bool:
    """Диалог стоит на вопросе о согласии и ждёт ответа клиента."""
    workflow = state.get("crm_workflow")
    if not isinstance(workflow, dict):
        return False
    return workflow.get("status") == "awaiting_site_consent"


def _form_is_complete(state: dict[str, object]) -> bool:
    plan = state.get("plan")
    action = plan.get("action") if isinstance(plan, dict) else None
    return action in {"route_found", "no_fit"}


def _read_submission_state(session: Session) -> LeadSubmissionState:
    raw = session.state.get(CRM_LEAD_STATE_KEY)
    if isinstance(raw, dict):
        return LeadSubmissionState.model_validate(raw)
    return LeadSubmissionState()


def _write_submission_state(
    session: Session,
    state: LeadSubmissionState,
) -> None:
    session.state[CRM_LEAD_STATE_KEY] = state.model_dump(mode="json")


def _claim_holder_state(raw: object) -> LeadSubmissionState:
    """Состояние процесса, занявшего слот. Непрочитанное — исход неизвестен."""
    if isinstance(raw, dict):
        try:
            return LeadSubmissionState.model_validate(raw)
        except Exception:  # noqa: BLE001 — чужая запись не должна ронять ход
            pass
    return LeadSubmissionState(
        status=LeadSubmissionStatus.OUTCOME_UNKNOWN,
        last_error="Слот отправки занят другим процессом; исход неизвестен.",
    )


def _record_lead_analytics(session_id: str, lead_id: int | None) -> None:
    """Кладёт номер лида в аналитику — ключ связи с карточкой в Битриксе.

    Дата-инженер заказчика читает только `analytics_events` и
    `dialog_messages`: в `sessions`, где номер лида лежит с самого начала,
    есть ПД, и доступа туда у него нет по построению. Без этой строки связать
    разговор с карточкой можно было лишь по телефону, а в переписке от номера
    остаётся только солёный отпечаток.

    Ключ дедупликации — на сессию: лид создаётся не более одного раза, и
    повторный вызов (восстановление после рестарта, известный исход) не должен
    добавлять вторую строку.
    """
    if not session_id or lead_id is None:
        return
    analytics.get_recorder().emit(
        analytics.EVENTS.LEAD_SUBMITTED,
        session_id,
        {"lead_id": lead_id},
        dedup_key=f"{session_id}:lead_submitted",
    )


def _record_crm_call_failed(
    session_id: str,
    *,
    stage: str,
    reason: str,
    retryable: bool,
    http_status: int | None = None,
    error_class: str | None = None,
) -> None:
    """Кладёт факт неудавшегося вызова CRM в аналитику.

    Источник правды для stage="lead_intake": и завершённая анкета, и брошенная
    (`submit_partial_if_due`) проходят один и тот же `_submit`, поэтому одна
    точка записи покрывает оба пути. Без dedup_key — сессия у at-most-once
    coordinator тратит попытку один раз, повторной строки просто неоткуда
    взяться, а восстановление после рестарта (`recover_interrupted_submission`)
    сюда не заходит вовсе: оно не делает новый вызов, только помечает исход
    прежнего как неизвестный.
    """
    payload: dict[str, object] = {
        "stage": stage,
        "reason": reason,
        "retryable": retryable,
    }
    if http_status is not None:
        payload["http_status"] = http_status
    if error_class is not None:
        payload["error_class"] = error_class
    analytics.get_recorder().emit(analytics.EVENTS.CRM_CALL_FAILED, session_id, payload)


def _existing_outcome(session: Session, *, reason: str) -> SubmissionOutcome:
    with session._lock:
        state = _read_submission_state(session)
    return SubmissionOutcome(
        status=state.status,
        attempted=False,
        lead_id=state.lead_id,
        reason=reason,
    )


__all__ = [
    "CRM_LEAD_STATE_KEY",
    "LeadIntakeCoordinator",
    "LeadSubmissionState",
    "LeadSubmissionStatus",
    "SubmissionOutcome",
    "SubmissionStore",
    "coordinator_for_store",
    "create_runtime_coordinator",
]
