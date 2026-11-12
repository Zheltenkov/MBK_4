"""Обрыв соединения к CRM и DaData обязан быть обычным сбоем транспорта.

Оба адаптера перечисляли знакомые классы: `HTTPError`, `TimeoutError`,
`URLError`. Между тем `urllib` оборачивает в `URLError` только отправку запроса
(`AbstractHTTPHandler.do_open`), а `getresponse()` и `read()` бросают то, что
пришло с сокета, как есть: `RemoteDisconnected`, `ssl.SSLError`,
`IncompleteRead`. Ни один из трёх в перечисление не попадал.

Чем это стоило клиенту:
* DaData — исключение уходило мимо `resolve` и `enrich_locations` и роняло ход
  целиком: клиент видел общее «что-то пошло не так»;
* CRM first-qualification — широкий `except Exception` наверху ловил обрыв, но
  ретрая уже не было (он висит на `CrmApiError`), и сессия садилась в
  терминальный `QUALIFICATION_ERROR`. Повтором сообщения такую сессию не
  вылечить — это потерянный лид.

Тот же дефект в llm_agent закрывали списком `OSError` (см.
test_transport_retry.py), и `IncompleteRead` остался снаружи и там: это
`HTTPException`, а не `OSError`. Поэтому правило здесь формулируется не
перечислением знакомых классов, а инвариантом «всё, что бросил транспорт».
"""
from __future__ import annotations

import http.client
import json
import ssl
from typing import Any

import pytest

import llm_agent
import delivery.crm as crm
from config import AppConfig
from delivery.crm_contracts import (
    AssetsAvailability,
    CarAvailability,
    CarCredit,
    CreditHistory,
    EmploymentType,
    FirstQualificationRequest,
    LeadIntakeRequest,
    OverdueDuration,
    RealtyType,
    SitePartnerConsent,
)
from geo.dadata import DadataClient


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


_QUALIFICATION_OK = {
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
        "need_site_consent": False,
        "thanks_url": "/spasibo/",
    },
    "success": True,
}

_DADATA_OK = {
    "suggestions": [
        {
            "value": "г Москва",
            "unrestricted_value": "г Москва",
            "data": {
                "country": "Россия",
                "country_iso_code": "RU",
                "region": "Москва",
                "region_with_type": "г Москва",
                "region_fias_id": "moscow-region",
                "region_iso_code": "RU-MOW",
                "city": "Москва",
                "city_with_type": "г Москва",
                "city_fias_id": "moscow-city",
                "fias_id": "moscow-city",
            },
        }
    ]
}


class _Response:
    """Ответ, который умеет оборваться на чтении тела."""

    def __init__(self, payload: dict[str, Any] | None, *, broken: bool = False) -> None:
        self._payload = payload
        self._broken = broken

    def read(self) -> bytes:
        if self._broken:
            raise http.client.IncompleteRead(b"{\"succ", 128)
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


# Три способа, которыми обрыв соединения доходит до нас в бою. Проверяем
# правилом, а не по одному представителю: именно перечисление знакомых классов
# и оставляло дыру.
_BREAKAGES = (
    pytest.param(
        lambda: http.client.RemoteDisconnected(
            "Remote end closed connection without response"
        ),
        id="remote-disconnected",
    ),
    pytest.param(lambda: ssl.SSLError("EOF occurred in violation of protocol"), id="ssl"),
    pytest.param(lambda: http.client.IncompleteRead(b"{", 128), id="incomplete-read"),
)


