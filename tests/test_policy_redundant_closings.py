"""Repeated empty closings do not reopen an answered conversation or erase useful speech."""
from copy import deepcopy
import json
import socket

import pytest

import core
from evaluation import local_style_eval as harness
from tests.test_policy_grounded_voice import strict_settings
from tests.test_policy_v22_independent import actual_turn
from tests.test_policy_voice import debt_state, onloader_state
from usage_tracker import SessionUsage
from pii_masking import mask_payload_for_llm
from session_store import _persistable_state
import utils

FAQ = "А можно пока просто объяснить, что такое реструктуризация?"
ANSWER = ("Реструктуризация — это изменение условий действующего кредита: например, "
          "продлевают срок или пересматривают график платежей.\n\n"
          "Сам долг при этом не списывается, меняются только правила его возврата.")
OLD_TAIL = "Если есть другие вопросы — спрашивайте, я на связи."
NEW_TAIL = "Если захотите обсудить варианты или что-то уточнить по вашей ситуации — пишите, я здесь."


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Closing regressions must not use a real network or send a lead")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(core.delivery, "submit", forbidden)


def pending_after_answer():
    state = debt_state()
    state["chat_history"] = [{"role": "user", "content": FAQ},
                             {"role": "assistant", "content": ANSWER}]
    state["script"]["asked"] = ["debts.total"]
    return state


@pytest.mark.parametrize("ack", ["Ага.", "Угу", "Ок", "Хорошо", "👍"])
def test_the_saved_ack_after_an_answered_FAQ_is_silent_through_the_normal_entry(ack):
    state = pending_after_answer()
    before = deepcopy(state)
    assert core.conversation_is_over(state, ack)
    result = harness.execute_core_turn(state, ack, strict_settings(), SessionUsage("offline-empty-ack"))
    assert result.bubbles == [] and result.voice["source"] == "lifecycle_silence"
    assert result.state["facts"] == before["facts"]
    assert result.state["questionnaire_policy"] == before["questionnaire_policy"]
    assert result.state["chat_history"] == before["chat_history"] + [{"role": "user", "content": ack}]
    assert state == before


def test_an_answered_FAQ_ack_tail_stays_quiet_after_serialization():
    state = pending_after_answer()
    for ack in ["Ага", "Ок", "Угу"]:
        assert core.conversation_is_over(state, ack)
        state["chat_history"].append({"role": "user", "content": ack})
        state = json.loads(json.dumps(_persistable_state(state)))


@pytest.mark.parametrize("message", [
    "Мне страшно.", "😢", "😭", "❤️", "...", "А какие документы нужны?",
    "Объясните ещё раз, пожалуйста.", "Спасибо, а можно без банкротства?", "400000 рублей.",
])
def test_emotions_FAQ_repeat_requests_and_new_facts_remain_answerable(message):
    assert not core.conversation_is_over(pending_after_answer(), message)


@pytest.mark.parametrize("message", ["Да.", "Ага.", "Хорошо.", "Да, спасибо."])
def test_the_whole_actual_assistant_turn_keeps_an_open_question_answerable(message):
    state = debt_state(debts={"total": 400000}, client={"region_class": "rf_other", "name": "Тест", "phone": "+70000000401"})
    state["chat_history"] = [{"role": "user", "content": "Какие есть варианты?"},
                             {"role": "assistant", "content": "Объяснить варианты решения долгов?"},
                             {"role": "assistant", "content": "Можем обсудить их здесь."}]
    assert not core.conversation_is_over(state, message)


def test_the_normal_active_questionnaire_keeps_its_existing_ack_surface():
    state = {"dialog_phase": "qualification", "chat_history": [
        {"role": "user", "content": "Меня зовут Ирина."},
        {"role": "assistant", "content": "Спасибо."}]}
    assert not core.conversation_is_over(state, "Ага.")


def test_a_silent_ack_cannot_record_a_decision_on_the_active_offer():
    state = onloader_state()
    state["chat_history"] = [{"role": "user", "content": "Можно общаться без передачи?"},
                             {"role": "assistant", "content": "Да, вопросы можно обсуждать здесь."}]
    before = deepcopy(state)
    assert core.conversation_is_over(state, "Ага.")
    assert state == before and state["onloader"]["decision"] is None


