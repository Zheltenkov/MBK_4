"""Code-owned capabilities remain authoritative after collecting the debt form."""
from dataclasses import replace
import socket

import pytest

import core
from dialogue_frame import DialogueFrame
import policy_voice
from tests import test_policy_v22_independent as entry
from tests.test_policy_v22_independent import actual_turn
from tests.test_policy_voice import debt_state


BAD = [
    "Что именно подойдёт в вашем случае, станет ясно по ссылке после короткой анкеты.",
    "Чтобы понять, что именно может подойти, нужно оформить заявку — это ни к чему не обязывает.",
    "Готовы перейти к оформлению?",
    "Что именно доступно в вашей ситуации, станет понятно только после оформления заявки.",
    "Оформление здесь недоступно, но давайте заполним новую анкету.",
    "Пришлю ссылку, по ней увидите подходящие варианты.",
    "Заполните новую анкету для заявки.",
    "Без заявки я не смогу объяснить, что вам подходит.",
]
SAFE = "При реструктуризации меняют условия договора. Сам долг остаётся. Что подойдёт вам, пока неясно."


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Debt continuation tests cannot use a real network or send leads")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(core.delivery, "submit", forbidden)


def frame():
    return DialogueFrame(None, "collected_held", known_fields=frozenset({
        "debts.total", "client.region_class", "client.name", "client.phone",
    }))


@pytest.mark.parametrize("text", BAD)
def test_unavailable_continuation_rejects_the_whole_candidate(text):
    assert policy_voice._model_reply(text, frame(), "offline_capability") is None


@pytest.mark.parametrize("text", [
    "Ссылки здесь пока нет. Оформлять анкету заново не нужно.",
    "Чтобы объяснить различие, не нужно оформлять заявку.",
    "Я не могу обещать, что после заявки станет ясно, что вам подходит.",
    "При обращении в банк иногда требуется подать заявку.",
    "Если в другом сервисе нужна анкета, проверьте его условия.",
    "Фраза «готовы перейти к оформлению?» была вопросом, а не возможностью этого чата.",
    "Что подойдёт вам, по этим данным пока неизвестно.",
    "Хотите обсудить, что такое реструктуризация?",
])
def test_denials_general_procedures_and_actual_chat_explanations_remain_available(text):
    reply = policy_voice._model_reply(text, frame(), "offline_capability")
    assert reply is not None and reply.text == text


@pytest.mark.parametrize("other", [
    replace(frame(), status="collecting_contacts", expected="client.phone", known_fields=frozenset()),
    replace(frame(), mode="onloader", offer_present=True, offer_status="awaiting_consent"),
    replace(frame(), offer_present=True, offer_status="awaiting_consent"),
])
def test_the_closed_debt_capability_does_not_change_other_operational_states(other):
    assert policy_voice._model_reply("Хотите перейти к оформлению заявки?", other, "other_scope") is not None


@pytest.mark.parametrize("bad", BAD[:4])
def test_normal_semantic_entry_repairs_capability_claims_even_when_the_model_critic_accepts(monkeypatch, bad):
    state = debt_state(debts={"total": 400000}, client={
        "region_class": "rf_other", "name": "Тест", "phone": "+70000000401",
    })
    answer, prepared, committed, calls = actual_turn(
        monkeypatch, state, "Что дальше?", replies=[bad, SAFE],
    )
    assert answer == SAFE and prepared["_policy_spoken"].source == "model"
    assert committed["facts"] == state["facts"] and not committed.get("delivery")
    assert len([c for c in calls if c["model"] == "synthetic-analysis"]) == 2


@pytest.mark.parametrize("semantic", [False, True])
def test_two_invalid_generations_are_not_salvaged_as_a_new_action(monkeypatch, semantic):
    state = debt_state(debts={"total": 400000}, client={
        "region_class": "rf_other", "name": "Тест", "phone": "+70000000401",
    })
    original_config = entry.AppConfig
    monkeypatch.setattr(entry, "AppConfig", lambda **values: original_config(
        **(values | {"policy_semantic_enabled": semantic})))
    # The second candidate has a useful sentence, but its whole action is invalid.
    answer, prepared, committed, _ = actual_turn(
        monkeypatch, state, "Что дальше?", replies=[BAD[0], SAFE + "\n\n" + BAD[2]],
    )
    assert prepared["_policy_spoken"].source == "fallback"
    assert answer != SAFE and "оформлению" not in answer and "по ссылке" not in answer
    assert committed["facts"] == state["facts"] and not committed.get("delivery")


