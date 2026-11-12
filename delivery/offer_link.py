"""Ссылка на офер-страницу с параметрами согласия.

Схема заказчика от 21.08.2026: при `need_site_consent = true` лид не уходит
сразу. Клиент получает ссылку на офер, жмёт там кнопку, сайт зовёт наш
эндпоинт и приносит согласие — и только тогда лид отправляется.

Чтобы сайт знал, о ком речь и куда отвечать, к ссылке дописываются четыре
параметра. Дописываем к тому адресу, который вернула CRM, каким бы он ни был:
у него может быть свой хвост запроса и якорь, и ломать их нельзя.
"""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


# Имена параметров продиктованы заказчиком дословно — переименовывать нельзя,
# их читает страница на его стороне.
LEAD_PARAM = "d_lead_id"
CALLBACK_PARAM = "url"
THANKS_PARAM = "thanks_url"
NAME_PARAM = "fio"


def build_offer_link(
    offer_url: str | None,
    *,
    session_id: str,
    callback_url: str,
    thanks_url: str | None,
    name: str | None,
) -> str | None:
    """Дописывает параметры согласия к адресу офера.

    Возвращает None, когда ссылки нет или некому отвечать: без адреса и без
    номера сессии страница не сможет ни показать кнопку, ни сообщить нам о
    нажатии, а «ссылка в никуда» хуже её отсутствия.
    """
    if not offer_url or not offer_url.strip() or not session_id:
        return None

    parts = urlsplit(offer_url.strip())
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query[LEAD_PARAM] = session_id
    query[CALLBACK_PARAM] = callback_url
    if thanks_url:
        query[THANKS_PARAM] = thanks_url
    if name and name.strip():
        # Пустое «fio=» на странице выглядит как обращение к пустому месту.
        query[NAME_PARAM] = name.strip()

    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )
