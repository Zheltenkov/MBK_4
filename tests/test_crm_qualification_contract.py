"""first-qualification — срез lead-intake: те же поля, те же типы, те же значения.

С 31.08.2026 (crm-mbk, коммит 18490d4d) квалификация CRM читает только
Битрикс-имена полей — те же, что приём лида. Анкетные ключи (`amount`, `age`,
`employment`…) она молча не видит: профиль клиента выходил пустым, строгий
RuleMatcher не пропускал ни одно правило, и 13 из 13 квалификаций за
11-14.09.2026 вернули category=null.

CRM считает категорию дважды: по квалификации (бот решает про офер и
согласие) и заново по лиду (категория в Битриксе). Поле, которое в двух
запросах называется или считается по-разному, разводит эти расчёты.
"""
from __future__ import annotations

from copy import deepcopy

from delivery.crm_contracts import FirstQualificationRequest, LeadIntakeRequest, RealtyQualificationRequest
from delivery.crm_mapping import map_first_qualification, map_lead_intake


# Что читают CrmLeadProfileMapper::toProfile и BUtsCrmLead::campaignProfile
# (crm-mbk, 14.09.2026) из того, что бот собирает в диалоге.
CRM_QUALIFICATION_FIELDS = {
    "SUMM",
    "BIRTHDATE",
    "UF_CRM_1452153725",
    "UF_CREDIT_HISTORY",
    "UF_CRM_LOAN_OVERDUE_DURATION",
    "UF_DEBT_SUMM",
    "UF_CRM_1460622474",
    "UF_CRM_1461049862",
    "UF_REALTY_CC_4_22",
    "UF_CRM_1452154011",
    "CHAT_REALTY_REGION",
    "CHAT_OWNER",
    "UF_CRM_RESTRICTION",
    "UF_HAVE_AUTO",
    "UF_CAR_PRODUCTION_YEAR_1",
    "UF_CREDIT_AUTO_1",
}

# Клиент, ответивший на всё, что влияет на категорию: каждое поле
# квалификации у него заполнено, пустое совпадение ничего бы не доказало.
FULL_PROFILE = {
    "request": {"amount": 1_500_000},
    "client": {
        "age": 42,
        "name": "Тестов Тест Тестович",
        "phone": "+79991234567",
        "location": {
            "unrestricted_value": "142100, Московская обл, г Подольск",
            "city_with_type": "г Подольск",
            "region_with_type": "Московская обл",
        },
    },
    "debts": {
        "has_active_credits": True,
        "total": 900_000,
        "credit_history": "has_overdue",
        "overdue_duration": "from_1m_to_3m",
    },
    "employment": {"type": "hired", "income": 95_000},
    "assets": {
        "has_realty": True,
        "realty": {
            "kind": "flat",
            "ownership_form": "private",
            "owners": "client_and_spouse",
            "restriction": "mortgage",
            "location": {
                "city_with_type": "г Подольск",
                "region_with_type": "Московская обл",
            },
        },
        "has_vehicle": True,
        "vehicle": {"year": 2019, "pledged": False},
    },
}


def _schema_properties(model) -> dict:
    return model.model_json_schema()["properties"]


def test_qualification_sends_exactly_the_fields_crm_reads():
    assert set(FirstQualificationRequest().as_api_dict()) == CRM_QUALIFICATION_FIELDS


def test_every_qualification_field_is_the_same_field_of_the_lead():
    """Имя, алиас, тип и ограничения — как у одноимённого поля лида."""
    lead = _schema_properties(LeadIntakeRequest)
    lead_names = LeadIntakeRequest.model_fields
    for name, field in FirstQualificationRequest.model_fields.items():
        assert name in lead_names, f"{name}: у лида такого поля нет"
        assert field.alias == lead_names[name].alias, (
            f"{name}: {field.alias} в квалификации против "
            f"{lead_names[name].alias} в лиде"
        )
        assert _schema_properties(FirstQualificationRequest)[field.alias] == (
            lead[field.alias]
        ), f"{field.alias}: тип или ограничения расходятся с лидом"


def test_a_full_profile_is_sent_identically_in_both_calls():
    qualification = map_first_qualification(deepcopy(FULL_PROFILE))
    lead = map_lead_intake(
        deepcopy(FULL_PROFILE),
        form_filled_out=True,
        site_consent_shown=False,
    )

    assert qualification.ready, [i.message for i in qualification.issues]
    assert lead.ready, [i.message for i in lead.issues]
    assert qualification.request is not None and lead.request is not None
    sent = qualification.request.as_api_dict()
    created = lead.request.as_api_dict()
    empty = sorted(field for field, value in sent.items() if value is None)
    assert not empty, f"профиль неполный, проверка слепа к полям: {empty}"
    assert {field: created[field] for field in sent} == sent


def test_the_realty_check_sends_the_object_fields_of_the_lead():
    lead = LeadIntakeRequest.model_fields
    fields = RealtyQualificationRequest.model_fields

    assert {field.alias for field in fields.values()} == {
        "UF_CRM_1452154011",
        "CHAT_REALTY_REGION",
        "CHAT_OWNER",
    }
    for name, field in fields.items():
        assert lead[name].alias == field.alias, f"{name}: алиас расходится с лидом"
    assert {field.alias for field in fields.values()} <= CRM_QUALIFICATION_FIELDS