def test_a_later_volunteered_exact_amount_still_enters_the_existing_pending_task(monkeypatch):
    state = pending_after_answer()
    assert not core.conversation_is_over(state, "400000 рублей.")
    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *a, **k: {"fact_updates": [], "internal_summary": ""})
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *a, **k: deepcopy(facts))
    prepared = core.prepare_turn(state, "400000 рублей.", strict_settings(), submit_delivery=False)
    assert prepared["questionnaire_policy"]["amount_observation"]["amount"] == 400000
    assert state["script"]["asked"] == ["debts.total"] and not prepared.get("delivery")


def omit(text, previous, user="Что такое реструктуризация?"):
    function = getattr(utils, "omit_repeated_availability_tail", None)
    assert callable(function)
    return function(text, recent_replies=previous, user_message=user)


@pytest.mark.parametrize("previous,tail", [
    (OLD_TAIL, NEW_TAIL),
    (NEW_TAIL, "Если появятся вопросы — пишите, я на связи."),
    (OLD_TAIL, "Обращайтесь, если что-то ещё понадобится."),
])
def test_same_empty_availability_act_is_omitted_across_paraphrases(previous, tail):
    text = ANSWER + "\n\n" + tail
    assert omit(text, [previous]) == ANSWER


@pytest.mark.parametrize("tail", [
    "Если долг 400000 рублей — напишите.", "Если захотите, напишите номер телефона.",
    "Если тревожно, пишите, я здесь.", "Если нужен договор — обращайтесь, я на связи.",
    "Пишите по ссылке https://example.invalid.", "Есть ли ещё вопросы?",
])
def test_real_fields_details_emotions_and_questions_are_preserved_byte_for_byte(tail):
    text = ANSWER + "\n\n" + tail
    assert omit(text, [OLD_TAIL]) == text


@pytest.mark.parametrize("message", ["Повторите, что можно написать позже.", "Скажите ещё раз."])
def test_an_explicit_repeat_request_preserves_the_current_answer(message):
    text = ANSWER + "\n\n" + NEW_TAIL
    assert omit(text, [OLD_TAIL], message) == text


def test_a_sole_availability_answer_and_a_first_invitation_are_preserved():
    assert omit(NEW_TAIL, [OLD_TAIL], "Можно общаться здесь?") == NEW_TAIL
    text = ANSWER + "\n\n" + NEW_TAIL
    assert omit(text, []) == text


@pytest.mark.parametrize("previous,tail", [
    (OLD_TAIL, "Если хотите, можем обсудить детали вашей ситуации прямо здесь."),
    ("При желании можем поговорить подробнее здесь.", NEW_TAIL),
    ("Если хотите, можем обсудить детали вашей ситуации прямо здесь.",
     "Можем разобрать вашу ситуацию подробнее прямо здесь, если хотите."),
    ("При желании могу поговорить с вами в чате.",
     "Если хотите, можем обсудить, что из этого ближе к вашей ситуации."),
])
def test_conditional_discussion_and_imperative_availability_share_one_repeated_empty_act(previous, tail):
    assert omit(ANSWER + "\n\n" + tail, [previous], "А если не банкротство, какие варианты?") == ANSWER


@pytest.mark.parametrize("tail", [
    "Если хотите, можем обсудить детали вашей ситуации прямо здесь.",
    "При желании могу поговорить с вами подробнее в чате.",
])
def test_the_first_conditional_offer_the_sole_answer_and_a_requested_repeat_are_preserved(tail):
    assert omit(ANSWER + "\n\n" + tail, []) == ANSWER + "\n\n" + tail
    assert omit(tail, [OLD_TAIL]) == tail
    assert omit(ANSWER + "\n\n" + tail, [OLD_TAIL], "Повторите, что вы предлагаете.") == ANSWER + "\n\n" + tail


