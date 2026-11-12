"""Что видит клиент, пока лид ждёт согласия с офер-страницы.

Ссылку в чат кладёт backend, а не модель: адрес, «поправленный» моделью, уводит
человека в никуда, и заметить это в проде почти невозможно. Модель здесь только
объясняет, зачем переходить.
"""
from __future__ import annotations

import core
import prompts


LINK = "https://info.mbk.ru/bfl?d_lead_id=a1b2c3d4e5f6&url=https%3A%2F%2Fwww.mbk.ru"


def _paused_state(**extra) -> dict:
    return {
        "facts": {"client": {"name": "Пётр"}},
        "dialog_phase": "awaiting_site_consent",
        "crm_workflow": {
            "status": "awaiting_site_consent",
            "offer_url": LINK,
            **extra,
        },
    }


def test_the_link_goes_out_once():
    state = _paused_state()

    first = core._site_consent_followup(state)
    second = core._site_consent_followup(state)

    assert any(LINK in bubble for bubble in first)
    assert second == []


def test_the_link_is_not_sent_without_a_pause():
    state = _paused_state()
    state["crm_workflow"]["status"] = "submitted"

    assert core._site_consent_followup(state) == []


def test_the_instruction_forbids_writing_the_address():
    text = prompts.SITE_CONSENT_LINK_INSTRUCTION

    assert "ссылк" in text.lower()
    assert "не пиши" in text.lower() or "не называй" in text.lower()


def test_the_message_names_the_button_and_the_reason():
    bubbles = prompts.site_consent_link_message("Пётр", LINK)

    text = " ".join(bubbles)
    assert LINK in text
    assert "кнопк" in text.lower()


def test_full_crm_name_is_not_used_in_the_client_message():
    state = _paused_state()
    state["facts"]["client"] = {
        "name": "Иванов Иван Иванович",
        "display_name": "Иван",
    }

    text = " ".join(core._site_consent_followup(state))

    assert "Иванов" not in text
    assert "Иванович" not in text
    assert "Иван" in text


def test_a_reply_in_the_chat_gets_a_reminder_not_a_confirmation(matrix):
    """«Согласен» словами — не согласие: подтверждает страница."""
    from routing import plan

    state = _paused_state()
    state["_plan_object"] = plan(matrix, {})

    assert core.turn_instruction(state) is prompts.SITE_CONSENT_LINK_INSTRUCTION
