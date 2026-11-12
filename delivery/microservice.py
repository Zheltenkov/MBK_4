"""Обращение в маршрутизирующий микросервис.

Один запрос за диалог — когда движок уже подтвердил, что клиент полностью проходит
маршрут. Сервис отвечает либо персонализированной ссылкой, либо отказом; своей логики
подбора здесь нет и быть не должно.

Этот legacy-контур пока обслуживает локальный ``mbk.lead.v1``/mock UI. Полученный
23.07.2026 контракт CRM вынесен отдельно в ``delivery.crm`` и
``delivery.crm_contracts``: его qualification и необратимый lead-intake имеют разные
retry/safety-политики и не должны смешиваться с выдачей локального JSON.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import transport
from dataclasses import dataclass, field
from typing import Any, Literal

from routing import RouteStatus

from .contracts import LeadRoutingRequest


logger = logging.getLogger("mbk.delivery")

Status = Literal["completed", "link", "no_fit", "error"]

REQUEST_TIMEOUT = int(os.getenv("MBK_ROUTER_TIMEOUT", "20"))
MAX_ATTEMPTS = max(1, int(os.getenv("MBK_ROUTER_MAX_ATTEMPTS", "2")))

@dataclass
class DeliveryResult:
    status: Status
    url: str | None = None
    message: str | None = None
    request: dict[str, Any] = field(default_factory=dict)
    response: Any = None
    mocked: bool = False
    attempts: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "url": self.url,
            "message": self.message,
            "mocked": self.mocked,
            "attempts": self.attempts,
            # В mock-режиме фронтенд показывает этот полный JSON вместо ссылки.
            "request": self.request,
        }


class DeliveryRequestError(RuntimeError):
    """Ошибка транспорта с явным признаком, безопасно ли повторить запрос."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


def router_url() -> str | None:
    return (os.getenv("MBK_ROUTER_URL") or "").strip() or None


def is_mocked() -> bool:
    """Мок включается только явно — отсутствие URL не должно выглядеть как успех."""
    return (os.getenv("MBK_ROUTER_MOCK") or "").strip() in ("1", "true", "True", "yes")


def validate_config() -> None:
    """Fail closed: runtime обязан выбрать реальный endpoint или явный mock."""
    if not is_mocked() and router_url() is None:
        raise RuntimeError(
            "MBK_ROUTER_URL не задан. Для локальной разработки явно установите "
            "MBK_ROUTER_MOCK=1."
        )


def submit(
    facts: dict[str, Any],
    matched: RouteStatus,
    *,
    idempotency_key: str | None = None,
) -> DeliveryResult:
    payload = _build_request(facts, matched)
    request_key = f"{idempotency_key}:{matched.id}" if idempotency_key else None

    if is_mocked():
        logger.info(
            "Мок-доставка: опрос завершён, маршрут %s (%s)",
            matched.id,
            matched.priority,
        )
        return DeliveryResult(
            status="completed",
            message="Опрос завершён",
            request=payload,
            mocked=True,
            attempts=0,
        )

    url = router_url()
    if not url:
        return DeliveryResult(
            status="error",
            message="маршрутизатор не настроен",
            request=payload,
            attempts=0,
        )

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = _post(url, payload, idempotency_key=request_key)
            return _parse_response(response, payload, attempts=attempt)
        except DeliveryRequestError as exc:
            logger.warning("Микросервис недоступен, попытка %s/%s: %s", attempt, MAX_ATTEMPTS, exc)
            if not exc.retryable or attempt >= MAX_ATTEMPTS:
                return DeliveryResult(
                    status="error", message=str(exc), request=payload, attempts=attempt
                )
            time.sleep(0.25 * attempt)
        except Exception as exc:  # noqa: BLE001 — внешний адаптер не должен ронять диалог
            logger.exception("Неожиданная ошибка адаптера маршрутизатора")
            return DeliveryResult(
                status="error", message=str(exc), request=payload, attempts=attempt
            )

    return DeliveryResult(status="error", message="неизвестная ошибка", request=payload)


def _build_request(facts: dict[str, Any], matched: RouteStatus) -> dict[str, Any]:
    """Стабильный временный контракт: все потенциальные ответы всегда присутствуют."""
    return LeadRoutingRequest.from_facts(facts, matched).as_json_dict()


def _parse_response(
    response: Any, payload: dict[str, Any], *, attempts: int = 1
) -> DeliveryResult:
    """Ответ v1 бинарный: либо ссылка, либо «решения нет»."""
    if isinstance(response, dict):
        url = response.get("url") or response.get("link") or response.get("landing")
        if url:
            normalized_url = str(url).strip()
            if not _is_allowed_url(normalized_url):
                return DeliveryResult(
                    status="error",
                    message="маршрутизатор вернул недопустимую ссылку",
                    request=payload,
                    response=response,
                    attempts=attempts,
                )
            return DeliveryResult(
                status="link",
                url=normalized_url,
                request=payload,
                response=response,
                attempts=attempts,
            )
        return DeliveryResult(
            status="no_fit",
            message=str(response.get("message") or ""),
            request=payload,
            response=response,
            attempts=attempts,
        )
    return DeliveryResult(status="error", message="неожиданный формат ответа",
                          request=payload, response=response, attempts=attempts)


def _is_allowed_url(url: str) -> bool:
    """Не пропускает javascript/file/data URL и неожиданный домен из ответа сервиса."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    configured = (os.getenv("MBK_ROUTER_ALLOWED_HOSTS") or "").strip()
    if not configured:
        return True
    allowed = {host.strip().lower() for host in configured.split(",") if host.strip()}
    return parsed.hostname.lower() in allowed


def _post(
    url: str, payload: dict[str, Any], *, idempotency_key: str | None = None
) -> Any:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    token = (os.getenv("MBK_ROUTER_TOKEN") or "").strip()
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    if idempotency_key:
        request.add_header("Idempotency-Key", idempotency_key)
    try:
        with transport.direct_urlopen(request, REQUEST_TIMEOUT) as response:
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise DeliveryRequestError(
            f"HTTP {exc.code}: {detail}", retryable=exc.code >= 500 or exc.code == 429
        ) from exc
    except (TimeoutError, urllib.error.URLError) as exc:
        raise DeliveryRequestError(str(exc), retryable=True) from exc
    except json.JSONDecodeError as exc:
        raise DeliveryRequestError("невалидный JSON в ответе", retryable=False) from exc
