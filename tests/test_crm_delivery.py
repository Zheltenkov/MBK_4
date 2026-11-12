"""Контракты и safety-политика боевого CRM API."""
from __future__ import annotations

import urllib.error
from email.message import Message

import pytest

import delivery.crm as crm
from delivery.crm_contracts import (
    AssetsAvailability,
    CarAvailability,
    CarCredit,
    CreditHistory,
    EmploymentType,
    FirstQualificationRequest,
    LeadIntakeRequest,
    OverdueDuration,
    RealtyOwner,
    RealtyType,
    OwnershipForm,
    SitePartnerConsent,
)


def _qualification_request() -> FirstQualificationRequest:
    return FirstQualificationRequest(
        SUMM=1_500_000,
        BIRTHDATE="01-01-1984",
        UF_CRM_1452153725="г Москва, ул Ленина 1",
        UF_CREDIT_HISTORY=CreditHistory.NO_OVERDUE,
        UF_DEBT_SUMM=900_000,
        UF_CRM_1460622474=EmploymentType.HIRED,
        UF_REALTY_CC_4_22=AssetsAvailability.YES,
        UF_CRM_1452154011=RealtyType.FLAT,
        CHAT_REALTY_REGION="Москва",
        CHAT_OWNER="CLIENT",
        UF_HAVE_AUTO=CarAvailability.YES,
        UF_CAR_PRODUCTION_YEAR_1=2018,
        UF_CREDIT_AUTO_1=CarCredit.NO,
    )


def _lead_request() -> LeadIntakeRequest:
    return LeadIntakeRequest(
        PHONE="79991234567",
        PHONE_DEAL="79991234567",
        LAST_NAME="Тестов",
        NAME="Тест",
        FULL_NAME="Тестов Тест Тестович",
        SUMM=1_500_000,
        CREDIT_PERIOD=36,
        UF_FROM_SITE_FILLED_OUT=1,
        UF_SITE_AGREE_PARTNERS=SitePartnerConsent.NOT_REQUIRED,
        UF_CREDIT_HISTORY=CreditHistory.NO_OVERDUE,
        UF_CRM_LOAN_OVERDUE_DURATION=OverdueDuration.NO_OVERDUE,
        UF_DEBT_SUMM=900_000,
        UF_CRM_1452154011=RealtyType.FLAT,
        CHAT_REALTY_REGION="Москва",
    )


def test_enum_values_match_backend_reference():
    assert RealtyType.FLAT == 190
    assert RealtyType.COMMERCIAL == 3075
    assert RealtyOwner.CLIENT_ONLY == 4673
    assert OwnershipForm.PRIVATE == 4671
    assert CarAvailability.YES == 4695


def test_qualification_contract_uses_bitrix_field_names():
    payload = _qualification_request().as_api_dict()

    assert payload["SUMM"] == 1_500_000
    assert payload["UF_CRM_1452154011"] == 190
    assert payload["UF_HAVE_AUTO"] == 4695
    assert payload["UF_CAR_PRODUCTION_YEAR_1"] == 2018
    assert payload["UF_CREDIT_AUTO_1"] == 4600
    assert payload["UF_DEBT_SUMM"] == "900000"
    assert payload["UF_CRM_LOAN_OVERDUE_DURATION"] is None


def test_lead_intake_requires_duplicated_phone_and_service_flags():
    request = LeadIntakeRequest(
        PHONE="79991234567",
        PHONE_DEAL="79991234567",
        UF_FROM_SITE_FILLED_OUT=0,
        RETURN_RESPONSE=True,
    )

    assert request.as_api_dict(exclude_none=True) == {
        "PHONE": "79991234567",
        "PHONE_DEAL": "79991234567",
        "UF_FROM_SITE_FILLED_OUT": 0,
        "RETURN_RESPONSE": True,
        # Умолчания рекламной атрибуции задал backend МБК: лид из чата
        # отличается от веб-формы каналом, а не источником.
        "UTMSOURCE": "crm",
        "ADV_CHANNEL": "bot",
    }


def test_phone_deal_must_equal_phone():
    with pytest.raises(ValueError, match="PHONE_DEAL должен совпадать с PHONE"):
        LeadIntakeRequest(
            PHONE="79991234567",
            PHONE_DEAL="79990000000",
            UF_FROM_SITE_FILLED_OUT=0,
            RETURN_RESPONSE=True,
        )


