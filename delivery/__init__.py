"""Передача подобранного клиента в маршрутизирующий микросервис."""
from .contracts import LeadRoutingRequest
from .microservice import DeliveryResult, is_mocked, router_url, submit, validate_config

__all__ = [
    "DeliveryResult",
    "LeadRoutingRequest",
    "is_mocked",
    "router_url",
    "submit",
    "validate_config",
]
