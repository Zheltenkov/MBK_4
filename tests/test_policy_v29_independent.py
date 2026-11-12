"""Independent main-entry checks for the three observed V28 residuals."""
import json
import socket

import pytest

import core
from dialogue_frame import DialogueFrame
from policy_semantics import TurnAuthority, evaluate_semantics, parse_semantics
import policy_voice
from tests.test_policy_debt_continuation_independent import held_state
from tests.test_policy_semantics_independent import envelope
from tests.test_policy_v22_independent import actual_turn


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Independent V29 checks cannot use the network or send leads")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(core.delivery, "submit", forbidden)


@pytest.mark.parametrize("bad", [
    "С суммой 400 тысяч обычно рассматривают реструктуризацию. Банкротство — когда платить совсем нечем.",
    "Списание возможно через банкротство, но оно не автоматическое и не полное.",
])
def test_actual_coarse_legal_assertions_are_not_served_when_the_model_critic_accepts(monkeypatch, bad):
    state = held_state()
    safe = ("При реструктуризации меняют условия договора. Сам долг остаётся. "
            "При банкротстве возможно освобождение от долгов по закону. Есть исключения, например алименты.")
    answer, _, committed, _ = actual_turn(
        monkeypatch, state, "Чем реструктуризация отличается от списания?", replies=[bad, safe],
    )
    assert answer != bad
    assert "платить совсем нечем" not in answer and "не полное" not in answer
    assert committed["facts"] == state["facts"] and not committed.get("delivery")


@pytest.mark.parametrize("text", [
    "Если нечем платить, можно обсудить банкротство. Это не подтверждает доступность процедуры для вас.",
    "Банкротство не означает, что у человека совсем нет дохода. Освобождение касается не всех обязательств.",
])
def test_conditional_discussion_and_a_denial_of_the_income_shortcut_remain_free(text):
    frame = DialogueFrame(None, "collected_held")
    reply = policy_voice._model_reply(text, frame, "independent_legal_contrast")
    assert reply is not None and reply.text == text


@pytest.mark.parametrize("latest", ["Да, Татарстан.", "Поняла. Я в Казани."])
def test_a_region_answer_does_not_renew_the_previous_optional_preference_question(monkeypatch, latest):
    state = held_state()
    question = "Что из этого вам ближе?"
    state["chat_history"] = [{"role": "assistant", "content": "Можно обсудить разные долговые решения. " + question}]
    useful = "При реструктуризации меняют условия договора. Сам долг остаётся."
    answer, _, committed, _ = actual_turn(
        monkeypatch, state, latest, replies=[useful + "\n\n" + question, useful],
    )
    assert question not in answer
    assert answer and committed["facts"] == state["facts"]
    assert not committed.get("delivery")


def test_an_explicit_request_to_repeat_a_question_remains_answerable(monkeypatch):
    state = held_state()
    question = "Что из этого вам ближе?"
    state["chat_history"] = [{"role": "assistant", "content": question}]
    answer, _, committed, _ = actual_turn(monkeypatch, state, "Повторите вопрос, пожалуйста.", replies=[question] * 2)
    assert answer == question and committed["facts"] == state["facts"]


def test_a_new_faq_after_the_optional_question_receives_its_substantive_answer(monkeypatch):
    state = held_state()
    state["chat_history"] = [{"role": "assistant", "content": "Что из этого вам ближе?"}]
    safe = "При реструктуризации меняют условия договора. Сам долг остаётся."
    answer, _, committed, _ = actual_turn(monkeypatch, state, "А что такое реструктуризация?", replies=[safe] * 2)
    assert answer == safe and committed["facts"] == state["facts"]


def test_a_bad_optional_need_kind_is_isolated_and_reported_without_forging_a_refusal():
    latest, draft = "Зачем нужна сумма долга?", "Размер долга помогает обсуждать подходящие варианты."
    raw = envelope()
    raw["user"]["information_need"] = {"kind": "private-unknown-value", "evidence": latest}
    parsed = parse_semantics(json.dumps(raw), latest_user_message=latest, draft=draft)
    frame = DialogueFrame(None, "collected_held", question_goal="debt_amount_purpose")
    result = evaluate_semantics(TurnAuthority.from_frame(frame, latest, draft), parsed)
    assert result.user_valid and result.claims_valid and not result.applied_user_acts
    assert result.need_error == "invalid_schema" and result.information_need is None
    assert not result.effective_frame.contact_declined
    issues = getattr(parsed, "need_schema_issues", ())
    assert issues and "private-unknown-value" not in repr(issues)


def test_model_outage_keeps_the_short_qualified_legal_answer_and_known_contacts(monkeypatch):
    state = held_state()
    answer, prepared, committed, _ = actual_turn(
        monkeypatch, state, "Чем реструктуризация отличается от списания?", unavailable=True,
    )
    assert prepared["_policy_spoken"].source == "fallback"
    assert len(answer) <= 650 and len(answer.split("\n\n")) <= 2
    assert "не полное" not in answer and "неполное" not in answer
    assert "алимент" in answer and "реструктуризац" in answer
    assert committed["facts"] == state["facts"] and not committed.get("delivery")
