# Развилка намерения — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Первая реплика клиента делится на «деньги», «уже оставлял заявку» и «не наш профиль»; в двух последних ветках анкета не начинается, а в ветке «уже оставлял заявку» лид не создаётся — всё за флагом `MBK_ENTRY_ROUTING`, выключенным по умолчанию.

**Architecture:** Намерение возвращает семантический извлекатель тем же вызовом, новым полем `entry_intent`; когда модели нет — решает словарь-страховка `routing/entry_intent.py`. Решение о сценарии принимает чистый модуль `entry_routing.py` и кладёт его в `state["entry"]`. Движок и матрица не меняются: план считается как всегда, ветка просто не даёт ему прозвучать, подменяя инструкцию хода и выключая анкетные вставки в `commit_turn`.

**Tech Stack:** Python 3.13, pydantic 2, pytest, ruff; OpenRouter (deepseek-v4-pro как семантический извлекатель).

**Spec:** `docs/superpowers/specs/2026-09-09-dialog-entry-and-order-design.md` — компоненты 1 и 2, разделы «Флаги и откат», «Метрики», «Тестирование».

## Global Constraints

Каждая задача неявно включает эти требования. Цитаты из спеки — дословно.

- «Маршрут выбирает детерминированный движок. Модель не получает ни названий продуктов, ни порогов, ни стоп-факторов.» Матрицу маршрутов и движок оценки не трогать вообще.
- «Лид создаётся не более одного раза.» В ветке `existing`: «Второй лид не создаётся» — «лид не создаётся ни при каком развитии разговора».
- «Персональные данные маскируются до внешних вызовов.» В payload аналитики ПД нет: только номер сессии и служебные признаки (`analytics/events.py`, `PERSONAL_DATA_KEYS`).
- «Ни одного дополнительного вызова модели и ни одной лишней секунды.»
- «Fail-open — несущее свойство. При любом сомнении — `unclear`, и разговор идёт как сегодня. Ни модель, ни словарь не имеют права молча увести человека от анкеты.»
- «Приоритет при конфликте: `existing` важнее `money`.»
- «Только первая реплика клиента.» «Возврат в анкету всегда открыт.»
- «Бот не обещает того, чего не делает.» «Про исход заявки бот произносит только факты из ответа CRM»; до контракта с CRM — «честно говорит, что заявку отсюда не видит, не начинает анкету, не создаёт лид и предлагает продолжить, если у человека появился новый запрос. Ничего не обещает.»
- Корпус: «доля `money`, ошибочно отнесённых к другим веткам, — ноль».
- Флаг `MBK_ENTRY_ROUTING` «выключен по умолчанию», «гасится по отдельности, не трогая остальное».
- Процесс: коммиты от `Zheltenkov <zheltenkovvp@gmail.com>`, без упоминания Claude и без трейлеров Co-Authored-By. Push, MR и любые отправки в git — только после утверждения человека и только в `dev`. Боевая база — только чтение.
- Проверки перед завершением каждой задачи: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`. Конфигов pytest/ruff в проекте нет, запуск с дефолтами.

## Решения, принятые при составлении плана

Спека описывает намерение, но часть «как» решена здесь. Каждое решение — по коду, а не по догадке.

1. **Поле едет в семантический извлекатель.** В бою `MBK_FACT_INTERPRETATION_MODE=semantic` (`deploy/prod/env.example:172`), основной вызов — `llm_agent.extract_fact_candidates`, контракт `interpretation/contracts.py:FactCandidateExtraction`. Эскиз в спеке смешал поля двух контрактов: `fact_updates`/`internal_summary` — это legacy `FactExtraction`. В legacy-режиме, при сбое модели и в shadow-режиме решает словарь.
2. **Промпт извлекателя меняется только при включённом флаге.** Выключенный флаг — поведение как сегодня, включая текст промпта: риск сдвинуть извлечение фактов на всех диалогах ради ветки, которая выключена, не нужен.
3. **Лид закрывается на трёх дорогах.** `crm_dialog_workflow.CrmDialogWorkflow.advance` (основной путь), `lead_intake_coordinator.LeadIntakeCoordinator.partial_is_due` (брошенная анкета: сессия с телефоном через 15 минут тишины уходит лидом сама) и локальная доставка в `core.prepare_turn`. Вторая дорога неочевидна: человек из ветки `existing`, назвавший телефон, без неё стал бы вторым лидом через 15 минут.
4. **Словарь никогда не возвращает `off_topic`.** «Карта» бывает кредитной картой; увести от анкеты по одному слову — нарушение fail-open.
5. **Ветка `existing` «липкая».** Из неё выводит только `money`. Переход `off_topic → existing` разрешён, `existing → off_topic` — нет: иначе снимался бы запрет второго лида.
6. **В ветках не пишутся `turn_completed` и `qualification_completed`.** Шага анкеты там нет, а план всё равно указывает на «сумму» — в воронке заказчика ветки выглядели бы обрывами на первом вопросе. Вместо этого пишется `entry_routed`.
7. **Инструкции веток перекрывают два общих правила системного промпта:** 1a («предложи оставить номер для связи» — в ветке это пустое обещание) и 6 («возвращай разговор к делу»).
8. **Выключение флага снимает `state["entry"]` на следующем ходу.** Откат полный, в том числе для сессий, попавших в ветку при включённом флаге.
9. **Корпус.** На 10.09.2026 в `dialog_messages` 226 диалогов с репликой клиента и 194 уникальные первые реплики после нормализации. Дубли схлопываются, число диалогов сохраняется в поле `count`.

## Открытый вопрос к человеку (не блокирует план)

31 из 226 первых реплик — голое приветствие. По спеке («только первая реплика») они уходят в анкету как `unclear`, и настоящий вопрос — «я оставлял заявку» — прозвучит уже второй репликой, когда развилка закрыта. Расширение «решение остаётся открытым, пока клиент не сказал ничего по существу» — отдельное решение человека. В план не входит; если его примут, это одна дополнительная задача поверх Task 6.

## Карта файлов

| Файл | Что | Задача |
|---|---|---|
| `core.py` | голая точка; решение в `prepare_turn`; инструкция веток; выключение анкетных вставок в `commit_turn`; запасная фраза ветки; запрет локальной доставки | 0, 8, 9, 10, 11 |
| `evaluation/entry_intent_corpus.json` | корпус первых реплик (создать) | 1 |
| `routing/entry_intent.py` | `EntryIntent` и словарь-страховка (создать) | 2 |
| `evaluation/entry_intent.py` | загрузка корпуса и подсчёт оценки (создать) | 3, 12 |
| `config.py`, `.env.example`, `deploy/prod/env.example`, `tests/conftest.py` | флаг `MBK_ENTRY_ROUTING` | 4 |
| `interpretation/contracts.py`, `interpretation/prompt.py`, `llm_agent.py` | поле `entry_intent` в ответе извлекателя | 5 |
| `entry_routing.py` | политика сценариев и события (создать) | 6, 7 |
| `analytics/events.py`, `docs/HANDOVER.md` | события `entry_routed`, `existing_client_request` | 7 |
| `prompts.py` | инструкции веток | 9 |
| `utils.py` | запасные фразы фильтра в ветках | 10 |
| `crm_dialog_workflow.py`, `lead_intake_coordinator.py` | запрет лида в ветке `existing` | 11 |
| `run_entry_intent_eval.py` | живой прогон корпуса (создать) | 12 |
| `evaluation/personas.py` | три роли для приёмки | 13 |
| `docs/agent-memory/SESSION.md`, спека | итоги | 14 |

## Что не входит

- Поход в CRM за статусом заявки — флаг `MBK_EXISTING_CLIENT_LOOKUP`, ждёт метода CRM и бизнес-процесса Михаила.
- Допродажа в ветке `existing` — ждёт ответа, умеет ли CRM дополнять заявку.
- Слепое парное сравнение из раздела «Тестирование» спеки. Для развилки прежние девять ролей идут прежним путём, и сравнивать нечего; сравнение нужно после компонента 3, где меняется сам диалог.
- Чипсы и порядок анкеты — компоненты 3 и 4, отдельные планы.

---

### Task 0: Реакция перед контактной просьбой не сжимается до голой точки

Старый дефект, отмеченный ревью 09.09.2026: `_safe_contact_reaction(["Понял вас."])` возвращает `"."`, и клиент читает «. Прежде чем продолжим, подскажите…».

**Files:**
- Modify: `core.py` — функция `_safe_contact_reaction`, строка `if cleaned:` (около 1406)
- Test: `tests/test_contact_reaction.py` (дописать в конец)

**Interfaces:**
- Consumes: —
- Produces: `_safe_contact_reaction(bubbles: list[str]) -> str` больше не возвращает строку без букв и цифр.

- [ ] **Step 1: Написать падающие тесты** — дописать в конец `tests/test_contact_reaction.py`:

```python
@pytest.mark.parametrize(
    "bubbles",
    [
        ["Понял вас."],
        ["Поняла вас!"],
        ["Хорошо."],
        ["Понял вас.", "Сколько вам лет?"],
    ],
)
def test_ritual_acknowledgement_does_not_shrink_to_a_bare_dot(bubbles):
    """«Понял вас.» после среза ритуала становилось «.», и клиент читал
    «. Прежде чем продолжим, подскажите…» (ревью 09.09.2026)."""
    assert _safe_contact_reaction(bubbles) == "Давайте разберёмся."


def test_ritual_is_dropped_but_the_next_sentence_survives_capitalised():
    reaction = _safe_contact_reaction(
        ["Поняла вас.", "ситуация обычная, разберёмся."]
    )

    assert reaction == "Ситуация обычная, разберёмся."
```

- [ ] **Step 2: Убедиться, что падают**

Run: `.venv313/Scripts/python -m pytest tests/test_contact_reaction.py -q`
Expected: 5 failed — вместо `"Давайте разберёмся."` приходит `"."`, во втором тесте `". ситуация обычная, разберёмся."`.

- [ ] **Step 3: Починить** — в `core.py`, в `_safe_contact_reaction`, заменить

```python
            if cleaned:
                # Заглавная только у первой фразы: вторая продолжает мысль.
                kept.append(cleaned[0].upper() + cleaned[1:] if not kept else cleaned)
```

на

```python
            # «Понял вас.» после срезов выше — голая точка. Фраза без единой
            # буквы или цифры не реакция: клиент читал «. Прежде чем
            # продолжим…» (ревью 09.09.2026).
            if re.search(r"\w", cleaned):
                # Заглавная только у первой фразы: вторая продолжает мысль.
                kept.append(cleaned[0].upper() + cleaned[1:] if not kept else cleaned)
```

- [ ] **Step 4: Проверить**

Run: `.venv313/Scripts/python -m pytest tests/test_contact_reaction.py tests/test_contact_request_flow.py tests/test_contact_request_variants.py -q`
Expected: all passed.
Run: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`
Expected: ruff чисто; все тесты зелёные.

- [ ] **Step 5: Коммит**

```bash
git add core.py tests/test_contact_reaction.py
git commit -m "Реакция перед контактной просьбой не сжимается до голой точки"
```

---

### Task 1: Корпус первых реплик (исполняет контроллер)

**Исполняет контроллер, не субагент.** Нужен доступ на чтение к боевой базе МБК; исполнителю доступ не передаётся. Здесь и ниже `<scratchpad>` — временный scratchpad-каталог сессии контроллера, вне репозитория. Подключение — помощник `<scratchpad>/db.py` (соединение `read_only`), пароль — в `доступ.txt` в корне рабочей копии, в репозиторий не попадает. **В базе ничего не менять.**

**Files:**
- Create: `evaluation/entry_intent_corpus.json`
- Scratch (вне git): `<scratchpad>/entry_corpus.py`, `<scratchpad>/entry_corpus_draft.json`

**Interfaces:**
- Produces: JSON-массив объектов `{"id": "e001", "message": str, "expected": "money"|"existing"|"off_topic"|"unclear", "count": int, "note"?: str}`. Ключи строго из этого набора, `id` уникальны. Задача 3 читает файл загрузчиком `load_entry_corpus`.

- [ ] **Step 1: Выгрузить черновик** — `<scratchpad>/entry_corpus.py`:

```python
"""Выгрузка первых реплик клиентов. Только чтение боевой базы."""
import json
import re
import sys
from collections import Counter
from pathlib import Path

SCRATCH = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRATCH))
from db import rows  # noqa: E402 — соединение read-only, пароль вне репозитория

_FINGERPRINT = re.compile(r"\[телефон[^\]]*\]")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_DIGIT_RUN = re.compile(r"[+\d][\d\s\-()]{8,}\d")


def _mask(text: str) -> str:
    text = _FINGERPRINT.sub("[телефон]", text)
    text = _EMAIL.sub("[почта]", text)

    def digits(match: re.Match[str]) -> str:
        run = match.group(0)
        # Телефон или номер карты — от десяти цифр. Сумма «1 500 000» — семь,
        # и она нужна корпусу: это главный признак денег.
        return "[номер]" if sum(ch.isdigit() for ch in run) >= 10 else run

    return _DIGIT_RUN.sub(digits, text)


def _key(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split()) or text


_, data = rows("""
    SELECT DISTINCT ON (session_id) session_id, occurred_at, message
    FROM dialog_messages
    WHERE role = 'client'
    ORDER BY session_id, turn, position
""")
first_seen: dict[str, str] = {}
counts: Counter[str] = Counter()
for _sid, _occurred_at, message in sorted(data, key=lambda row: row[1]):
    masked = _mask((message or "").strip())
    key = _key(masked)
    counts[key] += 1
    first_seen.setdefault(key, masked)

corpus = [
    {"id": f"e{index:03d}", "message": message, "expected": None, "count": counts[key]}
    for index, (key, message) in enumerate(first_seen.items(), start=1)
]
out = SCRATCH / "entry_corpus_draft.json"
out.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")
print(len(corpus), "реплик →", out)
```

Run: `.venv313/Scripts/python <scratchpad>/entry_corpus.py`
Expected: около 194 реплик (замер 10.09.2026: 226 диалогов, 194 уникальные).

- [ ] **Step 2: Разметить** — прочитать каждую реплику и проставить `expected` по правилам:
  - `existing` — спрашивает о заявке или обращении, которые **уже** оставил: статус, когда позвонят, почему не звонят, «я уже оставлял». Приоритет над `money`: «оставлял заявку на кредит» — `existing`.
  - `money` — нужны деньги или решение своих долгов: кредит, займ, рефинансирование, залог, долги, просрочки, приставы, коллекторы, банкротство, условия и ставки; вопрос о своём кредите в другом банке — тоже сюда. **При сомнении между `money` и другим — `money`**: жёсткое требование считается по этой метке, и лишняя `money` делает проверку строже, а не мягче.
  - `off_topic` — явно не про деньги и долги самого человека: карты, переводы, поддержка другого банка, налоги, вклады, инвестиции, вакансии, реклама.
  - `unclear` — приветствие без сути, «?», «тест», набор символов, реплика, из которой намерение не понять.
  - Спорный случай — пометить `note` одной фразой: почему выбрана метка.

