"""Inspect actual HTTP generation messages for the code-owned brief reply role."""
from copy import deepcopy
from io import BytesIO
import json

import pytest

from config import AppConfig
import core
from evaluation.local_style_eval import execute_core_turn
import llm_agent
import prompts
from tests.test_policy_grounded_voice import semantic_raw
from tests.test_policy_v31_receipts import held_state, offline  # noqa: F401
from tests.test_policy_voice import debt_state
from usage_tracker import SessionUsage


pytestmark = pytest.mark.usefixtures("real_policy_voice_critic")
PRIOR_USER = "Хочу спокойно разобраться со своей ситуацией."
PRIOR_ASSISTANT = "Давайте спокойно, шаг за шагом."


def http_turn(monkeypatch, *, semantic, first_region=False, replies=("Угу.",), user=None, inherited_role=False,
              critic_flags=()):
    state = (debt_state(debts={"total": 400000}, client={"name": "Ирина", "phone": "+70000000001"})
             if first_region else held_state())
    state["chat_history"] = [{"role": "user", "content": PRIOR_USER},
                             {"role": "assistant", "content": PRIOR_ASSISTANT}]
    latest = user or ("Казань." if first_region else "Да, Татарстан.")
    config = AppConfig(openrouter_api_key="synthetic-unused", model="synthetic-speaker",
                       extractor_model="synthetic-critic", policy_voice_enabled=True,
                       policy_semantic_enabled=semantic, debt_policy_enabled=True, request_timeout=40, max_tokens=1000)
    drafts, calls = iter(replies), []
    critiques = 0
    if inherited_role:
        original_payload = core.build_runtime_payload
        monkeypatch.setattr(core, "build_runtime_payload", lambda *args, **kwargs: {
            **original_payload(*args, **kwargs), "policy_reply_role": "brief_field",
        })

    def open_request(configuration, request):
        nonlocal critiques
        body = json.loads(request.data)
        calls.append(deepcopy(body))
        if body["model"] == config.model:
            text = next(drafts)
        else:
            # A disputed first critique keeps the real repair/privacy branch
            # reachable even when accepted brief replies now stop after C1.
            flags = critic_flags if critiques == 0 else ()
            critiques += 1
            text = (semantic_raw(style_flags=flags) if semantic else
                    json.dumps({"accept": not flags, "flags": list(flags)}))
        return BytesIO(json.dumps({"choices": [{"message": {"content": text}}]}).encode())

    monkeypatch.setattr(llm_agent, "_urlopen", open_request)
    result = execute_core_turn(state, latest, config, SessionUsage("offline-v34-brief"))
    return result, [call for call in calls if call["model"] == config.model], calls


@pytest.mark.parametrize("semantic", [False, True])
@pytest.mark.parametrize("first_region", [False, True])
def test_brief_http_messages_keep_persona_voice_history_and_the_same_generation_model(monkeypatch, semantic, first_region):
    result, generations, calls = http_turn(monkeypatch, semantic=semantic, first_region=first_region)
    assert result.bubbles == ["Угу."] and len(generations) == 1 and len(calls) == (1 if semantic else 2)
    messages = generations[0]["messages"]
    system = "\n\n".join(message["content"] for message in messages if message["role"] == "system")
    assert prompts.STYLE_EXAMPLES not in system
    assert prompts.CONVERSATION_SYSTEM_PROMPT not in system
    assert prompts.CONVERSATION_VOICE in system
    assert prompts.CONVERSATION_IDENTITY in system
    assert len([message for message in messages if message["role"] == "system"]) == 1
    assert messages[1:3] == [{"role": "user", "content": PRIOR_USER},
                              {"role": "assistant", "content": PRIOR_ASSISTANT}]
    assert messages[-1]["role"] == "user" and messages[-1]["content"]
    assert generations[0]["model"] == "synthetic-speaker"
    if first_region:
        assert "Казань" not in messages[-1]["content"] and "скрыт" in messages[-1]["content"]
    assert not result.state.get("delivery") and result.state["facts"]["debts"]["total"] == 400000


@pytest.mark.parametrize("semantic", [False, True])
def test_a_style_regeneration_uses_the_same_brief_http_path(monkeypatch, semantic):
    result, generations, calls = http_turn(
        monkeypatch, semantic=semantic, replies=("Спасибо за уточнение.", "Понимаю."),
    )
    # The historical second style draft remains a fixture but is never called
    # after a hard-valid brief answer and an accepted first critique.
    assert result.bubbles == ["Спасибо за уточнение."] and len(generations) == 1 and len(calls) == 2
    for generation in generations:
        system = "\n\n".join(message["content"] for message in generation["messages"] if message["role"] == "system")
        assert prompts.STYLE_EXAMPLES not in system and prompts.CONVERSATION_VOICE in system
        assert prompts.CONVERSATION_SYSTEM_PROMPT not in system
        assert len([message for message in generation["messages"] if message["role"] == "system"]) == 1
    assert '"previous_reply"' not in json.dumps(generations, ensure_ascii=False)


@pytest.mark.parametrize("semantic", [False, True])
def test_an_actual_new_faq_keeps_the_existing_full_http_prompt_byte_for_byte(monkeypatch, semantic):
    response = "При реструктуризации меняют условия договора."
    result, generations, _ = http_turn(
        monkeypatch, semantic=semantic, user="Чем отличается реструктуризация?", replies=(response,), inherited_role=True,
    )
    assert result.bubbles == [response]
    systems = [message["content"] for message in generations[0]["messages"] if message["role"] == "system"]
    assert systems[0] == prompts.CONVERSATION_SYSTEM_PROMPT + "\n\n" + prompts.STYLE_EXAMPLES
    assert len(systems) == 3
