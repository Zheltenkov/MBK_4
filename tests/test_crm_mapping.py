"""Детерминированный mapping фактов диалога в first-qualification."""
from __future__ import annotations

import pytest
from datetime import date

from delivery.crm_mapping import map_first_qualification, map_lead_intake, map_realty_qualification


def _complete_facts() -> dict:
    return {
        "request": {"amount": 1_500_000},
        "client": {
            "age": 42,
            "region": "Москва",
            "registration_address": "г Москва, ул Ленина, д 1, кв 5",
            "location": {
                "unrestricted_value": "г Москва, ул Ленина, д 1",
            },
        },
        "debts": {
            "total": 900_000,
            "credit_history": "no_overdue",
            "overdue_duration": "no_overdue",
        },
        "assets": {
            "has_realty": True,
            "realty": {
                "region": "Москва",
                "kind": "flat",
                "owners": "client_only",
                "ownership_form": "private",
                "location": {
                    "region_with_type": "г Москва",
                },
            },
            "has_vehicle": False,
        },
        "employment": {"type": "official"},
    }


def test_complete_known_mapping_builds_api_request():
    mapping = map_first_qualification(_complete_facts())

    assert mapping.ready is True
    assert mapping.request is not None
    payload = mapping.request.as_api_dict()
    assert payload["UF_CRM_1452154011"] == 190
    assert payload["CHAT_OWNER"] == "CLIENT"
    assert payload["UF_REALTY_CC_4_22"] == 4697
    assert payload["UF_HAVE_AUTO"] == 4696
    assert payload["UF_CAR_PRODUCTION_YEAR_1"] is None
    assert payload["UF_CREDIT_HISTORY"] == 9255
    # «Нет просрочки» уходит явным 22606, как у анкеты сайта.
    assert payload["UF_CRM_LOAN_OVERDUE_DURATION"] == 22606
    # Найм — словарём анкеты (3082), а не «официально» (376).
    assert payload["UF_CRM_1460622474"] == 3082


def test_legacy_ambiguous_employment_is_sent_as_null_not_a_guess():
    facts = _complete_facts()
    facts["employment"]["type"] = "other"
    mapping = map_first_qualification(facts)

    assert mapping.ready is True
    assert mapping.request is not None
    assert mapping.request.employment is None


def test_vehicle_credit_status_uses_confirmed_enum():
    facts = _complete_facts()
    facts["assets"]["has_vehicle"] = True
    facts["assets"]["vehicle"] = {"year": 2018, "pledged": False}

    mapping = map_first_qualification(facts)

    assert mapping.ready is True
    assert mapping.request is not None
    assert mapping.request.car_year_1 == 2018
    assert mapping.request.car_credit_1 == 4600


def test_no_realty_does_not_require_realty_details():
    facts = _complete_facts()
    facts["assets"] = {
        "has_realty": False,
        "has_vehicle": False,
    }

    mapping = map_first_qualification(facts)

    assert mapping.ready is True
    assert mapping.request is not None
    assert mapping.request.realty_type is None
    assert mapping.request.realty_region is None


def test_missing_answers_are_serialized_as_null():
    mapping = map_first_qualification({})

    assert mapping.ready is True
    assert mapping.request is not None
    payload = mapping.request.as_api_dict()
    assert payload
    assert all(value is None for value in payload.values())


def test_second_realty_object_has_priority_for_qualification():
    facts = _complete_facts()
    facts["assets"]["realty_2"] = {
        "region": "Казань",
        "kind": "commercial",
        "owners": "client_and_spouse",
        "ownership_form": "private",
    }

    mapping = map_first_qualification(facts)

    assert mapping.request is not None
    assert mapping.request.realty_region == "Казань"
    assert mapping.request.realty_type == 3075
    assert mapping.request.chat_owner == "BOTH"


def test_unsupported_realty_owner_and_type_are_sent_as_null():
    facts = _complete_facts()
    facts["assets"]["realty"]["kind"] = "other"
    facts["assets"]["realty"]["owners"] = "others"

    mapping = map_first_qualification(facts)

    assert mapping.ready is True
    assert mapping.request is not None
    assert mapping.request.realty_type is None
    assert mapping.request.chat_owner is None


