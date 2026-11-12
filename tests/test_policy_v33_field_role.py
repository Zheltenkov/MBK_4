"""The actual generation goal for plain region replies stays short and unambiguous."""
import json
from dataclasses import replace

import pytest

import llm_agent
import core
from policy_content import BANKRUPTCY_EXPLANATION
from tests.test_policy_v31_receipts import actual_turn, held_state, offline  # noqa: F401
from tests.test_policy_voice import debt_state
from tests.test_policy_grounded_voice import install, semantic_raw, strict_settings


pytestmark = pytest.mark.usefixtures("real_policy_voice_critic")


@pytest.fixture(autouse=True)
def legacy_diagnostics(monkeypatch):
    calls = []

    def critique(messages, config, **kwargs):
        calls.append(messages)
        return '{"accept":true,"flags":[]}'

    monkeypatch.setattr(llm_agent, "generate_policy_critique", critique)
    return calls


@pytest.mark.parametrize("semantic", [False, True])
@pytest.mark.parametrize("first_region,user", [
    (True, "Казань."),
    (False, "Да, Татарстан."),
])
def test_the_sent_generation_instruction_has_one_brief_field_role_in_both_renderers(
    monkeypatch, legacy_diagnostics, semantic, first_region, user,
):
    state = (debt_state(debts={"total": 400000}, client={"name": "Ирина", "phone": "+70000000001"})
             if first_region else held_state())
    natural = "Понимаю."
    answer, spoken, committed, calls, analyses = actual_turn(
        monkeypatch, user, [natural] * 2, semantic=semantic, state=state,
    )
    instruction = calls[0]["turn_instruction"]
    assert "Текущая цель — содержательно продолжить обсуждение" not in instruction
    assert BANKRUPTCY_EXPLANATION not in instruction
    assert '"new_application_available": false' in instruction
    assert '"offer_link_available": false' in instruction
    assert answer == natural and len(calls) == 1
    assert spoken.critic_calls == (0 if semantic else 1)
    assert spoken.asked_field is None and not committed.get("delivery")
    assert committed["facts"]["client"]["phone"] == state["facts"]["client"]["phone"]
    if semantic:
        # The certified whole ACK has no C result to inspect or fabricate.
        assert analyses == [] and spoken.reason == "model_code_checked_brief"
        assert calls[0]["policy_reply_role"] == "brief_field"
    else:
        task = json.loads(legacy_diagnostics[0][-1]["content"])["task"]
        assert task["primary_goal"] == "respond_to_field_update"


@pytest.mark.parametrize("user,candidate", [
    ("Чем реструктуризация отличается от списания?", "При реструктуризации меняют условия договора."),
    ("Мне страшно.", "Понимаю, давайте спокойно."),
    ("Вы правильно поняли, что я в Казани?", "Да, ваш регион понятен."),
    ("Нет, я в Самаре.", "Понимаю."),
])
def test_questions_emotion_confirmation_and_corrections_keep_their_response_role(monkeypatch, user, candidate):
    answer, _, committed, calls, analyses = actual_turn(monkeypatch, user, [candidate] * 2)
    assert answer == candidate and len(calls) == 1
    assert not getattr(analyses[0][1], "brief_field_reply", False)
    assert not committed.get("delivery")


@pytest.mark.parametrize("semantic", [False, True])
def test_the_optional_style_repair_keeps_the_same_brief_role_in_the_actual_sent_payload(monkeypatch, semantic):
    # Accepted C1 now ends the brief path. An uncleanable passive property and
    # an explicitly disputed C1 preserve this test's actual G2 role assertion.
    first = "Регион ясен."
    calls, _ = install(monkeypatch, [first, "Понимаю."], [
        semantic_raw(style_flags=("unrequested_receipt",)), semantic_raw(),
    ])
    verdicts = iter(['{"accept":false,"flags":["unrequested_receipt"]}', '{"accept":true,"flags":[]}'])
    monkeypatch.setattr(llm_agent, "generate_policy_critique", lambda *args, **kwargs: next(verdicts))
    config = replace(strict_settings(), policy_semantic_enabled=semantic)
    prepared = core.prepare_turn(held_state(), "Да, Татарстан.", config, submit_delivery=False)
    answer = "".join(core.stream_reply(prepared, "Да, Татарстан.", config))
    spoken = prepared["_policy_spoken"]
    assert answer == "Понимаю." and len(calls) == spoken.critic_calls == 2
    for call in calls:
        assert "Текущая цель — содержательно продолжить обсуждение" not in call["turn_instruction"]
        assert BANKRUPTCY_EXPLANATION not in call["turn_instruction"]
