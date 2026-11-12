"""Independent full-turn preparation and outage recovery regressions from V17."""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import json

import pytest

from config import AppConfig
import core
import debt_policy
from dialogue_frame import DialogueFrame, request_fields
import llm_agent
from onloader import policy as onloader_policy
from policy_content import content_issues
import policy_voice
from tests.test_policy_semantics_independent import envelope
from tests.test_policy_voice import debt_state, onloader_state


def full_turn(monkeypatch, state, latest, *, unavailable=False):
    """Exercise preparation before any model call, then stream and commit the same state."""
    calls = []
    config = AppConfig(openrouter_api_key="synthetic-unused", model="synthetic-speaker",
                       extractor_model="synthetic-analysis", request_timeout=40, max_tokens=1000,
                       policy_voice_enabled=True, policy_semantic_enabled=True, debt_policy_enabled=True)

    def open_request(configuration, request):
        body = json.loads(request.data)
        calls.append(body)
        if unavailable:
            raise TimeoutError("Synthetic model unavailability")
        content = json.dumps(envelope()) if body["model"] == config.extractor_model else "Можем общаться здесь."
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    def no_delivery(*args, **kwargs):
        pytest.fail("Independent full-turn regressions must not deliver a lead")

    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    monkeypatch.setattr(llm_agent, "extract_facts", lambda *args, **kwargs: {
        "fact_updates": [], "internal_summary": "",
    })
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *args, **kwargs: deepcopy(facts))
    monkeypatch.setattr(core.delivery, "submit", no_delivery)
    monkeypatch.setattr(core, "_record_transcript", lambda *args, **kwargs: None)
    prepared = core.prepare_turn(state, latest, config, submit_delivery=False)
    text = "".join(core.stream_reply(prepared, latest, config))
    committed = core.commit_turn(prepared, latest, text)["current_state"]
    assert not committed.get("delivery")
    assert committed["chat_history"][-2:] == [
        {"role": "user", "content": latest}, {"role": "assistant", "content": text},
    ]
    assert len(calls) <= 4
    return prepared, committed, calls


@pytest.mark.parametrize("previous", [
    "По долгам вопросы ещё есть?",
    "Оставите номер для анкеты? По долгам вопросы ещё есть?",
])
def test_stale_phone_cursor_cannot_turn_an_answer_to_another_question_into_phone_refusal(monkeypatch, previous):
    state = debt_state(debts={"total": 280000}, client={"region_class": "moscow_mo", "name": "Тест"})
    state["script"]["asked"] = ["client.phone"]
    state["chat_history"] = [{"role": "assistant", "content": previous}]
    prepared, committed, _ = full_turn(monkeypatch, state, "Нет, спасибо.")
    assert prepared["questionnaire_policy"]["phone_collection"] == "open"
    assert committed["questionnaire_policy"]["phone_collection"] == "open"


@pytest.mark.parametrize("latest", [
    "Нет, не разрешаю передачу. Никаких кнопок нажимать не хочу.",
    "Я не хочу передавать данные.",
    "Ага, тогда без передачи.",
])
def test_explicit_own_transfer_refusals_survive_an_outage_of_both_speech_and_analysis(monkeypatch, latest):
    state = onloader_state()
    state["chat_history"] = [{"role": "assistant", "content": "Здесь можно обсудить ваши вопросы."}]
    original_binding = deepcopy(state["onloader"])
    _, committed, calls = full_turn(monkeypatch, state, latest, unavailable=True)
    assert onloader_policy.chat_declined(committed)
    assert committed["onloader"] == original_binding
    assert committed["onloader"]["decision"] is None
    assert calls


@pytest.mark.parametrize("latest", [
    "Можно без передачи?",
    "Если откажусь от передачи, что будет?",
    "Муж не хочет передавать данные.",
    "Он сказал: «Не передавать». А я хочу уточнить условия.",
    "Я не против передачи.",
])
def test_model_unavailability_does_not_invent_a_transfer_refusal_from_a_question_or_another_person(
    monkeypatch, latest,
):
    state = onloader_state()
    state["chat_history"] = [{"role": "assistant", "content": "По долгам вопросы ещё есть?"}]
    _, committed, _ = full_turn(monkeypatch, state, latest, unavailable=True)
    assert not onloader_policy.chat_declined(committed)
    assert committed["onloader"]["decision"] is None


@pytest.mark.parametrize("latest", ["Номер оставлять не хочу.", "Давайте без телефона."])
def test_explicit_phone_refusal_is_preserved_when_both_models_are_unavailable(monkeypatch, latest):
    state = debt_state(debts={"total": 280000}, client={"region_class": "moscow_mo", "name": "Тест"})
    state["chat_history"] = [{"role": "assistant", "content": "На какой номер можно с вами связаться?"}]
    _, committed, _ = full_turn(monkeypatch, state, latest, unavailable=True)
    assert committed["questionnaire_policy"]["phone_collection"] == "declined"


