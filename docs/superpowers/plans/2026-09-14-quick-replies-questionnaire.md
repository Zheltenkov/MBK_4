# Чипсы на вопросах анкеты — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** На закрытых вопросах анкеты под репликой бота появляются кнопки ответа, и нажатие разбирается без вызова модели.

**Architecture:** Кнопки каждого закрытого поля задаются в `routing/matrix.yaml` (`quick_replies`). Правило показа живёт в `quick_replies.quick_replies`: после первой реплики клиента оно отдаёт кнопки поля, о котором бот спросил на последнем ходу (`script.asked`), если поле одно. Словари быстрого разбора дополняются так, чтобы каждая кнопка узнавалась без модели; это держит инвариантный тест. Контракт, виджет и стенд не меняются.

**Tech Stack:** Python 3.13, pydantic 2, PyYAML, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-09-14-quick-replies-questionnaire-design.md` (продолжение `docs/superpowers/specs/2026-09-14-quick-replies-greeting-design.md`).

## Global Constraints

- Кнопки, `label` = `value`, по порядку: `debts.has_active_credits` — «Да, есть», «Нет»; `employment.type` — «По найму», «Свой бизнес», «Не работаю»; `assets.has_realty` — «Да, есть», «Нет»; `assets.realty.kind` — «Квартира», «Дом», «Апартаменты», «Коммерческая»; `assets.has_second_realty` — «Да, есть», «Нет»; `assets.has_vehicle` — «Да, есть», «Нет»; `assets.vehicle.pledged` — «В кредите», «В залоге», «Чистая».
- Ожидаемые значения: «Да, есть» → `True`, «Нет» → `False`; «По найму» → `official`, «Свой бизнес» → `business`, «Не работаю» → `unemployed`; «Квартира» → `flat`, «Дом» → `house`, «Апартаменты» → `apartments`, «Коммерческая» → `commercial`; «В кредите», «В залоге» → `True`, «Чистая» → `False`.
- Кнопки только у полей `kind` `bool` и `enum`, не больше четырёх, только у полей, которые в `script` спрашиваются отдельным шагом.
- Каждая кнопка разбирается `interpretation.fastpath.parse_expected_answer(value, [path], matrix)` ровно в ожидаемое значение — без модели.
- Показ кнопок вопроса: флаги включены (`enabled`); клиент уже писал; последняя реплика истории — бота; идёт анкета (`entry_routing.questionnaire_active`); последняя реплика клиента — не прощание (`core.client_is_saying_goodbye`); `script.asked` — ровно одно поле с `quick_replies`. Кнопки приветствия не меняются.
- `quick_replies.py` не импортирует `core` и `entry_routing` на уровне модуля.
- Контракт `bubbles.quick_replies` / `state.quick_replies`, `main.py`, виджет `site_widget/`, стенд `frontend/` не меняются. Аналитика нажатий на вопросах анкеты не пишется.
- В `mbk/mbk-site`, `mbk/mbk_ai` и в origin ничего не отправлять. Боевая база — только чтение.
- Коммиты от `Zheltenkov <zheltenkovvp@gmail.com>`, без упоминания Claude и без трейлеров Co-Authored-By.
- Проверки перед завершением каждой задачи: `.venv313/Scripts/python -m ruff check .` и `.venv313/Scripts/python -m pytest -q`.

## Карта файлов

| Файл | Что | Задача |
|---|---|---|
| `routing/schema.py`, `routing/matrix.yaml`, `routing/normalize.py`, `interpretation/fastpath.py`, `tests/test_matrix_quick_replies.py` (новый), `docs/HANDOVER.md` | кнопки в матрице и их разбор без модели | 1 |
| `quick_replies.py`, `tests/test_quick_replies.py`, `tests/test_quick_replies_questionnaire.py` (новый) | правило показа | 2 |
| превью, `docs/agent-memory/SESSION.md` | приёмка | 3 (контроллер) |

---

### Task 1: Кнопки в матрице и разбор нажатия без модели

**Files:**
- Modify: `routing/schema.py` — модель `QuickReply` перед `FieldSpec`, поле и валидатор в `FieldSpec`
- Modify: `routing/matrix.yaml` — `quick_replies` у семи полей
- Modify: `routing/normalize.py` — два маркера в `_EMPLOYMENT`
- Modify: `interpretation/fastpath.py` — таблица `_PATH_BOOL_VALUES`, проверка в `_simple_bool`
- Create: `tests/test_matrix_quick_replies.py`
- Modify: `docs/HANDOVER.md` — абзац после «**Чипсы в приветствии**»

**Interfaces:**
- Consumes: —
- Produces:
  - `routing.schema.QuickReply` (поля `label: str`, `value: str`)
  - `FieldSpec.quick_replies: list[QuickReply] | None`
  - `matrix.fields[path].quick_replies` для семи полей из Global Constraints

- [ ] **Step 1: Написать падающие тесты** — создать `tests/test_matrix_quick_replies.py`:

```python
"""Кнопки вопросов анкеты: заданы в матрице и разбираются без модели.

Спека docs/superpowers/specs/2026-09-14-quick-replies-questionnaire-design.md.
Нажатие кнопки приходит боту обычным сообщением. Если быстрый разбор его не
узнаёт, ход зовёт модель — несколько секунд молчания на ответе, который
человек дал одним нажатием. Поэтому здесь проверяется каждая кнопка матрицы.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from interpretation import fastpath
from routing import FieldSpec


EXPECTED = {
    ("debts.has_active_credits", "Да, есть"): True,
    ("debts.has_active_credits", "Нет"): False,
    ("employment.type", "По найму"): "official",
    ("employment.type", "Свой бизнес"): "business",
    ("employment.type", "Не работаю"): "unemployed",
    ("assets.has_realty", "Да, есть"): True,
    ("assets.has_realty", "Нет"): False,
    ("assets.realty.kind", "Квартира"): "flat",
    ("assets.realty.kind", "Дом"): "house",
    ("assets.realty.kind", "Апартаменты"): "apartments",
    ("assets.realty.kind", "Коммерческая"): "commercial",
    ("assets.has_second_realty", "Да, есть"): True,
    ("assets.has_second_realty", "Нет"): False,
    ("assets.has_vehicle", "Да, есть"): True,
    ("assets.has_vehicle", "Нет"): False,
    ("assets.vehicle.pledged", "В кредите"): True,
    ("assets.vehicle.pledged", "В залоге"): True,
    ("assets.vehicle.pledged", "Чистая"): False,
}


def _chips(matrix) -> set[tuple[str, str]]:
    return {
        (path, chip.value)
        for path, spec in matrix.fields.items()
        for chip in spec.quick_replies or []
    }


def test_chips_are_exactly_the_agreed_set(matrix):
    assert _chips(matrix) == set(EXPECTED)


def test_label_is_the_text_that_is_sent(matrix):
    """Человек видит в своей реплике ровно то, что нажал."""
    for spec in matrix.fields.values():
        for chip in spec.quick_replies or []:
            assert chip.label == chip.value


@pytest.mark.parametrize("path, value", sorted(EXPECTED))
def test_every_chip_is_parsed_without_the_model(matrix, path, value):
    assert fastpath.parse_expected_answer(value, [path], matrix) == [
        {"path": path, "value": EXPECTED[(path, value)], "confidence": "high"}
    ]


def test_chips_live_only_on_questions_asked_alone(matrix):
    """Спрошено два поля — быстрый разбор не берётся, кнопка ответила бы на половину."""
    asked_alone = {step.ask[0] for step in matrix.script if len(step.ask) == 1}
    for path, spec in matrix.fields.items():
        if spec.quick_replies:
            assert path in asked_alone, path


def test_no_more_than_four_chips():
    chips = [{"label": str(i), "value": str(i)} for i in range(5)]

    with pytest.raises(ValidationError):
        FieldSpec(label="x", kind="bool", ask="x", quick_replies=chips)


def test_free_answer_fields_get_no_chips():
    with pytest.raises(ValidationError):
        FieldSpec(
            label="x",
            kind="money",
            ask="x",
            quick_replies=[{"label": "100 тысяч", "value": "100 тысяч"}],
        )


def test_empty_chip_text_is_rejected():
    with pytest.raises(ValidationError):
        FieldSpec(
            label="x",
            kind="bool",
            ask="x",
            quick_replies=[{"label": "", "value": "Да"}],
        )
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv313/Scripts/python -m pytest tests/test_matrix_quick_replies.py -q`
Expected: FAIL — `AttributeError: 'FieldSpec' object has no attribute 'quick_replies'` (или ValidationError про лишнее поле) и не пойманная ValidationError в тестах схемы.

- [ ] **Step 3: Схема** — в `routing/schema.py` перед `class FieldSpec(BaseModel):` вставить:

```python
class QuickReply(BaseModel):
    """Кнопка быстрого ответа под вопросом анкеты.

    `label` — что видит человек, `value` — что уходит боту при нажатии обычным
    сообщением. Значение обязано разбираться быстрым разбором без модели —
    это держит tests/test_matrix_quick_replies.py.
    """

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    value: str = Field(min_length=1)


```

В `FieldSpec` после поля `deferred: bool = False` добавить:

```python
    # Кнопки быстрого ответа под этим вопросом (спека 2026-09-14-quick-replies-
    # questionnaire-design.md). Только у закрытых полей и не больше четырёх:
    # виджет больше не рисует, а свободный ответ кнопками не выразить.
    quick_replies: list[QuickReply] | None = None

    @model_validator(mode="after")
    def _check_quick_replies(self) -> "FieldSpec":
        if not self.quick_replies:
            return self
        if self.kind not in {"bool", "enum"}:
            raise ValueError("quick_replies допустимы только у полей bool и enum")
        if len(self.quick_replies) > 4:
            raise ValueError("quick_replies: не больше четырёх кнопок")
        return self
```

- [ ] **Step 4: Кнопки в матрице** — в `routing/matrix.yaml` добавить блок `quick_replies` в конец описания каждого из семи полей (последней строкой блока поля, с тем же отступом, что `ask:`). Перед первым из них (`assets.has_realty`, он выше остальных в файле) добавить комментарий:

```yaml
    # Кнопки под вопросом (спека 2026-09-14-quick-replies-questionnaire-design.md):
    # текст кнопки уходит боту обычным сообщением и обязан разбираться быстрым
    # разбором без модели — см. tests/test_matrix_quick_replies.py.
```

Блоки:

`assets.has_realty`, `assets.has_second_realty`, `assets.has_vehicle`, `debts.has_active_credits`:

```yaml
    quick_replies:
      - {label: "Да, есть", value: "Да, есть"}
      - {label: "Нет", value: "Нет"}
```

`assets.realty.kind` (после строки `after: assets.has_realty` этого поля):

```yaml
    quick_replies:
      - {label: "Квартира", value: "Квартира"}
      - {label: "Дом", value: "Дом"}
      - {label: "Апартаменты", value: "Апартаменты"}
      - {label: "Коммерческая", value: "Коммерческая"}
```

`assets.vehicle.pledged` (после строки `after: assets.has_vehicle` этого поля):

```yaml
    quick_replies:
      - {label: "В кредите", value: "В кредите"}
      - {label: "В залоге", value: "В залоге"}
      - {label: "Чистая", value: "Чистая"}
```

`employment.type` (после строки `why: "часть решений требует подтверждённого дохода"`):

```yaml
    quick_replies:
      - {label: "По найму", value: "По найму"}
      - {label: "Свой бизнес", value: "Свой бизнес"}
      - {label: "Не работаю", value: "Не работаю"}
```

У `assets.realty.kind` блок ставится после `after:`; комментарий про решение заказчика и `skip_when_settled` у соседних полей не трогать. У `assets.realty_2.kind` кнопок нет (спрашивается в паре с регионом).

- [ ] **Step 5: Маркеры занятости** — в `routing/normalize.py`, список `_EMPLOYMENT`, перед строкой `("бизнес", "business"),` вставить:

```python
    # Тексты кнопок анкеты (quick_replies в matrix.yaml). Быстрый разбор
    # сравнивает ответ с маркером целиком по числу слов — без своих маркеров
    # «свой бизнес» и «по найму» уходили бы модели.
    ("свой бизнес", "business"),
```

и перед строкой `("найм", "official"),` вставить:

```python
    ("по найм", "official"),
```

- [ ] **Step 6: Залог авто словами** — в `interpretation/fastpath.py` сразу после определения `_PATH_BOOL_PHRASES` (после его закрывающей `}`) вставить:

```python

# Согласие и отказ, сказанные не словами «да»/«нет». На вопрос «машина в
# кредите или залоге, или чистая» кнопки анкеты отвечают «В кредите», «В
# залоге» и «Чистая» (quick_replies в matrix.yaml) — без этой таблицы нажатие
# уходило бы модели.
_PATH_BOOL_VALUES: dict[str, dict[str, bool]] = {
    "assets.vehicle.pledged": {
        "в кредите": True,
        "в залоге": True,
        "чистая": False,
        "чистый": False,
    },
}
```

В функции `_simple_bool` сразу после строки `phrase = " ".join(words)` вставить:

```python
    mapped = _PATH_BOOL_VALUES.get(path, {}).get(phrase)
    if mapped is not None:
        return mapped
```

- [ ] **Step 7: Прогнать тесты**

Run: `.venv313/Scripts/python -m pytest tests/test_matrix_quick_replies.py tests/test_fastpath.py tests/test_employment.py tests/test_normalize.py -q`
Expected: PASS. Если какая-то кнопка не разбирается (тест `test_every_chip_is_parsed_without_the_model` падает на конкретной паре), не расширяй правила быстрого разбора шире этой задачи: остановись и сообщи NEEDS_CONTEXT с парой и выводом `parse_expected_answer`.

Run: `.venv313/Scripts/python -m pytest -q` и `.venv313/Scripts/python -m ruff check .`
Expected: всё зелёное.

- [ ] **Step 8: HANDOVER** — в `docs/HANDOVER.md` найти абзац, начинающийся с `**Чипсы в приветствии**`, и после его SQL-блока и строки «Разговор, где человек только поздоровался и ушёл, в `started` не попадает: событий на голом приветствии нет.» вставить:

```markdown

**Кнопки на вопросах анкеты** задаются в `routing/matrix.yaml` полем
`quick_replies` у закрытого поля (`bool` или `enum`), не больше четырёх:
`{label, value}`, где `value` уходит боту обычным сообщением. Показываются,
когда бот спросил на ходе ровно это одно поле. Текст кнопки обязан
разбираться быстрым разбором без модели: новую кнопку добавляйте и в таблицу
`EXPECTED` в `tests/test_matrix_quick_replies.py` — тест проверит разбор.
Нажатия на вопросах анкеты в аналитику не пишутся.
```

- [ ] **Step 9: Commit**

```bash
git add routing/schema.py routing/matrix.yaml routing/normalize.py interpretation/fastpath.py tests/test_matrix_quick_replies.py docs/HANDOVER.md
git commit -m "Кнопки на закрытых вопросах анкеты в матрице и их разбор без модели"
```

---

### Task 2: Правило показа кнопок вопроса

**Files:**
- Modify: `quick_replies.py` — докстрока модуля, импорт `routing`, `quick_replies`, новая `_question_chips`
- Modify: `tests/test_quick_replies.py` — тесты правила
- Create: `tests/test_quick_replies_questionnaire.py` — сквозной ход

**Interfaces:**
- Consumes: `FieldSpec.quick_replies` (Task 1); `entry_routing.questionnaire_active(state) -> bool`; `core.client_is_saying_goodbye(user_message: str) -> bool`; `routing.get_matrix() -> Matrix`.
- Produces: `quick_replies.quick_replies(state: dict[str, Any], *, enabled: bool, matrix: Matrix | None = None) -> list[dict[str, str]]` — прежняя сигнатура плюс необязательная `matrix`; `main.py` вызывает без неё.

- [ ] **Step 1: Написать падающие тесты правила** — в конец `tests/test_quick_replies.py` добавить:

```python
CREDITS = [
    {"label": "Да, есть", "value": "Да, есть"},
    {"label": "Нет", "value": "Нет"},
]


def _questionnaire_state(
    asked,
    *,
    client_messages=("Иван",),
    answer_in_flight=False,
    entry=None,
) -> dict:
    history = [{"role": "assistant", "content": prompts.OPENING_MESSAGE}]
    for message in client_messages:
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": "Есть ли сейчас кредиты?"})
    if answer_in_flight:
        history.append({"role": "user", "content": "Да"})
    state = {"chat_history": history, "script": {"asked": list(asked)}}
    if entry is not None:
        state["entry"] = entry
    return state


def test_single_closed_question_offers_its_chips(matrix):
    state = _questionnaire_state(["debts.has_active_credits"])

    assert qr.quick_replies(state, enabled=True, matrix=matrix) == CREDITS


def test_two_questions_at_once_get_no_chips(matrix):
    state = _questionnaire_state(["request.amount", "client.age"])

    assert qr.quick_replies(state, enabled=True, matrix=matrix) == []


def test_question_without_chips_gets_none(matrix):
    state = _questionnaire_state(["client.region_class"])

    assert qr.quick_replies(state, enabled=True, matrix=matrix) == []


def test_no_chips_while_the_answer_is_on_its_way(matrix):
    state = _questionnaire_state(["debts.has_active_credits"], answer_in_flight=True)

    assert qr.quick_replies(state, enabled=True, matrix=matrix) == []


def test_entry_branches_get_no_questionnaire_chips(matrix):
    state = _questionnaire_state(
        ["debts.has_active_credits"],
        entry={"scenario": "off_topic", "intent": "off_topic", "source": "model"},
    )

    assert qr.quick_replies(state, enabled=True, matrix=matrix) == []


def test_farewell_gets_no_chips_of_the_previous_question(matrix):
    """На прощании script.asked не обновляется — кнопки прошлого вопроса не показываем."""
    import core

    farewell = "Спасибо, всего доброго"
    assert core.client_is_saying_goodbye(farewell), "предпосылка: это прощание"
    state = _questionnaire_state(
        ["debts.has_active_credits"], client_messages=("Иван", farewell)
    )

    assert qr.quick_replies(state, enabled=True, matrix=matrix) == []


def test_questionnaire_chips_obey_the_flag(matrix):
    state = _questionnaire_state(["debts.has_active_credits"])

    assert qr.quick_replies(state, enabled=False, matrix=matrix) == []
```

Создать `tests/test_quick_replies_questionnaire.py`:

```python
"""Кнопки вопроса анкеты после настоящего хода бота — и нажатие без модели.