@pytest.mark.parametrize("tail", [
    "Если хотите, можем обсудить детали, но условия изменения графика определяет кредитор.",
    "Если хотите, можем разобрать документы вместе с юристом.",
    "Если хотите, можем обсудить сумму 400000 рублей.",
    "Если хотите, можем обсудить, что вас пугает сильнее.",
    "Если хотите, можем обсудить детали и помочь направить заявление.",
])
def test_meaningful_facts_external_actions_and_support_do_not_become_empty_offers(tail):
    text = ANSWER + "\n\n" + tail
    assert omit(text, [OLD_TAIL]) == text


def test_cleanup_precedes_the_final_critic_and_keeps_the_new_answer(monkeypatch):
    state = debt_state(debts={"total": 400000}, client={"region_class": "rf_other", "name": "Тест", "phone": "+70000000401"})
    state["chat_history"] = [{"role": "assistant", "content": OLD_TAIL}]
    answer, _, committed, calls = actual_turn(monkeypatch, state, "Что такое реструктуризация?",
                                              replies=[ANSWER + "\n\n" + NEW_TAIL] * 2)
    assert answer == ANSWER
    drafts = [json.loads(call["messages"][-1]["content"])["draft"]
              for call in calls if call["model"] == "synthetic-analysis"]
    assert drafts and drafts[-1] == answer
    assert committed["facts"] == state["facts"] and not committed.get("delivery")


@pytest.mark.parametrize("message", ["Ага 😢", "Спасибо 😭", "Понял 😡"])
def test_an_acknowledgement_word_does_not_erase_its_emotional_symbol(message):
    assert not core._empty_conversation_ack(message)
    state = debt_state(debts={"total": 400000}, client={"name": "Тест", "phone": "+70000000401", "region_class": "rf_other"})
    state["chat_history"] = [{"role": "assistant", "content": ANSWER}]
    assert not core.conversation_is_over(state, message)


def test_an_empty_assistant_record_does_not_replace_the_last_answered_FAQ():
    state = pending_after_answer()
    assert core.conversation_is_over(state, "Ага.")
    state["chat_history"].extend([{"role": "user", "content": "Ага."},
                                  {"role": "assistant", "content": ""}])
    state = json.loads(json.dumps(_persistable_state(state)))
    assert core.conversation_is_over(state, "Ок.")
    assert state["script"]["asked"] == ["debts.total"]


@pytest.mark.parametrize("empty_records", [False, True])
def test_real_FAQ_prepare_stream_commit_then_three_quiet_acknowledgements(monkeypatch, empty_records):
    state = pending_after_answer()
    answer, _, state, _ = actual_turn(monkeypatch, state, FAQ, replies=[ANSWER] * 2)
    assert answer == ANSWER
    facts, policy = deepcopy(state["facts"]), deepcopy(state["questionnaire_policy"])
    def no_more_models(*args, **kwargs):
        raise AssertionError("An answered FAQ acknowledgement needs no model call")
    monkeypatch.setattr(core.llm_agent, "_urlopen", no_more_models)
    for ack in ["Ага.", "Ок.", "Угу"]:
        result = harness.execute_core_turn(state, ack, strict_settings(), SessionUsage("offline-serial-ack"))
        assert result.bubbles == [] and result.voice["source"] == "lifecycle_silence"
        state = result.state
        if empty_records:
            # Older callers can persist an empty assistant row for a silent turn.
            state["chat_history"].append({"role": "assistant", "content": ""})
        state = json.loads(json.dumps(_persistable_state(state)))
        assert state["facts"] == facts and state["questionnaire_policy"] == policy


SAVED_RECEIPT_DRAFTS = [
    "Казань, Татарстан — записала.\n\nЕсли появятся вопросы по вашей ситуации, пишите, я здесь.",
    "Спасибо, Казань отметила.\n\nЕсли захотите обсудить варианты по вашей ситуации или появятся новые вопросы — пишите, я здесь.",
]


def critic_visible_reply(prepared, text):
    # The final critic consumes the same text after the existing PII projection.
    return mask_payload_for_llm({"facts": prepared["facts"], "latest_user_message": text,
                                 "current_context_readonly": True}, True)["latest_user_message"]


