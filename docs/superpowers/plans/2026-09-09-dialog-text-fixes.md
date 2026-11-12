# Правки текста диалога: обещания, прощание, повтор вопроса, имя

> **Для агентов-исполнителей:** ОБЯЗАТЕЛЬНЫЙ САБ-НАВЫК: используйте
> superpowers:subagent-driven-development (рекомендуется) или
> superpowers:executing-plans для выполнения плана задача за задачей. Шаги
> размечены чекбоксами (`- [ ]`).

**Цель:** убрать четыре дефекта, видимые в боевой переписке, — ложные обещания
про заявку и менеджера, глухоту к прощанию, дословный повтор вопроса об имени и
роль имени как шлагбаума.

**Архитектура:** ничего нового не строится. Правятся три места существующего
кода: словарь запрещённых оборотов в исходящем гарде (`utils.guard_bubbles`),
ветка контактов в `core._merge_contact_request` и стартовая ветка планировщика
`routing.planner.plan`. Плюс один новый детерминированный распознаватель
прощания в `core`. Решения по-прежнему принимает код, модель только формулирует.

**Технологии:** Python 3.13, pydantic v2, pytest. Виртуальное окружение —
`.venv313`. Конфигов pytest и ruff в проекте нет, обе команды запускаются с
дефолтами.

**Спека:** `docs/superpowers/specs/2026-09-09-dialog-entry-and-order-design.md`

## Общие ограничения

- **Проверки перед завершением каждой задачи:** `.venv313/Scripts/python -m
  ruff check .` и `.venv313/Scripts/python -m pytest` — обе обязаны быть
  зелёными. На момент начала плана: 1395 passed, 1 skipped.
- **Решение принимает код, а не модель.** Формулировки, относящиеся к исходу
  (что бот сделает, что произойдёт с заявкой), задаются детерминированно в
  backend. Модель формулирует только вопрос и участие.
- **Никаких обещаний того, чего бот не делает.** Он не видит заявок в CRM и не
  связывает с менеджером.
- **Комментарии и сообщения коммитов — по-русски**, в стиле окружающего кода:
  объясняют «почему», а не «что». Упоминаний Claude в коммитах нет.
- **Коммиты в ветку `dev`.** Точка отката — тег `baseline-1.0.7-mbk15`.
- **Файлы репозитория в кодировке UTF-8 без BOM**, перевод строки — CRLF в
  рабочей копии (git нормализует).

## Структура файлов

| Файл | Ответственность | Задача |
|---|---|---|
| `utils.py` | гард исходящего текста: вырезает обороты, которых клиент видеть не должен | 1 |
| `prompts.py` | правила для модели и детерминированные инструкции хода | 1, 2 |
| `core.py` | распознавание прощания, ветка контактов, сборка ответа | 2, 3 |
| `routing/planner.py` | стартовая ветка: спрашивать имя один раз, дальше не держать | 4 |
| `tests/test_false_promises_guard.py` | новый: обещания не доходят до клиента | 1 |
| `tests/test_farewell_midway.py` | новый: прощание в середине разговора | 2 |
| `tests/test_contact_request_variants.py` | новый: повторная просьба звучит иначе | 3 |
| `tests/test_name_is_not_a_gate.py` | новый: имя не останавливает анкету | 4 |

---

### Задача 1: Обещания про заявку и менеджера не доходят до клиента

Из боевой переписки 08.09.2026, дословно: «Посмотрю, что у нас есть по вашей
заявке» — бот заявок не видит вообще; «С менеджером свяжу, но сначала нужно
разобраться в ситуации» — он не связывает, он собирает анкету и отдаёт лид.

Лечится тем же способом, что и гарантии одобрения: словарь оборотов в гарде
исходящего текста плюс правило в промпте. Гард — страховка, промпт — просьба;
работают вместе.

**Файлы:**
- Изменить: `utils.py` (список `_FORBIDDEN_GUARANTEES` и рядом с ним)
- Изменить: `prompts.py` (правило в системном промпте разговора)
- Создать: `tests/test_false_promises_guard.py`

