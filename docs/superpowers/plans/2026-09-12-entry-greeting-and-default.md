# Развилка по умолчанию и голые приветствия — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Развилка на входе работает, пока её явно не выключили, а разговор, начатый голым «Здравствуйте», получает приветствие в ответ и развилку по следующей, содержательной реплике.

**Architecture:** Флаг `MBK_ENTRY_ROUTING` меняет умолчание в `config.py`. Распознавание голого приветствия и правило «решение открыто» живут в `entry_routing.py` рядом с остальной политикой сценариев. В `core.prepare_turn` голое приветствие первой репликой пропускает извлекатель и откладывает решение на ход (временный ключ `_entry_greeting`); `core.turn_instruction` отдаёт на этом ходу инструкцию приветствия, `core.commit_turn` не добавляет анкетных вставок.

**Tech Stack:** Python 3.13, pydantic 2, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-09-09-dialog-entry-and-order-design.md` (компоненты 1 и 2) и решения человека 12.09.2026, записанные в `docs/agent-memory/SESSION.md`: флаг включён по умолчанию; голые приветствия — вариант Б. Реализация развилки — план `docs/superpowers/plans/2026-09-10-entry-intent.md`.

## Global Constraints

- Решение 12.09.2026: флаг `MBK_ENTRY_ROUTING` **включён по умолчанию**; выключение — явное `0` — возвращает поведение без развилки целиком. Это заменяет строку спеки «Все выключены по умолчанию» для этого флага.
- Решение 12.09.2026, вариант Б: «на голое приветствие бот здоровается в ответ и спрашивает, чем помочь, без анкеты и имени, решение о ветке открыто до первой содержательной реплики, не дольше двух реплик».
- «Fail-open — несущее свойство. При любом сомнении — `unclear`, и разговор идёт как сегодня.» Всё, что не голое приветствие, идёт обычным путём.
- Намерение определяет модель; список форм приветствия решает только, есть ли в реплике что-то кроме приветствия (пользователь 09.09.2026: «лучше семантически», не словарём).
- «Ни одного дополнительного вызова модели и ни одной лишней секунды.»
- Лид не создаётся ни в одной ветке развилки (`entry_routing.lead_blocked`).
- В payload аналитики нет ПД.
- Процесс: коммиты от `Zheltenkov <zheltenkovvp@gmail.com>`, без упоминания Claude и без трейлеров Co-Authored-By. Push — только после утверждения человека (решено: позже). Боевая база — только чтение.
- Проверки перед завершением каждой задачи: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`.

## Карта файлов

| Файл | Что | Задача |
|---|---|---|
| `config.py`, `.env.example`, `deploy/prod/env.example`, `tests/conftest.py`, `tests/test_entry_routing_flag.py`, `docs/HANDOVER.md` | флаг включён по умолчанию | 1 |
| `entry_routing.py`, `tests/test_entry_greeting.py` | распознавание голого приветствия, открытое решение | 2 |
| `prompts.py`, `core.py`, `tests/test_entry_greeting.py`, `tests/test_entry_routing.py`, `tests/test_entry_routing_turn.py` | пауза на приветствии в ходе | 3 |
| `evaluation/personas.py`, `tests/test_personas.py` | роль «сначала поздоровался» | 4 |
| `docs/agent-memory/SESSION.md`, спека | итоги приёмки | 5 |

---

### Task 1: Развилка включена по умолчанию

**Files:**
- Modify: `config.py` — поле `entry_routing_enabled` и аргумент в `load_config()`
- Modify: `.env.example`, `deploy/prod/env.example` — блок «Развилка на входе»
- Modify: `tests/conftest.py` — комментарий у `delenv("MBK_ENTRY_ROUTING")`
- Modify: `docs/HANDOVER.md` — строка «**События развилки на входе** (пишутся при `MBK_ENTRY_ROUTING=1`).»
- Modify: `tests/test_entry_routing_flag.py` — переписать целиком
- Modify: тесты, которые полагались на выключенное умолчание (см. Step 4)

**Interfaces:**
- Consumes: —
- Produces: `AppConfig.entry_routing_enabled: bool = True`; `load_config()` выключает развилку только значениями `0`, `false`, `no`, `off` (регистр не важен); отсутствие переменной или пустое значение — включено.

