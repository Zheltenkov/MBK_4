"""Shared F admission/evidence contracts; transport is always synthetic."""
import json
from io import BytesIO

import pytest

from dialogue_frame import DialogueFrame
import llm_agent
import policy_factual_audit as audit
from tests.test_policy_factual_audit import frame, wire
from tests.test_policy_semantic_analysis import configuration


ADMIN = "Чтобы оформить анкету по списанию долгов, нужен ваш номер телефона."
DOCUMENTS = ("Пока можно собрать договоры по всем кредитам и займам, а также справки "
             "о доходах и задолженности — это обычно нужно для оценки вариантов.")
BOUNDARY = "Подтверждённого списка документов у меня нет, поэтому не буду его придумывать."
LEGAL = "При реструктуризации меняют условия договора."


@pytest.mark.parametrize("draft", [ADMIN,
    "Чтобы подготовить для вас анкету по списанию долгов, нужен контактный телефон.",
    "Мы оформляем анкету по списанию долгов.", BOUNDARY,
    "Я не могу назвать подтверждённый список документов.",
    "Учитываю ровно 400000 рублей.", "Телефон уже есть.",
    "Какие документы нужны?", "Хотите обсудить документы?",
    "МБК оформляет реструктуризацию?",
    "Вы пишете: «Нужен паспорт». Я не подтверждаю этот список.",
])
def test_administrative_or_epistemic_turn_is_not_a_legal_assertion(draft):
    assert not audit.requires_audit(frame(None), draft)


@pytest.mark.parametrize("draft", [ADMIN, BOUNDARY, "Не могу подтвердить, какие документы нужны."])
def test_proven_nonassertion_can_skip_f_even_for_explanation_goal(draft):
    assert not audit.requires_audit(frame(), draft)


@pytest.mark.parametrize("draft", [DOCUMENTS,
    "Для банкротства нужен паспорт и СНИЛС.",
    "Для анкеты нужен паспорт.",
    "СНИЛС не нужен.",
    "Точного списка нет, но обычно нужен паспорт.",
    "Не могу подтвердить, какие документы нужны, но обычно нужен паспорт.",
    "Подготовьте паспорт и СНИЛС, хорошо?",
    "Хотите обсудить документы, которые обязательно потребуются: паспорт и СНИЛС?",
    "Хотите обсудить документы, паспорт понадобится?",
    "Хотите обсудить документы и паспорт понадобится?",
    "Мы оформляем документы, хорошо?",
    "Паспорт, СНИЛС и кредитные договоры.",
    "Вот список: паспорт и СНИЛС.",
    "Для банкротства СНИЛС не нужен, верно?",
    "Для банкротства нужен паспорт, не так ли?",
    "Оформим анкету и вам спишут долг, согласны?",
    "Вы пишете: «Нужен паспорт», и это действительно обязательный документ.",
    "Чтобы оформить анкету по списанию долгов, банк спишет вам долг.",
    "Мы оформляем банкротство.",
    "Специалист изучит вашу ситуацию и подскажет, какие документы могут понадобиться именно в вашем случае.",
])
def test_actual_document_service_or_legal_assertion_still_requires_f(draft):
    assert audit.requires_audit(frame(None), draft)
    assert not audit.requires_audit(DialogueFrame(None, "collecting", mode="onloader"), draft)


