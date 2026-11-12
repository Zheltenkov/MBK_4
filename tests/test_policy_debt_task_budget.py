"""A proved debt act uses its task frame and never retries a failed validator."""
from dataclasses import replace

import pytest

import llm_agent
import policy_grounded_voice
import policy_semantic_analysis
import policy_semantics
import policy_voice_critic
import prompts
from tests.test_policy_grounded_voice import install, semantic_raw, strict_settings
from tests.test_policy_proven_debt_repair import debt_frame


@pytest.mark.parametrize("latest", [
    "Разобраться с долгами",
    "Пока хочу обсудить всё здесь. Никуда передавать мои данные не нужно.",
])
def test_proved_debt_act_keeps_persona_and_examples_without_credit_intake_task(monkeypatch, latest):
    generations, _ = install(monkeypatch, ["Можем поговорить здесь."], [semantic_raw()])
    policy_grounded_voice.render({"latest_user_message": latest}, strict_settings(),
                                frame=debt_frame(), fallback="Можем поговорить здесь.")
    messages = llm_agent._conversation_messages(generations[0])
    system = "\n".join(item["content"] for item in messages if item["role"] == "system")
    assert prompts.CONVERSATION_SYSTEM_PROMPT not in system
    assert prompts.CONVERSATION_IDENTITY in system and prompts.STYLE_EXAMPLES in system
    assert prompts.CONVERSATION_VOICE in system
    assert prompts.CHAT_ORIGIN_RULE in system and prompts.SAFETY_ADVICE_RULE in system
    assert generations[0]["turn_instruction"] in system
    assert len([item for item in messages if item["role"] == "system"]) == 1
    assert messages[-1] == {"role": "user", "content": latest}


@pytest.mark.parametrize("latest", [
    "Разобраться с долгами. У меня долг 500000 рублей.",
    "Что такое банкротство?",
    "Если я скажу не передавайте мои данные?",
])
def test_mixed_or_unproved_input_keeps_original_conversation_prompt(monkeypatch, latest):
    generations, _ = install(monkeypatch, ["Можем поговорить здесь."], [semantic_raw()])
    policy_grounded_voice.render({"latest_user_message": latest, "policy_reply_role": "debt_act"},
                                strict_settings(), frame=debt_frame(), fallback="Можем поговорить здесь.")
    messages = llm_agent._conversation_messages(generations[0])
    assert messages[0]["content"] == prompts.CONVERSATION_SYSTEM_PROMPT + "\n\n" + prompts.STYLE_EXAMPLES


@pytest.mark.parametrize("kind", ["deadline", "timeout", "http", "connection"])
def test_provider_failure_cannot_be_repaired_by_a_second_generation(monkeypatch, kind):
    generations, analyses = install(monkeypatch, ["Можем поговорить здесь."] * 2, [semantic_raw()] * 2)

    def unavailable(payload, draft, frame, config, **kwargs):
        analyses.append((draft, frame))
        return policy_semantic_analysis.SemanticAnalysis(
            assessment=policy_voice_critic.CriticAssessment(
                None, "synthetic-extractor", "model_unavailable", True,
                failure=llm_agent.ModelFailure(kind, "read", "SyntheticFailure"),
            ),
            semantics=policy_semantics.parse_semantics("unavailable", latest_user_message="Привет", draft=draft),
            authority=policy_semantics.TurnAuthority.from_frame(frame, "Привет", draft),
        )

    monkeypatch.setattr(policy_semantic_analysis, "analyze", unavailable)
    result = policy_grounded_voice.render({"latest_user_message": "Привет"}, strict_settings(),
                                          frame=debt_frame(), fallback="Можем поговорить здесь.")
    assert len(generations) == len(analyses) == result.spoken.critic_calls == 1
    assert result.spoken.source == "fallback"
    assert result.spoken.reason == "semantic_provider_unavailable"
    assert result.paused


def test_real_outgoing_rejection_still_gets_bounded_repair(monkeypatch):
    # An actual unsupported phone condition still needs repair; a style flag alone no longer does.
    bad = "Для сохранения разговора необходим номер."
    generations, analyses = install(monkeypatch, [bad, "Можем обсудить это."],
                                    [semantic_raw(claims=[{"predicate": "phone_required_to_save_chat", "value": True,
                                        "evidence": bad, "modality": "asserted", "scope": "chat"}]), semantic_raw()])
    result = policy_grounded_voice.render({"latest_user_message": "Привет"}, strict_settings(),
                                          frame=debt_frame(), fallback="Можем поговорить здесь.")
    assert len(generations) == len(analyses) == result.spoken.critic_calls == 2
    assert result.spoken.source == "model"
    assert result.spoken.text == "Можем обсудить это."


def test_provider_failure_preserves_code_owned_refusal(monkeypatch):
    generations, analyses = install(monkeypatch, ["Можем поговорить здесь."] * 2, [semantic_raw()] * 2)

    def unavailable(payload, draft, frame, config, **kwargs):
        analyses.append((draft, frame))
        return policy_semantic_analysis.SemanticAnalysis(
            assessment=policy_voice_critic.CriticAssessment(None, "synthetic-extractor", "model_unavailable", True,
                failure=llm_agent.ModelFailure("deadline", "read", "DeadlineExceeded")),
            semantics=policy_semantics.parse_semantics("unavailable", latest_user_message=payload["latest_user_message"], draft=draft),
            authority=policy_semantics.TurnAuthority.from_frame(
                frame, payload["latest_user_message"], draft),
        )

    monkeypatch.setattr(policy_semantic_analysis, "analyze", unavailable)
    result = policy_grounded_voice.render({"latest_user_message": "Мои данные никуда не передавайте."},
        strict_settings(), frame=replace(debt_frame(), conversation_declined=True), fallback="Можем поговорить здесь.")
    assert len(generations) == len(analyses) == 1
    assert "decline_transfer" in result.applied_user_acts and not result.paused
    assert result.effective_frame.conversation_declined and result.spoken.source == "fallback"


@pytest.mark.parametrize("confirmed", [True, False])
def test_generation_receives_exactness_only_after_the_frame_confirms_it(confirmed):
    frame = debt_frame() if confirmed else replace(debt_frame(), confirming=True)
    instruction = prompts.policy_turn_instruction(frame)
    assert ("Общая сумма долга уже подтверждена точным числом" in instruction) is confirmed