- [ ] **Step 3: Вычистить ПД вручную** — при том же чтении заменить имена, фамилии и отчества на `[имя]`, адреса — на `[адрес]`. Названия банков и организаций оставить: они и есть признак `off_topic`.

- [ ] **Step 4: Самопроверка черновика**

```bash
.venv313/Scripts/python -c "
import json, re, collections
data = json.load(open(r'<scratchpad>/entry_corpus_draft.json', encoding='utf-8'))
print(collections.Counter(item['expected'] for item in data))
assert all(item['expected'] in {'money','existing','off_topic','unclear'} for item in data)
bad = [i['id'] for i in data if re.search(r'(?:\d[\s\-()+]*){10,}', i['message']) or '@' in i['message']]
assert not bad, bad
print('ok', len(data))
"
```
Expected: четыре метки в счётчике, `ok` и число реплик.

- [ ] **Step 5: Положить в репозиторий и закоммитить**

```bash
cp <scratchpad>/entry_corpus_draft.json evaluation/entry_intent_corpus.json
git add evaluation/entry_intent_corpus.json
git commit -m "Корпус первых реплик клиентов для развилки на входе"
```

---

### Task 2: Намерение и словарь-страховка

**Files:**
- Create: `routing/entry_intent.py`
- Test: `tests/test_entry_intent_dictionary.py`

**Interfaces:**
- Consumes: —
- Produces:
  - `class EntryIntent(StrEnum)`: `MONEY = "money"`, `EXISTING = "existing"`, `OFF_TOPIC = "off_topic"`, `UNCLEAR = "unclear"`
  - `classify_by_dictionary(message: str) -> EntryIntent` — чистая функция, никогда не возвращает `OFF_TOPIC`.

- [ ] **Step 1: Написать падающие тесты** — `tests/test_entry_intent_dictionary.py`:

```python
"""Словарь-страховка развилки: ловит только очевидное и никого не уводит зря."""
from __future__ import annotations

import pytest

from routing.entry_intent import EntryIntent, classify_by_dictionary


@pytest.mark.parametrize(
    "message",
    [
        "Здравствуйте, я оставлял заявку, когда позвонят?",
        "Добрый день. Заявку вчера оставила, что по ней?",
        "Что по моей заявке?",
        "Подскажите статус заявки",
        "Когда ожидать звонка менеджера?",
        "Никто не перезвонил",
        # existing важнее money: заявка уже есть, это не новое обращение
        "Оставлял заявку на кредит 500 тысяч",
    ],
)
def test_obvious_existing_requests(message):
    assert classify_by_dictionary(message) is EntryIntent.EXISTING


@pytest.mark.parametrize(
    "message",
    [
        "Нужны деньги",
        "Хочу взять кредит",
        # «подать», «оставить» — будущая заявка, а не поданная
        "Хочу подать заявку на займ",
        "Хочу оставить заявку на кредит",
        "Помогите с долгами, приставы списывают",
        "нужно 300 тыс",
        "Можно ли рефинансировать ипотеку?",
    ],
)
def test_money_requests_stay_money(message):
    assert classify_by_dictionary(message) is EntryIntent.MONEY


@pytest.mark.parametrize(
    "message",
    [
        "Здравствуйте",
        "Добрый вечер!",
        "?",
        "",
        "   ",
        # не наш профиль словарь не решает никогда: fail-open
        "Как разблокировать карту Сбербанка?",
        # «долго» — не «долг»
        "Долго ещё?",
    ],
)
def test_everything_else_is_unclear(message):
    assert classify_by_dictionary(message) is EntryIntent.UNCLEAR


def test_intent_values_are_the_contract_with_the_model():
    assert [intent.value for intent in EntryIntent] == [
        "money",
        "existing",
        "off_topic",
        "unclear",
    ]
```

- [ ] **Step 2: Убедиться, что падают**

Run: `.venv313/Scripts/python -m pytest tests/test_entry_intent_dictionary.py -q`
Expected: ERROR при сборе — `ModuleNotFoundError: No module named 'routing.entry_intent'`.

- [ ] **Step 3: Реализовать** — `routing/entry_intent.py`:

```python
"""Намерение первой реплики и словарь-страховка развилки на входе.

Основное решение принимает модель — тем же вызовом извлекателя, полем
`entry_intent`, по смыслу, а не по словам. Эта функция срабатывает, когда
модели нет: её не звали (режим legacy, shadow), она упала или поля не вернула.

Словарь короткий и ловит только очевидное. Fail-open: при сомнении —
`UNCLEAR`, и разговор идёт как без развилки. Правила для «не наш профиль»
здесь нет вовсе: «карта» бывает и кредитной картой, а увести человека от
анкеты по одному слову нельзя.
"""
from __future__ import annotations

import re
from enum import StrEnum


class EntryIntent(StrEnum):
    """С чем человек пришёл. Значения — контракт с моделью, менять молча нельзя."""

    MONEY = "money"
    EXISTING = "existing"
    OFF_TOPIC = "off_topic"
    UNCLEAR = "unclear"


# Только прошедшее время: «оставлял заявку» — заявка уже есть, «хочу оставить
# заявку» — новое обращение, то есть деньги.
_SUBMITTED = r"(?:оставлял|оставил|подавал|подал|отправлял|отправил)[аи]?\b"

_EXISTING_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        rf"\b{_SUBMITTED}\s+(?:\w+\s+)?заявк",
        rf"\bзаявк\w*\s+(?:\w+\s+)?{_SUBMITTED}",
        r"\b(?:мо[еяюй]\w*|статус\w*)\s+заявк",
        r"\bчто\s+(?:по|с)\s+(?:моей\s+)?заявк",
        r"\b(?:жду|ждать|ожидать|ожидаю)\s+звонк",
        r"\bкогда\s+(?:мне\s+)?(?:позвон|перезвон)\w*\s+менеджер",
        r"\bникто\s+не\s+(?:позвонил|перезвонил|связался)",
        r"\bне\s+перезвонил",
    )
)

_MONEY_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"кредит",
        r"займ|заем",
        r"ипотек",
        r"рефинанс",
        r"деньг|денег|денеж",
        r"\bдолг(?:и|ов|ам|ами|ах|а|у|е|ом)?\b",
        r"просроч",
        r"банкрот",
        r"коллектор",
        r"пристав",
        r"залог",
        r"рассроч",
        r"\bодолж",
        r"\d+\s*(?:тыс|т\.р|к\b|млн|миллион)",
        r"\bтысяч|\bмиллион|\bмлн\b",
    )
)


def classify_by_dictionary(message: str) -> EntryIntent:
    """Очевидное намерение по короткому списку. Всё прочее — `UNCLEAR`."""
    text = " ".join((message or "").casefold().replace("ё", "е").split())
    if not text:
        return EntryIntent.UNCLEAR
    # «Оставлял заявку на кредит» — вопрос о поданной заявке, а не новое
    # обращение: existing важнее money (спека, компонент 1).
    if any(pattern.search(text) for pattern in _EXISTING_PATTERNS):
        return EntryIntent.EXISTING
    if any(pattern.search(text) for pattern in _MONEY_PATTERNS):
        return EntryIntent.MONEY
    return EntryIntent.UNCLEAR
```

- [ ] **Step 4: Проверить**

Run: `.venv313/Scripts/python -m pytest tests/test_entry_intent_dictionary.py -q`
Expected: all passed.
Run: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`
Expected: ruff чисто; все тесты зелёные.

- [ ] **Step 5: Коммит**

```bash
git add routing/entry_intent.py tests/test_entry_intent_dictionary.py
git commit -m "Намерение первой реплики и словарь-страховка развилки"
```

---

### Task 3: Загрузка корпуса и проверка словаря на живом потоке

**Files:**
- Create: `evaluation/entry_intent.py`
- Test: `tests/test_entry_intent_corpus.py`

**Interfaces:**
- Consumes: `routing.entry_intent.EntryIntent`, `classify_by_dictionary` (Task 2); файл `evaluation/entry_intent_corpus.json` (Task 1).
- Produces:
  - `DEFAULT_CORPUS_PATH: Path`
  - `@dataclass(frozen=True) class EntryCase: id: str; message: str; expected: EntryIntent; count: int = 1`
  - `load_entry_corpus(path: Path = DEFAULT_CORPUS_PATH) -> list[EntryCase]` — бросает `ValueError` на любом нарушении формата.

- [ ] **Step 1: Написать падающие тесты** — `tests/test_entry_intent_corpus.py`:

```python
"""Корпус первых реплик: живой поток, без контактов, и словарь его не портит.

Требование спеки одно и жёсткое: реплики, где человеку нужны деньги, не
уходят в ветки «уже оставлял заявку» и «не наш профиль». Для словаря это
проверяется здесь, без сети; для модели — `run_entry_intent_eval.py`.
"""
from __future__ import annotations

import json
import re

import pytest

from evaluation.entry_intent import load_entry_corpus
from routing.entry_intent import EntryIntent, classify_by_dictionary


# 10.09.2026: 226 диалогов, 194 уникальные первые реплики. Порог ниже на
# случай мелких правок разметки, но не даёт корпусу тихо усохнуть.
MIN_CASES = 190
_LONG_DIGIT_RUN = re.compile(r"(?:\d[\s\-()+]*){10,}")
_UNMASKED_FINGERPRINT = re.compile(r"\[телефон [^\]]+\]")


def test_corpus_is_the_live_stream_not_a_toy_set():
    cases = load_entry_corpus()

    assert len(cases) >= MIN_CASES
    assert {case.expected for case in cases} == set(EntryIntent)
    assert all(case.count >= 1 for case in cases)


def test_corpus_carries_no_contact_details():
    for case in load_entry_corpus():
        assert not _LONG_DIGIT_RUN.search(case.message), case.id
        assert "@" not in case.message, case.id
        assert not _UNMASKED_FINGERPRINT.search(case.message), case.id


def test_dictionary_never_takes_money_away_from_the_questionnaire():
    lost = [
        case.id
        for case in load_entry_corpus()
        if case.expected is EntryIntent.MONEY
        and classify_by_dictionary(case.message)
        in {EntryIntent.EXISTING, EntryIntent.OFF_TOPIC}
    ]

    assert lost == []


def test_dictionary_never_decides_off_topic():
    assert all(
        classify_by_dictionary(case.message) is not EntryIntent.OFF_TOPIC
        for case in load_entry_corpus()
    )


def test_dictionary_catches_at_least_half_of_the_obvious_existing_requests():
    existing = [
        case for case in load_entry_corpus() if case.expected is EntryIntent.EXISTING
    ]
    caught = [
        case
        for case in existing
        if classify_by_dictionary(case.message) is EntryIntent.EXISTING
    ]

    assert len(caught) * 2 >= len(existing)


@pytest.mark.parametrize(
    ("broken", "reason"),
    [
        ({"id": "x", "message": "привет", "expected": "maybe"}, "maybe"),
        ({"id": "x", "message": "  ", "expected": "unclear"}, "пуст"),
        ({"id": "x", "message": "привет", "expected": "unclear", "extra": 1}, "лишн"),
        ({"id": "x", "message": "привет", "expected": "unclear", "count": 0}, "count"),
    ],
)
def test_loader_rejects_malformed_cases(tmp_path, broken, reason):
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps([broken], ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match=reason):
        load_entry_corpus(path)


def test_loader_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "corpus.json"
    case = {"id": "x", "message": "привет", "expected": "unclear"}
    path.write_text(json.dumps([case, case], ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="повтор"):
        load_entry_corpus(path)
```

- [ ] **Step 2: Убедиться, что падают**

Run: `.venv313/Scripts/python -m pytest tests/test_entry_intent_corpus.py -q`
Expected: ERROR при сборе — `ModuleNotFoundError: No module named 'evaluation.entry_intent'`.

- [ ] **Step 3: Реализовать** — `evaluation/entry_intent.py`:

```python
"""Корпус первых реплик для развилки на входе.

Живой поток из боевой базы, а не выдуманный набор: первые реплики клиентов с
20.08.2026, дубли схлопнуты (поле `count` — сколько диалогов начались этой
репликой), ожидаемый исход проставлен вручную. Персональные данные вычищены
при выгрузке: телефоны и номера — маркерами, имена — «[имя]».

Спека: docs/superpowers/specs/2026-09-09-dialog-entry-and-order-design.md.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from routing.entry_intent import EntryIntent


DEFAULT_CORPUS_PATH = Path(__file__).with_name("entry_intent_corpus.json")
_ALLOWED_KEYS = frozenset({"id", "message", "expected", "count", "note"})


@dataclass(frozen=True)
class EntryCase:
    """Одна первая реплика и ожидаемый исход."""

    id: str
    message: str
    expected: EntryIntent
    count: int = 1


def load_entry_corpus(path: Path = DEFAULT_CORPUS_PATH) -> list[EntryCase]:
    """Читает корпус и падает на любом нарушении формата, а не молча."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("корпус должен быть JSON-массивом")

    cases: list[EntryCase] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"[{index}] элемент корпуса — не объект")
        extra = set(item) - _ALLOWED_KEYS
        if extra:
            raise ValueError(f"[{index}] лишние ключи: {sorted(extra)}")
        case_id = item.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"[{index}] нет id")
        if case_id in seen:
            raise ValueError(f"повтор id {case_id}")
        message = item.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ValueError(f"{case_id}: пустая реплика")
        try:
            expected = EntryIntent(item.get("expected"))
        except ValueError as exc:
            raise ValueError(f"{case_id}: {exc}") from exc
        count = item.get("count", 1)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError(f"{case_id}: count должен быть целым от 1")
        seen.add(case_id)
        cases.append(EntryCase(case_id, message, expected, count))
    return cases
```

- [ ] **Step 4: Проверить**

Run: `.venv313/Scripts/python -m pytest tests/test_entry_intent_corpus.py -q`
Expected: all passed.

Если падает `test_dictionary_never_takes_money_away_from_the_questionnaire` — какой-то шаблон `_EXISTING_PATTERNS` из Task 2 цепляет реплику о деньгах. **Сузить или удалить этот шаблон**, не добавляя исключений под конкретную реплику, и дописать её в `test_money_requests_stay_money` из Task 2. Если падает порог «половина existing» — добавить шаблон по формулировке из корпуса и продублировать её в `test_obvious_existing_requests`. Не выходит ни то, ни другое — остановиться и сообщить контроллеру с номерами реплик.

Run: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`
Expected: ruff чисто; все тесты зелёные.

- [ ] **Step 5: Коммит**

```bash
git add evaluation/entry_intent.py tests/test_entry_intent_corpus.py
git commit -m "Загрузка корпуса первых реплик и проверка словаря на живом потоке"
```

(Если на Step 4 правился словарь, добавить в коммит `routing/entry_intent.py` и `tests/test_entry_intent_dictionary.py`.)

---

### Task 4: Флаг MBK_ENTRY_ROUTING

