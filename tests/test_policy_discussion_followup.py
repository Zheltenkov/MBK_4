"""A neutral field reply does not reopen the preceding optional discussion question."""
from copy import deepcopy
from dataclasses import replace

import pytest

import core
import llm_agent
from dialogue_frame import (
    DialogueFrame, discussion_followup_issue, omit_deferred_discussion_followup, should_defer_discussion_followup,
)
from tests.test_policy_grounded_voice import install, semantic_raw, strict_settings
from tests.test_policy_voice import debt_state


FIRST_DISCUSSION = (
    "С долгами можно разбираться разными способами. "
    "Что из этого вам ближе или уже пробовали что-то?"
)
SECOND_BODY = "Условия изменения графика можно обсуждать с кредитором."
RENEWED_QUESTION = "Что из этих вариантов вам ближе?"
OPTIONAL_OFFERS = [
    "Если хотите, можем обсудить детали вашей ситуации прямо здесь.",
    "Можем разобрать вашу ситуацию подробнее прямо здесь, если хотите.",
    "Если хотите, можем обсудить, что из этого ближе к вашей ситуации.",
    "При желании могу поговорить с вами подробнее в чате.",
]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Discussion-role controls must not call providers or delivery services")

    monkeypatch.setattr(llm_agent, "_urlopen", forbidden)
    monkeypatch.setattr(llm_agent, "extract_facts", lambda *args, **kwargs: {"fact_updates": [], "internal_summary": ""})
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *args, **kwargs: deepcopy(facts))
    monkeypatch.setattr(core.delivery, "submit", forbidden)
    monkeypatch.setattr(core, "_record_transcript", lambda *args, **kwargs: None)


def after_optional_question():
    state = debt_state(debts={"total": 400000}, client={"region_class": "rf_other", "name": "Ирина", "phone": "+70000000001"})
    state["chat_history"] = [{"role": "assistant", "content": FIRST_DISCUSSION}]
    return state


def turn(monkeypatch, state, user, candidates):
    calls, analyses = install(monkeypatch, candidates, [semantic_raw()] * len(candidates))
    prepared = core.prepare_turn(state, user, strict_settings(), submit_delivery=False)
    text = "".join(core.stream_reply(prepared, user, strict_settings()))
    committed = core.commit_turn(prepared, user, text)["current_state"]
    return text, prepared, committed, calls, analyses


def test_the_actual_two_turn_geographic_answer_keeps_the_first_question_but_does_not_renew_it(monkeypatch):
    state = debt_state(debts={"total": 400000}, client={"name": "Ирина", "phone": "+70000000001"})
    first, _, state, _, _ = turn(monkeypatch, state, "Поняла. Я в Казани.", [FIRST_DISCUSSION])
    assert first == FIRST_DISCUSSION
    facts, history = deepcopy(state["facts"]), deepcopy(state["chat_history"])
    reply, prepared, committed, calls, analyses = turn(
        monkeypatch, state, "Да, Татарстан.", [SECOND_BODY + "\n\n" + RENEWED_QUESTION],
    )
    assert reply == SECOND_BODY
    assert len(calls) == len(analyses) == 1 and analyses[0][0] == SECOND_BODY
    assert getattr(analyses[0][1], "defer_discussion_followup", False)
    assert committed["facts"] == facts and committed["chat_history"][:len(history)] == history
    assert prepared["_policy_spoken"].source == "model" and prepared["_policy_spoken"].asked_field is None
    assert not committed.get("delivery")


def test_a_question_only_retry_is_repaired_by_the_model_instead_of_turning_the_fact_reply_into_silence(monkeypatch):
    answer, prepared, _, calls, analyses = turn(
        monkeypatch, after_optional_question(), "Да, Татарстан.", [RENEWED_QUESTION, SECOND_BODY],
    )
    assert answer == SECOND_BODY and len(calls) == len(analyses) == 2
    assert prepared["_policy_spoken"].source == "model"


def test_an_empty_restored_assistant_record_does_not_hide_the_last_actual_optional_question(monkeypatch):
    state = after_optional_question()
    state["chat_history"].append({"role": "assistant", "content": ""})
    history = deepcopy(state["chat_history"])
    reply, _, committed, _, analyses = turn(
        monkeypatch, state, "Да, Татарстан.", [SECOND_BODY + "\n\n" + RENEWED_QUESTION],
    )
    assert reply == SECOND_BODY and getattr(analyses[0][1], "defer_discussion_followup", False)
    assert committed["chat_history"][:len(history)] == history


def test_a_later_nonquestion_bubble_does_not_hide_the_previous_open_optional_question(monkeypatch):
    state = after_optional_question()
    state["chat_history"][0]["content"] += "\n\nМожно ответить позже."
    reply, _, _, _, analyses = turn(
        monkeypatch, state, "Да, Татарстан.", [SECOND_BODY + "\n\n" + RENEWED_QUESTION],
    )
    assert reply == SECOND_BODY and getattr(analyses[0][1], "defer_discussion_followup", False)


