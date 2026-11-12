"""Поля лида, которые заказчик видит в карточке, и коды, по которым он их читает.

Список полей и кодов заказчик прислал 20.08.2026. Он же прислал замечание:
«UF_CRM_1452153725 — адрес прописки, по нему определяется регион клиента. Вот
это ещё пустое пришло». Поле заполнялось только с формы сайта, а виджет форму
не передаёт — у клиента из чата оно уходило пустым, и регион в карточке не
определялся.

Коды здесь продублированы намеренно, числами из письма заказчика, а не ссылками
на наши же перечисления: смысл теста в том, чтобы расхождение с их системой
падало у нас, а не всплывало через неделю кривой категорией лида.
"""
from __future__ import annotations

import pytest

from delivery.crm_contracts import (
    AssetsAvailability,
    CarAvailability,
    CarCredit,
    CreditHistory,
    EmploymentType,
    OverdueDuration,
    RealtyRestriction,
    RealtyType,
    SitePartnerConsent,
)
from delivery.crm_mapping import map_lead_intake


KAZAN = {
    "city": "г Казань",
    "region": "респ Татарстан",
    "location": {
        "unrestricted_value": "420000, респ Татарстан, г Казань",
        "value": "г Казань",
        "city_with_type": "г Казань",
        "region_with_type": "респ Татарстан",
    },
}


def _payload(**client) -> dict:
    facts = {
        "client": {"name": "Пётр", "phone": "+79850003131", "age": 41, **client},
        "request": {"amount": 800_000},
        "debts": {"total": 400_000},
    }
    mapping = map_lead_intake(facts, form_filled_out=True, site_consent_shown=False)
    assert mapping.ready, [issue.message for issue in mapping.issues]
    assert mapping.request is not None
    return mapping.request.model_dump(by_alias=True, exclude_none=True)


def test_the_registration_address_carries_the_locality_when_the_form_is_absent():
    """Пустое поле означает лид без региона — то есть без категории."""
    payload = _payload(**KAZAN)

    assert payload["UF_CRM_1452153725"] == "420000, респ Татарстан, г Казань"


def test_the_form_address_still_wins_over_the_locality():
    payload = _payload(registration_address="г Москва, ул Ленина, д 1, кв 2", **KAZAN)

    assert payload["UF_CRM_1452153725"] == "г Москва, ул Ленина, д 1, кв 2"


def test_without_a_city_the_address_stays_empty_instead_of_being_invented():
    payload = _payload()

    assert "UF_CRM_1452153725" not in payload


def test_the_locality_is_not_dressed_up_as_a_street_address():
    """Улицы в чате нет и придумывать её нельзя — уезжает только населённый пункт."""
    payload = _payload(**KAZAN)

    assert "ул " not in payload["UF_CRM_1452153725"]
    assert "д " not in payload["UF_CRM_1452153725"]


@pytest.mark.parametrize(
    "member, code",
    [
        (CreditHistory.NO_OVERDUE, 9255),
        (CreditHistory.PAST_OVERDUE, 9256),
        (CreditHistory.CURRENT_OVERDUE, 9257),
        (CreditHistory.READY_FOR_BANKRUPTCY, 9258),
        (OverdueDuration.NO_OVERDUE, 22606),
        (OverdueDuration.UP_TO_ONE_MONTH, 22602),
        (OverdueDuration.ONE_TO_THREE_MONTHS, 22603),
        (OverdueDuration.THREE_TO_SIX_MONTHS, 22604),
        (OverdueDuration.OVER_SIX_MONTHS, 22605),
        (EmploymentType.OFFICIAL, 376),
        (EmploymentType.HIRED, 3082),
        (EmploymentType.BUSINESS, 377),
        (EmploymentType.UNOFFICIAL, 2703),
        (EmploymentType.SELF_EMPLOYED, 15001),
        (EmploymentType.RETIRED, 15002),
        (EmploymentType.UNEMPLOYED, 378),
        (AssetsAvailability.YES, 4697),
        (AssetsAvailability.NO, 4698),
        (CarAvailability.YES, 4695),
        (CarAvailability.NO, 4696),
        (CarCredit.YES, 4699),
        (CarCredit.NO, 4600),
        (RealtyType.FLAT, 190),
        (RealtyType.HOUSE, 191),
        (RealtyType.TOWNHOUSE, 192),
        (RealtyType.APARTMENTS, 194),
        (RealtyType.ROOM, 2265),
        (RealtyType.COMMERCIAL, 3075),
        (RealtyRestriction.NO_RESTRICTION, 21003),
        (RealtyRestriction.MORTGAGE, 21000),
        (RealtyRestriction.PLEDGE, 21001),
        (RealtyRestriction.ARREST, 21002),
        (SitePartnerConsent.YES, 23000),
        (SitePartnerConsent.NO, 23001),
        (SitePartnerConsent.NOT_REQUIRED, 23002),
    ],
)
def test_codes_match_the_list_the_customer_published(member, code):
    assert int(member) == code


def test_the_owner_field_is_words_not_a_code():
    """CHAT_OWNER единственное перечисление, где CRM ждёт строку."""
    facts = {
        "client": {"name": "Пётр", "phone": "+79850003131", "age": 41, **KAZAN},
        "request": {"amount": 800_000},
        "debts": {"total": 400_000},
        "assets": {
            "has_realty": True,
            "realty": {
                "kind": "flat",
                "ownership_form": "private",
                "owners": "client_and_spouse",
                **KAZAN,
            },
        },
    }
    mapping = map_lead_intake(facts, form_filled_out=True, site_consent_shown=False)
    assert mapping.request is not None
    payload = mapping.request.model_dump(by_alias=True, exclude_none=True)

    assert payload["CHAT_OWNER"] == "BOTH"
