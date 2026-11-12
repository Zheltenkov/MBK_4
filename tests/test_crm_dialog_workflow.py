"""Сквозной application workflow CRM без сетевых вызовов."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import pytest

import analytics
import core
from analytics import AnalyticsRecorder, MemorySink
from crm_dialog_workflow import (
    CrmDialogStatus,
    CrmDialogWorkflow,
    parse_site_consent_answer,
)
from delivery.crm import CrmApiError
from delivery.crm_contracts import (
    FirstQualificationRequest,
    FirstQualificationResponse,
    LeadIntakeRequest,
    LeadIntakeResponse,
)
from delivery.crm_submission_gate import LeadSubmissionGate, SubmissionMode
from lead_intake_coordinator import LeadIntakeCoordinator
from routing import Plan, Route, RouteStatus
from session_store import Session
from state import init_state
from storage.sessions import LeadClaim
from usage_tracker import SessionUsage


NOW = datetime(2026, 7, 24, 15, 0, tzinfo=timezone.utc)
TEST_PHONE = "79991234567"


class FakeCrmClient:
    """Один fake обслуживает read-only qualification и irreversible lead intake."""

    def __init__(
        self,
        *,
        category: int | None = 2217,
        need_site_consent: bool = False,
        thanks_url: str | None = None,
        lead_result: object | None = None,
    ) -> None:
        self.category = category
        self.need_site_consent = need_site_consent
        self.thanks_url = thanks_url
        self.qualification_requests: list[FirstQualificationRequest] = []
        self.lead_requests: list[LeadIntakeRequest] = []
        self.lead_result = lead_result or LeadIntakeResponse.model_validate(
            {"result": {"leadID": 3_431_700}, "success": True}
        )

    def first_qualification(
        self,
        payload: FirstQualificationRequest,
    ) -> FirstQualificationResponse:
        self.qualification_requests.append(payload)
        return FirstQualificationResponse.model_validate(
            {
                "success": True,
                "result": {
                    "matched": self.category is not None,
                    "priority": "1-1" if self.category is not None else None,
                    "suitable_categories": (
                        [self.category] if self.category is not None else []
                    ),
                    "segment": "msk_mo",
                    "partner": None,
                    "offer": None,
                    "suitable_offers": [],
                    "need_partner_consent": False,
                    "vertical": "realty_pledge",
                    "category": self.category,
                    "need_site_consent": self.need_site_consent,
                    "thanks_url": self.thanks_url,
                },
            }
        )

    def lead_intake(self, payload: LeadIntakeRequest) -> LeadIntakeResponse:
        self.lead_requests.append(payload)
        if isinstance(self.lead_result, Exception):
            raise self.lead_result
        assert isinstance(self.lead_result, LeadIntakeResponse)
        return self.lead_result


def _facts(
    *,
    name: str = "Тестов Тест Тестович",
    phone: str = f"+{TEST_PHONE}",
) -> dict:
    return {
        "client": {
            "name": name,
            "phone": phone,
            "age": 42,
            "goal": "cash",
            "region_class": "moscow_mo",
            "region": "Москва",
        },
        "request": {"amount": 1_500_000},
        "assets": {
            "has_realty": True,
            "realty": {
                "region_class": "moscow_mo",
                "region": "Москва",
                "kind": "flat",
                "ownership_form": "private",
                "owners": "client_only",
            },
            "has_vehicle": False,
        },
        # Анкета проходится целиком, поэтому блок долгов тоже закрыт.
        "debts": {
            "has_active_credits": True,
            "total": 900_000,
            "credit_history": "no_overdue",
            "overdue_duration": "no_overdue",
        },
        "employment": {"type": "official", "income": 200_000},
    }


def _session(
    *,
    name: str = "Тестов Тест Тестович",
    plan_action: Literal["route_found", "no_fit"] = "route_found",
) -> Session:
    route = Route(
        id="realty_msk",
        priority="1-1",
        product="Залог недвижимости",
        segment="Мск/МО",
        family="realty",
    )
    matched = RouteStatus(route=route, state="PASS")
    is_route_found = plan_action == "route_found"
    plan = Plan(
        action=plan_action,
        statuses=[matched],
        matched=matched if is_route_found else None,
    )
    state = init_state()
    state["facts"] = _facts(name=name)
    state["plan"] = {
        "action": plan_action,
        "fields": [],
        "target": None,
        "matched": "realty_msk" if is_route_found else None,
        "routes": [],
    }
    state["_plan_object"] = plan
    return Session(
        id="crm-dialog-test",
        state=state,
        anketa=None,
        usage=SessionUsage(session_id="crm-dialog-test"),
        created_at=NOW,
        last_active=NOW,
    )


def _workflow(
    client: FakeCrmClient,
    persisted: list[str],
    *,
    gate: LeadSubmissionGate | None = None,
) -> CrmDialogWorkflow:
    active_gate = gate or LeadSubmissionGate(
        mode=SubmissionMode.TEST,
        allowed_test_phones=frozenset({TEST_PHONE}),
    )
    coordinator = LeadIntakeCoordinator(
        client,
        persist=lambda session: persisted.append(session.id),
        now=lambda: NOW,
        submission_gate=active_gate,
    )
    return CrmDialogWorkflow(
        client,
        coordinator,
        active_gate,
        persist=lambda session: persisted.append(session.id),
        enabled=True,
    )


def test_non_test_name_stays_local_and_never_calls_crm():
    persisted: list[str] = []
    client = FakeCrmClient()
    session = _session(name="Василий")

    result = _workflow(client, persisted).advance(session, "")

    assert result.status == CrmDialogStatus.GATED_LOCAL
    assert client.qualification_requests == []
    assert client.lead_requests == []
    assert session.state["delivery"]["status"] == "completed"
    assert session.state["delivery"]["request"]["schema_version"] == "mbk.lead.v1"


def test_no_consent_required_qualifies_and_submits_once():
    persisted: list[str] = []
    client = FakeCrmClient(need_site_consent=False)
    session = _session()
    session.state["facts"]["client"]["display_name"] = "Тест"
    workflow = _workflow(client, persisted)

    first = workflow.advance(session, "")
    second = workflow.advance(session, "Ещё вопрос")

    assert first.status == CrmDialogStatus.SUBMITTED
    assert second.status == CrmDialogStatus.SUBMITTED
    assert len(client.qualification_requests) == 1
    assert len(client.lead_requests) == 1
    assert client.lead_requests[0].agree_partners == 23002
    assert client.lead_requests[0].last_name == "Тестов"
    assert client.lead_requests[0].name == "Тест"
    assert client.lead_requests[0].second_name == "Тестович"
    assert session.state["crm_lead"]["lead_id"] == 3_431_700


@pytest.fixture()
def chat_consent_enabled(monkeypatch):
    """Прежнее поведение: бот спрашивает согласие сам.

    Оставлено под выключателем на случай, если у заказчика маршрутизация к
    партнёру ждёт 23000 именно от нас.
    """
    monkeypatch.setenv("MBK_ASK_SITE_CONSENT_IN_CHAT", "1")


def test_consent_is_not_asked_in_chat_by_default():
    """Согласие даёт сайт, а не бот.

    Заказчик 20.08.2026: клиент получает ссылку, переходит на офер и даёт
    согласие там. Пока бот спрашивал сам, ответ «да» уезжал как 23000 —
    партнёр получал согласие, которого человек не давал и дать не мог, потому
    что офер-страницы не видел.
    """
    persisted: list[str] = []
    client = FakeCrmClient(need_site_consent=True)
    session = _session()
    workflow = _workflow(client, persisted)

    result = workflow.advance(session, "")

    assert result.status == CrmDialogStatus.SUBMITTED
    assert len(client.lead_requests) == 1
    assert client.lead_requests[0].agree_partners == 23002
    assert session.state.get("dialog_phase") != "awaiting_site_consent"


def test_the_client_is_not_kept_waiting_for_the_link():
    """Лид и ссылка уходят сразу: паузу держали мы сами, не их API."""
    client = FakeCrmClient(need_site_consent=True)
    session = _session()
    workflow = _workflow(client, [])

    workflow.advance(session, "")

    assert session.state["crm_workflow"]["need_site_consent"] is False
    assert session.state["crm_workflow"]["thanks_url"]


def test_required_consent_waits_for_explicit_yes(chat_consent_enabled):
    persisted: list[str] = []
    client = FakeCrmClient(need_site_consent=True)
    session = _session()
    workflow = _workflow(client, persisted)

    awaiting = workflow.advance(session, "")
    unclear = workflow.advance(session, "Мне надо подумать")
    requests_before_answer = len(client.lead_requests)
    submitted = workflow.advance(session, "Да, давайте")

    assert awaiting.status == CrmDialogStatus.AWAITING_SITE_CONSENT
    assert unclear.status == CrmDialogStatus.AWAITING_SITE_CONSENT
    assert requests_before_answer == 0
    assert submitted.status == CrmDialogStatus.SUBMITTED
    assert len(client.qualification_requests) == 1
    assert len(client.lead_requests) == 1
    assert client.lead_requests[0].agree_partners == 23000


def test_required_consent_maps_explicit_no(chat_consent_enabled):
    persisted: list[str] = []
    client = FakeCrmClient(need_site_consent=True)
    session = _session()
    workflow = _workflow(client, persisted)

    workflow.advance(session, "")
    result = workflow.advance(session, "Нет, консультация не нужна")

    assert result.status == CrmDialogStatus.SUBMITTED
    assert len(client.lead_requests) == 1
    assert client.lead_requests[0].agree_partners == 23001


def test_category_null_still_creates_lead():
    persisted: list[str] = []
    client = FakeCrmClient(category=None, need_site_consent=False)
    session = _session()

    result = _workflow(client, persisted).advance(session, "")

    assert result.status == CrmDialogStatus.SUBMITTED
    assert result.qualified is False
    assert result.category is None
    assert len(client.lead_requests) == 1


def test_local_no_fit_still_runs_crm_and_creates_completed_lead():
    persisted: list[str] = []
    client = FakeCrmClient(category=None, need_site_consent=False)
    session = _session(plan_action="no_fit")

    result = _workflow(client, persisted).advance(session, "")

    assert result.status == CrmDialogStatus.SUBMITTED
    assert result.qualified is False
    assert len(client.qualification_requests) == 1
    assert len(client.lead_requests) == 1
    assert client.lead_requests[0].form_filled_out == 1
    assert session.state["delivery"]["status"] == "completed"
    assert session.state["delivery"]["request"]["routing"]["route_id"] is None


def test_safe_thanks_url_is_exposed_in_persisted_workflow_state():
    persisted: list[str] = []
    client = FakeCrmClient(
        category=2217,
        thanks_url="/spasibo-zaim-zalog-pts/",
    )
    session = _session()

    result = _workflow(client, persisted).advance(session, "")

    assert result.status == CrmDialogStatus.SUBMITTED
    assert result.thanks_url == "https://www.mbk.ru/spasibo-zaim-zalog-pts/"
    assert (
        session.state["crm_workflow"]["thanks_url"]
        == "https://www.mbk.ru/spasibo-zaim-zalog-pts/"
    )


def test_external_partner_url_is_exposed_in_persisted_workflow_state():
    persisted: list[str] = []
    client = FakeCrmClient(
        category=2217,
        thanks_url="https://partner.example/offer?id=42",
    )
    session = _session()

    result = _workflow(client, persisted).advance(session, "")

    assert result.status == CrmDialogStatus.SUBMITTED
    assert result.thanks_url == "https://partner.example/offer?id=42"
    assert session.state["crm_workflow"]["thanks_url"] == result.thanks_url


def test_lead_timeout_becomes_unknown_and_is_never_retried():
    persisted: list[str] = []
    client = FakeCrmClient(
        lead_result=CrmApiError("timeout", retryable=True),
    )
    session = _session()
    workflow = _workflow(client, persisted)

    first = workflow.advance(session, "")
    second = workflow.advance(session, "Повторите")

    assert first.status == CrmDialogStatus.OUTCOME_UNKNOWN
    assert second.status == CrmDialogStatus.OUTCOME_UNKNOWN
    assert len(client.lead_requests) == 1


def test_retryable_qualification_is_retried_without_losing_the_dialog(monkeypatch):
    client = FakeCrmClient(need_site_consent=False)
    original = client.first_qualification
    calls = 0

    def transient_failure(payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise CrmApiError("temporary outage", retryable=True)
        return original(payload)

    monkeypatch.setattr(client, "first_qualification", transient_failure)
    session = _session()
    workflow = _workflow(client, [])

    pending = workflow.advance(session, "")
    submitted = workflow.retry_pending(session)

    assert pending.status == CrmDialogStatus.QUALIFICATION_RETRY_PENDING
    assert submitted is not None
    assert submitted.status == CrmDialogStatus.SUBMITTED
    assert len(client.lead_requests) == 1
    assert session.state["dialog_phase"] == "survey_completed"


# --------------------------------------------------------------------------- #
# `crm_call_failed` — видимость сбоев обмена с CRM (дизайн 07.09.2026, сделано
# 19.09.2026). Точки отказа — статусы QUALIFICATION_ERROR/SUBMISSION_ERROR/
# OUTCOME_UNKNOWN и mapping.ready is False; RETRY_PENDING сюда не входит,
# потому что запрос ещё не остановился на ошибке, а ждёт повторной попытки.
# --------------------------------------------------------------------------- #
def _recording():
    sink = MemorySink()
    analytics.set_recorder(AnalyticsRecorder(sink))
    return sink


def test_qualification_mapping_failure_is_recorded():
    """`mapping.ready is False` — запрос в CRM не уходит, повод известен нам самим."""
    sink = _recording()
    try:
        client = FakeCrmClient()
        session = _session()
        # SUMM в контракте требует gt=0: отрицательная сумма валит pydantic
        # ещё до сборки запроса — ready становится False без единого вызова CRM.
        session.state["facts"]["request"]["amount"] = -100

        result = _workflow(client, []).advance(session, "")

        assert result.status == CrmDialogStatus.QUALIFICATION_ERROR
        assert client.qualification_requests == []
        (event,) = [e for e in sink.events if e.event == "crm_call_failed"]
        assert event.session_id == session.id
        assert event.payload == {
            "stage": "qualification",
            "reason": "mapping_rejected",
            "retryable": False,
        }
    finally:
        analytics.set_recorder(None)


def test_qualification_contract_mismatch_is_recorded():
    """Тот же класс сбоя, что три дня ронял квалификацию в дефекте widget_denis."""
    sink = _recording()
    try:
        client = FakeCrmClient()
        client.first_qualification = lambda payload: (_ for _ in ()).throw(
            CrmApiError(
                "Ответ CRM не соответствует контракту; поля: result.category",
                retryable=False,
                reason="contract_mismatch",
            )
        )
        session = _session()

        result = _workflow(client, []).advance(session, "")

        assert result.status == CrmDialogStatus.QUALIFICATION_ERROR
        (event,) = [e for e in sink.events if e.event == "crm_call_failed"]
        assert event.payload == {
            "stage": "qualification",
            "reason": "contract_mismatch",
            "retryable": False,
            "error_class": "CrmApiError",
        }
    finally:
        analytics.set_recorder(None)


def test_retryable_failure_waits_and_does_not_record_yet():
    """RETRY_PENDING — не отказ, а попытка в процессе; событие рано.

    Повтор идёт по таймеру или следующим ходом (`retry_pending`/`advance`), и
    если он тоже не удастся, СВОЙ crm_call_failed запишет уже он — раздельно,
    честно, без склейки в одну строку с этой попыткой (см. модуль-докстринг
    аналитики про «повтор по таймеру — отдельное событие»).
    """
    sink = _recording()
    try:
        client = FakeCrmClient()
        client.first_qualification = lambda payload: (_ for _ in ()).throw(
            CrmApiError("CRM HTTP 500", retryable=True, reason="http_error", http_status=500)
        )
        session = _session()

        result = _workflow(client, []).advance(session, "")

        assert result.status == CrmDialogStatus.QUALIFICATION_RETRY_PENDING
        assert not any(e.event == "crm_call_failed" for e in sink.events)
    finally:
        analytics.set_recorder(None)


def test_qualification_unexpected_exception_is_recorded():
    """Ненормализованный сбой (не CrmApiError) тоже должен стать видимым."""
    sink = _recording()
    try:
        client = FakeCrmClient()

        def boom(payload):
            raise KeyError("region_class")

        client.first_qualification = boom
        session = _session()

        result = _workflow(client, []).advance(session, "")

        assert result.status == CrmDialogStatus.QUALIFICATION_ERROR
        (event,) = [e for e in sink.events if e.event == "crm_call_failed"]
        assert event.payload == {
            "stage": "qualification",
            "reason": "http_error",
            "retryable": False,
            "error_class": "KeyError",
        }
    finally:
        analytics.set_recorder(None)


def test_successful_exchange_records_no_failure_event():
    sink = _recording()
    try:
        client = FakeCrmClient(need_site_consent=False)
        session = _session()

        result = _workflow(client, []).advance(session, "")

        assert result.status == CrmDialogStatus.SUBMITTED
        assert not any(e.event == "crm_call_failed" for e in sink.events)
    finally:
        analytics.set_recorder(None)


def test_failed_submission_claim_is_retried_before_any_lead_post():
    """База упала до POST: заявка ждёт безопасного retry, а не теряется."""
    client = FakeCrmClient(need_site_consent=False)
    active_gate = LeadSubmissionGate(
        mode=SubmissionMode.TEST,
        allowed_test_phones=frozenset({TEST_PHONE}),
    )
    claims = [
        LeadClaim(granted=False, error="database unavailable"),
        LeadClaim(granted=True),
    ]
    coordinator = LeadIntakeCoordinator(
        client,
        persist=lambda session: None,
        persist_submission=lambda session: None,
        claim_submission=lambda session: claims.pop(0),
        now=lambda: NOW,
        submission_gate=active_gate,
    )
    workflow = CrmDialogWorkflow(
        client,
        coordinator,
        active_gate,
        persist=lambda session: None,
        enabled=True,
    )
    session = _session()

    pending = workflow.advance(session, "")
    assert pending.status == CrmDialogStatus.SUBMISSION_RETRY_PENDING
    assert client.lead_requests == []

    submitted = workflow.retry_pending(session)

    assert submitted is not None
    assert submitted.status == CrmDialogStatus.SUBMITTED
    assert len(client.qualification_requests) == 1
    assert len(client.lead_requests) == 1


def test_safe_retry_preserves_chat_consent_answer(chat_consent_enabled):
    client = FakeCrmClient(need_site_consent=True)
    active_gate = LeadSubmissionGate(
        mode=SubmissionMode.TEST,
        allowed_test_phones=frozenset({TEST_PHONE}),
    )
    claims = [
        LeadClaim(granted=False, error="database unavailable"),
        LeadClaim(granted=True),
    ]
    coordinator = LeadIntakeCoordinator(
        client,
        persist=lambda session: None,
        persist_submission=lambda session: None,
        claim_submission=lambda session: claims.pop(0),
        now=lambda: NOW,
        submission_gate=active_gate,
    )
    workflow = CrmDialogWorkflow(
        client,
        coordinator,
        active_gate,
        persist=lambda session: None,
        enabled=True,
    )
    session = _session()

    assert workflow.advance(session, "").status == CrmDialogStatus.AWAITING_SITE_CONSENT
    pending = workflow.advance(session, "Да")
    submitted = workflow.retry_pending(session)

    assert pending.status == CrmDialogStatus.SUBMISSION_RETRY_PENDING
    assert submitted is not None and submitted.status == CrmDialogStatus.SUBMITTED
    assert client.lead_requests[0].agree_partners == 23000


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Да", True),
        ("Согласен, давайте", True),
        ("Нет", False),
        ("Не хочу", False),
        ("Мне надо подумать", None),
        ("", None),
    ],
)
def test_consent_parser_requires_explicit_answer(message: str, expected: bool | None):
    assert parse_site_consent_answer(message) is expected


def test_core_can_defer_legacy_delivery_to_crm_workflow(monkeypatch):
    state = init_state()
    state["facts"] = _facts()
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *args, **kwargs: {"fact_updates": [], "internal_summary": ""},
    )

    prepared = core.prepare_turn(
        state,
        "",
        submit_delivery=False,
    )

    assert prepared["plan"]["action"] == "route_found"
    assert prepared["delivery"] is None


def test_no_phone_means_no_submission_and_no_error_face():
    """Клиент не оставил номер — это не сбой, и говорить о сбое клиенту нельзя.

    Живой прогон 13.08.2026: после отказа дать телефон исчерпался лимит попыток,
    план стал route_found, workflow пошёл создавать лид, маппинг честно отказал
    (`lead_mapping_not_ready`) — и клиент услышал «приношу извинения, повторить
    отправку не получится». Похоже на нашу аварию, хотя это его решение.

    Лид без телефона в CRM не уходит и уйти не должен — это проверено
    маппингом. Здесь проверяется другое: диалог не превращается в извинение.
    """
    client = FakeCrmClient()
    session = _session()
    with session._lock:
        session.state["facts"]["client"]["phone"] = None
    # Боевой режим: тестовый гейт остановил бы отправку раньше и спрятал дефект.
    production = LeadSubmissionGate(mode=SubmissionMode.PRODUCTION)

    state = _workflow(client, [], gate=production).advance(session, "не дам номер")

    assert client.lead_requests == []
    assert state.status != CrmDialogStatus.SUBMISSION_ERROR
    assert session.state.get("dialog_phase") != "delivery_error"


@pytest.mark.parametrize(
    "message",
    [
        "Конечно",
        "Ок",
        "ага",
        "Угу",
        "Не откажусь",
        "+",
        "+1",
        "Ладно",
        "Можно",
        "Не против",
        "Окей",
    ],
)
def test_consent_parser_understands_live_agreement(message: str):
    """Живое согласие звучит не словом «да», а «конечно», «ок», «ага», «+».

    Аудит 19.08.2026: на вопрос о консультации клиент отвечал «Конечно» и
    «ок», парсер возвращал None, вопрос повторялся, а лид уходил уже с
    отказом. Слова «конечно» и «ага» routing/normalize.parse_bool понимал
    давно — не понимал только этот вопрос.
    """
    assert parse_site_consent_answer(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "Конечно нет",
        "Ок, но нет",
        "Не интересно",
        "Отказываюсь",
    ],
)
def test_consent_parser_reads_refusal_before_agreement(message: str):
    """Отказ разбирается первым: «конечно нет» — это нет, а не «конечно».

    Именно поэтому сюда нельзя взять parse_bool целиком: он ищет согласие
    подстрокой. Расширение списка согласий не должно ослабить это правило —
    ложное «да» отдаёт контакты клиента партнёру без его согласия.
    """
    assert parse_site_consent_answer(message) is False


@pytest.mark.parametrize(
    "message",
    [
        "Мне надо подумать",
        "Хорошо, я подумаю",
        "Ладно, давайте потом",
        "Не знаю",
        "",
    ],
)
def test_consent_parser_keeps_hesitation_unanswered(message: str):
    """Отложенный ответ — не согласие, даже если начинается с «ладно»."""
    assert parse_site_consent_answer(message) is None


def test_consent_question_is_not_asked_forever(chat_consent_enabled):
    """У вопроса о согласии есть потолок попыток, как у любого поля анкеты.

    Аудит 19.08.2026: счётчика в ветке AWAITING_SITE_CONSENT не было вовсе, и
    на невнятный ответ бот повторял один и тот же вопрос бесконечно — лид не
    уходил, пока клиент не замолчит на пятнадцать минут. Молчание в ответ на
    трижды заданный вопрос — не согласие, поэтому лид уезжает с честным «нет»,
    но уезжает.
    """
    persisted: list[str] = []
    client = FakeCrmClient(need_site_consent=True)
    session = _session()
    workflow = _workflow(client, persisted)

    workflow.advance(session, "")
    statuses = [
        workflow.advance(session, "а это вообще к чему").status for _ in range(6)
    ]

    assert CrmDialogStatus.AWAITING_SITE_CONSENT not in statuses[3:]
    assert statuses[-1] == CrmDialogStatus.SUBMITTED
    assert len(client.lead_requests) == 1
    assert client.lead_requests[0].agree_partners == 23001


def _restarted_session(
    workflow_status: str,
    *,
    lead_status: str = "not_sent",
    lead_id: int | None = None,
) -> Session:
    """Сессия, поднятая из базы после смерти процесса посреди обмена с CRM."""
    session = _session()
    session.state["crm_workflow"]["status"] = workflow_status
    session.state["crm_lead"]["status"] = lead_status
    session.state["crm_lead"]["lead_id"] = lead_id
    return session


def test_restart_during_qualification_resumes_and_still_creates_the_lead():
    """Иначе сессия стоит в qualifying вечно: ни лида, ни ошибки, ни следа."""
    persisted: list[str] = []
    client = FakeCrmClient(need_site_consent=False)
    session = _restarted_session("qualifying")

    result = _workflow(client, persisted).advance(session, "Я на связи")

    assert result.status == CrmDialogStatus.SUBMITTED
    assert len(client.qualification_requests) == 1
    assert len(client.lead_requests) == 1


def test_restart_before_the_lead_left_still_sends_it_once():
    """crm_lead пишет in_flight до сети: not_sent значит, что POST не начинался."""
    persisted: list[str] = []
    client = FakeCrmClient(need_site_consent=False)
    session = _restarted_session("submitting", lead_status="not_sent")

    result = _workflow(client, persisted).advance(session, "Я на связи")

    assert result.status == CrmDialogStatus.SUBMITTED
    assert len(client.lead_requests) == 1


def test_restart_with_lead_in_flight_closes_the_dialog_without_retry():
    """Лид мог уйти: повтор запрещён, но клиент обязан получить ответ."""
    persisted: list[str] = []
    client = FakeCrmClient()
    session = _restarted_session("submitting", lead_status="in_flight")

    result = _workflow(client, persisted).advance(session, "Ну что там?")

    assert result.status == CrmDialogStatus.OUTCOME_UNKNOWN
    assert client.lead_requests == []
    assert session.state["crm_lead"]["status"] == "outcome_unknown"
    assert session.state["dialog_phase"] == "delivery_error"


def test_restart_after_the_lead_left_finishes_the_dialog_normally():
    """Лид уже в CRM — извиняться не за что, разговор надо закрыть как обычно."""
    persisted: list[str] = []
    client = FakeCrmClient()
    session = _restarted_session(
        "submitting",
        lead_status="submitted",
        lead_id=3_431_700,
    )

    result = _workflow(client, persisted).advance(session, "Ну что там?")

    assert result.status == CrmDialogStatus.SUBMITTED
    assert client.lead_requests == []
    assert session.state["delivery"]["status"] == "completed"


def test_startup_recovery_looks_at_the_dialog_state_not_only_at_the_lead():
    """crm_lead при старте разбирали, crm_workflow не читал никто."""
    import main

    persisted: list[str] = []
    client = FakeCrmClient()
    session = _restarted_session("qualifying")

    class _Store:
        def all_sessions(self):
            return [session]

    recovered = main._recover_interrupted_dialogs(
        _Store(),
        _workflow(client, persisted),
    )

    assert recovered == 1
    assert session.state["crm_workflow"]["status"] == "not_started"
    # Старт не ходит в сеть: он только возвращает автомату право на ход.
    assert client.qualification_requests == []
    assert client.lead_requests == []


@pytest.mark.parametrize("scenario", ["existing", "off_topic"])
def test_branch_never_reaches_crm(scenario):
    """Спека, компонент 2: ни в «уже оставлял заявку», ни в «не наш профиль»
    второй лид не создаётся ни при каком развитии разговора — даже с
    телефоном и найденным маршрутом. В CRM не уходит даже квалификация."""
    persisted: list[str] = []
    client = FakeCrmClient(need_site_consent=False)
    session = _session()
    session.state["facts"]["client"]["display_name"] = "Тест"
    session.state["entry"] = {
        "scenario": scenario,
        "intent": scenario,
        "source": "model",
    }

    _workflow(client, persisted).advance(session, "")

    assert client.qualification_requests == []
    assert client.lead_requests == []
    assert (session.state.get("crm_lead") or {}).get("lead_id") is None


def test_return_to_the_questionnaire_lifts_the_lead_block():
    persisted: list[str] = []
    client = FakeCrmClient(need_site_consent=False)
    session = _session()
    session.state["facts"]["client"]["display_name"] = "Тест"
    session.state["entry"] = {
        "scenario": "questionnaire",
        "intent": "money",
        "source": "model",
    }

    result = _workflow(client, persisted).advance(session, "")

    assert result.status == CrmDialogStatus.SUBMITTED
    assert len(client.lead_requests) == 1