**Files:**
- Modify: `config.py` — поле `AppConfig` после `second_realty_enabled` и аргумент в `load_config()`
- Modify: `tests/conftest.py` — фикстура `predictable_environment`
- Modify: `.env.example` и `deploy/prod/env.example` — после блока `MBK_SITE_CONSENT_PAUSE_ENABLED`
- Test: `tests/test_entry_routing_flag.py`

**Interfaces:**
- Consumes: —
- Produces: `AppConfig.entry_routing_enabled: bool = False`; `load_config()` включает его значениями `1`, `true`, `yes` в `MBK_ENTRY_ROUTING` (регистр не важен).

- [ ] **Step 1: Написать падающие тесты** — `tests/test_entry_routing_flag.py`:

```python
"""Флаг развилки на входе: выключен, пока его не включили явно."""
from __future__ import annotations

from pathlib import Path

import pytest

from config import AppConfig, load_config


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "sk-or-test")


def test_entry_routing_is_off_by_default(monkeypatch):
    monkeypatch.delenv("MBK_ENTRY_ROUTING", raising=False)

    assert load_config().entry_routing_enabled is False
    assert AppConfig(openrouter_api_key="sk-or-test").entry_routing_enabled is False


@pytest.mark.parametrize("raw", ["1", "true", "yes", "TRUE"])
def test_entry_routing_switches_on_explicitly(monkeypatch, raw):
    monkeypatch.setenv("MBK_ENTRY_ROUTING", raw)

    assert load_config().entry_routing_enabled is True


@pytest.mark.parametrize("raw", ["0", "false", "no", ""])
def test_anything_else_keeps_it_off(monkeypatch, raw):
    monkeypatch.setenv("MBK_ENTRY_ROUTING", raw)

    assert load_config().entry_routing_enabled is False


def test_env_templates_list_the_flag_switched_off():
    """Ветка поставки не содержит файлов нашего стенда — проверяем те, что есть."""
    for template in (ROOT / ".env.example", ROOT / "deploy" / "prod" / "env.example"):
        if template.exists():
            assert "MBK_ENTRY_ROUTING=0" in template.read_text(encoding="utf-8"), template
```

- [ ] **Step 2: Убедиться, что падают**

Run: `.venv313/Scripts/python -m pytest tests/test_entry_routing_flag.py -q`
Expected: FAIL — `AttributeError: 'AppConfig' object has no attribute 'entry_routing_enabled'` и отсутствие строки в шаблонах.

- [ ] **Step 3: Реализовать**

В `config.py`, в `AppConfig` после поля `second_realty_enabled: bool = False`:

```python
    # Развилка на входе (docs/superpowers/specs/2026-09-09-dialog-entry-and-
    # order-design.md): первая реплика клиента делится на «деньги», «уже
    # оставлял заявку» и «не наш профиль». Выключено по умолчанию, и
    # выключение возвращает поведение без развилки целиком — вместе с
    # промптом извлекателя.
    entry_routing_enabled: bool = False
```

В `load_config()`, последним аргументом `AppConfig(...)` после `second_realty_enabled=...`:

```python
        entry_routing_enabled=(_read_env_value("MBK_ENTRY_ROUTING") or "").lower()
        in {"1", "true", "yes"},
```

В `tests/conftest.py`, в `predictable_environment` после `monkeypatch.delenv("MBK_TRUSTED_PROXY_HOPS", raising=False)`:

```python
    # Флаг развилки из локального .env включил бы её во всех тестах, которые
    # про поведение без развилки. Тест самой развилки включает её сам.
    monkeypatch.delenv("MBK_ENTRY_ROUTING", raising=False)
```

В `.env.example` и в `deploy/prod/env.example` — отдельным блоком после строки `MBK_SITE_CONSENT_PAUSE_ENABLED=0` и её поясняющих строк (перед следующим блоком `# ===`):

```
# === Развилка на входе ===
# Первая реплика клиента делится на «деньги», «уже оставлял заявку» и «не наш
# профиль». В двух последних ветках анкета не начинается, а в ветке «уже
# оставлял заявку» лид не создаётся. 0 — поведение без развилки целиком.
MBK_ENTRY_ROUTING=0
```

- [ ] **Step 4: Проверить**

Run: `.venv313/Scripts/python -m pytest tests/test_entry_routing_flag.py tests/test_deploy_config_agreement.py tests/test_prod_package.py -q`
Expected: all passed.
Run: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`
Expected: ruff чисто; все тесты зелёные.

- [ ] **Step 5: Коммит**

```bash
git add config.py tests/conftest.py .env.example deploy/prod/env.example tests/test_entry_routing_flag.py
git commit -m "Флаг MBK_ENTRY_ROUTING для развилки на входе, выключен по умолчанию"
```

---

### Task 5: Извлекатель возвращает намерение тем же вызовом

**Files:**
- Modify: `interpretation/contracts.py` — импорт и поле в `FactCandidateExtraction`
- Modify: `interpretation/prompt.py` — `build_interpretation_system_prompt`
- Modify: `llm_agent.py` — `_parse_candidate_extraction`, `extract_fact_candidates`, новая `_parse_entry_intent`
- Test: `tests/test_entry_intent_extractor.py`

**Interfaces:**
- Consumes: `EntryIntent` (Task 2); `AppConfig.entry_routing_enabled` (Task 4).
- Produces:
  - `FactCandidateExtraction.entry_intent: EntryIntent | None = None`
  - `build_interpretation_system_prompt(matrix: Matrix, *, with_entry_intent: bool = False) -> str`
  - `llm_agent._parse_entry_intent(raw: Any) -> EntryIntent | None` — мусор и отсутствие дают `None`.
  - `extract_fact_candidates(...)` при `config.entry_routing_enabled` просит у модели поле `entry_intent`; при выключенном флаге промпт и запрос прежние.

- [ ] **Step 1: Написать падающие тесты** — `tests/test_entry_intent_extractor.py`:

```python
"""Намерение приходит тем же вызовом извлекателя — ни лишнего вызова, ни секунды."""
from __future__ import annotations

import json

import llm_agent
from config import AppConfig
from interpretation.contracts import FactCandidateExtraction
from interpretation.prompt import build_interpretation_system_prompt
from routing.entry_intent import EntryIntent


PAYLOAD = {
    "facts": {},
    "short_history": [],
    "latest_user_message": "Что по моей заявке?",
    "internal_summary": "",
    "turn_instruction": "",
    "expected_paths": [],
}


def test_contract_field_is_optional():
    assert FactCandidateExtraction().entry_intent is None
    assert (
        FactCandidateExtraction.model_validate({"entry_intent": "existing"}).entry_intent
        is EntryIntent.EXISTING
    )


def test_parser_reads_the_intent(matrix):
    extraction = llm_agent._parse_candidate_extraction(
        {"candidates": [], "entry_intent": " Off_Topic "}, matrix
    )

    assert extraction.entry_intent is EntryIntent.OFF_TOPIC


def test_missing_intent_is_none(matrix):
    extraction = llm_agent._parse_candidate_extraction({"candidates": []}, matrix)

    assert extraction.entry_intent is None


def test_garbage_intent_is_none_and_costs_no_facts(matrix):
    """Поле добавочное: сломанное намерение не должно стоить хода фактов."""
    extraction = llm_agent._parse_candidate_extraction(
        {
            "candidates": [
                {"path": "request.amount", "value": 300000, "evidence": "300 тысяч"}
            ],
            "entry_intent": "дайте денег",
        },
        matrix,
    )

    assert extraction.entry_intent is None
    assert any(c.path == "request.amount" for c in extraction.candidates)


def test_prompt_without_the_flag_is_todays_prompt(matrix):
    assert "entry_intent" not in build_interpretation_system_prompt(matrix)


def test_prompt_with_the_flag_asks_for_the_intent_by_meaning(matrix):
    prompt = build_interpretation_system_prompt(matrix, with_entry_intent=True)

    assert '"entry_intent"' in prompt
    for value in ("money", "existing", "off_topic", "unclear"):
        assert f'"{value}"' in prompt


def _fake_model(monkeypatch, answer: dict) -> list[dict]:
    bodies: list[dict] = []

    def fake_post(config, body, *, max_attempts=3):
        bodies.append(body)
        return {
            "choices": [
                {"message": {"content": json.dumps(answer, ensure_ascii=False)}}
            ]
        }

    monkeypatch.setattr(llm_agent, "_post", fake_post)
    monkeypatch.setattr(llm_agent.observability, "_get_client", lambda: None)
    return bodies


def test_flag_on_asks_the_model_for_the_intent(monkeypatch, matrix):
    bodies = _fake_model(
        monkeypatch, {"boundaries": {}, "candidates": [], "entry_intent": "existing"}
    )
    config = AppConfig(openrouter_api_key="sk-or-test", entry_routing_enabled=True)

    extraction = llm_agent.extract_fact_candidates(PAYLOAD, config, matrix=matrix)

    assert extraction.entry_intent is EntryIntent.EXISTING
    assert len(bodies) == 1
    system, user = bodies[0]["messages"]
    assert '"entry_intent"' in system["content"]
    assert "entry_intent" in user["content"]


def test_flag_off_keeps_todays_request(monkeypatch, matrix):
    bodies = _fake_model(monkeypatch, {"boundaries": {}, "candidates": []})
    config = AppConfig(openrouter_api_key="sk-or-test")

    extraction = llm_agent.extract_fact_candidates(PAYLOAD, config, matrix=matrix)

    assert extraction.entry_intent is None
    system, user = bodies[0]["messages"]
    assert "entry_intent" not in system["content"]
    assert "entry_intent" not in user["content"]
```

- [ ] **Step 2: Убедиться, что падают**

Run: `.venv313/Scripts/python -m pytest tests/test_entry_intent_extractor.py -q`
Expected: FAIL — `extra_forbidden` на поле `entry_intent`; `TypeError: ... unexpected keyword argument 'with_entry_intent'`; адаптер не читает намерение из ответа модели.

- [ ] **Step 3: Контракт** — в `interpretation/contracts.py` после строки `from pydantic import BaseModel, ConfigDict, Field, model_validator` добавить

```python

from routing.entry_intent import EntryIntent
```

и в `FactCandidateExtraction` последним полем после `issues`:

```python
    # Намерение реплики для развилки на входе. None — модель поле не вернула
    # или вернула не то: тогда решает словарь-страховка (routing/entry_intent.py).
    entry_intent: EntryIntent | None = None
