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
        r"\d+\s*(?:тыс|т\.р|[кk]\b|млн|миллион)",
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
