"""Unknown incoming choices never authorize operational wording through null C slots."""
from dataclasses import replace

import pytest

from tests.test_policy_same_draft_stability_20261111 import NEUTRAL, selected, sequence


@pytest.mark.parametrize("draft", [
    "Подтвердите передачу данных.",
    "Можно передать данные специалисту?",
    "Дайте добро на передачу данных.",
    "Нажмите кнопку передачи данных.",
    "Можно направить сведения?",
    "Подтвердите отправку обращения.",
    "Данные можно передать?",
    "Передачу заявки из этого чата сейчас оформить не могу, но нажмите кнопку передачи данных.",
    "Передачу заявки из этого чата сейчас оформить не могу. Можно направить сведения?",
])
def test_missing_c_request_claims_cannot_authorize_operational_speech(monkeypatch, draft):
    result, _, _, _ = sequence(monkeypatch, drafts=(draft,))
    assert result.spoken.source == "fallback" and result.spoken.text != draft
    assert result.paused and not result.applied_user_acts
    assert not result.effective_frame.conversation_declined


@pytest.mark.parametrize("frame", [
    replace(selected(), mode="onloader", offer_present=True, offer_status="awaiting_consent"),
    replace(selected(), known_fields=frozenset()),
    replace(selected(), transfer_blocked=False),
])
def test_fast_retention_cannot_expand_beyond_confirmed_held_debt(monkeypatch, frame):
    result, _, _, _ = sequence(monkeypatch, frame=frame)
    assert result.spoken.source == "fallback"


@pytest.mark.parametrize("draft", [NEUTRAL, "Можем продолжить разговор здесь."])
def test_closed_capability_or_neutral_speech_remains_usable_with_valid_incoming(monkeypatch, draft):
    result, roles, _, _ = sequence(monkeypatch, drafts=(draft,), acts=[])
    assert result.spoken.source == "model" and result.spoken.text == draft
    assert roles == ["G", "C"] and not result.paused
    assert not result.applied_user_acts and not result.effective_frame.conversation_declined


def test_uncertain_incoming_does_not_admit_a_mixed_operational_reply(monkeypatch):
    text = NEUTRAL + " Можно направить сведения?"
    result, roles, _, _ = sequence(monkeypatch, drafts=(text,))
    assert result.spoken.source == "fallback" and result.spoken.text != text
    assert roles == ["G", "C"] and result.paused
