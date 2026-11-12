"""Real policy entry points with deterministic model doubles and no outbound operations."""
from __future__ import annotations

from dataclasses import replace
import json
from typing import get_args

import pytest

import core
import llm_agent
from onloader import policy as onloader_policy
import policy_semantic_analysis
import policy_semantics
import policy_grounded_voice
import policy_voice_critic
import prompts
from dialogue_frame import DialogueFrame
from tests.test_policy_voice import debt_state, onloader_state, settings


def semantic_raw(*, user_acts=(), claims=(), style_flags=()):
    slots = dict.fromkeys(get_args(policy_semantics.ClaimPredicate))
    for item in claims:
        predicate = item["predicate"]
        if slots[predicate] is None:
            slots[predicate] = []
        slots[predicate].append({key: value for key, value in item.items() if key != "predicate"})
    return json.dumps({
        "user": {"acts": list(user_acts), "information_need": None},
        "claims": {"predicates": slots, "requests": {"phone_request": None, "transfer_consent_request": None}},
        "style": {"accept": not style_flags, "flags": list(style_flags)},
    }, ensure_ascii=False)


def act(evidence, kind="decline_transfer"):
    return {"kind": kind, "evidence": evidence, "subject": "client", "mode": "asserted",
            "scope": "current_offer" if kind == "decline_transfer" else "contact_collection"}


def claim(evidence, predicate="phone_required_to_save_chat", value=True):
    return {"predicate": predicate, "evidence": evidence, "value": value,
            "modality": "asserted", "scope": "chat"}


def install(monkeypatch, replies, analyses):
    generations, assessments = [], []
    reply_iter, analysis_iter = iter(replies), iter(analyses)

    def generate(payload, cfg, **kwargs):
        generations.append(payload)
        return next(reply_iter)

    def analyze(payload, draft, frame, cfg, **kwargs):
        assessments.append((draft, frame))
        raw = next(analysis_iter)
        latest = payload["latest_user_message"]
        parsed = policy_semantics.parse_semantics(raw, latest_user_message=latest, draft=draft)
        style = policy_voice_critic.SemanticVerdict.model_validate_json(
            json.dumps(json.loads(raw)["style"]))
        return policy_semantic_analysis.SemanticAnalysis(
            assessment=policy_voice_critic.CriticAssessment(style, "synthetic-extractor", "accepted", True),
            semantics=parsed, authority=policy_semantics.TurnAuthority.from_frame(frame, latest, draft),
        )

    monkeypatch.setattr(llm_agent, "generate_reply", generate)
    monkeypatch.setattr(policy_semantic_analysis, "analyze", analyze)
    return generations, assessments


def strict_settings():
    return replace(settings(), policy_semantic_enabled=True, extractor_model="synthetic-extractor")


def test_exact_amount_is_required_for_debt_completion_while_general_chat_remains_available():
    debt = prompts.policy_turn_instruction(DialogueFrame(None, "incomplete_held", mode="debt"))
    onloader = prompts.policy_turn_instruction(DialogueFrame(None, "awaiting_consent", mode="onloader"))
    assert "Для завершения долговой анкеты обязательно точное число" in debt
    assert "Примерную сумму или диапазон сохраняем с неопределённостью" in debt
    assert "Общие вопросы можно обсуждать здесь и до завершения анкеты" in debt
    assert "Для завершения долговой анкеты обязательно точное число" not in onloader


def test_elliptical_refusal_commits_same_chat_without_formal_consent(monkeypatch):
    user = "Ага, тогда без передачи."
    answer = "Можем спокойно обсудить всё здесь."
    generations, assessments = install(monkeypatch, [answer], [semantic_raw(user_acts=[act(user)])])
    initial = onloader_state()
    prepared = core.prepare_turn(initial, user, strict_settings(), submit_delivery=False)
    assert not onloader_policy.chat_declined(prepared)
    text = "".join(core.stream_reply(prepared, user, strict_settings()))
    committed = core.commit_turn(prepared, user, text)["current_state"]
    assert text == answer
    assert onloader_policy.chat_declined(committed)
    assert committed["onloader"]["decision"] is None
    assert committed["chat_history"][-2:] == [
        {"role": "user", "content": user}, {"role": "assistant", "content": answer}]
    assert len(generations) == len(assessments) == 1
    assert initial.get(onloader_policy.CHAT_KEY) is None


def test_semantic_flag_without_voice_keeps_the_existing_deterministic_refusal():
    cfg = replace(strict_settings(), policy_voice_enabled=False)
    state = core.prepare_turn(onloader_state(), "Не передавать.", cfg, submit_delivery=False)
    assert onloader_policy.chat_declined(state)
    assert state["onloader"]["decision"] is None