```

- [ ] **Step 4: Промпт** — в `interpretation/prompt.py` над `def build_interpretation_system_prompt` добавить константу:

```python
_ENTRY_INTENT_SECTION = """
Отдельно от фактов определи, с чем человек пришёл, и верни это в поле
"entry_intent". Суди по смыслу реплики целиком, а не по отдельным словам:
люди пишут с ошибками и без знаков препинания.
- "money" — всё, что касается денег и долгов самого человека: нужен кредит,
  займ, рефинансирование, деньги под залог; есть долги, просрочки,
  коллекторы, приставы, мысли о банкротстве; вопрос об условиях и ставках;
  вопрос о своём кредите в другом банке.
- "existing" — человек спрашивает о заявке или обращении, которые УЖЕ
  оставил: что с ней, какой статус, когда позвонят, почему не перезвонили.
  Даже если при этом упомянуты кредит или сумма — это "existing": заявка
  уже есть.
- "off_topic" — вопрос не про деньги и не про долги этого человека: карты,
  переводы, как дозвониться до поддержки другого банка, налоги, вклады,
  инвестиции, работа.
- "unclear" — приветствие без сути, непонятный текст, или ты не уверен.
Если сомневаешься, "money" это или что-то другое, — выбирай "unclear", а не
"off_topic" и не "existing": ошибка в сторону анкеты исправима, а уведённый
от неё человек потерян.
Поле "entry_intent" заполняй всегда, даже если кандидатов нет.
""".strip()
```

Сигнатуру заменить на

```python
def build_interpretation_system_prompt(
    matrix: Matrix,
    *,
    with_entry_intent: bool = False,
) -> str:
```

Перед `return f"""` добавить

```python
    json_shape = (
        '{"boundaries": {...}, "candidates": [...], "entry_intent": "..."}'
        if with_entry_intent
        else '{"boundaries": {...}, "candidates": [...]}'
    )
    entry_block = f"{_ENTRY_INTENT_SECTION}\n\n" if with_entry_intent else ""
```

В теле f-строки заменить строку

```
Верни только JSON: {{"boundaries": {{...}}, "candidates": [...]}}.
```

на

```
Верни только JSON: {json_shape}.
```

и строку `Каталог:` заменить на `{entry_block}Каталог:`. Больше в промпте ничего не менять.

- [ ] **Step 5: Адаптер** — в `llm_agent.py` рядом с `from routing import Matrix, get_matrix` добавить `from routing.entry_intent import EntryIntent`.

В `_parse_candidate_extraction` заменить финальный `return`:

```python
    return FactCandidateExtraction(
        candidates=filtered,
        boundaries=boundaries,
        issues=[*issues, *policy_issues],
        entry_intent=_parse_entry_intent(value.get("entry_intent")),
    )


def _parse_entry_intent(raw: Any) -> EntryIntent | None:
    """Терпимо: мусор в поле равен его отсутствию — тогда решает словарь.

    Поле добавочное. Сломанное намерение не должно стоить хода фактов: факты
    и границы из того же ответа остаются в силе. Issue не пишется намеренно —
    у списка issues потолок 12, и лишняя запись могла бы уронить весь ответ.
    """
    if not isinstance(raw, str):
        return None
    try:
        return EntryIntent(raw.strip().casefold())
    except ValueError:
        return None
```

В `extract_fact_candidates` после `safe_payload = mask_payload_for_llm(...)` добавить

```python
    answer_shape = (
        '{"boundaries": {...}, "candidates": [...], "entry_intent": "..."}'
        if config.entry_routing_enabled
        else '{"boundaries": {...}, "candidates": [...]}'
    )
```

в `content` заменить последнюю строку `'Верни только JSON-объект {"boundaries": {...}, "candidates": [...]}.'` на `f"Верни только JSON-объект {answer_shape}."`, а в `messages` вызов системного промпта — на

```python
            "content": build_interpretation_system_prompt(
                active_matrix,
                with_entry_intent=config.entry_routing_enabled,
            ),
```

- [ ] **Step 6: Проверить**

Run: `.venv313/Scripts/python -m pytest tests/test_entry_intent_extractor.py tests/test_extractor_output.py tests/test_fact_shadow.py tests/test_fact_interpretation.py -q`
Expected: all passed.
Run: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`
Expected: ruff чисто; все тесты зелёные.

- [ ] **Step 7: Коммит**

```bash
git add interpretation/contracts.py interpretation/prompt.py llm_agent.py tests/test_entry_intent_extractor.py
git commit -m "Извлекатель возвращает намерение первой реплики тем же вызовом"
```

---

### Task 6: Политика сценариев

**Files:**
- Create: `entry_routing.py` (корень репозитория, рядом с `attribution.py`)
- Test: `tests/test_entry_routing.py`

**Interfaces:**
- Consumes: `EntryIntent`, `classify_by_dictionary` (Task 2).
- Produces (все имена — модуль `entry_routing`):
  - константы `QUESTIONNAIRE = "questionnaire"`, `EXISTING = "existing"`, `OFF_TOPIC = "off_topic"`, `TRANSITION_KEY = "_entry_transition"`
  - `is_first_client_message(state: dict) -> bool`
  - `next_scenario(current: str | None, intent: EntryIntent, *, first_message: bool) -> str`
  - `current_scenario(state: dict) -> str`, `questionnaire_active(state: dict) -> bool`, `lead_blocked(state: dict) -> bool`
  - `route_turn(state: dict, *, user_message: str, model_intent: EntryIntent | None, first_message: bool) -> None` — пишет `state["entry"] = {"scenario", "intent", "source"}` (`source` — `"model"` или `"dictionary"`) и `state[TRANSITION_KEY]` на первой реплике и при смене сценария.

- [ ] **Step 1: Написать падающие тесты** — `tests/test_entry_routing.py`:

```python
"""Какой сценарий ведёт разговор: решение по первой реплике и возврат в анкету."""
from __future__ import annotations

import pytest

import entry_routing as er
from routing.entry_intent import EntryIntent as I


@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        (I.EXISTING, er.EXISTING),
        (I.OFF_TOPIC, er.OFF_TOPIC),
        (I.MONEY, er.QUESTIONNAIRE),
        # fail-open: непонятное ведёт в анкету, как без развилки
        (I.UNCLEAR, er.QUESTIONNAIRE),
    ],
)
def test_first_message_picks_the_scenario(intent, expected):
    assert er.next_scenario(None, intent, first_message=True) == expected


@pytest.mark.parametrize("intent", list(I))
def test_questionnaire_never_leaves_after_the_first_message(intent):
    """Иначе слово «заявка» на пятом вопросе перекинет разговор в другую ветку."""
    assert (
        er.next_scenario(er.QUESTIONNAIRE, intent, first_message=False)
        == er.QUESTIONNAIRE
    )


@pytest.mark.parametrize("branch", [er.EXISTING, er.OFF_TOPIC])
def test_money_always_returns_to_the_questionnaire(branch):
    assert er.next_scenario(branch, I.MONEY, first_message=False) == er.QUESTIONNAIRE


@pytest.mark.parametrize("intent", [I.UNCLEAR, I.OFF_TOPIC, I.EXISTING])
def test_existing_branch_is_left_only_for_money(intent):
    """Ветка держит запрет второго лида: уйти из неё в «не наш профиль» значило
    бы снять запрет с человека, у которого заявка уже есть."""
    assert er.next_scenario(er.EXISTING, intent, first_message=False) == er.EXISTING


def test_off_topic_turns_into_existing_when_asked_about_an_application():
    assert (
        er.next_scenario(er.OFF_TOPIC, I.EXISTING, first_message=False) == er.EXISTING
    )


@pytest.mark.parametrize("intent", [I.UNCLEAR, I.OFF_TOPIC])
def test_off_topic_stays_off_topic_otherwise(intent):
    assert er.next_scenario(er.OFF_TOPIC, intent, first_message=False) == er.OFF_TOPIC


def test_missing_or_broken_entry_reads_as_questionnaire():
    assert er.current_scenario({}) == er.QUESTIONNAIRE
    assert er.current_scenario({"entry": "мусор"}) == er.QUESTIONNAIRE
    assert er.current_scenario({"entry": {"scenario": "unknown"}}) == er.QUESTIONNAIRE
    assert er.questionnaire_active({}) is True


def test_only_the_existing_branch_blocks_the_lead():
    assert er.lead_blocked({"entry": {"scenario": er.EXISTING}}) is True
    assert er.lead_blocked({"entry": {"scenario": er.OFF_TOPIC}}) is False
    assert er.lead_blocked({"entry": {"scenario": er.QUESTIONNAIRE}}) is False
    assert er.lead_blocked({}) is False


def test_first_client_message_is_detected_by_history():
    opening = {"role": "assistant", "content": "Добрый день!"}

    assert er.is_first_client_message({"chat_history": [opening]}) is True
    assert er.is_first_client_message({}) is True
    assert (
        er.is_first_client_message(
            {"chat_history": [opening, {"role": "user", "content": "Здравствуйте"}]}
        )
        is False
    )


def test_model_intent_wins_over_the_dictionary():
    state: dict = {}

    er.route_turn(
        state, user_message="Здравствуйте", model_intent=I.OFF_TOPIC, first_message=True
    )

    assert state["entry"] == {
        "scenario": er.OFF_TOPIC,
        "intent": "off_topic",
        "source": "model",
    }
    assert state[er.TRANSITION_KEY]["scenario"] == er.OFF_TOPIC


def test_dictionary_decides_when_the_model_is_silent():
    state: dict = {}

    er.route_turn(
        state,
        user_message="Оставлял заявку вчера, когда позвонит менеджер?",
        model_intent=None,
        first_message=True,
    )

    assert state["entry"]["scenario"] == er.EXISTING
    assert state["entry"]["source"] == "dictionary"


def test_unclear_first_message_is_recorded_as_questionnaire():
    """Разрез по сценариям нужен и для анкеты: иначе знаменатель не собрать."""
    state: dict = {}

    er.route_turn(state, user_message="Здравствуйте", model_intent=None, first_message=True)

    assert state["entry"] == {
        "scenario": er.QUESTIONNAIRE,
        "intent": "unclear",
        "source": "dictionary",
    }
    assert state[er.TRANSITION_KEY]["scenario"] == er.QUESTIONNAIRE


def test_session_already_in_the_questionnaire_is_not_reclassified():
    """Сессия, начатая до включения флага, не разворачивается посреди анкеты."""
    state: dict = {}

    er.route_turn(
        state,
        user_message="А что с моей заявкой?",
        model_intent=I.EXISTING,
        first_message=False,
    )

    assert "entry" not in state
    assert er.TRANSITION_KEY not in state


def test_staying_in_a_branch_is_not_a_transition():
    state = {"entry": {"scenario": er.EXISTING, "intent": "existing", "source": "model"}}

    er.route_turn(state, user_message="ну и ладно", model_intent=I.UNCLEAR, first_message=False)

    assert state["entry"]["scenario"] == er.EXISTING
    assert er.TRANSITION_KEY not in state


def test_return_to_the_questionnaire_is_a_transition():
    state = {"entry": {"scenario": er.OFF_TOPIC, "intent": "off_topic", "source": "model"}}

    er.route_turn(
        state,
        user_message="а вообще мне нужны деньги",
        model_intent=I.MONEY,
        first_message=False,
    )

    assert state["entry"]["scenario"] == er.QUESTIONNAIRE
    assert state[er.TRANSITION_KEY]["scenario"] == er.QUESTIONNAIRE
```

- [ ] **Step 2: Убедиться, что падают**

Run: `.venv313/Scripts/python -m pytest tests/test_entry_routing.py -q`
Expected: ERROR при сборе — `ModuleNotFoundError: No module named 'entry_routing'`.

- [ ] **Step 3: Реализовать** — `entry_routing.py`:

```python
"""Развилка на входе: какой сценарий ведёт разговор.

Спека: docs/superpowers/specs/2026-09-09-dialog-entry-and-order-design.md,
компоненты 1 и 2. Решение принимается по первой реплике клиента и хранится в
`state["entry"]`. Движок и матрица о нём не знают: план считается как всегда,
а ветка не даёт ему прозвучать (`core.turn_instruction`, `core.commit_turn`).

Сценариев три:
- QUESTIONNAIRE — анкета, как без развилки. Сюда же уходит «непонятно»:
  fail-open, при сомнении разговор идёт как сегодня;
- EXISTING — человек спрашивает о заявке, которую уже оставил. Анкета не
  начинается, лид не создаётся (`lead_blocked`);
- OFF_TOPIC — вопрос не про деньги и долги. Честный ответ и выход.

