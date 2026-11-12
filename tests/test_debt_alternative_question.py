"""Alternative-procedure questions preserve the active debt goal and contact choice."""
from copy import deepcopy

import pytest

import core
import debt_policy
import llm_agent
from tests.test_policy_grounded_voice import install, semantic_raw, strict_settings
from tests.test_policy_voice import debt_state


QUESTIONS = [
    "А без банкротства можно обойтись?",
    "Без банкротства обойтись можно?",
    "Обойтись можно без банкротства?",
    "Мне без банкротства можно помочь?",
    "Банкротство мне не нужно?",
    "Можно ли, пожалуйста, без банкротства?",
    "Москва, а без банкротства можно?",
    "Без банкротства, если возможно?",
    "Без банкротства.",
    "А без банкротства можно обойтись",
    "Можно обойтись без банкротства",
]


@pytest.mark.parametrize("message", QUESTIONS)
def test_discussing_an_alternative_does_not_turn_word_order_into_a_declined_goal(message):
    initial = debt_policy.DebtPolicyState(phone_collection="declined")
    selected = debt_policy.select_policy({debt_policy.POLICY_KEY: initial.as_state()}, message)
    assert selected == initial


@pytest.mark.parametrize("message", [
    "Банкротство мне не нужно.",
    "Не хочу банкротство. А без него можно обойтись?",
    "Я не хочу банкротство, а кредит можно?",
    "Не хочу банкротство; без него какие варианты?",
    "Стоп.",
])
def test_asserted_debt_refusal_and_stop_keep_their_existing_effect(message):
    initial = debt_policy.DebtPolicyState(phone_collection="declined")
    selected = debt_policy.select_policy({debt_policy.POLICY_KEY: initial.as_state()}, message)
    assert selected is not None and selected.mode == "cancelled" and selected.phone_collection == "declined"


@pytest.mark.parametrize("message", [
    'В примере написано "Банкротство мне не нужно".',
    "Если я не хочу банкротство, что это значит?",
    "Мой друг говорит: не хочу банкротство.",
])
def test_quotes_hypotheses_and_someone_elses_refusal_do_not_cancel_the_clients_goal(message):
    initial = debt_policy.DebtPolicyState(phone_collection="declined")
    selected = debt_policy.select_policy({debt_policy.POLICY_KEY: initial.as_state()}, message)
    assert selected == initial


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Alternative-question controls must not call a provider or delivery service")

    monkeypatch.setattr(llm_agent, "_urlopen", forbidden)
    monkeypatch.setattr(llm_agent, "extract_facts", lambda *args, **kwargs: {"fact_updates": [], "internal_summary": ""})
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *args, **kwargs: deepcopy(facts))
    monkeypatch.setattr(core.delivery, "submit", forbidden)
    monkeypatch.setattr(core, "_record_transcript", lambda *args, **kwargs: None)


@pytest.mark.parametrize("message", QUESTIONS[:3] + QUESTIONS[-2:])
def test_an_actual_followup_keeps_the_same_history_debt_facts_and_phone_refusal(monkeypatch, message):
    state = debt_state(debts={"total": 280000}, client={"region_class": "moscow_mo", "name": "Сергей"})
    state[debt_policy.POLICY_KEY]["phone_collection"] = "declined"
    state["script"]["asked"] = []
    state["chat_history"] = [
        {"role": "user", "content": "Номер оставлять не хочу."},
        {"role": "assistant", "content": "Можем продолжить разговор здесь."},
    ]
    facts, history = deepcopy(state["facts"]), deepcopy(state["chat_history"])
    answer = "Можно обсуждать изменение условий кредита с кредитором. Согласие на конкретные условия пока не подтверждено."
    install(monkeypatch, [answer] * 2, [semantic_raw()] * 2)
    prepared = core.prepare_turn(state, message, strict_settings(), submit_delivery=False)
    assert prepared[debt_policy.POLICY_KEY]["mode"] == "debt"
    text = "".join(core.stream_reply(prepared, message, strict_settings()))
    committed = core.commit_turn(prepared, message, text)["current_state"]
    assert committed[debt_policy.POLICY_KEY]["mode"] == "debt"
    assert committed[debt_policy.POLICY_KEY]["phone_collection"] == "declined"
    assert committed["facts"] == facts and committed["chat_history"][:len(history)] == history
    assert committed["chat_history"][-2:] == [{"role": "user", "content": message}, {"role": "assistant", "content": text}]
    assert prepared["_policy_spoken"].asked_field is None and not committed.get("delivery")