def test_municipal_realty_is_never_mapped_as_private():
    facts = _complete_facts()
    facts["assets"]["realty"]["ownership_form"] = "municipal"

    mapping = map_first_qualification(facts)

    assert mapping.request is not None
    # Собственник муниципального объекта не называется: CRM сделала бы объект
    # частным по одному этому полю.
    assert mapping.request.chat_owner is None


def test_completed_lead_maps_collected_fields_and_not_required_consent():
    facts = _complete_facts()
    facts["client"]["name"] = "Иванов Иван Иванович"
    facts["client"]["display_name"] = "Иван"
    facts["client"]["phone"] = "+7 999 123-45-67"
    facts["assets"]["realty"]["restriction"] = "none"

    mapping = map_lead_intake(
        facts,
        form_filled_out=True,
        site_consent_shown=False,
    )

    assert mapping.ready is True
    assert mapping.request is not None
    payload = mapping.request.as_api_dict(exclude_none=True)
    assert payload["PHONE"] == "79991234567"
    assert payload["PHONE_DEAL"] == "79991234567"
    assert payload["LAST_NAME"] == "Иванов"
    assert payload["NAME"] == "Иван"
    assert payload["SECOND_NAME"] == "Иванович"
    assert payload["FULL_NAME"] == "Иванов Иван Иванович"
    assert payload["UF_FROM_SITE_FILLED_OUT"] == 1
    assert payload["UF_SITE_AGREE_PARTNERS"] == 23002
    assert payload["UF_CREDIT_HISTORY"] == 9255
    assert payload["UF_CRM_1460622474"] == 3082
    assert payload["UF_REALTY_CC_4_22"] == 4697
    assert payload["UF_CRM_RESTRICTION"] == 21003
    assert payload["UF_HAVE_AUTO"] == 4696


def test_lead_mapping_recognizes_name_first_without_swapping_crm_fields():
    facts = _complete_facts()
    facts["client"].update(
        {
            "name": "Иван Иванов Иванович",
            "display_name": "Иван",
            "phone": "+79991234567",
        }
    )

    mapping = map_lead_intake(
        facts,
        form_filled_out=True,
        site_consent_shown=False,
    )

    assert mapping.request is not None
    payload = mapping.request.as_api_dict(exclude_none=True)
    assert payload["LAST_NAME"] == "Иванов"
    assert payload["NAME"] == "Иван"
    assert payload["SECOND_NAME"] == "Иванович"
    assert payload["FULL_NAME"] == "Иван Иванов Иванович"


def test_lead_mapping_recognizes_first_name_in_two_word_input():
    facts = _complete_facts()
    facts["client"].update(
        {
            "name": "Иван Петров",
            "display_name": "Иван",
            "phone": "+79991234567",
        }
    )

    mapping = map_lead_intake(
        facts,
        form_filled_out=True,
        site_consent_shown=False,
    )

    assert mapping.request is not None
    payload = mapping.request.as_api_dict(exclude_none=True)
    assert payload["LAST_NAME"] == "Петров"
    assert payload["NAME"] == "Иван"
    assert payload["FULL_NAME"] == "Иван Петров"


def test_unconfirmed_fio_splits_positionally_like_the_anketa():
    """Без подтверждённого имени действует порядок анкеты: Фамилия Имя.

    QA 27.08.2026: пустые компоненты оставляли карточку лида в CRM без
    фамилии и имени вовсе — хуже возможной перестановки, которую то же
    допущение анкеты делает месяцами.
    """
    facts = _complete_facts()
    facts["client"].update(
        {
            "name": "Иван Иванов",
            "phone": "+79991234567",
        }
    )

    mapping = map_lead_intake(
        facts,
        form_filled_out=True,
        site_consent_shown=False,
    )

    assert mapping.request is not None
    payload = mapping.request.as_api_dict(exclude_none=True)
    assert payload["FULL_NAME"] == "Иван Иванов"
    assert payload["LAST_NAME"] == "Иван"
    assert payload["NAME"] == "Иванов"
    assert "SECOND_NAME" not in payload