- [ ] **Step 1: Переписать тест флага** — `tests/test_entry_routing_flag.py` целиком:

```python
"""Флаг развилки на входе: включён, пока его не выключили явно (решение 12.09.2026)."""
from __future__ import annotations

from pathlib import Path

import pytest

from config import AppConfig, load_config


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "sk-or-test")


def test_entry_routing_is_on_by_default(monkeypatch):
    monkeypatch.delenv("MBK_ENTRY_ROUTING", raising=False)

    assert load_config().entry_routing_enabled is True
    assert AppConfig(openrouter_api_key="sk-or-test").entry_routing_enabled is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "FALSE"])
def test_entry_routing_switches_off_explicitly(monkeypatch, raw):
    monkeypatch.setenv("MBK_ENTRY_ROUTING", raw)

    assert load_config().entry_routing_enabled is False


@pytest.mark.parametrize("raw", ["1", "true", "yes", ""])
def test_anything_else_keeps_it_on(monkeypatch, raw):
    monkeypatch.setenv("MBK_ENTRY_ROUTING", raw)

    assert load_config().entry_routing_enabled is True


def test_env_templates_list_the_flag_switched_on():
    """Ветка поставки не содержит файлов нашего стенда — проверяем те, что есть."""
    for template in (ROOT / ".env.example", ROOT / "deploy" / "prod" / "env.example"):
        if template.exists():
            assert "MBK_ENTRY_ROUTING=1" in template.read_text(encoding="utf-8"), template
```

- [ ] **Step 2: Убедиться, что падает**

Run: `.venv313/Scripts/python -m pytest tests/test_entry_routing_flag.py -q`
Expected: FAIL — умолчание `False`, шаблоны содержат `=0`.

- [ ] **Step 3: Реализовать**

В `config.py` заменить комментарий и поле

```python
    # Развилка на входе (docs/superpowers/specs/2026-09-09-dialog-entry-and-
    # order-design.md): первая реплика клиента делится на «деньги», «уже
    # оставлял заявку» и «не наш профиль». Выключено по умолчанию, и
    # выключение возвращает поведение без развилки целиком — вместе с
    # промптом извлекателя.
    entry_routing_enabled: bool = False
```

на

```python
    # Развилка на входе (docs/superpowers/specs/2026-09-09-dialog-entry-and-
    # order-design.md): первая реплика клиента делится на «деньги», «уже
    # оставлял заявку» и «не наш профиль». Включено по умолчанию (решение
    # 12.09.2026); выключение — MBK_ENTRY_ROUTING=0 — возвращает поведение без
    # развилки целиком, вместе с промптом извлекателя.
    entry_routing_enabled: bool = True
```

В `load_config()` заменить

```python
        entry_routing_enabled=(_read_env_value("MBK_ENTRY_ROUTING") or "").lower()
        in {"1", "true", "yes"},
```

на

```python
        # Выключается только явным значением: забытая переменная означает
        # «развилка работает», как решено 12.09.2026.
        entry_routing_enabled=(_read_env_value("MBK_ENTRY_ROUTING") or "1").lower()
        not in {"0", "false", "no", "off"},
```

В `.env.example` и в `deploy/prod/env.example` блок

```
# === Развилка на входе ===
# Первая реплика клиента делится на «деньги», «уже оставлял заявку» и «не наш
# профиль». В двух последних ветках анкета не начинается, а в ветке «уже
# оставлял заявку» лид не создаётся. 0 — поведение без развилки целиком.
MBK_ENTRY_ROUTING=0
```

заменить на

```
# === Развилка на входе ===
# Первая реплика клиента делится на «деньги», «уже оставлял заявку» и «не наш
# профиль». В двух последних ветках анкета не начинается и лид не создаётся.
# Включено по умолчанию; 0 — поведение без развилки целиком.
MBK_ENTRY_ROUTING=1
```

В `tests/conftest.py` комментарий над `monkeypatch.delenv("MBK_ENTRY_ROUTING", raising=False)` заменить на:

```python
    # Флаг развилки из локального .env не должен решать за тесты: без
    # переменной действует умолчание кода (развилка включена). Тест, которому
    # нужно поведение без развилки, передаёт entry_routing_enabled=False сам.
```