@pytest.mark.parametrize("message", [
    "Чем реструктуризация отличается от списания?",
    "Спросите ещё раз, какой вариант мне ближе.",
    "Мне ближе реструктуризация.",
    "Мне страшно.",
    "Нет, я переехала в Киров и хочу обсудить условия.",
])
def test_a_new_question_repeat_request_preference_emotion_or_correction_keeps_free_discussion(monkeypatch, message):
    candidate = SECOND_BODY + "\n\n" + RENEWED_QUESTION
    answer, _, committed, _, analyses = turn(monkeypatch, after_optional_question(), message, [candidate] * 2)
    assert answer == candidate
    assert not getattr(analyses[0][1], "defer_discussion_followup", False)
    assert not committed.get("delivery")


def test_a_real_pending_intake_field_keeps_its_question(monkeypatch):
    state = debt_state(debts={"total": 400000}, client={"region_class": "rf_other", "phone": "+70000000001"})
    state["chat_history"] = [{"role": "assistant", "content": FIRST_DISCUSSION}]
    answer, prepared, _, _, analyses = turn(monkeypatch, state, "Да, Татарстан.", ["Как к вам обращаться?"])
    assert answer == "Как к вам обращаться?"
    assert prepared["_policy_spoken"].asked_field == "client.name"
    assert not getattr(analyses[0][1], "defer_discussion_followup", False)


def test_a_volunteered_new_amount_is_kept_while_the_optional_question_waits(monkeypatch):
    state = after_optional_question()
    state["script"]["asked"] = []
    answer, _, committed, _, _ = turn(
        monkeypatch, state, "Теперь общий долг 350000 рублей.", [SECOND_BODY + "\n\n" + RENEWED_QUESTION] * 2,
    )
    assert committed["facts"]["debts"]["total"] == 350000
    assert answer and not committed.get("delivery")


@pytest.mark.parametrize("reported", [
    "В примере вопрос «Что из этих вариантов вам ближе?».",
    "Вы спрашивали, что из этих вариантов вам ближе?",
])
def test_quoted_and_reported_questions_are_not_renewed_requests(reported):
    frame = DialogueFrame(None, "collected_held", defer_discussion_followup=True)
    assert discussion_followup_issue(reported, frame) is None
    assert omit_deferred_discussion_followup(reported, frame) == reported


def test_a_question_only_candidate_remains_visible_to_the_hard_guard():
    frame = DialogueFrame(None, "collected_held", defer_discussion_followup=True)
    assert omit_deferred_discussion_followup(RENEWED_QUESTION, frame) == RENEWED_QUESTION
    assert discussion_followup_issue(RENEWED_QUESTION, frame) == "repeated_discussion_followup"


@pytest.mark.parametrize("body", ["", SECOND_BODY + "\n\n"])
def test_a_decimal_inside_a_discussion_question_is_never_left_as_a_truncated_statement(body):
    frame = DialogueFrame(None, "collected_held", defer_discussion_followup=True)
    question = "Из 400.5 тысяч что вам ближе?"
    draft = body + question
    assert omit_deferred_discussion_followup(draft, frame) == (body.strip() or draft)


def test_a_decimal_fact_outside_the_optional_question_is_preserved():
    frame = DialogueFrame(None, "collected_held", defer_discussion_followup=True)
    fact = "В примере общая сумма составляет 400.5 тысяч."
    assert omit_deferred_discussion_followup(fact + "\n\n" + RENEWED_QUESTION, frame) == fact


def test_the_normal_entry_point_removes_a_whole_decimal_question_before_analysis(monkeypatch):
    reply, _, _, _, analyses = turn(
        monkeypatch, after_optional_question(), "Да, Татарстан.",
        [SECOND_BODY + "\n\nИз 400.5 тысяч что вам ближе?"],
    )
    assert reply == SECOND_BODY and analyses[0][0] == SECOND_BODY


def test_a_required_field_request_is_left_for_the_existing_collection_guard():
    frame = DialogueFrame(None, "collected_held", known_fields=frozenset({"client.phone"}),
                          defer_discussion_followup=True)
    question = "На какой номер можно с вами связаться?"
    assert omit_deferred_discussion_followup(question, frame) == question
    assert discussion_followup_issue(question, frame) is None


@pytest.mark.parametrize("changes", [
    {"mode": "onloader", "offer_present": True},
    {"expected": "debts.total", "status": "collecting_debt_context", "confirming": True},
    {"expected": "client.phone", "status": "collecting_contacts"},
])
def test_actual_offers_and_pending_required_fields_are_outside_the_optional_question_role(changes):
    frame = replace(DialogueFrame(None, "collected_held", known_fields=frozenset({"client.region_class"})), **changes)
    assert not should_defer_discussion_followup(frame, previous_reply=FIRST_DISCUSSION, user_message="Да, Татарстан.")


def test_a_previous_real_region_question_does_not_make_the_first_discussion_question_a_retry():
    frame = DialogueFrame(None, "collected_held", known_fields=frozenset({"client.region_class"}))
    assert not should_defer_discussion_followup(
        frame, previous_reply="В каком городе и регионе вы живёте?", user_message="Да, Татарстан.",
    )


