"""A general bankruptcy explanation remains modal and independently reviewed."""
from dataclasses import replace
import json

import pytest

import core
from dialogue_frame import DialogueFrame
import policy_factual_audit
import policy_voice
from tests.test_policy_grounded_voice import install, semantic_raw, strict_settings
from tests.test_policy_v31_receipts import held_state, offline  # noqa: F401


ACTUAL = (
    "При реструктуризации меняют условия договора — срок или график платежей, но сам долг не списывается.\n\n"
    "Списание возможно через банкротство: при соблюдении условий закона кредитный долг может быть списан "
    "полностью, но алименты и возмещение вреда жизни или здоровью всё равно остаются.\n\n"
    "Есть ли ещё вопросы по этим процедурам?",
    "При реструктуризации меняют условия договора — срок или график платежей, но сам долг остаётся.\n\n"
    "Списание возможно через судебное банкротство: при соблюдении условий закона кредитный долг может быть "
    "списан полностью, но алименты и возмещение вреда жизни или здоровью всё равно сохраняются.",
)
FRAME = DialogueFrame(None, "collected_held", question_goal="debt_explanation")


@pytest.mark.parametrize("text", ACTUAL)
def test_both_exact_live_general_explanations_are_hard_valid(text):
    assert policy_voice.validate_reply(text, None, frame=FRAME)
    assert policy_factual_audit.requires_audit(FRAME, text)


@pytest.mark.parametrize("text", [
    "Для вас списание возможно через банкротство: при соблюдении условий закона кредитный долг может быть списан полностью.",
    "Списание вашего долга возможно через банкротство: кредитный долг может быть списан полностью.",
    "Списание возможно через банкротство: ваш долг может быть списан полностью.",
    "Списание возможно через банкротство: кредитный долг будет списан полностью.",
    "Списание возможно через банкротство: кредитный долг может быть списан полностью, это гарантировано.",
    "Списание возможно через банкротство: кредитный долг может быть списан полностью, вам обязательно позвонят.",
    "Списание возможно через банкротство: кредитный долг может быть списан полностью, я отправлю ваши данные.",
    "Списание возможно через банкротство: кредитный долг может быть списан полностью после оплаты нашей комиссии.",
    "Списание возможно через банкротство: кредитный долг может быть списан полностью только если у человека нет дохода.",
    "Кредитор подтвердил: списание возможно через банкротство: кредитный долг может быть списан полностью.",
    "«Списание возможно через банкротство»: ваш долг может быть списан полностью.",
    "Списание возможно через банкротство: кредитный долг может быть списан полностью, но возмещение вреда сохраняется.",
])
def test_the_prefix_cannot_exempt_a_personal_outcome_another_claim_or_added_condition(text):
    assert not policy_voice.validate_reply(text, None, frame=FRAME)


@pytest.mark.parametrize("text", ACTUAL)
def test_the_exception_is_not_enabled_in_an_onloader(text):
    assert not policy_voice.validate_reply(text, None, frame=replace(FRAME, mode="onloader"))


@pytest.mark.parametrize("text", ACTUAL)
@pytest.mark.parametrize("factual_verdict", ["supported", "unsupported", "unknown", None])
def test_the_normal_chat_still_requires_both_semantic_and_factual_reviews(monkeypatch, text, factual_verdict):
    generations, analyses = install(monkeypatch, [text] * 2, [semantic_raw()] * 2)
    audits = []
    def assess(payload, draft, frame, config, **kwargs):
        audits.append(draft)
        return policy_factual_audit.FactualAssessment(
            factual_verdict, "synthetic-factual", factual_verdict or "model_unavailable",
            True,
        )
    monkeypatch.setattr(policy_factual_audit, "assess", assess)
    config = replace(strict_settings(), policy_factual_audit_enabled=True)
    initial = held_state()
    latest = "Чем реструктуризация отличается от списания?"
    prepared = core.prepare_turn(initial, latest, config, submit_delivery=False)
    answer = "".join(core.stream_reply(prepared, latest, config))
    assert analyses and audits and all(item == text for item in audits)
    assert (answer == text) is (factual_verdict == "supported")
    assert len(generations) == len(analyses) == len(audits) <= 2
    committed = core.commit_turn(prepared, latest, answer)["current_state"]
    assert committed["facts"] == initial["facts"] and not committed.get("delivery")


def test_a_failed_semantic_claim_channel_cannot_serve_the_prefix_text(monkeypatch):
    text = ACTUAL[1]
    bad = json.loads(semantic_raw())
    bad["claims"] = None
    _, analyses = install(monkeypatch, [text] * 2, [json.dumps(bad)] * 2)
    config = replace(strict_settings(), policy_factual_audit_enabled=True)
    prepared = core.prepare_turn(held_state(), "Что такое списание?", config, submit_delivery=False)
    answer = "".join(core.stream_reply(prepared, "Что такое списание?", config))
    assert analyses and answer != text
