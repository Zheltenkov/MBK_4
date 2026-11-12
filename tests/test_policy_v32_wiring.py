"""Local opt-in and traces separate the factual auditor from conversation extraction."""
from dataclasses import replace

import pytest

import config
from dialogue_frame import DialogueFrame
import llm_agent
from policy_content import content_issues
import policy_factual_audit
import policy_grounded_voice
import prompts
from tests.test_policy_grounded_voice import install, semantic_raw, strict_settings


def test_factual_audit_has_an_independent_default_off_runtime_flag(monkeypatch):
    values = {"OPEN_ROUTER_API_KEY": "sk-or-synthetic-unused"}
    monkeypatch.setattr(config, "_read_env_value", values.get)
    settings = config.load_config()
    assert getattr(settings, "policy_factual_audit_enabled", None) is False
    values["MBK_POLICY_FACTUAL_AUDIT_ENABLED"] = "1"
    assert config.load_config().policy_factual_audit_enabled is True
    assert config.load_config().policy_semantic_enabled is False


def test_factual_transport_has_its_own_trace_name_without_an_extra_retry(monkeypatch):
    calls = []

    def generate(messages, settings, **kwargs):
        calls.append(kwargs)
        return "{}"

    monkeypatch.setattr(llm_agent, "_generate_text", generate)
    llm_agent.generate_policy_critique(
        [], config.AppConfig(openrouter_api_key="synthetic-unused"),
        generation_name="policy_factual_audit", token_limit=700,
    )
    assert calls[0]["generation_name"] == "policy_factual_audit"
    assert calls[0]["max_attempts"] == 1 and calls[0]["json_response"] is True


def test_field_reply_instruction_uses_the_users_short_human_response_preference():
    instruction = prompts.policy_regeneration_instruction(
        DialogueFrame(None, "collected_held"), ("unrequested_receipt",),
    )
    assert "Сейчас уместен короткий человеческий отклик" in instruction
    assert "Для него не нужны благодарность за заполнение и повтор названия поля" in instruction
    assert '"new_application_available": false' in instruction


@pytest.mark.parametrize("factual_verdict", ["unsupported", "unknown", None, "outage"])
def test_a_code_clean_draft_without_factual_support_is_repaired_and_cannot_be_preserved(
    monkeypatch, factual_verdict,
):
    bad = "Реструктуризация прекращает долг."
    good = "При реструктуризации меняют условия потребительского кредита. Сам долг остаётся."
    assert content_issues(bad, mode="debt", question_goal="debt_explanation") == ()
    generations, _ = install(monkeypatch, [bad, good], [semantic_raw(), semantic_raw()])
    opinions = iter([factual_verdict, "supported"])
    audited = []

    def audit(payload, draft, frame, settings, **kwargs):
        audited.append(draft)
        verdict = next(opinions)
        if verdict == "outage":
            raise TimeoutError("Synthetic factual adapter outage")
        return policy_factual_audit.FactualAssessment(
            verdict, "synthetic-extractor", verdict or "invalid_schema", True,
        )

    monkeypatch.setattr(policy_factual_audit, "assess", audit)
    outcome = policy_grounded_voice.render(
        {"latest_user_message": "Чем реструктуризация отличается от списания?"},
        replace(strict_settings(), policy_factual_audit_enabled=True),
        frame=DialogueFrame(None, "collected_held", question_goal="debt_explanation"),
        fallback="Проверенное резервное пояснение.",
    )
    assert outcome.spoken.text == good and len(generations) == 2
    assert audited == [bad, good]
    expected = None if factual_verdict == "outage" else factual_verdict
    assert [item.verdict for item in outcome.spoken.factual_assessments] == [expected, "supported"]
    assert all(item.verdict.accept for item in outcome.spoken.critic_assessments)


def test_only_a_previously_factually_supported_reply_can_survive_an_optional_style_failure(monkeypatch):
    good = "При реструктуризации меняют условия потребительского кредита. Сам долг остаётся."
    install(monkeypatch, [good, good], [semantic_raw(style_flags=("wrong_current_turn",)), semantic_raw()])
    opinions = iter(["supported", "unknown"])

    def audit(*args, **kwargs):
        verdict = next(opinions)
        return policy_factual_audit.FactualAssessment(verdict, "synthetic-extractor", verdict, True)

    monkeypatch.setattr(policy_factual_audit, "assess", audit)
    outcome = policy_grounded_voice.render(
        {"latest_user_message": "Поясните реструктуризацию."},
        replace(strict_settings(), policy_factual_audit_enabled=True),
        frame=DialogueFrame(None, "collected_held", question_goal="debt_explanation"),
        fallback="Проверенное резервное пояснение.",
    )
    assert outcome.spoken.text == good and outcome.spoken.reason == "model_semantic_first_preserved"
    assert [item.verdict for item in outcome.spoken.factual_assessments] == ["supported", "unknown"]
