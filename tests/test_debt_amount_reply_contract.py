"""The speaker must ask for an answer the exact-debt parser can consume."""
from dataclasses import replace

import pytest

import llm_agent
import policy_voice
from config import AppConfig
from dialogue_frame import DialogueFrame


@pytest.mark.parametrize("draft", [
    "Давайте зафиксируем точную цифру. 280 000 рублей верно?",
    "Давайте зафиксируем точнее: 280 000 рублей? От точной суммы зависит подбор вариантов.",
    "Хорошо, считаем 400 000 рублей.",
    "Хорошо, 280 000 рублей.",
])
def test_recorded_unknown_amount_cannot_be_confirmed_or_asserted_by_the_speaker(draft):
    frame = DialogueFrame("debts.total", "collecting_debt_context", confirming=True)
    assert not policy_voice.validate_reply(draft, frame.allowed_field, frame=frame)


@pytest.mark.parametrize("draft", [
    "Напишите точную сумму остатка одним числом в рублях.",
    "Какова точная сумма оставшегося долга?",
    "Пока не считаем 400 000 рублей точной суммой. Назовите точный остаток в рублях.",
    "Если остаток составляет 400 000 рублей, результат всё равно зависит от условий.",
    "Пока сумма примерно 400 000 рублей, точный остаток ещё неизвестен.",
    "Это ровно 400 тысяч или больше/меньше?",
    "К какой сумме ближе — 350 тыс, 400 или больше?",
    "400 тыс — это точная сумма или всё-таки примерно?",
])
def test_open_numeric_requests_and_qualified_explanations_are_not_a_yes_no_confirmation(draft):
    frame = DialogueFrame("debts.total", "collecting_debt_context", confirming=True)
    assert policy_voice.validate_reply(draft, frame.allowed_field, frame=frame)


def test_a_known_amount_is_outside_the_unknown_debt_contract():
    frame = DialogueFrame(None, "collected_held", known_fields=frozenset({"debts.total"}))
    assert policy_voice.validate_reply("Считаем 400 000 рублей.", None, frame=frame)
    assert policy_voice.validate_reply("Это 400 000 рублей, верно?", None, frame=replace(frame, confirming=False))


@pytest.mark.parametrize("draft", [
    "Давайте зафиксируем точную цифру. 280 000 рублей верно?",
    "Хорошо, считаем 400 000 рублей.",
])
def test_the_actual_renderer_repairs_an_incompatible_amount_act_before_the_critic(monkeypatch, draft):
    calls = []
    final = "Напишите точную сумму остатка одним числом в рублях."
    replies = iter([draft, final])

    def generate(payload, config, **kwargs):
        calls.append(payload)
        return next(replies)

    monkeypatch.setattr(llm_agent, "generate_reply", generate)
    frame = DialogueFrame("debts.total", "collecting_debt_context", confirming=True, require_question=True)
    result = policy_voice.render({}, AppConfig(openrouter_api_key="synthetic-unused"),
                                 expected=frame.expected, status=frame.status, frame=frame,
                                 fallback="Укажите точную сумму долга одним числом в рублях.",
                                 fallback_question="debts.total")
    assert result.text == final and result.asked_field == "debts.total"
    assert len(calls) == 2 and result.critic_calls == 1
