"""Транспортный адаптер боевого classification API CRM МБК."""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request

import transport
from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .crm_contracts import (
    FirstQualificationRequest,
    FirstQualificationResponse,
    LeadIntakeRequest,
    LeadIntakeResponse,
    RealtyQualificationRequest,
    RealtyQualificationResponse,
)


logger = logging.getLogger("mbk.delivery.crm")
ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)

DEFAULT_BASE_URL = "https://crm.mbk.ru/lara/api/classification"


class CrmApiError(RuntimeError):
    """Нормализованная ошибка транспорта или внешнего контракта CRM.

    `reason` и `http_status` существуют ради аналитики `crm_call_failed`:
    вызывающий код (crm_dialog_workflow, lead_intake_coordinator) видит только
    уже нормализованное исключение и не может восстановить, какая именно
    ветка транспорта его сложила — HTTP-код, обрыв соединения или ответ, не
    прошедший pydantic. Классифицируем один раз, здесь, где это ещё известно
    точно.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        reason: str = "http_error",
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.reason = reason
        self.http_status = http_status


class LeadIntakeDisabledError(RuntimeError):
    """Защита от случайного создания лида в боевой CRM."""


class CrmClassificationClient:
    """Клиент трех подтвержденных операций classification API.

    Квалификационные запросы можно безопасно повторить при временной ошибке.
    Создание лида никогда не повторяется автоматически: backend не подтвердил
    поддержку idempotency key.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: float | None = None,
        classification_attempts: int | None = None,
        lead_intake_enabled: bool | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_url = (
            base_url
            or os.getenv("MBK_CRM_BASE_URL")
            or DEFAULT_BASE_URL
        ).rstrip("/")
        self._timeout = timeout or float(os.getenv("MBK_CRM_TIMEOUT", "20"))
        self._classification_attempts = max(
            1,
            classification_attempts
            or int(os.getenv("MBK_CRM_CLASSIFICATION_MAX_ATTEMPTS", "2")),
        )
        self._lead_intake_enabled = (
            lead_intake_enabled
            if lead_intake_enabled is not None
            else _env_flag("MBK_CRM_LEAD_INTAKE_ENABLED")
        )
        self._sleep = sleep

    @property
    def lead_intake_enabled(self) -> bool:
        """Позволяет scheduler не запускаться при выключенном боевом transport."""
        return self._lead_intake_enabled

    def first_qualification(
        self,
        payload: FirstQualificationRequest,
    ) -> FirstQualificationResponse:
        return self._classification_post(
            "first-qualification",
            payload,
            FirstQualificationResponse,
        )

    def realty_qualification(
        self,
        payload: RealtyQualificationRequest,
    ) -> RealtyQualificationResponse:
        return self._classification_post(
            "realty-qualification",
            payload,
            RealtyQualificationResponse,
        )

    def lead_intake(self, payload: LeadIntakeRequest) -> LeadIntakeResponse:
        if not self._lead_intake_enabled:
            raise LeadIntakeDisabledError(
                "Создание лидов отключено. Для осознанного боевого вызова установите "
                "MBK_CRM_LEAD_INTAKE_ENABLED=1."
            )
        # Не ретраим POST создания лида: после timeout результат операции неизвестен,
        # а повтор может создать дубль.
        response = self._post(
            "lead-intake",
            payload.as_api_dict(exclude_none=True),
        )
        _raise_if_explicit_failure(response, "lead-intake")
        validated = _validate_response(response, LeadIntakeResponse)
        return validated

    def _classification_post(
        self,
        endpoint: str,
        payload: BaseModel,
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        for attempt in range(1, self._classification_attempts + 1):
            try:
                response = self._post(
                    endpoint,
                    payload.model_dump(mode="json", by_alias=True),
                )
                _raise_if_explicit_failure(response, endpoint)
                validated = _validate_response(response, response_model)
                return validated
            except CrmApiError as exc:
                logger.warning(
                    "CRM classification %s: попытка %s/%s завершилась ошибкой: %s",
                    endpoint,
                    attempt,
                    self._classification_attempts,
                    exc,
                )
                if not exc.retryable or attempt >= self._classification_attempts:
                    raise
                self._sleep(0.25 * attempt)
        raise AssertionError("цикл попыток должен вернуть результат или ошибку")

    def _post(self, endpoint: str, payload: dict[str, object]) -> object:
        # Значения анкеты не логируются: они могут содержать ПД и финансовые данные.
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base_url}/{endpoint}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with transport.direct_urlopen(request, self._timeout) as response:
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            # Тело ошибки нередко эхом содержит отправленную анкету. Для
            # диагностики и retry-policy достаточно HTTP-кода; ПД не должны
            # попадать ни в exception, ни затем в постоянный incident log.
            raise CrmApiError(
                f"CRM HTTP {exc.code}",
                retryable=exc.code >= 500 or exc.code == 429,
                reason="http_error",
                http_status=exc.code,
            ) from exc
        except transport.TRANSPORT_ERRORS as exc:
            # Ретрай квалификации висит на CrmApiError, поэтому всё, что не
            # нормализовано здесь, доходит до вызывающего голым и сажает сессию
            # в терминальный QUALIFICATION_ERROR без единой повторной попытки.
            # Такую сессию повтором сообщения уже не вылечить — это потерянный
            # лид. Прежний список (TimeoutError, URLError) пропускал именно
            # обрыв соединения: RemoteDisconnected и ssl.SSLError летят из
            # getresponse(), IncompleteRead — из read(). Правило см. в
            # transport.TRANSPORT_ERRORS.
            #
            # reason="timeout": ответа не было вообще, а не «CRM ответила
            # ошибкой» — для аналитики это разные причины отказа, хотя обе
            # ретраятся одинаково.
            raise CrmApiError(
                str(exc) or type(exc).__name__,
                retryable=True,
                reason="timeout",
            ) from exc
        except json.JSONDecodeError as exc:
            # Тело не парсится — то же семейство проблем, что и pydantic
            # ValidationError ниже: ответ CRM не прошёл наш контракт, просто
            # сломался на шаг раньше.
            raise CrmApiError(
                "CRM вернула невалидный JSON",
                retryable=False,
                reason="contract_mismatch",
            ) from exc