**Интерфейсы:**
- Использует: `utils.guard_bubbles(bubbles, *, conversation_over=False)` —
  существующая функция, возвращает отфильтрованный список строк
- Отдаёт: константу `utils._FORBIDDEN_PROMISES` (кортеж строк в нижнем
  регистре) и расширенное поведение `guard_bubbles`

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_false_promises_guard.py`:

```python
"""Бот не обещает того, чего не делает.

Боевая переписка 08.09.2026: «Посмотрю, что у нас есть по вашей заявке» —
заявок бот не видит ни одной; «С менеджером свяжу» — не связывает. Правило
«не обещать невыполнимого» уже действует для одобрения и ставок; здесь оно
распространяется на заявки и менеджеров.
"""
from __future__ import annotations

import pytest

import prompts
from utils import guard_bubbles


@pytest.mark.parametrize(
    "bubble",
    [
        "Посмотрю, что у нас есть по вашей заявке.",
        "посмотрю вашу заявку прямо сейчас",
        "Сейчас проверю статус вашей заявки.",
        "С менеджером свяжу, но сначала пара вопросов.",
        "Соединю вас с менеджером.",
        "Передам менеджеру, он перезвонит вам сегодня.",
    ],
)
def test_a_promise_the_bot_cannot_keep_never_reaches_the_client(bubble):
    assert guard_bubbles([bubble, "Какая сумма вам нужна?"]) == [
        "Какая сумма вам нужна?"
    ]


@pytest.mark.parametrize(
    "bubble",
    [
        "Заявку оформим здесь, в чате.",
        "Ситуация понятная, не переживайте — разберёмся.",
        "Данных достаточно, могу переходить к подбору.",
    ],
)
def test_honest_phrases_are_not_touched(bubble):
    assert guard_bubbles([bubble]) == [bubble]


def test_the_prompt_forbids_promising_to_look_up_an_application():
    text = prompts.CONVERSATION_SYSTEM_PROMPT.lower()

    assert "заявк" in text
    assert "не обещай" in text or "не говори" in text
```

- [ ] **Шаг 2: Запустить тест и убедиться, что падает**

Запустить: `.venv313/Scripts/python -m pytest tests/test_false_promises_guard.py -v`

Ожидается: FAIL — первый тест не отфильтровал обещания, третий не нашёл
правило в промпте.

- [ ] **Шаг 3: Добавить словарь обещаний в гард**

В `utils.py` сразу после кортежа `_FORBIDDEN_GUARANTEES` добавить:

```python
# Обещания, которых бот не выполняет. Он не видит заявок в CRM и не связывает
# с менеджером: заявку создаёт лид-интейк, а звонит колл-центр заказчика.
# Боевая переписка 08.09.2026 — «Посмотрю, что у нас есть по вашей заявке» и
# «С менеджером свяжу»: обе фразы прозвучали и обе были неправдой.
_FORBIDDEN_PROMISES = (
    "посмотрю, что у нас есть по вашей заявке",
    "посмотрю вашу заявку",
    "посмотрю по вашей заявке",
    "проверю статус вашей заявки",
    "проверю вашу заявку",
    "статус вашей заявки",
    "с менеджером свяжу",
    "свяжу вас с менеджером",
    "соединю вас с менеджером",
    "соединю с менеджером",
    "передам менеджеру",
    "передам вашу заявку менеджеру",
)
```

В теле `guard_bubbles`, в цикле по пузырям, добавить проверку сразу после
проверки `_FORBIDDEN_GUARANTEES`:

```python
        if any(marker in lowered for marker in _FORBIDDEN_PROMISES):
            only_internal = False
            continue
```

- [ ] **Шаг 4: Добавить правило в промпт**

В `prompts.py`, в текст `CONVERSATION_SYSTEM_PROMPT`, в блок запретов добавить
абзац:

```
- Не обещай того, чего не делаешь. Ты не видишь заявок клиента в системе, не
  проверяешь их статус и не соединяешь с менеджером. Если человек спрашивает
  про свою заявку — честно скажи, что отсюда её не видишь, и предложи то, что
  действительно можешь: продолжить подбор или оставить номер для связи.
