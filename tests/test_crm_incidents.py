"""Внутренний журнал сбоев передачи в CRM.

Уведомлений у нас нет: канала со стороны МБК пока не назвали, и 12.08.2026
заказчик решил — «пока пиши внутренний лог». Смысл журнала в том, что он
переживает перезапуск и его можно открыть через неделю: `logger.warning`
растворяется в потоке `docker logs`, а вопрос «почему лид не доехал» приходит
не в момент сбоя.

Персональных данных в журнале быть не должно: в тексте ошибки CRM возвращает
кусок того, что мы ей послали, а туда входит телефон.
"""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from crm_dialog_workflow import CrmDialogStatus
from delivery.crm import CrmApiError
from delivery.crm_submission_gate import LeadSubmissionGate, SubmissionMode
from lead_intake_coordinator import LeadIntakeCoordinator

from tests.test_crm_dialog_workflow import (
    NOW,
    TEST_PHONE,
    FakeCrmClient,
    _session,
    _workflow,
)


class ExplodingCrmClient(FakeCrmClient):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    def first_qualification(self, payload):  # noqa: ANN001 — сигнатура фейка
        raise self.error


@pytest.fixture
def incident_log(tmp_path, monkeypatch):
    """Журнал пишется в logs/, поэтому рабочую папку уводим во временную."""
    monkeypatch.chdir(tmp_path)
    return tmp_path / "logs" / "crm_incidents.jsonl"


def _fail_once(error: Exception) -> None:
    client = ExplodingCrmClient(error)
    _workflow(client, []).advance(_session(), "да")


def test_failed_qualification_leaves_a_trace_on_disk(incident_log):
    _fail_once(CrmApiError("HTTP 502 от classification/first-qualification", retryable=False))

    assert incident_log.exists(), (
        "Сбой передачи виден только в потоке docker logs. Через неделю "
        "ответить, почему лид не доехал, будет нечем."
    )
    record = json.loads(incident_log.read_text(encoding="utf-8").strip())
    assert record["session_id"] == "crm-dialog-test"
    assert record["status"] == "qualification_error"
    assert "502" in record["detail"]
    assert record["occurred_at"]


def test_every_incident_is_a_separate_line(incident_log):
    _fail_once(CrmApiError("первый сбой", retryable=False))
    _fail_once(CrmApiError("второй сбой", retryable=False))

    lines = incident_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_the_phone_from_the_crm_answer_does_not_reach_the_journal(incident_log):
    """CRM возвращает в тексте ошибки кусок присланного payload."""
    _fail_once(CrmApiError('422: {"phone":"79991234567","name":"Тестов"}', retryable=False))

    written = incident_log.read_text(encoding="utf-8")

    assert "79991234567" not in written
    assert "Тестов" not in written
    assert "422" in written


def test_crm_error_address_and_email_do_not_reach_the_journal(incident_log):
    _fail_once(
        CrmApiError(
            '422: {"full_name":"Иванов Иван Иванович",'
            '"address":"Москва, Тверская 1","email":"ivan@example.test"}',
            retryable=False,
        )
    )

    written = incident_log.read_text(encoding="utf-8")

    assert "Иванов" not in written
    assert "Тверская" not in written
    assert "ivan@example.test" not in written
    assert "422" in written


def test_unquoted_mult_word_pii_is_fully_removed(incident_log):
    _fail_once(
        CrmApiError(
            "422; full_name=Иванов Иван Иванович; "
            "address=Москва, Тверская 1",
            retryable=False,
        )
    )

    written = incident_log.read_text(encoding="utf-8")

    assert "Иванов" not in written
    assert "Иванович" not in written
    assert "Москва" not in written
    assert "Тверская" not in written


def test_a_lead_without_a_region_is_recorded_but_not_stopped(incident_log):
    """Регион обязателен во всех вертикалях — подтвердил заказчик 12.08.2026.

    Останавливать из-за него отправку нельзя: решение о матчинге принимает CRM,
    и лид без региона всё равно лучше, чем потерянный клиент. Но без записи это
    выглядит как «клиент не подошёл», а не как потерянное поле — категория
    приходит пустой.
    """
    client = FakeCrmClient()
    session = _session()
    for key in ("region", "city", "registration_address", "location"):
        session.state["facts"]["client"].pop(key, None)

    _workflow(client, []).advance(session, "да")

    assert client.qualification_requests, "Отправку останавливать нельзя"
    statuses = [
        json.loads(line)["status"]
        for line in incident_log.read_text(encoding="utf-8").splitlines()
    ]
    assert "region_missing" in statuses


def test_a_broken_journal_never_breaks_the_dialog(incident_log, monkeypatch):
    """Диагностика не имеет права стоить клиенту разговора."""
    def explode(*args, **kwargs):
        raise OSError("диск переполнен")

    monkeypatch.setattr("logger.open", explode, raising=False)

    _fail_once(CrmApiError("сбой при неработающем журнале", retryable=False))


def test_full_form_after_partial_lead_is_recorded(incident_log):
    """Клиент вернулся после отправки брошенного лида и дошёл до конца.

    Sweeper уже потратил единственную разрешённую отправку сессии на анкету с
    одним телефоном (filled_out=0). Повторить POST нельзя: у CRM нет
    idempotency key, дедуп в окне 15 минут вернёт старый leadID и выбросит наше
    тело, а после окна создаст дубль. Значит полные сведения остаются только у
    нас, и единственный способ их не потерять — запись в журнал: по контракту
    их доносит до лида колл-центр, но сначала должен о них узнать.
    """
    client = FakeCrmClient()
    session = _session()
    coordinator = LeadIntakeCoordinator(
        client,
        persist=lambda _: None,
        now=lambda: NOW,
        submission_gate=LeadSubmissionGate(
            mode=SubmissionMode.TEST,
            allowed_test_phones=frozenset({TEST_PHONE}),
        ),
    )

    # Клиент бросил анкету на середине: план ещё не дошёл до маршрута.
    session.state["plan"]["action"] = "ask"
    partial = coordinator.submit_partial_if_due(
        session,
        now=NOW + timedelta(minutes=20),
    )
    assert partial.attempted
    assert client.lead_requests[0].form_filled_out == 0

    # Клиент вернулся и закрыл анкету целиком.
    session.state["plan"]["action"] = "route_found"
    coordinator.note_user_activity(session)
    result = _workflow(client, []).advance(session, "да")

    assert len(client.lead_requests) == 1, (
        "Повторная отправка того же лида запрещена контрактом CRM"
    )
    assert result.status == CrmDialogStatus.SUBMITTED
    assert session.state["delivery"]["status"] == "completed", (
        "Клиент не виноват в нашей проблеме — извиняться перед ним не за что"
    )

    written = (
        incident_log.read_text(encoding="utf-8") if incident_log.exists() else ""
    )
    statuses = [json.loads(line)["status"] for line in written.splitlines()]
    assert "full_form_not_delivered" in statuses, (
        "Полная анкета никуда не уехала и нигде не отмечена: диалог показал "
        "счастливый финал, а в CRM остался лид с одним телефоном."
    )
    assert TEST_PHONE not in written