@pytest.mark.parametrize("candidate", SAVED_RECEIPT_DRAFTS)
def test_the_actual_V22_receipt_tail_composition_keeps_model_speech_for_the_critic(monkeypatch, candidate):
    state = debt_state(debts={"total": 400000}, client={"region_class": "rf_other", "city": "Казань", "region": "Казань", "name": "Тест", "phone": "+70000000401"})
    state["chat_history"] = [{"role": "assistant", "content": OLD_TAIL}]
    answer, prepared, committed, calls = actual_turn(monkeypatch, state, "Поняла. Я в Казани.", replies=[candidate] * 2)
    assert answer == candidate.split("\n\n")[0]
    drafts = [json.loads(call["messages"][-1]["content"])["draft"]
              for call in calls if call["model"] == "synthetic-analysis"]
    assert drafts and drafts[-1] == critic_visible_reply(prepared, answer)
    assert prepared["_policy_spoken"].source == "model" and prepared["_policy_spoken"].asked_field is None
    assert committed["facts"] == state["facts"]


def test_two_known_region_answers_keep_distinct_model_receipts_instead_of_a_shared_terminal_fallback(monkeypatch):
    state = debt_state(debts={"total": 400000}, client={"region_class": "rf_other", "city": "Казань", "region": "Казань", "name": "Тест", "phone": "+70000000401"})
    state["chat_history"] = [{"role": "assistant", "content": OLD_TAIL}]
    facts = deepcopy(state["facts"])
    # First draft is verbatim V22. The second combines its saved receipt with
    # the same empty act to isolate composition from an unrelated transfer claim.
    steps = [("Поняла. Я в Казани.", SAVED_RECEIPT_DRAFTS[0]),
             ("Да, Татарстан.", "Регион я уже зафиксировала.\n\nЕсли появятся вопросы, пишите, я здесь.")]
    answers = []
    for message, candidate in steps:
        answer, prepared, state, calls = actual_turn(monkeypatch, state, message, replies=[candidate] * 2)
        assert answer == candidate.split("\n\n")[0]
        assert prepared["_policy_spoken"].source == "model" and prepared["_policy_spoken"].asked_field is None
        assert state["facts"] == facts
        drafts = [json.loads(call["messages"][-1]["content"])["draft"]
                  for call in calls if call["model"] == "synthetic-analysis"]
        assert drafts[-1] == critic_visible_reply(prepared, answer)
        answers.append(answer)
    assert answers[0] != answers[1]


def test_an_empty_receipt_is_still_removed_when_a_useful_answer_remains(monkeypatch):
    state = debt_state(debts={"total": 400000}, client={"region_class": "rf_other", "city": "Казань", "region": "Казань", "name": "Тест", "phone": "+70000000401"})
    candidate = "Казань, Татарстан — записала.\n\n" + ANSWER
    answer, _, committed, calls = actual_turn(monkeypatch, state, "Казань.", replies=[candidate] * 2)
    assert answer == ANSWER
    drafts = [json.loads(call["messages"][-1]["content"])["draft"]
              for call in calls if call["model"] == "synthetic-analysis"]
    assert drafts[-1] == answer and committed["facts"] == state["facts"]


@pytest.mark.parametrize("bad", ["Казань — это Татарстан, верно?", "Без номера я не могу продолжить разговор."])
def test_receipt_conservation_does_not_approve_a_real_collection_or_pressure_failure(monkeypatch, bad):
    state = debt_state(debts={"total": 400000}, client={"region_class": "rf_other", "city": "Казань", "region": "Казань", "name": "Тест", "phone": "+70000000401"})
    answer, _, committed, _ = actual_turn(monkeypatch, state, "Казань.", replies=[bad, ANSWER])
    assert answer == ANSWER and committed["facts"] == state["facts"]


ACTUAL_V23_TAILS = [
    "Если есть вопросы по возможным вариантам или хотите что-то уточнить — пишите, отвечу по существу.",
    "Если есть вопросы по ситуации с долгами или хотите что-то уточнить — пишите, отвечу по существу.",
]