```

- [ ] **Шаг 5: Запустить тест и убедиться, что проходит**

Запустить: `.venv313/Scripts/python -m pytest tests/test_false_promises_guard.py -v`

Ожидается: PASS, 10 тестов (6 + 3 + 1).

- [ ] **Шаг 6: Прогнать весь набор**

Запустить: `.venv313/Scripts/python -m pytest -q` и
`.venv313/Scripts/python -m ruff check .`

Ожидается: зелено. Проверено заранее: ни в `tests/`, ни в корпусах эвалов
оборотов «передам менеджеру», «свяжу вас с менеджером» и «соединю» сегодня нет,
поэтому новый словарь ничего существующего не задевает. Если тест всё же упал —
читать, какую именно фразу он ждёт, и заменять её в тесте на честную
формулировку, а не ослаблять словарь.

`CONVERSATION_SYSTEM_PROMPT` — f-строка (`prompts.py`, строка 61). Новый абзац
вставлять в неё как обычный текст; фигурные скобки в нём не использовать,
иначе Python примет их за подстановку.

- [ ] **Шаг 7: Коммит**

```bash
git add utils.py prompts.py tests/test_false_promises_guard.py
git commit -m "Бот больше не обещает посмотреть заявку и связать с менеджером

Боевая переписка 08.09.2026: «Посмотрю, что у нас есть по вашей заявке» —
заявок бот не видит ни одной, и посмотреть их ему нечем. «С менеджером свяжу» —
он не связывает: собирает анкету и отдаёт лид, а звонит колл-центр.

Правило «не обещать невыполнимого» уже действовало для одобрения и ставок.
Теперь оно распространено на заявки и менеджеров: словарь в исходящем гарде
плюс запрет в промпте. Гард — страховка на случай, когда просьба не сработала."
```

---

### Задача 2: Прощание в середине разговора

Из боевой переписки: человек пишет «Спасибо» или «До свидания», а бот в ответ
снова просит представиться. Проверено на выборке обрывов на вопросе об имени —
среди последних реплик клиента есть «Спасибо», «Спасибо!», «До свидания».

Функция `core.conversation_is_over` уже умеет распознавать поддакивания, но
работает **только после финала** — `dialog_phase` из `_TERMINAL_PHASES`. Здесь
нужен тот же приём для незавершённого разговора, и с другим исходом: не
молчание, а тёплое прощание без вопроса.

**Файлы:**
- Изменить: `core.py` (рядом с `conversation_is_over`, ~строка 1441; ветка
  инструкции в `turn_instruction`, ~строка 827; `_merge_contact_request`,
  ~строка 1185)
- Изменить: `prompts.py` (новая константа инструкции)
- Создать: `tests/test_farewell_midway.py`

**Интерфейсы:**
- Использует: `core._ACKNOWLEDGEMENTS` (frozenset строк), `core._ACK_WORD_RE`,
  `state["_plan_object"]` типа `routing.Plan`
- Отдаёт: `core.client_is_saying_goodbye(user_message: str) -> bool` и
  `prompts.FAREWELL_INSTRUCTION: str`

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_farewell_midway.py`:

```python
"""«Спасибо» посреди анкеты — это прощание, а не пропущенный вопрос.

Боевая переписка 08.09.2026: среди диалогов, оборвавшихся на вопросе об имени,
последние реплики клиента — «Спасибо», «Спасибо!», «До свидания». В ответ бот
просил представиться ещё раз. Это последнее, что человек запомнил о боте.

`conversation_is_over` умеет распознавать такие реплики, но только после
финала. Здесь то же самое до финала, и с другим исходом: не молчание, а
короткое человеческое прощание без вопроса.
"""
from __future__ import annotations

import pytest

import core
import prompts
from routing import Plan, get_matrix
from routing.planner import PlannedField
from state import init_state


@pytest.mark.parametrize(
    "message",
    ["Спасибо", "спасибо!", "Спасибо, до свидания", "До свидания", "Пока",
     "всего доброго", "Благодарю"],
)
def test_a_farewell_is_recognised(message):
    assert core.client_is_saying_goodbye(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "Спасибо, а какая ставка?",
        "спасибо, мне нужно 300000",
        "Спасибо за информацию, продолжим",
        "",
        "Иван",
    ],
)
def test_a_message_with_substance_is_not_a_farewell(message):
    assert core.client_is_saying_goodbye(message) is False


def test_the_farewell_turn_tells_the_model_to_say_goodbye():
    state = init_state()
    state["_plan_object"] = Plan(action="ask")
    state["last_user_message"] = "Спасибо"

    assert core.turn_instruction(state) is prompts.FAREWELL_INSTRUCTION


def test_prepare_turn_remembers_the_last_message():
    """turn_instruction получает только state — реплику надо туда положить."""
    state = core.prepare_turn(init_state(), "Спасибо", submit_delivery=False)

    assert state["last_user_message"] == "Спасибо"


def test_the_farewell_instruction_forbids_asking_anything():
    text = prompts.FAREWELL_INSTRUCTION.lower()

    assert "не задавай" in text or "не спрашивай" in text


def test_the_contact_request_is_not_glued_to_a_farewell():
    """Иначе «Спасибо» получает в ответ просьбу представиться."""
    spec = get_matrix().fields["client.name"]
    plan = Plan(action="ask", fields=[PlannedField(path="client.name", spec=spec)])

    bubbles = core._merge_contact_request(
        ["Спасибо вам! Если понадоблюсь — я здесь."],
        plan,
        user_message="Спасибо",
    )

    assert "обращаться" not in " ".join(bubbles).lower()
```

- [ ] **Шаг 2: Запустить тест и убедиться, что падает**

Запустить: `.venv313/Scripts/python -m pytest tests/test_farewell_midway.py -v`

Ожидается: FAIL с `AttributeError: module 'core' has no attribute
'client_is_saying_goodbye'`.

- [ ] **Шаг 3: Написать распознаватель прощания**

В `core.py` рядом с `conversation_is_over` добавить:

```python
# Слова, которыми заканчивают разговор. Подмножество `_ACKNOWLEDGEMENTS`:
# «ага» и «ок» посреди анкеты означают «продолжай», а «спасибо» и «до
# свидания» — «я ухожу».
_FAREWELL_WORDS = frozenset(
    {
        "спасибо", "спс", "благодарю", "пока", "прощайте", "досвидания",
        "свидания", "доброго", "всего", "удачи",
    }
)


def client_is_saying_goodbye(user_message: str) -> bool:
    """Клиент прощается, а не отвечает на вопрос.

    Боевая переписка 08.09.2026: на «Спасибо» и «До свидания» бот отвечал
    просьбой представиться. Распознаём так же строго, как поддакивания после
    финала: любой вопрос, любая цифра и любое лишнее слово означают, что
    разговор продолжается.
    """
    text = " ".join((user_message or "").split())
    if not text:
        return False
    if "?" in text or any(ch.isdigit() for ch in text):
        return False

    words = [word.casefold() for word in _ACK_WORD_RE.findall(text)]
    if not words or len(words) > 3:
        return False
    return all(word in _FAREWELL_WORDS or word in _ACKNOWLEDGEMENTS for word in words) and any(
        word in _FAREWELL_WORDS for word in words
    )
```

- [ ] **Шаг 4: Добавить инструкцию хода**

В `prompts.py` добавить константу рядом с другими инструкциями:

```python
FAREWELL_INSTRUCTION = (
    "Клиент прощается. Ответь одной короткой тёплой фразой и НЕ задавай "
    "никаких вопросов: ни про имя, ни про анкету, ни про телефон. Скажи, что "
    "будешь на связи, если понадобишься. Не уговаривай продолжить."
)
```