Возврат в анкету открыт всегда: названные в ветке деньги возвращают в
QUESTIONNAIRE, и больше разговор из анкеты не уходит.
"""
from __future__ import annotations

from typing import Any

from routing.entry_intent import EntryIntent, classify_by_dictionary


QUESTIONNAIRE = "questionnaire"
EXISTING = "existing"
OFF_TOPIC = "off_topic"
_BRANCHES = frozenset({EXISTING, OFF_TOPIC})

# Ключ с подчёркивания живёт один ход и в базу не уезжает
# (`session_store._persistable_state`). `core.commit_turn` забирает его и пишет
# событие — только за ход, который действительно состоялся.
TRANSITION_KEY = "_entry_transition"


def is_first_client_message(state: dict[str, Any]) -> bool:
    """Клиент ещё ничего не писал: в истории только приветствие бота."""
    history = state.get("chat_history") or []
    return not any(
        isinstance(item, dict) and item.get("role") == "user" for item in history
    )


def next_scenario(
    current: str | None,
    intent: EntryIntent,
    *,
    first_message: bool,
) -> str:
    """Сценарий после этой реплики. Вся политика развилки — здесь."""
    if first_message:
        if intent is EntryIntent.EXISTING:
            return EXISTING
        if intent is EntryIntent.OFF_TOPIC:
            return OFF_TOPIC
        # money и unclear: fail-open, разговор идёт как без развилки.
        return QUESTIONNAIRE
    if current not in _BRANCHES:
        # Только первая реплика. Иначе слово «заявка» на пятом вопросе
        # перекинуло бы разговор в другую ветку.
        return QUESTIONNAIRE
    if intent is EntryIntent.MONEY:
        # Возврат в анкету открыт всегда: ошибку классификатора лечит сам
        # человек, назвав сумму, кредит или долги.
        return QUESTIONNAIRE
    if intent is EntryIntent.EXISTING:
        return EXISTING
    # Из «уже оставлял заявку» выводят только деньги. Уйти оттуда в «не наш
    # профиль» значило бы снять запрет второго лида с человека, у которого
    # заявка уже есть.
    return current


def current_scenario(state: dict[str, Any]) -> str:
    """Сценарий из state; отсутствующий или битый читается как анкета."""
    entry = state.get("entry")
    scenario = entry.get("scenario") if isinstance(entry, dict) else None
    return scenario if scenario in _BRANCHES else QUESTIONNAIRE


def questionnaire_active(state: dict[str, Any]) -> bool:
    return current_scenario(state) == QUESTIONNAIRE


def lead_blocked(state: dict[str, Any]) -> bool:
    """В ветке «уже оставлял заявку» второй лид не создаётся.

    Главное свойство ветки (спека, компонент 2): именно оно убирает дубли
    карточек. Проверяют все три дороги к лиду — CRM workflow, брошенную
    анкету по таймеру и локальную доставку.
    """
    return current_scenario(state) == EXISTING


def route_turn(
    state: dict[str, Any],
    *,
    user_message: str,
    model_intent: EntryIntent | None,
    first_message: bool,
) -> None:
    """Решает сценарий хода и кладёт его в `state["entry"]`.

    Намерение берётся у модели; словарь — страховка, когда модель не звали,
    она упала или поля не вернула.
    """
    entry = state.get("entry")
    current = entry.get("scenario") if isinstance(entry, dict) else None
    if not first_message and current not in _BRANCHES:
        return
    if model_intent is not None:
        intent, source = model_intent, "model"
    else:
        intent, source = classify_by_dictionary(user_message), "dictionary"
    scenario = next_scenario(current, intent, first_message=first_message)
    decision = {"scenario": scenario, "intent": intent.value, "source": source}
    state["entry"] = decision
    if first_message or scenario != current:
        state[TRANSITION_KEY] = dict(decision)
```

- [ ] **Step 4: Проверить**

Run: `.venv313/Scripts/python -m pytest tests/test_entry_routing.py -q`
Expected: all passed.
Run: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`
Expected: ruff чисто; все тесты зелёные.

- [ ] **Step 5: Коммит**

```bash
git add entry_routing.py tests/test_entry_routing.py
git commit -m "Политика сценариев развилки на входе"
```

---

### Task 7: События развилки в аналитике

**Files:**
- Modify: `analytics/events.py` — класс `EVENTS`
- Modify: `entry_routing.py` — `record_transition`, `_has_phone`, импорт `analytics`
- Modify: `tests/test_analytics_hooks.py` — `test_event_catalogue_covers_only_what_we_own`
- Modify: `docs/HANDOVER.md` — раздел «6. Данные»
- Test: `tests/test_entry_analytics.py`

**Interfaces:**
- Consumes: `entry_routing.EXISTING`, формат перехода из `route_turn` (Task 6).
- Produces:
  - `analytics.EVENTS.ENTRY_ROUTED == "entry_routed"`, `analytics.EVENTS.EXISTING_CLIENT_REQUEST == "existing_client_request"`
  - `entry_routing.record_transition(state: dict, transition: dict | None) -> None` — payload `entry_routed`: `{"scenario", "intent", "source", "turn"}`; payload `existing_client_request`: `{"phone_given": bool, "turn"}`, `dedup_key=f"{session_id}:existing_client_request"`.

- [ ] **Step 1: Написать падающие тесты** — `tests/test_entry_analytics.py`:

```python
"""События развилки: разрез по сценариям и выгрузка «кто просил связаться»."""
from __future__ import annotations

import pytest

import analytics
import entry_routing as er
from analytics import EVENTS, AnalyticsRecorder, MemorySink


@pytest.fixture
def sink():
    memory = MemorySink()
    analytics.set_recorder(AnalyticsRecorder(memory))
    yield memory
    analytics.set_recorder(None)


def _state(**extra):
    return {"session_id": "entry-analytics", "message_count": 2, **extra}


def test_every_decision_is_recorded_with_its_scenario(sink):
    er.record_transition(
        _state(), {"scenario": er.OFF_TOPIC, "intent": "off_topic", "source": "model"}
    )

    (event,) = sink.events
    assert event.event == EVENTS.ENTRY_ROUTED == "entry_routed"
    assert event.payload == {
        "scenario": "off_topic",
        "intent": "off_topic",
        "source": "model",
        "turn": 2,
    }


def test_existing_request_is_recorded_once_without_personal_data(sink):
    state = _state(facts={"client": {"phone": "+79991234567", "name": "Иван"}})

    er.record_transition(
        state, {"scenario": er.EXISTING, "intent": "existing", "source": "dictionary"}
    )

    (request,) = [e for e in sink.events if e.event == EVENTS.EXISTING_CLIENT_REQUEST]
    assert EVENTS.EXISTING_CLIENT_REQUEST == "existing_client_request"
    assert request.payload == {"phone_given": True, "turn": 2}
    assert request.dedup_key == "entry-analytics:existing_client_request"


def test_phone_not_given_is_recorded_as_false(sink):
    er.record_transition(
        _state(), {"scenario": er.EXISTING, "intent": "existing", "source": "model"}
    )

    (request,) = [e for e in sink.events if e.event == EVENTS.EXISTING_CLIENT_REQUEST]
    assert request.payload["phone_given"] is False


def test_nothing_is_recorded_without_a_transition_or_a_session(sink):
    er.record_transition(_state(), None)
    er.record_transition(
        {"message_count": 1},
        {"scenario": er.EXISTING, "intent": "existing", "source": "model"},
    )

    assert sink.events == []
```

И в `tests/test_analytics_hooks.py`, в `test_event_catalogue_covers_only_what_we_own`, после `assert EVENTS.LEAD_SUBMITTED == "lead_submitted"`:

```python
    assert EVENTS.ENTRY_ROUTED == "entry_routed"
    assert EVENTS.EXISTING_CLIENT_REQUEST == "existing_client_request"
```

а в его docstring перед закрывающими кавычками — абзац:

```
    `ENTRY_ROUTED` и `EXISTING_CLIENT_REQUEST` появились 10.09.2026 вместе с
    развилкой на входе. Какой сценарий ведёт разговор, CRM не знает — считать
    это больше некому, так что правило «CRM считает своё» не нарушено.
```

- [ ] **Step 2: Убедиться, что падают**

Run: `.venv313/Scripts/python -m pytest tests/test_entry_analytics.py tests/test_analytics_hooks.py -q`
Expected: FAIL — `AttributeError: module 'entry_routing' has no attribute 'record_transition'` и `type object 'EVENTS' has no attribute 'ENTRY_ROUTED'`.

- [ ] **Step 3: Каталог** — в `analytics/events.py`, в `class EVENTS` после `LEAD_SUBMITTED = "lead_submitted"`:

```python
    # Развилка на входе (спека 09.09.2026). `entry_routed` — какой сценарий
    # ведёт разговор: пишется на первой реплике и при каждой смене сценария.
    # По нему считается разрез «сколько диалогов ушло в каждую ветку», и по
    # нему же разговоры веток исключаются из воронки анкеты.
    ENTRY_ROUTED = "entry_routed"
    # Обращение человека, который уже оставлял заявку: выгрузка «кто просил
    # связаться». Лид по такому обращению не создаётся намеренно.
    EXISTING_CLIENT_REQUEST = "existing_client_request"
```

- [ ] **Step 4: Запись** — в `entry_routing.py` добавить `import analytics` над `from routing.entry_intent import ...` и в конец модуля:

```python
def record_transition(
    state: dict[str, Any],
    transition: dict[str, Any] | None,
) -> None:
    """Пишет смену сценария в аналитику. Никогда не влияет на ответ клиенту.

    `existing_client_request` — один раз за сессию, при входе в ветку «уже
    оставлял заявку». В payload только признак, назвал ли человек телефон к
    этому моменту: сам номер лежит в `sessions`, куда у аналитика доступа нет.
    """
    if not transition:
        return
    session_id = str(state.get("session_id") or "")
    if not session_id:
        return
    recorder = analytics.get_recorder()
    turn = state.get("message_count")
    scenario = transition.get("scenario")
    recorder.emit(
        analytics.EVENTS.ENTRY_ROUTED,
        session_id,
        {
            "scenario": scenario,
            "intent": transition.get("intent"),
            "source": transition.get("source"),
            "turn": turn,
        },
    )
    if scenario == EXISTING:
        recorder.emit(
            analytics.EVENTS.EXISTING_CLIENT_REQUEST,
            session_id,
            {"phone_given": _has_phone(state), "turn": turn},
            dedup_key=f"{session_id}:existing_client_request",
        )


def _has_phone(state: dict[str, Any]) -> bool:
    phone = ((state.get("facts") or {}).get("client") or {}).get("phone")
    return isinstance(phone, str) and bool(phone.strip())
```

- [ ] **Step 5: Документ передачи** — в `docs/HANDOVER.md`, раздел «6. Данные»:

Первый абзац про `analytics_events` — заменить перечень «старт чата, завершённый ход, завершённая квалификация, созданный лид» на «старт чата, завершённый ход, завершённая квалификация, созданный лид, сценарий разговора и обращение того, кто уже оставлял заявку».

SQL-запрос «лид до CRM не доехал» заменить на:

```sql
SELECT d.session_id
FROM dialog_messages d
LEFT JOIN analytics_events e
  ON e.session_id = d.session_id AND e.event = 'lead_submitted'
WHERE d.role = 'client' AND d.message LIKE '%[телефон %'
  AND e.id IS NULL
  -- ветка «уже оставлял заявку»: лида там нет намеренно
  AND COALESCE((
    SELECT x.payload->>'scenario' FROM analytics_events x
    WHERE x.session_id = d.session_id AND x.event = 'entry_routed'
    ORDER BY x.occurred_at DESC LIMIT 1
  ), 'questionnaire') <> 'existing'
GROUP BY d.session_id;
```

И сразу после запроса — абзац:

```
**События развилки на входе** (пишутся при `MBK_ENTRY_ROUTING=1`).
`entry_routed` — какой сценарий ведёт разговор: `payload->>'scenario'` равно
`questionnaire` (анкета), `existing` (уже оставлял заявку) или `off_topic`
(не наш профиль). Пишется на первой реплике клиента и при каждой смене
сценария — например, когда человек из ветки назвал сумму и вернулся в
анкету. В ветках анкета не идёт, поэтому `turn_completed` там не пишется;
воронку анкеты считайте по разговорам, чей последний `entry_routed` —
`questionnaire`. `existing_client_request` — обращение человека, который уже
оставлял заявку: выгрузка «кто просил связаться»; `payload->>'phone_given'` —
назвал ли он телефон к этому моменту. Лид по такому обращению не создаётся
намеренно: иначе в CRM появляется дубль карточки.
```

- [ ] **Step 6: Проверить**

Run: `.venv313/Scripts/python -m pytest tests/test_entry_analytics.py tests/test_analytics_hooks.py tests/test_analytics.py -q`
Expected: all passed.
Run: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`
Expected: ruff чисто; все тесты зелёные.

- [ ] **Step 7: Коммит**

```bash
git add analytics/events.py entry_routing.py tests/test_entry_analytics.py tests/test_analytics_hooks.py docs/HANDOVER.md
git commit -m "События развилки: разрез по сценариям и обращения по уже поданной заявке"
```

---

### Task 8: Решение о сценарии внутри хода

**Files:**
- Modify: `core.py` — импорт; конец блока `if user_message:` в `prepare_turn`
- Test: `tests/test_entry_routing_turn.py`

**Interfaces:**
- Consumes: `AppConfig.entry_routing_enabled` (Task 4); `FactCandidateExtraction.entry_intent` (Task 5); `entry_routing.route_turn`, `is_first_client_message`, `TRANSITION_KEY` (Task 6).
- Produces: после `prepare_turn` при включённом флаге в state лежат `entry` и, если сценарий решён или сменился, `_entry_transition`. При выключенном флаге ключа `entry` в новом state нет.

- [ ] **Step 1: Написать падающие тесты** — `tests/test_entry_routing_turn.py`:

```python
"""Развилка внутри хода: откуда берётся намерение и когда решение принимается."""
from __future__ import annotations

import pytest

import core
import entry_routing as er
from config import AppConfig
from interpretation import FactCandidateExtraction
from prompts import OPENING_MESSAGE
from routing.entry_intent import EntryIntent
from state import init_state


def _config(mode: str = "semantic", *, enabled: bool = True) -> AppConfig:
    return AppConfig(
        openrouter_api_key="sk-or-test",
        fact_interpretation_mode=mode,
        entry_routing_enabled=enabled,
    )


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("MBK_DADATA_API", raising=False)
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *a, **k: {"fact_updates": [], "internal_summary": ""},
    )


def _model_says(monkeypatch, intent):
    monkeypatch.setattr(
        core.llm_agent,
        "extract_fact_candidates",
        lambda *a, **k: FactCandidateExtraction(entry_intent=intent),
    )


def _fresh_state() -> dict:
    state = init_state()
    state["session_id"] = "entry-turn"
    state["chat_history"] = [{"role": "assistant", "content": OPENING_MESSAGE}]
    return state


def _in_branch(scenario: str) -> dict:
    state = _fresh_state()
    state["chat_history"].append({"role": "user", "content": "первая реплика"})
    state["chat_history"].append({"role": "assistant", "content": "ответ ветки"})
    state["entry"] = {"scenario": scenario, "intent": scenario, "source": "model"}
    return state


def _prepare(state, message, config):
    return core.prepare_turn(state, message, config, submit_delivery=False)


@pytest.mark.parametrize(
    ("intent", "scenario"),
    [
        (EntryIntent.EXISTING, er.EXISTING),
        (EntryIntent.OFF_TOPIC, er.OFF_TOPIC),
        (EntryIntent.MONEY, er.QUESTIONNAIRE),
        (EntryIntent.UNCLEAR, er.QUESTIONNAIRE),
    ],
)
def test_first_message_is_routed_by_the_model(monkeypatch, intent, scenario):
    _model_says(monkeypatch, intent)

    prepared = _prepare(_fresh_state(), "Здравствуйте", _config())

    assert prepared["entry"] == {
        "scenario": scenario,
        "intent": intent.value,
        "source": "model",
    }
    assert prepared[er.TRANSITION_KEY]["scenario"] == scenario


def test_silent_model_leaves_the_decision_to_the_dictionary(monkeypatch):
    _model_says(monkeypatch, None)

    prepared = _prepare(
        _fresh_state(), "Оставлял заявку, когда позвонит менеджер?", _config()
    )

    assert prepared["entry"]["scenario"] == er.EXISTING
    assert prepared["entry"]["source"] == "dictionary"


def test_failed_model_leaves_the_decision_to_the_dictionary(monkeypatch):
    def broken(*args, **kwargs):
        raise TimeoutError("модель не ответила")

    monkeypatch.setattr(core.llm_agent, "extract_fact_candidates", broken)

    prepared = _prepare(_fresh_state(), "Что по моей заявке?", _config())

    assert prepared["entry"] == {
        "scenario": er.EXISTING,
        "intent": "existing",
        "source": "dictionary",
    }


def test_shadow_extractor_does_not_route_the_live_conversation(monkeypatch):
    """В shadow новый извлекатель диагностический: ни факты, ни намерение
    из него не влияют на разговор."""
    _model_says(monkeypatch, EntryIntent.OFF_TOPIC)

    prepared = _prepare(_fresh_state(), "Здравствуйте", _config("shadow"))

    assert prepared["entry"] == {
        "scenario": er.QUESTIONNAIRE,
        "intent": "unclear",
        "source": "dictionary",
    }


def test_legacy_mode_routes_by_the_dictionary(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("в legacy новый извлекатель не зовут")

    monkeypatch.setattr(core.llm_agent, "extract_fact_candidates", unexpected)

    prepared = _prepare(_fresh_state(), "Подскажите статус заявки", _config("legacy"))

    assert prepared["entry"]["scenario"] == er.EXISTING
    assert prepared["entry"]["source"] == "dictionary"


def test_session_mid_questionnaire_is_not_reclassified(monkeypatch):
    _model_says(monkeypatch, EntryIntent.EXISTING)
    state = _fresh_state()
    state["chat_history"].append({"role": "user", "content": "Нужны деньги"})
    state["chat_history"].append({"role": "assistant", "content": "Какая сумма нужна?"})

    prepared = _prepare(state, "Я же заявку уже оставлял", _config())

    assert "entry" not in prepared
    assert er.TRANSITION_KEY not in prepared


def test_money_in_a_branch_returns_to_the_questionnaire(monkeypatch):
    _model_says(monkeypatch, EntryIntent.MONEY)

    prepared = _prepare(_in_branch(er.OFF_TOPIC), "а вообще нужно 200 тысяч", _config())

    assert prepared["entry"]["scenario"] == er.QUESTIONNAIRE
    assert prepared[er.TRANSITION_KEY]["scenario"] == er.QUESTIONNAIRE


def test_unclear_reply_keeps_the_branch(monkeypatch):
    _model_says(monkeypatch, EntryIntent.UNCLEAR)

    prepared = _prepare(_in_branch(er.EXISTING), "ну ладно", _config())

    assert prepared["entry"]["scenario"] == er.EXISTING
    assert er.TRANSITION_KEY not in prepared


def test_switching_the_flag_off_drops_the_branch(monkeypatch):
    """Выключенный флаг — поведение без развилки целиком, в том числе для
    сессии, попавшей в ветку, пока флаг был включён."""
    _model_says(monkeypatch, EntryIntent.EXISTING)

    prepared = _prepare(_in_branch(er.EXISTING), "ну ладно", _config(enabled=False))

    assert "entry" not in prepared
    assert er.TRANSITION_KEY not in prepared
```

- [ ] **Step 2: Убедиться, что падают**

Run: `.venv313/Scripts/python -m pytest tests/test_entry_routing_turn.py -q`
Expected: FAIL — `KeyError: 'entry'` в тестах с включённым флагом; тест выключенного флага падает, потому что `entry` из state не снимается.

- [ ] **Step 3: Реализовать** — в `core.py` рядом с `import delivery` добавить `import entry_routing`.

В `prepare_turn` блок `if user_message:` заканчивается веткой `elif semantic_mode:` (строки около 357-363, последняя — `runtime_interpretation["runtime_source"] = "local_fastpath"`). Сразу после неё, с тем же отступом в 8 пробелов, что у `elif`, добавить:

```python

        # Развилка на входе (спека 2026-09-09, компоненты 1 и 2). Намерение
        # приходит тем же вызовом извлекателя — ни лишнего вызова, ни лишней
        # секунды. Словарь — страховка, когда модель не звали, она упала или
        # поле не вернула. Shadow не в счёт: там новый извлекатель
        # диагностический и на разговор не влияет.
        if config.entry_routing_enabled:
            model_intent = (
                shadow_extraction.entry_intent
                if semantic_mode and run_shadow and shadow_error is None
                else None
            )
            entry_routing.route_turn(
                new_state,
                user_message=user_message,
                model_intent=model_intent,
                first_message=entry_routing.is_first_client_message(state),
            )

    if not config.entry_routing_enabled:
        # Флаг выключен — разговор ведётся ровно как без развилки, в том числе
        # тот, что попал в ветку, пока флаг был включён: откат полный.
        new_state.pop("entry", None)
```

Строка `if not config.entry_routing_enabled:` стоит на уровне тела функции (4 пробела), до комментария `# Сырой ответ сохраняется ДО обогащения`.

- [ ] **Step 4: Проверить**

Run: `.venv313/Scripts/python -m pytest tests/test_entry_routing_turn.py tests/test_fact_shadow.py tests/test_dialog_flow.py -q`
Expected: all passed.
Run: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`
Expected: ruff чисто; все тесты зелёные.

- [ ] **Step 5: Коммит**

```bash
git add core.py tests/test_entry_routing_turn.py
git commit -m "Сценарий разговора решается по первой реплике внутри хода"
```

---

### Task 9: Ветки говорят своё и не ведут анкету

**Files:**
- Modify: `prompts.py` — две инструкции после `FAREWELL_INSTRUCTION`
- Modify: `core.py` — `turn_instruction` (после проверки `current_plan is None`), `commit_turn`
- Test: `tests/test_entry_scenarios.py`

**Interfaces:**
- Consumes: `entry_routing.current_scenario`, `questionnaire_active`, `record_transition`, `TRANSITION_KEY`, константы сценариев (Tasks 6-7); решение из `prepare_turn` (Task 8).
- Produces: `prompts.EXISTING_CLIENT_INSTRUCTION: str`, `prompts.OFF_TOPIC_INSTRUCTION: str`. В ветке `commit_turn` не добавляет переходных фраз, контактной просьбы, не считает попытки, не пишет `turn_completed` и `qualification_completed`; при переходе пишет события Task 7.

- [ ] **Step 1: Написать падающие тесты** — `tests/test_entry_scenarios.py`:

```python
"""Ветки развилки: что бот говорит и чего не делает."""
from __future__ import annotations

import pytest

import analytics
import core
import entry_routing as er
import prompts
from analytics import AnalyticsRecorder, MemorySink
from analytics import EVENTS as E
from config import AppConfig
from routing import plan as build_plan
from state import init_state


LEGACY_ON = AppConfig(
    openrouter_api_key="sk-or-test",
    fact_interpretation_mode="legacy",
    entry_routing_enabled=True,
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("MBK_DADATA_API", raising=False)
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *a, **k: {"fact_updates": [], "internal_summary": ""},
    )


@pytest.fixture
def sink():
    memory = MemorySink()
    analytics.set_recorder(AnalyticsRecorder(memory))
    yield memory
    analytics.set_recorder(None)


def _fresh_state() -> dict:
    state = init_state()
    state["session_id"] = "entry-scenario"
    state["chat_history"] = [{"role": "assistant", "content": prompts.OPENING_MESSAGE}]
    return state


def _turn(state, message, reply):
    prepared = core.prepare_turn(state, message, LEGACY_ON, submit_delivery=False)
    return core.commit_turn(prepared, message, reply)


def _instruction_state(matrix, scenario, message="Здравствуйте"):
    return {
        "_plan_object": build_plan(matrix, {}),
        "_last_user_message": message,
        "entry": {"scenario": scenario, "intent": scenario, "source": "model"},
    }


def test_existing_branch_gets_its_own_instruction(matrix):
    state = _instruction_state(matrix, er.EXISTING)

    assert core.turn_instruction(state) == prompts.EXISTING_CLIENT_INSTRUCTION


def test_off_topic_branch_gets_its_own_instruction(matrix):
    state = _instruction_state(matrix, er.OFF_TOPIC)

    assert core.turn_instruction(state) == prompts.OFF_TOPIC_INSTRUCTION


@pytest.mark.parametrize("scenario", [er.EXISTING, er.OFF_TOPIC])
def test_goodbye_in_a_branch_is_still_a_goodbye(matrix, scenario):
    state = _instruction_state(matrix, scenario, message="Спасибо, до свидания")

    assert core.turn_instruction(state) == prompts.FAREWELL_INSTRUCTION


def test_questionnaire_instruction_is_untouched(matrix):
    state = _instruction_state(matrix, er.QUESTIONNAIRE)

    assert core.turn_instruction(state) == prompts.turn_instruction(state["_plan_object"])


def test_branch_instructions_forbid_the_questionnaire_and_promises():
    for text in (prompts.EXISTING_CLIENT_INSTRUCTION, prompts.OFF_TOPIC_INSTRUCTION):
        lowered = text.casefold()
        assert "не спрашивай имя" in lowered
        assert "не начинай подбор" in lowered
    assert "не обещай" in prompts.EXISTING_CLIENT_INSTRUCTION.casefold()


def test_existing_client_is_not_asked_for_name_or_questionnaire(sink):
    committed = _turn(
        _fresh_state(),
        "Оставлял заявку вчера, когда позвонит менеджер?",
        "Отсюда заявку не видно.",
    )

    assert committed["messages"] == ["Отсюда заявку не видно."]
    script = committed["current_state"].get("script") or {}
    assert not script.get("attempts")
    assert not script.get("asked")


def test_existing_branch_is_recorded_instead_of_a_questionnaire_step(sink):
    _turn(
        _fresh_state(),
        "Оставлял заявку вчера, когда позвонит менеджер?",
        "Отсюда заявку не видно.",
    )

    names = [event.event for event in sink.events]
    assert E.TURN_COMPLETED not in names
    assert E.QUALIFICATION_COMPLETED not in names
    (routed,) = [e for e in sink.events if e.event == E.ENTRY_ROUTED]
    assert routed.payload["scenario"] == er.EXISTING
    assert routed.payload["source"] == "dictionary"
    (request,) = [e for e in sink.events if e.event == E.EXISTING_CLIENT_REQUEST]
    assert request.payload["phone_given"] is False


def test_money_first_message_keeps_todays_questionnaire(sink):
    """Флаг включён, человек пришёл за деньгами — всё как без развилки:
    имя спрашивается на первой реплике, шаг анкеты пишется."""
    committed = _turn(_fresh_state(), "Нужны деньги", "Сколько нужно?")

    assert "обращаться" in " ".join(committed["messages"])
    assert E.TURN_COMPLETED in [event.event for event in sink.events]
    (routed,) = [e for e in sink.events if e.event == E.ENTRY_ROUTED]
    assert routed.payload["scenario"] == er.QUESTIONNAIRE


def test_return_from_a_branch_starts_the_questionnaire(sink):
    # Словарь «не наш профиль» не решает никогда, поэтому ветку ставим руками —
    # так, как её поставила бы модель на первой реплике.
    state = _fresh_state()
    state["chat_history"].append(
        {"role": "user", "content": "Как разблокировать карту Сбербанка?"}
    )
    state["chat_history"].append({"role": "assistant", "content": "Это к самому банку."})
    state["entry"] = {"scenario": er.OFF_TOPIC, "intent": "off_topic", "source": "model"}

    committed = _turn(state, "А вообще мне нужен кредит", "Какая сумма нужна?")

    assert committed["current_state"]["entry"]["scenario"] == er.QUESTIONNAIRE
    assert "обращаться" in " ".join(committed["messages"])
    (routed,) = [e for e in sink.events if e.event == E.ENTRY_ROUTED]
    assert routed.payload["scenario"] == er.QUESTIONNAIRE
```

- [ ] **Step 2: Убедиться, что падают**

Run: `.venv313/Scripts/python -m pytest tests/test_entry_scenarios.py -q`
Expected: FAIL — `AttributeError: module 'prompts' has no attribute 'EXISTING_CLIENT_INSTRUCTION'`; в ветке к ответу приклеивается «как к вам обращаться», событий `entry_routed` нет.

- [ ] **Step 3: Инструкции** — в `prompts.py` сразу после `FAREWELL_INSTRUCTION`:

```python


# Развилка на входе (спека 2026-09-09, компонент 2). Бизнес-процесс ветки
# «уже оставлял заявку» продумывает заказчик; до него ветка работает в
# минимальном виде — честно и без обещаний. Общее правило 1а системного
# промпта предлагает «оставить номер для связи»: здесь это обещание, которое
# никто не выполнит, — лид в ветке не создаётся.
EXISTING_CLIENT_INSTRUCTION = (
    "Клиент спрашивает о заявке, которую уже оставлял. Отсюда заявок не видно: "
    "ни статуса, ни даты, ни менеджера. Скажи это честно, одной-двумя "
    "короткими фразами.\n"
    "НЕ обещай посмотреть, проверить, уточнить, передать, связать с "
    "менеджером или что кто-то перезвонит — чат ничего из этого не делает. Не "
    "предлагай оставить номер для связи: отсюда он никуда не уйдёт. Это "
    "правило сильнее общего правила про заявки.\n"
    "Не спрашивай имя, телефон и данные анкеты, не начинай подбор заново. "
    "Можно сказать, что если появился новый вопрос о деньгах или долгах, с ним "
    "можно помочь прямо здесь."
)

# «Не наш профиль»: 44% трафика чата приходит со статей, не давших ни одного
# лида, — карты, чужие банки, налоги. Первый вопрос анкеты для этих людей —
# момент, когда становится понятно, что они попали не туда. Общее правило 6
# («возвращай разговор к делу») здесь не действует.
OFF_TOPIC_INSTRUCTION = (
    "Клиент пришёл с вопросом не про кредиты, займы и долги. Ответь честно и "
    "коротко: это не наш профиль, мы помогаем с кредитами и долгами. Если "
    "вопрос к другому банку или организации — скажи, что с этим к ним самим. "
    "Короткая общая подсказка допустима, но без выдуманных телефонов, адресов "
    "и условий.\n"
    "Не спрашивай имя, телефон и данные анкеты, не начинай подбор и не "
    "уговаривай. В конце можно добавить, что если понадобится помощь с "
    "деньгами или долгами — можно писать сюда."
)
```

- [ ] **Step 4: Инструкция хода** — в `core.py`, в `turn_instruction`, сразу после

```python
    if current_plan is None:
        return prompts.OPENING_INSTRUCTION
```

добавить

```python

    # Ветки развилки говорят своё и анкету не ведут. Проверка выше всех
    # анкетных веток: подтверждение города, отказ от телефона, «нет решения»
    # — всё это про анкету, которой в ветке нет. Прощание остаётся прощанием.
    scenario = entry_routing.current_scenario(state)
    if scenario != entry_routing.QUESTIONNAIRE:
        if client_is_saying_goodbye(state.get("_last_user_message") or ""):
            return prompts.FAREWELL_INSTRUCTION
        if scenario == entry_routing.EXISTING:
            return prompts.EXISTING_CLIENT_INSTRUCTION
        return prompts.OFF_TOPIC_INSTRUCTION
```

- [ ] **Step 5: Фиксация хода** — в `core.py`, в `commit_turn`, заменить

```python
    new_state = deepcopy(state)
    current_plan = new_state.pop("_plan_object", None)
    bubbles = _guard_terminal_bubbles(bubbles, current_plan, new_state)
```

на

```python
    new_state = deepcopy(state)
    current_plan = new_state.pop("_plan_object", None)
    entry_transition = new_state.pop(entry_routing.TRANSITION_KEY, None)
    # В ветках развилки анкета не звучит: ни предохранитель финала, ни
    # переходные фразы, ни просьба о контактах, ни счётчик попыток.
    questionnaire = entry_routing.questionnaire_active(new_state)
    if questionnaire:
        bubbles = _guard_terminal_bubbles(bubbles, current_plan, new_state)
```

строку `if _question_is_voiced(user_message):` заменить на `if questionnaire and _question_is_voiced(user_message):`, а строку `_record_turn_analytics(new_state, current_plan)` — на

```python
    # Шаг анкеты пишется только там, где анкета идёт: иначе ветки «обрывались
    # бы на сумме» в воронке заказчика. Ветку отмечает `entry_routed`.
    if questionnaire:
        _record_turn_analytics(new_state, current_plan)
    entry_routing.record_transition(new_state, entry_transition)
```

- [ ] **Step 6: Проверить**

Run: `.venv313/Scripts/python -m pytest tests/test_entry_scenarios.py tests/test_analytics_hooks.py tests/test_contact_request_flow.py tests/test_farewell_midway.py tests/test_instruction.py -q`
Expected: all passed.
Run: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`
Expected: ruff чисто; все тесты зелёные.

- [ ] **Step 7: Коммит**

```bash
git add prompts.py core.py tests/test_entry_scenarios.py
git commit -m "Ветки развилки отвечают по своей инструкции и не ведут анкету"
```

---

### Task 10: Запасные фразы фильтра в ветках не зовут в анкету

Фильтр `utils.guard_bubbles` вырезает реплику с подстрокой «статус вашей заявки» — это и ложное обещание, и честное «статус вашей заявки отсюда не виден»: отличить их фильтр не может (комментарий у `_PROMISE_FALLBACK`). Когда вырезано всё, он подставляет «Здесь могу продолжить подбор и передать ваши данные дальше» — а в ветке `existing` лид не создаётся. В ветке `off_topic` ссылка на сайт чужого банка (`www.…`) вырезается и превращается в «Гарантировать одобрение или точную ставку заранее не могу». Инструкция ветки прямо подталкивает модель к фразе «статус не виден», так что это частый случай, а не редкий.

**Files:**
- Modify: `utils.py` — константа `_BRANCH_FALLBACK` после `_PROMISE_FALLBACK_AFTER_FINISH`, параметр `scenario` у `guard_bubbles`
- Modify: `core.py` — вызов `guard_bubbles` в начале `commit_turn`
- Test: `tests/test_branch_fallback.py`

**Interfaces:**
- Consumes: `entry_routing.current_scenario`, `EXISTING`, `OFF_TOPIC`, `QUESTIONNAIRE` (Task 6).
- Produces: `guard_bubbles(bubbles, *, conversation_over: bool = False, scenario: str | None = None) -> list[str]`; `utils._BRANCH_FALLBACK: dict[str, tuple[str, str]]` с ключами `"existing"` и `"off_topic"`. Для сценария вне словаря поведение прежнее.

- [ ] **Step 1: Написать падающие тесты** — `tests/test_branch_fallback.py`:

```python
"""Запасные фразы фильтра в ветках развилки не зовут в анкету и не обещают."""
from __future__ import annotations

import pytest

import core
import entry_routing as er
from state import init_state
from utils import _BRANCH_FALLBACK, _PROMISE_FALLBACK, guard_bubbles


def test_branch_fallbacks_cover_exactly_the_branches():
    """Ключи — строки: utils ниже по слоям, чем entry_routing. Сверяем здесь."""
    assert set(_BRANCH_FALLBACK) == {er.EXISTING, er.OFF_TOPIC}


def test_honest_answer_cut_in_the_existing_branch_stays_honest():
    """«Статус вашей заявки отсюда не виден» — честный ответ, но подстрока та
    же, что у ложного обещания. Прежняя запасная фраза предлагала «продолжить
    подбор и передать данные дальше» человеку, чей лид в ветке не создаётся."""
    result = guard_bubbles(
        ["Статус вашей заявки я отсюда не вижу."], scenario=er.EXISTING
    )

    assert result == list(_BRANCH_FALLBACK[er.EXISTING])
    assert "подбор" not in " ".join(result)


def test_link_cut_in_off_topic_does_not_become_a_guarantee_disclaimer():
    result = guard_bubbles(
        ["Зайдите на www.sberbank.ru и напишите в поддержку."], scenario=er.OFF_TOPIC
    )

    assert result == list(_BRANCH_FALLBACK[er.OFF_TOPIC])


@pytest.mark.parametrize("scenario", [er.EXISTING, er.OFF_TOPIC])
def test_branch_fallback_passes_its_own_filters(scenario):
    fallback = list(_BRANCH_FALLBACK[scenario])

    assert guard_bubbles(fallback, scenario=scenario) == fallback


def test_questionnaire_keeps_its_fallbacks():
    assert guard_bubbles(["Посмотрю вашу заявку."], scenario=er.QUESTIONNAIRE) == list(
        _PROMISE_FALLBACK
    )
    assert guard_bubbles(["Посмотрю вашу заявку."]) == list(_PROMISE_FALLBACK)


def test_commit_turn_answers_with_the_branch_fallback():
    state = init_state()
    state["entry"] = {"scenario": er.EXISTING, "intent": "existing", "source": "model"}

    committed = core.commit_turn(
        state, "Что с моей заявкой?", "Статус вашей заявки я отсюда не вижу."
    )

    assert committed["messages"] == list(_BRANCH_FALLBACK[er.EXISTING])
```

- [ ] **Step 2: Убедиться, что падают**

Run: `.venv313/Scripts/python -m pytest tests/test_branch_fallback.py -q`
Expected: ERROR при сборе — `ImportError: cannot import name '_BRANCH_FALLBACK' from 'utils'`.

- [ ] **Step 3: Фразы и параметр** — в `utils.py` сразу после `_PROMISE_FALLBACK_AFTER_FINISH = (...)`:

```python

# Запасные фразы веток развилки на входе (`entry_routing.EXISTING`,
# `entry_routing.OFF_TOPIC`). Анкеты там нет, и общие фразы — «продолжить
# подбор», «ещё немного данных», «гарантировать одобрение не могу» — зовут
# туда, куда человек не шёл, а в ветке «уже оставлял заявку» ещё и обещают
# передачу, которой не будет: лид там не создаётся. Ключи — строки, а не
# импорт: utils ниже по слоям; тест сверяет их с константами.
_BRANCH_FALLBACK = {
    "existing": (
        _PROMISE_FALLBACK[0],
        "Если появится новый вопрос о деньгах или долгах — помогу прямо здесь.",
    ),
    "off_topic": (
        "С этим вопросом помочь не смогу — мы занимаемся кредитами и долгами.",
        "Если понадобится помощь с деньгами — пишите сюда.",
    ),
}
```

Сигнатуру `guard_bubbles` заменить на

```python
def guard_bubbles(
    bubbles: List[str],
    *,
    conversation_over: bool = False,
    scenario: str | None = None,
) -> List[str]:
```

и сразу после

```python
    if len(safe) == len(bubbles):
        return bubbles
    if safe:
        return safe
```

добавить

```python
    # В ветке развилки вырезано всё — отвечаем фразой ветки, какой бы фильтр
    # ни сработал: общие запасные фразы написаны для анкеты.
    if scenario in _BRANCH_FALLBACK:
        return list(_BRANCH_FALLBACK[scenario])
```

- [ ] **Step 4: Вызов в commit_turn** — в `core.py`, в начале `commit_turn`, заменить

```python
    bubbles = guard_bubbles(
        prompts.split_bubbles(full_reply),
        conversation_over=state.get("dialog_phase") in _TERMINAL_PHASES,
    ) or [full_reply.strip() or "…"]
```

на

```python
    bubbles = guard_bubbles(
        prompts.split_bubbles(full_reply),
        conversation_over=state.get("dialog_phase") in _TERMINAL_PHASES,
        scenario=entry_routing.current_scenario(state),
    ) or [full_reply.strip() or "…"]
```

- [ ] **Step 5: Проверить**

Run: `.venv313/Scripts/python -m pytest tests/test_branch_fallback.py tests/test_false_promises_guard.py tests/test_closing_fallback.py tests/test_internal_terms_guard.py -q`
Expected: all passed.
Run: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`
Expected: ruff чисто; все тесты зелёные.

- [ ] **Step 6: Коммит**

```bash
git add utils.py core.py tests/test_branch_fallback.py
git commit -m "Запасные фразы фильтра в ветках развилки не зовут в анкету"
```

---

### Task 11: В ветке «уже оставлял заявку» лид не создаётся

**Files:**
- Modify: `crm_dialog_workflow.py` — импорт; начало `CrmDialogWorkflow.advance`
- Modify: `lead_intake_coordinator.py` — импорт; `LeadIntakeCoordinator.partial_is_due`
- Modify: `core.py` — условие локальной доставки в `prepare_turn`
- Test: `tests/test_crm_dialog_workflow.py`, `tests/test_lead_intake_coordinator.py`, `tests/test_entry_routing_turn.py` (дописать)

**Interfaces:**
- Consumes: `entry_routing.lead_blocked(state) -> bool` (Task 6).
- Produces: при `state["entry"]["scenario"] == "existing"` ни одна дорога к лиду не срабатывает: `advance` возвращает текущее состояние автомата без обращений в CRM, `partial_is_due` возвращает `False`, `delivery.submit` не вызывается.

- [ ] **Step 1: Написать падающие тесты**

В конец `tests/test_crm_dialog_workflow.py`:

```python
def test_existing_client_branch_never_reaches_crm():
    """Спека, компонент 2: в ветке «уже оставлял заявку» второй лид не
    создаётся ни при каком развитии разговора — даже с телефоном и
    найденным маршрутом. В CRM не уходит даже квалификация."""
    persisted: list[str] = []
    client = FakeCrmClient(need_site_consent=False)
    session = _session()
    session.state["facts"]["client"]["display_name"] = "Тест"
    session.state["entry"] = {
        "scenario": "existing",
        "intent": "existing",
        "source": "model",
    }

    _workflow(client, persisted).advance(session, "")

    assert client.qualification_requests == []
    assert client.lead_requests == []
    assert (session.state.get("crm_lead") or {}).get("lead_id") is None


def test_return_to_the_questionnaire_lifts_the_lead_block():
    persisted: list[str] = []
    client = FakeCrmClient(need_site_consent=False)
    session = _session()
    session.state["facts"]["client"]["display_name"] = "Тест"
    session.state["entry"] = {
        "scenario": "questionnaire",
        "intent": "money",
        "source": "model",
    }

    result = _workflow(client, persisted).advance(session, "")

    assert result.status == CrmDialogStatus.SUBMITTED
    assert len(client.lead_requests) == 1
```

В конец `tests/test_lead_intake_coordinator.py`:

```python
def test_existing_client_branch_is_never_swept_into_a_partial_lead():
    """Телефон, названный в ветке «уже оставлял заявку», не делает сессию
    брошенной анкетой: через 15 минут тишины ушёл бы второй лид."""
    persisted: list[str] = []
    client = FakeLeadClient()
    coordinator = _coordinator(client, persisted)
    session = _session()
    session.state["entry"] = {
        "scenario": "existing",
        "intent": "existing",
        "source": "model",
    }

    assert coordinator.partial_is_due(session, now=NOW) is False
    assert coordinator.sweep_abandoned([session], now=NOW) == []
    assert client.requests == []
```

В конец `tests/test_entry_routing_turn.py`:

```python
def _route_found_everywhere(monkeypatch) -> list:
    """План всегда «маршрут найден», доставка записывает вызовы вместо сети."""
    from types import SimpleNamespace

    from routing import Plan, Route, RouteStatus

    route = Route(
        id="realty_msk",
        priority="1-1",
        product="Залог недвижимости",
        segment="Мск/МО",
        family="realty",
    )
    matched = RouteStatus(route=route, state="PASS")
    found = Plan(action="route_found", statuses=[matched], matched=matched)
    monkeypatch.setattr(core, "build_plan", lambda *a, **k: found)
    calls: list = []

    def fake_submit(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(as_dict=lambda: {"status": "completed"})

    monkeypatch.setattr(core.delivery, "submit", fake_submit)
    return calls


def test_local_delivery_is_not_submitted_in_the_existing_branch(monkeypatch):
    calls = _route_found_everywhere(monkeypatch)
    _model_says(monkeypatch, EntryIntent.UNCLEAR)

    core.prepare_turn(_in_branch(er.EXISTING), "ок", _config(), submit_delivery=True)

    assert calls == []


def test_local_delivery_still_works_in_the_questionnaire(monkeypatch):
    calls = _route_found_everywhere(monkeypatch)
    _model_says(monkeypatch, EntryIntent.UNCLEAR)

    core.prepare_turn(
        _in_branch(er.QUESTIONNAIRE), "ок", _config(), submit_delivery=True
    )

    assert len(calls) == 1
```

- [ ] **Step 2: Убедиться, что падают**

Run: `.venv313/Scripts/python -m pytest tests/test_crm_dialog_workflow.py tests/test_lead_intake_coordinator.py tests/test_entry_routing_turn.py -q`
Expected: три новых теста запрета падают (лид уходит, `partial_is_due` возвращает `True`, `delivery.submit` вызван); два контрольных — проходят.

- [ ] **Step 3: CRM workflow** — в `crm_dialog_workflow.py` рядом с `from delivery.offer_link import build_offer_link` добавить `from entry_routing import lead_blocked`. В `advance` заменить

```python
        if not self.enabled:
            return _read_workflow_state(session)

        self.check_second_realty(session)
```

на

```python
        if not self.enabled:
            return _read_workflow_state(session)

        # Ветка «уже оставлял заявку»: второй лид не создаётся ни при каком
        # развитии разговора (спека, компонент 2). Проверка стоит до всего,
        # что ходит в CRM, включая вызов по второму объекту.
        with session._lock:
            blocked = lead_blocked(session.state)
        if blocked:
            return _read_workflow_state(session)

        self.check_second_realty(session)
```

- [ ] **Step 4: Брошенная анкета** — в `lead_intake_coordinator.py` после `import analytics` добавить `from entry_routing import lead_blocked`. В `partial_is_due` после

```python
            if state.status != LeadSubmissionStatus.NOT_SENT:
                return False
```

добавить

```python
            # Человек из ветки «уже оставлял заявку» мог назвать телефон сам.
            # Через 15 минут тишины такая сессия ушла бы вторым лидом — ровно
            # тем дублем, который ветка и убирает.
            if lead_blocked(session.state):
                return False
```

- [ ] **Step 5: Локальная доставка** — в `core.py`, в `prepare_turn`, условие

```python
    if (
        submit_delivery
        and current_plan.action == "route_found"
        and not new_state.get("delivery")
    ):
```

заменить на

```python
    if (
        submit_delivery
        and current_plan.action == "route_found"
        and not new_state.get("delivery")
        and not entry_routing.lead_blocked(new_state)
    ):
```

- [ ] **Step 6: Проверить**

Run: `.venv313/Scripts/python -m pytest tests/test_crm_dialog_workflow.py tests/test_lead_intake_coordinator.py tests/test_entry_routing_turn.py tests/test_lead_intake_invariant.py tests/test_bluegreen_lead_submission.py -q`
Expected: all passed.
Run: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`
Expected: ruff чисто; все тесты зелёные.

- [ ] **Step 7: Коммит**

```bash
git add crm_dialog_workflow.py lead_intake_coordinator.py core.py tests/test_crm_dialog_workflow.py tests/test_lead_intake_coordinator.py tests/test_entry_routing_turn.py
git commit -m "В ветке «уже оставлял заявку» лид не создаётся ни одной дорогой"
```

---

### Task 12: Живой прогон корпуса

**Files:**
- Modify: `evaluation/entry_intent.py` — оценка
- Create: `run_entry_intent_eval.py` (корень, рядом с `run_fact_interpretation_eval.py`)
- Test: `tests/test_entry_intent_eval.py`

**Interfaces:**
- Consumes: `EntryCase`, `load_entry_corpus`, `DEFAULT_CORPUS_PATH` (Task 3); `extract_fact_candidates` с флагом (Task 5); `classify_by_dictionary` (Task 2); `core.build_runtime_payload`.
- Produces:
  - `LEAVES_QUESTIONNAIRE: frozenset[EntryIntent]` = `{EXISTING, OFF_TOPIC}`
  - `@dataclass(frozen=True) class EntryEvalSummary: total: int; correct: int; confusion: dict[str, dict[str, int]]; lost_money: tuple[str, ...]; lost_money_dialogs: int; diverted_unclear: tuple[str, ...]` и свойство `accuracy: float`
  - `score_entry_predictions(cases: list[EntryCase], predictions: dict[str, EntryIntent]) -> EntryEvalSummary` — отсутствующее предсказание читается как `UNCLEAR`.
  - `run_entry_intent_eval.main(argv: list[str] | None = None) -> int` — без `--live` модель не вызывает и возвращает 0; с `--live` возвращает 1, если `lost_money` не пуст.

- [ ] **Step 1: Написать падающие тесты** — `tests/test_entry_intent_eval.py`:

```python
"""Оценка развилки по корпусу: считается без сети, модель зовётся только с --live."""
from __future__ import annotations

import run_entry_intent_eval
from evaluation.entry_intent import EntryCase, score_entry_predictions
from routing.entry_intent import EntryIntent as I


CASES = [
    EntryCase("m1", "нужны деньги", I.MONEY, count=3),
    EntryCase("m2", "кредит", I.MONEY),
    EntryCase("e1", "оставлял заявку", I.EXISTING),
    EntryCase("u1", "здравствуйте", I.UNCLEAR, count=5),
    EntryCase("o1", "карта сбера", I.OFF_TOPIC),
]


def test_money_sent_to_a_branch_is_the_hard_failure():
    summary = score_entry_predictions(
        CASES,
        {"m1": I.OFF_TOPIC, "m2": I.UNCLEAR, "e1": I.EXISTING, "u1": I.UNCLEAR, "o1": I.OFF_TOPIC},
    )

    # money → unclear не потеря: оба ведут в анкету
    assert summary.lost_money == ("m1",)
    assert summary.lost_money_dialogs == 3
    assert summary.diverted_unclear == ()
    assert summary.correct == 3
    assert summary.total == 5
    assert summary.confusion["money"]["off_topic"] == 1


def test_unclear_led_away_is_reported_separately():
    summary = score_entry_predictions(CASES, {"u1": I.EXISTING})

    assert summary.diverted_unclear == ("u1",)


def test_missing_prediction_reads_as_unclear():
    summary = score_entry_predictions(CASES, {})

    assert summary.lost_money == ()
    assert summary.confusion["money"]["unclear"] == 2
    assert summary.correct == 1


def test_without_live_flag_the_model_is_never_called(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("без --live модель не вызывается")

    monkeypatch.setattr(run_entry_intent_eval, "extract_fact_candidates", forbidden)

    assert run_entry_intent_eval.main([]) == 0
    assert "корпус" in capsys.readouterr().out
```

- [ ] **Step 2: Убедиться, что падают**

Run: `.venv313/Scripts/python -m pytest tests/test_entry_intent_eval.py -q`
Expected: ERROR при сборе — `No module named 'run_entry_intent_eval'` / `cannot import name 'score_entry_predictions'`.

- [ ] **Step 3: Оценка** — дописать в конец `evaluation/entry_intent.py`:

```python


# Ветки, в которых анкета не начинается. Потеря денег — это money, ушедший
# сюда; money → unclear потерей не считается: оба ведут в анкету.
LEAVES_QUESTIONNAIRE = frozenset({EntryIntent.EXISTING, EntryIntent.OFF_TOPIC})


@dataclass(frozen=True)
class EntryEvalSummary:
    """Итог прогона корпуса."""

    total: int
    correct: int
    # ожидаемое → предсказанное → число реплик
    confusion: dict[str, dict[str, int]]
    # Жёсткое требование спеки: должно быть пусто.
    lost_money: tuple[str, ...]
    # Сколько живых диалогов стоит за потерянными репликами (поле count).
    lost_money_dialogs: int
    # «Здравствуйте», уведённое в ветку: требованием не считается, но
    # выглядит плохо — печатается для разбора.
    diverted_unclear: tuple[str, ...]

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0


def score_entry_predictions(
    cases: list[EntryCase],
    predictions: dict[str, EntryIntent],
) -> EntryEvalSummary:
    """Сравнивает предсказания с разметкой. Нет предсказания — `UNCLEAR`."""
    confusion = {
        expected.value: {predicted.value: 0 for predicted in EntryIntent}
        for expected in EntryIntent
    }
    correct = 0
    lost: list[str] = []
    lost_dialogs = 0
    diverted: list[str] = []
    for case in cases:
        predicted = predictions.get(case.id, EntryIntent.UNCLEAR)
        confusion[case.expected.value][predicted.value] += 1
        if predicted is case.expected:
            correct += 1
        if predicted in LEAVES_QUESTIONNAIRE:
            if case.expected is EntryIntent.MONEY:
                lost.append(case.id)
                lost_dialogs += case.count
            elif case.expected is EntryIntent.UNCLEAR:
                diverted.append(case.id)
    return EntryEvalSummary(
        total=len(cases),
        correct=correct,
        confusion=confusion,
        lost_money=tuple(lost),
        lost_money_dialogs=lost_dialogs,
        diverted_unclear=tuple(diverted),
    )
```

- [ ] **Step 4: Прогон** — `run_entry_intent_eval.py`:

```python
"""Живая проверка развилки на входе по корпусу первых реплик.

