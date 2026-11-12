"""Independent normal-entry checks for exact debt and durable negative preferences."""
from dataclasses import replace
import json

import pytest

import core
import debt_policy
import llm_agent
from session_store import _persistable_state
from tests.test_policy_grounded_voice import strict_settings
from tests.test_policy_v31_receipts import actual_turn, held_state, offline  # noqa: F401


@pytest.mark.parametrize("semantic", [False, True])
def test_known_exact_amount_confirmation_is_rejected_without_losing_the_known_contact(monkeypatch, semantic):
    first = "Общая сумма у нас 400 000 рублей — это верно?"
    answer, spoken, state, generations, _ = actual_turn(
        monkeypatch, "Разобраться с долгами", [first, "Можем обсудить варианты здесь."],
        semantic=semantic,
    )
    assert answer == "Можем обсудить варианты здесь." and len(generations) == 2
    assert spoken.asked_field is None
    assert state["facts"]["debts"]["total"] == 400000
    assert state["facts"]["client"]["phone"] == "+70000000001"
    assert not state.get("delivery")


@pytest.mark.parametrize("user", [
    "Не передавайте мои данные.",
    "Пока хочу обсудить всё здесь. Никуда передавать мои данные не нужно.",
])
def test_own_transfer_refusal_survives_unavailable_models_and_a_persisted_next_turn(monkeypatch, user):
    def unavailable(*args, **kwargs):
        raise TimeoutError("Synthetic model unavailability")

    monkeypatch.setattr(llm_agent, "generate_reply", unavailable)
    monkeypatch.setattr(llm_agent, "generate_policy_critique", unavailable)
    config = strict_settings()
    initial = held_state()
    initial["session_id"] = "independent-durable-debt-refusal"
    prepared = core.prepare_turn(initial, user, config, submit_delivery=False)
    policy = debt_policy.parse_policy(prepared)
    assert policy is not None and getattr(policy, "transfer_declined", False) is True
    assert policy.phone_collection == "open"
    answer = "".join(core.stream_reply(prepared, user, config))
    committed = core.commit_turn(prepared, user, answer)["current_state"]
    restored = json.loads(json.dumps(_persistable_state(committed), ensure_ascii=False))
    next_turn = core.prepare_turn(restored, "Чем реструктуризация отличается от списания?", config,
                                  submit_delivery=False)
    restored_policy = debt_policy.parse_policy(next_turn)
    assert restored_policy is not None and getattr(restored_policy, "transfer_declined", False) is True
    assert restored_policy.phone_collection == "open"
    assert next_turn["session_id"] == initial["session_id"]
    assert next_turn["facts"]["debts"]["total"] == 400000
    assert next_turn["facts"]["client"]["phone"] == "+70000000001"
    assert any(turn.get("content") == user for turn in next_turn["chat_history"])
    assert not next_turn.get("delivery")
    assert not next_turn.get("onloader")


def test_old_policy_and_phone_refusal_do_not_invent_a_transfer_choice():
    policy = debt_policy.parse_policy({debt_policy.POLICY_KEY: {
        "version": 1, "mode": "debt", "reason": "explicit_request", "ever_debt": True,
        "phone_collection": "declined",
    }})
    assert policy is not None and getattr(policy, "transfer_declined", None) is False
    assert policy.phone_collection == "declined"


@pytest.mark.parametrize("user", [
    "А можно без передачи?",
    "Менеджер сказал: «Не передавайте мои данные».",
    "Менеджер написал: «Это пример. Не передавайте мои данные. Это только цитата».",
    'В инструкции написано: "Первый пункт. Не передавайте мои данные. Конец примера".',
    "Я не против передачи моих данных.",
])
def test_questions_reports_and_negated_objections_do_not_persist_a_false_refusal(monkeypatch, user):
    config = replace(strict_settings(), policy_voice_enabled=False)
    prepared = core.prepare_turn(held_state(), user, config, submit_delivery=False)
    policy = debt_policy.parse_policy(prepared)
    assert policy is not None and getattr(policy, "transfer_declined", False) is False


def test_an_independent_refusal_outside_a_quote_persists_but_mixed_input_still_uses_extraction(monkeypatch):
    extractions = []

    def extract(payload, *args, **kwargs):
        extractions.append(payload)
        return {"fact_updates": [], "internal_summary": ""}

    monkeypatch.setattr(llm_agent, "extract_facts", extract)
    user = 'В примере написано: «Первый пункт. Не передавайте мои данные. Конец». Не передавайте мои данные.'
    config = replace(strict_settings(), policy_voice_enabled=False)
    prepared = core.prepare_turn(held_state(), user, config, submit_delivery=False)
    policy = debt_policy.parse_policy(prepared)
    assert policy is not None and policy.transfer_declined is True
    assert policy.phone_collection == "open" and len(extractions) == 1
    assert not prepared.get("delivery")


def test_a_multi_sentence_curly_quote_cannot_create_a_separate_phone_refusal(monkeypatch):
    user = "Менеджер написал: “Первый пункт. Телефон не дам. Это пример”."
    _, _, committed, _, _ = actual_turn(monkeypatch, user, ["Понимаю."] * 2)
    policy = debt_policy.parse_policy(committed)
    assert policy is not None and policy.phone_collection == "open"
    assert policy.transfer_declined is False