@pytest.mark.parametrize("tail", ACTUAL_V23_TAILS + [
    "Если появятся общие вопросы про кредит, пишите — я отвечу.",
    "Когда возникнут вопросы о кредите, обращайтесь — постараюсь пояснить подробнее.",
])
def test_actual_and_new_topic_padding_share_the_same_empty_availability_act(tail):
    assert omit(ANSWER + "\n\n" + tail, [OLD_TAIL]) == ANSWER


@pytest.mark.parametrize("tail", [
    "Если банк простит долг, пишите — я отвечу.",
    "Если будет новый график платежей и вопросы, напишите.",
    "Если появятся вопросы и решите списать долг, пишите — я отвечу.",
    "Если появятся вопросы и понадобится срочная помощь, пишите — я отвечу.",
    "Если появятся вопросы и захочется поддержки, пишите — я отвечу.",
])
def test_every_coordinated_condition_must_itself_be_an_empty_information_act(tail):
    text = ANSWER + "\n\n" + tail
    assert omit(text, [OLD_TAIL]) == text


@pytest.mark.parametrize("tail", ACTUAL_V23_TAILS)
def test_saved_V23_receipt_and_generalized_tail_are_cleaned_before_the_critic(monkeypatch, tail):
    state = debt_state(debts={"total": 400000}, client={"region_class": "rf_other", "city": "Казань", "region": "Казань", "name": "Тест", "phone": "+70000000401"})
    state["chat_history"] = [{"role": "assistant", "content": OLD_TAIL}]
    receipt = "Да, Татарстан я отметила."
    answer, prepared, committed, calls = actual_turn(
        monkeypatch, state, "Да, Татарстан.", replies=[receipt + "\n\n" + tail] * 2,
    )
    assert answer == receipt and prepared["_policy_spoken"].source == "model"
    drafts = [json.loads(call["messages"][-1]["content"])["draft"]
              for call in calls if call["model"] == "synthetic-analysis"]
    assert drafts[-1] == critic_visible_reply(prepared, answer)
    assert committed["facts"] == state["facts"] and not committed.get("delivery")


@pytest.mark.parametrize("tail", [
    "Если банк простил долг и есть вопросы, пишите — я отвечу.",
    "Если после того как банк простил долг появятся вопросы, пишите — я отвечу.",
    "Если не появятся вопросы, пишите — я отвечу.",
])
def test_a_question_anchor_does_not_absorb_an_unknown_prefix_or_a_negated_condition(tail):
    text = ANSWER + "\n\n" + tail
    assert omit(text, [OLD_TAIL]) == text


@pytest.mark.parametrize("tail", [
    "Если появятся вопросы, пишите — отвечу чтобы вы не переживали.",
    "Если появятся вопросы, пишите — отвечу чтобы вам стало спокойнее.",
])
def test_a_supportive_purpose_clause_is_not_generic_answer_padding(tail):
    text = ANSWER + "\n\n" + tail
    assert omit(text, [OLD_TAIL]) == text


V24_RECEIPTS_AND_AVAILABILITY = [
    ("Казань, Татарстан — приняла.", "Если появятся вопросы по вашей ситуации, пишите — я здесь и готова помочь.", "\n\n"),
    ("Спасибо за уточнение.", "Если будут вопросы по вашей ситуации с долгами, пишите — я на связи.", " "),
]


@pytest.mark.parametrize("prefix,tail,separator", V24_RECEIPTS_AND_AVAILABILITY)
def test_saved_V24_availability_suffixes_keep_the_model_prefix_for_the_critic(monkeypatch, prefix, tail, separator):
    state = debt_state(debts={"total": 400000}, client={"region_class": "rf_other", "city": "Казань", "region": "Казань", "name": "Тест", "phone": "+70000000401"})
    state["chat_history"] = [{"role": "assistant", "content": OLD_TAIL}]
    answer, prepared, committed, calls = actual_turn(
        monkeypatch, state, "Да, Татарстан.", replies=[prefix + separator + tail] * 2,
    )
    assert answer == prefix and prepared["_policy_spoken"].source == "model"
    drafts = [json.loads(call["messages"][-1]["content"])["draft"]
              for call in calls if call["model"] == "synthetic-analysis"]
    assert drafts[-1] == critic_visible_reply(prepared, answer)
    assert committed["facts"] == state["facts"] and not committed.get("delivery")