def _validate_response(
    response: object,
    response_model: type[ResponseModelT],
) -> ResponseModelT:
    try:
        return response_model.model_validate(response)
    except ValidationError as exc:
        locations = sorted(
            {
                ".".join(str(part) for part in error.get("loc", ()))
                for error in exc.errors(include_input=False)
            }
        )
        safe_locations = ", ".join(locations[:10]) or "unknown"
        raise CrmApiError(
            "Ответ CRM не соответствует контракту; поля: " + safe_locations,
            retryable=False,
            reason="contract_mismatch",
        ) from exc


def _raise_if_explicit_failure(response: object, operation: str) -> None:
    """Нормализует боевой HTTP 200 + ``success=false`` до проверки happy-path схемы."""
    if not isinstance(response, dict) or response.get("success") is not False:
        return
    data = response.get("data")
    # Названия контрактных полей полезны и сами по себе не являются ПД. Их
    # значения и backend message не переносим: CRM может вернуть там исходный
    # телефон, ФИО или адрес.
    fields = []
    if isinstance(data, dict):
        fields = [
            str(key)
            for key in data
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", str(key))
        ]
    suffix = f"; поля: {', '.join(fields[:20])}" if fields else ""
    # reason="http_error": формально статус 200, но CRM явно отказала запросу —
    # тот же смысловой бакет, что и обычная HTTP-ошибка, отдельного значения
    # под «отказ при 200» аналитика не заводит.
    raise CrmApiError(
        f"CRM {operation} вернула success=false{suffix}",
        retryable=False,
        reason="http_error",
    )


def _env_flag(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes"}
