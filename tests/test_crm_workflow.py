"""State decisions после ответа first-qualification."""
from __future__ import annotations

from delivery.crm_contracts import FirstQualificationResponse
from delivery.crm_workflow import decide_qualification


def _response(*, category: int | None, need_site_consent: bool, thanks_url=None):
    return FirstQualificationResponse.model_validate(
        {
            "success": True,
            "result": {
                "matched": category is not None,
                "priority": None,
                "suitable_categories": [],
                "segment": None,
                "partner": None,
                "offer": None,
                "suitable_offers": [],
                "need_partner_consent": True,
                "vertical": None,
                "category": category,
                "need_site_consent": need_site_consent,
                "thanks_url": thanks_url,
            },
        }
    )


def test_category_null_still_creates_lead():
    decision = decide_qualification(
        _response(category=None, need_site_consent=False)
    )

    assert decision.next_step == "create_lead"
    assert decision.should_create_lead is True
    assert decision.qualified is False


def test_site_consent_is_the_only_bot_consent_flag():
    decision = decide_qualification(
        _response(category=4700, need_site_consent=True)
    )

    assert decision.next_step == "ask_site_consent"
    assert decision.should_create_lead is True


def test_relative_thanks_path_is_bound_to_mbk_host():
    decision = decide_qualification(
        _response(
            category=4700,
            need_site_consent=False,
            thanks_url="/spasibo-zaim-zalog-pts/",
        )
    )

    assert decision.thanks_url == "https://www.mbk.ru/spasibo-zaim-zalog-pts/"


def test_external_https_thanks_url_is_accepted():
    decision = decide_qualification(
        _response(
            category=4700,
            need_site_consent=False,
            thanks_url="https://partner.example/offer",
        )
    )

    # Backend 24.08.2026: оффер может штатно находиться на чужом домене.
    assert decision.thanks_url == "https://partner.example/offer"


def test_insecure_thanks_url_is_logged_with_its_host(caplog):
    """«Ссылки нет» и «ссылку отбросили мы» в state выглядят одинаково.

    Оба случая дают thanks_url: null, и на первом живом лиде 10.08.2026
    разобрать причину было нечем. Путь в лог не пишем — в нём могут быть
    параметры конкретного обращения.
    """
    import logging

    from delivery.crm_workflow import decide_qualification

    with caplog.at_level(logging.WARNING):
        decision = decide_qualification(
            _response(
                category=2217,
                need_site_consent=False,
                thanks_url="http://partner.example.com/spasibo/?token=secret",
            )
        )

    assert "partner.example.com" not in (decision.thanks_url or "")
    assert "partner.example.com" in caplog.text
    assert "secret" not in caplog.text


def test_non_browser_schemes_and_missing_host_are_rejected():
    for unsafe_url in (
        "javascript:alert(1)",
        "data:text/html,boom",
        "https:///missing-host",
    ):
        decision = decide_qualification(
            _response(
                category=2217,
                need_site_consent=False,
                thanks_url=unsafe_url,
            )
        )

        assert decision.thanks_url == "https://www.mbk.ru/thanks?c=0"
        assert decision.offer_url is None


def test_allowed_thanks_url_is_not_logged(caplog):
    import logging

    from delivery.crm_workflow import decide_qualification

    with caplog.at_level(logging.WARNING):
        decision = decide_qualification(
            _response(
                category=2217,
                need_site_consent=False,
                thanks_url="/spasibo-zaim-zalog-pts/",
            )
        )

    assert decision.thanks_url == "https://www.mbk.ru/spasibo-zaim-zalog-pts/"
    assert caplog.text == ""


def test_auto_collateral_thanks_url_on_the_info_subdomain_is_allowed():
    """Ссылка залога авто живёт на info.mbk.ru — отдельном хосте.

    Матрица Андрея от 10.08.2026: «Залог авто (все варианты)» →
    https://info.mbk.ru/spasibo-zaim-zalog-pts. До этой правки список
    разрешённых доменов знал только mbk.ru и www.mbk.ru, поэтому единственная
    заполненная ссылка залоговой ветки молча превращалась в null, и клиент
    не получал кнопку вовсе.
    """
    from delivery.crm_workflow import decide_qualification

    decision = decide_qualification(
        _response(
            category=1101,
            need_site_consent=False,
            thanks_url="https://info.mbk.ru/spasibo-zaim-zalog-pts",
        )
    )

    assert decision.thanks_url == "https://info.mbk.ru/spasibo-zaim-zalog-pts"


def test_credit_club_thanks_url_is_allowed():
    from delivery.crm_workflow import decide_qualification

    decision = decide_qualification(
        _response(
            category=1102,
            need_site_consent=False,
            thanks_url="https://www.mbk.ru/thanks-credit-club",
        )
    )

    assert decision.thanks_url == "https://www.mbk.ru/thanks-credit-club"


