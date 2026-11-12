"""Контракт delivery-адаптера: fail-closed, retry и безопасные ссылки."""
from __future__ import annotations

import delivery.microservice as microservice
from delivery.contracts import LeadRoutingRequest
from routing import evaluate_route


def _matched_route(matrix):
    route = next(route for route in matrix.routes if route.id == "realty_msk")
    facts = {
        "request": {"amount": 500_000},
        "client": {"age": 40, "region_class": "moscow_mo"},
        "assets": {
            "has_realty": True,
            "realty": {
                "region_class": "moscow_mo",
                "kind": "flat",
                "ownership_form": "private",
                "owners": "client_only",
            },
        },
    }
    return facts, evaluate_route(route, facts)


def test_missing_router_config_fails_closed(monkeypatch, matrix):
    monkeypatch.delenv("MBK_ROUTER_URL", raising=False)
    monkeypatch.delenv("MBK_ROUTER_MOCK", raising=False)
    facts, matched = _matched_route(matrix)

    result = microservice.submit(facts, matched)

    assert result.status == "error"
    assert result.url is None
    assert result.mocked is False


def test_request_contract_keeps_unasked_fields_as_null(matrix):
    facts, matched = _matched_route(matrix)

    payload = microservice._build_request(facts, matched)

    assert payload["schema_version"] == "mbk.lead.v1"
    assert payload["routing"]["route_id"] == "realty_msk"
    assert payload["client"]["phone"] is None
    assert payload["client"]["location"]["region_fias_id"] is None
    assert payload["preferences"]["collateral_allowed"] is None


def test_contract_can_represent_local_no_fit_without_inventing_route():
    payload = LeadRoutingRequest.from_facts({}, None).as_json_dict()

    assert payload["routing"] == {
        "route_id": None,
        "priority": None,
        "product": None,
        "segment": None,
        "family": None,
    }
    assert payload["assets"]["realty"]["restriction"] is None
    assert payload["assets"]["vehicle"]["year"] is None
    assert payload["debts"]["total"] is None
    assert payload["employment"]["type"] is None
    assert "bfl" not in payload
    assert "category" not in payload["routing"]
    assert "destination" not in payload["routing"]


def test_contract_keeps_product_specific_collateral_preferences(matrix):
    facts, matched = _matched_route(matrix)
    facts["preferences"] = {
        "collateral_allowed": True,
        "realty_collateral_allowed": False,
        "vehicle_collateral_allowed": True,
        "preferred_collateral": "vehicle",
    }

    payload = LeadRoutingRequest.from_facts(facts, matched).as_json_dict()

    assert payload["preferences"] == {
        "collateral_allowed": True,
        "realty_collateral_allowed": False,
        "vehicle_collateral_allowed": True,
        "preferred_collateral": "vehicle",
        "explore_alternatives": None,
    }


def test_request_contract_keeps_volunteered_realty_restriction(matrix):
    facts, matched = _matched_route(matrix)
    facts["assets"]["realty"]["restriction"] = "none"

    payload = microservice._build_request(facts, matched)

    assert payload["assets"]["realty"]["restriction"] == "none"


def test_mock_requires_explicit_flag(monkeypatch):
    monkeypatch.delenv("MBK_ROUTER_URL", raising=False)
    monkeypatch.delenv("MBK_ROUTER_MOCK", raising=False)
    assert microservice.is_mocked() is False

    monkeypatch.setenv("MBK_ROUTER_MOCK", "1")
    assert microservice.is_mocked() is True


def test_mock_completes_survey_and_returns_full_request(monkeypatch, matrix):
    monkeypatch.setenv("MBK_ROUTER_MOCK", "1")
    facts, matched = _matched_route(matrix)

    result = microservice.submit(facts, matched)

    assert result.status == "completed"
    assert result.url is None
    assert result.mocked is True
    assert result.request["schema_version"] == "mbk.lead.v1"


def test_retry_reuses_idempotency_key(monkeypatch, matrix):
    monkeypatch.setenv("MBK_ROUTER_URL", "https://router.example/api")
    monkeypatch.delenv("MBK_ROUTER_MOCK", raising=False)
    facts, matched = _matched_route(matrix)
    keys: list[str | None] = []

    def fake_post(url, payload, *, idempotency_key=None):
        keys.append(idempotency_key)
        if len(keys) == 1:
            raise microservice.DeliveryRequestError("timeout", retryable=True)
        return {"url": "https://www.mbk.ru/result/123"}

    monkeypatch.setattr(microservice, "_post", fake_post)
    result = microservice.submit(facts, matched, idempotency_key="session-1")

    assert result.status == "link"
    assert result.attempts == 2
    assert keys == ["session-1:realty_msk", "session-1:realty_msk"]


def test_unsafe_link_is_rejected(monkeypatch):
    monkeypatch.delenv("MBK_ROUTER_ALLOWED_HOSTS", raising=False)
    result = microservice._parse_response(
        {"url": "javascript:alert(1)"}, {"route_id": "x"}
    )
    assert result.status == "error"
    assert result.url is None


def test_link_host_allowlist(monkeypatch):
    monkeypatch.setenv("MBK_ROUTER_ALLOWED_HOSTS", "mbk.ru,www.mbk.ru")
    good = microservice._parse_response({"url": "https://www.mbk.ru/result"}, {})
    bad = microservice._parse_response({"url": "https://evil.example/result"}, {})

    assert good.status == "link"
    assert bad.status == "error"
