"""Детерминированное преобразование фактов диалога в qualification API.

Маппер не подставляет догадки. Если анкете не хватает обязательного ответа или
backend не расшифровал enum, результат содержит явные issues и сетевой вызов делать
нельзя.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError

from routing import get_fact
from routing.normalize import normalize_birthdate

from .crm_contracts import (
    CarAvailability,
    CarCredit,
    CreditHistory,
    EmploymentType,
    FirstQualificationRequest,
    LeadIntakeRequest,
    OverdueDuration,
    RealtyQualificationRequest,
    RealtyRestriction,
    RealtyType,
    AssetsAvailability,
    SitePartnerConsent,
)


logger = logging.getLogger(__name__)

IssueCode = Literal["missing_fact", "unknown_enum", "invalid_payload"]

# Почтовая нотификация Битрикса о новом лиде — как у анкеты
# (Leads::mapBitrix24): адресат захардкожен и там, отправитель — боевой
# MAIL_FROM_ADDRESS сайта. Без этих полей письмо о чат-лиде не уходит.
DEFAULT_EMAIL_TO = "leads@mbk-credit.ru"
DEFAULT_EMAIL_FROM = "robot@mbk.ru"

# COMMENTS в карточке лида — текст, менеджер читает его глазами; ограничение
# держит payload в разумных рамках. Начало диалога информативнее хвоста
# прощаний не бывает, поэтому при переполнении режем НАЧАЛО: финал анкеты
# (активы, телефон, согласие) важнее приветствия.
_TRANSCRIPT_LIMIT = 9000


def dialog_transcript(history: object) -> str | None:
    """Диалог для поля COMMENTS лида.

    Заказчик: «дай поле для комментариев куда писать в лид. смысл — надо
    видеть диалог». Формат: по строке на реплику, «Клиент:» / «Бот:».
    """
    if not isinstance(history, list):
        return None
    lines: list[str] = []
    for message in history:
        if not isinstance(message, dict):
            continue
        text = str(message.get("content") or "").strip()
        if not text:
            continue
        who = "Клиент" if message.get("role") == "user" else "Бот"
        lines.append(f"{who}: {text}")
    if not lines:
        return None
    text = "\n".join(lines)
    if len(text) > _TRANSCRIPT_LIMIT:
        text = "…" + text[-_TRANSCRIPT_LIMIT:]
    return text

_REALTY_TYPES: dict[str, RealtyType] = {
    "flat": RealtyType.FLAT,
    "house": RealtyType.HOUSE,
    "townhouse": RealtyType.TOWNHOUSE,
    "apartments": RealtyType.APARTMENTS,
    "room": RealtyType.ROOM,
    "commercial": RealtyType.COMMERCIAL,
}
_CHAT_OWNERS: dict[str, Literal["CLIENT", "BOTH"]] = {
    "client_only": "CLIENT",
    "client_and_spouse": "BOTH",
}
_CREDIT_HISTORY: dict[str, CreditHistory] = {
    "no_overdue": CreditHistory.NO_OVERDUE,
    "past_overdue": CreditHistory.PAST_OVERDUE,
    "has_overdue": CreditHistory.CURRENT_OVERDUE,
    "ready_bankruptcy": CreditHistory.READY_FOR_BANKRUPTCY,
}
_OVERDUE: dict[str, OverdueDuration | None] = {
    # «Нет просрочки» уходит явным 22606, как у анкеты сайта (OPTION_DELAY_NONE).
    # Правила матрицы сегодня допускают и пустое поле, но словарь значений у
    # двух источников лидов расходиться не должен.
    "no_overdue": OverdueDuration.NO_OVERDUE,
    "up_to_1m": OverdueDuration.UP_TO_ONE_MONTH,
    "from_1m_to_3m": OverdueDuration.ONE_TO_THREE_MONTHS,
    "from_3m_to_6m": OverdueDuration.THREE_TO_SIX_MONTHS,
    "over_6m": OverdueDuration.OVER_SIX_MONTHS,
}
_EMPLOYMENT: dict[str, EmploymentType] = {
    # Словарь анкеты сайта: найм (3082) / самозанятый (377) / не работаю (378).
    # Других значений справочника матрица правил не видела ни разу, а условие
    # занятости у «Потреб 1-й сорт» задано без «Пустого поля» — самозанятый с
    # кодом 15001 не проходил правило, которое проходит через анкету с 377.
    # Пенсионер и неофициально работающий в анкете выбрали бы «не работаю» и
    # «найм» — бот отправляет то же самое; сам факт в сессии остаётся точным.
    "official": EmploymentType.HIRED,
    "hired": EmploymentType.HIRED,
    "unofficial": EmploymentType.HIRED,
    "business": EmploymentType.BUSINESS,
    "self_employed": EmploymentType.BUSINESS,
    "retired": EmploymentType.UNEMPLOYED,
    "unemployed": EmploymentType.UNEMPLOYED,
}
_CAR_CREDIT: dict[bool, CarCredit] = {
    True: CarCredit.YES,
    False: CarCredit.NO,
}
_REALTY_RESTRICTION: dict[str, RealtyRestriction] = {
    "none": RealtyRestriction.NO_RESTRICTION,
    "mortgage": RealtyRestriction.MORTGAGE,
    "pledge": RealtyRestriction.PLEDGE,
    "arrest": RealtyRestriction.ARREST,
}


@dataclass(frozen=True)
class MappingIssue:
    code: IssueCode
    path: str
    message: str


@dataclass(frozen=True)
class CrmEnumMap:
    """Подтверждённые enum с возможностью подмены в изолированном тесте."""

    credit_history: dict[str, CreditHistory] = field(
        default_factory=lambda: dict(_CREDIT_HISTORY)
    )
    overdue: dict[str, OverdueDuration | None] = field(
        default_factory=lambda: dict(_OVERDUE)
    )
    employment: dict[str, EmploymentType] = field(
        default_factory=lambda: dict(_EMPLOYMENT)
    )
    car_credit: dict[bool, CarCredit] = field(
        default_factory=lambda: dict(_CAR_CREDIT)
    )
    realty_restriction: dict[str, RealtyRestriction] = field(
        default_factory=lambda: dict(_REALTY_RESTRICTION)
    )


# Продолжительности, означающие текущую просрочку, — всё, кроме «нет просрочки».
_OVERDUE_ACTIVE = frozenset({"up_to_1m", "from_1m_to_3m", "from_3m_to_6m", "over_6m"})


def _derived_credit_history(
    facts: dict[str, Any],
    enums: CrmEnumMap,
) -> CreditHistory | None:
    """Кредитная история словарём анкеты сайта: только «нет»/«есть просрочки».

    Матрица правил других значений справочника не пускает: условие «Кредитная
    история» у всех правил БФЛ — {9255, 9257} без «Пустого поля». Прямая
    отправка факта ломалась ровно на самом мотивированном клиенте: «готов
    оформить банкротство» (9258) в список не входит, и лид уходил в «Не прошёл
    квалификацию» (прод, 27.08.2026). Анкета сайта выводит поле из ответов про
    кредиты и просрочку (LeadsController::deriveCreditHistory) — здесь то же
    самое; сам факт `debts.credit_history` в сессии остаётся как был.
    """
    if get_fact(facts, "debts.has_active_credits") is False:
        return enums.credit_history.get("no_overdue")
    history = get_fact(facts, "debts.credit_history")
    duration = get_fact(facts, "debts.overdue_duration")
    if history == "has_overdue" or duration in _OVERDUE_ACTIVE:
        return enums.credit_history.get("has_overdue")
    if history is None and duration is None:
        # Про долги не отвечали вовсе — поле остаётся пустым, как раньше.
        return None
    return enums.credit_history.get("no_overdue")


@dataclass(frozen=True)
class QualificationMapping:
    request: FirstQualificationRequest | None
    issues: tuple[MappingIssue, ...]

    @property
    def ready(self) -> bool:
        return self.request is not None and not self.issues


@dataclass(frozen=True)
class LeadIntakeMapping:
    request: LeadIntakeRequest | None
    issues: tuple[MappingIssue, ...]

    @property
    def ready(self) -> bool:
        return self.request is not None and not self.issues


def map_first_qualification(
    facts: dict[str, Any],
    *,
    enum_map: CrmEnumMap | None = None,
) -> QualificationMapping:
    """Профиль клиента для first-qualification — срез будущего лида.

    Каждое поле считается тем же кодом, что и в map_lead_intake: CRM
    классифицирует клиента дважды — по квалификации и по лиду, — и два расчёта
    обязаны видеть одно и то же. Отсутствующий ответ уходит null: решение о
    матчинге принимает CRM, маппер ничего не додумывает.

    Одно отличие от лида намеренное: неизвестный код перечисления здесь
    становится null, а не issue. Квалификацию блокировать нельзя — у лида на
    этот случай своя проверка.
    """
    enums = enum_map or CrmEnumMap()
    # Второй объект, если он квалифицирующий, занимает ключи первого — как в лиде.
    realty_root = _qualifying_realty_root(facts)

    registration_address = _crm_registration_address(facts)
    if registration_address is None:
        # Предупреждение, а не issue: issue сделал бы mapping «не готовым» и
        # запретил бы сам вызов, а решение о матчинге принимает CRM, не мы.
        # Но знать об этом надо: без региона ответ гарантированно придёт с
        # category=null, и раньше это выглядело как «клиент не подошёл», а не
        # как потерянное поле. Город анкета спрашивает всегда, однако DaData
        # может не разобрать ответ, и тогда локации не будет.
        logger.warning(
            "CRM classification: регион клиента не определён, "
            "категория придёт пустой"
        )

    pledged = get_fact(facts, "assets.vehicle.pledged")
    try:
        request = FirstQualificationRequest(
            SUMM=get_fact(facts, "request.amount"),
            BIRTHDATE=_crm_birthdate(facts),
            UF_CRM_1452153725=registration_address,
            UF_CREDIT_HISTORY=_derived_credit_history(facts, enums),
            UF_CRM_LOAN_OVERDUE_DURATION=enums.overdue.get(
                get_fact(facts, "debts.overdue_duration")
            ),
            UF_DEBT_SUMM=get_fact(facts, "debts.total"),
            UF_CRM_1460622474=enums.employment.get(
                get_fact(facts, "employment.type")
            ),
            UF_CRM_1461049862=_crm_income(facts),
            UF_REALTY_CC_4_22=_assets_availability(
                get_fact(facts, "assets.has_realty")
            ),
            UF_CRM_1452154011=_supported_enum(
                get_fact(facts, f"{realty_root}.kind"),
                _REALTY_TYPES,
            ),
            CHAT_REALTY_REGION=_location_text(
                facts,
                realty_root,
                prefer_locality=True,
            ),
            CHAT_OWNER=_chat_owner(facts, realty_root),
            UF_CRM_RESTRICTION=enums.realty_restriction.get(
                get_fact(facts, f"{realty_root}.restriction")
            ),
            UF_HAVE_AUTO=_car_availability(get_fact(facts, "assets.has_vehicle")),
            UF_CAR_PRODUCTION_YEAR_1=get_fact(facts, "assets.vehicle.year"),
            UF_CREDIT_AUTO_1=(
                enums.car_credit.get(pledged) if isinstance(pledged, bool) else None
            ),
        )
    except ValidationError as exc:
        return QualificationMapping(
            request=None,
            issues=(
                MappingIssue(
                    "invalid_payload",
                    "$",
                    f"CRM payload не прошёл локальную валидацию: {exc}",
                ),
            ),
        )
    return QualificationMapping(request=request, issues=())



def map_realty_qualification(
    facts: dict[str, Any],
) -> "RealtyMapping":
    """Первый объект для ветвителя диалога.

    Здесь намеренно НЕ используется `_qualifying_realty_root`: тот отдаёт
    приоритет второму объекту, а вопрос ветвителя — проходит ли ПЕРВЫЙ. Иначе
    после ответа клиента про вторую квартиру проверка начала бы спрашивать сама
    себя.

    Собственник уже несёт форму собственности: `_chat_owner` называет его только
    у частного объекта, а CRM по нему и считает объект частным.
    """
    root = "assets.realty"
    realty_type = _supported_enum(get_fact(facts, f"{root}.kind"), _REALTY_TYPES)
    region = _location_text(facts, root, prefer_locality=True)
    chat_owner = _chat_owner(facts, root)
    if realty_type is None or chat_owner is None or not region:
        return RealtyMapping(request=None)
    return RealtyMapping(
        request=RealtyQualificationRequest(
            UF_CRM_1452154011=realty_type,
            CHAT_REALTY_REGION=region,
            CHAT_OWNER=chat_owner,
        )
    )


@dataclass(frozen=True)
class RealtyMapping:
    request: "RealtyQualificationRequest | None"


def map_lead_intake(
    facts: dict[str, Any],
    *,
    form_filled_out: bool,
    site_consent_shown: bool,
    site_consent_answer: bool | None = None,
    enum_map: CrmEnumMap | None = None,
    attribution: dict[str, str] | None = None,
    consent_abandoned: bool = False,
    dialog: str | None = None,
) -> LeadIntakeMapping:
    """Строит lead-intake только из действительно собранных ответов."""
    # Отдельных ключей под второй объект в CRM нет: Андрей 12.08.2026 — «если
    # первый не подошёл, шлётся второй в ключах первого». Квалифицирующим его
    # выбирает тот же код, что и для first-qualification, иначе категорию
    # определит один объект, а в лиде окажется другой.
    realty_root = _qualifying_realty_root(facts)

    enums = enum_map or CrmEnumMap()
    issues: list[MappingIssue] = []
    # Метки необязательны: клиент мог прийти прямой ссылкой. Их отсутствие лид
    # не задерживает.
    marks = attribution or {}

    phone = _crm_phone(get_fact(facts, "client.phone"))
    if phone is None:
        issues.append(
            MappingIssue(
                "missing_fact",
                "client.phone",
                "Для создания лида обязателен телефон.",
            )
        )

    agree_partners: SitePartnerConsent | None
    if not form_filled_out:
        # Backend требует полностью не передавать поле у брошенной анкеты.
        agree_partners = None
    elif not site_consent_shown:
        agree_partners = SitePartnerConsent.NOT_REQUIRED
    elif site_consent_answer is True:
        agree_partners = SitePartnerConsent.YES
    elif site_consent_answer is False:
        agree_partners = SitePartnerConsent.NO
    elif consent_abandoned:
        # Вопрос задан, клиент ушёл не ответив. Молчание — не согласие, поэтому
        # в CRM уходит честное «нет». Раньше такой ветки не было вовсе: маппинг
        # возвращал issue, лид не собирался, и самый дорогой клиент — с
        # телефоном и пройденной анкетой — терялся целиком (аудит 19.08.2026).
        agree_partners = SitePartnerConsent.NO
    else:
        agree_partners = None
        issues.append(
            MappingIssue(
                "missing_fact",
                "consent.site_offer",
                "Шаг согласия показан, но ответ клиента ещё не получен.",
            )
        )

    # Тот же вывод, что и в квалификации: два маппера читают одни факты и
    # расходиться не имеют права.
    credit_history = _derived_credit_history(facts, enums)
    overdue_duration = _optional_fact_enum(
        facts,
        "debts.overdue_duration",
        enums.overdue,
        issues,
    )
    employment = _optional_fact_enum(
        facts,
        "employment.type",
        enums.employment,
        issues,
    )
    realty_restriction = _optional_fact_enum(
        facts,
        f"{realty_root}.restriction",
        enums.realty_restriction,
        issues,
    )

    has_realty = get_fact(facts, "assets.has_realty")
    has_vehicle = get_fact(facts, "assets.has_vehicle")
    # Долевая собственность, участок, земля — законные ответы анкеты (матрица:
    # kind="other", owners="others"), но кода в CRM у них нет. Контракт
    # заказчика на этот счёт однозначен: «Неподдерживаемый собственник/доля →
    # realty_owner = null», «category = null не отменяет создание лида». Раньше
    # здесь появлялся issue, а issue отменяет весь вызов lead-intake, и клиент,
    # прошедший анкету целиком и оставивший телефон, пропадал вместе с полем.
    # Так же поступает map_first_qualification — два маппера читают одни факты
    # и расходиться не имеют права.
    realty_kind = _supported_enum(get_fact(facts, f"{realty_root}.kind"), _REALTY_TYPES)
    chat_owner = _chat_owner(facts, realty_root)

    pledged = get_fact(facts, "assets.vehicle.pledged")
    car_credit = None
    if pledged is not None:
        car_credit = _lookup_value(
            pledged,
            "assets.vehicle.pledged",
            enums.car_credit,
            issues,
            label="UF_CREDIT_AUTO_1",
        )

    if issues:
        return LeadIntakeMapping(request=None, issues=tuple(issues))
    assert phone is not None
    full_name = _optional_text(get_fact(facts, "client.name"))
    display_name = _optional_text(get_fact(facts, "client.display_name"))
    last_name, first_name, second_name = _split_full_name(
        full_name,
        display_name=display_name,
    )

    try:
        request = LeadIntakeRequest(
            PHONE=phone,
            PHONE_DEAL=phone,
            UF_FROM_SITE_FILLED_OUT=1 if form_filled_out else 0,
            RETURN_RESPONSE=True,
            LAST_NAME=last_name,
            NAME=first_name,
            SECOND_NAME=second_name,
            FULL_NAME=full_name,
            BIRTHDATE=_crm_birthdate(facts),
            UF_CRM_1452153725=_crm_registration_address(facts),
            UF_CRM_1452153749=(
                _optional_text(get_fact(facts, "client.residence_address"))
                or _location_text(facts, "client")
            ),
            SUMM=get_fact(facts, "request.amount"),
            UF_CREDIT_HISTORY=credit_history,
            UF_CRM_LOAN_OVERDUE_DURATION=overdue_duration,
            UF_DEBT_SUMM=get_fact(facts, "debts.total"),
            UF_CRM_1460622474=employment,
            UF_REALTY_CC_4_22=_assets_availability(has_realty),
            UF_CRM_1452154011=realty_kind,
            CHAT_REALTY_REGION=_location_text(
                facts,
                realty_root,
                prefer_locality=True,
            ),
            CHAT_OWNER=chat_owner,
            UF_CRM_RESTRICTION=realty_restriction,
            UF_HAVE_AUTO=_car_availability(has_vehicle),
            UF_CAR_PRODUCTION_YEAR_1=get_fact(facts, "assets.vehicle.year"),
            # Поля нарочно накрест: у сущности CRM_LEAD они подписаны
            # наоборот — UF_CAR_BRAND_1 в карточке лида это «Модель авто»,
            # UF_CAR_MODEL_1 — «Марка авто» (у контакта и сделки подписи
            # прямые). Анкета сайта мапит так же (Leads::mapBitrix24:
            # auto_model_* с маркой → UF_CAR_MODEL_*); маппинг по смыслу имён
            # полей показывал в карточке марку и модель местами
            # (QA 27.08.2026: «шевроле круз» → Марка=круз, Модель=шевроле).
            UF_CAR_BRAND_1=_optional_text(get_fact(facts, "assets.vehicle.model")),
            UF_CAR_MODEL_1=_optional_text(get_fact(facts, "assets.vehicle.make")),
            UF_CREDIT_AUTO_1=car_credit,
            UF_CRM_1461049862=_crm_income(facts),
            UF_SITE_AGREE_PARTNERS=agree_partners,
            # Источник и канал фиксированные: лид пришёл из чата, чем бы ни была
            # реклама. Переменная часть — кампания и метки страницы.
            UTMSOURCE="crm",
            ADV_CHANNEL="bot",
            UTM_CAMPAIGN=marks.get("utm_campaign"),
            UTMKEY=marks.get("utm_term"),
            K50=marks.get("utm_content"),
            LANDING=marks.get("landing"),
            COMMENTS=dialog,
            GOOGLE_ANALYTICS_CLIENT_ID=marks.get("ga_client_id"),
            YANDEX_METRIKA_CLIENT_ID=marks.get("ym_client_id"),
            DEFAULT_EMAIL_TO=DEFAULT_EMAIL_TO,
            DEFAULT_EMAIL_FROM=DEFAULT_EMAIL_FROM,
        )
    except ValidationError as exc:
        return LeadIntakeMapping(
            request=None,
            issues=(
                MappingIssue(
                    "invalid_payload",
                    "$",
                    f"CRM lead payload не прошёл локальную валидацию: {exc}",
                ),
            ),
        )
    return LeadIntakeMapping(request=request, issues=())


def _split_full_name(
    full_name: str | None,
    *,
    display_name: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Разбирает ФИО: по подтверждённому имени, иначе позиционно, как анкета.

    CRM принимает ``FULL_NAME`` и компоненты ФИО независимо. Когда имя
    подтверждено отдельным фактом (``display_name``), компоненты собираются
    вокруг него — это точнее позиционного разбора при вводе «Имя Фамилия».
    Когда подтверждённого имени нет, действует порядок анкеты сайта
    («Фамилия Имя Отчество», Leads::mapBitrix24): её поле так и называется —
    «ФИО», и то же допущение она делает месяцами. Пустые компоненты хуже
    возможной перестановки: карточка лида в CRM выходила без фамилии и имени
    вовсе (QA 27.08.2026), хотя клиент назвал полное ФИО.
    """
    if full_name is None:
        return None, None, None
    parts = full_name.split()
    if len(parts) == 1:
        return None, parts[0], None

    # Многословное обращение не является подтверждённым именем. Ровно одно
    # совпадение защищает также от экзотического ФИО с повторяющимися словами.
    normalized_display = " ".join((display_name or "").split())
    if not normalized_display or " " in normalized_display:
        return _positional_split(parts)
    name_indexes = [
        index
        for index, part in enumerate(parts)
        if part.casefold() == normalized_display.casefold()
    ]
    if len(name_indexes) != 1:
        return _positional_split(parts)

    first_name = parts[name_indexes[0]]
    remaining = [part for index, part in enumerate(parts) if index != name_indexes[0]]
    if len(remaining) == 1:
        only_other = remaining[0]
        if _is_likely_patronymic(only_other):
            return None, first_name, only_other
        return only_other, first_name, None

    patronymics = [part for part in remaining if _is_likely_patronymic(part)]
    if len(remaining) == 2 and len(patronymics) == 1:
        second_name = patronymics[0]
        last_name = next(part for part in remaining if part != second_name)
        return last_name, first_name, second_name

    # Само имя уже подтверждено и может быть передано отдельно, но фамилию и
    # отчество при неоднозначном составе не угадываем.
    return None, first_name, None


def _positional_split(
    parts: list[str],
) -> tuple[str | None, str | None, str | None]:
    """Порядок «Фамилия Имя Отчество» — как у анкеты (Leads::mapBitrix24).

    Четвёртое и дальнейшие слова отбрасываются, как и там: полная строка всё
    равно уходит в ``FULL_NAME``.
    """
    return (
        parts[0],
        parts[1] if len(parts) > 1 else None,
        parts[2] if len(parts) > 2 else None,
    )


def _is_likely_patronymic(value: str) -> bool:
    """Распознаёт устойчивые окончания русских и тюркских отчеств."""
    normalized = value.casefold().replace("ё", "е")
    return normalized.endswith(
        (
            "ович",
            "евич",
            "инич",
            "овна",
            "евна",
            "инична",
            "оглы",
            "кызы",
        )
    )


def _optional_fact_enum(
    facts: dict[str, Any],
    path: str,
    mapping: dict[Any, Any],
    issues: list[MappingIssue],
) -> Any:
    raw = get_fact(facts, path)
    if raw is None or raw == "":
        return None
    return _lookup_value(raw, path, mapping, issues, label="CRM enum")


def _supported_enum(raw: Any, mapping: dict[str, Any]) -> Any:
    """Значение без кода в CRM — это пустое поле, а не отказ от лида.

    Применимо там, где backend сам разрешил присылать null (тип объекта,
    состав собственников). Для полей, где пустого значения быть не должно,
    остаётся строгий `_lookup_value` с issue.
    """
    if not isinstance(raw, str) or not raw:
        return None
    return mapping.get(raw)