В `docs/HANDOVER.md` строку

```
**События развилки на входе** (пишутся при `MBK_ENTRY_ROUTING=1`).
```

заменить на

```
**События развилки на входе** (пишутся, пока развилка включена: по умолчанию
да, выключает `MBK_ENTRY_ROUTING=0`).
```

- [ ] **Step 4: Тесты, полагавшиеся на выключенное умолчание**

Run: `.venv313/Scripts/python -m pytest -q`

Известный случай: `tests/test_entry_intent_extractor.py`, тест `test_flag_off_keeps_todays_request` создаёт `AppConfig(openrouter_api_key="sk-or-test")` и проверяет промпт без развилки — передать `entry_routing_enabled=False` явно.

Для каждого другого упавшего теста решить по одному правилу:
- тест проверяет поведение **без развилки** (точный состав событий аналитики, промпт извлекателя, старый сценарий) — передать `entry_routing_enabled=False` в его `AppConfig(...)` и добавить над ним одну строку комментария `# Поведение без развилки: флаг выключен явно.`;
- падение показывает, что **с включённой развилкой продукт ведёт себя неправильно** (например, анкетный тест уводится в ветку) — не править тест, остановиться и сообщить BLOCKED с именем теста и причиной.

Expected после правок: все тесты зелёные; ruff чисто.

- [ ] **Step 5: Коммит**

```bash
git add config.py .env.example deploy/prod/env.example tests/conftest.py tests/test_entry_routing_flag.py docs/HANDOVER.md tests/
git commit -m "Развилка на входе включена по умолчанию"
```

---

### Task 2: Голое приветствие и открытое решение

**Files:**
- Modify: `entry_routing.py` — импорт `re`; новые константы и функции после `is_first_client_message`
- Test: `tests/test_entry_greeting.py` (создать)

**Interfaces:**
- Consumes: —
- Produces:
  - `GREETING_KEY = "_entry_greeting"`
  - `is_bare_greeting(message: str, *, assistant_name: str | None = None) -> bool`
  - `entry_decision_open(state: dict, *, assistant_name: str | None = None) -> bool`
  - `holds_for_greeting(state: dict, user_message: str, *, assistant_name: str | None = None) -> bool`

- [ ] **Step 1: Написать падающие тесты** — `tests/test_entry_greeting.py`:

```python
"""Голое приветствие: бот здоровается в ответ, решение о ветке ждёт сути.

Решение человека 12.09.2026, вариант Б. Каждый седьмой разговор начинается с
одного «Здравствуйте», и настоящий вопрос приходит второй репликой — когда
развилка уже закрыта, а бот успел попросить представиться.
"""
from __future__ import annotations

import pytest

import entry_routing as er


OPENING = "Добрый день! Я Ника, специалист МБК. Чем я могу вам помочь?"


def _history(*client_messages: str) -> dict:
    history = [{"role": "assistant", "content": OPENING}]
    for message in client_messages:
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": "…"})
    return {"chat_history": history}


@pytest.mark.parametrize(
    "message",
    [
        "Здравствуйте",
        "Добрый день!",
        "доброе утро",
        "Здравствуйте?",
        "Привет",
        "Доброго времени суток",
        "  Добрый вечер ,  ",
        "алло",
    ],
)
def test_bare_greetings(message):
    assert er.is_bare_greeting(message) is True


def test_greeting_may_name_the_assistant():
    assert er.is_bare_greeting("Добрый день, Ника!", assistant_name="Ника") is True
    assert er.is_bare_greeting("Добрый день, Ника!") is False


@pytest.mark.parametrize(
    "message",
    [
        "",
        "   ",
        "?",
        "День",
        "Ника",
        "Здравствуйте, у меня вопрос",
        "Добрый день, нужен кредит",
        "Здравствуйте 89161234567",
        "Всего доброго",
        "Спасибо",
        "Здравствуйте, оставлял заявку",
    ],
)
def test_anything_with_substance_is_not_a_bare_greeting(message):
    assert er.is_bare_greeting(message) is False


def test_decision_is_open_before_the_first_client_message():
    assert er.entry_decision_open(_history()) is True
    assert er.entry_decision_open({}) is True


def test_decision_stays_open_after_a_bare_greeting():
    assert er.entry_decision_open(_history("Здравствуйте")) is True


def test_decision_closes_after_a_substantive_first_message():
    assert er.entry_decision_open(_history("Нужны деньги")) is False


def test_decision_closes_after_two_client_messages():
    """Не дольше двух реплик: два приветствия подряд — анкета, как сегодня."""
    assert er.entry_decision_open(_history("Здравствуйте", "Добрый день")) is False


def test_only_a_bare_first_greeting_holds_the_decision():
    assert er.holds_for_greeting(_history(), "Здравствуйте") is True
    assert er.holds_for_greeting(_history(), "Здравствуйте, нужен кредит") is False
    assert er.holds_for_greeting(_history("Здравствуйте"), "Здравствуйте") is False
    assert (
        er.holds_for_greeting(_history(), "Привет, Ника", assistant_name="Ника")
        is True
    )
```

