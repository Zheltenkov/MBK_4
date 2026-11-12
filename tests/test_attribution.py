"""Рекламные метки: фильтрация на входе и путь до payload лида."""
from __future__ import annotations

import asyncio

import pytest

from attribution import clean_utm
from delivery.crm_mapping import map_lead_intake
from session_store import SessionStore


def _ready_facts() -> dict:
    return {
        "client": {"name": "Иван Петров", "phone": "+79990000000"},
        "request": {"amount": 500_000},
    }


def test_unknown_marks_are_dropped():
    cleaned = clean_utm(
        {
            "utm_source": "yandex",
            "utm_campaign": "zalog",
            "password": "секрет",
            "session_id": "чужая сессия",
        }
    )
    assert cleaned == {"utm_source": "yandex", "utm_campaign": "zalog"}


def test_long_value_is_cut_to_the_field_limit():
    cleaned = clean_utm({"utm_campaign": "к" * 400, "landing": "l" * 2000})
    assert cleaned is not None
    assert len(cleaned["utm_campaign"]) == 255
    assert len(cleaned["landing"]) == 1000


@pytest.mark.parametrize(
    "raw",
    [None, {}, "utm_source=yandex", {"utm_source": ""}, {"utm_source": {"a": 1}}],
)
def test_nothing_useful_means_no_attribution(raw):
    assert clean_utm(raw) is None


def test_marks_reach_the_lead_payload():
    mapping = map_lead_intake(
        _ready_facts(),
        form_filled_out=True,
        site_consent_shown=False,
        attribution={
            "utm_campaign": "zalog_msk",
            "utm_term": "кредит под залог",
            "utm_content": "banner_3",
            "landing": "https://mbk.ru/zalog",
        },
    )
    assert mapping.ready
    request = mapping.request
    assert request is not None
    payload = request.model_dump(by_alias=True, exclude_none=True)
    assert payload["UTM_CAMPAIGN"] == "zalog_msk"
    assert payload["UTMKEY"] == "кредит под залог"
    assert payload["K50"] == "banner_3"
    assert payload["LANDING"] == "https://mbk.ru/zalog"
    # Источник и канал не зависят от рекламы: лид всё равно пришёл из чата.
    assert payload["UTMSOURCE"] == "crm"
    assert payload["ADV_CHANNEL"] == "bot"


def test_lead_without_marks_still_goes_out():
    """Клиент по прямой ссылке — метки отсутствуют, лид создаётся."""
    mapping = map_lead_intake(
        _ready_facts(),
        form_filled_out=True,
        site_consent_shown=False,
        attribution=None,
    )
    assert mapping.ready
    assert mapping.request is not None
    payload = mapping.request.model_dump(by_alias=True, exclude_none=True)
    assert payload["UTMSOURCE"] == "crm"
    assert payload["ADV_CHANNEL"] == "bot"
    assert "UTM_CAMPAIGN" not in payload
    assert "LANDING" not in payload


def test_session_keeps_marks_from_the_moment_it_is_created():
    session = asyncio.run(
        SessionStore().create(
            anketa=None,
            utm={"utm_source": "yandex", "utm_content": "banner_3", "junk": "x"},
        )
    )
    assert session.state["attribution"] == {
        "utm_source": "yandex",
        "utm_content": "banner_3",
    }


def test_session_without_marks_has_none():
    session = asyncio.run(SessionStore().create(anketa=None))
    assert session.state["attribution"] is None


def test_analytics_client_ids_survive_cleaning():
    """Куки _ga/_ym_uid едут теми же метками, что и у анкеты."""
    cleaned = clean_utm(
        {"ga_client_id": "GA1.2.111.222", "ym_client_id": "1712345", "чужое": "x"}
    )
    assert cleaned == {
        "ga_client_id": "GA1.2.111.222",
        "ym_client_id": "1712345",
    }