Жёсткое требование спеки одно: ни одна реплика с ожидаемым «money» не уходит
в ветку «уже оставлял заявку» или «не наш профиль». Остальные ошибки
допустимы — их лечит возврат в анкету, — но печатаются для разбора.

Реплика подаётся так же, как в бою на первом ходе: история из одного
приветствия, флаг развилки включён, семантический извлекатель. Сбой модели
на реплике означает работу словаря — ровно как в `core.prepare_turn`.

Без --live только проверяет корпус и ничего не вызывает.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import core
from config import load_config
from evaluation.entry_intent import (
    DEFAULT_CORPUS_PATH,
    EntryCase,
    load_entry_corpus,
    score_entry_predictions,
)
from llm_agent import extract_fact_candidates
from prompts import OPENING_MESSAGE
from routing import get_matrix
from routing.entry_intent import EntryIntent, classify_by_dictionary
from state import init_state


ROOT = Path(__file__).resolve().parent
DEFAULT_LOG_ROOT = ROOT / "logs" / "entry_intent_evals"
MAX_WORKERS = 8


def _first_message_payload(message: str) -> dict[str, Any]:
    state = init_state()
    state["chat_history"] = [{"role": "assistant", "content": OPENING_MESSAGE}]
    return core.build_runtime_payload(state, message)