- [ ] **Шаг 5: Подключить ветку в `turn_instruction`**

В `core.turn_instruction`, сразу после проверки `current_plan is None` и до
ветки уточнения локации, добавить:

```python
    if client_is_saying_goodbye(state.get("last_user_message") or ""):
        return prompts.FAREWELL_INSTRUCTION
```

Ключа `last_user_message` в состоянии **сегодня нет** — проверено. Его надо
завести: `turn_instruction` принимает только `state`, а реплика ему нужна.

В `core.prepare_turn`, сразу после `new_state = deepcopy(state)` (строка ~172),
добавить:

```python
    # Инструкция хода читает только state, а решение «клиент прощается»
    # принимается по реплике. Кладём её сюда, а не тащим параметром через все
    # вызовы: в истории она всё равно окажется на этом же ходу.
    new_state["last_user_message"] = user_message
```

Ключ попадает в `_public_state` только если его туда добавить — а его там нет,
проверено: фронту уезжают `chat_history`, `facts`, `dialog_phase` и план. Новых
персональных данных не появляется: та же реплика через ход ляжет в
`chat_history`.

- [ ] **Шаг 6: Не приклеивать просьбу о контактах к прощанию**

`core._merge_contact_request` принимает **два** аргумента — `bubbles` и
`current_plan` (строка 1172), состояния у неё нет. Добавляем третий, именованный
и необязательный, чтобы не трогать другие вызовы:

```python
def _merge_contact_request(
    bubbles: list[str],
    current_plan: Plan | None,
    *,
    user_message: str = "",
) -> list[str]:
```

И первой строкой тела:

```python
    if client_is_saying_goodbye(user_message):
        # Человек уходит. Просьба представиться поверх прощания — это то, на
        # чём обрывались диалоги: «Спасибо» → «подскажите, как к вам обращаться?»
        return bubbles
```

В `core.commit_turn` (строка ~944) единственный вызов дополняется аргументом:

```python
        bubbles = _merge_contact_request(bubbles, current_plan, user_message=user_message)
```

- [ ] **Шаг 7: Запустить тест и убедиться, что проходит**

Запустить: `.venv313/Scripts/python -m pytest tests/test_farewell_midway.py -v`

Ожидается: PASS, 16 тестов (7 + 5 + 4 отдельных).

- [ ] **Шаг 8: Прогнать весь набор**

Запустить: `.venv313/Scripts/python -m pytest -q` и
`.venv313/Scripts/python -m ruff check .`

Ожидается: зелено. Особое внимание — `tests/test_farewell_loop.py`: он
проверяет молчание после финала, и новая ветка не должна его сломать
(`conversation_is_over` срабатывает раньше и возвращает пустой ответ).

- [ ] **Шаг 9: Коммит**

```bash
git add core.py prompts.py tests/test_farewell_midway.py
git commit -m "«Спасибо» посреди анкеты больше не получает вопрос в ответ

Боевая переписка 08.09.2026: среди диалогов, оборвавшихся на вопросе об имени,
последние реплики клиента — «Спасибо», «Спасибо!», «До свидания». Бот в ответ
просил представиться ещё раз, и это последнее, что человек о нём запоминал.

Распознавание прощания устроено так же строго, как молчание после финала:
любой вопрос, любая цифра, любое лишнее слово означают, что разговор
продолжается. На прощание бот отвечает одной фразой и не задаёт вопросов —
просьба о контактах к такому ходу не приклеивается."
```

---

### Задача 3: Повторная просьба звучит иначе

Просьба представиться зашита константой в `core._merge_contact_request`
(строка ~1221) и потому повторяется дословно. Замер по базе: 25 случаев, когда
одна и та же фраза бота стоит в соседних ходах; «Прежде чем продолжим,
подскажите, пожалуйста, как к вам обращаться?» — четыре из них.

Число попыток уже считается: `core._count_attempts` пишет их в
`state["script"]["attempts"]`, ключ — путь поля.