- [ ] **Step 2: Убедиться, что падают**

Run: `.venv313/Scripts/python -m pytest tests/test_entry_greeting.py -q`
Expected: FAIL — `module 'entry_routing' has no attribute 'is_bare_greeting'`.

- [ ] **Step 3: Реализовать** — в `entry_routing.py`:

В импорты (после `from __future__ import annotations` и пустой строки) добавить `import re` перед `from typing import Any`.

После константы `TRANSITION_KEY` добавить:

```python

# Голое приветствие первой репликой откладывает решение на ход. Ключ живёт
# один ход, как и TRANSITION_KEY: `core.turn_instruction` отдаёт по нему
# инструкцию приветствия, `core.commit_turn` забирает его.
GREETING_KEY = "_entry_greeting"

# Слова, из которых складывается голое приветствие. Это не классификатор
# намерения: список решает только, есть ли в реплике что-то кроме
# приветствия. Всё, где есть хоть одно другое слово или цифра, идёт обычным
# путём, и намерение по-прежнему определяет модель.
_GREETING_WORDS = frozenset(
    {
        "здравствуйте", "здравствуй", "здраствуйте", "здрасте", "здрасьте",
        "привет", "приветствую", "добрый", "доброе", "доброго", "доброй",
        "день", "дня", "вечер", "вечера", "утро", "утра", "ночи", "времени",
        "суток", "алло", "ау", "hello", "hi",
    }
)
# Хотя бы одно слово должно быть собственно приветствием: «День» или имя
# ассистента сами по себе приветствием не являются.
_GREETING_ROOTS = ("здра", "привет", "добр", "алло", "ау", "hello", "hi")
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
```

После функции `is_first_client_message` добавить:

```python


def is_bare_greeting(message: str, *, assistant_name: str | None = None) -> bool:
    """Реплика — только приветствие: «Здравствуйте», «Добрый день, Ника!».

    Цифра, вопрос по делу или любое слово вне приветствия — уже не голое
    приветствие, и такая реплика идёт обычным путём (fail-open).
    """
    text = " ".join((message or "").split())
    if not text or any(ch.isdigit() for ch in text):
        return False
    words = [word.casefold() for word in _WORD_RE.findall(text)]
    if not words:
        return False
    allowed = set(_GREETING_WORDS)
    if assistant_name:
        allowed.update(word.casefold() for word in _WORD_RE.findall(assistant_name))
    if not all(word in allowed for word in words):
        return False
    return any(word.startswith(_GREETING_ROOTS) for word in words)


def _client_messages(state: dict[str, Any]) -> list[str]:
    history = state.get("chat_history") or []
    return [
        str(item.get("content") or "")
        for item in history
        if isinstance(item, dict) and item.get("role") == "user"
    ]


def entry_decision_open(
    state: dict[str, Any],
    *,
    assistant_name: str | None = None,
) -> bool:
    """Решение о ветке ещё не принято: клиент пока не сказал ничего по существу.

    Открыто на первой реплике клиента и на второй — если первая была голым
    приветствием. Дольше не держим: две реплики без сути — анкета, как
    сегодня (решение человека 12.09.2026, вариант Б).
    """
    previous = _client_messages(state)
    if not previous:
        return True
    return len(previous) == 1 and is_bare_greeting(
        previous[0], assistant_name=assistant_name
    )


def holds_for_greeting(
    state: dict[str, Any],
    user_message: str,
    *,
    assistant_name: str | None = None,
) -> bool:
    """Первая реплика клиента — голое приветствие: решение откладывается на ход.

    На этом ходу бот здоровается в ответ и спрашивает, с чем человек пришёл,
    без анкеты и без просьбы об имени. `state` — состояние до хода.
    """
    return not _client_messages(state) and is_bare_greeting(
        user_message, assistant_name=assistant_name
    )
```

