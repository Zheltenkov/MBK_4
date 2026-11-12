"""Строгие контракты внешнего classification API CRM МБК.

Эти модели намеренно отделены от внутреннего ``mbk.lead.v1``. Внутренняя схема
хранит ответы диалога, а CRM-схема использует enum и номенклатуру backend/Bitrix.
"""
from __future__ import annotations

from enum import IntEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)


def _as_crm_amount_string(value: Any) -> Any:
    """Приводит сумму к строке — так эти поля принимает CRM.

    Ровно пять сумм их валидатор требует строкой, остальные числа принимает
    числами. Проверено прямой пробой 11.08.2026 запросом без телефона (лид при
    этом не создаётся): UF_DEBT_SUMM, UF_CRM_MORTGAGE_BALANCE и три
    UF_CRM_CAR_LOAN_BALANCE отвечают «must be a string».

    Мы храним их числами и валидируем как числа — иначе в CRM уехало бы
    «примерно триста» вместо суммы. Строкой они становятся только на границе.
    """
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        raise ValueError("сумма не может быть булевым значением")
    if isinstance(value, int):
        if value < 0:
            raise ValueError("сумма не может быть отрицательной")
        return str(value)
    return value


# Сумма, которую CRM принимает только строкой. Число на входе, строка на выходе.
CrmAmountString = Annotated[str | None, BeforeValidator(_as_crm_amount_string)]


class _CrmModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    def as_api_dict(self, *, exclude_none: bool = False) -> dict[str, object]:
        """Сериализует модель с внешними именами полей CRM."""
        return self.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=exclude_none,
        )


