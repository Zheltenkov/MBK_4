"""Deferring collection for a FAQ does not suspend the truth about unknown debt."""
from copy import deepcopy
from dataclasses import replace

import pytest

import core
import llm_agent
from dialogue_frame import DialogueFrame, constrain_collection, debt_amount_reply_issue, request_fields
import policy_voice
from routing import get_fact
from tests.test_policy_grounded_voice import install, semantic_raw, strict_settings
from tests.test_policy_voice import debt_state


DEFERRED_REQUEST = "Если сможете назвать точный остаток — скажите, пожалуйста."
FALSE_PROGRESS = "Если нет, можем продолжить и с примерной цифрой."
ACTUAL_REPLY = DEFERRED_REQUEST + " " + FALSE_PROGRESS
SAFE_PURPOSE = (
    "От размера долга зависит, какой путь имеет смысл обсуждать. "
    "Общие вопросы можем обсуждать здесь и без точной суммы."
)


def deferred_frame():
    return DialogueFrame("debts.total", "collecting_debt_context", confirming=True,
                          question_goal="debt_amount_purpose")


def test_a_deferred_debt_question_keeps_the_unknown_precision_guard():
    frame = deferred_frame()
    assert frame.allowed_field is None
    assert debt_amount_reply_issue(ACTUAL_REPLY, frame) == "unconfirmed_amount_assertion"
    assert not policy_voice.validate_reply(ACTUAL_REPLY, None, frame=frame)
    assert policy_voice._model_reply(ACTUAL_REPLY, frame, "synthetic") is None


@pytest.mark.parametrize("text", [
    DEFERRED_REQUEST, "Можете назвать остаток?", "Укажите оставшийся остаток.",
])
def test_the_pending_amount_gives_an_unambiguous_remainder_request_its_topic(text):
    frame = deferred_frame()
    assert request_fields(text, frame) == {"debts.total"}
    assert constrain_collection(text, frame).removed_fields == ("debts.total",)
    assert policy_voice._model_reply(text, frame, "synthetic") is None


@pytest.mark.parametrize("text", [
    SAFE_PURPOSE,
    "Если остаток долга больше 300000 рублей, условия могут различаться.",
    "Для общей консультации примерной суммы достаточно.",
    "Можно продолжить отвечать на ваши вопросы без точного остатка.",
    "Фраза «Если нет, можем продолжить и с примерной цифрой» не завершает анкету.",
    "В примере написано «Если сможете назвать точный остаток — скажите, пожалуйста».",
])
def test_deferred_faq_explanations_numbers_quotes_and_general_chat_remain_valid(text):
    frame = deferred_frame()
    assert debt_amount_reply_issue(text, frame) is None
    assert not request_fields(text, frame)
    assert policy_voice.validate_reply(text, None, frame=frame)


def test_a_known_exact_amount_is_outside_the_uncertain_task_contract():
    frame = replace(deferred_frame(), expected=None, confirming=False, status="collected_held",
                    known_fields=frozenset({"debts.total"}))
    assert debt_amount_reply_issue(FALSE_PROGRESS, frame) is None


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Deferred precision controls must not call a provider or delivery service")

    monkeypatch.setattr(llm_agent, "_urlopen", forbidden)
    monkeypatch.setattr(llm_agent, "extract_facts", lambda *args, **kwargs: {"fact_updates": [], "internal_summary": ""})
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *args, **kwargs: deepcopy(facts))
    monkeypatch.setattr(core.delivery, "submit", forbidden)
    monkeypatch.setattr(core, "_record_transcript", lambda *args, **kwargs: None)


def test_actual_debt_faq_repairs_the_saved_progress_promise_without_losing_uncertainty(monkeypatch):
    state = debt_state()
    user = "Около 400 тысяч."
    prepared = core.prepare_turn(state, user, strict_settings(), submit_delivery=False)
    previous = core.commit_turn(prepared, user, "Это точная сумма или примерная?")["current_state"]
    message = "Я уже сказала сумму. Зачем вам её знать?"
    calls, analyses = install(monkeypatch, [ACTUAL_REPLY, SAFE_PURPOSE], [semantic_raw()] * 2)
    prepared = core.prepare_turn(previous, message, strict_settings(), submit_delivery=False)
    text = "".join(core.stream_reply(prepared, message, strict_settings()))
    committed = core.commit_turn(prepared, message, text)["current_state"]
    assert text == SAFE_PURPOSE and len(calls) == len(analyses) == 2
    assert get_fact(committed["facts"], "debts.total") is None
    assert committed["questionnaire_policy"]["amount_observation"]["kind"] == "unknown"
    assert prepared["_policy_spoken"].asked_field is None and not committed.get("delivery")