@pytest.mark.parametrize("latest", [
    "Можно без телефона?",
    "Если не оставлю номер, что будет?",
    "Он сказал: «Номер оставлять не хочу». А я хочу уточнить условия.",
])
def test_an_outage_does_not_turn_a_phone_question_or_quoted_refusal_into_a_decision(monkeypatch, latest):
    state = debt_state(debts={"total": 280000}, client={"region_class": "moscow_mo", "name": "Тест"})
    state["chat_history"] = [{"role": "assistant", "content": "На какой номер можно с вами связаться?"}]
    _, committed, _ = full_turn(monkeypatch, state, latest, unavailable=True)
    assert committed["questionnaire_policy"]["phone_collection"] == "open"


@pytest.mark.parametrize("latest", ["Можно ли без банкротства?", "Москва, можно ли без банкротства?"])
def test_a_question_about_avoiding_bankruptcy_does_not_cancel_the_whole_debt_conversation(monkeypatch, latest):
    state = debt_state(debts={"total": 280000}, client={"region_class": "moscow_mo", "name": "Тест"})
    state["chat_history"] = [{"role": "assistant", "content": "Какой вариант хотите обсудить?"}]
    _, committed, _ = full_turn(monkeypatch, state, latest)
    policy = debt_policy.parse_policy(committed)
    assert policy is not None and policy.mode == "debt"
    assert committed["facts"] == state["facts"]


@pytest.mark.parametrize("text", [
    "Алименты или возмещение вреда всё равно придётся платить.",
    "Возмещение вреда нужно выплачивать и после банкротства.",
    "После списания возмещение вреда обязательно надо платить.",
])
def test_unqualified_harm_survival_is_rejected_when_expressed_as_an_obligation_to_pay(text):
    assert "overbroad_harm_exception" in content_issues(text, mode="debt", question_goal="debt_explanation")


@pytest.mark.parametrize("text", [
    "Возмещение вреда жизни или здоровью всё равно придётся платить.",
    "Возмещение морального вреда нужно выплачивать и после банкротства.",
])
def test_obligation_wording_keeps_the_statutory_qualified_harm_exceptions(text):
    assert not content_issues(text, mode="debt", question_goal="debt_explanation")


@pytest.mark.parametrize("question", [
    "Точную сумму долга можете сейчас назвать?",
    "Общий остаток долга можете написать?",
])
def test_object_fronted_amount_collection_cannot_hide_in_an_answer_only_debt_faq(question):
    frame = DialogueFrame("debts.total", "collecting_debt_context", confirming=True,
                          question_goal="debt_explanation")
    assert "debts.total" in request_fields(question, frame)
    assert policy_voice._model_reply("Реструктуризация меняет условия договора.\n\n" + question,
                                     frame, "model_semantic_checked") is None


@pytest.mark.parametrize("prior_refusal", [False, True])
def test_a_current_recipient_question_is_answered_during_an_outage_in_the_same_chat(monkeypatch, prior_refusal):
    state = onloader_state()
    if prior_refusal:
        _, state, _ = full_turn(monkeypatch, state, "Не передавать.", unavailable=True)
        assert onloader_policy.chat_declined(state)
    state["chat_history"].append({"role": "assistant", "content": "Ранее обсуждалась компания «Устаревшая компания»."})
    original_binding = deepcopy(state["onloader"])
    _, committed, calls = full_turn(monkeypatch, state, "А кому именно уйдут мои данные?", unavailable=True)
    answer = committed["chat_history"][-1]["content"]
    assert original_binding["offer"]["partner_name"] in answer
    assert "Устаревшая компания" not in answer
    assert onloader_policy.chat_declined(committed) is prior_refusal
    assert committed["onloader"] == original_binding and committed["onloader"]["decision"] is None
    assert all(word not in answer.casefold() for word in ("нажмите", "подтвердите", "номер телефона", "уже переданы"))
    assert calls


def test_a_generic_who_question_cannot_authorize_an_unrequested_recipient_explanation(monkeypatch):
    state = onloader_state()
    _, committed, _ = full_turn(monkeypatch, state, "Кому можно пожаловаться?", unavailable=True)
    answer = committed["chat_history"][-1]["content"]
    assert state["onloader"]["offer"]["partner_name"] not in answer
    assert not onloader_policy.chat_declined(committed)


def test_an_unknown_current_card_does_not_recover_a_recipient_from_old_history(monkeypatch):
    state = {"onloader": {"status": "unavailable"}, "facts": {},
             "chat_history": [{"role": "assistant", "content": "Раньше обсуждалась компания «Устаревшая компания»."}]}
    _, committed, _ = full_turn(monkeypatch, state, "А кому именно уйдут мои данные?", unavailable=True)
    assert "Устаревшая компания" not in committed["chat_history"][-1]["content"]
    assert not onloader_policy.chat_declined(committed)
