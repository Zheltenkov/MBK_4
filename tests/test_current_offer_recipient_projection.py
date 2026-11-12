"""The current typed card provides the public recipient label independently of chat history."""
from copy import deepcopy
from io import BytesIO
import json

import pytest

import core
from config import AppConfig
from dialogue_frame import DialogueFrame
import prompts
from tests.test_policy_voice import onloader_state


pytestmark = pytest.mark.usefixtures("real_policy_voice_critic")


def test_both_real_model_requests_receive_the_current_card_label_without_private_authority(monkeypatch):
    state = onloader_state()
    state["onloader"]["offer"]["partner_name"] = 'Текущая компания "Консультация"'
    state["facts"] = {"client": {"name": "Ирина Синтетическая", "phone": "+70000000401"}}
    state["chat_history"] = [{"role": "assistant", "content": "В прежней карточке была Старая компания."}]
    facts = deepcopy(state["facts"])
    bodies = []
    final = 'В текущей карточке указана компания «Текущая компания "Консультация"».'

    def model_open(configuration, request):
        body = json.loads(request.data)
        bodies.append(body)
        content = (json.dumps({"accept": True, "flags": []})
                   if configuration.model == configuration.extractor_model else final)
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    def no_delivery(*args, **kwargs):
        pytest.fail("A recipient explanation cannot send an application")

    monkeypatch.setattr(core.llm_agent, "_urlopen", model_open)
    monkeypatch.setattr(core.delivery, "submit", no_delivery)
    monkeypatch.setattr(core, "_record_transcript", lambda *args, **kwargs: None)
    config = AppConfig(openrouter_api_key="synthetic-unused", extractor_model="synthetic-critic",
                       policy_voice_enabled=True)
    message = "Кому уйдут сведения?"
    prepared = core.prepare_turn(state, message, config, submit_delivery=False)
    speech = "".join(core.stream_reply(prepared, message, config))
    committed = core.commit_turn(prepared, message, speech)["current_state"]
    assert speech == final
    assert len(bodies) == 2
    instruction = bodies[0]["messages"][2]["content"]
    data = instruction.split("Данные текущей карточки получателя\n", 1)[1].split("\nНазвание", 1)[0]
    assert json.loads(data) == {"recipient_name": state["onloader"]["offer"]["partner_name"]}
    diagnostic = json.loads(bodies[-1]["messages"][-1]["content"])
    assert diagnostic["task"]["verified_context"]["current_offer_recipient_name"] == state["onloader"]["offer"]["partner_name"]
    assert diagnostic["history"][0]["content"] == state["chat_history"][0]["content"]
    wire = json.dumps(bodies, ensure_ascii=False)
    assert all(value not in wire for value in ("private-operation", "private-handle-synthetic",
                                              "private-presentation", "+70000000401", "Ирина"))
    assert committed["facts"] == facts
    assert committed["onloader"]["decision"] is None
    assert not committed.get("delivery")


def test_a_missing_current_card_label_cannot_be_recovered_as_a_fact_from_old_history():
    frame = DialogueFrame(None, "pending", mode="onloader")
    instruction = prompts.policy_turn_instruction(frame)
    assert "название получателя не подтверждено" in instruction
    assert "Данные текущей карточки получателя" not in instruction
    diagnostic = json.loads(prompts.policy_critic_messages(frame, draft="Пока название не подтверждено.",
        history=[{"role": "assistant", "content": "Ранее была Старая компания."}],
        latest_user_message="Кому уйдут данные?")[-1]["content"])
    assert "current_offer_recipient_name" not in diagnostic["task"]["verified_context"]


def test_an_existing_offer_keeps_its_current_public_label_in_the_debt_chat_too():
    frame = DialogueFrame(None, "collected_held", mode="debt", offer_present=True,
                          offer_recipient_name="Текущая компания")
    instruction = prompts.policy_turn_instruction(frame)
    assert '"recipient_name": "Текущая компания"' in instruction
    diagnostic = json.loads(prompts.policy_critic_messages(frame, draft="В карточке Текущая компания.",
        history=[], latest_user_message="Кому уйдут сведения?")[-1]["content"])
    assert diagnostic["task"]["verified_context"]["current_offer_recipient_name"] == "Текущая компания"


def test_a_stale_label_without_an_actual_offer_is_not_current_card_context():
    frame = DialogueFrame(None, "pending", mode="onloader", offer_present=False,
                          offer_recipient_name="Старая компания")
    instruction = prompts.policy_turn_instruction(frame)
    assert "название получателя не подтверждено" in instruction
    assert '"recipient_name"' not in instruction
    diagnostic = json.loads(prompts.policy_critic_messages(frame, draft="Название пока не подтверждено.",
        history=[], latest_user_message="Кому уйдут сведения?")[-1]["content"])
    assert "current_offer_recipient_name" not in diagnostic["task"]["verified_context"]