**Файлы:**
- Изменить: `core.py` (`_merge_contact_request`, ветки `contact_paths`)
- Создать: `tests/test_contact_request_variants.py`

**Интерфейсы:**
- Использует: `state["script"]["attempts"]` — `dict[str, int]`, ключ
  `"client.name"`; `routing.Plan.field_paths`
- Отдаёт: изменённое поведение `core._merge_contact_request`

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_contact_request_variants.py`:

```python
"""Второй раз бот просит имя другими словами.

Просьба зашита константой в backend — осознанно, чтобы модель не переврала
контактный вопрос. Но варианта для второй попытки не было, и повтор выходил
дословным: 25 случаев в базе за три недели, из них четыре — про имя.
"""
from __future__ import annotations

from routing import Plan, get_matrix
from routing.planner import PlannedField
from state import init_state

import core


def _plan_for_name() -> Plan:
    spec = get_matrix().fields["client.name"]
    return Plan(action="ask", fields=[PlannedField(path="client.name", spec=spec)])


def _state(attempts: int) -> dict:
    state = init_state()
    state["script"] = {"attempts": {"client.name": attempts}}
    return state


def test_the_first_request_is_the_familiar_one():
    bubbles = core._merge_contact_request(["Давайте разберёмся."], _plan_for_name(), _state(1))

    assert "как к вам обращаться" in " ".join(bubbles).lower()


def test_the_second_request_is_worded_differently():
    first = core._merge_contact_request(["Давайте разберёмся."], _plan_for_name(), _state(1))
    second = core._merge_contact_request(["Давайте разберёмся."], _plan_for_name(), _state(2))

    assert first != second


def test_the_second_request_offers_a_way_out():
    """Человек мог не захотеть называть имя — предлагаем продолжить без него."""
    second = " ".join(
        core._merge_contact_request(["Хорошо."], _plan_for_name(), _state(2))
    ).lower()

    assert "продолж" in second or "без имени" in second
```

- [ ] **Шаг 2: Запустить тест и убедиться, что падает**

Запустить: `.venv313/Scripts/python -m pytest tests/test_contact_request_variants.py -v`

Ожидается: FAIL на `test_the_second_request_is_worded_differently` — обе
просьбы совпадают.

- [ ] **Шаг 3: Ввести вариант для повторной попытки**

В `core._merge_contact_request`, в ветке `elif contact_paths == {"client.name"}:`
(та, что не под `action == "contacts"`, строка ~1219) заменить тело на:

```python
    elif contact_paths == {"client.name"}:
        asked = int(
            ((state.get("script") or {}).get("attempts") or {}).get("client.name") or 0
        )
        request = (
            "Прежде чем продолжим, подскажите, пожалуйста, как к вам обращаться?"
            if asked <= 1
            # Второй раз дословно повторять нельзя: в базе такие повторы стоят
            # подряд и выглядят как заедание. Даём выход — имя не обязательно.
            else "Могу обращаться просто на «вы», если имя называть не хочется. "
            "Продолжим?"
        )
```

- [ ] **Шаг 4: Запустить тест и убедиться, что проходит**

Запустить: `.venv313/Scripts/python -m pytest tests/test_contact_request_variants.py -v`

Ожидается: PASS, 3 теста.

- [ ] **Шаг 5: Прогнать весь набор**

Запустить: `.venv313/Scripts/python -m pytest -q` и
`.venv313/Scripts/python -m ruff check .`

Ожидается: зелено. Если упал тест, проверяющий дословную формулировку просьбы
(искать по строке «Прежде чем продолжим» в `tests/`), — он должен проверять
первую попытку, то есть `attempts = 1`; поправить состояние в тесте, а не
формулировку в коде.

- [ ] **Шаг 6: Коммит**

```bash
git add core.py tests/test_contact_request_variants.py
git commit -m "Повторная просьба об имени звучит иначе и даёт выход

Просьба зашита константой в backend — осознанно, чтобы модель не переврала
контактный вопрос. Но варианта для второй попытки не было, и повтор выходил
дословным: 25 таких повторов в базе за три недели, четыре из них про имя.