def test_user_refusal_survives_two_invalid_claim_channels(monkeypatch):
    user = "Ага, тогда без передачи."
    raw = json.loads(semantic_raw(user_acts=[act(user)]))
    raw["claims"] = {"claims": "bad-channel"}
    encoded = json.dumps(raw, ensure_ascii=False)
    generations, assessments = install(monkeypatch, ["Можем поговорить здесь."] * 2, [encoded] * 2)
    prepared = core.prepare_turn(onloader_state(), user, strict_settings(), submit_delivery=False)
    text = "".join(core.stream_reply(prepared, user, strict_settings()))
    committed = core.commit_turn(prepared, user, text)["current_state"]
    assert onloader_policy.chat_declined(committed)
    assert committed["onloader"]["decision"] is None
    assert prepared["_policy_spoken"].source == "fallback"
    assert len(generations) == len(assessments) == 2


@pytest.mark.parametrize("unsafe,expected_assessments", [
    ("Чтобы закрепить за вами ответы, нужен номер.", 2),
    ("Телефон необходим для сохранения нашего разговора.", 1),
])
def test_semantic_storage_pretext_spends_at_most_two_pairs(monkeypatch, unsafe, expected_assessments):
    raw = semantic_raw(claims=[claim(unsafe)])
    generations, assessments = install(monkeypatch, [unsafe] * 2, [raw] * 2)
    state = debt_state(debts={"total": 280000}, client={"region_class": "moscow_mo", "name": "Сергей"})
    text = core._policy_conversation(state, "Зачем вам телефон?", strict_settings())
    assert unsafe not in text
    assert state["_policy_spoken"].source == "fallback"
    # A code-rejected G2 does not need C2; semantic-only rejection still does.
    assert len(generations) == 2 and len(assessments) == expected_assessments


def test_phone_refusal_updates_policy_without_erasing_history_or_facts(monkeypatch):
    user = "Давайте обойдёмся без моего номера."
    answer = "Да, можем обсудить вопрос здесь."
    install(monkeypatch, [answer], [semantic_raw(user_acts=[act(user, "decline_phone")])])
    state = debt_state(debts={"total": 280000}, client={"region_class": "moscow_mo", "name": "Сергей"})
    before = json.dumps(state["facts"], ensure_ascii=False, sort_keys=True)
    text = core._policy_conversation(state, user, strict_settings())
    committed = core.commit_turn(state, user, text)["current_state"]
    assert committed["questionnaire_policy"]["phone_collection"] == "declined"
    assert json.dumps(committed["facts"], ensure_ascii=False, sort_keys=True) == before
    assert committed["chat_history"][-1]["content"] == answer


def test_optional_style_repair_cannot_replace_checked_first_with_false_claim(monkeypatch):
    first = "Можем поговорить здесь."
    bad = "Для сохранения разговора необходим номер."
    install(monkeypatch, [first, bad], [semantic_raw(style_flags=["unrequested_receipt"]),
                                      semantic_raw(claims=[claim(bad)])])
    state = onloader_state()
    text = core._policy_conversation(state, "Можно просто поговорить?", strict_settings())
    assert text == first
    assert state["_policy_spoken"].reason == "model_semantic_first_preserved"


def test_frame_change_cannot_publish_a_newly_normalized_unanalysed_body(monkeypatch):
    user = "Раскройте смысл предложения."
    first = ("Нужен ваш номер для консультации компании.\n\n"
             "Контакт уже есть в анкете.\n\n"
             "Здесь можно обсудить вопросы.\n\n"
             "Вы сами решаете, передавать ли сведения.")
    final = "Контакт предназначен компании для консультации по обращению. Здесь можем просто поговорить."

    def analysis_for(text):
        purpose = claim(text.split("\n\n")[0].split(". ")[0], "consultation_purpose")
        purpose["scope"] = "current_offer"
        raw = json.loads(semantic_raw(claims=[purpose]))
        raw["user"]["information_need"] = {"kind": "transfer_purpose", "evidence": user}
        return json.dumps(raw, ensure_ascii=False)

    generations, assessments = install(monkeypatch, [first, final], [analysis_for(first), analysis_for(final)])
    frame = DialogueFrame(None, "awaiting_consent", known_fields=frozenset({"client.phone"}), mode="onloader",
                          offer_present=True, offer_status="awaiting_consent", offer_recipient_name="Тестовая компания")
    result = policy_grounded_voice.render({"latest_user_message": user}, strict_settings(), frame=frame,
                                         fallback="Можем поговорить здесь.")
    assert result.spoken.text == final
    assert len(generations) == len(assessments) == 2
    assert assessments[0][0] == first
    assert assessments[1][0] == final
    assert result.spoken.source == "model"
