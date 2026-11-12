# Чипсы в приветствии — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Под первой репликой бота появляются кнопки «Получить деньги» и «Другой вопрос»: бот отдаёт их в контракте `quick_replies`, понимает нажатие, пишет его в аналитику, а виджет `mbk-site` и наш стенд их рисуют.

**Architecture:** Кнопки не хранятся, а вычисляются из состояния разговора функцией `quick_replies.quick_replies(state, enabled=...)`: пока клиент не написал ни слова — две кнопки, дальше пусто. Бот отдаёт их полем события `bubbles` и полем публичного состояния. Нажатие приходит обычным сообщением; «Другой вопрос» первой репликой держит решение развилки на ход, как голое приветствие. Виджет правится в рабочей копии `site_widget/`, в `mbk-site` уезжает позже, после утверждения человека.

**Tech Stack:** Python 3.13, FastAPI/SSE, pytest, ruff; виджет — ES5 без сборки у нас, тесты `node:test` + `jsdom`; стенд — React 18 + Vite + Tailwind.

**Spec:** `docs/superpowers/specs/2026-09-14-quick-replies-greeting-design.md` (решения человека 14.09.2026). Родительская спека — `docs/superpowers/specs/2026-09-09-dialog-entry-and-order-design.md`, компонент 4.

## Global Constraints

- Кнопки приветствия, по порядку: `{"label": "Получить деньги", "value": "Получить деньги", "choice": "money"}`, `{"label": "Другой вопрос", "value": "Другой вопрос", "choice": "other"}`. «Я уже оставлял заявку» (`choice: "existing"`) — закомментированная строка: «не показывается, пока нет ответа бизнеса по ветке существующего клиента».
- Флаг `MBK_QUICK_REPLIES`: `AppConfig.quick_replies_enabled: bool = True`, выключают только `0/false/no/off`. Чипсы действуют, только когда включены оба флага: `quick_replies_enabled` и `entry_routing_enabled`.
- Клиенту уходят только поля `label` и `value`. Непустой список — ровно когда флаги включены и в `chat_history` нет ни одной реплики с `role == "user"`.
- Событие `bubbles`: поле `quick_replies` добавляется, только если список не пуст. Публичное состояние: поле `quick_replies` всегда, пустой список — если кнопок нет или разговор закрыт.
- Нажатие — обычное сообщение с текстом `value`, тем же путём, что набранный текст. `chip_choice` сравнивает без учёта регистра, `ё/е`, лишних пробелов и знаков по краям.
- «Другой вопрос» первой репликой при включённых флагах: извлекатель не зовётся; решение о ветке открыто ещё на одну реплику; `OTHER_QUESTION_KEY = "_entry_other_question"`; инструкция `prompts.OTHER_QUESTION_INSTRUCTION`; анкетных вставок нет. Правило «не дольше двух реплик клиента» не меняется.
- Событие `quick_reply_used`, payload `{"choice": "money" | "other", "turn": <message_count>}`, без ПД; ключ хода `CHOICE_KEY = "_quick_reply_choice"`; пишется в `commit_turn` за состоявшийся ход.
- Виджет: правятся только `site_widget/_ai-chat.js` и `site_widget/_!ai-chat.scss`. Текст кнопки — только через `textContent`. Не больше четырёх кнопок. Поле ввода не блокируется никогда. Новых цветов в стилях нет — только переменные файла.
- В `mbk/mbk-site`, `mbk/mbk_ai` и в origin нашего репозитория ничего не отправлять. Боевая база — только чтение.
- Коммиты от `Zheltenkov <zheltenkovvp@gmail.com>`, без упоминания Claude и без трейлеров Co-Authored-By.
- Проверки перед завершением каждой задачи: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`; в задачах виджета — ещё `npm test` в `site_widget/`.

## Карта файлов

| Файл | Что | Задача |
|---|---|---|
| `config.py`, `quick_replies.py` (новый), `.env.example`, `deploy/prod/env.example`, `tests/conftest.py`, `tests/test_quick_replies.py` (новый), `tests/test_quick_replies_flag.py` (новый) | флаг и вычисление кнопок | 1 |
| `prompts.py`, `entry_routing.py`, `core.py`, `tests/test_entry_other_question.py` (новый) | «Другой вопрос» держит решение | 2 |
| `analytics/events.py`, `quick_replies.py`, `core.py`, `tests/test_analytics_hooks.py`, `tests/test_quick_reply_analytics.py` (новый), `docs/HANDOVER.md` | событие `quick_reply_used` | 3 |
| `main.py`, `tests/test_bubble_pacing.py`, `tests/test_public_state_quick_replies.py` (новый) | поле в SSE и публичном состоянии | 4 |
| `site_widget/_ai-chat.js`, `site_widget/package.json` (новый), `site_widget/package-lock.json` (новый), `site_widget/test/ai-chat.test.mjs` (новый) | виджет: логика и тесты | 5 |
| `site_widget/_!ai-chat.scss`, `site_widget/README.md` (новый) | виджет: стили и порядок поставки | 6 |
| `frontend/src/components/ChatView.jsx` | кнопки на нашем стенде | 7 |
| `evaluation/personas.py`, `tests/test_personas.py` | роли ролевого прогона | 8 |
| `docs/agent-memory/SESSION.md` | приёмка | 9 (контроллер) |

Нетронутые копии `site_widget/_ai-chat.js`, `site_widget/_!ai-chat.scss`, `site_widget/test/ai-chat-widget.blade.php` уже закоммичены контроллером («Копия виджета AI-чата из mbk-site без правок»); их git-хеши равны `blob_id` в `mbk-site`.

---

### Task 1: Флаг `MBK_QUICK_REPLIES` и вычисление кнопок

**Files:**
- Modify: `config.py` — поле `AppConfig.quick_replies_enabled`, функции `_flag_on_by_default` и `quick_replies_active`, разбор в `load_config`
- Create: `quick_replies.py`
- Modify: `.env.example`, `deploy/prod/env.example` — блок «Развилка на входе»
- Modify: `tests/conftest.py` — `delenv("MBK_QUICK_REPLIES")`
- Create: `tests/test_quick_replies.py`, `tests/test_quick_replies_flag.py`

**Interfaces:**
- Consumes: —
- Produces:
  - `config.AppConfig.quick_replies_enabled: bool = True`
  - `config.quick_replies_active() -> bool` — оба флага `MBK_QUICK_REPLIES` и `MBK_ENTRY_ROUTING` не выключены явно
  - `quick_replies.GREETING_CHIPS: tuple[dict[str, str], ...]` — элементы с ключами `label`, `value`, `choice`
  - `quick_replies.CHOICE_KEY = "_quick_reply_choice"`
  - `quick_replies.quick_replies(state: dict[str, Any], *, enabled: bool) -> list[dict[str, str]]`
  - `quick_replies.chip_choice(message: str) -> str | None`
  - `quick_replies.first_message_choice(state: dict[str, Any], user_message: str) -> str | None`

- [ ] **Step 1: Написать падающие тесты** — создать `tests/test_quick_replies.py`:

```python
"""Чипсы приветствия: кнопки вычисляются из разговора, нажатие узнаётся по тексту.

Спека docs/superpowers/specs/2026-09-14-quick-replies-greeting-design.md.
"""
from __future__ import annotations

import pytest

import prompts
import quick_replies as qr


GREETING = [
    {"label": "Получить деньги", "value": "Получить деньги"},
    {"label": "Другой вопрос", "value": "Другой вопрос"},
]


def _state(*client_messages: str) -> dict:
    history = [{"role": "assistant", "content": prompts.OPENING_MESSAGE}]
    for message in client_messages:
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": "…"})
    return {"chat_history": history}


def test_greeting_offers_two_chips():
    assert qr.quick_replies(_state(), enabled=True) == GREETING


def test_state_before_the_greeting_offers_the_same_chips():
    """POST /session отдаёт состояние ещё до приветствия — кнопки те же."""
    assert qr.quick_replies({}, enabled=True) == GREETING


def test_chips_disappear_after_the_first_client_message():
    assert qr.quick_replies(_state("Нужны деньги"), enabled=True) == []


def test_disabled_chips_are_empty():
    assert qr.quick_replies(_state(), enabled=False) == []


def test_client_gets_only_label_and_value():
    for chip in qr.quick_replies(_state(), enabled=True):
        assert set(chip) == {"label", "value"}


def test_existing_client_chip_is_not_offered_yet():
    """«Я уже оставлял заявку» ждёт ответа бизнеса (решение 14.09.2026)."""
    assert all("заявк" not in chip["label"].casefold() for chip in qr.GREETING_CHIPS)


@pytest.mark.parametrize(
    "message, choice",
    [
        ("Получить деньги", "money"),
        ("получить деньги", "money"),
        ("  Получить   деньги! ", "money"),
        ("Другой вопрос", "other"),
        ("другой вопрос?", "other"),
        ("«Другой вопрос»", "other"),
    ],
)
def test_chip_press_is_recognized(message, choice):
    assert qr.chip_choice(message) == choice


@pytest.mark.parametrize(
    "message",
    [
        "",
        "   ",
        "деньги",
        "Получить деньги срочно",
        "У меня другой вопрос по карте",
        "вопрос",
    ],
)
def test_other_text_is_not_a_chip_press(message):
    assert qr.chip_choice(message) is None


def test_press_counts_only_as_the_first_client_message():
    assert qr.first_message_choice(_state(), "Другой вопрос") == "other"
    assert qr.first_message_choice(_state("Здравствуйте"), "Другой вопрос") is None