def test_phone_refusal_and_known_contacts_survive_the_followup_policy(monkeypatch):
    state = after_optional_question()
    state["questionnaire_policy"]["phone_collection"] = "declined"
    facts = deepcopy(state["facts"])
    reply, prepared, committed, _, _ = turn(monkeypatch, state, "Да, Татарстан.", [SECOND_BODY] * 2)
    assert reply == SECOND_BODY and committed["facts"] == facts
    assert committed["questionnaire_policy"]["phone_collection"] == "declined"
    assert prepared["_policy_spoken"].asked_field is None and not committed.get("delivery")


@pytest.mark.parametrize("offer", OPTIONAL_OFFERS)
def test_optional_discussion_offers_share_the_prior_and_current_followup_role(monkeypatch, offer):
    state = after_optional_question()
    state["chat_history"][0]["content"] = offer
    answer, prepared, committed, calls, analyses = turn(
        monkeypatch, state, "Да, Татарстан.", [SECOND_BODY + "\n\n" + offer],
    )
    assert answer == SECOND_BODY and len(calls) == len(analyses) == 1
    assert analyses[0][0] == SECOND_BODY and analyses[0][1].defer_discussion_followup
    assert committed["facts"] == state["facts"] and prepared["_policy_spoken"].asked_field is None


def test_an_offer_only_retry_stays_visible_until_the_model_repairs_it(monkeypatch):
    answer, _, _, calls, analyses = turn(
        monkeypatch, after_optional_question(), "Да, Татарстан.", [OPTIONAL_OFFERS[1], SECOND_BODY],
    )
    assert answer == SECOND_BODY and len(calls) == len(analyses) == 2
    assert analyses[0][0] == OPTIONAL_OFFERS[1]


def test_the_full_saved_three_turn_sequence_does_not_restore_the_empty_offer_after_a_new_faq(monkeypatch):
    state = debt_state(debts={"total": 400000}, client={"name": "Ирина", "phone": "+70000000001"})
    first, _, state, _, _ = turn(monkeypatch, state, "Поняла. Я в Казани.", [SECOND_BODY + "\n\n" + OPTIONAL_OFFERS[0]])
    assert first.endswith(OPTIONAL_OFFERS[0])
    second, _, state, _, _ = turn(monkeypatch, state, "Да, Татарстан.", [SECOND_BODY + "\n\n" + OPTIONAL_OFFERS[1]])
    assert second == SECOND_BODY
    third, _, committed, _, analyses = turn(
        monkeypatch, state, "А если я не хочу банкротство — что тогда?", [SECOND_BODY + "\n\n" + OPTIONAL_OFFERS[2]],
    )
    assert third == SECOND_BODY and analyses[0][0] == SECOND_BODY
    assert not analyses[0][1].defer_discussion_followup
    assert committed["facts"] == state["facts"] and not committed.get("delivery")


@pytest.mark.parametrize("mixed", [
    "Если хотите, можем обсудить детали, но изменение графика зависит от кредитора.",
    "Если хотите, можем разобрать документы с вашим юристом.",
    "При желании можем обсудить передачу документов в суд.",
    "Если хотите, можем обсудить детали и помочь подать заявление.",
])
def test_mixed_information_or_external_service_stays_whole_for_repair(mixed):
    frame = DialogueFrame(None, "collected_held", defer_discussion_followup=True)
    assert discussion_followup_issue(mixed, frame) == "repeated_discussion_followup"
    assert omit_deferred_discussion_followup(SECOND_BODY + "\n\n" + mixed, frame) == SECOND_BODY + "\n\n" + mixed


@pytest.mark.parametrize("statement", [
    "При желании кредитор может обсудить изменение графика.",
    "Если хотите оформить заявление, документы рассматривает суд.",
    "Вы сказали: «Если хотите, можем обсудить детали вашей ситуации прямо здесь».",
])
def test_external_procedure_and_reported_offers_do_not_establish_a_bot_followup(statement):
    frame = DialogueFrame(None, "collected_held", defer_discussion_followup=True)
    assert discussion_followup_issue(statement, frame) is None
    assert omit_deferred_discussion_followup(statement, frame) == statement


def test_a_locally_negated_speaker_ability_is_not_a_discussion_offer():
    frame = DialogueFrame(None, "collected_held", defer_discussion_followup=True)
    statement = "Я не могу обсудить ваши условия с банком за вас, даже если хотите."
    assert discussion_followup_issue(statement, frame) is None
    assert omit_deferred_discussion_followup(statement, frame) == statement


def test_a_negated_external_ability_does_not_hide_a_separate_positive_offer():
    frame = DialogueFrame(None, "collected_held", defer_discussion_followup=True)
    statement = "Я не могу обсудить условия с банком, но если хотите, можем разобрать вашу ситуацию здесь."
    assert discussion_followup_issue(statement, frame) == "repeated_discussion_followup"
    assert omit_deferred_discussion_followup(statement, frame) == statement