Спека docs/superpowers/specs/2026-09-14-quick-replies-questionnaire-design.md.
"""
from __future__ import annotations

import pytest

import core
import prompts
import quick_replies
from config import AppConfig
from routing import plan as build_plan
from state import init_state
from utils import set_by_path


LEGACY = AppConfig(openrouter_api_key="sk-or-test", fact_interpretation_mode="legacy")
CREDITS = [
    {"label": "Да, есть", "value": "Да, есть"},
    {"label": "Нет", "value": "Нет"},
]


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.delenv("MBK_DADATA_API", raising=False)
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *a, **k: {"fact_updates": [], "internal_summary": ""},
    )


def _state_before_the_credits_question() -> dict:
    state = init_state()
    state["session_id"] = "questionnaire-chips"
    for path, value in {
        "client.name": "Иван",
        "client.display_name": "Иван",
        "request.amount": 500_000,
        "client.age": 40,
        "client.region_class": "rf_other",
        "client.city": "Воронеж",
    }.items():
        set_by_path(state["facts"], path, value)
    state["chat_history"] = [
        {"role": "assistant", "content": prompts.OPENING_MESSAGE},
        {"role": "user", "content": "Нужен кредит"},
        {"role": "assistant", "content": "В каком городе вы живёте?"},
    ]
    state["script"] = {"asked": ["client.region_class"]}
    return state


def _ask_about_credits(matrix) -> dict:
    state = _state_before_the_credits_question()
    assert build_plan(matrix, state["facts"]).field_paths == [
        "debts.has_active_credits"
    ], "предпосылка: следующим бот спрашивает о кредитах"
    prepared = core.prepare_turn(state, "Воронеж", LEGACY, submit_delivery=False)
    committed = core.commit_turn(
        prepared, "Воронеж", "Есть ли сейчас кредиты или займы?"
    )
    return committed["current_state"]


def test_question_about_credits_offers_yes_no_chips(offline, matrix):
    state = _ask_about_credits(matrix)

    assert state["script"]["asked"] == ["debts.has_active_credits"]
    assert quick_replies.quick_replies(state, enabled=True, matrix=matrix) == CREDITS


def test_pressing_a_chip_is_answered_without_the_model(offline, monkeypatch, matrix):
    state = _ask_about_credits(matrix)

    def unexpected(*args, **kwargs):
        raise AssertionError("нажатие кнопки разбирается без модели")

    monkeypatch.setattr(core.llm_agent, "extract_facts", unexpected)
    monkeypatch.setattr(core.llm_agent, "extract_fact_candidates", unexpected)

    answered = core.prepare_turn(state, "Да, есть", LEGACY, submit_delivery=False)

    assert answered["facts"]["debts"]["has_active_credits"] is True
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv313/Scripts/python -m pytest tests/test_quick_replies.py tests/test_quick_replies_questionnaire.py -q`
Expected: FAIL — `TypeError: quick_replies() got an unexpected keyword argument 'matrix'`. Тест `test_pressing_a_chip_is_answered_without_the_model` может уже проходить (разбор сделан в Task 1) — это регрессионная проверка.

Если падает предпосылка `build_plan(...).field_paths == ["debts.has_active_credits"]` или `state["script"]["asked"]`, поправь только исходные факты/историю в `_state_before_the_credits_question`, чтобы следующим вопросом стали кредиты; проверки кнопок не ослабляй. Если добиться не удаётся — NEEDS_CONTEXT с выводом плана.

- [ ] **Step 3: Реализация** — в `quick_replies.py`:

Заменить докстроку модуля (первые строки файла до `from __future__`) на:

```python
"""Чипсы — быстрые ответы под репликой бота.

Спеки: docs/superpowers/specs/2026-09-14-quick-replies-greeting-design.md
(приветствие) и 2026-09-14-quick-replies-questionnaire-design.md (вопросы
анкеты). Пока клиент не написал ни слова, под первой репликой бота —
«Получить деньги» и «Другой вопрос»: 137 человек открыли чат и ушли молча.
Дальше кнопки есть у закрытых вопросов анкеты — они заданы в matrix.yaml
полем `quick_replies`.

Кнопки не хранятся в состоянии, а вычисляются из него, и устаревшей кнопки не
бывает. Нажатие приходит обычным сообщением с текстом кнопки — тем же путём,
что набранный текст.
"""
```

После строки `import analytics` добавить:

```python
from routing import Matrix, get_matrix
```

Заменить функцию `quick_replies` целиком на:

```python
def quick_replies(
    state: dict[str, Any],
    *,
    enabled: bool,
    matrix: Matrix | None = None,
) -> list[dict[str, str]]:
    """Кнопки под последней репликой бота: `label` и `value`, по порядку.

    Пока клиент не написал ни слова — кнопки приветствия. Дальше — кнопки
    вопроса анкеты, заданного последним ходом. Пусто, если чипсы выключены или
    кнопок у вопроса нет.
    """
    if not enabled:
        return []
    if not _client_has_spoken(state):
        return [
            {"label": chip["label"], "value": chip["value"]}
            for chip in GREETING_CHIPS
        ]
    return _question_chips(state, matrix or get_matrix())


def _question_chips(state: dict[str, Any], matrix: Matrix) -> list[dict[str, str]]:
    """Кнопки вопроса анкеты, о котором бот спросил последним ходом.

    `script.asked` пишет `core.commit_turn` — ровно те поля, о которых бот
    спросил. Кнопки есть, только если поле одно: быстрый разбор узнаёт
    короткий ответ без модели лишь в этом случае, а кнопка на паре вопросов
    ответила бы на половину.
    """
    # Импорт здесь, а не наверху модуля: core и entry_routing сами импортируют
    # quick_replies, а к моменту вызова оба уже загружены.
    import entry_routing
    from core import client_is_saying_goodbye

    history = [item for item in state.get("chat_history") or [] if isinstance(item, dict)]
    if not history or history[-1].get("role") != "assistant":
        return []
    if not entry_routing.questionnaire_active(state):
        return []
    last_client = next(
        (item.get("content") for item in reversed(history) if item.get("role") == "user"),
        "",
    )
    # Прощание — не ответ на вопрос: на таком ходу `script.asked` не
    # обновляется, и под «Всего доброго» висели бы кнопки прошлого вопроса.
    if client_is_saying_goodbye(str(last_client or "")):
        return []
    asked = (state.get("script") or {}).get("asked") or []
    if len(asked) != 1:
        return []
    spec = matrix.fields.get(asked[0])
    if spec is None or not spec.quick_replies:
        return []
    return [{"label": chip.label, "value": chip.value} for chip in spec.quick_replies]
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv313/Scripts/python -m pytest tests/test_quick_replies.py tests/test_quick_replies_questionnaire.py tests/test_public_state_quick_replies.py tests/test_entry_other_question.py -q`
Expected: PASS.

Run: `.venv313/Scripts/python -m pytest -q` и `.venv313/Scripts/python -m ruff check .`
Expected: всё зелёное. Если падает `tests/test_dockerfile.py` из-за импортов — модули `routing`, `core`, `entry_routing` уже копируются в образ; сообщи вывод теста в отчёте, не меняй Dockerfile без причины.

- [ ] **Step 5: Commit**

```bash
git add quick_replies.py tests/test_quick_replies.py tests/test_quick_replies_questionnaire.py
git commit -m "Кнопки вопроса анкеты под репликой бота"
```

---

### Task 3 (контроллер): приёмка

- [ ] **Step 1: Проверки** — `.venv313/Scripts/python -m ruff check .`; `.venv313/Scripts/python -m pytest -q`; `cd site_widget && npm test` (виджет не менялся — контроль). Expected: всё зелёное.
- [ ] **Step 2: Превью** — в scratchpad-превью виджета подставной ответ на «Получить деньги» — вопрос «Есть ли сейчас кредиты или займы?» с кнопками «Да, есть» / «Нет»; снимок панели, отправить человеку.
- [ ] **Step 3: Финальное ревью** — общее по обоим планам чипсов (приветствие и вопросы анкеты), диапазон от коммита спеки чипсов до HEAD; отложенные мелочи из журналов обоих планов.
- [ ] **Step 4: Память** — `docs/agent-memory/SESSION.md`: итог обоих блоков, что осталось до поставки. Commit `docs(memory): чипсы в приветствии и на вопросах анкеты — итоги`.
