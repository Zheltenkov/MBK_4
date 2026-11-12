"""Already discarded brief drafts do not spend a semantic call or forge its result."""
from dataclasses import replace
import json
import logging

import pytest

from dialogue_frame import DialogueFrame
import policy_grounded_voice
from tests.test_policy_grounded_voice import act, install, semantic_raw, strict_settings
from tests.test_policy_v31_receipts import offline  # noqa: F401
from tests.test_policy_v34_brief_render import http_turn


@pytest.mark.usefixtures("real_policy_voice_critic")
def test_a_rejected_creditor_question_uses_two_generations_and_one_actual_final_critic(monkeypatch, caplog):
    bad = "Сколько всего у вас кредиторов?"
    with caplog.at_level(logging.INFO, logger="policy_grounded_voice"):
        result, generations, requests = http_turn(
            monkeypatch, semantic=True, first_region=True, replies=(bad, "Приняла."),
        )
    assert result.bubbles == ["Приняла."] and len(generations) == 2 and len(requests) == 3
    assert result.voice["candidates"] == [bad, "Приняла."]
    assert len(result.voice["critic_assessments"]) == len(result.voice["diagnostic_assessments"]) == 1
    assert result.voice["critic_calls"] == 1
    critic_inputs = [json.loads(request["messages"][-1]["content"]) for request in requests
                     if request["model"] == "synthetic-critic"]
    assert len(critic_inputs) == 1 and critic_inputs[0]["draft"] == "Приняла."
    rejected = [record for record in caplog.records if record.message == "policy_brief_code_rejection"]
    assert len(rejected) == 1 and "unallowed_collection" in rejected[0].code_repair_flags
    assert bad not in caplog.text
    assert not result.state.get("delivery")


@pytest.mark.usefixtures("real_policy_voice_critic")
def test_a_second_invalid_brief_draft_uses_trusted_fallback_without_inventing_a_critic(monkeypatch):
    bad = "Сколько всего у вас кредиторов?"
    result, generations, requests = http_turn(monkeypatch, semantic=True, first_region=True, replies=(bad, bad))
    assert len(generations) == len(requests) == 2
    assert result.voice["source"] == "fallback" and bad not in " ".join(result.bubbles)
    assert result.voice["critic_calls"] == 0 and result.voice["critic_assessments"] == []
    assert result.voice["diagnostic_assessments"] == [] and result.voice["semantic_analyses"] == []
    assert not result.state.get("delivery")


@pytest.mark.parametrize("changes,latest,bad", [
    ({"plain_reply_fields": frozenset()}, "Да, Татарстан.", "Сколько у вас кредиторов?"),
    ({"conversation_paused": True}, "Да, Татарстан.", "Сколько у вас кредиторов?"),
    ({"mode": "onloader", "offer_present": True}, "Да, Татарстан.", "Сколько у вас кредиторов?"),
    ({"question_goal": "debt_explanation"}, "Что такое банкротство?", "На какой номер вам позвонить?"),
    ({"refused_fields": frozenset({"client.phone"})}, "Номер не дам.", "На какой номер вам позвонить?"),
    ({}, "Номер не дам.", "На какой номер вам позвонить?"),
    ({}, "Да, Татарстан.", "Алименты и любое возмещение вреда остаются."),
])
def test_ambiguous_refusal_offer_paused_faq_and_legal_paths_keep_their_existing_semantic_calls(monkeypatch, changes, latest, bad):
    calls, analyses = install(monkeypatch, [bad, "Понимаю."], [semantic_raw(), semantic_raw()])
    frame = replace(DialogueFrame(None, "collected_held", known_fields=frozenset({"client.region_class", "client.phone"}),
                                  plain_reply_fields=frozenset({"client.region_class"})), **changes)
    result = policy_grounded_voice.render({"latest_user_message": latest}, strict_settings(), frame=frame, fallback="Понимаю.")
    assert result.spoken.text == "Понимаю." and len(calls) == len(analyses) == result.spoken.critic_calls == 2


def test_the_actual_final_critic_must_still_validate_incoming_user_acts(monkeypatch):
    calls, analyses = install(
        monkeypatch, ["Сколько у вас кредиторов?", "Приняла."],
        [semantic_raw(user_acts=[act("Номер не дам.", kind="decline_phone")])],
    )
    frame = DialogueFrame(None, "collected_held", known_fields=frozenset({"client.region_class"}),
                          plain_reply_fields=frozenset({"client.region_class"}))
    result = policy_grounded_voice.render({"latest_user_message": "Да, Татарстан."}, strict_settings(),
                                          frame=frame, fallback="Понимаю.")
    assert len(calls) == 2 and len(analyses) == result.spoken.critic_calls == 1
    assert result.spoken.source == "fallback" and result.paused
    assert result.applied_user_acts == ()