def _lookup_value(
    raw: Any,
    path: str,
    mapping: dict[Any, Any],
    issues: list[MappingIssue],
    *,
    label: str,
) -> Any:
    if raw not in mapping:
        issues.append(
            MappingIssue(
                "unknown_enum",
                path,
                f"Backend не подтвердил {label} для внутреннего значения {raw!r}.",
            )
        )
        return None
    return mapping[raw]


def _location_text(
    facts: dict[str, Any],
    root_path: str,
    *,
    prefer_locality: bool = False,
) -> str | None:
    location = get_fact(facts, f"{root_path}.location")
    if isinstance(location, dict):
        if prefer_locality:
            candidate = (
                location.get("city_with_type")
                or location.get("settlement_with_type")
                or location.get("region_with_type")
            )
            region = location.get("region_with_type")
            if (
                isinstance(candidate, str)
                and isinstance(region, str)
                and region.strip()
                and region.strip() != candidate.strip()
            ):
                # Населённый пункт ВМЕСТЕ с регионом — как строка подсказки
                # DaData, которую шлёт анкета («Московская обл, г Красно-
                # знаменск»). Голый город терял область: RegionScopeResolver
                # CRM ищет «моск» в тексте, «г Краснознаменск» уезжал в scope
                # rf, и залоговое правило Мск/МО не совпадало — квартира в МО
                # ушла в «Потреб 2-й сорт» (лид 3475724, 01.09.2026). Улица и
                # дом сюда по-прежнему не попадают.
                candidate = f"{region.strip()}, {candidate.strip()}"
        else:
            candidate = (
                location.get("unrestricted_value")
                or location.get("display_name")
                or location.get("value")
            )
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()

    city = get_fact(facts, f"{root_path}.city")
    region = get_fact(facts, f"{root_path}.region")
    values = [
        value.strip()
        for value in (city, region)
        if isinstance(value, str) and value.strip()
    ]
    return ", ".join(dict.fromkeys(values)) or None


