"""Unknown debt cannot be declared sufficient for intake or counted as a voiced question."""
from copy import deepcopy
from dataclasses import replace

import pytest

import core
import debt_policy
import llm_agent
from config import AppConfig
from dialogue_frame import DialogueFrame, constrain_collection, debt_amount_reply_issue, request_fields
import policy_voice
from routing import get_fact
from tests.test_policy_voice import debt_state


RECORDED_REPLY = (
    "Хорошо, так и оставим — около 400 тысяч. "
    "Если точнее не знаете, этого достаточно для продолжения. "
    "Есть ещё вопросы по ситуации или можем двигаться дальше?"
)


def uncertain_frame():
    return DialogueFrame("debts.total", "collecting_debt_context", confirming=True, require_question=True)


@pytest.mark.parametrize("text", [
    RECORDED_REPLY,
    "Примерной суммы достаточно для завершения анкеты.",
    "Этой оценки хватит, чтобы продолжить заполнение анкеты.",
    "Можно продолжить заполнение анкеты без точной суммы.",
    "Давайте двигаться дальше.",
    "Если точной суммы нет, можем продолжить анкету.",
    "Можем завершить анкету.",
    "Для беседы примерной суммы достаточно, но для продолжения анкеты этой оценки тоже хватит.",
])
def test_an_unknown_amount_does_not_authorize_asserted_intake_progress(text):
    frame = uncertain_frame()
    assert debt_amount_reply_issue(text, frame) == "unconfirmed_amount_assertion"
    assert not policy_voice.validate_reply(text, frame.allowed_field, frame=frame)
    assert policy_voice._model_reply(text, frame, "synthetic") is None


@pytest.mark.parametrize("text", [
    "Примерной суммы достаточно для общих пояснений в чате.",
    "Общаться здесь можно и без точной суммы.",
    "Можем продолжить обсуждение здесь, точная сумма пока неизвестна.",
    "Для общей консультации этого достаточно, можем продолжить.",
    "Можем продолжить отвечать на ваши вопросы.",
    "Для беседы примерной суммы достаточно, но для завершения анкеты нужно точное число.",
    "Примерной суммы недостаточно для завершения анкеты.",
    "Этой оценки не хватит, чтобы завершить анкету.",
    "Если точный остаток известен, можно продолжить заполнение анкеты.",
    "Этого достаточно для продолжения анкеты?",
    "Фраза «Этого достаточно для продолжения анкеты» сама по себе ничего не подтверждает.",
    'В примере написано "Можем двигаться дальше".',
])
def test_chat_explanations_denials_questions_and_quoted_examples_do_not_assert_progress(text):
    assert debt_amount_reply_issue(text, uncertain_frame()) is None


@pytest.mark.parametrize("frame", [
    replace(uncertain_frame(), confirming=False),
    DialogueFrame(None, "collected_held", known_fields=frozenset({"debts.total"})),
    DialogueFrame("client.name", "collecting_contacts", confirming=True),
])
def test_the_progress_guard_is_only_for_the_current_unconfirmed_debt_amount(frame):
    assert debt_amount_reply_issue(RECORDED_REPLY, frame) is None


@pytest.mark.parametrize("receipt", [
    "Хорошо, так и оставим — около 400 тысяч.",
    "Оставим сумму примерной: 400 тысяч рублей.",
    "Запишем 400 тысяч как приблизительную сумму.",
    "Сумму долга мы уточним позже.",
])
def test_a_declarative_receipt_is_not_a_request_to_state_an_amount(receipt):
    frame = uncertain_frame()
    assert request_fields(receipt, frame) == set()
    assert constrain_collection(receipt, frame).asked_field is None


@pytest.mark.parametrize("question", [
    "Назовите сумму долга.",
    "Укажите общую сумму.",
    "Нужно уточнить сумму долга.",
    "Можете назвать сумму?",
    "Можете назвать сумму долга?",
    "Сумму долга можете написать?",
    "Напишите остаток долга.",
    "400 тысяч — это точная сумма или примерно?",
])
def test_directed_requests_and_actual_confirmation_questions_remain_supported(question):
    assert "debts.total" in request_fields(question, uncertain_frame())


@pytest.mark.parametrize("text,field", [
    ("Оставляйте номер телефона.", "client.phone"),
    ("Указывайте общую сумму долга.", "debts.total"),
    ("Сообщайте ваш город.", "client.region_class"),
    ("Уточняйте общую сумму долга.", "debts.total"),
    ("Рассказывайте общую сумму долга.", "debts.total"),
    ("Называйте ваше имя.", "client.name"),
])
def test_imperfective_imperatives_are_still_collection_acts_with_and_without_permission(text, field):
    allowed = DialogueFrame(field, "collecting_debt_context")
    forbidden = DialogueFrame("debts.total", "collecting_debt_context", question_goal="debt_explanation",
                              refused_fields=frozenset({"client.phone"}))
    assert field in request_fields(text, allowed)
    assert constrain_collection(text, allowed).asked_field == field
    assert field in request_fields(text, forbidden)
    assert constrain_collection(text, forbidden).removed_fields == (field,)
    assert policy_voice._model_reply(text, forbidden, "synthetic") is None