Со второй попытки бот предлагает обращаться на «вы» и продолжить: человек мог
просто не захотеть называть имя, и настаивать здесь бессмысленно."
```

---

### Задача 4: Имя перестаёт быть шлагбаумом

`routing.planner.plan` начинает с ветки знакомства: пока поля с `initial: true`
пусты, анкета не начинается вообще. Единственное такое поле — `client.name`
(`routing/matrix.yaml`, строка 42). Из-за этого любая реплика, из которой имя не
извлеклось, возвращает тот же вопрос: вторая по частоте точка обрыва — 17
диалогов.

Для CRM обязателен только телефон; имя нужно к моменту создания лида, а не в
начале разговора. У `client.name` уже стоит `contact: true`, поэтому после
снятия шлагбаума оно само добирается на контактном шаге вместе с телефоном.

**Файлы:**
- Изменить: `routing/planner.py` (ветка `initial` в `plan`, строки ~117-130)
- Создать: `tests/test_name_is_not_a_gate.py`

**Интерфейсы:**
- Использует: `routing.planner.plan(matrix, facts, *, attempts=None, ...)`,
  `routing.planner._asked_too_many_times(attempts, path)`
- Отдаёт: изменённое поведение `plan` — ветка знакомства пропускается, когда
  имя уже спрашивали

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_name_is_not_a_gate.py`:

```python
"""Имя спрашивают один раз, дальше анкета идёт без него.

Вторая по частоте точка обрыва в бою — вопрос об имени, 17 диалогов за три
недели. Пока имя не названо, планировщик не пускал дальше ничего: любая
реплика, из которой имя не извлеклось, возвращала тот же вопрос.

Для CRM обязателен только телефон. Имя помечено `contact: true`, поэтому после
снятия шлагбаума оно добирается вместе с телефоном в конце — данные не
теряются, а разговор не встаёт.
"""
from __future__ import annotations

from routing import get_matrix, plan


def test_the_name_is_asked_first_when_nobody_asked_yet():
    result = plan(get_matrix(), {}, attempts={})

    assert [field.path for field in result.fields] == ["client.name"]


def test_after_one_unanswered_ask_the_questionnaire_starts():
    """Раньше здесь снова возвращался вопрос об имени, и так до бесконечности."""
    result = plan(get_matrix(), {}, attempts={"client.name": 1})

    assert result.action == "ask"
    assert "client.name" not in [field.path for field in result.fields]


def test_the_name_comes_back_with_the_phone_at_the_end():
    facts = {
        "request": {"amount": 500_000},
        "client": {"age": 44, "region_class": "msk_mo"},
        "debts": {"has_active_credits": False},
        "employment": {"type": "official", "income": 80_000},
        "assets": {"has_realty": False, "has_vehicle": False},
    }

    result = plan(get_matrix(), facts, max_fields=4, attempts={"client.name": 1})

    assert result.action == "contacts"
    assert {"client.name", "client.phone"} <= {field.path for field in result.fields}


def test_a_named_client_never_sees_the_question_again():
    result = plan(get_matrix(), {"client": {"name": "Иван"}}, attempts={"client.name": 1})

    assert "client.name" not in [field.path for field in result.fields]
```

- [ ] **Шаг 2: Запустить тест и убедиться, что падает**

Запустить: `.venv313/Scripts/python -m pytest tests/test_name_is_not_a_gate.py -v`

Ожидается: FAIL на `test_after_one_unanswered_ask_the_questionnaire_starts` —
планировщик снова вернул `client.name`.

- [ ] **Шаг 3: Пропускать ветку знакомства после первой попытки**

В `routing/planner.py`, в функции `plan`, заменить формирование списка
`initial`:

```python
    # Знакомство. Спрашиваем один раз: имя, из которого не удалось ничего
    # извлечь, останавливало анкету навсегда — 17 обрывов в бою за три недели.
    # Для CRM обязателен только телефон, а `contact: true` вернёт вопрос об
    # имени в конце, вместе с номером.
    initial = [
        path
        for path, spec in matrix.fields.items()
        if spec.initial
        and get_fact(facts, path) in (None, "")
        and not (attempts or {}).get(path)
    ]
```

