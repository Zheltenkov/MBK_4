"""Recorded visible-placeholder and recipient-scope regressions without network."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

import core
import llm_agent
from config import AppConfig
from dialogue_frame import DialogueFrame
from onloader import policy as onloader_policy
from pii_masking import contains_private_placeholder
import policy_voice
from tests.test_policy_voice import debt_state, onloader_state

pytestmark = pytest.mark.usefixtures("real_policy_voice_critic")


@pytest.mark.parametrize("text", ["[Имя], можно продолжить здесь.", "[ФИО], напишите вопрос.",
                                   "Ответ для [фамилия].", "[ дата рождения ]", "[точный адрес]"])
def test_bare_personal_mask_labels_cannot_be_visible_reply_text(text):
    assert contains_private_placeholder(text)
    assert not policy_voice.validate_reply(text, None)


@pytest.mark.parametrize("text", [
    "Реструктуризация [изменение графика выплат] сама по себе не списывает долг.",
    "Можно продолжить здесь [без передачи данных].",
    "В примере указан пункт [1].",
    "Имя повторять не нужно, можно задать вопрос.",
])
def test_meaningful_brackets_and_ordinary_personal_field_words_are_not_mask_tokens(text):
    assert not contains_private_placeholder(text)


@pytest.mark.parametrize("text", [
    "Данные передаются только в «Тестовую компанию» — это указано в карточке выше.",
    "Других получателей сейчас нет.",
    "Данные увидит исключительно эта компания.",
    "Только эта компания получит данные.",
    "Только «Тестовая компания» получит данные.",
])
@pytest.mark.parametrize("mode", ["onloader", "debt"])
def test_a_current_card_does_not_prove_exclusive_actual_recipients(text, mode):
    frame = DialogueFrame(None, "onloader", mode=mode, offer_present=True,
                          offer_status="awaiting_consent")
    assert policy_voice._unsupported_action(text, frame)


@pytest.mark.parametrize("text", [
    "В карточке получателем указана «Тестовая компания».",
    "В этой карточке указан только один получатель.",
    "Из карточки известно только название получателя.",
    "Я не утверждаю, что данные уйдут только этой компании.",
    "Нельзя гарантировать, что других получателей нет.",
    "Я не могу гарантировать, что данные уйдут только этой компании.",
    "Данные не передаются только одной компании.",
    "Если данные уходят только одному получателю, это отдельное условие.",
    "Если других получателей нет, это требует подтверждения.",
    "Вы спрашиваете: «Других получателей нет?»",
    "Других получателей нет?",
    "Только после вашего согласия компания получит данные.",
    "Данные передаются только с вашего разрешения.",
    "Данные передаются только в случае вашего согласия.",
])
@pytest.mark.parametrize("mode", ["onloader", "debt"])
def test_declared_recipient_negation_quotation_and_conditional_premises_are_not_global_claims(text, mode):
    frame = DialogueFrame(None, "onloader", mode=mode, offer_present=True,
                          offer_status="awaiting_consent")
    assert not policy_voice._unsupported_action(text, frame)


def test_a_local_disclaimer_does_not_authorize_a_later_exclusive_transfer_claim():
    frame = DialogueFrame(None, "onloader", mode="onloader", offer_present=True,
                          offer_status="awaiting_consent")
    assert policy_voice._unsupported_action(
        "Я не утверждаю, что других получателей нет. Данные уйдут только этой компании.", frame)
    assert policy_voice._unsupported_action(
        "Если вы подтвердите передачу, данные уйдут только этой компании.", frame)


@pytest.mark.parametrize("kind", ["placeholder", "recipient_scope"])
def test_actual_model_boundary_repairs_the_recorded_issue_before_the_critic_without_state_actions(monkeypatch, kind):
    config = AppConfig(openrouter_api_key="synthetic-unused", extractor_model="synthetic-distinct-critic",
                       debt_policy_enabled=True, policy_voice_enabled=True)
    if kind == "placeholder":
        state = debt_state(client={"region_class": "rf_other"}, debts={"total": 400000})
        message = "Ирина."
        bad = ("[Имя], чтобы оформить анкету, нужен контактный номер. "
               "Можете оставить его сейчас или продолжить без него — ответы сохранятся в любом случае.")
        good = "Оставьте номер для оформления анкеты, если хотите. Обсудить вопросы можно и без него."
    else:
        state = onloader_state()
        state["onloader"]["offer"]["partner_name"] = "Тестовая компания"
        state["facts"] = {"client": {"name": "Ирина", "phone": "+70000000401"}}
        message = "А кому уйдут данные?"
        bad = ("Данные передаются только в «Тестовую компанию» — это указано в карточке выше. "
               "Других получателей сейчас нет.")
        good = ("В карточке получателем указана «Тестовая компания» для консультации. "
                "Общаться здесь можно и без передачи.")
    replies = iter([bad, good])
    bodies = []

    def post(settings, body, **kwargs):
        assert kwargs["max_attempts"] == 1
        bodies.append(deepcopy(body))
        text = json.dumps({"accept": True, "flags": []}) if settings.model == settings.extractor_model else next(replies)
        return {"choices": [{"message": {"content": text}}]}

    def forbidden(*args, **kwargs):
        pytest.fail("Recorded regressions cannot use network or submit an application")

    monkeypatch.setattr(llm_agent, "_post", post)
    monkeypatch.setattr(llm_agent, "_urlopen", forbidden)
    monkeypatch.setattr(llm_agent, "extract_facts", lambda *args, **kwargs: {})
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *args, **kwargs: deepcopy(facts))
    monkeypatch.setattr(core.delivery, "submit", forbidden)
    prepared = core.prepare_turn(state, message, config, submit_delivery=False)
    before_facts = deepcopy(prepared["facts"])
    speech = "".join(core.stream_reply(prepared, message, config))
    committed = core.commit_turn(prepared, message, speech)["current_state"]
    assert speech == good
    assert [body["model"] for body in bodies] == [config.model, config.model, config.extractor_model]
    assert json.loads(bodies[-1]["messages"][-1]["content"])["draft"] == speech
    assert "[Имя]" not in speech
    assert all(value not in json.dumps(bodies, ensure_ascii=False) for value in ("Ирина", "+70000000401"))
    assert committed["facts"] == before_facts
    assert not committed.get("delivery")
    if kind == "recipient_scope":
        assert committed["onloader"]["decision"] is None
        followup_config = replace(config, policy_voice_enabled=False)
        followup = core.prepare_turn(committed, "Нет, не разрешаю передачу.", followup_config,
                                     submit_delivery=False)
        assert onloader_policy.chat_declined(followup)
        assert followup["onloader"]["decision"] is None
        assert followup["chat_history"] == committed["chat_history"]
