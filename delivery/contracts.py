"""Временный schema-first контракт запроса маршрутизатора МБК.

Контракт перечисляет все потенциальные ответы текущего опроса. Поля не удаляются,
если конкретная ветка их не спрашивала: Pydantic сериализует их как JSON `null`.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from routing import RouteStatus, get_fact


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LocationCandidatePayload(_ContractModel):
    value: str | None = None
    unrestricted_value: str | None = None
    country: str | None = None
    country_iso_code: str | None = None
    region: str | None = None
    region_with_type: str | None = None
    region_fias_id: str | None = None
    region_iso_code: str | None = None
    city: str | None = None
    city_with_type: str | None = None
    city_fias_id: str | None = None
    settlement: str | None = None
    settlement_with_type: str | None = None
    settlement_fias_id: str | None = None
    fias_id: str | None = None
    fias_level: int | None = None
    display_name: str | None = None


class LocationPayload(LocationCandidatePayload):
    status: Literal["resolved", "ambiguous", "not_found", "unavailable"] | None = None
    query: str | None = None
    confirmation_required: bool | None = None
    candidates: list[LocationCandidatePayload] = Field(default_factory=list)


class ClientPayload(_ContractModel):
    name: str | None = None
    phone: str | None = None
    age: int | None = None
    birthdate: str | None = None
    goal: Literal["cash", "debt_relief", "refinance"] | None = None
    region_class: Literal["moscow_mo", "realty_list", "rf_other"] | None = None
    region: str | None = None
    city: str | None = None
    registration_address: str | None = None
    residence_address: str | None = None
    location: LocationPayload = Field(default_factory=LocationPayload)


class RequestedProductPayload(_ContractModel):
    amount: int | None = None


class PreferencesPayload(_ContractModel):
    collateral_allowed: bool | None = None
    realty_collateral_allowed: bool | None = None
    vehicle_collateral_allowed: bool | None = None
    preferred_collateral: Literal["realty", "vehicle", "any"] | None = None
    explore_alternatives: bool | None = None


class RealtyPayload(_ContractModel):
    # mbk_list — лист «МБК» (маршрут 1-2), realty_list — лист «Кредит.Клаб» (1-3).
    region_class: (
        Literal["moscow_mo", "mbk_list", "realty_list", "rf_other"] | None
    ) = None
    region: str | None = None
    city: str | None = None
    location: LocationPayload = Field(default_factory=LocationPayload)
    # `room` появился в матрице 12.08.2026 вместе с исправлением нормализатора,
    # а здесь его не было — и клиент с комнатой ронял CRM-workflow прямо на
    # проде. Список обязан совпадать с matrix.yaml; это проверяет тест
    # test_every_realty_kind_of_the_matrix_fits_the_internal_contract.
    kind: Literal[
        "flat", "apartments", "commercial", "house", "room", "other"
    ] | None = None
    ownership_form: Literal["private", "municipal", "other"] | None = None
    owners: Literal["client_only", "client_and_spouse", "others"] | None = None
    restriction: Literal["none", "mortgage", "pledge", "arrest"] | None = None


class VehiclePayload(_ContractModel):
    year: int | None = None
    make: str | None = None
    model: str | None = None
    pledged: bool | None = None


class AssetsPayload(_ContractModel):
    has_realty: bool | None = None
    realty: RealtyPayload = Field(default_factory=RealtyPayload)
    has_vehicle: bool | None = None
    vehicle: VehiclePayload = Field(default_factory=VehiclePayload)


class DebtsPayload(_ContractModel):
    has_active_credits: bool | None = None
    total: int | None = None
    credit_history: (
        Literal["no_overdue", "past_overdue", "has_overdue", "ready_bankruptcy"]
        | None
    ) = None
    overdue_duration: (
        Literal[
            "no_overdue",
            "up_to_1m",
            "from_1m_to_3m",
            "from_3m_to_6m",
            "over_6m",
        ]
        | None
    ) = None


class EmploymentPayload(_ContractModel):
    type: (
        Literal[
            "business",
            "official",
            "hired",
            "unofficial",
            "self_employed",
            "retired",
            "unemployed",
        ]
        | None
    ) = None
    income: int | None = None


class RoutingPayload(_ContractModel):
    # При локальном no_fit не придумываем маршрут: CRM всё равно должна получить
    # qualification/lead-intake, а диагностический контракт сохраняет явные null.
    route_id: str | None = None
    priority: str | None = None
    product: str | None = None
    segment: str | None = None
    family: Literal["realty", "debt_relief", "vehicle", "consumer"] | None = None


class LeadRoutingRequest(_ContractModel):
    """Полный JSON, отправляемый в маршрутизирующий микросервис."""

    schema_version: Literal["mbk.lead.v1"] = "mbk.lead.v1"
    routing: RoutingPayload
    client: ClientPayload = Field(default_factory=ClientPayload)
    request: RequestedProductPayload = Field(default_factory=RequestedProductPayload)
    preferences: PreferencesPayload = Field(default_factory=PreferencesPayload)
    assets: AssetsPayload = Field(default_factory=AssetsPayload)
    debts: DebtsPayload = Field(default_factory=DebtsPayload)
    employment: EmploymentPayload = Field(default_factory=EmploymentPayload)

    @classmethod
    def from_facts(
        cls,
        facts: dict[str, Any],
        matched: RouteStatus | None,
    ) -> "LeadRoutingRequest":
        route = matched.route if matched is not None else None
        return cls(
            routing=RoutingPayload(
                route_id=route.id if route is not None else None,
                priority=route.priority if route is not None else None,
                product=route.product if route is not None else None,
                segment=route.segment if route is not None else None,
                family=route.family if route is not None else None,
            ),
            client=ClientPayload(
                name=get_fact(facts, "client.name"),
                phone=get_fact(facts, "client.phone"),
                age=get_fact(facts, "client.age"),
                birthdate=get_fact(facts, "client.birthdate"),
                goal=get_fact(facts, "client.goal"),
                region_class=get_fact(facts, "client.region_class"),
                region=get_fact(facts, "client.region"),
                city=get_fact(facts, "client.city"),
                registration_address=get_fact(
                    facts,
                    "client.registration_address",
                ),
                residence_address=get_fact(
                    facts,
                    "client.residence_address",
                ),
                location=_location_payload(get_fact(facts, "client.location")),
            ),
            request=RequestedProductPayload(
                amount=get_fact(facts, "request.amount"),
            ),
            preferences=PreferencesPayload(
                collateral_allowed=get_fact(
                    facts,
                    "preferences.collateral_allowed",
                ),
                realty_collateral_allowed=get_fact(
                    facts,
                    "preferences.realty_collateral_allowed",
                ),
                vehicle_collateral_allowed=get_fact(
                    facts,
                    "preferences.vehicle_collateral_allowed",
                ),
                preferred_collateral=get_fact(
                    facts,
                    "preferences.preferred_collateral",
                ),
                explore_alternatives=get_fact(
                    facts,
                    "preferences.explore_alternatives",
                ),
            ),
            assets=AssetsPayload(
                has_realty=get_fact(facts, "assets.has_realty"),
                realty=RealtyPayload(
                    region_class=get_fact(facts, "assets.realty.region_class"),
                    region=get_fact(facts, "assets.realty.region"),
                    city=get_fact(facts, "assets.realty.city"),
                    location=_location_payload(
                        get_fact(facts, "assets.realty.location")
                    ),
                    kind=get_fact(facts, "assets.realty.kind"),
                    ownership_form=get_fact(
                        facts,
                        "assets.realty.ownership_form",
                    ),
                    owners=get_fact(facts, "assets.realty.owners"),
                    restriction=get_fact(
                        facts,
                        "assets.realty.restriction",
                    ),
                ),
                has_vehicle=get_fact(facts, "assets.has_vehicle"),
                vehicle=VehiclePayload(
                    year=get_fact(facts, "assets.vehicle.year"),
                    make=get_fact(facts, "assets.vehicle.make"),
                    model=get_fact(facts, "assets.vehicle.model"),
                    pledged=get_fact(facts, "assets.vehicle.pledged"),
                ),
            ),
            debts=DebtsPayload(
                has_active_credits=get_fact(facts, "debts.has_active_credits"),
                total=get_fact(facts, "debts.total"),
                credit_history=get_fact(facts, "debts.credit_history"),
                overdue_duration=get_fact(facts, "debts.overdue_duration"),
            ),
            employment=EmploymentPayload(
                type=get_fact(facts, "employment.type"),
                income=get_fact(facts, "employment.income"),
            ),
        )

    def as_json_dict(self) -> dict[str, Any]:
        # Непройденные ветки сохраняются в JSON со значением null.
        return self.model_dump(mode="json", exclude_none=False)


def _location_payload(value: Any) -> LocationPayload:
    if not isinstance(value, dict):
        return LocationPayload()
    # State содержит диагностическое поле error, но техническая причина сбоя
    # внешнего провайдера не является частью бизнес-контракта маршрутизатора.
    allowed = set(LocationPayload.model_fields)
    return LocationPayload.model_validate(
        {key: item for key, item in value.items() if key in allowed}
    )