# --------------------------------------------------------------------------- #
# CRM
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("make_error", _BREAKAGES)
def test_crm_qualification_retries_dropped_connection(monkeypatch, make_error):
    """Один оборванный коннект не должен сажать сессию в терминальную ошибку.

    Ретрай в `_classification_post` висит на `CrmApiError`; обрыв проходил мимо
    и попадал в `except Exception` вызывающего — сразу QUALIFICATION_ERROR.
    """
    client = crm.CrmClassificationClient(
        base_url="https://crm.example/classification",
        classification_attempts=2,
        sleep=lambda _: None,
    )
    calls = {"n": 0}

    def flaky(request, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise make_error()
        return _Response(_QUALIFICATION_OK)

    monkeypatch.setattr(crm.transport, "direct_urlopen", flaky)

    response = client.first_qualification(_qualification_request())

    assert calls["n"] == 2, "обрыв обязан стоить ровно одной повторной попытки"
    assert response.result.category == 4700


def test_crm_reports_dropped_connection_as_retryable_error(monkeypatch):
    """Исчерпав попытки, обрыв обязан выглядеть как временный сбой транспорта."""
    client = crm.CrmClassificationClient(
        base_url="https://crm.example/classification",
        classification_attempts=1,
    )

    def broken(request, timeout):
        raise http.client.RemoteDisconnected("closed")

    monkeypatch.setattr(crm.transport, "direct_urlopen", broken)

    with pytest.raises(crm.CrmApiError) as exc_info:
        client.first_qualification(_qualification_request())

    assert exc_info.value.retryable is True


def test_crm_body_torn_mid_read_is_a_transport_error(monkeypatch):
    """Тело ответа оборвалось на середине — исход запроса неизвестен, но это транспорт."""
    client = crm.CrmClassificationClient(
        base_url="https://crm.example/classification",
        classification_attempts=1,
    )

    monkeypatch.setattr(
        crm.transport,
        "direct_urlopen",
        lambda request, timeout: _Response(None, broken=True),
    )

    with pytest.raises(crm.CrmApiError) as exc_info:
        client.first_qualification(_qualification_request())

    assert exc_info.value.retryable is True


def test_lead_intake_stays_at_most_once_on_dropped_connection(monkeypatch):
    """Создание лида по-прежнему уходит ровно один раз.

    Нормализация обрыва не смеет превратиться в повтор POST: idempotency key
    backend не подтвердил, и второй запрос — это дубль лида.
    """
    client = crm.CrmClassificationClient(
        base_url="https://crm.example/classification",
        lead_intake_enabled=True,
    )
    calls = {"n": 0}

    def broken(request, timeout):
        calls["n"] += 1
        raise http.client.RemoteDisconnected("closed")

    monkeypatch.setattr(crm.transport, "direct_urlopen", broken)

    with pytest.raises(crm.CrmApiError):
        client.lead_intake(_lead_request())

    assert calls["n"] == 1, "лид не ретраится: исход первого запроса неизвестен"


# --------------------------------------------------------------------------- #
# DaData
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("make_error", _BREAKAGES)
def test_dadata_retries_dropped_connection(monkeypatch, make_error):
    calls = {"n": 0}

    def flaky(request, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise make_error()
        return _Response(_DADATA_OK)

    monkeypatch.setattr("geo.dadata.transport.direct_urlopen", flaky)
    monkeypatch.setattr("geo.dadata.time.sleep", lambda _: None)
    client = DadataClient(token="test-token", max_attempts=2)

    resolution = client.resolve("Москва")

    assert calls["n"] == 2
    assert resolution.status == "resolved"


@pytest.mark.parametrize("make_error", _BREAKAGES)
def test_dadata_unavailability_does_not_kill_the_turn(monkeypatch, make_error):
    """Клиент не должен видеть аварию из-за недоступной подсказки адреса.

    `resolve` ловит только `DadataRequestError`; всё остальное улетало наружу
    через `enrich_locations` и заканчивалось общим сообщением об ошибке.
    """

    def broken(request, timeout):
        raise make_error()

    monkeypatch.setattr("geo.dadata.transport.direct_urlopen", broken)
    monkeypatch.setattr("geo.dadata.time.sleep", lambda _: None)
    client = DadataClient(token="test-token", max_attempts=1)

    resolution = client.resolve("Химки")

    assert resolution.status == "unavailable"


def test_dadata_body_torn_mid_read_is_not_invalid_json(monkeypatch):
    """Оборванное тело — сбой связи, а не «DaData вернула невалидный JSON».

    Разница не косметическая: невалидный JSON не ретраится, обрыв — обязан.
    """
    calls = {"n": 0}

    def flaky(request, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Response(None, broken=True)
        return _Response(_DADATA_OK)

    monkeypatch.setattr("geo.dadata.transport.direct_urlopen", flaky)
    monkeypatch.setattr("geo.dadata.time.sleep", lambda _: None)
    client = DadataClient(token="test-token", max_attempts=2)

    resolution = client.resolve("Москва")

    assert calls["n"] == 2
    assert resolution.status == "resolved"


# --------------------------------------------------------------------------- #
# llm_agent — та же дыра, закрытая наполовину
# --------------------------------------------------------------------------- #


def test_llm_body_torn_mid_read_is_retried(monkeypatch):
    """`IncompleteRead` — `HTTPException`, а не `OSError`, и список её не ловил."""
    config = AppConfig(openrouter_api_key="sk-or-test")
    calls = {"n": 0}

    def flaky(cfg, request):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Response(None, broken=True)
        return _Response({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(llm_agent, "_urlopen", flaky)
    monkeypatch.setattr(llm_agent.time, "sleep", lambda _: None)

    data = llm_agent._post(config, {"model": "test", "messages": []})

    assert calls["n"] == 2
    assert data["choices"][0]["message"]["content"] == "ok"
