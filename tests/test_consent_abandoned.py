"""Клиент замолчал на вопросе о согласии — лид всё равно должен уехать.

Аудит 19.08.2026. Самый дорогой клиент теряется целиком: анкета пройдена,
телефон оставлен, категория подтверждена — CRM ответила «нужно согласие на
консультацию», бот спросил, а человек закрыл вкладку.

Дальше не срабатывает ни один путь:

* полный путь ждёт явного да/нет и не имеет потолка ожидания вовсе;
* путь брошенной анкеты отказывается работать именно потому, что анкета
  пройдена: `partial_is_due` видит plan.action == route_found и выходит.

Получается наоборот: клиенту, который бросил анкету на середине, мы телефон
отправляем, а тому, кто прошёл её до конца, — нет.

Отправка идёт под тем же флагом, что и обычные брошенные лиды
(MBK_CRM_ABANDONED_LEADS_ENABLED): новых обещаний заказчику мы не даём.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from delivery.crm_contracts import SitePartnerConsent
from delivery.crm_mapping import map_lead_intake
from delivery.crm_submission_gate import AllowAllSubmissionGate
from lead_intake_coordinator import LeadIntakeCoordinator
from session_store import Session
from state import init_state
from usage_tracker import SessionUsage


NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


class _Client:
    """Транспорт, который считает попытки и всегда отвечает успехом."""

    lead_intake_enabled = True

    def __init__(self) -> None:
        self.requests = []

    def lead_intake(self, request):
        self.requests.append(request)

        class _Result:
            lead_id = 4242

        class _Response:
            result = _Result()

        return _Response()


def _session_waiting_for_consent() -> Session:
    state = init_state()
    state["facts"] = {
        "client": {"name": "Сергей Иванов", "phone": "+79990000000"},
        "request": {"amount": 900_000},
    }
    # Анкета пройдена, маршрут найден — именно это и блокировало отправку.
    state["plan"] = {"action": "route_found"}
    state["crm_workflow"] = {
        **state["crm_workflow"],
        "status": "awaiting_site_consent",
        "need_site_consent": True,
        "site_consent_answer": None,
    }
    session = Session(
        id="cons01",
        state=state,
        anketa=None,
        usage=SessionUsage(session_id="cons01"),
    )
    session.last_active = NOW - timedelta(minutes=30)
    return session


def _coordinator(client: _Client) -> LeadIntakeCoordinator:
    return LeadIntakeCoordinator(
        client,
        persist=lambda session: None,
        submission_gate=AllowAllSubmissionGate(),
        now=lambda: NOW,
    )


def test_silence_at_the_consent_question_still_delivers_the_lead():
    client = _Client()
    coordinator = _coordinator(client)
    session = _session_waiting_for_consent()

    outcome = coordinator.submit_partial_if_due(session, now=NOW)

    assert client.requests, (
        "самый дорогой клиент — с телефоном и пройденной анкетой — "
        "не уехал в CRM вовсе"
    )
    assert outcome.attempted is True


def test_unanswered_consent_is_recorded_as_no_not_as_yes():
    """Молчание — не согласие. В CRM уходит честное «нет»."""
    client = _Client()
    _coordinator(client).submit_partial_if_due(_session_waiting_for_consent(), now=NOW)

    payload = client.requests[0].model_dump(by_alias=True, exclude_none=True)

    assert payload["UF_SITE_AGREE_PARTNERS"] == SitePartnerConsent.NO


def test_a_client_who_answered_is_not_swept():
    """Ответил — обычный путь отработает сам, sweeper вмешиваться не должен."""
    client = _Client()
    session = _session_waiting_for_consent()
    session.state["crm_workflow"]["status"] = "submitted"

    _coordinator(client).submit_partial_if_due(session, now=NOW)

    assert not client.requests


def test_mapping_can_express_shown_but_unanswered():
    """Контракт должен уметь сказать «спросили, ответа не получили».

    Раньше такой ветки не существовало: маппинг возвращал issue, и даже
    принудительная отправка была невозможна.
    """
    mapping = map_lead_intake(
        {"client": {"name": "Сергей", "phone": "+79990000000"}},
        form_filled_out=True,
        site_consent_shown=True,
        site_consent_answer=None,
        consent_abandoned=True,
    )

    assert mapping.ready, [i.message for i in mapping.issues]
    assert mapping.request is not None
    payload = mapping.request.model_dump(by_alias=True, exclude_none=True)
    assert payload["UF_SITE_AGREE_PARTNERS"] == SitePartnerConsent.NO
