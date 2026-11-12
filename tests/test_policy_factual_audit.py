"""Independent factual-audit contracts use synthetic transport, not accuracy scores."""
import importlib
import importlib.util
import json

import pytest

from dialogue_frame import DialogueFrame
import llm_agent
from tests.test_policy_semantic_analysis import configuration


def audit_module():
    assert importlib.util.find_spec("policy_factual_audit") is not None
    return importlib.import_module("policy_factual_audit")


def frame(goal="debt_explanation"):
    return DialogueFrame(None, "collected_held", question_goal=goal)


def wire(messages, relation="supported"):
    request = json.loads(messages[-1]["content"])
    source_id = request["source_catalog"][0]["id"]
    return {"rows": [{"unit_id": unit["id"], "relation": relation,
                      "source_ids": [source_id] if relation in ("supported", "unsupported") else [],
                      "mismatch": {"supported": "none", "unsupported": "object_scope",
                                   "unknown": "insufficient_source", "no_assertion": "none"}[relation]}
                     for unit in request["draft_units"]]}


@pytest.mark.parametrize("relation,expected", [("supported", "supported"), ("unsupported", "unsupported"),
                                                ("unknown", "unknown"), ("no_assertion", "unknown")])
def test_raw_factual_relation_is_retained_with_complete_unit_and_source_binding(monkeypatch, relation, expected):
    module = audit_module()
    calls = []
    def generate(messages, config, **kwargs):
        calls.append((messages, config, kwargs))
        return json.dumps(wire(messages, relation), ensure_ascii=False)
    monkeypatch.setattr(llm_agent, "generate_policy_critique", generate)
    result = module.assess({"facts": {}, "latest_user_message": "private-user-question",
                            "short_history": [{"role": "user", "content": "private-history"}]},
                           "Первый пункт.\n\nВторой пункт.", frame(), configuration())
    assert result.verdict == expected and result.attempted and len(result.rows) == 2
    assert len(calls) == 1 and calls[0][2]["generation_name"] == "policy_factual_audit"
    body = json.loads(calls[0][0][-1]["content"])
    assert set(body) == {"wire_version", "draft_units", "source_catalog"}
    assert body["wire_version"] == module.WIRE_VERSION
    assert result.wire_version == module.WIRE_VERSION
    assert result.source_catalog and all(fact.source_id in {"bankruptcy_basics", "consumer_credit_restructuring"} for fact in result.source_catalog)
    assert "private-" not in json.dumps(calls[0][0], ensure_ascii=False)
    assert [unit["text"] for unit in body["draft_units"]] == ["Первый пункт.", "Второй пункт."]


@pytest.mark.parametrize("mutation", ["empty", "missing", "duplicate", "foreign_unit", "foreign_source",
                                       "duplicate_source", "old_free_quote", "extra", "wrong_relation"])
def test_malformed_or_unbound_audit_abstains_without_repair_or_semantic_approval(monkeypatch, mutation):
    module = audit_module()
    calls = []
    def generate(messages, config, **kwargs):
        calls.append(messages)
        value = wire(messages)
        if mutation == "empty":
            value["rows"] = []
        elif mutation == "missing":
            value["rows"].pop()
        elif mutation == "duplicate":
            value["rows"][1] = dict(value["rows"][0])
        elif mutation == "foreign_unit":
            value["rows"][0]["unit_id"] = "not-provided"
        elif mutation == "foreign_source":
            value["rows"][0]["source_ids"] = ["not-provided"]
        elif mutation == "duplicate_source":
            value["rows"][0]["source_ids"] *= 2
        elif mutation == "old_free_quote":
            value["rows"][0]["source_evidence"] = "PRIVATE_MODEL_QUOTE"
        elif mutation == "extra":
            value["rows"][0]["new_authority"] = True
        else:
            value["rows"][0]["relation"] = "probably_supported"
        return json.dumps(value, ensure_ascii=False)
    monkeypatch.setattr(llm_agent, "generate_policy_critique", generate)
    result = module.assess({}, "Первый пункт.\n\nВторой пункт.", frame(), configuration())
    assert result.verdict is None and result.reason == "invalid_schema" and len(calls) == 1
    assert result.rows == ()
    assert result.schema_issues
    serialized = json.dumps([issue.__dict__ for issue in result.schema_issues])
    assert "not-provided" not in serialized and "PRIVATE_MODEL_QUOTE" not in serialized