def test_nested_foreign_host_is_allowed_when_crm_returns_it():
    """Домен определяет CRM; визуальное сходство с mbk.ru ничего не меняет."""
    from delivery.crm_workflow import decide_qualification

    decision = decide_qualification(
        _response(
            category=1101,
            need_site_consent=False,
            thanks_url="https://mbk.ru.evil.example/spasibo",
        )
    )

    assert decision.thanks_url == "https://mbk.ru.evil.example/spasibo"


def test_empty_thanks_url_falls_back_to_the_default_page():
    """Пустая ссылка означает дефолтную страницу благодарности, а не её отсутствие.

    Андрей 10.08.2026: «у категории 2217 ссылки в матрице нет и не было. Сайт в
    этом случае ведёт клиента на дефолтную страницу благодарности
    https://www.mbk.ru/thanks — используй её как фолбэк, тогда обещание бота
    „пришлю ссылку“ всегда выполнимо». Он же 17.08: «перезванивать никто не
    должен по залогу».

    Мы прочитали то письмо иначе — как «у части правил ссылки нет, там звонит
    менеджер», — и построили на этом целую ветку финала. В итоге клиент залога
    недвижимости уходил ждать звонка, которого никто не собирался делать.
    """
    decision = decide_qualification(
        _response(category=2217, need_site_consent=False, thanks_url=None)
    )

    assert decision.thanks_url == "https://www.mbk.ru/thanks?c=0"


def test_whitespace_thanks_url_also_falls_back_to_the_default_page():
    decision = decide_qualification(
        _response(category=2217, need_site_consent=False, thanks_url="   ")
    )

    assert decision.thanks_url == "https://www.mbk.ru/thanks?c=0"
    assert decision.offer_url is None


def test_an_insecure_url_falls_back():
    """Любой домен допустим, но небезопасную схему клиенту не показываем."""
    decision = decide_qualification(
        _response(
            category=2217,
            need_site_consent=False,
            thanks_url="http://partner.example/offer",
        )
    )

    assert decision.thanks_url == "https://www.mbk.ru/thanks?c=0"


def test_a_real_url_still_wins_over_the_default():
    decision = decide_qualification(
        _response(
            category=3075,
            need_site_consent=False,
            thanks_url="https://info.mbk.ru/spasibo-zaim-zalog-pts",
        )
    )

    assert decision.thanks_url == "https://info.mbk.ru/spasibo-zaim-zalog-pts"


# --------------------------------------------------------------------------- #
# Настоящий офер против подставленной страницы благодарности
# --------------------------------------------------------------------------- #
def test_the_offer_url_is_empty_when_crm_gave_nothing():
    """Дефолтная благодарность — не офер: кнопки согласия на ней нет.

    Проверено 21.08.2026: страница https://www.mbk.ru/thanks отдаёт общий
    шаблон сайта, ни `d_lead_id`, ни вызова consent-эндпоинта там нет. Если
    держать лид на паузе в расчёте на эту страницу, клиент никогда не нажмёт
    кнопку, и через пятнадцать минут в CRM уедет отказ, которого он не давал.
    """
    decision = decide_qualification(
        _response(category=2217, need_site_consent=True, thanks_url=None)
    )

    assert decision.thanks_url == "https://www.mbk.ru/thanks?c=0"
    assert decision.offer_url is None


def test_the_offer_url_is_the_page_crm_returned():
    decision = decide_qualification(
        _response(
            category=3075,
            need_site_consent=True,
            thanks_url="https://info.mbk.ru/spasibo-zaim-zalog-pts",
        )
    )

    assert decision.offer_url == "https://info.mbk.ru/spasibo-zaim-zalog-pts"


def test_a_foreign_https_host_is_a_valid_offer_url():
    decision = decide_qualification(
        _response(
            category=3075,
            need_site_consent=True,
            thanks_url="https://partner.example/offer",
        )
    )

    assert decision.offer_url == "https://partner.example/offer"
    assert decision.thanks_url == "https://partner.example/offer"


def test_unknown_fields_in_crm_response_do_not_break_qualification():
    """Прод 04–06.09.2026: CRM добавила result.widget_denis, и строгий
    контракт три дня ронял каждую квалификацию — 19 лидов потеряно."""
    from delivery.crm_contracts import FirstQualificationResponse

    response = FirstQualificationResponse.model_validate(
        {
            "success": True,
            "result": {
                "matched": True,
                "priority": "1-1",
                "category": 2217,
                "suitable_categories": [2217],
                "segment": None,
                "partner": None,
                "offer": None,
                "suitable_offers": [],
                "need_partner_consent": False,
                "need_site_consent": False,
                "widget_denis": {"anything": "new"},
                "another_future_field": 1,
            },
            "surprise": "field",
        }
    )

    assert response.result.category == 2217
