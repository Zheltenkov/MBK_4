"""Negative debt preferences need own current evidence, never a permission grant."""
from dataclasses import replace
import json

import pytest

from dialogue_frame import DialogueFrame
from policy_semantics import TurnAuthority, evaluate_semantics, parse_semantics
from tests.test_policy_semantics import wire


REFUSAL = "Нет, не разрешаю передавать мои данные специалисту. Давайте обсуждать здесь."
DRAFT = "Хорошо, продолжим здесь."


def active_debt() -> DialogueFrame:
    # This code-owned intent marker distinguishes confirmed debt from clarify.
    return DialogueFrame("client.region_class", "collecting_debt_context",
                         known_fields=frozenset({"intent", "debts.total", "client.name"}))


def evaluate(latest: str, *, frame: DialogueFrame | None = None, evidence: str | None = None,
             kind: str = "decline_transfer", subject: str = "client", mode: str = "asserted",
             scope: str = "current_offer"):
    selected = frame or active_debt()
    raw = wire(acts=[{"kind": kind, "evidence": evidence or latest,
                      "subject": subject, "mode": mode, "scope": scope}])
    parsed = parse_semantics(json.dumps(raw, ensure_ascii=False), latest_user_message=latest, draft=DRAFT)
    return evaluate_semantics(TurnAuthority.from_frame(selected, latest, DRAFT), parsed)


@pytest.mark.parametrize("phone_known", [False, True])
def test_own_transfer_refusal_applies_before_offer_without_declining_phone(phone_known: bool):
    frame = active_debt()
    if phone_known:
        frame = replace(frame, known_fields=frame.known_fields | {"client.phone"})
    result = evaluate(REFUSAL, frame=frame)
    assert tuple(act.kind for act in result.applied_user_acts) == ("decline_transfer",)
    assert result.effective_frame.conversation_declined
    assert result.effective_frame.refused_fields == frame.refused_fields
    assert not frame.conversation_declined


@pytest.mark.parametrize("latest,evidence", [
    ("Если я не разрешаю передавать данные, что будет?", "не разрешаю передавать данные"),
    ("Мама не разрешает передавать мои данные.", "не разрешает передавать мои данные"),
    ("Муж не хочет передавать данные.", "не хочет передавать данные"),
    ("Она сказала: «Не передавать мои данные».", "Не передавать мои данные"),
    ("Раньше я не разрешала передавать мои данные.", "не разрешала передавать мои данные"),
    ("Не знаю, разрешать ли передачу данных.", "Не знаю, разрешать ли передачу данных."),
    ("Я не уверен, что хочу разрешать передачу данных.", "не уверен, что хочу разрешать передачу данных"),
    ("Он написал: «Это пример. Не передавать мои данные. Это только цитата».", "Не передавать мои данные."),
    ("Брат написал: «Это пример. Не передавать мои данные. Это только цитата».", "Не передавать мои данные."),
    ("Не передавать данные моего отца.", "Не передавать данные моего отца."),
    ("Не передавайте данные соседки.", "Не передавайте данные соседки."),
    ("Не вижу проблем, разрешаю передачу моих данных.", "Не вижу проблем, разрешаю передачу моих данных."),
    ("Мои данные сестра не разрешает передавать.", "Мои данные сестра не разрешает передавать."),
    ("Не разрешаю продавать мою машину.", "Не разрешаю продавать мою машину."),
    ("Не хочу запрещать передачу моих данных.", "Не хочу запрещать передачу моих данных."),
    ("Не буду препятствовать передаче моих данных.", "Не буду препятствовать передаче моих данных."),
    ("Я не готов обсуждать передачу моих данных сейчас.", "Я не готов обсуждать передачу моих данных сейчас."),
    ("Мои данные не передавались.", "Мои данные не передавались."),
    ("Не передавали мои данные.", "Не передавали мои данные."),
    ("Я не разрешала передавать мои данные.", "Я не разрешала передавать мои данные."),
    ("Не соглашалась на передачу моих данных.", "Не соглашалась на передачу моих данных."),
    ("Не передавайте чужие данные, мои данные разрешаю передавать.", "Не передавайте чужие данные, мои данные разрешаю передавать."),
])
def test_wrong_client_asserted_model_label_cannot_escape_its_actual_scope(latest: str, evidence: str):
    result = evaluate(latest, evidence=evidence)
    assert not result.applied_user_acts
    assert not result.effective_frame.conversation_declined
    assert "client.phone" not in result.effective_frame.refused_fields


@pytest.mark.parametrize("changes", [
    {"subject": "other"}, {"subject": "unknown"}, {"mode": "conditional"},
    {"mode": "question"}, {"mode": "quoted"}, {"mode": "reported"},
    {"scope": "contact_collection"},
])
def test_correctly_scoped_nonown_or_nonasserted_candidates_remain_rejected(changes):
    result = evaluate(REFUSAL, **changes)
    assert not result.applied_user_acts and not result.effective_frame.conversation_declined


@pytest.mark.parametrize("frame", [
    replace(active_debt(), known_fields=frozenset()),
    replace(active_debt(), mode="onloader"),
    replace(active_debt(), offer_present=True, offer_status="queued"),
    replace(active_debt(), offer_present=True, offer_status="stale"),
])
def test_new_debt_admission_does_not_reopen_clarify_or_change_nonwaiting_offer_rules(frame: DialogueFrame):
    result = evaluate(REFUSAL, frame=frame)
    assert not result.applied_user_acts and not result.effective_frame.conversation_declined


@pytest.mark.parametrize("latest", ["Да.", "Да, расскажите подробнее.", "Понятно, спасибо.",
                                         "Разрешаю передавать данные.", "Не против передачи данных."])
def test_acknowledgements_and_affirmative_permission_never_become_negative_preference(latest: str):
    result = evaluate(latest)
    assert not result.applied_user_acts and not result.effective_frame.conversation_declined


def test_formal_accept_act_remains_outside_the_semantic_schema():
    result = evaluate("Да, передавайте.", kind="accept_transfer")
    assert not result.user_valid and not result.applied_user_acts
    assert not result.effective_frame.conversation_declined


def test_current_awaiting_onloader_refusal_still_uses_its_original_gate():
    frame = replace(active_debt(), mode="onloader", offer_present=True, offer_status="awaiting_consent")
    result = evaluate(REFUSAL, frame=frame)
    assert tuple(act.kind for act in result.applied_user_acts) == ("decline_transfer",)
    assert result.effective_frame.conversation_declined


def test_debt_refusal_keeps_exact_latest_evidence_binding():
    result = evaluate(REFUSAL, evidence="Такого фрагмента в реплике нет.")
    assert not result.user_valid and not result.applied_user_acts