def _chat_owner(
    facts: dict[str, Any],
    root: str,
) -> Literal["CLIENT", "BOTH"] | None:
    """Собственник объекта для CRM — только у подтверждённо частного объекта.

    С 31.08.2026 CRM выводит форму собственности сама: нет поля
    UF_FORM_OF_OWNERSHIP_FLAT, но назван собственник — объект частный, 4671
    (crm-mbk, CrmLeadProfileMapper::toProfile). Кода муниципальной
    собственности у нас нет. Назвать собственника муниципального или
    непонятного объекта — значит объявить его частным и открыть залог, а
    backend этого прямо не разрешал: «Муниципальный объект нельзя превращать в
    частный одним наличием владельца». Состав собственников в лиде при этом
    теряется, но диалог целиком уезжает специалисту в COMMENTS.
    """
    owners = get_fact(facts, f"{root}.owners")
    if owners is None:
        return None
    code = _CHAT_OWNERS.get(owners) if isinstance(owners, str) else None
    if code is None:
        logger.warning(
            "CRM: состав собственников %r без кода CRM, поле уедет пустым",
            owners,
        )
        return None
    if get_fact(facts, f"{root}.ownership_form") != "private":
        return None
    return code


def _qualifying_realty_root(facts: dict[str, Any]) -> str:
    """Какой объект недвижимости представляет клиента в CRM.

    Отдельных ключей под второй объект нет: он занимает поля первого. Заказчик
    12.08.2026 подтвердил, что так и останется — данные вытесненного объекта в
    лид не попадают, и это принято осознанно. Значит подменять первый можно
    только тогда, когда второй действительно лучше: иначе в лиде останется
    объект, который ничего не даёт, а тот, что давал, пропадёт совсем.

    Правила backend-разработчика от 12.08.2026:

    - **тип обязан отличаться.** Если дом под залог не прошёл, второй дом тоже
      не пройдёт, а поля первого он перезапишет;
    - **регион обязателен, собственник вместе с ним.** Без них CRM не отнесёт
      объект ни к какой категории, и подмена только ухудшит лид.

    Половина второго объекта хуже, чем целый первый.
    """
    second = get_fact(facts, "assets.realty_2")
    if not isinstance(second, dict):
        return "assets.realty"

    has_region = any(
        isinstance(second.get(key), str) and second[key].strip()
        for key in ("city", "region", "region_class")
    )
    owners = second.get("owners")
    kind = second.get("kind")
    differs = kind is not None and kind != get_fact(facts, "assets.realty.kind")

    if has_region and owners and differs:
        return "assets.realty_2"
    return "assets.realty"


