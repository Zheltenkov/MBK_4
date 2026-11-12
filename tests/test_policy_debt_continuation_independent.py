"""Independent capability boundaries after the debt questionnaire is complete."""
from copy import deepcopy
import socket

import pytest

import core
from dialogue_frame import DialogueFrame
import policy_voice
from tests.test_policy_v22_independent import actual_turn
from tests.test_policy_voice import debt_state


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Independent continuation tests cannot use the network or send leads")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(core.delivery, "submit", forbidden)


def held_state():
    return debt_state(debts={"total": 400000}, client={
        "region_class": "rf_other", "region": "Казань", "name": "Тест", "phone": "+70000000401",
    })


@pytest.mark.parametrize("latest,bad", [
    ("Поняла. Я в Казани.", "Что именно подойдёт в вашем случае, станет ясно по ссылке после короткой анкеты. "
     "Хотите продолжить и посмотреть варианты?"),
    ("Да, Татарстан.", "Чтобы понять, что именно может подойти, нужно оформить заявку — это ни к чему "
     "не обязывает. Готовы перейти к оформлению?"),
    ("Чем реструктуризация отличается от списания?", "Что именно доступно в вашей ситуации с долгом "
     "400 тысяч, станет понятно только после оформления заявки."),
])
def test_recorded_future_capabilities_are_repaired_despite_an_accepting_model_critic(monkeypatch, latest, bad):
    state = held_state()
    before = deepcopy(state["facts"])
    safe = "При реструктуризации меняют условия договора. Сам долг остаётся. Что подойдёт вам, пока неясно."
    answer, prepared, committed, calls = actual_turn(monkeypatch, state, latest, replies=[bad, safe])
    assert answer != bad
    assert "по ссылке" not in answer and "перейти к оформлению" not in answer
    assert "только после оформления" not in answer
    assert prepared["_policy_spoken"].asked_field is None
    assert committed["facts"] == before and not committed.get("delivery")
    assert committed["questionnaire_policy"]["phone_collection"] == "open"
    assert any(call["model"] == "synthetic-analysis" for call in calls)


@pytest.mark.parametrize("latest", ["Как оформить заявку?", "Можно оставить заявку?", "Где ссылка на анкету?"])
def test_a_direct_application_question_can_receive_an_honest_capability_answer(monkeypatch, latest):
    state = held_state()
    safe = "Данные уже собраны. Передача заявки из этого чата пока не подключена. " \
           "Вопросы по долгам можно обсудить здесь."
    answer, prepared, committed, _ = actual_turn(monkeypatch, state, latest, replies=[safe] * 2)
    assert answer == safe
    assert prepared["_policy_spoken"].asked_field is None
    assert committed["facts"] == state["facts"] and not committed.get("delivery")


@pytest.mark.parametrize("text", [
    "Я не могу обещать ссылку или оформление заявки в этом чате.",
    "Если сервис передачи когда-нибудь подключат, порядок оформления потребуется проверить отдельно.",
    "Внесудебное банкротство оформляют через МФЦ при соблюдении условий закона.",
    "Фраза «нужно оформить заявку» сама по себе не подтверждает, что здесь доступна такая возможность.",
    "При реструктуризации меняют условия договора, а сам долг остаётся.",
])
def test_negation_hypothetical_external_legal_steps_and_quoted_words_remain_available(text):
    frame = DialogueFrame(None, "collected_held", known_fields=frozenset({
        "debts.total", "client.region_class", "client.name", "client.phone",
    }))
    reply = policy_voice._model_reply(text, frame, "independent_capability_contrast")
    assert reply is not None and reply.text == text


def test_collecting_a_missing_phone_remains_an_available_ordinary_intake_question():
    frame = DialogueFrame("client.phone", "collecting_contacts", known_fields=frozenset({
        "debts.total", "client.region_class", "client.name",
    }))
    text = "Какой номер телефона указать для заявки?"
    reply = policy_voice._model_reply(text, frame, "independent_collecting")
    assert reply is not None and reply.asked_field == "client.phone"


def test_an_actual_backend_onloader_offer_keeps_its_own_confirmation_path():
    frame = DialogueFrame(None, "awaiting_consent", mode="onloader", offer_present=True,
                          offer_status="awaiting_consent")
    text = "Хотите перейти к оформлению заявки?"
    reply = policy_voice._model_reply(text, frame, "independent_current_offer")
    assert reply is not None and reply.text == text


@pytest.mark.parametrize("text", [
    "Если решите обратиться к кредитору, можете подать заявку на реструктуризацию в свой банк.",
    "Чтобы узнать условия реструктуризации в банке, нужно подать заявку в банк.",
])
def test_an_explicit_external_bank_step_does_not_become_this_chats_missing_application(text):
    frame = DialogueFrame(None, "collected_held")
    reply = policy_voice._model_reply(text, frame, "independent_external_bank")
    assert reply is not None and reply.text == text
