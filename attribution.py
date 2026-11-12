"""Рекламная атрибуция: метки страницы, с которой клиент пришёл в чат.

Виджет висит на лендинге, куда ведёт реклама. Менеджеру в CRM нужно видеть,
какая кампания привела клиента, иначе лид из чата неотличим от лида «ниоткуда»
и рекламу нечем оценивать.

Метки приходят снаружи, из браузера, и доверия к ним нет: значения попадают в
CRM как есть. Поэтому берётся закрытый список ключей, каждое значение режется по
длине и приводится к строке. Всё остальное молча отбрасывается — атрибуция
никогда не важнее разговора.
"""
from __future__ import annotations

from typing import Any


# Ключ -> предельная длина значения. Границы взяты по полям CRM: длинная строка
# не должна валить создание лида на стороне backend.
UTM_KEYS: dict[str, int] = {
    "utm_source": 255,
    "utm_medium": 255,
    "utm_campaign": 255,
    "utm_term": 255,
    "utm_content": 255,
    "landing": 1000,
    # Идентификаторы счётчиков аналитики — сырые значения кук _ga и _ym_uid,
    # ровно как их шлёт анкета сайта. Без них чат-лид выпадает из сквозной
    # аналитики GA/Метрики.
    "ga_client_id": 255,
    "ym_client_id": 64,
}


def clean_utm(raw: Any) -> dict[str, str] | None:
    """Оставляет от присланных меток только known-good подмножество.

    Возвращает None, если полезного не осталось: пустой словарь в состоянии
    сессии выглядел бы как «атрибуция была», хотя её не было.
    """
    if not isinstance(raw, dict):
        return None
    cleaned: dict[str, str] = {}
    for key, limit in UTM_KEYS.items():
        value = raw.get(key)
        if value is None or isinstance(value, (dict, list, tuple, set, bool)):
            continue
        text = str(value).strip()
        if not text:
            continue
        cleaned[key] = text[:limit]
    return cleaned or None