def test_nickname_falls_back_to_the_positional_anketa_order():
    """Обращение «Ваня» не находится в ФИО — разбор идёт порядком анкеты."""
    facts = _complete_facts()
    facts["client"].update(
        {
            "name": "Иванов Иван Иванович",
            "display_name": "Ваня",
            "phone": "+79991234567",
        }
    )

    mapping = map_lead_intake(
        facts,
        form_filled_out=True,
        site_consent_shown=False,
    )

    assert mapping.request is not None
    payload = mapping.request.as_api_dict(exclude_none=True)
    assert payload["FULL_NAME"] == "Иванов Иван Иванович"
    assert payload["LAST_NAME"] == "Иванов"
    assert payload["NAME"] == "Иван"
    assert payload["SECOND_NAME"] == "Иванович"


def test_site_consent_answer_maps_to_yes_and_no():
    facts = _complete_facts()
    facts["client"]["phone"] = "+79991234567"

    accepted = map_lead_intake(
        facts,
        form_filled_out=True,
        site_consent_shown=True,
        site_consent_answer=True,
    )
    declined = map_lead_intake(
        facts,
        form_filled_out=True,
        site_consent_shown=True,
        site_consent_answer=False,
    )

    assert accepted.request is not None
    assert declined.request is not None
    assert accepted.request.agree_partners == 23000
    assert declined.request.agree_partners == 23001


def test_shown_consent_without_answer_blocks_completed_lead():
    facts = _complete_facts()
    facts["client"]["phone"] = "+79991234567"

    mapping = map_lead_intake(
        facts,
        form_filled_out=True,
        site_consent_shown=True,
    )

    assert mapping.ready is False
    assert any(issue.path == "consent.site_offer" for issue in mapping.issues)


def test_abandoned_lead_omits_consent_even_if_step_was_shown():
    mapping = map_lead_intake(
        {"client": {"phone": "+79991234567"}},
        form_filled_out=False,
        site_consent_shown=True,
    )

    assert mapping.ready is True
    assert mapping.request is not None
    payload = mapping.request.as_api_dict(exclude_none=True)
    assert payload["UF_FROM_SITE_FILLED_OUT"] == 0
    assert "UF_SITE_AGREE_PARTNERS" not in payload


def test_new_questionnaire_fields_reach_the_lead():
    """Доход и марка/модель — новые вопросы анкеты. Места под них в контракте
    Андрея были давно, но ничего их не заполняло."""
    facts = _complete_facts()
    facts["client"]["name"] = "Иванов Иван Иванович"
    facts["client"]["phone"] = "+7 999 123-45-67"
    facts["employment"]["income"] = 150_000
    facts["assets"]["has_vehicle"] = True
    facts["assets"]["vehicle"] = {
        "year": 2018,
        "make": "Kia",
        "model": "Rio",
        "pledged": False,
    }

    mapping = map_lead_intake(
        facts,
        form_filled_out=True,
        site_consent_shown=False,
    )

    assert mapping.request is not None
    payload = mapping.request.as_api_dict(exclude_none=True)
    assert payload["UF_CRM_1461049862"] == 150_000
    # Накрест: у CRM_LEAD подписи полей перевёрнуты (UF_CAR_MODEL_1 в
    # карточке лида — «Марка авто»), анкета мапит так же.
    assert payload["UF_CAR_MODEL_1"] == "Kia"
    assert payload["UF_CAR_BRAND_1"] == "Rio"
    assert payload["UF_CAR_PRODUCTION_YEAR_1"] == 2018