```

Создать `tests/test_quick_replies_flag.py`:

```python
"""Флаг чипсов: включён, пока не выключен явно, и работает только с развилкой."""
from __future__ import annotations

from pathlib import Path

import pytest

import config
from config import AppConfig, load_config


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "sk-or-test")
    monkeypatch.delenv("MBK_QUICK_REPLIES", raising=False)


def test_quick_replies_are_on_by_default():
    assert load_config().quick_replies_enabled is True
    assert AppConfig(openrouter_api_key="sk-or-test").quick_replies_enabled is True
    assert config.quick_replies_active() is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "OFF"])
def test_quick_replies_switch_off_explicitly(monkeypatch, raw):
    monkeypatch.setenv("MBK_QUICK_REPLIES", raw)

    assert load_config().quick_replies_enabled is False
    assert config.quick_replies_active() is False


@pytest.mark.parametrize("raw", ["1", "true", "yes", ""])
def test_anything_else_keeps_them_on(monkeypatch, raw):
    monkeypatch.setenv("MBK_QUICK_REPLIES", raw)

    assert load_config().quick_replies_enabled is True


def test_chips_need_entry_routing(monkeypatch):
    """Без развилки нажатие «Другой вопрос» некому обработать."""
    monkeypatch.setenv("MBK_ENTRY_ROUTING", "0")

    assert config.quick_replies_active() is False


def test_env_templates_list_the_flag_switched_on():
    """Ветка поставки не содержит файлов нашего стенда — проверяем те, что есть."""
    for template in (ROOT / ".env.example", ROOT / "deploy" / "prod" / "env.example"):
        if template.exists():
            assert "MBK_QUICK_REPLIES=1" in template.read_text(encoding="utf-8"), template
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv313/Scripts/python -m pytest tests/test_quick_replies.py tests/test_quick_replies_flag.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'quick_replies'` и `AttributeError`/`TypeError` про `quick_replies_enabled`.

- [ ] **Step 3: Флаг в `config.py`** — в `AppConfig` сразу после поля `entry_routing_enabled: bool = True` добавить:

```python
    # Чипсы в приветствии (docs/superpowers/specs/2026-09-14-quick-replies-
    # greeting-design.md): кнопки «Получить деньги» и «Другой вопрос» под
    # первой репликой бота. Включено по умолчанию, как развилка, и работает
    # только вместе с ней. Выключение — MBK_QUICK_REPLIES=0.
    quick_replies_enabled: bool = True
```

После функции `_read_env_value` добавить:

```python
def _flag_on_by_default(name: str) -> bool:
    """Флаг, который выключает только явное значение: забытая переменная — «включено»."""
    return (_read_env_value(name) or "1").lower() not in {"0", "false", "no", "off"}


def quick_replies_active() -> bool:
    """Показывать ли чипсы: нужны оба флага — чипсов и развилки на входе.

    Без развилки нажатие «Другой вопрос» некому обработать. Функция нужна
    `main._public_state`, где полного конфига нет; `load_config` разбирает те
    же переменные тем же `_flag_on_by_default`.
    """
    return _flag_on_by_default("MBK_QUICK_REPLIES") and _flag_on_by_default(
        "MBK_ENTRY_ROUTING"
    )
```

В `load_config` заменить

```python
        entry_routing_enabled=(_read_env_value("MBK_ENTRY_ROUTING") or "1").lower()
        not in {"0", "false", "no", "off"},
    )
```

на

```python
        entry_routing_enabled=_flag_on_by_default("MBK_ENTRY_ROUTING"),
        quick_replies_enabled=_flag_on_by_default("MBK_QUICK_REPLIES"),
    )
```

(Комментарий «Выключается только явным значением…» над `entry_routing_enabled` оставить.)

- [ ] **Step 4: Модуль `quick_replies.py`** — создать в корне репозитория:

```python
"""Чипсы — быстрые ответы под репликой бота.

Спека: docs/superpowers/specs/2026-09-14-quick-replies-greeting-design.md.
Сейчас кнопки есть только у приветствия: пока клиент не написал ни слова, под
первой репликой бота — «Получить деньги» и «Другой вопрос». 137 человек
открыли чат и ушли молча; кнопка даёт им действие вместо пустого поля ввода.

Кнопки не хранятся в состоянии, а вычисляются из него: после любой реплики
клиента список сам становится пустым, и устаревшей кнопки не бывает. Нажатие
приходит обычным сообщением с текстом кнопки — тем же путём, что набранный
текст.
"""
from __future__ import annotations

from typing import Any


# Кнопки приветствия, по порядку показа. `choice` — код для аналитики и для
# разбора нажатия; клиенту уходят только `label` и `value`.
GREETING_CHIPS: tuple[dict[str, str], ...] = (
    {"label": "Получить деньги", "value": "Получить деньги", "choice": "money"},
    {"label": "Другой вопрос", "value": "Другой вопрос", "choice": "other"},
    # Не показывается, пока нет ответа бизнеса по ветке существующего клиента
    # (решение человека 14.09.2026): что такому человеку отвечать и куда
    # передавать обращение, ещё не решено.
    # {"label": "Я уже оставлял заявку", "value": "Я уже оставлял заявку", "choice": "existing"},
)

# Временный ключ хода (подчёркивание — в базу не уезжает): какую кнопку нажал
# клиент. `core.prepare_turn` кладёт, `core.commit_turn` забирает и пишет
# событие — только за ход, который действительно состоялся.
CHOICE_KEY = "_quick_reply_choice"

_EDGE_PUNCTUATION = " .,!?;:«»\"'()-—…"


def _normalize(text: str) -> str:
    collapsed = " ".join(text.casefold().replace("ё", "е").split())
    return collapsed.strip(_EDGE_PUNCTUATION)


def _client_has_spoken(state: dict[str, Any]) -> bool:
    history = state.get("chat_history") or []
    return any(
        isinstance(item, dict) and item.get("role") == "user" for item in history
    )


def quick_replies(state: dict[str, Any], *, enabled: bool) -> list[dict[str, str]]:
    """Кнопки под последней репликой бота: `label` и `value`, по порядку.

    Пусто, если чипсы выключены или клиент уже что-то написал.
    """
    if not enabled or _client_has_spoken(state):
        return []
    return [
        {"label": chip["label"], "value": chip["value"]} for chip in GREETING_CHIPS
    ]


def chip_choice(message: str) -> str | None:
    """Какую кнопку означает реплика: `choice` кнопки или None.

    Регистр, «ё», лишние пробелы и знаки по краям не важны: «другой вопрос!»
    — это «Другой вопрос». Любое другое слово — уже не нажатие.
    """
    normalized = _normalize(message or "")
    if not normalized:
        return None
    for chip in GREETING_CHIPS:
        if _normalize(chip["value"]) == normalized:
            return chip["choice"]
    return None


def first_message_choice(state: dict[str, Any], user_message: str) -> str | None:
    """Нажатие кнопки первой репликой клиента. `state` — состояние до хода."""
    if _client_has_spoken(state):
        return None
    return chip_choice(user_message)
```

- [ ] **Step 5: Шаблоны и тестовая изоляция**

В `.env.example` и в `deploy/prod/env.example` сразу после строки `MBK_ENTRY_ROUTING=1` вставить (пустая строка перед комментарием):

```text

# Чипсы в приветствии: кнопки «Получить деньги» и «Другой вопрос» под первой
# репликой бота. Работают только вместе с развилкой. Включено по умолчанию;
# 0 — без кнопок.
MBK_QUICK_REPLIES=1
```

В `tests/conftest.py` сразу после строки `monkeypatch.delenv("MBK_ENTRY_ROUTING", raising=False)` добавить:

```python
    # Чипсы — так же: действует умолчание кода (включены).
    monkeypatch.delenv("MBK_QUICK_REPLIES", raising=False)
```

- [ ] **Step 6: Прогнать тесты**

Run: `.venv313/Scripts/python -m pytest tests/test_quick_replies.py tests/test_quick_replies_flag.py tests/test_entry_routing_flag.py -q`
Expected: PASS.

Run: `.venv313/Scripts/python -m pytest -q` и `.venv313/Scripts/python -m ruff check .`
Expected: всё зелёное, `All checks passed!`.

- [ ] **Step 7: Commit**

```bash
git add config.py quick_replies.py .env.example deploy/prod/env.example tests/conftest.py tests/test_quick_replies.py tests/test_quick_replies_flag.py
git commit -m "Чипсы приветствия: флаг MBK_QUICK_REPLIES и вычисление кнопок"
```

---

### Task 2: «Другой вопрос» держит решение развилки

**Files:**
- Modify: `prompts.py` — после `GREETING_INSTRUCTION`
- Modify: `entry_routing.py` — импорт, `OTHER_QUESTION_KEY`, `entry_decision_open`, `holds_for_other_question`
- Modify: `core.py` — `prepare_turn` (блок `greeting_hold`, строки ~235–397), `turn_instruction` (~889), `commit_turn` (~1015–1019)
- Create: `tests/test_entry_other_question.py`

**Interfaces:**
- Consumes: `quick_replies.chip_choice(message) -> str | None`; `AppConfig.quick_replies_enabled` (Task 1).
- Produces:
  - `prompts.OTHER_QUESTION_INSTRUCTION: str`
  - `entry_routing.OTHER_QUESTION_KEY = "_entry_other_question"`
  - `entry_routing.holds_for_other_question(state: dict[str, Any], user_message: str) -> bool`
  - `entry_routing.entry_decision_open(state, *, assistant_name: str | None = None, chips_enabled: bool = False) -> bool`

- [ ] **Step 1: Написать падающие тесты** — создать `tests/test_entry_other_question.py`:

```python
"""Кнопка «Другой вопрос»: бот просит рассказать вопрос, решение о ветке ждёт.

Спека docs/superpowers/specs/2026-09-14-quick-replies-greeting-design.md.
Человек нажал кнопку под приветствием, но вопроса ещё не задал: начать анкету
значило бы его не услышать, а решать ветку пока не по чему.
"""
from __future__ import annotations

