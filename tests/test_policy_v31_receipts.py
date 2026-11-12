"""Historical receipt cases retain one checked brief reply without optional model work."""
from copy import deepcopy
from dataclasses import replace

import pytest

import core
import llm_agent
import policy_voice
import policy_voice_critic
from dialogue_frame import DialogueFrame
from tests.test_policy_grounded_voice import install, semantic_raw, strict_settings
from tests.test_policy_voice import debt_state


NATURAL_REPLY = "Да, разберёмся спокойно."


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Receipt checks cannot call providers or deliver leads")

    monkeypatch.setattr(llm_agent, "_urlopen", forbidden)
    monkeypatch.setattr(llm_agent, "extract_facts", lambda *args, **kwargs: {"fact_updates": [], "internal_summary": ""})
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *args, **kwargs: deepcopy(facts))
    monkeypatch.setattr(core.delivery, "submit", forbidden)
    monkeypatch.setattr(core, "_record_transcript", lambda *args, **kwargs: None)


def held_state():
    return debt_state(debts={"total": 400000}, client={
        "region_class": "rf_other", "name": "Ирина", "phone": "+70000000001",
    })


def actual_turn(monkeypatch, user, replies, *, semantic=True, state=None):
    state = held_state() if state is None else state
    calls, analyses = install(monkeypatch, replies, [semantic_raw()] * len(replies))
    config = replace(strict_settings(), policy_semantic_enabled=semantic)
    prepared = core.prepare_turn(state, user, config, submit_delivery=False)
    answer = "".join(core.stream_reply(prepared, user, config))
    committed = core.commit_turn(prepared, user, answer)["current_state"]
    return answer, prepared["_policy_spoken"], committed, calls, analyses


@pytest.mark.parametrize("semantic", [False, True])
@pytest.mark.parametrize("user,receipt", [
    ("Поняла. Я в Казани.", "Казань приняла."),
    ("Да, Татарстан.", "Хорошо, регион ясен."),
])
def test_the_actual_receipt_can_be_rephrased_freely_in_one_soft_repair(monkeypatch, semantic, user, receipt):
    before = held_state()
    answer, spoken, state, calls, _ = actual_turn(
        monkeypatch, user, [receipt, NATURAL_REPLY], semantic=semantic, state=before,
    )
    expected = {"Казань приняла.": "Приняла.", "Хорошо, регион ясен.": "Хорошо."}[receipt]
    code_checked = semantic and expected == "Хорошо."
    assert answer == expected and len(calls) == 1 and spoken.critic_calls == (0 if code_checked else 1)
    assert spoken.source == "model" and spoken.asked_field is None
    expected_facts = deepcopy(before["facts"])
    if "Казани" in user:
        expected_facts["client"]["region"] = "Казань"
    assert state["facts"] == expected_facts and not state.get("delivery")
    if code_checked:
        assert spoken.reason == "model_code_checked_brief"
        assert spoken.diagnostic_assessments == spoken.critic_assessments == ()
    else:
        first, = spoken.diagnostic_assessments
        assert first.code_style_issues == ()
        assert first.code_content_issues == () and first.effective_verdict.accept
        assert spoken.critic_assessments[0].verdict.accept and spoken.critic_assessments[0].verdict.flags == []


@pytest.mark.parametrize("semantic", [False, True])
@pytest.mark.parametrize("repair", ["Хорошо, регион ясен.", "На какой номер вам позвонить?"])
def test_a_second_clerical_or_invalid_draft_preserves_the_first_safe_model_reply(monkeypatch, semantic, repair):
    first = "Казань приняла."
    answer, spoken, _, calls, _ = actual_turn(
        monkeypatch, "Поняла. Я в Казани.", [first, repair], semantic=semantic,
    )
    # The optional second fixture, including the invalid phone question, is
    # never generated. Hard first-draft repairs have separate unchanged tests.
    assert answer == "Приняла." and len(calls) == spoken.critic_calls == 1 and spoken.source == "model"
    assert spoken.asked_field is None and spoken.diagnostic_assessments[0].code_style_issues == ()