def test_amounts_crm_expects_as_strings_are_serialised_as_strings():
    """Пять сумм CRM принимает только строкой, остальные числа — числами.

    Проверено прямой пробой их валидатора 11.08.2026 (запрос без телефона,
    поэтому лид не создавался):
      UF_DEBT_SUMM, UF_CRM_MORTGAGE_BALANCE,
      UF_CRM_CAR_LOAN_BALANCE_1/2/3 — «must be a string».

    До этого поля объявлялись int, и lead-intake отбивал всю заявку целиком.
    Живые прогоны этого не показывали: там debts.total держался reducer'ом как
    приблизительный и в payload не попадал вовсе. Первый же клиент, чётко
    назвавший сумму долга, лид бы не создал.
    """
    from delivery.crm_contracts import LeadIntakeRequest

    payload = LeadIntakeRequest(
        PHONE="79990000000",
        PHONE_DEAL="79990000000",
        UF_FROM_SITE_FILLED_OUT=1,
        UF_DEBT_SUMM=0,
        UF_CRM_MORTGAGE_BALANCE=1_500_000,
        UF_CRM_CAR_LOAN_BALANCE_1=0,
        UF_CRM_CAR_LOAN_BALANCE_2=250_000,
        UF_CRM_CAR_LOAN_BALANCE_3=None,
        SUMM=700_000,
    ).as_api_dict(exclude_none=True)

    assert payload["UF_DEBT_SUMM"] == "0"
    assert payload["UF_CRM_MORTGAGE_BALANCE"] == "1500000"
    assert payload["UF_CRM_CAR_LOAN_BALANCE_1"] == "0"
    assert payload["UF_CRM_CAR_LOAN_BALANCE_2"] == "250000"
    assert "UF_CRM_CAR_LOAN_BALANCE_3" not in payload
    # Остальные суммы их валидатор принимает числами и менять их нельзя.
    assert payload["SUMM"] == 700_000


def test_residence_region_is_sent_for_a_chat_only_client():
    """Без региона CRM не относит клиента ни к какой категории вовсе.

    residence_region брался только из client.registration_address — поля с формы
    сайта, которое в чистом чате не заполняется никогда. Проверено прямой пробой
    11.08.2026: тот же профиль без региона даёт matched=false, с регионом —
    category 4705, vertical auto_pledge и ссылку на info.mbk.ru.

    Анкета город спрашивает и DaData его нормализует, то есть данные у нас были
    всё это время — просто не доезжали.
    """
    from delivery.crm_mapping import map_first_qualification

    mapping = map_first_qualification(
        {
            "client": {"age": 40, "region_class": "moscow_mo", "city": "Москва"},
            "request": {"amount": 700_000},
            "debts": {"has_active_credits": False, "total": 0},
            "employment": {"type": "hired", "income": 120_000},
            "assets": {"has_realty": False, "has_vehicle": True,
                       "vehicle": {"year": 2019, "pledged": False}},
        }
    )

    assert mapping.request is not None
    assert mapping.request.registration_address == "Москва"


def test_registration_address_is_used_when_there_is_no_locality():
    """Клиент с формы сайта: адрес есть, разобранной локации нет."""
    from delivery.crm_mapping import map_first_qualification

    mapping = map_first_qualification(
        {
            "client": {
                "age": 40,
                "region_class": "moscow_mo",
                "registration_address": "г Москва, ул Тверская, д 1",
            },
            "request": {"amount": 700_000},
            "debts": {"has_active_credits": False, "total": 0},
            "employment": {"type": "hired"},
            "assets": {"has_realty": False, "has_vehicle": False},
        }
    )

    assert mapping.request is not None
    assert mapping.request.registration_address == "г Москва, ул Тверская, д 1"


def test_missing_region_is_logged_but_does_not_block_the_call(caplog):
    """Без региона классификация возвращает пустоту — это должно быть видно.

    11.08.2026: тот же профиль без residence_region даёт matched=false и
    category=null. Раньше это происходило молча и выглядело как «клиент не
    подошёл», а не как потерянное поле. Анкета город спрашивает всегда, но
    DaData может не разобрать ответ, и тогда локации не будет.
    """
    import logging

    from delivery.crm_mapping import map_first_qualification

    with caplog.at_level(logging.WARNING):
        mapping = map_first_qualification(
            {
                "client": {"age": 40, "region_class": "moscow_mo"},
                "request": {"amount": 700_000},
                "debts": {"has_active_credits": False, "total": 0},
                "employment": {"type": "hired"},
                "assets": {"has_realty": False, "has_vehicle": False},
            }
        )

    assert "регион клиента не определён" in caplog.text
    # Запрос всё равно уходит: решение о матчинге принимает CRM, а не мы, и
    # issue здесь запретил бы вызов вовсе.
    assert mapping.ready is True