def test_first_qualification_parses_confirmed_response(monkeypatch):
    client = crm.CrmClassificationClient(
        base_url="https://crm.example/classification",
        classification_attempts=1,
    )
    monkeypatch.setattr(
        client,
        "_post",
        lambda endpoint, payload: {
            "result": {
                "matched": True,
                "priority": "2-2",
                "suitable_categories": [21300],
                "segment": "rf",
                "partner": "microservice_bankruptcy_region",
                "offer": "bfl",
                "suitable_offers": ["bfl"],
                "need_partner_consent": True,
                "vertical": "bfl",
                "category": 4700,
                "need_site_consent": True,
                "thanks_url": "/spasibo-zaim-zalog-pts/",
            },
            "success": True,
        },
    )

    response = client.first_qualification(_qualification_request())

    assert response.result.category == 4700
    assert response.result.need_site_consent is True


def test_realty_qualification_parses_actual_success_flag(monkeypatch):
    client = crm.CrmClassificationClient(
        base_url="https://crm.example/classification",
        classification_attempts=1,
    )
    monkeypatch.setattr(
        client,
        "_post",
        lambda endpoint, payload: {
            "result": {"is_first_category": True},
            "success": True,
        },
    )

    response = client.realty_qualification(
        crm.RealtyQualificationRequest(
            UF_CRM_1452154011=RealtyType.FLAT,
            CHAT_REALTY_REGION="Москва",
            CHAT_OWNER="CLIENT",
        )
    )

    assert response.success is True
    assert response.result.is_first_category is True


def test_classification_success_false_is_an_error(monkeypatch):
    client = crm.CrmClassificationClient(classification_attempts=1)
    monkeypatch.setattr(
        client,
        "_post",
        lambda endpoint, payload: {
            "result": {
                "matched": False,
                "priority": None,
                "suitable_categories": [],
                "segment": None,
                "partner": None,
                "offer": None,
                "suitable_offers": [],
                "need_partner_consent": False,
                "vertical": None,
                "category": None,
                "need_site_consent": False,
                "thanks_url": None,
            },
            "success": False,
        },
    )

    with pytest.raises(crm.CrmApiError, match="success=false") as exc_info:
        client.first_qualification(_qualification_request())

    assert exc_info.value.retryable is False


def test_classification_retries_only_retryable_failures(monkeypatch):
    client = crm.CrmClassificationClient(
        classification_attempts=2,
        sleep=lambda _: None,
    )
    calls = 0

    def fake_post(endpoint, payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise crm.CrmApiError("timeout", retryable=True)
        return {
            "result": {
                "matched": False,
                "priority": None,
                "suitable_categories": [],
                "segment": None,
                "partner": None,
                "offer": None,
                "suitable_offers": [],
                "need_partner_consent": False,
                "vertical": None,
                "category": None,
                "need_site_consent": False,
                "thanks_url": None,
            },
            "success": True,
        }

    monkeypatch.setattr(client, "_post", fake_post)

    response = client.first_qualification(_qualification_request())

    assert calls == 2
    assert response.result.category is None


def test_lead_intake_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MBK_CRM_LEAD_INTAKE_ENABLED", raising=False)
    client = crm.CrmClassificationClient()
    called = False

    def forbidden_post(endpoint, payload):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(client, "_post", forbidden_post)

    with pytest.raises(crm.LeadIntakeDisabledError):
        client.lead_intake(_lead_request())

    assert called is False


def test_abandoned_lead_omits_partner_consent_field():
    request = _lead_request().model_copy(
        update={
            "form_filled_out": 0,
            "agree_partners": None,
        }
    )

    payload = request.as_api_dict(exclude_none=True)

    assert payload["UF_FROM_SITE_FILLED_OUT"] == 0
    assert "UF_SITE_AGREE_PARTNERS" not in payload


def test_lead_intake_has_no_automatic_retry(monkeypatch):
    client = crm.CrmClassificationClient(lead_intake_enabled=True)
    calls = 0

    def timeout(endpoint, payload):
        nonlocal calls
        calls += 1
        raise crm.CrmApiError("timeout", retryable=True)

    monkeypatch.setattr(client, "_post", timeout)

    with pytest.raises(crm.CrmApiError):
        client.lead_intake(_lead_request())

    assert calls == 1


def test_lead_intake_accepts_optional_success_flag(monkeypatch):
    client = crm.CrmClassificationClient(lead_intake_enabled=True)
    monkeypatch.setattr(
        client,
        "_post",
        lambda endpoint, payload: {
            "result": {"leadID": 3_429_999},
            "success": True,
        },
    )

    response = client.lead_intake(_lead_request())

    assert response.success is True
    assert response.result.lead_id == 3_429_999


def test_lead_intake_success_false_is_not_accepted(monkeypatch):
    client = crm.CrmClassificationClient(lead_intake_enabled=True)
    monkeypatch.setattr(
        client,
        "_post",
        lambda endpoint, payload: {
            "result": {"leadID": 3_429_999},
            "success": False,
        },
    )

    with pytest.raises(crm.CrmApiError, match="success=false"):
        client.lead_intake(_lead_request())


def test_lead_intake_validation_error_preserves_backend_field_name(monkeypatch):
    client = crm.CrmClassificationClient(lead_intake_enabled=True)
    monkeypatch.setattr(
        client,
        "_post",
        lambda endpoint, payload: {
            "success": False,
            "message": "Validation errors",
            "data": {
                "PHONE_DEAL": ["The phone deal field is required."],
            },
        },
    )

    with pytest.raises(crm.CrmApiError, match="PHONE_DEAL") as exc_info:
        client.lead_intake(_lead_request())

    assert exc_info.value.retryable is False


def test_http_error_retryability_is_preserved(monkeypatch):
    client = crm.CrmClassificationClient(
        base_url="https://crm.example/classification",
        classification_attempts=1,
    )

    def fail_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "bad request",
            Message(),
            None,
        )

    monkeypatch.setattr(crm.transport, "direct_urlopen", fail_urlopen)

    with pytest.raises(crm.CrmApiError) as exc_info:
        client.first_qualification(_qualification_request())

    assert exc_info.value.retryable is False