@pytest.mark.parametrize("draft", ["", "x" * 3001, "\n\n".join(str(i) for i in range(5))])
def test_input_limits_abstain_before_transport_without_truncating_the_claims(monkeypatch, draft):
    module = audit_module()
    def forbidden(*args, **kwargs):
        pytest.fail("Overflow/empty input must not open transport")
    monkeypatch.setattr(llm_agent, "generate_policy_critique", forbidden)
    result = module.assess({}, draft, frame(), configuration())
    assert result.verdict is None and not result.attempted and result.reason == "input_limit"


@pytest.mark.parametrize("goal,draft,required", [(None, "Спасибо за уточнение.", False),
                                                 (None, "При банкротстве действуют условия закона.", True),
                                                 (None, "Кредитный долг могут списать полностью.", True),
                                                 (None, "Ваш долг спишут полностью.", True),
                                                 (None, "От долгов могут освободить по правилам закона.", True),
                                                 (None, "Список вопросов можно обсудить здесь.", False),
                                                 ("debt_explanation", "Поясню разницу.", True)])
def test_eligibility_uses_the_code_goal_or_spontaneous_legal_content_only(goal, draft, required):
    assert audit_module().requires_audit(frame(goal), draft) is required


def test_an_outage_is_a_single_attempted_abstention(monkeypatch):
    module = audit_module()
    def unavailable(*args, **kwargs):
        raise TimeoutError("synthetic")
    monkeypatch.setattr(llm_agent, "generate_policy_critique", unavailable)
    result = module.assess({}, "Возможна реструктуризация.", frame(), configuration())
    assert result.verdict is None and result.reason == "model_unavailable" and result.attempted


def test_one_unknown_unit_cannot_be_hidden_by_a_supported_unit(monkeypatch):
    module = audit_module()
    def generate(messages, config, **kwargs):
        value = wire(messages)
        unknown = wire(messages, "unknown")["rows"][1]
        value["rows"][1] = unknown
        return json.dumps(value, ensure_ascii=False)
    monkeypatch.setattr(llm_agent, "generate_policy_critique", generate)
    result = module.assess({}, "Первый пункт.\n\nВторой пункт.", frame(), configuration())
    assert result.verdict == "unknown" and result.reason == "unknown"


def test_duplicate_json_fields_do_not_become_a_supported_audit(monkeypatch):
    module = audit_module()
    def generate(messages, config, **kwargs):
        raw = json.dumps(wire(messages), ensure_ascii=False)
        return raw[:-1] + ', "rows": []}'
    monkeypatch.setattr(llm_agent, "generate_policy_critique", generate)
    result = module.assess({}, "Первый пункт.", frame(), configuration())
    assert result.verdict is None and result.reason == "invalid_schema"


def test_local_redaction_does_not_turn_history_or_client_facts_into_factual_input(monkeypatch):
    module = audit_module()
    sent = []
    def generate(messages, config, **kwargs):
        sent.extend(messages)
        return json.dumps(wire(messages), ensure_ascii=False)
    monkeypatch.setattr(llm_agent, "generate_policy_critique", generate)
    payload = {"facts": {"client": {"name": "СекретноеИмя", "phone": "+70000000001"}},
               "short_history": [{"role": "user", "content": "secret-history"}]}
    result = module.assess(payload, "СекретноеИмя, поясню условия.", frame(), configuration(),
                           draft_redactor=lambda text: text.replace("СекретноеИмя", "[имя скрыто]"))
    assert result.verdict == "supported"
    serialized = json.dumps(sent, ensure_ascii=False)
    assert "СекретноеИмя" not in serialized and "70000000001" not in serialized
    assert "secret-history" not in serialized and '"facts"' not in serialized