- [ ] **Шаг 4: Запустить тест и убедиться, что проходит**

Запустить: `.venv313/Scripts/python -m pytest tests/test_name_is_not_a_gate.py -v`

Ожидается: PASS, 4 теста.

- [ ] **Шаг 5: Прогнать весь набор**

Запустить: `.venv313/Scripts/python -m pytest -q` и
`.venv313/Scripts/python -m ruff check .`

Ожидается: часть тестов планировщика и диалога может упасть — они писались,
когда имя было шлагбаумом. Разбирать каждый по существу:

- если тест проверяет, что **первый** вопрос — про имя, он остаётся верным:
  передать `attempts={}` или не передавать вовсе;
- если тест ждёт повторного вопроса об имени после неудачной попытки, он
  кодирует прежнее поведение — переписать под новое, сохранив исходную мысль в
  докстринге;
- если упал ролевой или диалоговый эвал (`tests/test_dialog_eval.py`,
  `tests/test_behavior_eval.py`) — прочитать сценарий: возможно, в нём имя
  называется вторым ходом, и порядок вопросов сдвинулся.

Ни один падающий тест не «чинить» ослаблением проверки: либо поведение верное
и тест переписывается, либо поведение неверное и правится код.

- [ ] **Шаг 6: Коммит**

```bash
git add routing/planner.py tests/test_name_is_not_a_gate.py
git commit -m "Имя больше не останавливает анкету

Вторая по частоте точка обрыва в бою — вопрос об имени, 17 диалогов за три
недели. Пока имя не названо, планировщик не пускал дальше ничего: реплика, из
которой имя не извлеклось, возвращала тот же вопрос, и так по кругу.

Спрашиваем один раз. Для CRM обязателен только телефон, а `contact: true` у
поля возвращает вопрос об имени в конце, вместе с номером: данные не теряются,
разговор не встаёт."
```

---

### Задача 5: Проверка на живых ролях

Четыре правки меняют то, что видит клиент. Прежде чем отдавать в поставку,
нужно убедиться, что разговор не поехал: ролевой прогон измеряет человечность и
связность на девяти сценариях.

**Файлы:**
- Изменить: нет. Задача проверочная.

**Интерфейсы:**
- Использует: `run_roleplay_eval.py --base-url <адрес>` — существующий прогон

- [ ] **Шаг 1: Поднять локальный backend**

```bash
.venv313/Scripts/python -m uvicorn main:app --host 127.0.0.1 --port 8020
```

Проверить: `curl http://127.0.0.1:8020/api/health` возвращает 200.

- [ ] **Шаг 2: Прогнать девять ролей**

```bash
.venv313/Scripts/python run_roleplay_eval.py --base-url http://127.0.0.1:8020
```

Ожидается: девять ролей из девяти дошли до конца; человечность не ниже 98,6;
связность 100.

- [ ] **Шаг 3: Прочитать диалоги ролей, где клиент благодарит**

Найти в выводе прогона реплики «спасибо» и убедиться, что бот не задаёт после
них вопрос. Если задаёт — распознаватель прощания слишком строг: проверить, не
попала ли в реплику цифра или знак вопроса.

- [ ] **Шаг 4: Зафиксировать результат прогона**

Дописать в `docs/agent-memory/SESSION.md`, в раздел «Сделано», строку с датой,
числами прогона и списком четырёх правок.

- [ ] **Шаг 5: Коммит**

```bash
git add docs/agent-memory/SESSION.md
git commit -m "Ролевой прогон после правок текста: результат зафиксирован"
```

---

## Что дальше

Этот блок не трогает ни движок, ни матрицу, ни контракт с CRM, поэтому уезжает
в поставку отдельно от остальных. Следующий блок спеки — чипсы и кнопки в
приветствии: он требует доступа на запись в `mbk-site`.