@pytest.mark.parametrize("separator", [" ", "\n\n", "\n"])
def test_sentence_or_paragraph_boundaries_do_not_change_the_existing_prefix(separator):
    prefix = "Первое пояснение.\n\nИ ещё одно полезное пояснение!"
    text = prefix + separator + "Когда возникнут вопросы, обращайтесь — мы можем помочь."
    assert omit(text, ["Полезный ответ. " + OLD_TAIL]) == prefix


@pytest.mark.parametrize("suffix", [
    "Когда появятся вопросы, пишите — я готова помочь списать долг.",
    "Когда появятся вопросы, пишите — могу помочь с документами.",
    "Когда появятся вопросы, пишите — мы готовы помочь, чтобы вам было спокойнее.",
    "Когда появятся вопросы, пишите — я готова помочь вам сейчас, понимаю ваши переживания.",
])
def test_help_readiness_with_an_object_or_support_clause_is_preserved(suffix):
    text = ANSWER + " " + suffix
    assert omit(text, [OLD_TAIL]) == text


@pytest.mark.parametrize("separator", [" ", "\n\n"])
def test_several_empty_invitation_acts_are_still_a_sole_availability_answer(separator):
    invitation = "Когда появятся вопросы, пишите — мы готовы помочь."
    text = separator.join([OLD_TAIL, invitation, OLD_TAIL])
    assert omit(text, [OLD_TAIL]) == text


@pytest.mark.parametrize("text", [
    'Она сказала: «Можно обсудить это позже. Если появятся вопросы, пишите — я на связи.',
    'Он написал: "Можно вернуться позже. Если появятся вопросы, пишите — я на связи.',
    'Адрес страницы https://example.invalid/help. Если появятся вопросы, пишите — я на связи.',
    'И т.д. Если появятся вопросы, пишите — я на связи.',
])
def test_uncertain_quote_URL_or_abbreviation_boundaries_preserve_the_entire_text(text):
    assert omit(text, [OLD_TAIL]) == text


def test_a_decimal_fact_prefix_is_conserved_when_its_separate_empty_tail_is_removed():
    prefix = "Общая сумма 400.5 тысячи рублей."
    assert omit(prefix + " " + NEW_TAIL, [OLD_TAIL]) == prefix


@pytest.mark.parametrize("intent", [
    "обсудим", "обсужу", "поговорим", "поговорю", "можем обсудить", "могу поговорить",
    "готова обсудить", "готовы поговорить", "постараюсь объяснить", "объясним", "поясним",
])
def test_response_and_discussion_share_a_closed_communication_intent_family(intent):
    assert omit(ANSWER + "\n\nЕсли есть вопросы по вашей ситуации — задавайте, " + intent + ".", [OLD_TAIL]) == ANSWER


def test_actual_V25_discussion_invitation_is_cleaned_before_the_critic(monkeypatch):
    state = debt_state(debts={"total": 400000}, client={"region_class": "rf_other", "city": "Казань", "region": "Казань", "name": "Тест", "phone": "+70000000401"})
    state["chat_history"] = [{"role": "assistant", "content": OLD_TAIL}]
    receipt = "Приняла, Казань и Татарстан."
    candidate = receipt + "\n\nЕсли есть вопросы по вашей ситуации — задавайте, обсудим."
    answer, prepared, committed, calls = actual_turn(
        monkeypatch, state, "Да, Татарстан.", replies=[candidate] * 2,
    )
    assert answer == receipt and prepared["_policy_spoken"].source == "model"
    drafts = [json.loads(call["messages"][-1]["content"])["draft"]
              for call in calls if call["model"] == "synthetic-analysis"]
    assert drafts[-1] == critic_visible_reply(prepared, answer)
    assert committed["facts"] == state["facts"] and not committed.get("delivery")