class _CrmResponseModel(_CrmModel):
    """Ответы CRM: неизвестные поля игнорируются, а не валят квалификацию.

    CRM — сервер, она добавляет поля когда хочет: 04.09.2026 в ответе
    first-qualification появился `result.widget_denis`, и строгий контракт
    три дня ронял КАЖДУЮ квалификацию («Ответ CRM не соответствует
    контракту») — 19 клиентов, дошедших до конца анкеты, остались без лида.
    Строгими остаются только запросы (_CrmModel): их пишем мы, опечатка в
    имени поля должна падать. Читатель чужого API обязан быть терпимым.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class RealtyType(IntEnum):
    FLAT = 190
    HOUSE = 191
    TOWNHOUSE = 192
    APARTMENTS = 194
    ROOM = 2265
    COMMERCIAL = 3075


class RealtyOwner(IntEnum):
    CLIENT_ONLY = 4673
    CLIENT_AND_SPOUSE = 4675


class OwnershipForm(IntEnum):
    PRIVATE = 4671


class CarAvailability(IntEnum):
    YES = 4695
    NO = 4696


class AssetsAvailability(IntEnum):
    YES = 4697
    NO = 4698


class CreditHistory(IntEnum):
    NO_OVERDUE = 9255
    PAST_OVERDUE = 9256
    CURRENT_OVERDUE = 9257
    READY_FOR_BANKRUPTCY = 9258


class OverdueDuration(IntEnum):
    NO_OVERDUE = 22606
    UP_TO_ONE_MONTH = 22602
    ONE_TO_THREE_MONTHS = 22603
    THREE_TO_SIX_MONTHS = 22604
    OVER_SIX_MONTHS = 22605


class EmploymentType(IntEnum):
    OFFICIAL = 376
    HIRED = 3082
    BUSINESS = 377
    UNOFFICIAL = 2703
    SELF_EMPLOYED = 15001
    RETIRED = 15002
    UNEMPLOYED = 378


class CarCredit(IntEnum):
    YES = 4699
    NO = 4600


class Gender(IntEnum):
    MALE = 4450
    FEMALE = 4451


class Education(IntEnum):
    HIGHER = 9228
    INCOMPLETE_HIGHER = 9229
    SECONDARY_VOCATIONAL = 9230
    SECONDARY = 9231


class MaritalStatus(IntEnum):
    SINGLE = 4668
    MARRIED = 4669
    CIVIL_MARRIAGE = 4670
    DIVORCED = 15000


class ActiveLoanType(IntEnum):
    UNSECURED_CONSUMER = 4657
    REALTY_SECURED = 4658
    MORTGAGE = 4659
    MICROLOANS = 4660
    CAR_SECURED = 4661


class RealtyRestriction(IntEnum):
    NO_RESTRICTION = 21003
    MORTGAGE = 21000
    PLEDGE = 21001
    ARREST = 21002


class SitePartnerConsent(IntEnum):
    YES = 23000
    NO = 23001
    NOT_REQUIRED = 23002


class FirstQualificationRequest(_CrmModel):
    """Профиль клиента для first-qualification — Битрикс-именами полей.

    С 31.08.2026 (crm-mbk, коммит 18490d4d «Новая партнёрка») квалификация
    читает те же поля, что и приём лида: «Отдельного «анкетного» формата у
    квалификации нет» (FirstQualificationController). Анкетные ключи CRM молча
    не видела, профиль приходил пустым, и строгий RuleMatcher не пропускал ни
    одно правило — category=null у каждого лида бота до 14.09.2026.

    Имена атрибутов, алиасы и типы совпадают с LeadIntakeRequest поле в поле:
    квалификация — срез лида. CRM считает категорию дважды — по этому запросу
    (бот по ней решает про офер и согласие) и заново по лиду (категория в
    Битриксе), — и расходиться эти расчёты не должны. Проверяет
    tests/test_crm_qualification_contract.py.

    Состав — поля, которые читают CrmLeadProfileMapper::toProfile и предикт
    ёмкости кампаний BUtsCrmLead::campaignProfile, из тех, что бот собирает.
    Отсутствующий ответ уходит null.
    """

    amount: int | None = Field(default=None, alias="SUMM", gt=0)
    # Возраст CRM вычисляет из даты рождения: Carbon::parse(BIRTHDATE)->age.
    birthdate: str | None = Field(
        default=None,
        alias="BIRTHDATE",
        pattern=r"^\d{2}-\d{2}-\d{4}$",
    )
    # Регион проживания CRM ищет в адресе прописки (RegionScopeResolver).
    registration_address: str | None = Field(
        default=None,
        alias="UF_CRM_1452153725",
        min_length=1,
    )
    credit_history: CreditHistory | None = Field(
        default=None,
        alias="UF_CREDIT_HISTORY",
    )
    overdue_duration: OverdueDuration | None = Field(
        default=None,
        alias="UF_CRM_LOAN_OVERDUE_DURATION",
    )
    debt_amount: CrmAmountString = Field(default=None, alias="UF_DEBT_SUMM")
    employment: EmploymentType | None = Field(
        default=None,
        alias="UF_CRM_1460622474",
    )
    monthly_income: int | None = Field(
        default=None,
        alias="UF_CRM_1461049862",
        ge=0,
    )
    has_realty: AssetsAvailability | None = Field(
        default=None,
        alias="UF_REALTY_CC_4_22",
    )
    realty_type: RealtyType | None = Field(
        default=None,
        alias="UF_CRM_1452154011",
    )
    realty_region: str | None = Field(default=None, alias="CHAT_REALTY_REGION")
    chat_owner: Literal["CLIENT", "BOTH"] | None = Field(
        default=None,
        alias="CHAT_OWNER",
    )
    realty_restriction: RealtyRestriction | None = Field(
        default=None,
        alias="UF_CRM_RESTRICTION",
    )
    has_car: CarAvailability | None = Field(default=None, alias="UF_HAVE_AUTO")
    car_year_1: int | None = Field(
        default=None,
        alias="UF_CAR_PRODUCTION_YEAR_1",
        ge=1900,
        le=2100,
    )
    car_credit_1: CarCredit | None = Field(default=None, alias="UF_CREDIT_AUTO_1")


class FirstQualificationResult(_CrmResponseModel):
    # Поля ниже фактически получены от боевого endpoint 23.07.2026, хотя их не
    # было в сокращенном примере backend-разработчика.
    matched: bool
    priority: str | None
    suitable_categories: list[int]
    segment: str | None
    partner: str | None
    offer: str | None
    suitable_offers: list[str]
    need_partner_consent: bool
    vertical: str | None = None
    category: int | None = None
    need_site_consent: bool
    thanks_url: str | None = None


class FirstQualificationResponse(_CrmResponseModel):
    result: FirstQualificationResult
    success: bool


class RealtyQualificationRequest(_CrmModel):
    """Первый объект для realty-qualification — Битрикс-именами, как у лида.

    С 31.08.2026 проверка объекта строит профиль тем же CrmLeadProfileMapper,
    что и приём (FirstQualificationController::realtyCheck): тип
    UF_CRM_1452154011, регион CHAT_REALTY_REGION, собственник CHAT_OWNER. Форму
    собственности CRM выводит сама — частная, раз собственник назван, — поэтому
    запрос собирается только для подтверждённо частного объекта.
    """

    realty_type: RealtyType = Field(alias="UF_CRM_1452154011")
    realty_region: str = Field(alias="CHAT_REALTY_REGION", min_length=1)
    chat_owner: Literal["CLIENT", "BOTH"] = Field(alias="CHAT_OWNER")


class RealtyQualificationResult(_CrmResponseModel):
    is_first_category: bool


class RealtyQualificationResponse(_CrmResponseModel):
    result: RealtyQualificationResult
    # Фактически присутствует в боевом ответе 24.07.2026, хотя отсутствовал
    # в сокращённом примере backend-разработчика.
    success: bool


class LeadIntakeRequest(_CrmModel):
    """Полный подтвержденный контракт создания лида.

    Backend фактически требует PHONE и PHONE_DEAL с одним номером клиента, а также
    два служебных флага. Остальные поля передаются, если соответствующий ответ
    действительно был собран в диалоге.
    """

    phone: str = Field(alias="PHONE", pattern=r"^\d{10,15}$")
    phone_deal: str = Field(alias="PHONE_DEAL", pattern=r"^\d{10,15}$")
    form_filled_out: Literal[0, 1] = Field(alias="UF_FROM_SITE_FILLED_OUT")
    return_response: Literal[True] = Field(default=True, alias="RETURN_RESPONSE")

    # Клиент.
    last_name: str | None = Field(default=None, alias="LAST_NAME", min_length=1)
    name: str | None = Field(default=None, alias="NAME", min_length=1)
    second_name: str | None = Field(default=None, alias="SECOND_NAME", min_length=1)
    full_name: str | None = Field(default=None, alias="FULL_NAME", min_length=1)
    email: str | None = Field(default=None, alias="EMAIL", min_length=3)
    gender: Gender | None = Field(default=None, alias="UF_GENDER")
    birthdate: str | None = Field(
        default=None,
        alias="BIRTHDATE",
        pattern=r"^\d{2}-\d{2}-\d{4}$",
    )
    education: Education | None = Field(default=None, alias="UF_EDUCATION")
    marital_status: MaritalStatus | None = Field(
        default=None,
        alias="UF_MARITAL_STATUS",
    )
    has_dependents: Literal[0, 1] | None = Field(
        default=None,
        alias="UF_CRM_DEPENDENTS",
    )
    dependents_count: int | None = Field(
        default=None,
        alias="UF_DEPENDENTS",
        ge=0,
    )

    # Паспорт и адреса.
    passport_number: str | None = Field(
        default=None,
        alias="UF_CRM_1452153589",
        min_length=1,
    )
    passport_issue_date: str | None = Field(
        default=None,
        alias="UF_CRM_1452153763",
        pattern=r"^\d{2}-\d{2}-\d{4}$",
    )
    birthplace: str | None = Field(
        default=None,
        alias="UF_CRM_1452152806",
        min_length=1,
    )
    police_unit_code: str | None = Field(
        default=None,
        alias="UF_CRM_1461049017",
        min_length=1,
    )
    police_unit_name: str | None = Field(
        default=None,
        alias="UF_CRM_1452153623",
        min_length=1,
    )
    registration_address: str | None = Field(
        default=None,
        alias="UF_CRM_1452153725",
        min_length=1,
    )
    registration_date: str | None = Field(
        default=None,
        alias="UF_CRM_REGISTRATION_DATE",
        pattern=r"^\d{2}-\d{2}-\d{4}$",
    )
    residence_address: str | None = Field(
        default=None,
        alias="UF_CRM_1452153749",
        min_length=1,
    )

    # Запрос клиента.
    amount: int | None = Field(default=None, alias="SUMM", gt=0)
    credit_period: int | None = Field(default=None, alias="CREDIT_PERIOD", gt=0)

    # Кредитная нагрузка.
    credit_history: CreditHistory | None = Field(
        default=None,
        alias="UF_CREDIT_HISTORY",
    )
    overdue_duration: OverdueDuration | None = Field(
        default=None,
        alias="UF_CRM_LOAN_OVERDUE_DURATION",
    )
    debt_amount: CrmAmountString = Field(default=None, alias="UF_DEBT_SUMM")
    monthly_payments: int | None = Field(
        default=None,
        alias="UF_GENERAL_MONTHLY_PAYMENTS",
        ge=0,
    )
    active_loan_types: list[ActiveLoanType] | None = Field(
        default=None,
        alias="UF_ACTIVE_LOANS_TYPES",
    )

    # Работа.
    employment: EmploymentType | None = Field(
        default=None,
        alias="UF_CRM_1460622474",
    )
    employer_name: str | None = Field(
        default=None,
        alias="UF_CRM_1452153064",
        min_length=1,
    )
    position: str | None = Field(default=None, alias="UF_POSITION", min_length=1)
    work_start: str | None = Field(
        default=None,
        alias="UF_CRM_1595576698",
        pattern=r"^\d{2}-\d{4}$",
    )
    work_experience_months: int | None = Field(
        default=None,
        alias="UF_CRM_WORK_EXPERIENCE_IN_MONTHS",
        ge=0,
    )
    monthly_income: int | None = Field(
        default=None,
        alias="UF_CRM_1461049862",
        ge=0,
    )
    work_address: str | None = Field(
        default=None,
        alias="UF_CRM_1452153012",
        min_length=1,
    )
    work_phone: str | None = Field(
        default=None,
        alias="UF_CRM_1452152943",
        min_length=1,
    )

    # Недвижимость.
    has_realty: AssetsAvailability | None = Field(
        default=None,
        alias="UF_REALTY_CC_4_22",
    )
    realty_type: RealtyType | None = Field(
        default=None,
        alias="UF_CRM_1452154011",
    )
    realty_region: str | None = Field(default=None, alias="CHAT_REALTY_REGION")
    chat_owner: Literal["CLIENT", "BOTH"] | None = Field(
        default=None,
        alias="CHAT_OWNER",
    )
    mortgage_balance: CrmAmountString = Field(
        default=None,
        alias="UF_CRM_MORTGAGE_BALANCE",
    )
    realty_restriction: RealtyRestriction | None = Field(
        default=None,
        alias="UF_CRM_RESTRICTION",
    )
    rent_expense: int | None = Field(
        default=None,
        alias="UF_CRM_RENT_EXPENSE",
        ge=0,
    )

    # Автомобили. CRM поддерживает до трех, хотя текущий диалог v1 собирает один.
    has_car: CarAvailability | None = Field(default=None, alias="UF_HAVE_AUTO")
    car_brand_1: str | None = Field(default=None, alias="UF_CAR_BRAND_1")
    car_model_1: str | None = Field(default=None, alias="UF_CAR_MODEL_1")
    car_year_1: int | None = Field(
        default=None,
        alias="UF_CAR_PRODUCTION_YEAR_1",
        ge=1900,
        le=2100,
    )
    car_credit_1: CarCredit | None = Field(default=None, alias="UF_CREDIT_AUTO_1")
    car_loan_balance_1: CrmAmountString = Field(
        default=None,
        alias="UF_CRM_CAR_LOAN_BALANCE_1",
    )
    car_brand_2: str | None = Field(default=None, alias="UF_CAR_BRAND_2")
    car_model_2: str | None = Field(default=None, alias="UF_CAR_MODEL_2")
    car_year_2: int | None = Field(
        default=None,
        alias="UF_CAR_PRODUCTION_YEAR_2",
        ge=1900,
        le=2100,
    )
    car_credit_2: CarCredit | None = Field(default=None, alias="UF_CREDIT_AUTO_2")
    car_loan_balance_2: CrmAmountString = Field(
        default=None,
        alias="UF_CRM_CAR_LOAN_BALANCE_2",
    )
    car_brand_3: str | None = Field(default=None, alias="UF_CAR_BRAND_3")
    car_model_3: str | None = Field(default=None, alias="UF_CAR_MODEL_3")
    car_year_3: int | None = Field(
        default=None,
        alias="UF_CAR_PRODUCTION_YEAR_3",
        ge=1900,
        le=2100,
    )
    car_credit_3: CarCredit | None = Field(default=None, alias="UF_CREDIT_AUTO_3")
    car_loan_balance_3: CrmAmountString = Field(
        default=None,
        alias="UF_CRM_CAR_LOAN_BALANCE_3",
    )

    # Согласие и аналитика.
    # Для брошенной анкеты поле должно отсутствовать, поэтому None исключается
    # при отправке lead-intake.
    agree_partners: SitePartnerConsent | None = Field(
        default=None,
        alias="UF_SITE_AGREE_PARTNERS",
    )
    # Значения по умолчанию задал backend МБК: лид из чата отличается от
    # веб-формы каналом, а не источником.
    utm_source: str | None = Field(default="crm", alias="UTMSOURCE")
    utm_campaign: str | None = Field(default=None, alias="UTM_CAMPAIGN")
    adv_channel: str | None = Field(default="bot", alias="ADV_CHANNEL")
    utm_key: str | None = Field(default=None, alias="UTMKEY")
    k50: str | None = Field(default=None, alias="K50")
    landing: str | None = Field(default=None, alias="LANDING")
    comments: str | None = Field(default=None, alias="COMMENTS")
    # Сквозная аналитика и почтовая нотификация Битрикса — как у анкеты
    # (Leads::mapBitrix24): без GA/YM чат-лид выпадает из отчётов, без
    # DEFAULT_EMAIL_* Битрикс не шлёт письмо о новом лиде.
    ga_client_id: str | None = Field(
        default=None, alias="GOOGLE_ANALYTICS_CLIENT_ID"
    )
    ym_client_id: str | None = Field(
        default=None, alias="YANDEX_METRIKA_CLIENT_ID"
    )
    default_email_to: str | None = Field(default=None, alias="DEFAULT_EMAIL_TO")
    default_email_from: str | None = Field(
        default=None, alias="DEFAULT_EMAIL_FROM"
    )

    @model_validator(mode="after")
    def phone_fields_must_match(self) -> "LeadIntakeRequest":
        """Сайт и backend подтвердили: оба телефонных поля должны совпадать."""
        if self.phone_deal != self.phone:
            raise ValueError("PHONE_DEAL должен совпадать с PHONE")
        return self


class LeadIntakeResult(_CrmResponseModel):
    lead_id: int = Field(alias="leadID", gt=0)


class LeadIntakeResponse(_CrmResponseModel):
    result: LeadIntakeResult
    # В сокращённом примере поля нет, но оба classification endpoint используют
    # единый верхнеуровневый success. Принимаем его опционально до live-smoke.
    success: bool | None = None
