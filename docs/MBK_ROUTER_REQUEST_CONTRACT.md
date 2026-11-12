# Временный контракт запроса маршрутизатора `mbk.lead.v1`

Источник правды — Pydantic-модель
[`delivery/contracts.py`](../delivery/contracts.py). Контракт временный: когда Андрей
пришлёт финальную схему, имена и типы полей будут сопоставлены с ней.

Главное правило: структура JSON стабильна. Если ветка опроса не задавала вопрос,
соответствующее поле передаётся как `null`, а не исчезает из объекта. Массив
`location.candidates` передаётся пустым, если подтверждение одноимённого города не
требовалось.

Если локальная матрица завершилась `no_fit`, блок `routing` сохраняется в той же
структуре, но его поля равны `null`: система не придумывает несуществующий маршрут.
Это не блокирует последующую CRM-квалификацию и создание лида.

```json
{
  "schema_version": "mbk.lead.v1",
  "routing": {
    "route_id": "realty_msk",
    "priority": "1-1",
    "product": "Залог недвижимости",
    "segment": "Мск/МО",
    "family": "realty"
  },
  "client": {
    "name": "Василий",
    "phone": "+79990000000",
    "age": 42,
    "goal": "cash",
    "region_class": "moscow_mo",
    "region": "Московская обл",
    "city": "г Химки",
    "location": {
      "value": "Московская обл, г Химки",
      "unrestricted_value": "Московская обл, г Химки",
      "country": "Россия",
      "country_iso_code": "RU",
      "region": "Московская",
      "region_with_type": "Московская обл",
      "region_fias_id": "29251dcf-00a1-4e34-98d4-5c47484a36d4",
      "region_iso_code": "RU-MOS",
      "city": "Химки",
      "city_with_type": "г Химки",
      "city_fias_id": "d76255c8-3173-4db5-a39b-badd3ebdf851",
      "settlement": null,
      "settlement_with_type": null,
      "settlement_fias_id": null,
      "fias_id": "d76255c8-3173-4db5-a39b-badd3ebdf851",
      "fias_level": 4,
      "display_name": "Московская обл, г Химки",
      "status": "resolved",
      "query": "Химки",
      "confirmation_required": false,
      "candidates": []
    }
  },
  "request": {
    "amount": 800000
  },
  "preferences": {
    "collateral_allowed": null,
    "realty_collateral_allowed": null,
    "vehicle_collateral_allowed": null,
    "preferred_collateral": null,
    "explore_alternatives": null
  },
  "assets": {
    "has_realty": true,
    "realty": {
      "region_class": "moscow_mo",
      "region": "г Москва",
      "city": "г Москва",
      "location": {
        "value": null,
        "unrestricted_value": null,
        "country": null,
        "country_iso_code": null,
        "region": null,
        "region_with_type": null,
        "region_fias_id": null,
        "region_iso_code": null,
        "city": null,
        "city_with_type": null,
        "city_fias_id": null,
        "settlement": null,
        "settlement_with_type": null,
        "settlement_fias_id": null,
        "fias_id": null,
        "fias_level": null,
        "display_name": null,
        "status": null,
        "query": null,
        "confirmation_required": null,
        "candidates": []
      },
      "kind": "flat",
      "ownership_form": "private",
      "owners": "client_only",
      "restriction": null
    },
    "has_vehicle": null,
    "vehicle": {
      "year": null,
      "pledged": null
    }
  },
  "debts": {
    "total": null,
    "credit_history": null,
    "overdue_duration": null
  },
  "employment": {
    "type": null
  }
}
```

## Семантика географии

- `resolved` — DaData вернула один точный населённый пункт.
- `ambiguous` — найдено несколько одноимённых пунктов; до подтверждения клиента
  `region_class` остаётся `null`.
- `not_found` — DaData не нашла вариант.
- `unavailable` — API выключен, исчерпан локальный бюджет или произошла ошибка.

`region_class` вычисляется сервером, не LLM:

- `moscow_mo` — ISO `RU-MOW` или `RU-MOS`;
- `realty_list` — пара «регион + населённый пункт» присутствует в приложенном CSV
  Кредит.Клаб;
- `rf_other` — остальная Россия.

Отдельного BFL-хвоста в контракте нет: используются только вопросы из утверждённой
матрицы.