def test_sentence_catalog_keeps_all_curated_text_group_order_and_deterministic_ids():
    module = audit_module()
    public = module.prompts.policy_verified_context(frame())
    catalog = module._source_catalog(public)
    assert catalog == module._source_catalog(public)
    assert len({fact.id for fact in catalog}) == len(catalog)
    for source_id in ("bankruptcy_basics", "consumer_credit_restructuring"):
        facts = [fact for fact in catalog if fact.source_id == source_id]
        assert [fact.ordinal for fact in facts] == list(range(1, len(facts) + 1))
        assert "".join("".join(fact.text.split()) for fact in facts) == "".join(public[source_id].split())


@pytest.mark.parametrize("mutation,code,field", [
    ("foreign_source", "unknown_source_id", "source_ids"),
    ("duplicate_source", "duplicate_source_id", "source_ids"),
    ("missing", "unit_coverage", "unit_id"),
    ("old_free_quote", "extra_forbidden", "unknown"),
])
def test_safe_schema_diagnostics_identify_binding_classes_without_private_values(monkeypatch, mutation, code, field):
    module = audit_module()
    def generate(messages, config, **kwargs):
        value = wire(messages)
        if mutation == "foreign_source":
            value["rows"][0]["source_ids"] = ["PRIVATE_SOURCE_ID"]
        elif mutation == "duplicate_source":
            value["rows"][0]["source_ids"] *= 2
        elif mutation == "missing":
            value["rows"].pop()
        else:
            value["rows"][0]["source_evidence"] = "PRIVATE_QUOTE"
        return json.dumps(value, ensure_ascii=False)
    monkeypatch.setattr(llm_agent, "generate_policy_critique", generate)
    result = module.assess({}, "Первый пункт.\n\nВторой пункт.", frame(), configuration())
    assert result.verdict is None and result.reason == "invalid_schema"
    assert any(issue.code == code and issue.field == field for issue in result.schema_issues)
    assert all(1 <= issue.count <= 16 for issue in result.schema_issues)
    assert "PRIVATE_" not in repr(result.schema_issues)
    assert result.source_catalog and result.wire_version == module.WIRE_VERSION


def test_the_factual_task_explicitly_selects_none_reasoning(monkeypatch):
    module = audit_module()
    calls = []
    def generate(messages, config, **kwargs):
        calls.append((config, kwargs))
        return json.dumps(wire(messages), ensure_ascii=False)
    monkeypatch.setattr(llm_agent, "generate_policy_critique", generate)
    result = module.assess({}, "Проверяемое пояснение.", frame(), configuration())
    assert result.verdict == "supported"
    assert calls[0][1].get("reasoning_effort") == "none"
    assert calls[0][1]["generation_name"] == "policy_factual_audit"


def test_factual_reasoning_and_json_share_a_bounded_2048_token_ten_second_phase(monkeypatch):
    module = audit_module()
    calls = []
    def generate(messages, config, **kwargs):
        calls.append((config, kwargs))
        return json.dumps(wire(messages), ensure_ascii=False)
    monkeypatch.setattr(llm_agent, "generate_policy_critique", generate)
    module.assess({}, "Проверяемое пояснение.", frame(), configuration(max_tokens=4000, request_timeout=30))
    settings, kwargs = calls[0]
    assert settings.max_tokens == kwargs["token_limit"] == 2048
    assert settings.request_timeout == 10
    assert module.MAX_REPLY_TOKENS == 2048 and module.MAX_PHASE_SECONDS == 10


def test_combined_conversational_analysis_keeps_its_existing_reasoning_and_output_contract(monkeypatch):
    import policy_semantic_analysis as combined
    from tests.test_policy_semantic_analysis import stub
    calls = stub(monkeypatch, combined._empty_response())
    combined.analyze({"latest_user_message": "Можно общаться здесь?"}, "Можно.", frame(None), configuration())
    assert calls[0][2].get("reasoning_effort") is None
    assert calls[0][2]["token_limit"] == 1000 and calls[0][1].request_timeout <= 10
