"""Simple proven field replies use one G+C while safety repairs keep their budget."""
from dataclasses import replace

import pytest

from dialogue_frame import DialogueFrame
import core
import policy_voice
import policy_voice_critic
from tests.test_policy_grounded_voice import install, semantic_raw, strict_settings
from tests.test_policy_v31_receipts import actual_turn, held_state, offline  # noqa: F401
from tests.test_policy_v34_brief_render import http_turn


@pytest.mark.parametrize("semantic", [False, True])
@pytest.mark.parametrize("user,generated,served", [
    ("Поняла. Я в Казани.", "Хорошо, Казань.", "Хорошо."),
    ("Да, Татарстан.", "Приняла, Татарстан.", "Приняла."),
    ("Казань.", "Казань приняла.", "Приняла."),
])
def test_the_original_model_ack_is_kept_and_the_only_critic_sees_the_served_text(monkeypatch, semantic, user, generated, served):
    answer, spoken, state, calls, analyses = actual_turn(monkeypatch, user, [generated, "Понимаю."], semantic=semantic)
    expected_critics = int(not semantic or served != "Хорошо.")
    assert answer == served and len(calls) == 1 and spoken.critic_calls == expected_critics
    assert spoken.asked_field is None and not state.get("delivery")
    assert state["facts"]["client"]["phone"] == "+70000000001"
    if semantic and expected_critics:
        assert analyses[0][0] == served


@pytest.mark.usefixtures("real_policy_voice_critic")
@pytest.mark.parametrize("semantic", [False, True])
def test_http_evidence_retains_raw_generation_but_checks_the_normalized_reply(monkeypatch, semantic):
    result, generations, calls = http_turn(
        monkeypatch, semantic=semantic, first_region=True, replies=("Хорошо, Казань.", "Понимаю."),
    )
    assert result.bubbles == ["Хорошо."] and len(generations) == 1 and len(calls) == (1 if semantic else 2)
    assert result.voice["candidates"] == ["Хорошо, Казань."]
    if semantic:
        assert result.voice["critic_assessments"] == [] and result.voice["critic_calls"] == 0
    else:
        assert result.voice["critic_assessments"][0]["verdict"]["accept"]


@pytest.mark.parametrize("semantic", [False, True])
def test_a_repeated_hard_valid_brief_ack_does_not_trigger_another_generation(monkeypatch, semantic):
    state = held_state()
    state["chat_history"] = [{"role": "assistant", "content": "Угу."}]
    answer, spoken, _, calls, _ = actual_turn(monkeypatch, "Да, Татарстан.", ["Угу."] * 2,
                                             semantic=semantic, state=state)
    assert answer == "Угу." and len(calls) == 1 and spoken.critic_calls == (0 if semantic else 1)


@pytest.mark.parametrize("user,candidate", [
    ("Мне страшно.", "Приняла, Татарстан."),
    ("Татарстан правильно записали?", "Приняла, Татарстан."),
    ("Да, Татарстан.", "Приняла, Татарстан. Понимаю, что это непросто."),
    ("Да, Татарстан.", "Регион ясен."),
])
def test_unknown_mixed_support_confirmation_and_passive_statements_are_not_rewritten(monkeypatch, user, candidate):
    answer, _, _, _, _ = actual_turn(monkeypatch, user, [candidate] * 2)
    assert answer == candidate


@pytest.mark.parametrize("semantic", [False, True])
def test_a_hard_invalid_phone_question_still_uses_the_existing_repair(monkeypatch, semantic):
    answer, spoken, _, calls, _ = actual_turn(
        monkeypatch, "Да, Татарстан.", ["На какой номер вам позвонить?", "Понимаю."], semantic=semantic,
    )
    assert answer == "Понимаю." and len(calls) == 2 and spoken.asked_field is None


@pytest.mark.parametrize("semantic", [False, True])
def test_a_rejected_first_critic_verdict_does_not_use_the_fast_acceptance_gate(monkeypatch, semantic):
    calls, _ = install(monkeypatch, ["Приняла, Татарстан.", "Понимаю."],
                       [semantic_raw(style_flags=["wrong_current_turn"]), semantic_raw()])
    verdicts = iter([policy_voice_critic.SemanticVerdict(accept=False, flags=["wrong_current_turn"]),
                     policy_voice_critic.SemanticVerdict(accept=True, flags=[])])
    monkeypatch.setattr(policy_voice_critic, "assess", lambda *args, **kwargs: policy_voice_critic.CriticAssessment(
        next(verdicts), "synthetic-critic", "semantic_rejection", True,
    ))
    config = replace(strict_settings(), policy_semantic_enabled=semantic)
    prepared = core.prepare_turn(held_state(), "Да, Татарстан.", config, submit_delivery=False)
    answer = "".join(core.stream_reply(prepared, "Да, Татарстан.", config))
    assert answer == "Понимаю." and len(calls) == 2


@pytest.mark.parametrize("changes,text,verdict", [
    ({}, "Приняла.", None),
    ({"mode": "onloader", "offer_present": True}, "Приняла.", True),
    ({"refused_fields": frozenset({"client.phone"})}, "Приняла.", True),
    ({"question_goal": "debt_explanation"}, "Приняла.", True),
    ({}, "При банкротстве действуют условия закона.", True),
])
def test_the_fast_gate_excludes_uncertainty_legal_content_and_other_tasks(changes, text, verdict):
    frame = replace(DialogueFrame(None, "collected_held", known_fields=frozenset({"client.region_class"}),
                                  plain_reply_fields=frozenset({"client.region_class"})), **changes)
    assessment = None if verdict is None else policy_voice_critic.SemanticVerdict(accept=verdict, flags=[])
    assert not policy_voice.accepts_brief_style_without_repair(text, frame, assessment)
