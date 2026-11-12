"""The speaker cannot turn uncertainty into precision by asking to copy its number."""
from copy import deepcopy
import json

import pytest

import core
import debt_policy
import llm_agent
import policy_voice
from config import AppConfig
from dialogue_frame import DialogueFrame, debt_amount_reply_issue
from session_store import _persistable_state
from tests.test_policy_voice import debt_state


RECORDED_COERCION = (
    "Понимаю, что точной цифры под рукой нет, но для работы мне нужно одно конкретное число.\n\n"
    "Напишите 280 000, если это ближе всего к реальности."
)


def test_the_recorded_conditional_copy_instruction_is_not_a_request_for_verified_amount():
    frame = DialogueFrame("debts.total", "collecting_debt_context", confirming=True)
    assert debt_amount_reply_issue(RECORDED_COERCION, frame) == "suggested_amount_copy"
    assert not policy_voice.validate_reply(RECORDED_COERCION, frame.allowed_field, frame=frame)


@pytest.mark.parametrize("text", [
    "Если остаток долга больше 300 000 рублей, условия могут различаться.",
    "Укажите точную сумму в формате «280000».",
    "Пример записи — «280000». Назовите свой точный остаток.",
    "Напишите сумму, если долг больше 280000.",
    "Если сумма 280000, это всё равно не гарантирует результат.",
])
def test_conditional_thresholds_and_quoted_format_examples_do_not_copy_an_unknown_value(text):
    frame = DialogueFrame("debts.total", "collecting_debt_context", confirming=True)
    assert debt_amount_reply_issue(text, frame) is None


def test_a_qualified_past_amount_report_is_not_an_instruction_to_copy_it():
    frame = DialogueFrame("debts.total", "collecting_debt_context", confirming=True)
    text = "Вы сообщили 280000 как примерную сумму."
    assert debt_amount_reply_issue(text, frame) is None
    assert policy_voice.validate_reply(text, frame.allowed_field, frame=frame)


@pytest.mark.parametrize("frame", [
    DialogueFrame(None, "collected_held", known_fields=frozenset({"debts.total"}), confirming=True),
    DialogueFrame("client.phone", "collecting_contacts", confirming=True),
    DialogueFrame(None, "onloader", mode="onloader", confirming=True),
    DialogueFrame("debts.total", "collecting_debt_context", confirming=False),
])
def test_copy_diagnosis_is_scoped_to_the_allowed_unconfirmed_debt_amount(frame):
    assert debt_amount_reply_issue(RECORDED_COERCION, frame) is None


@pytest.mark.usefixtures("real_policy_voice_critic")
@pytest.mark.parametrize("safe_repair", [True, False])
def test_actual_provider_repair_cannot_coerce_the_value_in_either_speaker_slot(monkeypatch, safe_repair):
    final = ("Назовите, пожалуйста, точную сумму остатка долга в рублях, если она вам известна. "
             "Если не знаете, так и скажите.")
    bodies = []
    speaker_calls = 0

    def provider(config, body, *, max_attempts):
        nonlocal speaker_calls
        bodies.append(deepcopy(body))
        assert max_attempts == 1
        if body.get("response_format") == {"type": "json_object"}:
            answer = '{"accept":true,"flags":[]}'
        else:
            speaker_calls += 1
            if speaker_calls == 1:
                answer = RECORDED_COERCION
            else:
                answer = final if safe_repair else "Напишите 280 000, если это ближе всего к реальности."
        return {"choices": [{"message": {"content": answer}}], "usage": {}}

    def forbidden(*args, **kwargs):
        pytest.fail("A precision regression cannot use a real model or submit an application")

    monkeypatch.setattr(llm_agent, "_post", provider)
    monkeypatch.setattr(llm_agent, "_urlopen", forbidden)
    monkeypatch.setattr(llm_agent, "extract_facts", lambda *args, **kwargs: {"fact_updates": [], "internal_summary": ""})
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *args, **kwargs: deepcopy(facts))
    monkeypatch.setattr(core, "_record_transcript", lambda *args, **kwargs: None)
    monkeypatch.setattr(core.delivery, "submit", forbidden)
    config = AppConfig(openrouter_api_key="synthetic-unused", model="synthetic-speaker",
                       extractor_model="synthetic-critic", debt_policy_enabled=True,
                       policy_voice_enabled=True, fact_interpretation_mode="legacy")
    state = debt_state(client={"name": "Ирина", "phone": "+70000000401", "region_class": "rf_other"},
                       debts={"total": None})
    state["session_id"] = "synthetic-amount-coercion"
    before = deepcopy(state["facts"])
    message = "Примерно 280 тысяч рублей."
    prepared = core.prepare_turn(state, message, config, submit_delivery=False)
    speech = "".join(core.stream_reply(prepared, message, config))
    spoken = prepared["_policy_spoken"]
    committed = core.commit_turn(prepared, message, speech)["current_state"]
    restored = json.loads(json.dumps(_persistable_state(committed), ensure_ascii=False))
    assert speaker_calls == 2
    assert RECORDED_COERCION not in speech
    assert spoken.source == ("model" if safe_repair else "fallback")
    if safe_repair:
        assert speech == final
    else:
        assert "Напишите 280 000" not in speech
    assert [body["model"] for body in bodies] == (
        ["synthetic-speaker", "synthetic-speaker", "synthetic-critic"] if safe_repair else
        ["synthetic-speaker", "synthetic-speaker"]
    )
    assert debt_policy.parse_policy(restored).amount_observation.kind == "unknown"
    assert restored["facts"] == before
    assert restored["session_id"] == state["session_id"] and not restored.get("delivery")
    # Both speaker contexts use the same uncertainty rule, with a typed repair diagnosis.
    speaker_bodies = [body for body in bodies if body["model"] == "synthetic-speaker"]
    assert all("собственную достоверно известную сумму" in json.dumps(body, ensure_ascii=False)
               for body in speaker_bodies)
    assert "suggested_amount_copy" in json.dumps(speaker_bodies[1], ensure_ascii=False)
    assert all("Ирина" not in json.dumps(body, ensure_ascii=False) and "+70000000401" not in json.dumps(body)
               for body in bodies)