def test_region_present_produces_no_warning(caplog):
    from delivery.crm_mapping import map_first_qualification

    map_first_qualification(
        {
            "client": {"age": 40, "region_class": "moscow_mo", "city": "Москва"},
            "request": {"amount": 700_000},
            "debts": {"has_active_credits": False, "total": 0},
            "employment": {"type": "hired"},
            "assets": {"has_realty": False, "has_vehicle": False},
        }
    )

    assert "регион клиента не определён" not in caplog.text


def test_ready_for_bankruptcy_is_sent_the_way_the_anketa_sends_it():
    """Прод 27.08.2026: 9258 не входит в условие «Кредитная история» правил БФЛ.

    Клиент, прямо попросивший банкротство, уходил в «Не прошёл квалификацию».
    Поле выводится из ответов о просрочке, как в анкете сайта: словарь — только
    «нет просрочек» (9255) и «есть просрочки» (9257).
    """
    facts = _complete_facts()
    facts["debts"]["has_active_credits"] = True
    facts["debts"]["credit_history"] = "ready_bankruptcy"
    facts["debts"]["overdue_duration"] = "over_6m"

    payload = map_first_qualification(facts).request.as_api_dict()

    assert payload["UF_CREDIT_HISTORY"] == 9257


def test_past_overdue_without_current_one_is_a_clean_history():
    facts = _complete_facts()
    facts["debts"]["credit_history"] = "past_overdue"
    facts["debts"]["overdue_duration"] = "no_overdue"

    payload = map_first_qualification(facts).request.as_api_dict()

    assert payload["UF_CREDIT_HISTORY"] == 9255


def test_no_active_credits_beats_any_credit_history_fact():
    facts = _complete_facts()
    facts["debts"]["has_active_credits"] = False
    facts["debts"]["credit_history"] = "ready_bankruptcy"
    facts["debts"].pop("overdue_duration", None)

    payload = map_first_qualification(facts).request.as_api_dict()

    assert payload["UF_CREDIT_HISTORY"] == 9255


def test_employment_collapses_to_the_anketa_vocabulary():
    """Матрица видела только найм/самозанятый/не работаю — как в анкете."""
    expectations = {
        "official": 3082,
        "hired": 3082,
        "unofficial": 3082,
        "business": 377,
        "self_employed": 377,
        "retired": 378,
        "unemployed": 378,
    }
    for fact_value, crm_value in expectations.items():
        facts = _complete_facts()
        facts["employment"]["type"] = fact_value
        payload = map_first_qualification(facts).request.as_api_dict()
        assert payload["UF_CRM_1460622474"] == crm_value, fact_value


def test_lead_carries_dialog_analytics_and_email_notification():
    """Паритет с анкетой: COMMENTS, GA/YM и почтовая нотификация Битрикса."""
    facts = _complete_facts()
    facts["client"]["phone"] = "+79991234567"

    mapping = map_lead_intake(
        facts,
        form_filled_out=True,
        site_consent_shown=False,
        attribution={"ga_client_id": "GA1.2.111.222", "ym_client_id": "1712345"},
        dialog="Клиент: хочу кредит\nБот: Разберёмся",
    )

    assert mapping.request is not None
    payload = mapping.request.as_api_dict(exclude_none=True)
    assert payload["COMMENTS"] == "Клиент: хочу кредит\nБот: Разберёмся"
    assert payload["GOOGLE_ANALYTICS_CLIENT_ID"] == "GA1.2.111.222"
    assert payload["YANDEX_METRIKA_CLIENT_ID"] == "1712345"
    assert payload["DEFAULT_EMAIL_TO"] == "leads@mbk-credit.ru"
    assert payload["DEFAULT_EMAIL_FROM"] == "robot@mbk.ru"