@pytest.mark.parametrize("receipt", [
    "Оставим эту сумму приблизительной.",
    "Записала примерно 400 тысяч.",
    "Указала сумму в анкете.",
    "Сообщу вам, когда появятся сведения.",
])
def test_declarative_first_person_verbs_do_not_become_collection_imperatives(receipt):
    assert request_fields(receipt, uncertain_frame()) == set()


@pytest.fixture
def offline_extraction(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Pending debt controls must not call providers or delivery services")

    monkeypatch.setattr(llm_agent, "_urlopen", forbidden)
    monkeypatch.setattr(llm_agent, "extract_facts", lambda *args, **kwargs: {
        "fact_updates": [{"path": "debts.total", "value": 400000}], "internal_summary": "",
    })
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *args, **kwargs: deepcopy(facts))
    monkeypatch.setattr(core.delivery, "submit", forbidden)
    monkeypatch.setattr(core, "_record_transcript", lambda *args, **kwargs: None)


def held_debt(observation):
    state = debt_state()
    state[debt_policy.POLICY_KEY]["amount_observation"] = observation.model_dump(mode="json")
    state["fact_statuses"]["debts.total"] = "needs_confirmation"
    state["script"]["asked"] = []
    state["chat_history"] = [{"role": "assistant", "content": "Точная сумма пока неизвестна. Можем общаться здесь."}]
    return state


@pytest.mark.usefixtures("offline_extraction")
@pytest.mark.parametrize("observation", [
    debt_policy.DebtObservation(kind="unknown"),
    debt_policy.DebtObservation(kind="range", lower=350000, upper=450000),
])
@pytest.mark.parametrize("message", ["400000 рублей.", "400000", "Ровно 400 тысяч рублей."])
def test_the_pending_debt_task_accepts_a_volunteered_exact_number_without_a_repeated_question(observation, message):
    state = held_debt(observation)
    prepared = core.prepare_turn(state, message, AppConfig(openrouter_api_key="synthetic-unused"), submit_delivery=False)
    policy = debt_policy.parse_policy(prepared)
    assert policy is not None and policy.amount_observation == debt_policy.DebtObservation(kind="exact", amount=400000)
    assert get_fact(prepared["facts"], "debts.total") == 400000
    assert prepared["fact_statuses"]["debts.total"] == "known"
    assert "debts.total" in prepared["_policy_received_fields"]
    assert state["script"]["asked"] == [] and not prepared.get("delivery")


@pytest.mark.usefixtures("offline_extraction")
@pytest.mark.parametrize("message", [
    "Мне 44 года.", "Мой доход 400000 рублей.", "Нужен новый кредит 400000 рублей.",
    "89991234567", "Телефон 89991234567", '"400000 рублей"',
    "Если долг 400000 рублей, что изменится?", "Вы назвали 400000 рублей.",
])
def test_other_numbers_and_hypotheses_do_not_complete_the_unasked_debt_task(message):
    observation = debt_policy.DebtObservation(kind="range", lower=350000, upper=450000)
    prepared = core.prepare_turn(held_debt(observation), message, AppConfig(openrouter_api_key="synthetic-unused"),
                                 submit_delivery=False)
    policy = debt_policy.parse_policy(prepared)
    assert policy is not None and policy.amount_observation is not None and policy.amount_observation.kind != "exact"
    assert get_fact(prepared["facts"], "debts.total") is None and not prepared.get("delivery")


@pytest.mark.usefixtures("offline_extraction")
@pytest.mark.parametrize("other_asked", ["client.name", "client.phone", "client.age"])
def test_another_spoken_field_does_not_inherit_the_pending_debt_amount(other_asked):
    state = held_debt(debt_policy.DebtObservation(kind="unknown"))
    state["script"]["asked"] = [other_asked]
    prepared = core.prepare_turn(state, "400000 рублей.", AppConfig(openrouter_api_key="synthetic-unused"),
                                 submit_delivery=False)
    assert get_fact(prepared["facts"], "debts.total") is None
    assert prepared[debt_policy.POLICY_KEY]["amount_observation"]["kind"] == "unknown"


@pytest.mark.usefixtures("offline_extraction")
@pytest.mark.parametrize("mode", ["debt", "cancelled"])
def test_a_completed_or_cancelled_debt_task_does_not_reinterpret_a_new_unasked_number(mode):
    state = debt_state(debts={"total": 350000})
    state[debt_policy.POLICY_KEY]["mode"] = mode
    state[debt_policy.POLICY_KEY]["amount_observation"] = debt_policy.DebtObservation(kind="exact", amount=350000).model_dump(mode="json")
    state["script"]["asked"] = []
    prepared = core.prepare_turn(state, "400000 рублей.", AppConfig(openrouter_api_key="synthetic-unused"),
                                 submit_delivery=False)
    assert get_fact(prepared["facts"], "debts.total") == 350000
    assert prepared[debt_policy.POLICY_KEY]["mode"] == mode and not prepared.get("delivery")
