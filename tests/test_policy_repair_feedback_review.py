"""Independent provider composition proof for concrete deterministic repair feedback."""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import json

import pytest

import core
import policy_voice
from config import AppConfig
from dialogue_frame import DialogueFrame
from policy_content import BANKRUPTCY_EXPLANATION
import prompts
from tests.test_policy_voice import debt_state


pytestmark = pytest.mark.usefixtures("real_policy_voice_critic")


@pytest.mark.parametrize("factual_error", ["", "Банкротство означает полное освобождение от всех долгов. "])
def test_a_real_repair_gets_the_rejected_field_without_private_facts_or_more_calls(monkeypatch, factual_error):
    state = debt_state(client={"name": "Ирина Синтетическая Проверочная", "phone": "+70000000401"},
                       debts={"total": 400000})
    rejected = factual_error + "Есть ли сейчас активные просрочки по кредитам или займам?"
    final = "Если хотите, можем обсудить ваши вопросы здесь."
    bodies = []
    speakers = 0

    def open_request(configuration, request):
        nonlocal speakers
        body = json.loads(request.data)
        bodies.append(body)
        if configuration.model == configuration.extractor_model:
            content = json.dumps({"accept": True, "flags": []})
        else:
            speakers += 1
            content = rejected if speakers == 1 else final
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    monkeypatch.setattr(core.llm_agent, "_urlopen", open_request)
    monkeypatch.setattr(core.llm_agent, "extract_facts", lambda *args, **kwargs: {
        "fact_updates": [], "internal_summary": "",
    })
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *args, **kwargs: deepcopy(facts))
    monkeypatch.setattr(core, "_record_transcript", lambda *args, **kwargs: None)

    def no_delivery(*args, **kwargs):
        pytest.fail("Repair feedback cannot submit an application")

    monkeypatch.setattr(core.delivery, "submit", no_delivery)
    config = AppConfig(openrouter_api_key="synthetic-unused", extractor_model="synthetic-critic",
                       policy_voice_enabled=True, debt_policy_enabled=True)
    prepared = core.prepare_turn(state, "Казань.", config, submit_delivery=False)
    before = deepcopy(prepared["facts"])
    speech = "".join(core.stream_reply(prepared, "Казань.", config))
    committed = core.commit_turn(prepared, "Казань.", speech)["current_state"]

    assert speech == final
    assert [body["model"] for body in bodies] == [config.model, config.model, config.extractor_model]
    second_system_messages = [item["content"] for item in bodies[1]["messages"] if item["role"] == "system"]
    repair_instruction = second_system_messages[-1]
    assert '["debts.overdue_duration"]' in repair_instruction
    assert "Убери эти запросы целиком" in repair_instruction
    assert "не переформулируй их" in repair_instruction
    if factual_error:
        # Regeneration still receives the factual diagnosis without its bad source assertion.
        assert "unconditional_discharge" in repair_instruction
        assert BANKRUPTCY_EXPLANATION in repair_instruction
    # The rejected collection wording must not anchor the second speaker call.
    assert rejected not in repair_instruction
    original_dialogue = [item for item in bodies[0]["messages"] if item["role"] != "system"]
    repaired_dialogue = [item for item in bodies[1]["messages"] if item["role"] != "system"]
    assert repaired_dialogue == original_dialogue
    assert bodies[1]["messages"][0] == bodies[0]["messages"][0]
    assert json.loads(bodies[-1]["messages"][-1]["content"])["draft"] == speech
    assert all(private not in json.dumps(bodies, ensure_ascii=False)
               for private in ("Ирина", "Синтетическая", "Проверочная", "+70000000401"))
    assert committed["facts"] == before
    assert "overdue_duration" not in committed["facts"]["debts"]
    assert not committed.get("delivery")


def test_pressure_on_the_allowed_phone_field_retains_the_masked_editing_source():
    frame = DialogueFrame("client.phone", "collecting_contact", require_question=True)
    draft = "Чтобы продолжить разговор, оставьте номер телефона."
    revision = policy_voice._revision_payload(
        {"facts": {}, "latest_user_message": "Объясните, зачем он нужен."}, draft, frame, None, None,
    )
    instruction = revision["turn_instruction"]
    assert '"client.phone"' in instruction
    assert "Черновик для редактирования" in instruction
    assert json.dumps(draft, ensure_ascii=False) in instruction
    assert revision["latest_user_message"] == "Объясните, зачем он нужен."


def test_a_legitimate_current_question_gets_no_invented_removed_field_feedback():
    frame = DialogueFrame("client.phone", "collecting_contact",
                          known_fields=frozenset({"client.name", "client.region_class", "debts.total"}))
    instruction = prompts.policy_revision_instruction(
        frame, "Оставите номер для оформления анкеты? Обсудить вопросы можно и без него.",
        needs_repair=False, missing_question=False,
    )
    assert "Проверка черновика отклонила сбор сведений" not in instruction
    assert "debts.overdue_duration" not in instruction