- [ ] **Step 4: Проверить**

Run: `.venv313/Scripts/python -m pytest tests/test_entry_greeting.py tests/test_entry_routing.py -q`
Expected: all passed.
Run: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`
Expected: ruff чисто; все тесты зелёные.

- [ ] **Step 5: Коммит**

```bash
git add entry_routing.py tests/test_entry_greeting.py
git commit -m "Распознавание голого приветствия и открытое решение о ветке"
```

---

### Task 3: Пауза на приветствии внутри хода

**Files:**
- Modify: `prompts.py` — `GREETING_INSTRUCTION` после `OFF_TOPIC_INSTRUCTION`
- Modify: `core.py` — `prepare_turn` (пропуск извлекателя и отложенное решение), `turn_instruction`, `commit_turn`
- Modify: `entry_routing.py` — удалить ставшую ненужной `is_first_client_message`
- Modify: `tests/test_entry_routing.py` — удалить `test_first_client_message_is_detected_by_history`
- Modify: `tests/test_entry_routing_turn.py` — первые реплики «Здравствуйте» заменить содержательными (Step 5)
- Test: `tests/test_entry_greeting.py` (дописать в конец)

**Interfaces:**
- Consumes: `GREETING_KEY`, `holds_for_greeting`, `entry_decision_open` (Task 2); умолчание флага (Task 1).
- Produces: `prompts.GREETING_INSTRUCTION: str`. На ходу голого приветствия: извлекатель не вызывается, `state["entry"]` не пишется, событий `entry_routed`/`turn_completed` нет, к ответу не добавляется просьба об имени, попытки не считаются.

- [ ] **Step 1: Написать падающие тесты** — дописать в конец `tests/test_entry_greeting.py`:

```python
import analytics
import core
import prompts
from analytics import AnalyticsRecorder, MemorySink
from analytics import EVENTS as E
from config import AppConfig
from state import init_state


LEGACY = AppConfig(openrouter_api_key="sk-or-test", fact_interpretation_mode="legacy")


@pytest.fixture
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
    state["session_id"] = "entry-greeting"
    state["chat_history"] = [{"role": "assistant", "content": prompts.OPENING_MESSAGE}]
    return state


def _turn(state, message, reply, config=LEGACY):
    prepared = core.prepare_turn(state, message, config, submit_delivery=False)
    return prepared, core.commit_turn(prepared, message, reply)


def test_bare_greeting_gets_a_greeting_back_without_the_questionnaire(offline, sink):
    prepared, committed = _turn(
        _fresh_state(), "Здравствуйте", "Здравствуйте! С чем пришли?"
    )

    assert core.turn_instruction(prepared) == prompts.GREETING_INSTRUCTION
    assert committed["messages"] == ["Здравствуйте! С чем пришли?"]
    state = committed["current_state"]
    assert "entry" not in state
    assert er.GREETING_KEY not in state
    script = state.get("script") or {}
    assert not script.get("attempts")
    assert not script.get("asked")
    names = [event.event for event in sink.events]
    assert E.TURN_COMPLETED not in names
    assert E.ENTRY_ROUTED not in names