def test_dialog_transcript_formats_roles_and_trims_the_beginning():
    from delivery.crm_mapping import _TRANSCRIPT_LIMIT, dialog_transcript

    history = [
        {"role": "assistant", "content": "Добрый день!"},
        {"role": "user", "content": "оформить банкротство"},
        {"role": "assistant", "content": ""},
        "мусор",
    ]
    assert dialog_transcript(history) == (
        "Бот: Добрый день!\nКлиент: оформить банкротство"
    )
    assert dialog_transcript([]) is None
    assert dialog_transcript(None) is None

    long_history = [
        {"role": "user", "content": f"реплика {i} " + "х" * 200}
        for i in range(100)
    ]
    text = dialog_transcript(long_history)
    assert text is not None and len(text) <= _TRANSCRIPT_LIMIT + 1
    assert text.startswith("…")
    assert text.endswith("х" * 10)


def test_locality_travels_with_its_region_like_the_dadata_suggestion():
    """Лид 3475724 (01.09.2026): «г Краснознаменск» без области уезжал в rf.

    RegionScopeResolver CRM определяет Мск/МО подстрокой «моск» — регион
    должен ехать вместе с городом, как в подсказке DaData у анкеты.
    """
    facts = _complete_facts()
    facts["assets"]["realty"]["location"] = {
        "city_with_type": "г Краснознаменск",
        "region_with_type": "Московская обл",
    }

    payload = map_first_qualification(facts).request.as_api_dict()

    assert payload["CHAT_REALTY_REGION"] == "Московская обл, г Краснознаменск"


def test_moscow_city_is_not_duplicated_as_its_own_region():
    facts = _complete_facts()
    facts["assets"]["realty"]["location"] = {
        "city_with_type": "г Москва",
        "region_with_type": "г Москва",
    }

    payload = map_first_qualification(facts).request.as_api_dict()

    assert payload["CHAT_REALTY_REGION"] == "г Москва"


@pytest.mark.parametrize("ownership_form", ["municipal", "other", None])
def test_lead_names_no_owner_without_confirmed_private_ownership(ownership_form):
    """С 31.08.2026 CRM считает объект частным, как только назван собственник.

    CrmLeadProfileMapper::toProfile: форма собственности берётся из
    UF_FORM_OF_OWNERSHIP_FLAT, а без него — 4671 «частная», если собственник
    указан. Кода муниципальной собственности у нас нет, поэтому собственник
    муниципального или непонятного объекта открыл бы клиенту залог. Backend:
    «Муниципальный объект нельзя превращать в частный одним наличием владельца».
    """
    facts = _complete_facts()
    facts["client"]["phone"] = "+79991234567"
    if ownership_form is None:
        facts["assets"]["realty"].pop("ownership_form")
    else:
        facts["assets"]["realty"]["ownership_form"] = ownership_form

    mapping = map_lead_intake(facts, form_filled_out=True, site_consent_shown=False)

    assert mapping.ready is True
    assert mapping.request is not None
    payload = mapping.request.as_api_dict(exclude_none=True)
    assert "CHAT_OWNER" not in payload
    # Остальное об объекте уезжает: тип и регион специалисту нужны.
    assert payload["UF_CRM_1452154011"] == 190


def test_lead_names_the_owner_of_private_realty():
    facts = _complete_facts()
    facts["client"]["phone"] = "+79991234567"

    mapping = map_lead_intake(facts, form_filled_out=True, site_consent_shown=False)

    assert mapping.request is not None
    assert mapping.request.as_api_dict(exclude_none=True)["CHAT_OWNER"] == "CLIENT"


