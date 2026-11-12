"""Чипсы — быстрые ответы под репликой бота.

Спеки: docs/superpowers/specs/2026-09-14-quick-replies-greeting-design.md
(приветствие) и 2026-09-14-quick-replies-questionnaire-design.md (вопросы
анкеты). Пока клиент не написал ни слова, под первой репликой бота — четыре
варианта: деньги, долги, уже оставленная заявка и другой вопрос.
Дальше кнопки есть у закрытых вопросов анкеты — они заданы в matrix.yaml
полем `quick_replies`.

Кнопки не хранятся в состоянии, а вычисляются из него, и устаревшей кнопки не
бывает. Нажатие приходит обычным сообщением с текстом кнопки — тем же путём,
что набранный текст.
"""
from __future__ import annotations

from typing import Any

import analytics
from debt_policy import debt_delivery_held
from routing import Matrix, get_matrix


# Кнопки приветствия, по порядку показа. `choice` — код для аналитики и для
# разбора нажатия; клиенту уходят только `label` и `value`.
GREETING_CHIPS: tuple[dict[str, str], ...] = (
    {"label": "Получить деньги", "value": "Получить деньги", "choice": "money"},
    {"label": "Разобраться с долгами", "value": "Разобраться с долгами", "choice": "debt"},
    {"label": "Я уже оставлял заявку", "value": "Я уже оставлял заявку", "choice": "existing"},
    {"label": "Другой вопрос", "value": "Другой вопрос", "choice": "other"},
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


def quick_replies(
    state: dict[str, Any],
    *,
    enabled: bool,
    debt_enabled: bool = True,
    matrix: Matrix | None = None,
) -> list[dict[str, str]]:
    """Кнопки под последней репликой бота: `label` и `value`, по порядку.

    Пока клиент не написал ни слова — кнопки приветствия. Дальше — кнопки
    вопроса анкеты, заданного последним ходом. Пусто, если чипсы выключены или
    кнопок у вопроса нет.
    """
    # The debt policy defines its own questions and currently supplies text answers.
    if not enabled or debt_delivery_held(state):
        return []
    if not _client_has_spoken(state):
        return [
            {"label": chip["label"], "value": chip["value"]}
            for chip in GREETING_CHIPS
            if debt_enabled or chip["choice"] != "debt"
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
    # `questionnaire_active` читает отсутствующий `entry` как анкету. На ходах
    # удержания развилки — голое приветствие, «Другой вопрос» — решение ещё не
    # принято, и кнопок анкеты там нет только потому, что `script.asked` пуст.
    # Держат это tests/test_entry_greeting.py и tests/test_entry_other_question.py.
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