def test_unknown_document_advice_abstains_before_wire(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("The current catalogue has no document requirements")
    monkeypatch.setattr(llm_agent, "generate_policy_critique", forbidden)
    result = audit.assess({}, DOCUMENTS, frame(), configuration())
    assert result.verdict == "unknown" and result.reason == "evidence_scope_uncovered"
    assert not result.attempted and not result.rows
    assert any(issue.kind == "document_scope" for issue in result.evidence_issues)


@pytest.mark.parametrize("relation", ["supported", "no_assertion"])
def test_definition_cannot_cover_document_requirements(monkeypatch, relation):
    # A possible document source elsewhere allows F to run; citing a definition
    # instead still cannot establish that requirement.
    source = LEGAL + " Для судебного банкротства нужен паспорт."
    monkeypatch.setattr(audit.prompts, "policy_verified_context", lambda _: {"bankruptcy_basics": source})
    monkeypatch.setattr(llm_agent, "generate_policy_critique",
                        lambda messages, config, **kwargs: json.dumps(wire(messages, relation)))
    result = audit.assess({}, "Для судебного банкротства нужен паспорт.", frame(), configuration())
    assert result.attempted and result.verdict == "unknown"
    assert result.rows[0].relation == relation
    assert result.evidence_issues


@pytest.mark.parametrize("source", ["Для судебного банкротства нужен паспорт.",
    "Может понадобиться паспорт.", "Паспорт не нужен.", "Если есть просрочка, нужен паспорт.",
    "Можно подготовить паспорт."])
def test_supported_document_source_keeps_its_exact_identity(monkeypatch, source):
    monkeypatch.setattr(audit.prompts, "policy_verified_context", lambda _: {"bankruptcy_basics": source})
    monkeypatch.setattr(llm_agent, "generate_policy_critique",
                        lambda messages, config, **kwargs: json.dumps(wire(messages)))
    result = audit.assess({}, source, frame(), configuration())
    assert result.verdict == "supported" and result.attempted and not result.evidence_issues
    assert result.source_catalog[0].text == source
    assert result.rows[0].source_ids == (result.source_catalog[0].id,)


@pytest.mark.parametrize("relation,expected", [("supported", "supported"), ("no_assertion", "unknown")])
def test_admin_prefix_does_not_create_actor_claim_or_hide_legal_claim(monkeypatch, relation, expected):
    monkeypatch.setattr(audit.prompts, "policy_verified_context", lambda _: {"bankruptcy_basics": LEGAL})
    monkeypatch.setattr(llm_agent, "generate_policy_critique",
                        lambda messages, config, **kwargs: json.dumps(wire(messages, relation)))
    draft = "Мы оформляем анкету по списанию долгов. " + LEGAL
    assert audit.requires_audit(frame(None), draft)
    result = audit.assess({}, draft, frame(), configuration())
    assert result.attempted and result.verdict == expected
    assert not any(issue.kind == "actor_scope" for issue in result.evidence_issues)


def test_admin_purpose_is_not_approval_of_typed_phone_storage_barrier(monkeypatch):
    from tests.test_debt_simple_conversation_20261111 import SAFE, run
    draft = "Чтобы сохранить ответы в анкету, оставьте номер телефона."
    result, calls, _ = run(monkeypatch, drafts=(draft, SAFE), hard_claim=True)
    assert result.spoken.text == SAFE and calls == ["G", "C", "G", "C"]


@pytest.mark.parametrize("draft,source", [
    ("Нужен паспорт.", "Паспорт не нужен."),
    ("Нужен паспорт.", "Может понадобиться паспорт."),
    ("Нужен паспорт.", "Если есть просрочка, нужен паспорт."),
    ("Нужен паспорт.", "Нужны документы."),
    ("Для внесудебного банкротства нужен паспорт.", "Для судебного банкротства нужен паспорт."),
    ("Нужен паспорт.", "Можно подготовить паспорт."),
])
def test_document_predicate_object_scope_and_qualification_are_not_borrowed(monkeypatch, draft, source):
    monkeypatch.setattr(audit.prompts, "policy_verified_context", lambda _: {"bankruptcy_basics": source})
    monkeypatch.setattr(llm_agent, "generate_policy_critique", lambda *args, **kwargs: pytest.fail("No compatible source"))
    result = audit.assess({}, draft, frame(), configuration())
    assert result.verdict == "unknown" and not result.attempted
    assert result.evidence_issues[0].kind == "document_scope"


def test_actual_admin_and_document_repair_lifecycle(monkeypatch):
    result, calls = run_lifecycle(monkeypatch, (DOCUMENTS, BOUNDARY))
    assert result.spoken.text == BOUNDARY and result.spoken.source == "model"
    assert calls == ["G", "C", "G", "C"]  # Early unknown: no wasteful F transport.
    assert result.spoken.factual_assessments[0].reason == "evidence_scope_uncovered"


def test_literal_admin_keeps_human_g1_without_f_transport(monkeypatch):
    selected = DialogueFrame("client.phone", "collecting_contacts", known_fields=frozenset({"intent"}))
    result, calls = run_lifecycle(monkeypatch, (ADMIN,), selected)
    assert result.spoken.text == ADMIN and result.spoken.source == "model"
    assert calls == ["G", "C"]


@pytest.mark.parametrize("draft", ["Специалист анализирует вашу ситуацию.",
    "Специалист изучит вашу ситуацию.", "Специалист подбирает законные способы снижения долговой нагрузки."])
def test_observed_external_service_predicates_require_matching_source(monkeypatch, draft):
    assert audit.requires_audit(frame(None), draft)
    monkeypatch.setattr(llm_agent, "generate_policy_critique", lambda *args, **kwargs: pytest.fail("No service source"))
    missing = audit.assess({}, draft, frame(), configuration())
    assert missing.verdict == "unknown" and not missing.attempted
    monkeypatch.setattr(audit.prompts, "policy_verified_context", lambda _: {"bankruptcy_basics": draft})
    monkeypatch.setattr(llm_agent, "generate_policy_critique",
                        lambda messages, config, **kwargs: json.dumps(wire(messages)))
    supported = audit.assess({}, draft, frame(), configuration())
    assert supported.verdict == "supported" and supported.attempted


@pytest.mark.parametrize("draft", ["Можем обсудить ваш вопрос здесь.",
    "Если есть вопросы по списанию долгов, спрашивайте."])
def test_ordinary_chat_continuation_is_not_service_capability(draft):
    assert not audit.requires_audit(frame(None), draft)


def run_lifecycle(monkeypatch, drafts, selected=None):
    """Consistent missing-phone profile, real G/C/F boundary, no outbound I/O."""
    from dataclasses import replace
    import policy_grounded_voice as grounded
    from tests.test_policy_semantics import wire as semantic_wire

    cfg = replace(configuration(), policy_voice_enabled=True, policy_semantic_enabled=True,
                  policy_factual_audit_enabled=True, request_timeout=40, max_tokens=2048)
    calls = []
    generated = 0
    def transport(config, request):
        nonlocal generated
        body = json.loads(request.data)
        if body["model"] == cfg.model:
            calls.append("G")
            content = drafts[min(generated, len(drafts) - 1)]
            generated += 1
        else:
            incoming = json.loads(body["messages"][-1]["content"])
            assert not incoming.get("wire_version", "").startswith("factual-audit"), "No justified F transport"
            calls.append("C")
            content = json.dumps(semantic_wire(), ensure_ascii=False)
        return BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
    monkeypatch.setattr(llm_agent, "_urlopen", transport)
    current = selected or DialogueFrame(None, "collected_held", known_fields=frozenset({"intent"}))
    result = grounded.render({"latest_user_message": "Что мне подготовить?", "facts": {}, "short_history": []},
                             cfg, frame=current, fallback="Можем обсудить здесь.")
    return result, calls
