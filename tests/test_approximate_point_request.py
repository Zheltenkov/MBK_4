"""An exact-amount clarification cannot request an estimated point instead."""
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


RECORDED_ESTIMATE_REQUEST = (
    "Тогда назовите сумму, в которой вы уверены.\n\n"
    "Хотя бы ориентировочно, но одним числом — это важно для подбора вариантов."
)
VERIFIED_OR_UNKNOWN_REQUEST = (
    "Назовите собственную точную сумму остатка долга в рублях, если она вам известна. "
    "Если пока не знаете, можно так и ответить."
)


@pytest.mark.parametrize("reply", [
    RECORDED_ESTIMATE_REQUEST,
    "Назовите ориентировочную сумму одним числом.",
    "Тогда назовите сумму, в которой вы уверены.\n\nХотя бы примерно, но одним числом.",
])
def test_an_unknown_exact_amount_cannot_be_replaced_by_a_requested_estimate_point(reply):
    frame = DialogueFrame("debts.total", "collecting_debt_context", confirming=True)
    assert debt_amount_reply_issue(reply, frame) == "approximate_amount_request"
    assert not policy_voice.validate_reply(reply, frame.allowed_field, frame=frame)


@pytest.mark.parametrize("reply", [
    "Вы назвали ориентировочную сумму, точная пока неизвестна.",
    "Это точная сумма или приблизительная?",
    "Если остаток долга больше 300000 рублей, условия могут различаться.",
    "Формат записи — «280000». Назовите точный остаток, если знаете.",
    "Фраза «Хотя бы ориентировочно, но одним числом» не заменяет точный ответ.",
    "Назовите точный остаток. Ориентировочная оценка точным числом не становится.",
    "Ориентировочно одним числом не отвечайте. Назовите точную сумму, если знаете.",
])
def test_approximation_explanations_choices_and_quoted_examples_do_not_request_an_estimate(reply):
    frame = DialogueFrame("debts.total", "collecting_debt_context", confirming=True)
    assert debt_amount_reply_issue(reply, frame) is None


@pytest.mark.parametrize("frame", [
    DialogueFrame(None, "collected_held", known_fields=frozenset({"debts.total"}), confirming=True),
    DialogueFrame("client.phone", "collecting_contacts", confirming=True),
    DialogueFrame(None, "onloader", mode="onloader", confirming=True),
    DialogueFrame("debts.total", "collecting_debt_context", confirming=False),
])
def test_estimate_request_contract_is_only_the_allowed_unconfirmed_debt_amount(frame):
    assert debt_amount_reply_issue(RECORDED_ESTIMATE_REQUEST, frame) is None


def test_the_same_precision_class_keeps_the_users_orientational_number_unknown():
    observation = debt_policy.detect_debt_observation("Ориентировочно 280 тысяч рублей.", expected=True)
    assert observation is not None and observation.kind == "unknown"
    assert observation.amount is None


@pytest.mark.usefixtures("real_policy_voice_critic")
@pytest.mark.parametrize("flow", ["hard_repair", "advisory_revision", "hard_repair_rejected"])
def test_real_provider_paths_cannot_serve_the_recorded_estimate_request_or_promote_unknown(monkeypatch, flow):
    bodies = []
    speech_calls = 0

    def provider(config, body, *, max_attempts):
        nonlocal speech_calls
        bodies.append(deepcopy(body))
        assert max_attempts == 1
        if body.get("response_format") == {"type": "json_object"}:
            answer = ('{"accept":false,"flags":["unneeded_question"]}' if flow == "advisory_revision"
                      else '{"accept":true,"flags":[]}')
        else:
            speech_calls += 1
            if flow == "hard_repair_rejected":
                # The whole second action must be rejected before its qualifier can be trimmed.
                answer = RECORDED_ESTIMATE_REQUEST
            elif flow == "hard_repair":
                answer = RECORDED_ESTIMATE_REQUEST if speech_calls == 1 else VERIFIED_OR_UNKNOWN_REQUEST
            else:
                answer = VERIFIED_OR_UNKNOWN_REQUEST if speech_calls == 1 else RECORDED_ESTIMATE_REQUEST
        return {"choices": [{"message": {"content": answer}}], "usage": {}}

    def forbidden(*args, **kwargs):
        pytest.fail("A precision-request regression cannot call a real model or deliver an application")

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
    state["session_id"] = "synthetic-estimated-point-request"
    before = deepcopy(state["facts"])
    message = "Примерно 280 тысяч рублей."
    prepared = core.prepare_turn(state, message, config, submit_delivery=False)
    speech = "".join(core.stream_reply(prepared, message, config))
    spoken = prepared["_policy_spoken"]
    committed = core.commit_turn(prepared, message, speech)["current_state"]
    restored = json.loads(json.dumps(_persistable_state(committed), ensure_ascii=False))
    assert speech_calls == 2
    if flow == "hard_repair_rejected":
        assert spoken.source == "fallback" and spoken.reason == "hard_repair_invalid"
        assert spoken.critic_calls == 0
        frame = DialogueFrame("debts.total", "collecting_debt_context", confirming=True)
        assert debt_amount_reply_issue(speech, frame) is None
    else:
        assert speech == VERIFIED_OR_UNKNOWN_REQUEST
        assert spoken.critic_calls == 1
        assert spoken.source == "model" and not spoken.reason.endswith("_typed_question")
    assert [body["model"] for body in bodies] == (
        ["synthetic-speaker", "synthetic-speaker"] if flow == "hard_repair_rejected" else
        ["synthetic-speaker", "synthetic-speaker", "synthetic-critic"] if flow == "hard_repair" else
        ["synthetic-speaker", "synthetic-critic", "synthetic-speaker"]
    )
    assert restored["facts"] == before
    assert debt_policy.parse_policy(restored).amount_observation.kind == "unknown"
    assert restored["session_id"] == state["session_id"] and not restored.get("delivery")
    assert all(private not in json.dumps(bodies, ensure_ascii=False) for private in ("Ирина", "+70000000401"))