def _crm_phone(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return digits if 10 <= len(digits) <= 15 else None


def _crm_income(facts: dict[str, Any]) -> int | None:
    """Доход для CRM; ноль не отправляется вовсе.

    Прод 29.09.2026: у двух клиентов без официального дохода бот записал 0, и
    lead-intake отклонил весь лид (success=false по UF_CRM_1461049862).
    Андрей, 30.09.2026: «не присылать поле совсем, если ноль».
    """
    income = get_fact(facts, "employment.income")
    return income or None


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _birthdate_from_age(value: Any) -> str | None:
    """Первое января года рождения, когда точной даты нет.

    BIRTHDATE — обязательная графа карточки клиента в CRM, а точную дату
    спрашивает только форма на сайте. Виджет заказчика её не передаёт, поэтому
    у клиента из чата поле уходило пустым: менеджер получал лид без возраста
    вовсе (живой прогон заказчика 20.08.2026, «27 лет» — и пустая графа).

    В чате бот спрашивает полные годы, и этого достаточно для непротиворечивой
    даты. День и месяц — решение заказчика: «01 01 делай». Настоящая дата с
    формы, если она есть, всегда важнее выведенной.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    # Границы те же, что у разбора возраста: мусор не должен превращаться в
    # правдоподобную дату, которую потом никто не отличит от настоящей.
    if not 18 <= value <= 120:
        return None
    return f"01-01-{date.today().year - value}"


def _crm_date(value: Any) -> str | None:
    """YYYY-MM-DD из HTML date → требуемый CRM формат DD-MM-YYYY."""
    normalized = _optional_text(value)
    if normalized is None:
        return None
    iso_match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", normalized)
    if iso_match:
        year, month, day = iso_match.groups()
        return f"{day}-{month}-{year}"
    if re.fullmatch(r"\d{2}-\d{2}-\d{4}", normalized):
        return normalized
    # Привычное «10.05.1985» и «10 мая 1985» разбираются тем же кодом, что и
    # ответ в чате: формат поля — не повод терять дату.
    if iso := normalize_birthdate(normalized):
        year, month, day = iso.split("-")
        return f"{day}-{month}-{year}"
    # Совсем непонятное значение уходит пустым. Раньше оно проезжало как есть
    # и валило проверку всего payload: лид не уходил в CRM вовсе из-за одной
    # необязательной графы.
    logger.warning("CRM lead: дата рождения непонятного формата, поле пропущено")
    return None


def _crm_birthdate(facts: dict[str, Any]) -> str | None:
    """Дата рождения для CRM: настоящая с формы, иначе выведенная из возраста.

    Отдельного поля возраста у CRM нет: возраст она вычисляет из этой даты
    (CrmLeadProfileMapper::age → Carbon::parse(BIRTHDATE)->age). Одна функция
    на квалификацию и лид — иначе два расчёта категории увидят разный возраст.
    """
    return _crm_date(get_fact(facts, "client.birthdate")) or _birthdate_from_age(
        get_fact(facts, "client.age")
    )


def _crm_registration_address(facts: dict[str, Any]) -> str | None:
    """Адрес прописки UF_CRM_1452153725 — по нему CRM определяет регион клиента.

    Заполнялся он только с формы сайта: у клиента из чата поле уходило пустым,
    и регион в карточке не определялся (замечание заказчика 20.08.2026).
    Полного адреса в чате нет и быть не может — бот спрашивает город и регион,
    — поэтому в запас идёт нормализованный населённый пункт: улицу не
    выдумываем, а регион CRM из этой строки читает.

    Квалификация и лид берут поле одним кодом. До 14.09.2026 квалификация
    ставила населённый пункт впереди адреса формы — два расчёта категории могли
    прочесть разные строки.
    """
    return _optional_text(
        get_fact(facts, "client.registration_address")
    ) or _location_text(facts, "client")


def _assets_availability(value: Any) -> AssetsAvailability | None:
    if value is True:
        return AssetsAvailability.YES
    if value is False:
        return AssetsAvailability.NO
    return None


def _car_availability(value: Any) -> CarAvailability | None:
    if value is True:
        return CarAvailability.YES
    if value is False:
        return CarAvailability.NO
    return None