def test_qualification_speaks_the_bitrix_names_crm_reads():
    """31.08.2026 CRM перестала понимать анкетные ключи квалификации.

    crm-mbk, коммит 18490d4d: FirstQualificationController строит профиль из
    Битрикс-имён, «отдельного анкетного формата у квалификации нет». Бот слал
    `amount`, `age`, `employment` — CRM их не видела, профиль выходил пустым, и
    13 из 13 квалификаций за 11-14.09.2026 вернули category=null.
    """
    mapping = map_first_qualification(_complete_facts())

    assert mapping.request is not None
    payload = mapping.request.as_api_dict()
    assert payload["SUMM"] == 1_500_000
    assert payload["UF_DEBT_SUMM"] == "900000"
    assert payload["UF_CRM_1460622474"] == 3082
    assert payload["UF_CRM_1452153725"] == "г Москва, ул Ленина, д 1, кв 5"
    assert not {"amount", "age", "debt", "employment", "residence_region"} & set(payload)


def test_age_reaches_crm_as_a_birthdate():
    """Поля возраста у CRM нет: она вычисляет его из BIRTHDATE (Carbon::parse)."""
    facts = _complete_facts()

    mapping = map_first_qualification(facts)
    assert mapping.request is not None
    assert mapping.request.birthdate == f"01-01-{date.today().year - 42}"

    facts["client"]["birthdate"] = "1984-05-10"
    mapping = map_first_qualification(facts)
    assert mapping.request is not None
    assert mapping.request.birthdate == "10-05-1984"


def test_qualification_reads_the_region_from_the_same_string_as_the_lead():
    """Адрес формы важнее населённого пункта — в обоих расчётах CRM одинаково."""
    facts = _complete_facts()
    facts["client"]["phone"] = "+79991234567"

    qualification = map_first_qualification(facts)
    lead = map_lead_intake(facts, form_filled_out=True, site_consent_shown=False)

    assert qualification.request is not None and lead.request is not None
    assert (
        qualification.request.registration_address
        == lead.request.registration_address
        == "г Москва, ул Ленина, д 1, кв 5"
    )


def test_realty_check_speaks_the_bitrix_names_of_the_object():
    """realty-qualification с 31.08.2026 строит профиль тем же маппером, что приём.

    FirstQualificationController::realtyCheck: «Вход — те же Битрикс-поля
    объекта, что на приёме (UF_CRM_1452154011 / CHAT_REALTY_REGION /
    CHAT_OWNER)». Форму собственности CRM выводит из собственника сама.
    """
    mapping = map_realty_qualification(_complete_facts())

    assert mapping.request is not None
    assert mapping.request.as_api_dict() == {
        "UF_CRM_1452154011": 190,
        "CHAT_REALTY_REGION": "г Москва",
        "CHAT_OWNER": "CLIENT",
    }


@pytest.mark.parametrize(
    "field, value",
    [
        ("ownership_form", "municipal"),
        ("owners", "others"),
        ("kind", "other"),
    ],
)
def test_realty_check_is_not_sent_for_an_object_crm_cannot_read(field, value):
    facts = _complete_facts()
    facts["assets"]["realty"][field] = value

    assert map_realty_qualification(facts).request is None


@pytest.mark.parametrize("income", [0, None])
def test_zero_income_is_not_sent_at_all(income):
    """Прод 29.09.2026: два лида без официального дохода CRM отклонила —
    lead-intake ответил success=false по UF_CRM_1461049862 = 0. Андрей
    30.09.2026: «не присылать поле совсем, если ноль»."""
    facts = _complete_facts()
    facts["client"]["name"] = "Иванов Иван Иванович"
    facts["client"]["phone"] = "+7 999 123-45-67"
    facts["employment"]["income"] = income

    lead = map_lead_intake(facts, form_filled_out=True, site_consent_shown=False)
    qualification = map_first_qualification(facts)

    assert lead.request is not None and qualification.request is not None
    assert "UF_CRM_1461049862" not in lead.request.as_api_dict(exclude_none=True)
    assert "UF_CRM_1461049862" not in qualification.request.as_api_dict(exclude_none=True)


def test_real_income_is_still_sent():
    facts = _complete_facts()
    facts["employment"]["income"] = 70_000

    qualification = map_first_qualification(facts)

    assert qualification.request is not None
    assert qualification.request.as_api_dict()["UF_CRM_1461049862"] == 70_000
