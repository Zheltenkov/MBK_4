"""Ссылка на офер с параметрами согласия.

Схема заказчика от 21.08.2026: когда квалификация вернула
`need_site_consent = true`, лид не отправляется, а клиент получает ссылку на
офер-страницу. К ссылке дописываются четыре параметра, по которым сайт потом
позовёт наш эндпоинт и принесёт согласие.

Дописываем к той ссылке, которую вернула CRM, — какая бы она ни была. Поэтому
здесь проверяется не «красивый адрес», а грязные случаи: свой хвост запроса,
якорь, кириллица в имени, повторный вызов.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from delivery.offer_link import build_offer_link


CALLBACK = "https://www.mbk.ru/api/chat-consent"
THANKS = "https://www.mbk.ru/thanks"


def params(url: str) -> dict[str, str]:
    return {key: value[0] for key, value in parse_qs(urlsplit(url).query).items()}


def test_all_four_parameters_are_appended():
    url = build_offer_link(
        "https://info.mbk.ru/bfl",
        session_id="a1b2c3d4e5f6",
        callback_url=CALLBACK,
        thanks_url=THANKS,
        name="Пётр",
    )

    assert params(url) == {
        "d_lead_id": "a1b2c3d4e5f6",
        "url": CALLBACK,
        "thanks_url": THANKS,
        "fio": "Пётр",
    }


def test_the_offer_own_query_survives():
    url = build_offer_link(
        "https://info.mbk.ru/bfl?utm_source=chat&promo=1",
        session_id="a1b2c3d4e5f6",
        callback_url=CALLBACK,
        thanks_url=THANKS,
        name="Пётр",
    )

    got = params(url)
    assert got["utm_source"] == "chat"
    assert got["promo"] == "1"
    assert got["d_lead_id"] == "a1b2c3d4e5f6"


def test_the_anchor_stays_at_the_end():
    url = build_offer_link(
        "https://info.mbk.ru/bfl#form",
        session_id="a1b2c3d4e5f6",
        callback_url=CALLBACK,
        thanks_url=THANKS,
        name="Пётр",
    )

    assert url.endswith("#form")
    assert params(url)["d_lead_id"] == "a1b2c3d4e5f6"


def test_the_name_is_encoded_and_readable_back():
    url = build_offer_link(
        "https://info.mbk.ru/bfl",
        session_id="a1b2c3d4e5f6",
        callback_url=CALLBACK,
        thanks_url=THANKS,
        name="Пётр Иванович",
    )

    assert " " not in urlsplit(url).query
    assert params(url)["fio"] == "Пётр Иванович"


def test_a_missing_name_leaves_the_parameter_out():
    """Пустое «fio=» на странице выглядит как обращение к пустому месту."""
    url = build_offer_link(
        "https://info.mbk.ru/bfl",
        session_id="a1b2c3d4e5f6",
        callback_url=CALLBACK,
        thanks_url=THANKS,
        name=None,
    )

    assert "fio" not in params(url)


def test_appending_twice_does_not_double_the_parameters():
    """Повторная выдача ссылки в том же диалоге — обычное дело."""
    once = build_offer_link(
        "https://info.mbk.ru/bfl",
        session_id="a1b2c3d4e5f6",
        callback_url=CALLBACK,
        thanks_url=THANKS,
        name="Пётр",
    )
    twice = build_offer_link(
        once,
        session_id="a1b2c3d4e5f6",
        callback_url=CALLBACK,
        thanks_url=THANKS,
        name="Пётр",
    )

    assert once == twice


@pytest.mark.parametrize("offer", [None, "", "   "])
def test_without_an_offer_there_is_no_link(offer):
    """Пустой адрес не превращаем в ссылку с параметрами."""
    assert build_offer_link(
        offer,
        session_id="a1b2c3d4e5f6",
        callback_url=CALLBACK,
        thanks_url=THANKS,
        name="Пётр",
    ) is None


def test_the_session_id_is_required():
    """Без него сайту нечего прислать обратно — ссылка бессмысленна."""
    assert build_offer_link(
        "https://info.mbk.ru/bfl",
        session_id="",
        callback_url=CALLBACK,
        thanks_url=THANKS,
        name="Пётр",
    ) is None
