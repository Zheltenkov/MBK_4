# Квалификация CRM на Битрикс-именах полей — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** CRM снова видит профиль клиента в `first-qualification` и `realty-qualification` и возвращает категорию, а не `null`.

**Architecture:** Запрос квалификации становится срезом запроса лида. Поля `FirstQualificationRequest` совпадают с полями `LeadIntakeRequest` по имени атрибута, Битрикс-алиасу и типу. Значения считают те же функции `delivery/crm_mapping.py`, что и для лида. Собственник (`CHAT_OWNER`) передаётся только при подтверждённой частной собственности, одинаково во всех трёх запросах. Инвариантные тесты не дают квалификации и лиду разойтись.

**Tech Stack:** Python 3.13, pydantic 2, pytest, ruff.

**Spec:** отдельной спеки нет. План опирается на разбор кода заказчика 14.09.2026 (чтение `mbk/crm-mbk` и `mbk/mbk-site`, ничего не менялось) и на решение человека 14.09.2026 «Да исправляй». Факты разбора — `docs/agent-memory/facts/crm-anketa-dictionary.md` и `docs/agent-memory/SESSION.md`.

## Что выяснено в коде заказчика

- `crm-mbk`, коммит 18490d4d от 31.08.2026 («#97878 Новая партнёрка»), удалил анкетный формат входа квалификации. `FirstQualificationController`: «ЕДИНЫЙ СЛОВАРЬ ПОЛЕЙ: вход — те же Битрикс-имена, что у приёма (lead-intake)… Отдельного «анкетного» формата у квалификации нет». Сайт (`mbk-site`, `ClassificationRepository`) шлёт в квалификацию те же поля, что и в лид.
- Профиль строит `CrmLeadProfileMapper::toProfile`. Поля и что из них берётся:

  | Поле | Что из него берётся |
  |---|---|
  | `SUMM` | сумма |
  | `BIRTHDATE` | возраст: `Carbon::parse(...)->age` |
  | `UF_DEBT_SUMM` | долг |
  | `UF_REALTY_CC_4_22` | есть недвижимость, если `4697` |
  | `UF_CRM_1452153725` | регион проживания: Москва/МО или РФ |
  | `UF_CRM_1452154011` | тип объекта |
  | `CHAT_REALTY_REGION` | регион объекта |
  | `CHAT_OWNER` | собственник: `CLIENT` → 4673, `BOTH` → 4675 |
  | `UF_FORM_OF_OWNERSHIP_FLAT` | форма собственности; если поля нет, а собственник назван, — 4671 «частная» |
  | `UF_HAVE_AUTO` | есть авто |
  | `UF_CAR_PRODUCTION_YEAR_i`, `UF_CREDIT_AUTO_i` | авто: год и кредит |
  | `UF_CREDIT_HISTORY` | кредитная история |
  | `UF_CRM_LOAN_OVERDUE_DURATION` | срок просрочки |
  | `UF_CRM_1460622474` | занятость |
- `RuleMatcher`: «Условие, не заданное в правиле, ограничением не считается. Заданное условие при отсутствующем значении профиля — НЕ выполнено (строго)». Исключение — наборы, где правило разрешает «Пустое поле».
- Предикт ёмкости кампаний БФЛ (`BUtsCrmLead::campaignProfile`) собирает все известные UF-поля запроса, в том числе доход `UF_CRM_1461049862` и ограничения `UF_CRM_RESTRICTION`. От него зависит `need_site_consent`.
- Бот шлёт в квалификацию анкетные ключи (`amount`, `age`, `employment`…). CRM их не видит, и все 13 из 13 квалификаций в боевой базе за 11–14.09.2026 вернули `category = null`.

## Global Constraints

- «ЕДИНЫЙ СЛОВАРЬ ПОЛЕЙ: вход — те же Битрикс-имена, что у приёма (lead-intake)» — квалификация шлёт только Битрикс-имена.
- Поле квалификации = поле лида: имя атрибута, алиас и тип берутся из `LeadIntakeRequest`, значение считает тот же код, что в `map_lead_intake`.
- Состав полей квалификации ровно такой: `SUMM`, `BIRTHDATE`, `UF_CRM_1452153725`, `UF_CREDIT_HISTORY`, `UF_CRM_LOAN_OVERDUE_DURATION`, `UF_DEBT_SUMM`, `UF_CRM_1460622474`, `UF_CRM_1461049862`, `UF_REALTY_CC_4_22`, `UF_CRM_1452154011`, `CHAT_REALTY_REGION`, `CHAT_OWNER`, `UF_CRM_RESTRICTION`, `UF_HAVE_AUTO`, `UF_CAR_PRODUCTION_YEAR_1`, `UF_CREDIT_AUTO_1`. Телефон, ФИО, согласие, метки и диалог в квалификацию не уходят.
- `CHAT_OWNER` передаётся только при `ownership_form == "private"`, во всех трёх запросах. Комментарий backend в нашем маппинге: «Муниципальный объект нельзя превращать в частный одним наличием владельца». Кода муниципальной собственности у нас нет.
- Отсутствующий ответ уходит `null`. Пустой регион не блокирует вызов (предупреждение и инцидент `region_missing` остаются). `category = null` не отменяет лид.
- Запросы строгие (`extra="forbid"`), ответы терпимые (`_CrmResponseModel`). `UF_DEBT_SUMM` уходит строкой (`CrmAmountString`).
- Второй объект, если он квалифицирующий, занимает ключи первого (`_qualifying_realty_root`).
- В `mbk/crm-mbk` и `mbk/mbk-site` ничего не менять. Боевая база — только чтение. Живая проба зовёт только `first-qualification` и `realty-qualification` на синтетическом профиле; `lead-intake` не вызывается.
- Процесс: коммиты от `Zheltenkov <zheltenkovvp@gmail.com>`, без упоминания Claude и без трейлеров Co-Authored-By. Push — только после утверждения человека.
- Проверки перед завершением каждой задачи: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`.

## Карта файлов

| Файл | Что | Задача |
|---|---|---|
| `delivery/crm_mapping.py`, `tests/test_crm_mapping.py`, `tests/test_crm_field_contract.py` | собственник только для частного объекта | 1 |
| `delivery/crm_contracts.py`, `delivery/crm_mapping.py`, `crm_dialog_workflow.py`, `core.py`, `tests/test_crm_mapping.py`, `tests/test_crm_delivery.py`, `tests/test_transport_drop.py`, `tests/test_second_realty_region.py`, `tests/test_lead_intake_invariant.py` | first-qualification на Битрикс-именах | 2 |
| `tests/test_crm_qualification_contract.py` (новый), `tests/test_lead_intake_invariant.py` | инвариант «квалификация — срез лида» | 3 |
| `delivery/crm_contracts.py`, `delivery/crm_mapping.py`, `tests/test_crm_mapping.py`, `tests/test_crm_delivery.py`, `tests/test_crm_qualification_contract.py` | realty-qualification на Битрикс-именах | 4 |
| `docs/MBK_CRM_BACKEND_CONTRACT.md`, `docs/HANDOVER.md` | документы контракта | 5 |
| скрипт в scratchpad, `docs/agent-memory/SESSION.md`, `docs/agent-memory/facts/crm-anketa-dictionary.md` | приёмка, живая проба, память | 6 (контроллер) |

---

### Task 1: Собственник — только при частной собственности

**Files:**
- Modify: `delivery/crm_mapping.py` — добавить `_CHAT_OWNERS` и `_chat_owner`; использовать в `map_lead_intake` (блок `owners = get_fact(...)` / `chat_owner: Literal[...]`, строки ~443–454) и в `map_first_qualification` (расчёт `realty_owner` / `ownership_form`, строки ~237–248)
- Modify: `tests/test_crm_mapping.py` — новые тесты, правка `test_municipal_realty_is_never_mapped_as_private`
- Modify: `tests/test_crm_field_contract.py` — `test_the_owner_field_is_words_not_a_code`

**Interfaces:**
- Consumes: —
- Produces: `_chat_owner(facts: dict[str, Any], root: str) -> Literal["CLIENT", "BOTH"] | None` в `delivery/crm_mapping.py`. Возвращает код собственника только при `get_fact(facts, f"{root}.ownership_form") == "private"` и поддерживаемом `owners` (`client_only` → `"CLIENT"`, `client_and_spouse` → `"BOTH"`). Неподдерживаемый `owners` → `None` с предупреждением в лог.

- [ ] **Step 1: Написать падающие тесты** — в `tests/test_crm_mapping.py` добавить `import pytest` вторым импортом (после `from __future__ import annotations`) и дописать в конец файла:

```python
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
```

И заменить `test_municipal_realty_is_never_mapped_as_private` целиком:

```python
def test_municipal_realty_is_never_mapped_as_private():
    facts = _complete_facts()
    facts["assets"]["realty"]["ownership_form"] = "municipal"

    mapping = map_first_qualification(facts)

    assert mapping.request is not None
    # Собственник муниципального объекта не называется: CRM сделала бы объект
    # частным по одному этому полю.
    assert mapping.request.realty_owner is None
    assert mapping.request.ownership_form is None
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv313/Scripts/python -m pytest tests/test_crm_mapping.py -q -k "owner or municipal"`
Expected: FAIL. `test_lead_names_no_owner_without_confirmed_private_ownership` для всех трёх значений (`CHAT_OWNER` = `"CLIENT"` в payload) и `test_municipal_realty_is_never_mapped_as_private` (`realty_owner` = 4673).

- [ ] **Step 3: Реализация** — в `delivery/crm_mapping.py` сразу после словаря `_REALTY_OWNERS` добавить:

```python
_CHAT_OWNERS: dict[str, Literal["CLIENT", "BOTH"]] = {
    "client_only": "CLIENT",
    "client_and_spouse": "BOTH",
}
```

Перед `def _qualifying_realty_root` добавить функцию:

```python
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
```

В `map_lead_intake` заменить блок

```python
    owners = get_fact(facts, f"{realty_root}.owners")
    chat_owner: Literal["CLIENT", "BOTH"] | None = None
    if owners == "client_only":
        chat_owner = "CLIENT"
    elif owners == "client_and_spouse":
        chat_owner = "BOTH"
    elif owners is not None:
        logger.warning(
            "CRM lead-intake: состав собственников %r без кода CRM, "
            "поле уедет пустым",
            owners,
        )
```

на

```python
    chat_owner = _chat_owner(facts, realty_root)
```

В `map_first_qualification` заменить

```python
        realty_owner = _REALTY_OWNERS.get(
            get_fact(facts, f"{realty_root}.owners")
        )
        # Backend: 4671 только для подтверждённой частной собственности и
        # поддерживаемого состава собственников. Муниципальный объект нельзя
        # превращать в частный одним наличием владельца.
        ownership_form = (
            OwnershipForm.PRIVATE
            if realty_owner is not None
            and get_fact(facts, f"{realty_root}.ownership_form") == "private"
            else None
        )
```

на

```python
        # Тот же собственник, что уедет в лид: только у частного объекта.
        realty_owner = (
            _REALTY_OWNERS.get(get_fact(facts, f"{realty_root}.owners"))
            if _chat_owner(facts, realty_root) is not None
            else None
        )
        ownership_form = OwnershipForm.PRIVATE if realty_owner is not None else None
```

- [ ] **Step 4: Поправить тест, который называл собственника без формы** — в `tests/test_crm_field_contract.py`, `test_the_owner_field_is_words_not_a_code`, строку

```python
            "realty": {"kind": "flat", "owners": "client_and_spouse", **KAZAN},
```

заменить на

```python
            "realty": {
                "kind": "flat",
                "ownership_form": "private",
                "owners": "client_and_spouse",
                **KAZAN,
            },
```

- [ ] **Step 5: Прогнать тесты**

Run: `.venv313/Scripts/python -m pytest -q`
Expected: PASS. Если упал ещё какой-то тест, который ждёт `CHAT_OWNER` или `realty_owner` у объекта без `"ownership_form": "private"` в фикстуре, добавь в эту фикстуру `"ownership_form": "private"`. Правило `_chat_owner` не менять.

Run: `.venv313/Scripts/python -m ruff check .`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add delivery/crm_mapping.py tests/test_crm_mapping.py tests/test_crm_field_contract.py
git commit -m "Собственник объекта уходит в CRM только у частной собственности"
```

---

### Task 2: first-qualification на Битрикс-именах

**Files:**
- Modify: `delivery/crm_contracts.py` — переписать `FirstQualificationRequest`, удалить `CarPayload`
- Modify: `delivery/crm_mapping.py` — вспомогательные функции, переписать `map_first_qualification`, использовать функции в `map_lead_intake`, удалить `_qualification_cars` и `_optional_bool`
- Modify: `crm_dialog_workflow.py:576` — имя атрибута региона
- Modify: `core.py:593` — упоминание `residence_region` в докстроке
- Modify: `tests/test_crm_mapping.py`, `tests/test_crm_delivery.py`, `tests/test_transport_drop.py`, `tests/test_second_realty_region.py`, `tests/test_lead_intake_invariant.py`

**Interfaces:**
- Consumes: `_chat_owner(facts, root)` из Task 1.
- Produces:
  - `FirstQualificationRequest` с атрибутами `amount`, `birthdate`, `registration_address`, `credit_history`, `overdue_duration`, `debt_amount`, `employment`, `monthly_income`, `has_realty`, `realty_type`, `realty_region`, `chat_owner`, `realty_restriction`, `has_car`, `car_year_1`, `car_credit_1`. Алиасы и типы как у одноимённых полей `LeadIntakeRequest`, все по умолчанию `None`.
  - В `delivery/crm_mapping.py`: `_crm_birthdate(facts) -> str | None`, `_crm_registration_address(facts) -> str | None`, `_assets_availability(value: Any) -> AssetsAvailability | None`, `_car_availability(value: Any) -> CarAvailability | None`.
  - `map_first_qualification(facts, *, enum_map=None) -> QualificationMapping` — сигнатура прежняя.

- [ ] **Step 1: Написать падающие тесты** — в `tests/test_crm_mapping.py` добавить импорт `from datetime import date` (после `import pytest`) и дописать в конец файла:

```python
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
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv313/Scripts/python -m pytest tests/test_crm_mapping.py -q -k "bitrix_names or birthdate or same_string"`
Expected: FAIL: `KeyError: 'SUMM'` и `AttributeError` у `birthdate` / `registration_address`.

- [ ] **Step 3: Контракт** — в `delivery/crm_contracts.py` удалить класс `CarPayload` целиком и заменить класс `FirstQualificationRequest` целиком на:

```python
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
```

`AssetsAvailability` и `RealtyRestriction` объявлены в том же файле выше, импорт не нужен.

- [ ] **Step 4: Вспомогательные функции маппинга** — в `delivery/crm_mapping.py` в конец файла, после `_crm_date`, добавить:

```python
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
```

- [ ] **Step 5: Лид на тех же функциях** — в `map_lead_intake` внутри вызова `LeadIntakeRequest(...)` заменить

```python
            BIRTHDATE=(
                _crm_date(get_fact(facts, "client.birthdate"))
                or _birthdate_from_age(get_fact(facts, "client.age"))
            ),
            # Адрес прописки CRM использует как источник региона клиента, а
            # заполнялся он только с формы сайта: у клиента из чата поле
            # уходило пустым, и регион в карточке не определялся (замечание
            # заказчика 20.08.2026). Полного адреса в чате нет и быть не
            # может — бот спрашивает город и регион, — поэтому в запас идёт
            # нормализованный населённый пункт: улицу не выдумываем, а регион
            # CRM из этой строки читает.
            UF_CRM_1452153725=(
                _optional_text(get_fact(facts, "client.registration_address"))
                or _location_text(facts, "client")
            ),
```

на

```python
            BIRTHDATE=_crm_birthdate(facts),
            UF_CRM_1452153725=_crm_registration_address(facts),
```

заменить

```python
            UF_REALTY_CC_4_22=(
                AssetsAvailability.YES
                if has_realty is True
                else AssetsAvailability.NO
                if has_realty is False
                else None
            ),
```

на

```python
            UF_REALTY_CC_4_22=_assets_availability(has_realty),
```

и заменить

```python
            UF_HAVE_AUTO=(
                CarAvailability.YES
                if has_vehicle is True
                else CarAvailability.NO
                if has_vehicle is False
                else None
            ),
```

на

```python
            UF_HAVE_AUTO=_car_availability(has_vehicle),
```

- [ ] **Step 6: Квалификация — срез лида** — в `delivery/crm_mapping.py` заменить функцию `map_first_qualification` целиком на:

```python
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
            UF_CRM_1461049862=get_fact(facts, "employment.income"),
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
```

Удалить функции `_qualification_cars` и `_optional_bool`: после замены их никто не вызывает. Из импорта `from .crm_contracts import (...)` убрать `CarPayload`. `OwnershipForm`, `RealtyOwner` и словарь `_REALTY_OWNERS` пока остаются — их использует `map_realty_qualification` (Task 4).

- [ ] **Step 7: Потребители атрибута региона** — в `crm_dialog_workflow.py` строку

```python
            if mapping.request.residence_region is None:
```

заменить на

```python
            if mapping.request.registration_address is None:
```

В `core.py`, докстрока `_keep_raw_location`, заменить

```python
    не разобрал ни один из них, поле уходит в CRM пустым — а `residence_region`
```

на

```python
    не разобрал ни один из них, поле уходит в CRM пустым — а адрес прописки `UF_CRM_1452153725`
```

- [ ] **Step 8: Существующие тесты квалификации на новые имена** — в `tests/test_crm_mapping.py` заменить функции целиком.

`test_complete_known_mapping_builds_api_request`:

```python
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
```

`test_vehicle_credit_status_uses_confirmed_enum`:

```python
def test_vehicle_credit_status_uses_confirmed_enum():
    facts = _complete_facts()
    facts["assets"]["has_vehicle"] = True
    facts["assets"]["vehicle"] = {"year": 2018, "pledged": False}

    mapping = map_first_qualification(facts)

    assert mapping.ready is True
    assert mapping.request is not None
    assert mapping.request.car_year_1 == 2018
    assert mapping.request.car_credit_1 == 4600
```

`test_missing_answers_are_serialized_as_null`:

```python
def test_missing_answers_are_serialized_as_null():
    mapping = map_first_qualification({})

    assert mapping.ready is True
    assert mapping.request is not None
    payload = mapping.request.as_api_dict()
    assert payload
    assert all(value is None for value in payload.values())
```

`test_second_realty_object_has_priority_for_qualification`:

```python
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
```

`test_unsupported_realty_owner_and_type_are_sent_as_null`:

```python
def test_unsupported_realty_owner_and_type_are_sent_as_null():
    facts = _complete_facts()
    facts["assets"]["realty"]["kind"] = "other"
    facts["assets"]["realty"]["owners"] = "others"

    mapping = map_first_qualification(facts)

    assert mapping.ready is True
    assert mapping.request is not None
    assert mapping.request.realty_type is None
    assert mapping.request.chat_owner is None
```

`test_municipal_realty_is_never_mapped_as_private`:

```python
def test_municipal_realty_is_never_mapped_as_private():
    facts = _complete_facts()
    facts["assets"]["realty"]["ownership_form"] = "municipal"

    mapping = map_first_qualification(facts)

    assert mapping.request is not None
    # Собственник муниципального объекта не называется: CRM сделала бы объект
    # частным по одному этому полю.
    assert mapping.request.chat_owner is None
```

В `test_residence_region_is_sent_for_a_chat_only_client` последнюю строку заменить на `assert mapping.request.registration_address == "Москва"`. В `test_registration_address_is_used_when_there_is_no_locality` — на `assert mapping.request.registration_address == "г Москва, ул Тверская, д 1"`.

Замены ключей словаря в остальных тестах:
- `test_ready_for_bankruptcy_is_sent_the_way_the_anketa_sends_it`, `test_past_overdue_without_current_one_is_a_clean_history`, `test_no_active_credits_beats_any_credit_history_fact`: `payload["credit_history"]` → `payload["UF_CREDIT_HISTORY"]`;
- `test_employment_collapses_to_the_anketa_vocabulary`: `payload["employment"]` → `payload["UF_CRM_1460622474"]`;
- `test_locality_travels_with_its_region_like_the_dadata_suggestion`, `test_moscow_city_is_not_duplicated_as_its_own_region`: `payload["realty_region"]` → `payload["CHAT_REALTY_REGION"]`.

`test_legacy_ambiguous_employment_is_sent_as_null_not_a_guess` и `test_no_realty_does_not_require_realty_details` не меняются: атрибуты `employment`, `realty_type`, `realty_region` сохранили имена.

- [ ] **Step 9: Фикстуры запросов в тестах транспорта** — в `tests/test_crm_delivery.py` и в `tests/test_transport_drop.py` заменить функцию `_qualification_request` целиком на:

```python
def _qualification_request() -> FirstQualificationRequest:
    return FirstQualificationRequest(
        SUMM=1_500_000,
        BIRTHDATE="01-01-1984",
        UF_CRM_1452153725="г Москва, ул Ленина 1",
        UF_CREDIT_HISTORY=CreditHistory.NO_OVERDUE,
        UF_DEBT_SUMM=900_000,
        UF_CRM_1460622474=EmploymentType.HIRED,
        UF_REALTY_CC_4_22=AssetsAvailability.YES,
        UF_CRM_1452154011=RealtyType.FLAT,
        CHAT_REALTY_REGION="Москва",
        CHAT_OWNER="CLIENT",
        UF_HAVE_AUTO=CarAvailability.YES,
        UF_CAR_PRODUCTION_YEAR_1=2018,
        UF_CREDIT_AUTO_1=CarCredit.NO,
    )
```

В обоих файлах добавить `AssetsAvailability` в импорт из `delivery.crm_contracts` и убрать `CarPayload`. Остальные имена, которые `ruff check` пометит как неиспользуемые (F401), убрать из импорта.

В `tests/test_crm_delivery.py` заменить `test_qualification_contract_uses_lowercase_backend_fields` целиком на:

```python
def test_qualification_contract_uses_bitrix_field_names():
    payload = _qualification_request().as_api_dict()

    assert payload["SUMM"] == 1_500_000
    assert payload["UF_CRM_1452154011"] == 190
    assert payload["UF_HAVE_AUTO"] == 4695
    assert payload["UF_CAR_PRODUCTION_YEAR_1"] == 2018
    assert payload["UF_CREDIT_AUTO_1"] == 4600
    assert payload["UF_DEBT_SUMM"] == "900000"
    assert payload["UF_CRM_LOAN_OVERDUE_DURATION"] is None
```

- [ ] **Step 10: Остальные тесты на старые имена**
- `tests/test_second_realty_region.py`, `test_qualification_carries_the_region_of_the_qualifying_object`: `payload.get("realty_region")` → `payload.get("CHAT_REALTY_REGION")`.
- `tests/test_lead_intake_invariant.py`, `test_two_mappers_never_disagree_on_the_same_value`: оба `qualification.request.realty_owner` → `qualification.request.chat_owner`. Полная замена этого теста — Task 3.

- [ ] **Step 11: Прогнать тесты**

Run: `.venv313/Scripts/python -m pytest -q`
Expected: PASS, включая три новых теста из Step 1.

Run: `.venv313/Scripts/python -m ruff check .`
Expected: `All checks passed!`

Run: `rg -n "residence_region|CarPayload|_qualification_cars|_optional_bool" delivery core.py crm_dialog_workflow.py`
Expected: пусто.

Run: `rg -n "CarPayload|lowercase_backend|payload\[\"(credit_history|employment|realty_region)\"\]|get\(\"realty_region\"\)" tests`
Expected: пусто. Имя `test_residence_region_is_sent_for_a_chat_only_client` и докстроки с `residence_region` в тестах про регион — исторический текст, их не трогать.

- [ ] **Step 12: Commit**

```bash
git add delivery/crm_contracts.py delivery/crm_mapping.py crm_dialog_workflow.py core.py tests/test_crm_mapping.py tests/test_crm_delivery.py tests/test_transport_drop.py tests/test_second_realty_region.py tests/test_lead_intake_invariant.py
git commit -m "Квалификация CRM на Битрикс-именах полей, как у лида"
```

---

### Task 3: Инвариант «квалификация — срез лида»

**Files:**
- Create: `tests/test_crm_qualification_contract.py`
- Modify: `tests/test_lead_intake_invariant.py` — заменить `test_two_mappers_never_disagree_on_the_same_value`

**Interfaces:**
- Consumes: `FirstQualificationRequest`, `LeadIntakeRequest`, `map_first_qualification`, `map_lead_intake` после Task 2.
- Produces: `CRM_QUALIFICATION_FIELDS: set[str]` в `tests/test_crm_qualification_contract.py` (Task 4 допишет туда проверку realty).

- [ ] **Step 1: Контрактный тест** — создать `tests/test_crm_qualification_contract.py`:

```python
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

from delivery.crm_contracts import FirstQualificationRequest, LeadIntakeRequest
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
```

- [ ] **Step 2: Прогнать контрактный тест**

Run: `.venv313/Scripts/python -m pytest tests/test_crm_qualification_contract.py -q`
Expected: PASS (3 passed). Если `test_a_full_profile_is_sent_identically_in_both_calls` падает на пустом поле, дополни `FULL_PROFILE` ответом, от которого это поле заполняется. Если падает на расхождении значений — чини маппинг: поле квалификации должно считаться тем же кодом, что в `map_lead_intake`.

- [ ] **Step 3: Проверить, что тест ловит расхождение** — временно, без коммита, в `map_first_qualification` заменить `UF_CRM_1452153725=registration_address,` на `UF_CRM_1452153725=_location_text(facts, "client", prefer_locality=True),`.

Run: `.venv313/Scripts/python -m pytest tests/test_crm_qualification_contract.py -q`
Expected: FAIL в `test_a_full_profile_is_sent_identically_in_both_calls` (`'Московская обл, г Подольск'` против `'142100, Московская обл, г Подольск'`).

Вернуть строку: `git checkout delivery/crm_mapping.py`. Run: `git status --short delivery/` — Expected: пусто.

- [ ] **Step 4: Перебор всех значений матрицы** — в `tests/test_lead_intake_invariant.py` заменить функцию `test_two_mappers_never_disagree_on_the_same_value` целиком на:

```python
def test_qualification_is_a_slice_of_the_lead(matrix):
    """Квалификация и лид читают одни факты — расходиться им нельзя.

    С 31.08.2026 CRM считает категорию дважды одним кодом: по
    first-qualification (бот по ней решает про офер и согласие) и заново по
    lead-intake (категория в Битриксе). Каждое поле квалификации обязано
    совпадать с тем же полем лида при любом законном ответе клиента, иначе
    клиенту покажут один продукт, а лид уйдёт в другой.

    Квалификация неподдерживаемый код кладёт в null, это закреплено тестом
    test_crm_mapping.py::test_unsupported_realty_owner_and_type_are_sent_as_null.
    Если тот же факт отменяет лид, клиент уходит с определённой категорией и
    без обращения — самый дорогой возможный исход.
    """
    disagreements: list[str] = []
    for path, value in _enum_values(matrix):
        facts = _survey(**{path: value})
        qualification = map_first_qualification(facts)
        lead = _lead(facts)
        if qualification.ready and not lead.ready:
            disagreements.append(
                f"{path}={value!r}: квалификация принимает, лид-интейк отменяет"
            )
            continue
        if qualification.request is None or lead.request is None:
            continue
        sent = qualification.request.as_api_dict()
        created = lead.request.as_api_dict()
        for field, sent_value in sent.items():
            if created.get(field) != sent_value:
                disagreements.append(
                    f"{path}={value!r}: {field} = {sent_value!r} в квалификации "
                    f"против {created.get(field)!r} в лиде"
                )
    assert not disagreements, "мапперы CRM расходятся:\n" + "\n".join(disagreements)
```

- [ ] **Step 5: Проверить, что перебор ловит расхождение** — временно, без коммита, в `map_first_qualification` заменить `UF_HAVE_AUTO=_car_availability(get_fact(facts, "assets.has_vehicle")),` на `UF_HAVE_AUTO=None,`.

Run: `.venv313/Scripts/python -m pytest tests/test_lead_intake_invariant.py -q -k slice`
Expected: FAIL со строками `UF_HAVE_AUTO = None в квалификации против 4696 в лиде`.

Вернуть: `git checkout delivery/crm_mapping.py`. Run: `git status --short delivery/` — Expected: пусто.

- [ ] **Step 6: Прогнать всё**

Run: `.venv313/Scripts/python -m pytest -q`
Expected: PASS.

Run: `.venv313/Scripts/python -m ruff check .`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add tests/test_crm_qualification_contract.py tests/test_lead_intake_invariant.py
git commit -m "Инвариант: квалификация CRM — срез лида"
```

---

### Task 4: realty-qualification на Битрикс-именах

**Files:**
- Modify: `delivery/crm_contracts.py` — `RealtyQualificationRequest`
- Modify: `delivery/crm_mapping.py` — `map_realty_qualification`; убрать `_REALTY_OWNERS` и неиспользуемые импорты
- Modify: `tests/test_crm_mapping.py`, `tests/test_crm_delivery.py`, `tests/test_crm_qualification_contract.py`

**Interfaces:**
- Consumes: `_chat_owner(facts, root)` (Task 1), `_supported_enum`, `_location_text`, `_REALTY_TYPES`; `CRM_QUALIFICATION_FIELDS` в `tests/test_crm_qualification_contract.py` (Task 3).
- Produces: `RealtyQualificationRequest` с обязательными атрибутами `realty_type: RealtyType` (алиас `UF_CRM_1452154011`), `realty_region: str` (`CHAT_REALTY_REGION`, `min_length=1`), `chat_owner: Literal["CLIENT", "BOTH"]` (`CHAT_OWNER`). `map_realty_qualification(facts) -> RealtyMapping` — сигнатура прежняя.

- [ ] **Step 1: Написать падающие тесты** — в `tests/test_crm_mapping.py` импорт заменить на `from delivery.crm_mapping import map_first_qualification, map_lead_intake, map_realty_qualification` и дописать в конец файла:

```python
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
```

И в `tests/test_crm_qualification_contract.py` добавить импорт `RealtyQualificationRequest` в строку `from delivery.crm_contracts import ...` и дописать в конец:

```python
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
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv313/Scripts/python -m pytest tests/test_crm_mapping.py tests/test_crm_qualification_contract.py -q -k "realty_check"`
Expected: FAIL. `test_realty_check_speaks_the_bitrix_names_of_the_object` получает словарь с ключами `realty_type` / `ownership_form`; у `test_the_realty_check_sends_the_object_fields_of_the_lead` алиасы — `{None}` вместо Битрикс-имён. Параметризованный тест про неподходящий объект уже зелёный — это регрессионная проверка.

- [ ] **Step 3: Контракт** — в `delivery/crm_contracts.py` заменить класс `RealtyQualificationRequest` целиком на:

```python
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
```

- [ ] **Step 4: Маппинг** — в `delivery/crm_mapping.py` заменить функцию `map_realty_qualification` целиком на:

```python
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
```

Удалить словарь `_REALTY_OWNERS`: после замены его никто не использует. Из импорта `from .crm_contracts import (...)` убрать `OwnershipForm` и `RealtyOwner`. Сами перечисления в `crm_contracts.py` остаются — это справочник кодов заказчика, его проверяют `tests/test_crm_delivery.py::test_enum_values_match_backend_reference` и документ контракта.

- [ ] **Step 5: Тест транспорта** — в `tests/test_crm_delivery.py`, `test_realty_qualification_parses_actual_success_flag`, заменить вызов

```python
    response = client.realty_qualification(
        crm.RealtyQualificationRequest(
            realty_type=RealtyType.FLAT,
            realty_region="Москва",
            realty_owner=RealtyOwner.CLIENT_ONLY,
            ownership_form=OwnershipForm.PRIVATE,
        )
    )
```

на

```python
    response = client.realty_qualification(
        crm.RealtyQualificationRequest(
            UF_CRM_1452154011=RealtyType.FLAT,
            CHAT_REALTY_REGION="Москва",
            CHAT_OWNER="CLIENT",
        )
    )
```

- [ ] **Step 6: Прогнать тесты**

Run: `.venv313/Scripts/python -m pytest -q`
Expected: PASS.

Run: `.venv313/Scripts/python -m ruff check .`
Expected: `All checks passed!`

Run: `rg -n "_REALTY_OWNERS|realty_owner=|ownership_form=OwnershipForm" --glob "*.py" .`
Expected: пусто.

- [ ] **Step 7: Commit**

```bash
git add delivery/crm_contracts.py delivery/crm_mapping.py tests/test_crm_mapping.py tests/test_crm_delivery.py tests/test_crm_qualification_contract.py
git commit -m "Проверка объекта в CRM на Битрикс-именах полей"
```

---

### Task 5: Документы контракта

**Files:**
- Modify: `docs/MBK_CRM_BACKEND_CONTRACT.md` — раздел «## Подтверждённый first-qualification»
- Modify: `docs/HANDOVER.md` — раздел 5 «Контракт CRM»

**Interfaces:**
- Consumes: поведение после Tasks 1–4.
- Produces: —

- [ ] **Step 1: Контракт** — в `docs/MBK_CRM_BACKEND_CONTRACT.md` заменить раздел от строки `## Подтверждённый first-qualification` до строки перед `## Подтверждённые enum` целиком на:

```markdown
## Входные поля квалификации: Битрикс-имена

С 31.08.2026 (crm-mbk, коммит 18490d4d «#97878 Новая партнёрка») обе
квалификации читают те же Битрикс-имена полей, что и `lead-intake`. Анкетного
формата (`amount`, `age`, `residence_region`, `cars`…) у них больше нет. Такие
ключи CRM не видит, профиль клиента выходит пустым, ответ приходит с
`category = null`. Так было у всех квалификаций бота с 31.08 по 14.09.2026.

Профиль строит `CrmLeadProfileMapper::toProfile`, правила проверяет
`RuleMatcher`. Условие, не заданное в правиле, не ограничивает. Заданное
условие при пустом поле профиля не выполнено, кроме наборов, где правило
разрешает «Пустое поле».

`first-qualification` получает:

| Поле | Что это | Как читает CRM |
|---|---|---|
| `SUMM` | сумма | число |
| `BIRTHDATE` | дата рождения `ДД-ММ-ГГГГ` | возраст = `Carbon::parse(BIRTHDATE)->age`; без даты с формы бот шлёт 1 января года рождения |
| `UF_CRM_1452153725` | адрес прописки | регион проживания: Москва/МО или РФ |
| `UF_DEBT_SUMM` | долг, строкой | число |
| `UF_CREDIT_HISTORY` | кредитная история | код |
| `UF_CRM_LOAN_OVERDUE_DURATION` | срок просрочки | код |
| `UF_CRM_1460622474` | занятость | код |
| `UF_CRM_1461049862` | доход | профиль предикта ёмкости кампаний |
| `UF_REALTY_CC_4_22` | есть недвижимость | `4697` — есть |
| `UF_CRM_1452154011` | тип объекта | код |
| `CHAT_REALTY_REGION` | регион объекта, текстом | Москва/МО, списки городов, РФ |
| `CHAT_OWNER` | `CLIENT` / `BOTH` | собственник `4673` / `4675`; без `UF_FORM_OF_OWNERSHIP_FLAT` объект считается частным (`4671`) |
| `UF_CRM_RESTRICTION` | ограничения на объект | профиль предикта ёмкости кампаний |
| `UF_HAVE_AUTO` | есть авто | `4695` / `4696` |
| `UF_CAR_PRODUCTION_YEAR_1`, `UF_CREDIT_AUTO_1` | год и кредит авто | условие года и кредита |

`realty-qualification` получает `UF_CRM_1452154011`, `CHAT_REALTY_REGION`,
`CHAT_OWNER` и отвечает `is_first_category`.

Правила бота:

- Запрос квалификации — срез запроса лида: те же поля, посчитанные тем же
  кодом (`delivery/crm_mapping.py`). CRM классифицирует клиента дважды, по
  квалификации и по лиду, и расчёты не должны расходиться. Держат это
  `tests/test_crm_qualification_contract.py` и
  `tests/test_lead_intake_invariant.py`.
- `CHAT_OWNER` передаётся только при подтверждённой частной собственности. Кода
  муниципальной собственности у нас нет, а названный собственник делает объект
  частным. Муниципальный объект не должен открывать залог.
- Отсутствующий ответ передаётся `null`. `category = null` не отменяет лид:
  `lead-intake` вызывается обязательно.
- Для недвижимости передаётся второй объект, если он квалифицирующий, в ключах
  первого, иначе первый.
```

- [ ] **Step 2: HANDOVER** — в `docs/HANDOVER.md`, раздел 5, после абзаца «Полная схема: `MBK_CRM_BACKEND_CONTRACT.md`. Ниже — то, что выяснено живыми пробами и в контракте не описано.» вставить подраздел:

```markdown
### Квалификация говорит Битрикс-именами

С 31.08.2026 CRM принимает в `first-qualification` и `realty-qualification`
только те же имена полей, что в `lead-intake` (`SUMM`, `BIRTHDATE`,
`UF_CRM_1452153725`…). Старые анкетные ключи CRM молча не видит и отвечает
`category = null` любому клиенту. До 14.09.2026 бот слал анкетные ключи, и
категорию не получил ни один лид.

Запрос квалификации собирается как срез запроса лида теми же функциями
`delivery/crm_mapping.py`. Если меняется поле лида, которое влияет на
категорию, оно меняется и в квалификации — тесты
`tests/test_crm_qualification_contract.py` упадут, если нет. Состав полей —
`MBK_CRM_BACKEND_CONTRACT.md`, раздел «Входные поля квалификации».

Собственник (`CHAT_OWNER`) уходит только при частной собственности: без поля
формы собственности CRM считает объект с собственником частным.
```

В подразделе «### Без региона категории не будет» строку

```markdown
`residence_region` обязателен: тот же профиль без него даёт
```

заменить на

```markdown
Адрес прописки `UF_CRM_1452153725` обязателен: тот же профиль без него даёт
```

- [ ] **Step 3: Проверить, что старых имён в документах не осталось**

Run: `rg -n "residence_region|realty_owner|has_car = null|is_credit" docs/MBK_CRM_BACKEND_CONTRACT.md docs/HANDOVER.md`
Expected: ни одного описания старого формата как действующего. Допустимы перечень старых ключей в новом разделе контракта («(`amount`, `age`, `residence_region`, `cars`…)») и исторические упоминания в HANDOVER, где сказано, что так было раньше. Всё прочее переписать на Битрикс-имена.

- [ ] **Step 4: Commit**

```bash
git add docs/MBK_CRM_BACKEND_CONTRACT.md docs/HANDOVER.md
git commit -m "docs: квалификация CRM на Битрикс-именах полей"
```

---

### Task 6 (контроллер): приёмка, живая проба, память

Выполняет контроллер, не исполнитель. Проба ходит в боевую CRM заказчика.

- [ ] **Step 1: Проверки**

Run: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`
Expected: чисто; число тестов больше, чем 1674 passed на 13.09.2026.

- [ ] **Step 2: Живая проба квалификации** — скрипт `probe_qualification.py` в scratchpad. Профили синтетические, лид не создаётся, `lead-intake` не вызывается:

```python
"""Живая проба квалификации CRM на синтетических профилях. Лид не создаётся."""
import sys

sys.path.insert(0, r"C:\Users\zvp\VS_projects\MBK_v4")

from delivery.crm import CrmClassificationClient  # noqa: E402
from delivery.crm_mapping import map_first_qualification, map_realty_qualification  # noqa: E402

MOSCOW = {"unrestricted_value": "г Москва", "city_with_type": "г Москва", "region_with_type": "г Москва"}
PLEDGE = {
    "request": {"amount": 2_000_000},
    "client": {"age": 45, "location": MOSCOW},
    "debts": {"has_active_credits": False, "total": 0},
    "employment": {"type": "hired", "income": 150_000},
    "assets": {
        "has_realty": True,
        "realty": {"kind": "flat", "ownership_form": "private", "owners": "client_only", "location": MOSCOW},
        "has_vehicle": False,
    },
}
BFL = {
    "request": {"amount": 300_000},
    "client": {"age": 38, "location": {"unrestricted_value": "г Воронеж", "city_with_type": "г Воронеж", "region_with_type": "Воронежская обл"}},
    "debts": {"has_active_credits": True, "total": 800_000, "credit_history": "has_overdue", "overdue_duration": "over_6m"},
    "employment": {"type": "unemployed"},
    "assets": {"has_realty": False, "has_vehicle": False},
}

client = CrmClassificationClient(classification_attempts=1)
for name, facts in (("залог Москва", PLEDGE), ("БФЛ регион", BFL)):
    mapping = map_first_qualification(facts)
    assert mapping.ready, mapping.issues
    result = client.first_qualification(mapping.request).result
    print(name, "matched=", result.matched, "category=", result.category,
          "vertical=", result.vertical, "segment=", result.segment,
          "need_site_consent=", result.need_site_consent)
realty = map_realty_qualification(PLEDGE)
print("объект Москва is_first_category=", client.realty_qualification(realty.request).result.is_first_category)
```

Run: `.venv313/Scripts/python <scratchpad>/probe_qualification.py`
Expected: у «залог Москва» `matched=True` и непустая `category`, `is_first_category=True`. «БФЛ регион» — записать как есть. Если категория пустая, разобрать, какого условия правила не хватает. Считать проблему решённой, не разобравшись, нельзя.

- [ ] **Step 3: Память** — в `docs/agent-memory/SESSION.md` пункт «Пустая категория CRM» переписать в «Сделано»: коммиты плана, итог пробы, открытый вопрос Андрею о коде муниципальной формы собственности (`UF_FORM_OF_OWNERSHIP_FLAT`). В `docs/agent-memory/facts/crm-anketa-dictionary.md` раздел «С 31.08.2026 имена полей квалификации — только битриксовые»: убрать «Наши… шлют анкетные ключи», записать, что починено 14.09.2026, и правило про `CHAT_OWNER`. Перед поставкой: проверить, перечисляет ли `sync_vendor.py` тесты поимённо, и если да — дописать `tests/test_crm_qualification_contract.py`.

- [ ] **Step 4: Commit**

```bash
git add docs/agent-memory/SESSION.md docs/agent-memory/facts/crm-anketa-dictionary.md
git commit -m "docs(memory): квалификация CRM починена — итоги"
```