def _classify(case: EntryCase, config, matrix) -> dict[str, Any]:
    try:
        extraction = extract_fact_candidates(
            _first_message_payload(case.message), config, matrix=matrix
        )
        model_intent, error = extraction.entry_intent, None
    except Exception as exc:  # noqa: BLE001 — сбой модели = работа словаря
        model_intent, error = None, f"{type(exc).__name__}: {exc}"[:300]
    effective = model_intent or classify_by_dictionary(case.message)
    return {
        "id": case.id,
        "message": case.message,
        "expected": case.expected.value,
        "model": model_intent.value if model_intent else None,
        "effective": effective.value,
        "source": "model" if model_intent else "dictionary",
        "error": error,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Живая проверка развилки на входе")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument(
        "--live",
        action="store_true",
        help="разрешить реальные вызовы модели; без флага только проверка корпуса",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    cases = load_entry_corpus(args.corpus)
    print(f"корпус: {len(cases)} реплик, {sum(c.count for c in cases)} диалогов")
    if not args.live:
        print("без --live модель не вызывается")
        return 0

    config = replace(
        load_config(),
        fact_interpretation_mode="semantic",
        entry_routing_enabled=True,
    )
    matrix = get_matrix()
    workers = max(1, min(args.workers, MAX_WORKERS))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(lambda case: _classify(case, config, matrix), cases))

    predictions = {row["id"]: EntryIntent(row["effective"]) for row in rows}
    summary = score_entry_predictions(cases, predictions)
    by_id = {row["id"]: row for row in rows}

    print(f"точность: {summary.accuracy:.1%} ({summary.correct}/{summary.total})")
    print(f"ответов модели без намерения: {sum(r['source'] == 'dictionary' for r in rows)}")
    print("ожидалось → получено:")
    for expected, row in summary.confusion.items():
        print(f"  {expected:>9}: {row}")
    print(f"money увели из анкеты: {len(summary.lost_money)} реплик, "
          f"{summary.lost_money_dialogs} диалогов")
    for case_id in summary.lost_money:
        print(f"  ! {case_id} → {by_id[case_id]['effective']}: {by_id[case_id]['message']}")
    print(f"приветствие увели из анкеты: {len(summary.diverted_unclear)}")
    for case_id in summary.diverted_unclear:
        print(f"  ? {case_id} → {by_id[case_id]['effective']}: {by_id[case_id]['message']}")

    out = args.out or DEFAULT_LOG_ROOT / f"entry_intent_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "summary": {
                    "total": summary.total,
                    "correct": summary.correct,
                    "accuracy": summary.accuracy,
                    "confusion": summary.confusion,
                    "lost_money": list(summary.lost_money),
                    "lost_money_dialogs": summary.lost_money_dialogs,
                    "diverted_unclear": list(summary.diverted_unclear),
                },
                "cases": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"отчёт: {out}")
    return 1 if summary.lost_money else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Проверить**

