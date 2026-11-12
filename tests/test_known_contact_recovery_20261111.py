"""A proved contact-reference fallback must not repeat the prior generic hold."""
from dataclasses import replace

import pytest

import debt_policy
import policy_grounded_voice as grounded
from dialogue_frame import DialogueFrame
from routing import Plan
from tests.test_policy_grounded_voice import semantic_raw
from tests.test_policy_recovery_independent import run_provider_sequence


LATEST = "Да, согласен на передачу специалисту. Телефон я уже оставил выше."
FACTS = {"debts": {"total": 600000}, "client": {
    "name": "Сергей", "phone": "+70000000001", "region_class": "moscow_mo"}}


def frame():
    return DialogueFrame(None, "collected_held", known_fields=frozenset({
        "intent", "debts.total", "client.region_class", "client.name", "client.phone"}),
        known_phone_reference=True, transfer_blocked=True)


def hold():
    return debt_policy.render_debt_reply(debt_policy.DebtPolicyState(),
        Plan(action="no_fit", transition="debt_collected"), FACTS)


def test_invalid_g_receipt_gets_relevant_contact_fallback_without_consent(monkeypatch):
    illegal = "Согласие на передачу получено."
    result, requests = run_provider_sequence(monkeypatch, frame(), LATEST,
        [illegal, illegal], fallback=hold(), facts=FACTS)
    assert len(requests) == 2  # Both known-invalid drafts need no fabricated C opinion.
    assert result.spoken.critic_calls == 0 and not result.spoken.critic_assessments
    assert result.spoken.source == "fallback"
    assert result.spoken.reason.startswith("code_known_contact_hold:")
    assert result.spoken.text != hold() and "Номер уже есть" in result.spoken.text
    assert "передача" in result.spoken.text.lower() and "не подключена" in result.spoken.text
    assert result.spoken.asked_field is None and not result.applied_user_acts
    assert not result.effective_frame.conversation_declined


@pytest.mark.parametrize("selected", [replace(frame(), conversation_declined=True),
    replace(frame(), refused_fields=frozenset({"client.phone"})), replace(frame(), closing=True),
    replace(frame(), mode="onloader"), replace(frame(), offer_present=True),
    replace(frame(), known_fields=frozenset({"intent", "client.phone"})),
])
def test_no_other_flow_or_partial_context_gains_known_contact_hold(selected):
    assert grounded._caller_owned_debt_recovery(hold(), selected, FACTS, "test") is None


@pytest.mark.parametrize("change", [{"known_phone_reference": False}, {"transfer_blocked": False}])
def test_private_reference_and_blocked_capability_are_both_required(change):
    result = grounded._caller_owned_debt_recovery(hold(), replace(frame(), **change), FACTS, "test")
    assert result is not None and result.text == hold()
    assert "code_known_contact_hold" not in result.reason


def test_arbitrary_caller_text_cannot_gain_known_contact_authority():
    assert grounded._caller_owned_debt_recovery("Уже передали заявку.", frame(), FACTS, "test") is None


def test_persisted_pause_remains_with_contact_projection():
    selected = replace(frame(), conversation_paused=True)
    result = grounded._caller_owned_debt_recovery(hold(), selected, FACTS, "test")
    assert result is not None and result.asked_field is None
    assert selected.conversation_paused and selected.expected is None


def test_valid_g_stays_free_instead_of_using_contact_fallback(monkeypatch):
    answer = "Продолжим здесь. Что ещё хотите разобрать?"
    result, requests = run_provider_sequence(monkeypatch, frame(), LATEST,
        [answer, semantic_raw()], fallback=hold(), facts=FACTS)
    assert len(requests) == 2 and result.spoken.text == answer and result.spoken.source == "model"