def test_bare_greeting_does_not_call_the_extractor(offline, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("на голое приветствие извлекатель не зовут")

    monkeypatch.setattr(core.llm_agent, "extract_facts", unexpected)
    monkeypatch.setattr(core.llm_agent, "extract_fact_candidates", unexpected)

    prepared = core.prepare_turn(
        _fresh_state(),
        "Добрый день!",
        AppConfig(openrouter_api_key="sk-or-test", fact_interpretation_mode="semantic"),
        submit_delivery=False,
    )

    assert prepared[er.GREETING_KEY] is True


def test_question_after_a_greeting_is_routed(offline, sink):
    _p, first = _turn(_fresh_state(), "Здравствуйте", "Здравствуйте! С чем пришли?")

    _p, second = _turn(
        first["current_state"],
        "Оставлял заявку, когда позвонит менеджер?",
        "Отсюда заявку не видно.",
    )

    assert second["current_state"]["entry"]["scenario"] == er.EXISTING
    assert second["messages"] == ["Отсюда заявку не видно."]


def test_money_after_a_greeting_starts_the_questionnaire(offline, sink):
    _p, first = _turn(_fresh_state(), "Здравствуйте", "Здравствуйте! С чем пришли?")

    _p, second = _turn(first["current_state"], "Нужны деньги", "Сколько нужно?")

    assert second["current_state"]["entry"]["scenario"] == er.QUESTIONNAIRE
    assert "обращаться" in " ".join(second["messages"])


def test_two_greetings_in_a_row_start_the_questionnaire(offline, sink):
    _p, first = _turn(_fresh_state(), "Здравствуйте", "Здравствуйте! С чем пришли?")

    prepared, second = _turn(first["current_state"], "Добрый день", "Слушаю вас.")

    assert second["current_state"]["entry"]["scenario"] == er.QUESTIONNAIRE
    assert core.turn_instruction(prepared) != prompts.GREETING_INSTRUCTION


def test_greeting_hold_needs_the_flag(offline):
    off = AppConfig(
        openrouter_api_key="sk-or-test",
        fact_interpretation_mode="legacy",
        entry_routing_enabled=False,
    )

    prepared, committed = _turn(
        _fresh_state(), "Здравствуйте", "Здравствуйте!", config=off
    )

    assert er.GREETING_KEY not in prepared
    assert "обращаться" in " ".join(committed["messages"])
```

Run: `.venv313/Scripts/python -m pytest tests/test_entry_greeting.py -q`
Expected: новые тесты падают (`no attribute 'GREETING_INSTRUCTION'`, в ходе нет паузы).

- [ ] **Step 2: Инструкция** — в `prompts.py` сразу после `OFF_TOPIC_INSTRUCTION = (...)`:

```python


# Голое приветствие первой репликой (решение 12.09.2026, вариант Б). Каждый
# седьмой разговор начинается с одного «Здравствуйте», и настоящий вопрос
# приходит следующей репликой. Раньше бот сразу просил представиться и начинал
# анкету — и развилка уже не видела, что человек пришёл спросить о заявке.
GREETING_INSTRUCTION = (
    "Клиент пока только поздоровался. Поздоровайся в ответ одной короткой "
    "живой фразой и спроси, с чем он пришёл. Не спрашивай имя, телефон и "
    "данные анкеты, не перечисляй услуги и ничего не обещай."
)
```

- [ ] **Step 3: `core.prepare_turn`**

Заменить

```python
        if expected_updates is not None or strict_local_name:
```

на

```python
        # Голое приветствие первой репликой: фактов в нём нет, и решение о
        # ветке откладывается на ход (вариант Б, 12.09.2026). Звать извлекатель
        # незачем — это пять секунд молчания перед приветствием в ответ.
        greeting_hold = config.entry_routing_enabled and entry_routing.holds_for_greeting(
            state, user_message, assistant_name=prompts.ASSISTANT_NAME
        )
        if expected_updates is not None or strict_local_name or greeting_hold:
```

Блок развилки заменить

```python
        if config.entry_routing_enabled:
            model_intent = (
                entry_routing.reconcile_intent(
                    shadow_extraction.entry_intent,
                    money_signal=shadow_extraction.mentions_money(),
                )
                if semantic_mode and run_shadow and shadow_error is None
                else None
            )
            entry_routing.route_turn(
                new_state,
                user_message=user_message,
                model_intent=model_intent,
                first_message=entry_routing.is_first_client_message(state),
            )
```

на

```python
        if config.entry_routing_enabled and greeting_hold:
            new_state[entry_routing.GREETING_KEY] = True
        elif config.entry_routing_enabled:
            model_intent = (
                entry_routing.reconcile_intent(
                    shadow_extraction.entry_intent,
                    money_signal=shadow_extraction.mentions_money(),
                )
                if semantic_mode and run_shadow and shadow_error is None
                else None
            )
            entry_routing.route_turn(
                new_state,
                user_message=user_message,
                model_intent=model_intent,
                # Решение открыто до первой содержательной реплики, не дольше
                # двух реплик клиента.
                first_message=entry_routing.entry_decision_open(
                    state, assistant_name=prompts.ASSISTANT_NAME
                ),
            )
```

- [ ] **Step 4: `core.turn_instruction` и `core.commit_turn`**

В `turn_instruction` сразу после

```python
    if current_plan is None:
        return prompts.OPENING_INSTRUCTION
```

добавить

```python

    # Клиент пока только поздоровался — решение о ветке отложено на ход.
    if state.get(entry_routing.GREETING_KEY):
        return prompts.GREETING_INSTRUCTION
```

В `commit_turn` заменить

```python
    entry_transition = new_state.pop(entry_routing.TRANSITION_KEY, None)
    # В ветках развилки анкета не звучит: ни предохранитель финала, ни
    # переходные фразы, ни просьба о контактах, ни счётчик попыток.
    questionnaire = entry_routing.questionnaire_active(new_state)
```

на

```python
    entry_transition = new_state.pop(entry_routing.TRANSITION_KEY, None)
    greeting = bool(new_state.pop(entry_routing.GREETING_KEY, False))
    # В ветках развилки анкета не звучит: ни предохранитель финала, ни
    # переходные фразы, ни просьба о контактах, ни счётчик попыток. На голом
    # приветствии — тоже: сначала человек скажет, с чем пришёл.
    questionnaire = entry_routing.questionnaire_active(new_state) and not greeting
```

- [ ] **Step 5: Убрать старое и поправить тесты хода**

- В `entry_routing.py` удалить функцию `is_first_client_message` (её место заняла `entry_decision_open`); убедиться поиском, что других вызовов нет.
- В `tests/test_entry_routing.py` удалить `test_first_client_message_is_detected_by_history`.
- В `tests/test_entry_routing_turn.py` реплику `"Здравствуйте"` во всех вызовах `_prepare(_fresh_state(), "Здравствуйте", ...)` заменить на `"Добрый день, у меня вопрос"` — эти тесты проверяют решение по содержательной первой реплике, а голое приветствие теперь откладывает решение. Ожидания тестов не менять.
- Если после этого падают другие тесты, чья первая реплика клиента — голое приветствие, а проверяют они не приветствие: заменить реплику на содержательную так же и перечислить такие тесты в отчёте.

- [ ] **Step 6: Проверить**

Run: `.venv313/Scripts/python -m pytest tests/test_entry_greeting.py tests/test_entry_routing.py tests/test_entry_routing_turn.py tests/test_entry_scenarios.py tests/test_entry_farewell.py -q`
Expected: all passed.
Run: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`
Expected: ruff чисто; все тесты зелёные.

- [ ] **Step 7: Коммит**

```bash
git add prompts.py core.py entry_routing.py tests/test_entry_greeting.py tests/test_entry_routing.py tests/test_entry_routing_turn.py tests/
git commit -m "На голое приветствие бот здоровается в ответ, развилка ждёт сути"
```

---

### Task 4: Роль «сначала только поздоровался»

**Files:**
- Modify: `evaluation/personas.py` — новая роль в конец `PERSONAS` (после `off_topic_then_money`)
- Modify: `tests/test_personas.py` — один тест

**Interfaces:**
- Consumes: `Persona`, `PERSONAS`, `persona_by_id`.
- Produces: роль `greeting_then_existing`.

- [ ] **Step 1: Падающий тест** — в конец `tests/test_personas.py`:

```python
def test_greeting_persona_starts_with_a_bare_greeting():
    persona = persona_by_id("greeting_then_existing")

    assert persona.expects_completion is False
    assert "«Здравствуйте»" in persona.story
    assert "как к вам обращаться" in persona.forbidden_any
```

Run: `.venv313/Scripts/python -m pytest tests/test_personas.py -q`
Expected: FAIL — `KeyError: 'неизвестная роль: greeting_then_existing'`.

- [ ] **Step 2: Роль** — в `evaluation/personas.py` перед закрывающей `]` списка `PERSONAS` (после роли `off_topic_then_money`):

```python
    # Каждый седьмой разговор начинается с голого «Здравствуйте», и настоящий
    # вопрос приходит второй репликой (решение 12.09.2026, вариант Б).
    Persona(
        id="greeting_then_existing",
        description="Сначала только поздоровался, потом спросил про заявку",
        story=(
            "Неделю назад ты оставил на сайте заявку на кредит, и тебе так и "
            "не позвонили. Первым сообщением ты пишешь только «Здравствуйте» "
            "и больше ничего, ждёшь ответа. Когда специалист ответит, "
            "спрашиваешь, что с твоей заявкой и когда позвонят. Анкету заново "
            "заполнять не хочешь. Если честно скажут, что отсюда заявку не "
            "видно, поблагодари и попрощайся."
        ),
        manner="Вежливый, немногословный.",
        forbidden_any=[
            "как к вам обращаться",
            "какая сумма",
            "оставьте номер",
            "посмотрю по заявке",
            "передам менеджеру",
        ],
        max_turns=5,
        expects_completion=False,
    ),
```

- [ ] **Step 3: Проверить и закоммитить**

Run: `.venv313/Scripts/python -m pytest tests/test_personas.py -q`, `.venv313/Scripts/python -m ruff check .`
Expected: passed; ruff чисто.

```bash
git add evaluation/personas.py tests/test_personas.py
git commit -m "Роль живого прогона: сначала только поздоровался"
```

---

### Task 5: Приёмка (исполняет контроллер)

**Files:**
- Modify: `docs/agent-memory/SESSION.md`
- Modify: `docs/superpowers/specs/2026-09-09-dialog-entry-and-order-design.md` — раздел «Решения после приёмки»

- [ ] **Step 1: Полные проверки** — `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`: чисто и зелёно.

- [ ] **Step 2: Ролевой прогон на умолчании.** Убедиться, что в локальном `.env` нет `MBK_ENTRY_ROUTING`. Поднять сервер **без** переменной: `.venv313/Scripts/python -m uvicorn main:app --host 127.0.0.1 --port 8000` (фоном), затем `.venv313/Scripts/python run_roleplay_eval.py --base-url http://127.0.0.1:8000`.
Expected:
- все прежние роли проходят, как в прогонах 11.09.2026;
- `greeting_then_existing`: первый ответ бота — приветствие и вопрос «с чем пришли» без просьбы об имени; второй — ответ ветки «уже оставлял заявку»; нарушений нет. Если роль-клиент не написала первым сообщением голое приветствие — пройти роль повторно (`--ids greeting_then_existing`), это поведение модели-клиента, а не бота.

- [ ] **Step 3: Смысловая оценка** диалогов прогона моделью по брифу `.superpowers/sdd/2026-09-10-entry-intent/task-14-judge-brief.md` (субагент читает отчёт прогона целиком). Новые дефекты, внесённые этим планом, — в отдельные правки; прежние дефекты анкеты — в список следующего блока.

- [ ] **Step 4: Документы.** В спеке после раздела «Флаги и откат» добавить раздел:

```
### Решения после приёмки (11-12.09.2026)

- Флаг `MBK_ENTRY_ROUTING` включён по умолчанию (решение человека
  12.09.2026); `0` возвращает поведение без развилки целиком.
- Лид не создаётся ни в одной ветке развилки: «не наш профиль» — тоже выход
  (финальное ревью 11.09.2026).
- Голое приветствие первой репликой откладывает решение на ход: бот
  здоровается в ответ и спрашивает, с чем человек пришёл; решение — по первой
  содержательной реплике, не дольше двух реплик (вариант Б, 12.09.2026).
- После прощания в ветке бот молчит на пустое «спасибо».
```

`docs/agent-memory/SESSION.md`: в «Сделано» — пункт о флаге по умолчанию и приветствиях с итогами прогона; из «Следующий шаг» убрать «включать ли `MBK_ENTRY_ROUTING` в бою» и вопрос про голые приветствия как решённые.

```bash
git add docs/agent-memory/SESSION.md docs/superpowers/specs/2026-09-09-dialog-entry-and-order-design.md
git commit -m "docs(memory): развилка по умолчанию и голые приветствия — итоги"
```

Push не делать — отправку решает человек.