import pytest

import analytics
import core
import entry_routing as er
import prompts
from analytics import AnalyticsRecorder, MemorySink
from analytics import EVENTS as E
from config import AppConfig
from state import init_state


LEGACY = AppConfig(openrouter_api_key="sk-or-test", fact_interpretation_mode="legacy")


def _history(*client_messages: str) -> dict:
    history = [{"role": "assistant", "content": prompts.OPENING_MESSAGE}]
    for message in client_messages:
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": "…"})
    return {"chat_history": history}


def test_other_question_holds_only_as_the_first_message():
    assert er.holds_for_other_question(_history(), "Другой вопрос") is True
    assert er.holds_for_other_question(_history(), "другой вопрос!") is True
    assert er.holds_for_other_question(_history("Здравствуйте"), "Другой вопрос") is False
    assert er.holds_for_other_question(_history(), "Получить деньги") is False


def test_decision_stays_open_after_the_chip_only_with_chips_on():
    assert er.entry_decision_open(_history("Другой вопрос"), chips_enabled=True) is True
    assert er.entry_decision_open(_history("Другой вопрос"), chips_enabled=False) is False
    assert er.entry_decision_open(_history("Другой вопрос")) is False


def test_decision_closes_after_two_client_messages_even_after_the_chip():
    state = _history("Другой вопрос", "Какой курс доллара?")

    assert er.entry_decision_open(state, chips_enabled=True) is False


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
    state["session_id"] = "entry-other-question"
    state["chat_history"] = [{"role": "assistant", "content": prompts.OPENING_MESSAGE}]
    return state


def _turn(state, message, reply, config=LEGACY):
    prepared = core.prepare_turn(state, message, config, submit_delivery=False)
    return prepared, core.commit_turn(prepared, message, reply)


def test_chip_gets_a_request_for_the_question_without_the_questionnaire(offline, sink):
    prepared, committed = _turn(
        _fresh_state(), "Другой вопрос", "Расскажите, какой у вас вопрос?"
    )

    assert core.turn_instruction(prepared) == prompts.OTHER_QUESTION_INSTRUCTION
    assert committed["messages"] == ["Расскажите, какой у вас вопрос?"]
    state = committed["current_state"]
    assert "entry" not in state
    assert er.OTHER_QUESTION_KEY not in state
    script = state.get("script") or {}
    assert not script.get("attempts")
    assert not script.get("asked")
    names = [event.event for event in sink.events]
    assert E.TURN_COMPLETED not in names
    assert E.ENTRY_ROUTED not in names