@pytest.mark.parametrize("semantic", [False, True])
def test_an_outage_during_the_optional_repair_preserves_the_first_safe_model_reply(monkeypatch, semantic):
    calls, _ = install(monkeypatch, [], [semantic_raw()])

    def generate(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            return "Казань приняла."
        raise TimeoutError("synthetic optional repair outage")

    monkeypatch.setattr(llm_agent, "generate_reply", generate)
    config = replace(strict_settings(), policy_semantic_enabled=semantic)
    state = core.prepare_turn(held_state(), "Поняла. Я в Казани.", config, submit_delivery=False)
    answer = "".join(core.stream_reply(state, "Поняла. Я в Казани.", config))
    # No optional request is opened, so its simulated outage is never reached.
    assert answer == "Приняла." and len(calls) == 1
    assert state["_policy_spoken"].source == "model" and state["_policy_spoken"].critic_calls == 1


def test_a_legacy_critic_style_suggestion_cannot_replace_a_free_reply_with_a_clerical_receipt(monkeypatch):
    verdicts = iter([
        policy_voice_critic.SemanticVerdict(accept=False, flags=["wrong_current_turn"]),
        policy_voice_critic.SemanticVerdict(accept=True, flags=[]),
    ])
    monkeypatch.setattr(policy_voice_critic, "assess", lambda *args, **kwargs: policy_voice_critic.CriticAssessment(
        next(verdicts), "synthetic-critic", "accepted", True,
    ))
    answer, spoken, _, calls, _ = actual_turn(
        monkeypatch, "Да, Татарстан.", [NATURAL_REPLY, "Хорошо, регион ясен."], semantic=False,
    )
    # A disputed C1 still spends the repair. C2 sees a normalized free
    # acknowledgment rather than the raw region receipt.
    assert answer == "Хорошо." and len(calls) == spoken.critic_calls == 2
    assert spoken.critic_assessments[0].verdict.accept is False
    assert spoken.critic_assessments[1].verdict.accept is True
    assert not any(item.code_style_issues for item in spoken.diagnostic_assessments)


@pytest.mark.parametrize("semantic", [False, True])
def test_the_first_region_value_keeps_its_code_proof_after_model_payload_masking(monkeypatch, semantic):
    state = debt_state(debts={"total": 400000}, client={"name": "Ирина", "phone": "+70000000001"})
    answer, spoken, committed, calls, analyses = actual_turn(
        monkeypatch, "Казань.", ["Регион записала.", NATURAL_REPLY], semantic=semantic, state=state,
    )
    assert answer == "Записала." and len(calls) == spoken.critic_calls == 1
    assert "Казань" not in calls[0]["latest_user_message"] and "скрыт" in calls[0]["latest_user_message"]
    assert committed["facts"]["client"]["region_class"] == "rf_other"
    assert committed["facts"]["client"]["phone"] == state["facts"]["client"]["phone"]
    # A recording verb still has its honest style diagnosis. It cannot spend
    # another pair after the exact visible body passed C1.
    assert spoken.diagnostic_assessments[0].code_style_issues == ("unrequested_receipt",)
    if semantic:
        assert analyses[0][1].plain_reply_fields == frozenset({"client.region_class"})


@pytest.mark.parametrize("proven,expected", [
    (frozenset({"client.region_class"}), ("unrequested_receipt",)),
    (frozenset({"client.name"}), ()),
    (frozenset(), ()),
])
def test_a_masked_value_requires_the_matching_field_proof(proven, expected):
    frame = DialogueFrame(None, "collected_held", known_fields=frozenset({"client.region_class"}),
                          plain_reply_fields=proven)
    assert policy_voice.field_receipt_style_issues("Регион записала.", frame, "[город или регион скрыт].") == expected


@pytest.mark.parametrize("user,candidate", [
    ("Мне тревожно.", "Понимаю, давайте спокойно."),
    ("Вы точно поняли, что я в Казани?", "Да, регион ясен."),
])
def test_emotion_and_geographic_questions_do_not_create_a_plain_field_proof(monkeypatch, user, candidate):
    answer, _, _, _, analyses = actual_turn(monkeypatch, user, [candidate] * 2)
    assert answer == candidate and analyses[0][1].plain_reply_fields == frozenset()


@pytest.mark.parametrize("semantic", [False, True])
@pytest.mark.parametrize("user,candidate", [
    ("Да, Татарстан.", NATURAL_REPLY),
    ("Мне страшно.", "Понимаю, давайте спокойно."),
    ("Вы правильно поняли регион?", "Да, регион ясен."),
    ("Чем отличается реструктуризация?", "При реструктуризации меняют условия договора."),
])
def test_free_reactions_support_requested_confirmation_and_faq_keep_their_model_wording(monkeypatch, semantic, user, candidate):
    answer, spoken, _, calls, _ = actual_turn(monkeypatch, user, [candidate] * 2, semantic=semantic)
    assert answer == candidate and len(calls) == 1 and spoken.source == "model"
    assert not any(item.code_style_issues for item in spoken.diagnostic_assessments)


@pytest.mark.parametrize("changes", [
    {"mode": "onloader"}, {"status": "incomplete_held"}, {"offer_present": True},
    {"expected": "client.phone"}, {"confirming": True}, {"closing": True},
    {"question_goal": "debt_explanation"}, {"conversation_declined": True},
    {"refused_fields": frozenset({"client.phone"})},
])
def test_receipt_style_advice_does_not_expand_to_other_tasks_or_refusal(changes):
    frame = replace(DialogueFrame(None, "collected_held", known_fields=frozenset({"client.region_class"})), **changes)
    assert policy_voice.field_receipt_style_issues("Казань приняла.", frame, "Поняла. Я в Казани.") == ()