@pytest.mark.parametrize("text", [
    "Если решите обратиться к кредитору, можете подать заявку на реструктуризацию в свой банк.",
    "Чтобы узнать условия реструктуризации в банке, нужно подать заявку в банк.",
])
def test_an_explicit_offplatform_application_step_is_educational(text):
    reply = policy_voice._model_reply(text, frame(), "external_operation")
    assert reply is not None and reply.text == text


@pytest.mark.parametrize("text", [
    "Нашу анкету нужно оформить, чтобы подать заявку в банк.",
    "Оформите новую анкету, затем сможете подать заявку в банк.",
    "Давайте подадим заявку в банк.",
    "Здесь можно подать заявку в банк, готовы перейти к оформлению?",
])
def test_a_bank_name_does_not_authorize_a_current_chat_application(text):
    assert policy_voice._model_reply(text, frame(), "unavailable_chat_operation") is None


@pytest.mark.parametrize("semantic", [False, True])
def test_a_valid_second_generation_retains_its_model_wording_in_both_renderers(monkeypatch, semantic):
    state = debt_state(debts={"total": 400000}, client={
        "region_class": "rf_other", "name": "Тест", "phone": "+70000000401",
    })
    original_config = entry.AppConfig
    monkeypatch.setattr(entry, "AppConfig", lambda **values: original_config(
        **(values | {"policy_semantic_enabled": semantic})))
    answer, prepared, committed, _ = actual_turn(monkeypatch, state, "Что дальше?", replies=[BAD[0], SAFE])
    assert answer == SAFE and prepared["_policy_spoken"].source == "model"
    assert committed["facts"] == state["facts"] and not committed.get("delivery")


def test_legacy_regeneration_receives_the_same_typed_capability_failure(monkeypatch):
    flags = []
    def regeneration(current_frame, semantic_flags):
        flags.extend(semantic_flags)
        return "Code-owned regeneration instruction"
    monkeypatch.setattr(policy_voice.prompts, "policy_regeneration_instruction", regeneration)
    payload = policy_voice._revision_payload({"facts": {}, "latest_user_message": "Что дальше?"}, BAD[0], frame(), None, None)
    assert flags == ["unavailable_debt_continuation"]
    assert payload["turn_instruction"] == "Code-owned regeneration instruction"
    assert BAD[0] not in payload["turn_instruction"]



def test_a_hypothetical_future_integration_does_not_authorize_a_current_form_CTA():
    text = "Если передачу подключат, давайте оформим новую заявку."
    assert policy_voice._model_reply(text, frame(), "conditional_scope") is None


@pytest.mark.parametrize("text", [
    "Если передачу подключат — давайте оформим новую заявку.",
    "Подайте заявку здесь, чтобы узнать доступные варианты.",
])
def test_imperative_operation_forms_and_a_hypothetical_prefix_do_not_grant_a_capability(text):
    assert policy_voice._model_reply(text, frame(), "operation_role") is None


@pytest.mark.parametrize("text", [
    "Могу прислать ссылку на анкету.",
    "Можем выслать ссылку для оформления.",
    "Могу показать страницу с результатом.",
    "Можно открыть страницу с результатом.",
    "Можете перейти по ссылке для оформления.",
])
def test_known_link_operation_families_include_their_modal_infinitives(text):
    assert policy_voice._model_reply(text, frame(), "unavailable_link_operation") is None


@pytest.mark.parametrize("text", [
    "Я не могу прислать ссылку на анкету.",
    "Здесь не можем выслать ссылку для оформления.",
])
def test_local_denials_of_link_operations_remain_available(text):
    reply = policy_voice._model_reply(text, frame(), "unavailable_link_denial")
    assert reply is not None and reply.text == text
