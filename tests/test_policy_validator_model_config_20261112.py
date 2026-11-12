"""The combined validator may change without moving G, extraction E or factual F."""
from dataclasses import asdict, replace
import json
import os

import pytest

import config
from config import AppConfig
from dialogue_frame import DialogueFrame
import llm_agent
import policy_factual_audit as factual
import policy_semantic_analysis as semantic
import policy_grounded_voice as grounded
from tests.test_policy_factual_audit import wire


@pytest.mark.parametrize("override,expected", [(None, "extractor"), ("", "extractor"),
    ("  ", "extractor"), (" checker ", "checker")])
def test_override_is_optional_and_normalized(override, expected):
    cfg = AppConfig("synthetic-unused", model="speaker", extractor_model="extractor",
        policy_semantic_model=override)
    assert cfg.effective_policy_semantic_model == expected
    assert cfg.model == "speaker" and cfg.extractor_model == "extractor"


def test_existing_positional_configuration_is_preserved():
    cfg = AppConfig("synthetic-unused", "speaker", "extractor", 0.25)
    assert cfg.temperature == 0.25 and cfg.policy_semantic_model is None


@pytest.mark.parametrize("provider", ["polza", "openrouter"])
def test_env_selection_is_provider_scoped_and_does_not_move_other_roles(monkeypatch, provider):
    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: False)
    for key in list(os.environ):
        normalized = key.removeprefix("$env:")
        if normalized.startswith(("POLZA_", "OPENROUTER_", "OPEN_ROUTER_", "MBK_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MBK_LLM_PROVIDER", provider)
    monkeypatch.setenv("POLZA_API_KEY", "synthetic-polza-unused")
    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "sk-or-synthetic-unused")
    monkeypatch.setenv("POLZA_POLICY_SEMANTIC_MODEL", "polza-checker")
    monkeypatch.setenv("MBK_POLICY_SEMANTIC_MODEL", "generic-checker")
    cfg = config.load_config()
    assert cfg.policy_semantic_model == ("polza-checker" if provider == "polza" else "generic-checker")
    assert cfg.model == "qwen/qwen3.7-max"
    assert cfg.extractor_model == ("deepseek/deepseek-v4-pro" if provider == "polza" else None)


def test_only_combined_c_uses_override_and_f_stays_on_extractor(monkeypatch):
    cfg = AppConfig("synthetic-unused", model="speaker", extractor_model="extractor",
        policy_semantic_model="checker", max_tokens=2048)
    selected = DialogueFrame(None, "collected_held", known_fields=frozenset({"intent"}))
    seen = []
    def generate(messages, called, **kwargs):
        seen.append(called.model)
        body = json.loads(messages[-1]["content"])
        return json.dumps(wire(messages) if body.get("wire_version", "").startswith("factual-audit")
                          else semantic._empty_response())
    monkeypatch.setattr(llm_agent, "generate_policy_critique", generate)
    incoming = semantic.analyze({"latest_user_message": "Продолжим здесь."},
        "Можем обсудить здесь.", selected, cfg)
    assert incoming.assessment.model == "checker" and incoming.assessment.failure is None
    audit = factual.assess({}, "Закон предусматривает судебное и внесудебное банкротство.", selected, cfg)
    assert audit.model == "extractor" and audit.attempted
    assert seen == ["checker", "extractor"]
    assert asdict(cfg)["model"] == "speaker" and cfg.extractor_model == "extractor"


def test_override_equal_to_speaker_is_rejected_not_silently_downgraded(monkeypatch):
    monkeypatch.setattr(llm_agent, "generate_policy_critique", lambda *args, **kwargs: pytest.fail("No call"))
    cfg = AppConfig("synthetic-unused", model="speaker", extractor_model="extractor",
        policy_semantic_model="speaker")
    result = semantic.analyze({"latest_user_message": "Продолжим."}, "Можем обсудить здесь.",
        DialogueFrame(None, "collected_held"), cfg)
    assert result.assessment.reason == "model_not_distinct" and not result.assessment.attempted


def test_grounded_diagnostics_name_actual_c_model(monkeypatch):
    cfg = AppConfig("synthetic-unused", model="speaker", extractor_model="extractor",
        policy_semantic_model="checker", policy_voice_enabled=True, policy_semantic_enabled=True)
    monkeypatch.setattr(llm_agent, "generate_reply", lambda *args, **kwargs: "Можем обсудить здесь.")
    monkeypatch.setattr(llm_agent, "generate_policy_critique",
        lambda *args, **kwargs: json.dumps(semantic._empty_response()))
    result = grounded.render({"latest_user_message": "Продолжим разговор.", "facts": {}}, cfg,
        frame=DialogueFrame(None, "collected_held"), fallback="Можем обсудить здесь.")
    assert result.spoken.critic_model == "checker"
    assert all(item.model == "checker" for item in result.spoken.critic_assessments)
    assert replace(cfg, policy_semantic_model=None).effective_policy_semantic_model == "extractor"
