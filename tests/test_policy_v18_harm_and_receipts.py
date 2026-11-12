"""Payment-obligation paraphrases keep legal qualifications and empty receipts bounded."""
import json

import pytest

from dialogue_frame import DialogueFrame
from policy_content import content_issues
from tests.test_policy_recovery_independent import run_provider_sequence
from tests.test_policy_semantics_independent import envelope
from utils import omit_empty_field_receipt_bubbles


@pytest.mark.parametrize("text", [
    "Например, алименты или возмещение вреда всё равно придётся платить.",
    "После банкротства возмещение вреда нужно будет выплачивать.",
    "После списания вы обязаны погашать задолженность по возмещению вреда.",
    "Возмещение вреда всё равно необходимо выплачивать.",
    "После списания возмещение вреда обязательно надо платить.",
    "Даже после банкротства возмещение вреда вам придётся возмещать.",
    "После банкротства вы должны платить возмещение вреда.",
    "Не утверждаю, что банкротство всё спишет, но возмещение вреда придётся платить.",
])
def test_unqualified_harm_survival_is_the_same_claim_when_expressed_as_an_obligation(text):
    assert "overbroad_harm_exception" in content_issues(text, mode="debt", question_goal="debt_explanation")


@pytest.mark.parametrize("text", [
    "Возмещение вреда жизни или здоровью придётся платить.",
    "Возмещение морального вреда необходимо выплачивать.",
    "Требования о возмещении вреда здоровью вы обязаны погашать.",
    "Возмещение вреда не нужно платить автоматически во всех случаях.",
    "Возмещение вреда не надо платить автоматически во всех случаях.",
    "Вы не обязаны платить любое возмещение вреда только из-за его названия.",
    "Возмещение вреда придётся не платить, а отдельно рассматривать по закону.",
    "Если возмещение вреда придётся платить, порядок нужно уточнить.",
    "Возмещение вреда придётся платить, если требование сохраняется по закону.",
    "Возмещение вреда придётся платить?",
    "Нужно ли будет платить возмещение вреда?",
    "В образце написано «Возмещение вреда придётся платить».",
    "Не утверждаю, что возмещение вреда придётся платить.",
    "Неверно говорить, что любое возмещение вреда придётся платить после банкротства.",
    "Алименты нужно платить, а возмещение вреда зависит от вида требования.",
])
def test_qualification_denial_conditional_question_and_quotation_do_not_assert_universal_survival(text):
    assert "overbroad_harm_exception" not in content_issues(text, mode="debt", question_goal="debt_explanation")


def test_the_actual_provider_repair_receives_the_payment_survival_diagnosis(monkeypatch):
    bad = "Например, алименты или возмещение вреда всё равно придётся платить."
    good = "При банкротстве требования о возмещении вреда жизни или здоровью сохраняются."
    result, bodies = run_provider_sequence(
        monkeypatch, DialogueFrame(None, "collected_held", mode="debt", question_goal="debt_explanation"),
        "Чем реструктуризация отличается от списания?", [bad, envelope(), good, envelope()],
    )
    assert result.spoken.text == good
    assert result.spoken.source == "model" and result.spoken.asked_field is None
    assert len(bodies) == 4
    repair = json.dumps(bodies[2], ensure_ascii=False)
    assert "overbroad_harm_exception" in repair
    assert "жизни или здоровью" in repair
    assert bad not in repair


@pytest.mark.parametrize("receipt", [
    "Город и регион мне уже известны, переспрашивать не буду.",
    "Регион мне известен, больше уточнять не нужно.",
    "Мне уже известны город и регион.",
    "Хорошо, регион ясен.",
    "Место жительства уже известно.",
])
def test_a_whole_known_state_receipt_after_a_plain_region_answer_is_omitted(receipt):
    useful = "Если беспокоит график выплат, можем обсудить его здесь."
    assert omit_empty_field_receipt_bubbles(
        receipt + "\n\n" + useful,
        received_fields=frozenset({"client.region_class"}), user_message="Казань.",
    ) == useful


@pytest.mark.parametrize("message,text", [
    ("Казань. Вы учли регион?", "Город и регион мне уже известны, переспрашивать не буду."),
    ("Казань, мне страшно.", "Регион мне известен, больше уточнять не нужно."),
    ("Казань.", "Регион мне известен. От региона зависит порядок обращения."),
    ("Казань.", "Регион мне известен, больше уточнять не нужно. Понимаю, что вам страшно."),
    ("Казань.", "Регион мне известен?"),
])
def test_known_state_receipt_changes_preserve_substance_emotion_and_requested_confirmation(message, text):
    assert omit_empty_field_receipt_bubbles(
        text, received_fields=frozenset({"client.region_class"}), user_message=message,
    ) == text
