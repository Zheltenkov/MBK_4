"""Точечные проверки сопоставления страниц."""
import pytest
from crm_dialog_workflow import page_has_consent_button

BFL = "https://www.mbk.ru/bfl-finish"


@pytest.fixture(autouse=True)
def only_bfl(monkeypatch):
    monkeypatch.setenv("MBK_SITE_CONSENT_BUTTON_PAGES", BFL)


@pytest.mark.parametrize(
    "url",
    [
        BFL,
        BFL + "/",
        BFL + "?d_lead_id=abc",
        BFL + "#form",
        "https://WWW.MBK.RU/bfl-finish",
    ],
)
def test_the_same_page_in_different_writings(url):
    assert page_has_consent_button(url)


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "https://info.mbk.ru/bfl-finish",
        "https://mbk.ru/bfl-finish",
        "https://www.mbk.ru/bfl-finish-old",
        "https://www.mbk.ru/thanks",
    ],
)
def test_other_pages_are_not_it(url):
    assert not page_has_consent_button(url)


def test_several_pages_are_allowed(monkeypatch):
    monkeypatch.setenv(
        "MBK_SITE_CONSENT_BUTTON_PAGES", f"{BFL}, https://www.mbk.ru/thanks-credit-club"
    )
    assert page_has_consent_button("https://www.mbk.ru/thanks-credit-club")
    assert page_has_consent_button(BFL)
    assert not page_has_consent_button("https://www.mbk.ru/thanks")