Run: `.venv313/Scripts/python -m pytest tests/test_entry_intent_eval.py -q`
Expected: all passed.
Run: `.venv313/Scripts/python run_entry_intent_eval.py`
Expected: `корпус: N реплик, M диалогов` и `без --live модель не вызывается`, код 0.
Run: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`
Expected: ruff чисто; все тесты зелёные.

- [ ] **Step 6: Коммит**

```bash
git add evaluation/entry_intent.py run_entry_intent_eval.py tests/test_entry_intent_eval.py
git commit -m "Живой прогон развилки по корпусу первых реплик"
```

---

### Task 13: Роли для приёмки веток

**Files:**
- Modify: `evaluation/personas.py` — три роли в конец `PERSONAS`
- Test: `tests/test_personas.py` (создать)

**Interfaces:**
- Consumes: `Persona`, `PERSONAS`, `persona_by_id` (существуют).
- Produces: роли `existing_client_status`, `off_topic_other_bank`, `off_topic_then_money`.

- [ ] **Step 1: Написать падающие тесты** — `tests/test_personas.py`:

```python
"""Роли живого прогона: уникальны, и у веток развилки свои правила успеха."""
from __future__ import annotations

from evaluation.personas import PERSONAS, persona_by_id


def test_persona_ids_are_unique():
    ids = [persona.id for persona in PERSONAS]

    assert len(ids) == len(set(ids))


def test_branch_personas_do_not_expect_a_questionnaire():
    """Ветки анкету не ведут: «не дошла до конца» для них — правильное поведение."""
    for persona_id in ("existing_client_status", "off_topic_other_bank"):
        persona = persona_by_id(persona_id)
        assert persona.expects_completion is False
        assert any("обращаться" in phrase for phrase in persona.forbidden_any)


def test_return_to_the_questionnaire_is_checked_to_the_end():
    persona = persona_by_id("off_topic_then_money")

    assert persona.expects_completion is True
    assert persona.facts["request"]["amount"] == 150_000
```

- [ ] **Step 2: Убедиться, что падают**

Run: `.venv313/Scripts/python -m pytest tests/test_personas.py -q`
Expected: FAIL — `KeyError: 'неизвестная роль: existing_client_status'`.

- [ ] **Step 3: Добавить роли** — в `evaluation/personas.py` перед закрывающей `]` списка `PERSONAS` (после роли `second_realty`):

```python
    # Развилка на входе (спека 09.09.2026). 22% живого потока начинают с
    # вопроса о своей заявке. Раньше они проходили анкету заново, и шестеро
    # создали второй лид. Бот заявок не видит и обещать ничего не вправе.
    Persona(
        id="existing_client_status",
        description="Уже оставлял заявку и спрашивает, когда позвонят",
        story=(
            "Позавчера ты оставил на сайте МБК заявку на кредит 300 тысяч, и "
            "тебе до сих пор никто не позвонил. Ты пишешь в чат узнать, что с "
            "заявкой и когда ждать звонка. Новую анкету заполнять не хочешь — "
            "ты уже всё указывал. Если специалист честно скажет, что отсюда "
            "заявку не видно, поблагодари и попрощайся."
        ),
        manner="Немного раздражён ожиданием, пишешь коротко.",
        forbidden_any=[
            "как к вам обращаться",
            "какая сумма",
            "посмотрю по заявке",
            "проверю статус",
            "передам менеджеру",
            "свяжу вас",
            "вам перезвонят",
            "с вами свяжутся",
        ],
        max_turns=5,
        expects_completion=False,
    ),
    # 44% трафика чата приходит со статей о чужих банках и картах. Честный
    # ответ и выход — анкета для них момент, когда понятно, что попали не туда.
    Persona(
        id="off_topic_other_bank",
        description="Хочет разблокировать карту другого банка",
        story=(
            "Ты прочитал на сайте статью о том, как дозвониться до поддержки "
            "банка, и открыл чат. Тебе нужно разблокировать карту Сбербанка, "
            "заблокированную после перевода. Кредиты и займы тебе не нужны. "
            "Если специалист скажет, что это не к ним, поблагодари и уйди."
        ),
        manner="Вежлив, торопишься, пишешь с опечатками.",
        forbidden_any=[
            "как к вам обращаться",
            "какая сумма",
            "оставьте номер",
            "какой номер телефона",
        ],
        max_turns=4,
        expects_completion=False,
    ),
    # Возврат в анкету открыт всегда: человек, начавший не по теме и потом
    # попросивший денег, проходит обычную анкету.
    Persona(
        id="off_topic_then_money",
        facts={
            "client": {"age": 35, "phone": "+79000000012"},
            "request": {"amount": 150_000},
        },
        description="Начал не по теме, потом попросил денег",
        story=(
            "Андрей, 35 лет, Казань. Сначала спрашиваешь, как узнать свою "
            "задолженность по налогам. Когда специалист ответит, что это не "
            "их профиль, вспоминаешь, что вообще-то нужно 150 тысяч на ремонт "
            "квартиры, и спрашиваешь, могут ли тут помочь. Дальше отвечаешь "
            "на вопросы. Работаешь официально, 70 тысяч. Кредитов нет. "
            "Квартира съёмная, своей недвижимости нет. Машины нет. Телефон "
            "+7 900 000 00 12."
        ),
        manner="Спокойный, отвечаешь по делу.",
    ),
```

- [ ] **Step 4: Проверить**

Run: `.venv313/Scripts/python -m pytest tests/test_personas.py -q`
Expected: all passed.
Run: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`
Expected: ruff чисто; все тесты зелёные.

- [ ] **Step 5: Коммит**

```bash
git add evaluation/personas.py tests/test_personas.py
git commit -m "Роли живого прогона для веток развилки"
```

---

### Task 14: Приёмка (исполняет контроллер)

**Исполняет контроллер:** нужны живые вызовы модели и локальный сервер. Модель — за деньги; каждый шаг запускать один раз, повтор — только после правки.

**Files:**
- Modify: `docs/agent-memory/SESSION.md`
- Modify: `docs/superpowers/specs/2026-09-09-dialog-entry-and-order-design.md` — строка статуса

- [ ] **Step 1: Полные проверки**

Run: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`
Expected: ruff чисто; все тесты зелёные. Записать число тестов.

- [ ] **Step 2: Живой прогон корпуса**

Run: `.venv313/Scripts/python run_entry_intent_eval.py --live`
Expected: код 0, `money увели из анкеты: 0 реплик`.

Если потеря не ноль — разобрать каждую реплику. Правка — только в `_ENTRY_INTENT_SECTION` (`interpretation/prompt.py`) и только смыслом, без перечня слов из корпуса (решение пользователя 09.09.2026: «лучше семантически»). После правки — повторить Step 1 и Step 2. Если реплика размечена неверно — поправить разметку в корпусе отдельным коммитом с `note`, объясняющим причину.

`приветствие увели из анкеты` — не требование, но каждый случай посмотреть: если модель уводит голое «здравствуйте» в ветку, это правится в промпте так же.

- [ ] **Step 3: Извлечение фактов не просело**

Run (флаг выключен): `MBK_ENTRY_ROUTING=0 .venv313/Scripts/python run_fact_interpretation_eval.py --live`
Run (флаг включён): `MBK_ENTRY_ROUTING=1 .venv313/Scripts/python run_fact_interpretation_eval.py --live`
Expected: при включённом флаге число пройденных случаев не меньше, чем при выключенном. Просело — вернуться к `_ENTRY_INTENT_SECTION`: блок намерения мешает фактам.

- [ ] **Step 4: Ролевой прогон**

Поднять сервер с флагом: `MBK_ENTRY_ROUTING=1 .venv313/Scripts/python -m uvicorn main:app --port 8000` (фоном), затем `.venv313/Scripts/python run_roleplay_eval.py`.
Expected:
- девять прежних ролей дошли и прошли, как в прогоне 10.09.2026 (`logs/roleplay/roleplay_20260910_202842.json`): человечность ≥ 90, связность ≥ 90, без нарушений;
- `existing_client_status` и `off_topic_other_bank` — без нарушений, `crm_status` пустой, имя не спрашивалось;
- `off_topic_then_money` дошла до конца анкеты и собрала сумму, возраст и телефон.

- [ ] **Step 5: Итоги в память и спеку**

`docs/agent-memory/SESSION.md`: в «Сделано» — блок развилки за флагом `MBK_ENTRY_ROUTING` (коммиты, число тестов, итоги Step 2-4 цифрами и путями к отчётам); из «Не трогать» убрать пункт про `_safe_contact_reaction` — починен в Task 0; в «Следующий шаг» — «Человеку: решить про push в dev и про включение флага в бою»; открытый вопрос про голые приветствия (31 из 226 первых реплик).

Спека: строку `- Статус: дизайн на согласовании` заменить на `- Статус: компоненты 1-2 реализованы за флагом MBK_ENTRY_ROUTING (план docs/superpowers/plans/2026-09-10-entry-intent.md)`.

```bash
git add docs/agent-memory/SESSION.md docs/superpowers/specs/2026-09-09-dialog-entry-and-order-design.md
git commit -m "docs(memory): развилка на входе за флагом — итоги приёмки"
```

Push не делать — отправку решает человек.
