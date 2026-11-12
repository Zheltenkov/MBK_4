"""Лид ждёт согласия, которое клиент даёт кнопкой на офер-странице.

Схема заказчика от 21.08.2026. При `need_site_consent = true` лид не уходит
сразу: клиент получает ссылку на офер, жмёт там кнопку, сайт зовёт наш
эндпоинт — и только тогда заявка отправляется с согласием. Не нажал за
пятнадцать минут — уходит без согласия, и роутер заказчика уводит такой лид в
колл-центр.

Одно отступление от буквы схемы держит первый тест: пауза имеет смысл, только
если CRM вернула настоящую офер-страницу. Когда ссылки нет, мы подставляем
общую благодарность, а на ней кнопки согласия нет — проверено 21.08.2026.
Держать лид в расчёте на такую страницу значит гарантированно записать клиенту
отказ, которого он не давал.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from crm_dialog_workflow import CrmDialogStatus
from tests.test_crm_dialog_workflow import (  # noqa: F401 — переиспользуем стенд
    FakeCrmClient,
    _session,
    _workflow,
)


def _later(minutes: int) -> datetime:
    """Момент относительно настоящих часов: пауза отмечается ими же."""
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


OFFER = "https://info.mbk.ru/bfl-region"


@pytest.fixture(autouse=True)
def pause_enabled(monkeypatch):
    """Схема включается флагом: пока на офере нет кнопки, ждать нечего."""
    monkeypatch.setenv("MBK_SITE_CONSENT_PAUSE_ENABLED", "1")


def _paused(**kwargs):
    """Диалог, дошедший до паузы: анкета пройдена, CRM просит согласие."""
    client = FakeCrmClient(need_site_consent=True, thanks_url=OFFER, **kwargs)
    session = _session()
    workflow = _workflow(client, [])
    state = workflow.advance(session, "")
    return client, session, workflow, state


# --------------------------------------------------------------------------- #
# Пауза
# --------------------------------------------------------------------------- #
def test_the_lead_waits_when_there_is_a_real_offer_page():
    client, session, _, state = _paused()

    assert state.status == CrmDialogStatus.AWAITING_SITE_CONSENT
    assert client.lead_requests == []
    assert session.state["dialog_phase"] == "awaiting_site_consent"


def test_the_offer_link_is_stored_with_the_consent_parameters():
    _, session, _, state = _paused()

    assert state.offer_url is not None
    assert f"d_lead_id={session.id}" in state.offer_url
    assert "chat-consent" in state.offer_url


def test_offer_link_sends_only_confirmed_display_name():
    client = FakeCrmClient(need_site_consent=True, thanks_url=OFFER)
    session = _session()
    # CRM test-gate требует специальное полное имя из _session; меняем только
    # независимое обращение, которое должно попасть в query.
    session.state["facts"]["client"]["display_name"] = "Иван"

    state = _workflow(client, []).advance(session, "")
    query = parse_qs(urlsplit(state.offer_url or "").query)

    assert query["fio"] == ["Иван"]


def test_without_a_real_offer_page_the_lead_goes_at_once():
    """Иначе клиент ждёт кнопку на странице, где её нет."""
    client = FakeCrmClient(need_site_consent=True, thanks_url=None)
    session = _session()

    state = _workflow(client, []).advance(session, "")

    assert state.status == CrmDialogStatus.SUBMITTED
    assert len(client.lead_requests) == 1
    assert client.lead_requests[0].agree_partners == 23002


def test_a_reply_in_the_chat_does_not_release_the_lead():
    """Согласие даёт сайт. Слова в чате его не заменяют."""
    client, session, workflow, _ = _paused()

    state = workflow.advance(session, "Да, согласен, отправляйте")

    assert state.status == CrmDialogStatus.AWAITING_SITE_CONSENT
    assert client.lead_requests == []


# --------------------------------------------------------------------------- #
# Нажатие кнопки
# --------------------------------------------------------------------------- #
def test_the_button_sends_the_lead_with_consent():
    client, session, workflow, _ = _paused()

    state = workflow.confirm_site_consent(session)

    assert state.status == CrmDialogStatus.SUBMITTED
    assert len(client.lead_requests) == 1
    assert client.lead_requests[0].agree_partners == 23000


def test_pressing_the_button_twice_creates_one_lead():
    """Повторный клик и двойная отправка формы — обычное дело."""
    client, session, workflow, _ = _paused()

    workflow.confirm_site_consent(session)
    state = workflow.confirm_site_consent(session)

    assert state.status == CrmDialogStatus.SUBMITTED
    assert len(client.lead_requests) == 1


def test_the_button_on_a_session_that_never_waited_changes_nothing():
    """Чужой или устаревший номер сессии не должен ничего отправлять."""
    client = FakeCrmClient(need_site_consent=False)
    session = _session()
    workflow = _workflow(client, [])
    workflow.advance(session, "")
    before = len(client.lead_requests)

    workflow.confirm_site_consent(session)

    assert len(client.lead_requests) == before


# --------------------------------------------------------------------------- #
# Таймаут
# --------------------------------------------------------------------------- #
def test_the_lead_goes_without_consent_after_the_timeout():
    client, session, workflow, _ = _paused()

    state = workflow.expire_site_consent(session, now=_later(6))

    assert state is not None
    assert state.status == CrmDialogStatus.SUBMITTED
    assert len(client.lead_requests) == 1
    assert client.lead_requests[0].agree_partners == 23001


def test_before_the_timeout_nothing_is_sent():
    client, session, workflow, _ = _paused()

    state = workflow.expire_site_consent(session, now=_later(4))

    assert state is None
    assert client.lead_requests == []


def test_a_late_click_after_the_timeout_does_not_send_a_second_lead():
    """Заказчик против пересоздания уже отправленного лида."""
    client, session, workflow, _ = _paused()
    workflow.expire_site_consent(session, now=_later(6))

    workflow.confirm_site_consent(session)

    assert len(client.lead_requests) == 1
    assert client.lead_requests[0].agree_partners == 23001


def test_the_click_wins_when_it_comes_first():
    client, session, workflow, _ = _paused()
    workflow.confirm_site_consent(session)

    workflow.expire_site_consent(session, now=_later(6))

    assert len(client.lead_requests) == 1
    assert client.lead_requests[0].agree_partners == 23000


@pytest.mark.parametrize("minutes", [0, 1, 4])
def test_the_pause_survives_short_gaps(minutes):
    client, session, workflow, _ = _paused()

    workflow.expire_site_consent(session, now=_later(minutes))

    assert client.lead_requests == []


def test_the_pause_is_off_until_the_page_is_ready(monkeypatch):
    """Без кнопки на странице ожидание только навредит: лид уйдёт позже и с отказом."""
    monkeypatch.delenv("MBK_SITE_CONSENT_PAUSE_ENABLED", raising=False)
    client = FakeCrmClient(need_site_consent=True, thanks_url=OFFER)
    session = _session()

    state = _workflow(client, []).advance(session, "")

    assert state.status == CrmDialogStatus.SUBMITTED
    assert client.lead_requests[0].agree_partners == 23002


# --------------------------------------------------------------------------- #
# Где кнопка есть, а где её нет
# --------------------------------------------------------------------------- #
# Механика заказчика от 25.08.2026, дословно: «виджет ставит куку
# mbk_chat_consent = session_id (домен www.mbk.ru). Клиент открывает
# /bfl-finish, жмёт кнопку → JS зовёт finishBflConfirm → guard читает куку и
# форвардит именно этот session_id боту. Сама страница про клиента ничего не
# знает — знает кука».
#
# Отсюда две границы. Кука видна только своему домену: офер на `info.mbk.ru`
# (залоговые маршруты) клиента не узнает. И guard стоит на конкретной странице,
# а не на всём домене.
BFL_FINISH = "https://www.mbk.ru/bfl-finish"


def test_the_default_offer_page_is_used_when_the_crm_has_none(monkeypatch):
    """У маршрутов БФЛ своей ссылки в матрице нет — кнопка на постоянной."""
    monkeypatch.setenv("MBK_SITE_CONSENT_DEFAULT_URL", BFL_FINISH)
    client = FakeCrmClient(need_site_consent=True, thanks_url=None)
    session = _session()

    state = _workflow(client, []).advance(session, "")

    assert state.status == CrmDialogStatus.AWAITING_SITE_CONSENT
    assert client.lead_requests == []
    assert (state.offer_url or "").startswith(BFL_FINISH)


def test_the_default_page_gets_no_consent_parameters(monkeypatch):
    """Backend заказчика 26.08.2026: параметры согласия своим страницам нельзя.

    Постоянная страница узнаёт клиента по куке `mbk_chat_consent`, которую
    ставит виджет. Параметр `d_lead_id` у неё занят под свой формат: её код
    декодирует его как base64 с числовым идентификатором лида и на нашем
    hex-номере сессии падает с 500. Единственное исключение — `fio`: его
    анкета сайта пробрасывает всегда, кнопка несёт его дальше на витрину и
    партнёрские «спасибо» (QA 28.08.2026: без fio клиент видел «Маргариту
    Ивановну» по всей цепочке).
    """
    monkeypatch.setenv("MBK_SITE_CONSENT_DEFAULT_URL", BFL_FINISH)
    client = FakeCrmClient(need_site_consent=True, thanks_url=None)

    state = _workflow(client, []).advance(_session(), "")

    query = parse_qs(urlsplit(state.offer_url or "").query)
    assert set(query) == {"fio"}
    assert query["fio"] == ["Тестов Тест Тестович"]


def test_a_page_from_the_crm_still_gets_the_parameters(monkeypatch):
    """Офер-страницы из матрицы читают клиента из адреса — там всё как было."""
    monkeypatch.setenv("MBK_SITE_CONSENT_DEFAULT_URL", BFL_FINISH)
    client = FakeCrmClient(need_site_consent=True, thanks_url=OFFER)
    session = _session()

    state = _workflow(client, []).advance(session, "")
    query = parse_qs(urlsplit(state.offer_url or "").query)

    assert query["d_lead_id"] == [session.id]
    assert "url" in query


def test_without_a_default_page_nothing_changes(monkeypatch):
    monkeypatch.delenv("MBK_SITE_CONSENT_DEFAULT_URL", raising=False)
    client = FakeCrmClient(need_site_consent=True, thanks_url=None)

    state = _workflow(client, []).advance(_session(), "")

    assert state.status == CrmDialogStatus.SUBMITTED


def test_an_offer_on_another_host_does_not_hold_the_lead(monkeypatch):
    """Кука с www.mbk.ru на info.mbk.ru не читается — кнопке нечем узнать клиента."""
    monkeypatch.setenv("MBK_SITE_CONSENT_BUTTON_PAGES", BFL_FINISH)
    client = FakeCrmClient(
        need_site_consent=True,
        thanks_url="https://info.mbk.ru/spasibo-zaim-zalog-pts",
    )

    state = _workflow(client, []).advance(_session(), "")

    assert state.status == CrmDialogStatus.SUBMITTED
    assert client.lead_requests[0].agree_partners == 23002


def test_the_listed_page_still_holds_the_lead(monkeypatch):
    monkeypatch.setenv("MBK_SITE_CONSENT_BUTTON_PAGES", BFL_FINISH)
    client = FakeCrmClient(
        need_site_consent=True, thanks_url=f"{BFL_FINISH}?utm_source=crm"
    )

    state = _workflow(client, []).advance(_session(), "")

    assert state.status == CrmDialogStatus.AWAITING_SITE_CONSENT
    assert client.lead_requests == []


def test_an_empty_list_does_not_restrict_anything(monkeypatch):
    monkeypatch.delenv("MBK_SITE_CONSENT_BUTTON_PAGES", raising=False)
    client = FakeCrmClient(
        need_site_consent=True,
        thanks_url="https://info.mbk.ru/spasibo-zaim-zalog-pts",
    )

    state = _workflow(client, []).advance(_session(), "")

    assert state.status == CrmDialogStatus.AWAITING_SITE_CONSENT


def test_state_with_sent_offer_link_survives_restart_validation():
    """Прод 27.08.2026: core.py пишет offer_link_sent в state мимо модели.

    Схема с extra="forbid" не знала этого поля, восстановление сессии в
    awaiting_site_consent падало на старте и валило весь процесс в crash-loop.
    """
    from crm_dialog_workflow import CrmDialogState

    state = CrmDialogState.model_validate(
        {
            "status": "awaiting_site_consent",
            "offer_url": "https://www.mbk.ru/bfl-finish",
            "offer_link_sent": True,
        }
    )

    assert state.offer_link_sent is True


def test_matrix_offer_link_does_not_point_thanks_back_to_itself():
    """thanks_url из матрицы совпадает с самим офером — в ссылку он не идёт.

    Иначе кнопка возвращала клиента на тот же офер уже без параметров
    (страница сама уводит на свою брендированную «спасибо», когда параметра
    нет — например /spasibo-nssd).
    """
    from delivery.offer_link import build_offer_link

    page = "https://info.mbk.ru/offer-auto-x"
    link = build_offer_link(
        page,
        session_id="a1b2c3d4e5f6",
        callback_url="https://www.mbk.ru/api/chat-consent",
        thanks_url=None,
        name="Тест",
    )

    assert link is not None
    query = parse_qs(urlsplit(link).query)
    assert "thanks_url" not in query
    assert query["d_lead_id"] == ["a1b2c3d4e5f6"]
    assert query["url"] == ["https://www.mbk.ru/api/chat-consent"]


def test_offer_page_map_overrides_the_matrix_url(monkeypatch):
    """Матрица хранит «URL спасибо» — страницу ПОСЛЕ согласия.

    У залога авто там витрина партнёров без кнопки. Адрес самой страницы
    подтверждения задаёт карта «категория=адрес»; витрина при этом занимает
    своё место — параметр thanks_url, куда кнопка уводит после нажатия.
    """
    monkeypatch.setenv(
        "MBK_SITE_CONSENT_OFFER_PAGES",
        "4705=https://info.mbk.ru/offer-auto",
    )
    client = FakeCrmClient(
        category=4705,
        need_site_consent=True,
        thanks_url="https://info.mbk.ru/spasibo-zaim-zalog-pts",
    )
    session = _session()

    state = _workflow(client, []).advance(session, "")

    assert state.status == CrmDialogStatus.AWAITING_SITE_CONSENT
    parts = urlsplit(state.offer_url or "")
    query = parse_qs(parts.query)
    assert parts.path == "/offer-auto"
    assert query["d_lead_id"] == [session.id]
    assert query["thanks_url"] == ["https://info.mbk.ru/spasibo-zaim-zalog-pts"]


def test_mapped_page_on_own_domain_goes_clean_like_the_default(monkeypatch):
    """Внутри домена алгоритм свой: кука, параметры не дописываются."""
    monkeypatch.setenv(
        "MBK_SITE_CONSENT_OFFER_PAGES",
        "4705=https://www.mbk.ru/auto-finish",
    )
    client = FakeCrmClient(
        category=4705,
        need_site_consent=True,
        thanks_url="https://info.mbk.ru/spasibo-zaim-zalog-pts",
    )
    session = _session()

    state = _workflow(client, []).advance(session, "")

    assert state.status == CrmDialogStatus.AWAITING_SITE_CONSENT
    # На свою страницу — кука плюс fio, как ведёт анкета (finishAuto).
    parts = urlsplit(state.offer_url or "")
    assert (parts.hostname, parts.path) == ("www.mbk.ru", "/auto-finish")
    assert parse_qs(parts.query) == {"fio": ["Тестов Тест Тестович"]}


def test_broken_offer_page_pairs_are_skipped(monkeypatch):
    from crm_dialog_workflow import site_consent_offer_pages

    monkeypatch.setenv(
        "MBK_SITE_CONSENT_OFFER_PAGES",
        "мусор,4705=https://info.mbk.ru/offer-auto, =x,4706=",
    )

    assert site_consent_offer_pages() == {4705: "https://info.mbk.ru/offer-auto"}


def test_bfl_categories_pause_on_the_built_in_default_map():
    """Дефолтная карта работает без env: БФЛ едет на /bfl-finish чистым."""
    client = FakeCrmClient(category=3895, need_site_consent=True, thanks_url=None)
    session = _session()

    state = _workflow(client, []).advance(session, "")

    assert state.status == CrmDialogStatus.AWAITING_SITE_CONSENT
    parts = urlsplit(state.offer_url or "")
    assert (parts.hostname, parts.path) == ("www.mbk.ru", "/bfl-finish")
    assert parse_qs(parts.query) == {"fio": ["Тестов Тест Тестович"]}


def test_offer_page_map_can_be_switched_off(monkeypatch):
    from crm_dialog_workflow import site_consent_offer_pages

    monkeypatch.setenv("MBK_SITE_CONSENT_OFFER_PAGES", "off")

    assert site_consent_offer_pages() == {}


def test_auto_categories_pause_on_the_anketa_confirmation_page():
    """Авто идёт как в анкете: /auto-finish с кукой, витрина — после кнопки."""
    client = FakeCrmClient(
        category=4705,
        need_site_consent=True,
        thanks_url="https://info.mbk.ru/spasibo-zaim-zalog-pts",
    )
    session = _session()

    state = _workflow(client, []).advance(session, "")

    assert state.status == CrmDialogStatus.AWAITING_SITE_CONSENT
    # На свою страницу — кука плюс fio, как ведёт анкета (finishAuto).
    parts = urlsplit(state.offer_url or "")
    assert (parts.hostname, parts.path) == ("www.mbk.ru", "/auto-finish")
    assert parse_qs(parts.query) == {"fio": ["Тестов Тест Тестович"]}
