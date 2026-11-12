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
- OFF_TOPIC — вопрос не про деньги и долги. Честный ответ и выход, лид не
  создаётся (`lead_blocked`).

Возврат в анкету открыт всегда: названные в ветке деньги возвращают в
QUESTIONNAIRE, и больше разговор из анкеты не уходит.
"""
from __future__ import annotations

import re
from typing import Any

import analytics
from context_policy import context_delivery_held
from debt_policy import debt_delivery_held
from routing.entry_intent import EntryIntent, classify_by_dictionary
from quick_replies import chip_choice


QUESTIONNAIRE = "questionnaire"
EXISTING = "existing"
OFF_TOPIC = "off_topic"
_BRANCHES = frozenset({EXISTING, OFF_TOPIC})

# Ключ с подчёркивания живёт один ход и в базу не уезжает
# (`session_store._persistable_state`). `core.commit_turn` забирает его и пишет
# событие — только за ход, который действительно состоялся.
TRANSITION_KEY = "_entry_transition"

# Голое приветствие первой репликой откладывает решение на ход. Ключ живёт
# один ход, как и TRANSITION_KEY: `core.turn_instruction` отдаёт по нему
# инструкцию приветствия, `core.commit_turn` забирает его.
GREETING_KEY = "_entry_greeting"

# Первая реплика клиента — нажатая кнопка «Другой вопрос» (спека чипсов
# 14.09.2026). Вопроса ещё нет, и решение о ветке ждёт следующей реплики, как
# после голого приветствия. Живёт один ход, как GREETING_KEY.
OTHER_QUESTION_KEY = "_entry_other_question"

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


def holds_for_other_question(state: dict[str, Any], user_message: str) -> bool:
    """Первая реплика клиента — кнопка «Другой вопрос»: решение ждёт вопроса.

    Флаги чипсов и развилки проверяет вызывающий (`core.prepare_turn`).
    `state` — состояние до хода.
    """
    return not _client_messages(state) and chip_choice(user_message) == "other"


def next_scenario(
    current: str | None,
    intent: EntryIntent,
    *,
    first_message: bool,
    after_other_question: bool = False,
) -> str:
    """Сценарий после этой реплики. Вся политика развилки — здесь.

    `after_other_question` — человек первой репликой нажал «Другой вопрос» и в
    этой реплике не назвал денег. Тогда неясное — не наш профиль: кнопка
    «Получить деньги» была рядом, и он её не выбрал.
    """
    if first_message:
        if intent is EntryIntent.EXISTING:
            return EXISTING
        if intent is EntryIntent.OFF_TOPIC:
            return OFF_TOPIC
        if intent is EntryIntent.UNCLEAR and after_other_question:
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


def reconcile_intent(
    intent: EntryIntent | None,
    *,
    money_signal: bool,
) -> EntryIntent | None:
    """Сверяет намерение с фактами того же ответа модели.

    «Не наш профиль» при найденных в том же ответе деньгах или долгах —
    противоречие, то есть сомнение, а при сомнении разговор идёт в анкету
    (fail-open). Только для off_topic: existing важнее money, и сумма в
    «оставлял заявку на 500 тысяч» его не отменяет.
    """
    if intent is EntryIntent.OFF_TOPIC and money_signal:
        return EntryIntent.UNCLEAR
    return intent


def current_scenario(state: dict[str, Any]) -> str:
    """Сценарий из state; отсутствующий или битый читается как анкета."""
    entry = state.get("entry")
    scenario = entry.get("scenario") if isinstance(entry, dict) else None
    return scenario if scenario in _BRANCHES else QUESTIONNAIRE


def questionnaire_active(state: dict[str, Any]) -> bool:
    return current_scenario(state) == QUESTIONNAIRE


def lead_blocked(state: dict[str, Any]) -> bool:
    """В ветках развилки лид не создаётся.

    «Уже оставлял заявку»: второй лид — дубль карточки, именно его ветка и
    убирает (спека, компонент 2). «Не наш профиль»: честный ответ и выход —
    телефон, названный там («уберите мой номер из базы обзвона»), не должен
    через 15 минут тишины уйти лидом. Вернувшийся в анкету назвал деньги, и
    лид ему положен. Проверяют все три дороги к лиду — CRM workflow,
    брошенную анкету по таймеру и локальную доставку.
    """
    return context_delivery_held(state) or debt_delivery_held(state) or current_scenario(state) != QUESTIONNAIRE


def close_branch(state: dict[str, Any]) -> None:
    """Отмечает, что разговор в ветке закончен прощанием.

    На следующее пустое «спасибо» бот молчит — так же, как после финала
    анкеты (`core.conversation_is_over`). Реплика по делу проходит обычный
    ход, и `route_turn` переписывает `state["entry"]` — признак сбрасывается.
    """
    entry = state.get("entry")
    if isinstance(entry, dict) and entry.get("scenario") in _BRANCHES:
        entry["closed"] = True


def branch_closed(state: dict[str, Any]) -> bool:
    entry = state.get("entry")
    return (
        isinstance(entry, dict)
        and entry.get("scenario") in _BRANCHES
        and entry.get("closed") is True
    )


def _pressed_other_question(state: dict[str, Any]) -> bool:
    """Первой репликой человек нажал кнопку «Другой вопрос»."""
    messages = _client_messages(state)
    return bool(messages) and chip_choice(messages[0]) == "other"


def route_turn(
    state: dict[str, Any],
    *,
    user_message: str,
    model_intent: EntryIntent | None,
    first_message: bool,
    money_signal: bool = False,
) -> None:
    """Решает сценарий хода и кладёт его в `state["entry"]`.

    Намерение берётся у модели; словарь — страховка, когда модель не звали,
    она упала или поля не вернула. `money_signal` — модель нашла в этой же
    реплике деньги или долги: тогда неясное не уводим из анкеты даже после
    кнопки «Другой вопрос».
    """
    entry = state.get("entry")
    current = entry.get("scenario") if isinstance(entry, dict) else None
    if not first_message and current not in _BRANCHES:
        return
    # A clear first greeting choice outranks an inconsistent model classification.
    explicit_choice = chip_choice(user_message) if first_message else None
    if explicit_choice in {"money", "existing"}:
        intent = EntryIntent.MONEY if explicit_choice == "money" else EntryIntent.EXISTING
        source = "dictionary"
    elif model_intent is not None:
        intent, source = model_intent, "model"
    else:
        intent, source = classify_by_dictionary(user_message), "dictionary"
    scenario = next_scenario(
        current,
        intent,
        first_message=first_message,
        after_other_question=(
            first_message and not money_signal and _pressed_other_question(state)
        ),
    )
    decision = {"scenario": scenario, "intent": intent.value, "source": source}
    state["entry"] = decision
    if first_message or scenario != current:
        state[TRANSITION_KEY] = dict(decision)


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


# Просьба отменить заявку, не звонить, удалить номер или данные. Бой
# 28.09.2026: «я передумала, уберите мой номер из базы». Бот этого не умеет и
# честно так говорит (`prompts.CANCEL_REQUEST_RULE`), но само обращение
# терялось. Ответ бизнеса 30.09.2026: «нужен процесс создания таких
# обращений» — до процесса их хотя бы видно в аналитике.
#
# Узко намеренно: глагол отмены обязан стоять рядом с «заявкой», «номером» или
# «данными». Голое «передумал» — это ещё и «передумал, нужно не 300, а 500».
_CANCEL_OBJECT = r"(?:заявк\w*|номер\w*|данны\w*|анкет\w*|телефон\w*)"
_CANCEL_VERB = r"(?:отмен\w*|аннулир\w*|удал\w*|убер\w*|отозва\w*|отзов\w*|стер\w*|сотри\w*)"
_CANCEL_RE = re.compile(
    rf"(?:{_CANCEL_VERB}\s+(?:\w+\s+){{0,3}}{_CANCEL_OBJECT}"
    rf"|{_CANCEL_OBJECT}\s+(?:\w+\s+){{0,2}}{_CANCEL_VERB}"
    r"|не\s+(?:надо\s+|нужно\s+)?(?:мне\s+)?(?:больше\s+)?звон(?:ить|ите)"
    r"|не\s+звон(?:ить|ите))",
    re.IGNORECASE,
)


def client_asks_to_cancel(message: str) -> bool:
    return bool(_CANCEL_RE.search(message or ""))


def record_cancel_request(state: dict[str, Any], user_message: str) -> None:
    """Отмечает просьбу об отмене в аналитике. Никогда не влияет на ответ.

    Один раз за сессию. В payload только признаки, по которым обращение можно
    найти и разобрать: назван ли телефон и ушёл ли лид. Самих данных нет —
    они лежат в `sessions`, куда у аналитика доступа нет.
    """
    if not client_asks_to_cancel(user_message):
        return
    session_id = str(state.get("session_id") or "")
    if not session_id:
        return
    lead = state.get("crm_lead") or {}
    analytics.get_recorder().emit(
        analytics.EVENTS.CANCEL_REQUESTED,
        session_id,
        {
            "phone_given": _has_phone(state),
            "lead_sent": lead.get("status") == "submitted",
            "turn": state.get("message_count"),
        },
        dedup_key=f"{session_id}:cancel_requested",
    )
