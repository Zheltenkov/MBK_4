"""Independent continuation-act and diagnostic provenance regressions from V29."""
import json
import socket

import pytest

import core
from config import AppConfig
from dialogue_frame import DialogueFrame
import llm_agent
import policy_semantic_analysis
from tests.test_policy_debt_continuation_independent import held_state
from tests.test_policy_semantics_independent import envelope
from tests.test_policy_v22_independent import actual_turn


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Independent V30 checks cannot use the network or send leads")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(core.delivery, "submit", forbidden)


@pytest.mark.parametrize("offer", [
    "Если хотите, можем обсудить детали вашей ситуации прямо здесь.",
    "Можем разобрать вашу ситуацию подробнее прямо здесь, если хотите.",
    "Если хотите, можем обсудить, что из этого ближе к вашей ситуации.",
])
def test_a_region_reply_cannot_renew_the_actual_v29_conditional_discussion_offers(monkeypatch, offer):
    state = held_state()
    state["chat_history"] = [{
        "role": "assistant", "content": "Если хотите, можем обсудить детали вашей ситуации прямо здесь.",
    }]
    useful = "При реструктуризации меняют условия договора. Сам долг остаётся."
    answer, _, committed, _ = actual_turn(
        monkeypatch, state, "Да, Татарстан.", replies=[useful + "\n\n" + offer, useful],
    )
    assert offer not in answer and "если хотите" not in answer.casefold()
    assert answer and committed["facts"] == state["facts"] and not committed.get("delivery")


def test_an_emotional_reply_can_receive_a_supportive_offer_instead_of_field_reply_deferral(monkeypatch):
    state = held_state()
    state["chat_history"] = [{
        "role": "assistant", "content": "Если хотите, можем обсудить детали вашей ситуации прямо здесь.",
    }]
    supportive = "Понимаю, что вам тревожно. Если хотите, можем обсудить, что сейчас беспокоит сильнее."
    answer, _, committed, _ = actual_turn(
        monkeypatch, state, "Мне тревожно, я не знаю, с чего начать.", replies=[supportive] * 2,
    )
    assert answer == supportive and committed["facts"] == state["facts"]


def test_an_explicit_request_to_repeat_the_offer_preserves_the_model_wording(monkeypatch):
    state = held_state()
    offer = "Если хотите, можем обсудить детали вашей ситуации прямо здесь."
    state["chat_history"] = [{"role": "assistant", "content": offer}]
    answer, _, committed, _ = actual_turn(
        monkeypatch, state, "Повторите, что вы предлагаете.", replies=[offer] * 2,
    )
    assert answer == offer and committed["facts"] == state["facts"]


def test_the_saved_region_region_faq_sequence_does_not_restart_the_same_optional_offer(monkeypatch):
    state = held_state()
    state["chat_history"] = [{"role": "assistant", "content": "Размер долга помогает понять направление."}]
    ordinary = "При судебном банкротстве возможно освобождение от долгов по правилам закона. Есть исключения."
    faq = "При реструктуризации меняют условия договора. Сам долг остаётся. " + ordinary
    steps = [
        ("Поняла. Я в Казани.", ordinary, "Если хотите, можем обсудить детали вашей ситуации прямо здесь."),
        ("Да, Татарстан.", ordinary, "Можем разобрать вашу ситуацию подробнее прямо здесь, если хотите."),
        ("Чем реструктуризация отличается от списания?", faq,
         "Если хотите, можем обсудить, что из этого ближе к вашей ситуации."),
    ]
    for index, (latest, body, offer) in enumerate(steps):
        draft = body + "\n\n" + offer
        answer, prepared, state, _ = actual_turn(monkeypatch, state, latest, replies=[draft] * 2)
        assert prepared["_policy_spoken"].source == "model"
        expected = draft if index == 0 else body
        assert answer == expected, f"Unexpected served text at saved turn {index + 3}"
        assert state["facts"]["debts"]["total"] == 400000
        assert state["facts"]["client"]["phone"] == "+70000000401"
        assert not state.get("delivery")


@pytest.mark.parametrize("draft,expected_code_issue", [
    ("При банкротстве алименты и возмещение вреда всё равно остаются.", True),
    ("При банкротстве алименты и возмещение вреда жизни или здоровью всё равно остаются.", False),
])
def test_the_code_diagnosis_never_turns_a_raw_model_acceptance_into_a_claim_of_model_success(
    monkeypatch, draft, expected_code_issue,
):
    raw = envelope()
    monkeypatch.setattr(llm_agent, "generate_policy_critique", lambda *args, **kwargs: json.dumps(raw))
    frame = DialogueFrame(None, "collected_held", question_goal="debt_explanation")
    result = policy_semantic_analysis.analyze(
        {"latest_user_message": "Что не списывается?", "facts": {}, "short_history": []},
        draft, frame, AppConfig(openrouter_api_key="unused", model="synthetic-speaker",
                               extractor_model="synthetic-analysis"),
    )
    assert result.assessment.verdict.accept and result.assessment.verdict.flags == []
    assert result.assessment.reason == "accepted"
    assert bool(getattr(result, "code_content_issues", ())) is expected_code_issue
    assert result.effective_verdict.accept is not expected_code_issue
    assert ("unsupported_claim" in result.effective_verdict.flags) is expected_code_issue


def test_a_clean_code_scan_does_not_override_the_raw_model_rejection(monkeypatch):
    raw = envelope()
    raw["style"] = {"accept": False, "flags": ["wrong_current_turn"]}
    monkeypatch.setattr(llm_agent, "generate_policy_critique", lambda *args, **kwargs: json.dumps(raw))
    result = policy_semantic_analysis.analyze(
        {"latest_user_message": "Что такое реструктуризация?", "facts": {}, "short_history": []},
        "Сам долг при реструктуризации остаётся.", DialogueFrame(None, "collected_held"),
        AppConfig(openrouter_api_key="unused", model="synthetic-speaker", extractor_model="synthetic-analysis"),
    )
    assert not getattr(result, "code_content_issues", ())
    assert result.effective_verdict is result.assessment.verdict
    assert not result.effective_verdict.accept and result.effective_verdict.flags == ["wrong_current_turn"]


def test_the_normal_entry_serves_the_repair_and_keeps_both_raw_and_effective_diagnostics(monkeypatch):
    state = held_state()
    bad = "При банкротстве алименты и возмещение вреда всё равно остаются."
    safe = ("При реструктуризации меняют условия договора. Сам долг остаётся. "
            "При банкротстве алименты и возмещение вреда жизни или здоровью сохраняются.")
    answer, prepared, committed, _ = actual_turn(
        monkeypatch, state, "Чем реструктуризация отличается от списания?", replies=[bad, safe],
    )
    spoken = prepared["_policy_spoken"]
    assert answer == safe and spoken.source == "model"
    assert len(spoken.critic_assessments) == len(spoken.diagnostic_assessments) == 2
    assert all(item.verdict.accept for item in spoken.critic_assessments)
    first, second = spoken.diagnostic_assessments
    assert first.code_content_issues == ("overbroad_harm_exception",)
    assert not first.effective_verdict.accept
    assert second.code_content_issues == () and second.effective_verdict.accept
    assert committed["facts"] == state["facts"] and not committed.get("delivery")
