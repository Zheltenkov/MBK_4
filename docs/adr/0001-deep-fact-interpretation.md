# ADR-0001: Deep-модуль интерпретации фактов вместо словарей фраз

- Статус: принято, этапы 1–2 реализованы; runtime-switch не выполнен
- Дата: 24.07.2026
- Область: понимание пользовательской реплики до routing

## Проблема

В текущем `core.py` есть защитные конструкции вида `marker in text`. Они быстро
закрыли конкретные регрессии, но являются **shallow module**: публичный эффект мал,
а список частных формулировок, отрицаний и контекстных исключений растёт в самом
orchestration-коде. Их deletion test показывает слабую глубину: удаление нескольких
строк снова ломает только известные фразы, а не ясно очерченную возможность системы.

Разговорная речь содержит отрицание, исправление, диапазоны, местоимения, чужого
собственника и временной контекст. Исчерпывающе покрыть это перечнем подстрок нельзя.
При этом отдать запись фактов модели без deterministic validation опасно для
маршрута и CRM.

## Решение

Создаётся один **deep module** `interpretation` с узким **interface**:

```text
typed FactCandidate[]
        ↓
deterministic reducer
        ↓
canonical facts + statuses + decisions + provenance events
```

**Implementation** внутри module отвечает за:

- allowlist путей;
- канонические типы и enum матрицы;
- grounding по evidence;
- subject resolution на границе client/non-client;
- операции `set`, `correct`, `clear`;
- конфликты и необходимость подтверждения;
- fail-closed причины отказа.

Semantic extractor отвечает за понимание языка, но не мутирует state. Reducer не
содержит разговорных синонимов. Routing engine остаётся полностью
детерминированным.

Этот module становится архитектурным **seam** между вероятностным пониманием языка
и бизнес-логикой. За счёт него повышаются **locality** изменений и **leverage**:
новый способ сказать «двушка» требует улучшения extractor/eval, но не правки
`core.py`, planner, CRM mapping и matrix engine.

## Что заимствовано из MBK v3

Из v3 сохранена полезная идея статусов знания и конфликтов. Усиления v4:

- `confidence` не принимается на доверии от LLM;
- добавлены `operation`, `source`, `evidence`, `explicitness`, `subject`;
- статус вычисляет reducer;
- LLM не выбирает продукт;
- итоговый маршрут по-прежнему определяет матрица.

## State transitions

| Условие | Решение | Изменение facts | Статус |
|---|---|---|---|
| Явное каноническое новое значение | accept | записать | `known` |
| То же значение повторно | noop | не менять | `confirmed` |
| Другое значение с `set` | conflict | не менять | `conflict` |
| Другое значение с `correct` | accept | заменить | `confirmed` |
| `clear` | clear | удалить | `unknown` |
| Неоднозначность | clarify | не менять | `needs_confirmation` |
| Субъект не клиент | clarify | не менять | `needs_confirmation` |
| Нет evidence/путь/тип невалиден | reject | не менять | без доверенного статуса |

## Этапность и recovery

1. Этап 1: typed contracts, reducer, внутренние state-поля, PII-safe provenance,
   offline-корпус и тесты. Runtime остаётся `legacy`.
2. Этап 2: structured-output extraction-вызов работает в `shadow`; legacy и shadow
   запускаются параллельно, сравниваются facts и pre-geo plan, но пользователь
   получает прежний результат. Реализовано с session budget и без retry.
3. Этап 3: 50-case ground-truth eval, точечные prompt/adapter improvements и
   controlled rerun. Реализовано инкрементально; полный post-change run ещё нужен.
4. Этап 4: переключение только после прохода eval-порогов; legacy остаётся fallback.
5. Этап 5: удалить marker-фильтры после стабильного периода и повторного deletion
   test.

При timeout, parse/schema error или недоступности модели новый контур не мутирует
state. Orchestrator логирует причину и использует legacy fallback.

## Метрики перед переключением

- exact path/value F1 по offline-корпусу;
- false-positive rate по отрицаниям и non-client subject;
- correction/retraction accuracy;
- доля `needs_confirmation`;
- расхождение legacy/deep по фактам и итоговому маршруту;
- extraction latency p50/p95;
- токены и стоимость на один пользовательский ход;
- schema/parse/fallback rate.

Минимальный gate переключения должен быть зафиксирован после первого shadow-прогона,
а не подогнан под единичный успешный разговор.

## Последствия

Плюсы:

- вариативность речи решается семантически;
- матрица и CRM защищены deterministic validation;
- конфликт и исправление становятся наблюдаемыми;
- появляется воспроизводимый eval-контур.

Цена:

- structured extractor должен возвращать более строгий контракт;
- state получает внутренние метаданные;
- до удаления legacy некоторое время существуют два контура;
- для production нужны versioned prompts/traces и лимиты стоимости.