def test_chip_does_not_call_the_extractor(offline, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("на «Другой вопрос» извлекатель не зовут")

    monkeypatch.setattr(core.llm_agent, "extract_facts", unexpected)
    monkeypatch.setattr(core.llm_agent, "extract_fact_candidates", unexpected)

    prepared = core.prepare_turn(
        _fresh_state(),
        "Другой вопрос",
        AppConfig(openrouter_api_key="sk-or-test", fact_interpretation_mode="semantic"),
        submit_delivery=False,
    )

    assert prepared[er.OTHER_QUESTION_KEY] is True


def test_question_after_the_chip_is_routed(offline, sink):
    _p, first = _turn(_fresh_state(), "Другой вопрос", "Какой у вас вопрос?")

    _p, second = _turn(
        first["current_state"],
        "Оставлял заявку, когда позвонит менеджер?",
        "Отсюда заявку не видно.",
    )

    assert second["current_state"]["entry"]["scenario"] == er.EXISTING
    assert second["messages"] == ["Отсюда заявку не видно."]


def test_money_after_the_chip_starts_the_questionnaire(offline, sink):
    _p, first = _turn(_fresh_state(), "Другой вопрос", "Какой у вас вопрос?")

    _p, second = _turn(first["current_state"], "Нужны деньги", "Сколько нужно?")

    assert second["current_state"]["entry"]["scenario"] == er.QUESTIONNAIRE
    assert "обращаться" in " ".join(second["messages"])


def test_chip_hold_needs_quick_replies(offline):
    off = AppConfig(
        openrouter_api_key="sk-or-test",
        fact_interpretation_mode="legacy",
        quick_replies_enabled=False,
    )

    prepared, committed = _turn(_fresh_state(), "Другой вопрос", "Слушаю вас.", config=off)

    assert er.OTHER_QUESTION_KEY not in prepared
    assert committed["current_state"]["entry"]["scenario"] == er.QUESTIONNAIRE


def test_chip_hold_needs_entry_routing(offline):
    off = AppConfig(
        openrouter_api_key="sk-or-test",
        fact_interpretation_mode="legacy",
        entry_routing_enabled=False,
    )

    prepared, _committed = _turn(_fresh_state(), "Другой вопрос", "Слушаю вас.", config=off)

    assert er.OTHER_QUESTION_KEY not in prepared
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv313/Scripts/python -m pytest tests/test_entry_other_question.py -q`
Expected: FAIL — `AttributeError: module 'entry_routing' has no attribute 'holds_for_other_question'`, `TypeError` про `chips_enabled`.

- [ ] **Step 3: Инструкция в `prompts.py`** — сразу после определения `GREETING_INSTRUCTION` (закрывающая скобка и пустые строки) вставить:

```python
# Клиент нажал кнопку «Другой вопрос» под приветствием (спека чипсов
# 14.09.2026). Вопроса он ещё не задал, и начинать анкету рано: сначала
# человек скажет, с чем пришёл.
OTHER_QUESTION_INSTRUCTION = (
    "Клиент нажал кнопку «Другой вопрос» и пока не сказал, какой. Одной "
    "короткой живой фразой попроси рассказать, какой у него вопрос. Не "
    "спрашивай имя, телефон и данные анкеты, не перечисляй услуги и ничего не "
    "обещай."
)
```

- [ ] **Step 4: `entry_routing.py`**

После строки `from routing.entry_intent import EntryIntent, classify_by_dictionary` добавить:

```python
from quick_replies import chip_choice
```

После определения `GREETING_KEY = "_entry_greeting"` добавить:

```python

# Первая реплика клиента — нажатая кнопка «Другой вопрос» (спека чипсов
# 14.09.2026). Вопроса ещё нет, и решение о ветке ждёт следующей реплики, как
# после голого приветствия. Живёт один ход, как GREETING_KEY.
OTHER_QUESTION_KEY = "_entry_other_question"
```

Заменить функцию `entry_decision_open` целиком на:

```python
def entry_decision_open(
    state: dict[str, Any],
    *,
    assistant_name: str | None = None,
    chips_enabled: bool = False,
) -> bool:
    """Решение о ветке ещё не принято: клиент пока не сказал ничего по существу.

    Открыто на первой реплике клиента и на второй — если первая была голым
    приветствием или, при включённых чипсах, нажатой кнопкой «Другой вопрос».
    Дольше не держим: две реплики без сути — анкета, как сегодня (решение
    человека 12.09.2026, вариант Б).
    """
    previous = _client_messages(state)
    if not previous:
        return True
    if len(previous) != 1:
        return False
    first = previous[0]
    if is_bare_greeting(first, assistant_name=assistant_name):
        return True
    # Без чипсов «другой вопрос» — обычный текст, и решение по нему уже принято.
    return chips_enabled and chip_choice(first) == "other"
```

После функции `holds_for_greeting` добавить:

```python
def holds_for_other_question(state: dict[str, Any], user_message: str) -> bool:
    """Первая реплика клиента — кнопка «Другой вопрос»: решение ждёт вопроса.

    Флаги чипсов и развилки проверяет вызывающий (`core.prepare_turn`).
    `state` — состояние до хода.
    """
    return not _client_messages(state) and chip_choice(user_message) == "other"
```

- [ ] **Step 5: `core.py`, `prepare_turn`** — заменить

```python
        greeting_hold = config.entry_routing_enabled and entry_routing.holds_for_greeting(
            state, user_message, assistant_name=prompts.ASSISTANT_NAME
        )
        if expected_updates is not None or strict_local_name or greeting_hold:
```

на

```python
        greeting_hold = config.entry_routing_enabled and entry_routing.holds_for_greeting(
            state, user_message, assistant_name=prompts.ASSISTANT_NAME
        )
        # Нажатая кнопка «Другой вопрос» — вопроса ещё нет, фактов тоже:
        # решение ждёт следующей реплики (спека чипсов 14.09.2026).
        other_question_hold = (
            config.entry_routing_enabled
            and config.quick_replies_enabled
            and entry_routing.holds_for_other_question(state, user_message)
        )
        if (
            expected_updates is not None
            or strict_local_name
            or greeting_hold
            or other_question_hold
        ):
```

Там же ниже заменить

```python
        if config.entry_routing_enabled and greeting_hold:
            new_state[entry_routing.GREETING_KEY] = True
        elif config.entry_routing_enabled:
```

на

```python
        if config.entry_routing_enabled and greeting_hold:
            new_state[entry_routing.GREETING_KEY] = True
        elif other_question_hold:
            new_state[entry_routing.OTHER_QUESTION_KEY] = True
        elif config.entry_routing_enabled:
```

и в вызове `entry_routing.entry_decision_open(...)` внутри `route_turn(...)` заменить

```python
                first_message=entry_routing.entry_decision_open(
                    state, assistant_name=prompts.ASSISTANT_NAME
                ),
```

на

```python
                first_message=entry_routing.entry_decision_open(
                    state,
                    assistant_name=prompts.ASSISTANT_NAME,
                    chips_enabled=config.quick_replies_enabled,
                ),
```

- [ ] **Step 6: `core.py`, `turn_instruction` и `commit_turn`**

В `turn_instruction` сразу после

```python
    if state.get(entry_routing.GREETING_KEY):
        return prompts.GREETING_INSTRUCTION
```

добавить

```python
    if state.get(entry_routing.OTHER_QUESTION_KEY):
        return prompts.OTHER_QUESTION_INSTRUCTION
```

В `commit_turn` заменить

```python
    greeting = bool(new_state.pop(entry_routing.GREETING_KEY, False))
    # В ветках развилки анкета не звучит: ни предохранитель финала, ни
    # переходные фразы, ни просьба о контактах, ни счётчик попыток. На голом
    # приветствии — тоже: сначала человек скажет, с чем пришёл.
    questionnaire = entry_routing.questionnaire_active(new_state) and not greeting
```

на

```python
    greeting = bool(new_state.pop(entry_routing.GREETING_KEY, False))
    other_question = bool(new_state.pop(entry_routing.OTHER_QUESTION_KEY, False))
    # В ветках развилки анкета не звучит: ни предохранитель финала, ни
    # переходные фразы, ни просьба о контактах, ни счётчик попыток. На голом
    # приветствии и на кнопке «Другой вопрос» — тоже: сначала человек скажет,
    # с чем пришёл.
    questionnaire = entry_routing.questionnaire_active(new_state) and not (
        greeting or other_question
    )
```

- [ ] **Step 7: Прогнать тесты**

Run: `.venv313/Scripts/python -m pytest tests/test_entry_other_question.py tests/test_entry_greeting.py tests/test_entry_routing.py tests/test_entry_routing_turn.py -q`
Expected: PASS.

Run: `.venv313/Scripts/python -m pytest -q` и `.venv313/Scripts/python -m ruff check .`
Expected: всё зелёное.

- [ ] **Step 8: Commit**

```bash
git add prompts.py entry_routing.py core.py tests/test_entry_other_question.py
git commit -m "Кнопка «Другой вопрос» держит решение развилки до вопроса"
```

---

### Task 3: Событие `quick_reply_used`

**Files:**
- Modify: `analytics/events.py` — константа в классе событий после `EXISTING_CLIENT_REQUEST`
- Modify: `quick_replies.py` — импорт `analytics`, функция `record_choice`
- Modify: `core.py` — `prepare_turn` (после блока развилки), `commit_turn` (забрать ключ, записать событие)
- Modify: `tests/test_analytics_hooks.py` — каталог событий
- Create: `tests/test_quick_reply_analytics.py`
- Modify: `docs/HANDOVER.md` — абзац после «События развилки на входе»

**Interfaces:**
- Consumes: `quick_replies.first_message_choice`, `quick_replies.CHOICE_KEY` (Task 1); `AppConfig.quick_replies_enabled`.
- Produces:
  - `analytics.EVENTS.QUICK_REPLY_USED == "quick_reply_used"`
  - `quick_replies.record_choice(state: dict[str, Any], choice: str | None) -> None`

- [ ] **Step 1: Написать падающие тесты** — создать `tests/test_quick_reply_analytics.py`:

```python
"""Нажатие кнопки приветствия пишется в аналитику отдельным событием.

По нему считается, сколько раньше молчавших посетителей начали разговор
кнопкой (спека docs/superpowers/specs/2026-09-14-quick-replies-greeting-design.md).
"""
from __future__ import annotations

import pytest

import analytics
import core
import prompts
import quick_replies
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
    state["session_id"] = "quick-reply-analytics"
    state["chat_history"] = [{"role": "assistant", "content": prompts.OPENING_MESSAGE}]
    return state


def _turn(state, message, reply, config=LEGACY):
    prepared = core.prepare_turn(state, message, config, submit_delivery=False)
    return prepared, core.commit_turn(prepared, message, reply)


def _chip_payloads(sink) -> list[dict]:
    return [event.payload for event in sink.events if event.event == E.QUICK_REPLY_USED]


def test_money_chip_is_recorded(offline, sink):
    _p, committed = _turn(_fresh_state(), "Получить деньги", "Как к вам обращаться?")

    assert _chip_payloads(sink) == [{"choice": "money", "turn": 1}]
    assert quick_replies.CHOICE_KEY not in committed["current_state"]


def test_other_question_chip_is_recorded(offline, sink):
    _turn(_fresh_state(), "Другой вопрос", "Какой у вас вопрос?")

    assert _chip_payloads(sink) == [{"choice": "other", "turn": 1}]


def test_typed_text_is_not_a_chip(offline, sink):
    _turn(_fresh_state(), "Нужны деньги", "Как к вам обращаться?")

    assert _chip_payloads(sink) == []


def test_chip_text_later_in_the_dialog_is_not_counted(offline, sink):
    _p, first = _turn(_fresh_state(), "Здравствуйте", "Здравствуйте! С чем пришли?")

    _turn(first["current_state"], "Другой вопрос", "Слушаю вас.")

    assert _chip_payloads(sink) == []


def test_nothing_is_recorded_with_chips_off(offline, sink):
    off = AppConfig(
        openrouter_api_key="sk-or-test",
        fact_interpretation_mode="legacy",
        quick_replies_enabled=False,
    )

    _turn(_fresh_state(), "Получить деньги", "Как к вам обращаться?", config=off)

    assert _chip_payloads(sink) == []
```

В `tests/test_analytics_hooks.py`, функция `test_event_catalogue_covers_only_what_we_own`: в конец докстроки (перед закрывающими `"""`) дописать абзац

```text

    `QUICK_REPLY_USED` появилось 14.09.2026 вместе с чипсами в приветствии:
    нажатие кнопки происходит в нашем виджете, CRM о нём не знает.
```

и после строки `assert EVENTS.EXISTING_CLIENT_REQUEST == "existing_client_request"` добавить

```python
    assert EVENTS.QUICK_REPLY_USED == "quick_reply_used"
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv313/Scripts/python -m pytest tests/test_quick_reply_analytics.py tests/test_analytics_hooks.py -q`
Expected: FAIL — `AttributeError: ... has no attribute 'QUICK_REPLY_USED'`.

- [ ] **Step 3: Константа события** — в `analytics/events.py` сразу после строки `EXISTING_CLIENT_REQUEST = "existing_client_request"` добавить:

```python
    # Чипсы в приветствии (спека 14.09.2026): клиент первой репликой нажал
    # кнопку. `payload->>'choice'` — `money` («Получить деньги») или `other`
    # («Другой вопрос»). По нему считается, сколько раньше молчавших
    # посетителей начали разговор кнопкой.
    QUICK_REPLY_USED = "quick_reply_used"
```

- [ ] **Step 4: `quick_replies.record_choice`** — в `quick_replies.py` после строки `from typing import Any` добавить пустую строку и `import analytics`; в конец файла добавить:

```python
def record_choice(state: dict[str, Any], choice: str | None) -> None:
    """Пишет нажатие кнопки в аналитику. Никогда не влияет на ответ клиенту."""
    if not choice:
        return
    session_id = str(state.get("session_id") or "")
    if not session_id:
        return
    analytics.get_recorder().emit(
        analytics.EVENTS.QUICK_REPLY_USED,
        session_id,
        {"choice": choice, "turn": state.get("message_count")},
    )
```

- [ ] **Step 5: `core.py`**

Добавить `import quick_replies` рядом с `import entry_routing` (в том же блоке импортов, по алфавиту).

В `prepare_turn` найти конец блока развилки — вызов `entry_routing.route_turn(...)` с аргументом `first_message=entry_routing.entry_decision_open(...)` и закрывающей `)` — и сразу после этого блока, на том же уровне отступа, что `if config.entry_routing_enabled and greeting_hold:` (внутри `if user_message:`), добавить:

```python
        # Нажатие кнопки приветствия — для аналитики. Событие пишет
        # `commit_turn`, только за состоявшийся ход (спека чипсов 14.09.2026).
        if config.entry_routing_enabled and config.quick_replies_enabled:
            choice = quick_replies.first_message_choice(state, user_message)
            if choice:
                new_state[quick_replies.CHOICE_KEY] = choice
```

В `commit_turn` сразу после строки `other_question = bool(new_state.pop(entry_routing.OTHER_QUESTION_KEY, False))` добавить:

```python
    quick_reply_choice = new_state.pop(quick_replies.CHOICE_KEY, None)
```

и сразу после строки `entry_routing.record_transition(new_state, entry_transition)` добавить:

```python
    quick_replies.record_choice(new_state, quick_reply_choice)
```

- [ ] **Step 6: HANDOVER** — в `docs/HANDOVER.md` в абзаце, начинающемся с `**События развилки на входе**`, заменить `(голое «Здравствуйте» откладывает решение на ход)` на `(голое «Здравствуйте» и кнопка «Другой вопрос» откладывают решение на ход)`. Сразу после этого абзаца (перед следующим заголовком или абзацем) вставить:

~~~markdown
**Чипсы в приветствии** (пишутся, пока включены чипсы и развилка: по умолчанию
да, выключает `MBK_QUICK_REPLIES=0`). `quick_reply_used` — клиент первой
репликой нажал кнопку под приветствием: `payload->>'choice'` равно `money`
(«Получить деньги») или `other` («Другой вопрос»). Набранный руками точный
текст кнопки засчитывается так же. Эффект — доля разговоров, где клиент
написал хоть что-то, среди открытых чатов, по дням, до и после выкатки
виджета:

```sql
SELECT date_trunc('day', s.occurred_at) AS day,
       count(*) AS opened,
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM analytics_events t
         WHERE t.session_id = s.session_id
           AND t.event IN ('turn_completed', 'entry_routed', 'quick_reply_used')
       )) AS started,
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM analytics_events q
         WHERE q.session_id = s.session_id AND q.event = 'quick_reply_used'
       )) AS by_chip
FROM analytics_events s
WHERE s.event = 'chat_started'
GROUP BY 1
ORDER BY 1;
```

Разговор, где человек только поздоровался и ушёл, в `started` не попадает:
событий на голом приветствии нет.
~~~

- [ ] **Step 7: Прогнать тесты**

Run: `.venv313/Scripts/python -m pytest tests/test_quick_reply_analytics.py tests/test_analytics_hooks.py tests/test_entry_other_question.py tests/test_entry_analytics.py -q`
Expected: PASS.

Run: `.venv313/Scripts/python -m pytest -q` и `.venv313/Scripts/python -m ruff check .`
Expected: всё зелёное.

- [ ] **Step 8: Commit**

```bash
git add analytics/events.py quick_replies.py core.py tests/test_analytics_hooks.py tests/test_quick_reply_analytics.py docs/HANDOVER.md
git commit -m "Нажатие кнопки приветствия пишется событием quick_reply_used"
```

---

### Task 4: Поле `quick_replies` в SSE и публичном состоянии

**Files:**
- Modify: `main.py` — импорты, докстрока модуля (строка про `event: bubbles`), `_paced_bubbles`, вызов после `commit_turn` в `_run_turn_sse_unlocked`, `_public_state`
- Modify: `tests/test_bubble_pacing.py`
- Create: `tests/test_public_state_quick_replies.py`

**Interfaces:**
- Consumes: `quick_replies.quick_replies(state, *, enabled)`, `config.quick_replies_active()`, `AppConfig.quick_replies_enabled`, `AppConfig.entry_routing_enabled` (Task 1).
- Produces:
  - `main._paced_bubbles(bubbles: list[str], chips: list[dict[str, str]] | None = None)` — async-генератор одного события `bubbles`
  - публичное состояние `main._public_state(session)["quick_replies"]: list[dict[str, str]]`

- [ ] **Step 1: Написать падающие тесты**

В конец `tests/test_bubble_pacing.py` добавить:

```python
def _payloads(bubbles, chips=None) -> list[dict]:
    async def run():
        return [
            json.loads(event["data"])
            async for event in main._paced_bubbles(bubbles, chips)
        ]

    return asyncio.run(run())


def test_chips_ride_the_same_event():
    chips = [{"label": "Получить деньги", "value": "Получить деньги"}]

    assert _payloads(["Добрый день!"], chips) == [
        {"bubbles": ["Добрый день!"], "quick_replies": chips}
    ]


def test_no_chips_means_no_field():
    """Старый виджет и любой ход после реплики клиента видят прежний контракт."""
    assert _payloads(["Слушаю."], []) == [{"bubbles": ["Слушаю."]}]
    assert _payloads(["Слушаю."]) == [{"bubbles": ["Слушаю."]}]
```

Создать `tests/test_public_state_quick_replies.py`:

```python
"""Публичное состояние несёт кнопки: по нему виджет восстанавливает их после перезагрузки."""
from __future__ import annotations

import main
from prompts import OPENING_MESSAGE
from session_store import Session
from state import init_state
from usage_tracker import SessionUsage


def _session(*client_messages: str) -> Session:
    state = init_state()
    state["chat_history"] = [{"role": "assistant", "content": OPENING_MESSAGE}]
    for message in client_messages:
        state["chat_history"].append({"role": "user", "content": message})
    return Session(
        id="chips-test",
        state=state,
        anketa=None,
        usage=SessionUsage(session_id="chips-test"),
    )


def test_greeting_state_carries_the_chips():
    chips = main._public_state(_session())["quick_replies"]

    assert [chip["value"] for chip in chips] == ["Получить деньги", "Другой вопрос"]


def test_chips_are_gone_after_the_client_spoke():
    assert main._public_state(_session("Нужны деньги"))["quick_replies"] == []


def test_flag_off_empties_the_chips(monkeypatch):
    monkeypatch.setenv("MBK_QUICK_REPLIES", "0")

    assert main._public_state(_session())["quick_replies"] == []


def test_entry_routing_off_empties_the_chips(monkeypatch):
    monkeypatch.setenv("MBK_ENTRY_ROUTING", "0")

    assert main._public_state(_session())["quick_replies"] == []


def test_closed_dialog_has_no_chips(monkeypatch):
    monkeypatch.setattr(main, "should_close_dialog", lambda state: True)

    assert main._public_state(_session())["quick_replies"] == []
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv313/Scripts/python -m pytest tests/test_bubble_pacing.py tests/test_public_state_quick_replies.py -q`
Expected: FAIL — `TypeError: _paced_bubbles() takes 1 positional argument but 2 were given` и `KeyError: 'quick_replies'`.

- [ ] **Step 3: Импорты и докстрока** — в `main.py`:
- после `import delivery` добавить `import quick_replies`;
- заменить `from config import load_config` на `from config import load_config, quick_replies_active`;
- в докстроке модуля заменить строку

```text
  event: bubbles  — { "bubbles": ["...", "..."] }     после стрима — разбивка на пузыри
```

на

```text
  event: bubbles  — { "bubbles": ["...", "..."], "quick_replies": [{label, value}] }
                    после стрима — разбивка на пузыри; кнопки — только если есть
```

- [ ] **Step 4: `_paced_bubbles`** — заменить функцию целиком на:

```python
async def _paced_bubbles(
    bubbles: list[str],
    chips: list[dict[str, str]] | None = None,
):
    """Фиксирует уже показанный stream одним атомарным событием.

    Клиент видит живой набор по событиям ``chunk``. Повторно разыгрывать те же
    пузыри с искусственными паузами после окончания stream нельзя: первый
    финальный пузырь очищал весь накопленный текст, остальные исчезали на
    несколько секунд и создавали ощущение тормозящего бота.

    Кнопки, если есть, едут тем же событием: виджет рисует их под пузырями
    (спека чипсов 14.09.2026). Пустой список поля не добавляет — старый виджет
    разницы не видит.
    """
    data: dict[str, Any] = {"bubbles": bubbles}
    if chips:
        data["quick_replies"] = chips
    yield {
        "event": "bubbles",
        "data": json.dumps(data, ensure_ascii=False),
    }
```

- [ ] **Step 5: Кнопки на ходе** — в `_run_turn_sse_unlocked` заменить

```python
    async for event in _paced_bubbles(committed["messages"]):
        yield event
```

на

```python
    chips = quick_replies.quick_replies(
        session.state,
        enabled=cfg.quick_replies_enabled and cfg.entry_routing_enabled,
    )
    async for event in _paced_bubbles(committed["messages"], chips):
        yield event
```

- [ ] **Step 6: `_public_state`** — в словаре, который возвращает функция, сразу после пары `"chat_history": s.get("chat_history", []),` добавить:

```python
        # Кнопки под последней репликой бота (спека чипсов 14.09.2026). Виджет
        # берёт их отсюда, когда восстанавливает разговор после перезагрузки
        # страницы. Пустой список — кнопок нет.
        "quick_replies": (
            []
            if should_close_dialog(s)
            else quick_replies.quick_replies(s, enabled=quick_replies_active())
        ),
```

- [ ] **Step 7: Прогнать тесты**

Run: `.venv313/Scripts/python -m pytest tests/test_bubble_pacing.py tests/test_public_state_quick_replies.py tests/test_session_opening.py -q`
Expected: PASS.

Run: `.venv313/Scripts/python -m pytest -q` и `.venv313/Scripts/python -m ruff check .`
Expected: всё зелёное.

- [ ] **Step 8: Commit**

```bash
git add main.py tests/test_bubble_pacing.py tests/test_public_state_quick_replies.py
git commit -m "Кнопки приветствия в событии bubbles и в публичном состоянии"
```

---

### Task 5: Виджет — логика чипсов и тесты

**Files:**
- Create: `site_widget/package.json`, `site_widget/test/ai-chat.test.mjs`
- Create (генерирует `npm install`): `site_widget/package-lock.json`
- Modify: `site_widget/_ai-chat.js`

**Interfaces:**
- Consumes: контракт бота (Tasks 1, 4): событие `bubbles` — `{"bubbles": [...], "quick_replies": [{"label", "value"}]}` (поле только при непустом списке); `state.quick_replies` — всегда список.
- Produces: в `_ai-chat.js` функции `clearQuickReplies()`, `renderQuickReplies(items)`, `sendText(raw) -> boolean`; разметка ряда `div.mbkc-chips > button.mbkc-chip[data-value]` (Task 6 стилизует эти классы).

- [ ] **Step 1: Пакет тестов** — создать `site_widget/package.json`:

```json
{
  "name": "mbk-site-ai-chat-widget",
  "private": true,
  "description": "Рабочая копия виджета AI-чата из mbk/mbk-site с тестами",
  "type": "module",
  "scripts": {
    "test": "node --test \"test/*.test.mjs\""
  },
  "devDependencies": {
    "jsdom": "26.1.0"
  }
}
```

Run: `cd site_widget && npm install`
Expected: создан `site_widget/package-lock.json`, `node_modules/` (игнорируется `.gitignore`), без ошибок.

- [ ] **Step 2: Написать падающие тесты** — создать `site_widget/test/ai-chat.test.mjs`:

```js
// Тесты чипсов виджета AI-чата: разметка — копия blade-шаблона mbk-site,
// сеть и поток ответа подменены. Спека:
// docs/superpowers/specs/2026-09-14-quick-replies-greeting-design.md.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

const here = dirname(fileURLToPath(import.meta.url));
const WIDGET = readFileSync(join(here, '..', '_ai-chat.js'), 'utf8');
// Комментарии Blade вырезаются, остальное — обычный HTML.
const MARKUP = readFileSync(join(here, 'ai-chat-widget.blade.php'), 'utf8')
  .replace(/\{\{--[\s\S]*?--\}\}/g, '');

const GREETING = [
  { label: 'Получить деньги', value: 'Получить деньги' },
  { label: 'Другой вопрос', value: 'Другой вопрос' },
];

function flush() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

async function mountWidget({ storedSid = null, restoredState = null } = {}) {
  const dom = new JSDOM(`<!doctype html><html><body>${MARKUP}</body></html>`, {
    url: 'https://www.mbk.ru/',
    runScripts: 'outside-only',
  });
  const { window } = dom;
  const streams = [];

  class FakeEventSource {
    constructor(url) {
      this.url = url;
      this.listeners = {};
      this.closed = false;
      streams.push(this);
    }

    addEventListener(name, handler) {
      (this.listeners[name] ||= []).push(handler);
    }

    close() {
      this.closed = true;
    }

    emit(name, data) {
      for (const handler of this.listeners[name] || []) {
        handler({ data: JSON.stringify(data) });
      }
    }
  }

  window.EventSource = FakeEventSource;
  window.fetch = async (url, options = {}) => {
    if (options.method === 'POST') {
      return { ok: true, status: 200, json: async () => ({ session_id: 'sid-1' }) };
    }
    if (restoredState) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ session_id: storedSid, state: restoredState }),
      };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  };
  if (storedSid) window.localStorage.setItem('mbkAiChat.sid', storedSid);

  window.eval(WIDGET);
  const root = window.document.getElementById('mbk-ai-chat');
  root.querySelector('.mbkc-launcher').click();
  await flush();
  await flush();
  return { window, root, streams };
}

function answer(stream, { bubbles, quickReplies, state }) {
  const payload = { bubbles };
  if (quickReplies !== undefined) payload.quick_replies = quickReplies;
  stream.emit('bubbles', payload);
  if (state) stream.emit('state', { state });
  stream.emit('done', {});
}

function chips(root) {
  return [...root.querySelectorAll('.mbkc-chips .mbkc-chip')].map((button) => [
    button.textContent,
    button.getAttribute('data-value'),
  ]);
}

test('кнопки приветствия рисуются под пузырями последним рядом ленты', async () => {
  const { root, streams } = await mountWidget();

  assert.equal(streams.length, 1);
  assert.equal(streams[0].url, '/chat-api/session/sid-1/stream');
  answer(streams[0], { bubbles: ['Добрый день!'], quickReplies: GREETING });

  assert.deepEqual(chips(root), [
    ['Получить деньги', 'Получить деньги'],
    ['Другой вопрос', 'Другой вопрос'],
  ]);
  const feed = root.querySelector('.mbkc-feed-inner');
  assert.ok(feed.lastElementChild.classList.contains('mbkc-chips'));
});

test('без поля quick_replies виджет ведёт себя как раньше', async () => {
  const { root, streams } = await mountWidget();

  answer(streams[0], { bubbles: ['Добрый день!'] });

  assert.deepEqual(chips(root), []);
  assert.equal(root.querySelectorAll('.mbkc-row--in .mbkc-bubble').length, 1);
});

test('нажатие отправляет текст кнопки обычным сообщением и убирает ряд', async () => {
  const { root, streams } = await mountWidget();
  answer(streams[0], { bubbles: ['Добрый день!'], quickReplies: GREETING });

  root.querySelectorAll('.mbkc-chip')[1].click();

  assert.equal(streams.length, 2);
  assert.equal(
    streams[1].url,
    '/chat-api/session/sid-1/stream?message=' + encodeURIComponent('Другой вопрос'),
  );
  assert.deepEqual(chips(root), []);
  const outgoing = [...root.querySelectorAll('.mbkc-row--out .mbkc-bubble span')].map(
    (span) => span.textContent,
  );
  assert.deepEqual(outgoing, ['Другой вопрос']);
});

test('пока бот отвечает, нажатие ничего не отправляет и ряд остаётся', async () => {
  const { root, streams } = await mountWidget();
  streams[0].emit('bubbles', { bubbles: ['Добрый день!'], quick_replies: GREETING });

  root.querySelector('.mbkc-chip').click();

  assert.equal(streams.length, 1);
  assert.equal(chips(root).length, 2);
});

test('начал печатать — ряд пропадает, пустой ввод ряд не трогает', async () => {
  const { window, root, streams } = await mountWidget();
  answer(streams[0], { bubbles: ['Добрый день!'], quickReplies: GREETING });
  const input = root.querySelector('.mbkc-input');

  input.value = '   ';
  input.dispatchEvent(new window.Event('input', { bubbles: true }));
  assert.equal(chips(root).length, 2);

  input.value = 'у меня';
  input.dispatchEvent(new window.Event('input', { bubbles: true }));
  assert.deepEqual(chips(root), []);
});

test('отправка из поля ввода убирает ряд', async () => {
  const { window, root, streams } = await mountWidget();
  answer(streams[0], { bubbles: ['Добрый день!'], quickReplies: GREETING });
  const input = root.querySelector('.mbkc-input');
  input.value = 'Нужны деньги';

  root
    .querySelector('.mbkc-inputbar')
    .dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));

  assert.equal(streams.length, 2);
  assert.deepEqual(chips(root), []);
  assert.equal(input.value, '');
});

test('состояние с пустым списком кнопок убирает ряд', async () => {
  const { root, streams } = await mountWidget();
  streams[0].emit('bubbles', { bubbles: ['Добрый день!'], quick_replies: GREETING });

  streams[0].emit('state', { state: { quick_replies: [] } });

  assert.deepEqual(chips(root), []);
});

test('после перезагрузки кнопки берутся из состояния сессии', async () => {
  const { root, streams } = await mountWidget({
    storedSid: 'sid-9',
    restoredState: {
      chat_history: [{ role: 'assistant', content: 'Добрый день!' }],
      quick_replies: GREETING,
    },
  });

  assert.equal(streams.length, 0);
  assert.deepEqual(
    chips(root).map(([label]) => label),
    ['Получить деньги', 'Другой вопрос'],
  );
});

test('после перезагрузки разговора с репликой клиента кнопок нет', async () => {
  const { root } = await mountWidget({
    storedSid: 'sid-9',
    restoredState: {
      chat_history: [
        { role: 'assistant', content: 'Добрый день!' },
        { role: 'user', content: 'Нужны деньги' },
      ],
      quick_replies: [],
    },
  });

  assert.deepEqual(chips(root), []);
});

test('мусорные элементы отбрасываются, кнопок не больше четырёх', async () => {
  const { root, streams } = await mountWidget();
  const junk = [
    null,
    'строка',
    { label: '', value: 'x' },
    { label: 'Без значения' },
    { label: 'Число', value: 5 },
    { label: '1', value: '1' },
    { label: '2', value: '2' },
    { label: '3', value: '3' },
    { label: '4', value: '4' },
    { label: '5', value: '5' },
  ];

  answer(streams[0], { bubbles: ['Добрый день!'], quickReplies: junk });

  assert.deepEqual(
    chips(root).map(([label]) => label),
    ['1', '2', '3', '4'],
  );
});

test('не массив вместо списка — кнопок нет', async () => {
  const { root, streams } = await mountWidget();

  answer(streams[0], { bubbles: ['Добрый день!'], quickReplies: { label: 'x', value: 'x' } });

  assert.deepEqual(chips(root), []);
});

test('текст кнопки — только текст, разметка не исполняется', async () => {
  const { root, streams } = await mountWidget();
  const label = '<img src=x onerror="window.hacked=1">';

  answer(streams[0], { bubbles: ['Добрый день!'], quickReplies: [{ label, value: 'x' }] });

  assert.equal(root.querySelector('.mbkc-chip img'), null);
  assert.equal(root.querySelector('.mbkc-chip').textContent, label);
});

test('закрытый диалог кнопок не показывает', async () => {
  const { root, streams } = await mountWidget();

  streams[0].emit('state', { state: { closed: true, quick_replies: [] } });
  streams[0].emit('bubbles', { bubbles: ['Спасибо!'], quick_replies: GREETING });

  assert.deepEqual(chips(root), []);
});
```

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `cd site_widget && npm test`
Expected: FAIL — тесты про кнопки (кнопок нет: `[]` вместо двух), тест «без поля quick_replies» и «закрытый диалог» проходят.

- [ ] **Step 4: Правка `_ai-chat.js`**

4.1. В шапке-комментарии файла заменить строку

```js
 * GET /chat-api/session/{id}/stream[?message=...] -> SSE (chunk|bubbles|state|done|error);
```

на

```js
 * GET /chat-api/session/{id}/stream[?message=...] -> SSE (chunk|bubbles|state|done|error);
 *   bubbles может нести quick_replies: [{label, value}] — кнопки под репликой бота;
```

4.2. Сразу после функции `hideTyping()` вставить:

```js
  /* ---------- быстрые ответы (чипсы) ---------- */

  /* Кнопки под репликой бота: бот присылает их полем quick_replies события
     bubbles и в состоянии сессии. Нажатие уходит обычным сообщением с текстом
     кнопки — тем же путём, что набранный текст. Поле ввода не блокируется. */
  var MAX_CHIPS = 4;

  function clearQuickReplies() {
    var rows = els.feedInner.querySelectorAll('.mbkc-chips');
    for (var i = 0; i < rows.length; i++) rows[i].remove();
  }

  function renderQuickReplies(items) {
    clearQuickReplies();
    if (st.closed || !Array.isArray(items)) return;
    var row = document.createElement('div');
    row.className = 'mbkc-chips';
    for (var i = 0; i < items.length && row.childNodes.length < MAX_CHIPS; i++) {
      var item = items[i];
      if (!item || typeof item.label !== 'string' || typeof item.value !== 'string') continue;
      if (!item.label.trim() || !item.value.trim()) continue;
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'mbkc-chip';
      button.textContent = item.label;
      button.setAttribute('data-value', item.value);
      row.appendChild(button);
    }
    if (!row.childNodes.length) return;
    els.feedInner.appendChild(row);
    scrollDown();
  }
```

4.3. В `startDialog`, в ветке восстановления, заменить

```js
          st.started = true;
          st.busy = false;
          setBusyUI(false);
          if (!hist.length) openStream(null); // сессия есть, приветствия ещё не было
```

на

```js
          st.started = true;
          st.busy = false;
          setBusyUI(false);
          if (hist.length) renderQuickReplies(state.quick_replies || []);
          else openStream(null); // сессия есть, приветствия ещё не было
```

4.4. Заменить обработчик события `bubbles` целиком на:

```js
    es.addEventListener('bubbles', function (ev) {
      var payload = {};
      try { payload = JSON.parse(ev.data) || {}; } catch (e) {}
      var bubbles = payload.bubbles || [];
      if (!bubbles.length) return;
      st.gotBubbles = true;
      /* Черновик со стрим-текстом заменяется финальными пузырями */
      if (st.draft) { st.draft.row.remove(); st.draft = null; st.draftText = ''; }
      hideTyping();
      for (var i = 0; i < bubbles.length; i++) addMessage('bot', bubbles[i]);
      renderQuickReplies(payload.quick_replies || []);
    });
```

4.5. В обработчике события `state` заменить

```js
        if (state && state.closed) {
          st.closed = true;
          dropSid();
          addSystem('Диалог завершён. Обновите страницу, чтобы начать новый.');
        }
```

на

```js
        if (state && Array.isArray(state.quick_replies) && !state.quick_replies.length) {
          clearQuickReplies();
        }
        if (state && state.closed) {
          st.closed = true;
          clearQuickReplies();
          dropSid();
          addSystem('Диалог завершён. Обновите страницу, чтобы начать новый.');
        }
```

4.6. Заменить функцию `submit()` целиком на:

```js
  /* Одна дорога сообщения для поля ввода и для кнопок. Возвращает false,
     если отправлять нельзя: пусто, идёт ответ, диалог закрыт, нет сессии. */
  function sendText(raw) {
    var text = (raw || '').trim();
    if (!text || st.busy || st.closed || !st.sid) return false;
    if (text.length > MAX_LEN) text = text.slice(0, MAX_LEN);
    clearQuickReplies();
    addMessage('user', text);
    openStream(text);
    return true;
  }

  function submit() {
    if (sendText(els.input.value)) els.input.value = '';
  }
```

4.7. В блоке «обработчики» заменить

```js
  els.input.addEventListener('input', function () { setBusyUI(st.busy); });
```

на

```js
  els.input.addEventListener('input', function () {
    /* Человек начал печатать сам — кнопки больше не предлагаем. */
    if (els.input.value.trim()) clearQuickReplies();
    setBusyUI(st.busy);
  });

  els.feedInner.addEventListener('click', function (ev) {
    var chip = ev.target.closest('.mbkc-chip');
    if (!chip) return;
    sendText(chip.getAttribute('data-value'));
  });
```

- [ ] **Step 5: Прогнать тесты**

Run: `cd site_widget && npm test`
Expected: PASS, все 13 тестов.

Run: `node --check site_widget/_ai-chat.js`
Expected: без вывода (синтаксис корректен).

Run: `git diff --stat HEAD -- site_widget/_ai-chat.js`
Expected: правка только в `site_widget/_ai-chat.js`; других изменений в копиях виджета нет.

Run: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`
Expected: всё зелёное (Python не менялся).

- [ ] **Step 6: Commit**

```bash
git add site_widget/package.json site_widget/package-lock.json site_widget/test/ai-chat.test.mjs site_widget/_ai-chat.js
git commit -m "Виджет: кнопки quick_replies под репликой бота и тесты на jsdom"
```

---

### Task 6: Виджет — стили и порядок поставки

**Files:**
- Modify: `site_widget/_!ai-chat.scss` — после блока `#mbk-ai-chat .mbkc-system { ... }`, перед строкой `// Индикатор набора`
- Create: `site_widget/README.md`

**Interfaces:**
- Consumes: классы `.mbkc-chips`, `.mbkc-chip` (Task 5).
- Produces: —

- [ ] **Step 1: Стили** — в `site_widget/_!ai-chat.scss` перед строкой `// Индикатор набора` вставить:

```scss
// Быстрые ответы (чипсы) под репликой бота. Отступ слева — по краю пузыря:
// отступ строки 1.6rem + аватар 4.8rem + зазор 0.8rem.
#mbk-ai-chat .mbkc-chips {
	display: flex;
	flex-wrap: wrap;
	gap: 0.8rem;
	padding: 0 1.6rem 0 7.2rem;
}

#mbk-ai-chat .mbkc-chip {
	padding: 0.6rem 1.4rem;
	background: $mbkc-card;
	border: 1px solid $mbkc-border;
	border-radius: 999px;
	color: $mbkc-foreground;
	font-size: 1.3rem;
	line-height: 1.4;
	text-align: left;
	transition: border-color 0.15s, background-color 0.15s;

	&:hover,
	&:focus-visible {
		border-color: $red;
		background: $mbkc-tint;
		outline: none;
	}
}

```

Специфичность `#mbk-ai-chat .mbkc-chip` (1,1,0) выше общего сброса `#mbk-ai-chat button` (1,0,1) — фон и рамка не затираются.

- [ ] **Step 2: Проверить, что правка только добавляет** —

Run: `git diff --numstat HEAD -- "site_widget/_!ai-chat.scss"`
Expected: `N	0	site_widget/_!ai-chat.scss` — только добавленные строки, ноль удалённых.

Run: `rg -n "\$(mbkc-card|mbkc-border|mbkc-foreground|mbkc-tint|red)\b" "site_widget/_!ai-chat.scss"` (без `rg` — `git grep -nE` той же регуляркой)
Expected: каждая переменная из нового блока встречается в файле и выше него (объявлена в палитре файла или уже используется: `$red` — фирменная переменная сайта).

- [ ] **Step 3: README** — создать `site_widget/README.md`:

````markdown
# Виджет AI-чата mbk-site — рабочая копия

Файлы — копии из репозитория заказчика `mbk/mbk-site` с правкой чипсов
(спека `docs/superpowers/specs/2026-09-14-quick-replies-greeting-design.md`).
В поставку `mbk_ai` папка не входит: `sync_vendor.py` её не перечисляет.

| Файл здесь | Путь в mbk-site | Копия снята с |
|---|---|---|
| `_ai-chat.js` | `www/resources/from_verstala/src/js/modules/_ai-chat.js` | blob `f151e68a95b807effd4eed230f5f9708fb0d1c20`, коммит `13d5a961` |
| `_!ai-chat.scss` | `www/resources/from_verstala/src/sass/modules/_!ai-chat.scss` | blob `1c7c4f2673b34c12fe39a2d428b361f2219074b4`, коммит `7f3b16c7` |
| `test/ai-chat-widget.blade.php` | `www/resources/views/ai-chat-widget.blade.php` | blob `6cd909912e19580cc59bb77626471672e5c95b19`, коммит `7f3b16c7` |

`master` mbk-site на момент копии — `e308abc7`. Нетронутая копия лежит в
истории нашего репозитория коммитом «Копия виджета AI-чата из mbk-site без
правок»; правка — следующими коммитами, её видно `git diff`.

## Контракт с ботом

- Событие `bubbles`: `{"bubbles": [...], "quick_replies": [{"label", "value"}]}`.
  Поле есть, только когда кнопки есть.
- Состояние сессии (`GET /chat-api/session/<id>`, событие `state`): поле
  `quick_replies` всегда, пустой список — кнопок нет.
- Нажатие отправляет `value` обычным сообщением.

## Тесты

```bash
cd site_widget
npm install
npm test
```

## Поставка — только после утверждения человека

1. Сверить `blob_id` обоих файлов на `master` mbk-site с таблицей выше. Если
   файл изменился — сначала перенести их правку сюда и перезапустить тесты.
2. Создать ветку `dev` от `master` и коммит двух файлов
   (`_ai-chat.js`, `_!ai-chat.scss`) по путям из таблицы.
3. MR `dev → master`, `assignee_id` и `reviewer_ids` — vasin (id=2). В
   описании: контракт выше; что пересобрать — `gulp build` в
   `www/resources/from_verstala` (обновятся `www/public/app/js/app.js` и
   `app.css`); как проверить — открыть чат на главной, под приветствием две
   кнопки, нажатие отправляет текст кнопки, ряд пропадает при вводе текста.
4. Сборку и выкатку сайта делает Андрей.
````

- [ ] **Step 4: Прогнать проверки**

Run: `cd site_widget && npm test`
Expected: PASS (стили на тесты не влияют).

Run: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`
Expected: всё зелёное.

- [ ] **Step 5: Commit**

```bash
git add "site_widget/_!ai-chat.scss" site_widget/README.md
git commit -m "Виджет: стили кнопок и порядок поставки в mbk-site"
```

---

### Task 7: Кнопки на нашем стенде

**Files:**
- Modify: `frontend/src/components/ChatView.jsx`

**Interfaces:**
- Consumes: контракт бота (Tasks 1, 4).
- Produces: —

- [ ] **Step 1: Состояние кнопок** — в `ChatView` сразу после строки `const [usage, setUsage] = useState(null);` добавить:

```jsx
  // Кнопки под последней репликой бота (спека чипсов 14.09.2026): приходят в
  // событии bubbles и в состоянии сессии; нажатие — обычное сообщение.
  const [quickReplies, setQuickReplies] = useState(initialState?.quick_replies || []);
```

- [ ] **Step 2: Синхронизация** — в `useEffect` с polling, в функции `refresh`, после строки `if (state.chat_history) setMessages(state.chat_history);` добавить:

```jsx
        if (Array.isArray(state.quick_replies)) setQuickReplies(state.quick_replies);
```

В `runStream` заменить

```jsx
    if (userMessage) {
      setMessages((m) => [...m, { role: 'user', content: userMessage }]);
    }
```

на

```jsx
    if (userMessage) {
      setQuickReplies([]);
      setMessages((m) => [...m, { role: 'user', content: userMessage }]);
    }
```

заменить

```jsx
      onBubbles: ({ bubbles }) => {
        // Сервер прислал финальную разбивку — заменяем накопленный буфер
        // на готовые пузыри (учитывают guard_bubbles).
        setStreamingBuffer('');
        setMessages((m) => [...m, ...bubbles.map((c) => ({ role: 'assistant', content: c }))]);
      },
```

на

```jsx
      onBubbles: ({ bubbles, quick_replies: chips }) => {
        // Сервер прислал финальную разбивку — заменяем накопленный буфер
        // на готовые пузыри (учитывают guard_bubbles).
        setStreamingBuffer('');
        setMessages((m) => [...m, ...bubbles.map((c) => ({ role: 'assistant', content: c }))]);
        if (bubbles.length) setQuickReplies(Array.isArray(chips) ? chips : []);
      },
```

и в `onState` после строки `if (state?.chat_history) setMessages(state.chat_history);` добавить:

```jsx
        if (Array.isArray(state?.quick_replies)) setQuickReplies(state.quick_replies);
```

- [ ] **Step 3: Отрисовка и ввод** — в разметке ленты сразу после блока

```jsx
          {streamingBubbles.map((b, i) => (
            <ChatBubble key={`s${i}`} role="assistant" text={b} streaming={i === streamingBubbles.length - 1} />
          ))}
```

добавить

```jsx
          {!isStreaming && !isCompleted && quickReplies.length > 0 && (
            <QuickReplies items={quickReplies} onPick={(value) => runStream(value)} />
          )}
```

В `<textarea ...>` рядом с `onKeyDown` добавить атрибут

```jsx
              onInput={(e) => {
                if (e.currentTarget.value.trim()) setQuickReplies([]);
              }}
```

В конец файла, после функции `ChatBubble`, добавить компонент:

```jsx
function QuickReplies({ items, onPick }) {
  return (
    <div className="flex flex-wrap gap-2 bubble-fade-in">
      {items.map((item) => (
        <button
          key={item.value}
          type="button"
          onClick={() => onPick(item.value)}
          className="rounded-full border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 hover:border-brand-600"
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Сборка**

Run: `cd frontend && npm run build`
Expected: сборка без ошибок (`dist/` игнорируется git).

Run: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`
Expected: всё зелёное.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ChatView.jsx
git commit -m "Стенд: кнопки приветствия в чате"
```

---

### Task 8: Роли ролевого прогона

**Files:**
- Modify: `evaluation/personas.py` — две роли в конец списка `PERSONAS` (перед закрывающей `]`)
- Modify: `tests/test_personas.py`

**Interfaces:**
- Consumes: `quick_replies.chip_choice` (Task 1).
- Produces: роли `chip_money`, `chip_other_question`.

- [ ] **Step 1: Написать падающие тесты** — в конец `tests/test_personas.py` добавить:

```python
def test_chip_roles_start_with_the_exact_chip_text():
    """Роль «нажимает кнопку»: первая реплика — ровно текст кнопки."""
    import quick_replies

    for persona_id, text in (
        ("chip_money", "Получить деньги"),
        ("chip_other_question", "Другой вопрос"),
    ):
        persona = persona_by_id(persona_id)
        assert f"ровно «{text}»" in persona.story
        assert quick_replies.chip_choice(text) is not None


def test_money_chip_role_goes_through_the_questionnaire():
    persona = persona_by_id("chip_money")

    assert persona.expects_completion is True
    assert persona.facts["request"]["amount"] == 300_000


def test_other_question_chip_role_is_not_a_questionnaire():
    persona = persona_by_id("chip_other_question")

    assert persona.expects_completion is False
    assert "как к вам обращаться" in persona.forbidden_any
    assert "какая сумма" in persona.forbidden_any
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv313/Scripts/python -m pytest tests/test_personas.py -q`
Expected: FAIL — `KeyError: 'неизвестная роль: chip_money'`.

- [ ] **Step 3: Роли** — в `evaluation/personas.py` в конец списка `PERSONAS`, после роли `greeting_then_existing` (после её закрывающей `),`), добавить:

```python
    # Чипсы в приветствии (спека 14.09.2026): человек не пишет, а жмёт кнопку
    # под первой репликой бота. Роль отправляет ровно текст кнопки — так
    # нажатие приходит боту.
    Persona(
        id="chip_money",
        facts={
            "client": {"age": 35, "region_class": "rf_other", "phone": "+79000000021"},
            "request": {"amount": 300_000},
            "debts": {"has_active_credits": False, "total": 0},
            "employment": {"type": "official", "income": 70_000},
            "assets": {"has_realty": False, "has_vehicle": False},
        },
        description="Первой репликой нажал кнопку «Получить деньги»",
        story=(
            "Андрей, 35 лет, живёт в Самаре. Нужно 300 тысяч на ремонт. "
            "Кредитов и долгов нет. Работает официально, инженером, около 70 "
            "тысяч в месяц. Недвижимости и машины нет. Телефон +7 900 000 00 21. "
            "Первым сообщением ты отправляешь ровно «Получить деньги» — как "
            "будто нажал кнопку, — и больше ничего. Дальше отвечаешь на вопросы."
        ),
        manner="Спокойный, отвечает коротко и по делу.",
        forbidden_any=["отлично", "супер"],
    ),
    Persona(
        id="chip_other_question",
        description="Нажал «Другой вопрос», потом спросил не про деньги",
        story=(
            "Первым сообщением ты отправляешь ровно «Другой вопрос» — как "
            "будто нажал кнопку, — и больше ничего, ждёшь ответа. Когда "
            "специалист спросит, какой вопрос, спрашиваешь, можно ли у них "
            "оформить ОСАГО на машину. Если скажут, что это не их профиль, "
            "поблагодари и попрощайся."
        ),
        manner="Вежливый, немногословный.",
        forbidden_any=["как к вам обращаться", "какая сумма", "оставьте номер"],
        max_turns=5,
        expects_completion=False,
    ),
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv313/Scripts/python -m pytest tests/test_personas.py -q`
Expected: PASS.

Run: `.venv313/Scripts/python -m pytest -q` и `.venv313/Scripts/python -m ruff check .`
Expected: всё зелёное.

- [ ] **Step 5: Commit**

```bash
git add evaluation/personas.py tests/test_personas.py
git commit -m "Роли прогона: кнопки «Получить деньги» и «Другой вопрос»"
```

---

### Task 9 (контроллер): приёмка

Выполняет контроллер. Внешних отправок нет.

- [ ] **Step 1: Проверки** — `.venv313/Scripts/python -m ruff check .`; `.venv313/Scripts/python -m pytest -q`; `cd site_widget && npm test`; `cd frontend && npm run build`. Expected: всё зелёное; тестов Python больше, чем 1689 passed на 14.09.2026.
- [ ] **Step 2: Ручной просмотр стенда** — поднять сервер без переменных `MBK_QUICK_REPLIES` и `MBK_ENTRY_ROUTING` так же, как для ролевого прогона 13.09.2026, открыть стенд браузером: под приветствием две кнопки; «Другой вопрос» → бот просит рассказать вопрос; ввод текста убирает ряд; перезагрузка страницы до нажатия — кнопки на месте.
- [ ] **Step 3: Ролевой прогон** — все роли, включая `chip_money` и `chip_other_question`, на умолчаниях флагов (`run_roleplay_eval.py`, параметры — как в прогоне 13.09.2026, `logs/roleplay/roleplay_20260913_191912.json`). Смысловая оценка диалогов — как у прошлых блоков. Expected: новые роли проходят; прежние — без регресса.
- [ ] **Step 4: Память** — `docs/agent-memory/SESSION.md`: итог блока, что осталось до поставки (утверждение человека на push в dev и в `mbk-site`; `quick_replies.py` дописать в поимённый список `sync_vendor.py`; сверка `blob_id` по `site_widget/README.md`). Commit `docs(memory): чипсы в приветствии — итоги`.