# --------------------------------------------------------------------------- #
# `reason`/`http_status` на CrmApiError — источник правды для аналитики
# `crm_call_failed`. Классификация делается здесь, а не в вызывающем коде:
# только это место знает, какая именно ветка транспорта сложила исключение,
# а на уровне workflow видно только нормализованный CrmApiError.
# --------------------------------------------------------------------------- #


def test_http_error_carries_status_and_reason(monkeypatch):
    client = crm.CrmClassificationClient(
        base_url="https://crm.example/classification",
        classification_attempts=1,
    )

    def fail_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            502,
            "bad gateway",
            Message(),
            None,
        )

    monkeypatch.setattr(crm.transport, "direct_urlopen", fail_urlopen)

    with pytest.raises(crm.CrmApiError) as exc_info:
        client.first_qualification(_qualification_request())

    assert exc_info.value.reason == "http_error"
    assert exc_info.value.http_status == 502


def test_dropped_connection_reason_is_timeout(monkeypatch):
    """Обрыв соединения — тот же класс проблемы, что таймаут: ответа не было."""
    client = crm.CrmClassificationClient(
        base_url="https://crm.example/classification",
        classification_attempts=1,
    )

    def broken(request, timeout):
        raise ConnectionResetError("closed")

    monkeypatch.setattr(crm.transport, "direct_urlopen", broken)

    with pytest.raises(crm.CrmApiError) as exc_info:
        client.first_qualification(_qualification_request())

    assert exc_info.value.reason == "timeout"
    assert exc_info.value.http_status is None


def test_contract_violation_reason_is_contract_mismatch(monkeypatch):
    """Ответ дошёл, но не прошёл pydantic — та же ошибка, что уронила widget_denis."""
    client = crm.CrmClassificationClient(
        base_url="https://crm.example/classification",
        classification_attempts=1,
    )
    monkeypatch.setattr(
        client,
        "_post",
        lambda endpoint, payload: {"result": {}, "success": True},
    )

    with pytest.raises(crm.CrmApiError) as exc_info:
        client.first_qualification(_qualification_request())

    assert exc_info.value.reason == "contract_mismatch"
    assert exc_info.value.retryable is False
    assert exc_info.value.http_status is None


def test_explicit_failure_reason_is_http_error(monkeypatch):
    """success=false — HTTP 200, но CRM отказала: тот же бакет, что и HTTP-ошибка."""
    client = crm.CrmClassificationClient(classification_attempts=1)
    monkeypatch.setattr(
        client,
        "_post",
        lambda endpoint, payload: {"result": {}, "success": False},
    )

    with pytest.raises(crm.CrmApiError) as exc_info:
        client.first_qualification(_qualification_request())

    assert exc_info.value.reason == "http_error"
    assert exc_info.value.http_status is None
