# Shadow-проверка интерпретации фактов

Дата: 24.07.2026.

## Что делает режим

На каждом пользовательском ходе одновременно запускаются:

1. текущий legacy extractor — только он продолжает менять рабочие `facts`;
2. новый semantic extractor — его кандидаты проверяет deterministic reducer.

Результат второго контура сохраняется в `state.interpretation`, но не влияет на:

- ответ ассистента;
- следующий вопрос;
- матрицу и фактический маршрут;
- локальный `mbk.lead.v1`;
- qualification API;
- создание CRM-лида.

## Безопасное включение

Для локального сравнения:

```text
MBK_FACT_INTERPRETATION_MODE=shadow
MBK_FACT_SHADOW_MAX_CALLS_PER_SESSION=5
MBK_FACT_SHADOW_MAX_TOKENS=1000
MBK_FACT_SHADOW_TIMEOUT=30

MBK_ROUTER_MOCK=1
MBK_CRM_WORKFLOW_ENABLED=0
MBK_CRM_LEAD_INTAKE_ENABLED=0
MBK_CRM_ABANDONED_LEADS_ENABLED=0
```

Shadow выключен по умолчанию. Он делает максимум один дополнительный OpenRouter-вызов
на пользовательский ход и максимум пять на сессию. Parse retry и transport retry для
него отключены. Лимит 1000 output tokens нужен не для длинного текста, а чтобы JSON
сложной составной реплики не оборвался после 8–10 фактов и контрастных boundary-полей.
Legacy и shadow выполняются параллельно, поэтому их latency не складывается; верхняя
граница экспериментального вызова — 30 секунд.

## Что смотреть в state

```json
{
  "interpretation": {
    "mode": "shadow",
    "last_status": "success",
    "shadow_calls": 1,
    "last_result": {
      "comparison": {
        "agreement_paths": ["request.amount"],
        "legacy_only_paths": [],
        "shadow_only_paths": [],
        "conflict_paths": []
      },
      "pre_geo_plan": {
        "legacy": {"action": "ask"},
        "shadow": {"action": "ask"},
        "equal": true
      },
      "candidate_issues": []
    },
    "aggregate": {
      "turns": 1,
      "successes": 1,
      "errors": 0,
      "exact_turns": 1,
      "plan_matches": 1,
      "candidate_issues": 0,
      "invalid_schema_issues": 0,
      "domain_policy_issues": 0
    }
  }
}
```

Расшифровка:

- `agreement_paths` — оба контура дали одинаковое итоговое значение;
- `legacy_only_paths` — факт принял только текущий контур;
- `shadow_only_paths` — факт понял только новый контур;
- `conflict_paths` — оба изменили путь, но по-разному;
- `needs_confirmation` — reducer намеренно не записал неоднозначный/чужой факт;
- `candidate_issues` — невалидные отдельные элементы и применённые typed policy;
- `invalid_schema_issues` — отдельный кандидат отброшен, валидная часть batch
  сохранена;
- `domain_policy_issues` — применена узкая наблюдаемая доменная нормализация;
- `pre_geo_plan.equal` — совпадает ли deterministic plan до DaData enrichment;
- `budget_exhausted` — session cap исчерпан, дополнительных вызовов больше нет;
- `error` — shadow не сработал, legacy-диалог продолжился штатно.

Сами сравнения не содержат чувствительных значений. Evidence приходит из уже
обезличенной реплики.

## Gate перед переключением

Переключать runtime на новый reducer рано, пока не накоплен репрезентативный набор.
Минимальный первый прогон:

- не менее 50 разнообразных пользовательских ходов;
- обязательны отрицания, исправления, диапазоны, однословные ответы, имущество
  супруга, раздражение и несколько фактов одной фразой;
- `errors / turns ≤ 2%`;
- `plan_matches / successes ≥ 98%`;
- ручной разбор всех `conflict_paths`;
- ноль ложных записей имущества/долгов другого человека;
- ноль молчаливых перезаписей без `correct`;
- отдельно сравнить токены, стоимость и p95.

Расхождение не всегда означает ошибку shadow: marker-based legacy может быть как раз
неверной стороной. Поэтому конфликт размечается человеком и затем добавляется в
versioned offline-корпус.

## Первый live smoke

24.07.2026 выполнен один валидный семантический smoke на
`deepseek/deepseek-v4-pro`:

> Я не ИП, работаю по найму. Квартира есть у жены, не у меня. Долгов где-то
> 300–500 тысяч.

Результат:

- `employment.type=hired` принят;
- недвижимость и её тип не записаны клиенту: `subject=spouse`,
  `needs_confirmation`;
- диапазон долга не превращён в выдуманное среднее:
  `needs_confirmation`;
- latency 5,787 с;
- 2 065 input / 243 output token, из input 1 792 cached;
- CRM и DaData не вызывались.

Перед ним был диагностический transport-вызов, в котором PowerShell исказил
кириллицу в `?`; его нельзя считать семантической проверкой, но он также вошёл в
расход. Всего два OpenRouter-вызова, оценочная совокупная стоимость `$0.001449`.
Полный машинный отчёт:
`logs/fact_shadow/20260724_live_smoke.json`.