@pytest.mark.parametrize("intent", [
    "обсудим и передадим заявку", "спишем долг", "одобрим кредит", "поговорим чтобы вам стало легче",
    "обсудим, я понимаю вашу тревогу", "будем добиваться списания",
])
def test_communication_intent_does_not_absorb_business_actions_or_support_clauses(intent):
    text = ANSWER + "\n\nЕсли есть вопросы, пишите — " + intent + "."
    assert omit(text, [OLD_TAIL]) == text


@pytest.mark.parametrize("intent", [
    "обсудим вашу заявку с банком", "обсудим вашу заявку с партнёром", "поговорим с кредитором",
    "готовы обсудить вашу заявку с банком", "можем поговорить с кредитором",
])
def test_discussion_with_an_external_party_is_not_an_empty_availability_suffix(intent):
    text = ANSWER + "\n\nЕсли есть вопросы, пишите — " + intent + "."
    assert omit(text, [OLD_TAIL]) == text


@pytest.mark.parametrize("intent", ["обсудим в чате", "поговорим здесь"])
def test_a_generic_chat_location_does_not_change_a_closed_discussion_intent(intent):
    assert omit(ANSWER + "\n\nЕсли есть вопросы, пишите — " + intent + ".", [OLD_TAIL]) == ANSWER


@pytest.mark.parametrize("condition", ["хотите ответить банку", "хотите объяснить банку ситуацию"])
def test_new_response_suffix_intents_do_not_expand_the_existing_user_condition_roles(condition):
    text = ANSWER + "\n\nЕсли " + condition + ", пишите — я на связи."
    assert omit(text, [OLD_TAIL]) == text


@pytest.mark.parametrize("intent", ["объясним банку вашу ситуацию", "поясним партнёру ваши обстоятельства"])
def test_new_plural_explanation_intents_keep_external_action_details(intent):
    text = ANSWER + "\n\nЕсли есть вопросы, пишите — " + intent + "."
    assert omit(text, [OLD_TAIL]) == text


@pytest.mark.parametrize("intent", [
    "разберусь", "разберёмся", "разберемся", "можем разобраться", "готова разобраться",
    "разберёмся вместе", "обсудим вместе", "поговорим вместе",
])
def test_reflexive_discussion_and_collective_manner_are_generic_communication_intents(intent):
    text = ANSWER + "\n\nЕсли появятся вопросы по вашей ситуации — пишите, " + intent + "."
    assert omit(text, [OLD_TAIL]) == ANSWER


def test_actual_V27_receipt_and_collective_discussion_are_normalized_before_the_critic(monkeypatch):
    state = debt_state(debts={"total": 400000}, client={"region_class": "rf_other", "city": "Казань", "region": "Казань", "name": "Тест", "phone": "+70000000401"})
    state["chat_history"] = [{"role": "assistant", "content": OLD_TAIL}]
    receipt = "Хорошо, регион ясен."
    candidate = receipt + "\n\nЕсли появятся вопросы по вашей ситуации — пишите, разберёмся вместе."
    answer, prepared, committed, calls = actual_turn(
        monkeypatch, state, "Да, Татарстан.", replies=[candidate] * 2,
    )
    assert answer == receipt and prepared["_policy_spoken"].source == "model"
    drafts = [json.loads(call["messages"][-1]["content"])["draft"]
              for call in calls if call["model"] == "synthetic-analysis"]
    assert drafts[-1] == critic_visible_reply(prepared, answer)
    assert committed["facts"] == state["facts"] and not committed.get("delivery")


@pytest.mark.parametrize("tail", [
    "Если появятся вопросы, пишите — разберёмся вместе с кредитором.",
    "Если появятся вопросы, пишите — разберёмся вместе с погашением ваших долгов.",
    "Если появятся вопросы, пишите — разберёмся вместе, я понимаю, как вам тяжело.",
    "Если появятся вопросы, пишите — разберёмся вместе чтобы вам стало спокойнее.",
])
def test_collective_discussion_with_external_objects_or_actual_support_is_preserved(tail):
    text = ANSWER + "\n\n" + tail
    assert omit(text, [OLD_TAIL]) == text
