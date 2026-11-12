"""Observed storage and discharge failures, with offline adversarial F opinions."""
import pytest

from dialogue_frame import DialogueFrame
from policy_content import BANKRUPTCY_EXPLANATION, content_issues
from tests.test_factual_admission_classes_20261111 import run_lifecycle
from tests.test_factual_evidence_binding_20261111 import supplied_opinion


LIVE_PHONE = ("Чтобы сохранить ваши ответы в анкете, оставьте, пожалуйста, номер телефона. "
              "Это нужно только для оформления, продолжать разговор можно и без него.")
LIVE_DISCHARGE = ("Списание возможно через процедуру банкротства по закону, и оно может освободить "
                  "от кредитных обязательств полностью.")
JUDICIAL_FULL = ("При судебном банкротстве при соблюдении условий закона кредитный долг "
                 "может быть списан полностью.")


@pytest.mark.parametrize("draft", [LIVE_PHONE,
    "Чтобы сохранить ваши ответы в анкету, оставьте номер телефона.",
    "Номер телефона нужен, чтобы сохранить ответы в анкете.",
    "Для сохранения анкеты нужен номер телефона.",
    "Без номера телефона не смогу сохранить ваши ответы.",
])
def test_storage_contact_dependency_is_code_owned_even_when_c_omits_it(draft):
    assert "phone_required_to_save_chat" in content_issues(draft, mode="debt", question_goal=None)


@pytest.mark.parametrize("draft", [
    "Ответы в анкете сохранятся и без телефона.",
    "Телефон нужен для связи по обращению.",
    "Чтобы оформить анкету по списанию долгов, нужен ваш номер телефона.",
    "Чтобы отправить анкету, оставьте номер телефона.",
    "Не оставляйте номер для сохранения ответов — они сохраняются и без него.",
    "Чтобы сохранить ответы, телефон не нужен, оставьте номер только для связи.",
    "Для сохранения анкеты телефон не требуется, сообщите его для связи.",
    "Номер уже сохранён. Продолжим здесь.",
    "Ваши ответы сохранены. На какой номер можно с вами связаться?",
    "Я не утверждаю, что номер нужен для сохранения анкеты.",
    "Вы написали: «Чтобы сохранить анкету, оставьте номер телефона».",
])
def test_real_contact_purpose_acknowledgment_and_denial_are_not_storage_barrier(draft):
    assert "phone_required_to_save_chat" not in content_issues(draft, mode="debt", question_goal=None)


def test_actual_null_c_renderer_replaces_storage_link_with_ordinary_phone_question(monkeypatch):
    current = DialogueFrame("client.phone", "collecting_contacts", known_fields=frozenset({"intent"}))
    good = "На какой номер можно с вами связаться?"
    result, calls = run_lifecycle(monkeypatch, (LIVE_PHONE, good), current)
    assert result.spoken.text == good and result.spoken.asked_field == "client.phone"
    assert calls == ["G", "C", "G", "C"]
    assert not result.paused and not result.applied_user_acts


@pytest.mark.parametrize("draft", [LIVE_DISCHARGE,
    "При банкротстве при соблюдении условий закона кредитный долг может быть списан полностью.",
    "При внесудебном банкротстве через МФЦ при соблюдении условий закона кредитный долг может быть списан полностью.",
    "При судебном банкротстве кредитный долг может быть списан полностью.",
    "При судебном банкротстве при соблюдении условий закона кредитный долг будет списан полностью.",
    "При судебном банкротстве при соблюдении условий закона все обязательства могут быть списаны полностью.",
])
def test_judicial_conditional_possible_source_cannot_establish_broader_discharge(monkeypatch, draft):
    result, _ = supplied_opinion(monkeypatch, draft, {"bankruptcy_basics": JUDICIAL_FULL})
    assert result.verdict == "unknown" and result.reason == "evidence_scope_uncovered"
    assert result.rows[0].relation == "supported"
    assert result.evidence_issues


@pytest.mark.parametrize("source,draft", [
    (JUDICIAL_FULL, JUDICIAL_FULL),
    ("Для судебного банкротства оцениваются условия закона. При соблюдении условий закона "
     "кредитный долг может быть списан полностью.", JUDICIAL_FULL),
    ("При банкротстве при соблюдении условий закона кредитный долг может быть списан полностью.",
     "При банкротстве при соблюдении условий закона кредитный долг может быть списан полностью."),
    ("При внесудебном банкротстве через МФЦ при соблюдении условий закона кредитный долг может быть списан полностью.",
     "При внесудебном банкротстве через МФЦ при соблюдении условий закона кредитный долг может быть списан полностью."),
])
def test_matching_source_and_connected_antecedent_preserve_qualified_discharge(monkeypatch, source, draft):
    result, calls = supplied_opinion(monkeypatch, draft, {"bankruptcy_basics": source})
    assert result.verdict == "supported" and len(calls) == 1
    assert not result.evidence_issues


def test_actual_catalogue_full_discharge_retains_judicial_antecedent_and_exact_citation(monkeypatch):
    result, _ = supplied_opinion(monkeypatch, LIVE_DISCHARGE,
                                {"bankruptcy_basics": BANKRUPTCY_EXPLANATION}, citation_ordinals=(9,))
    assert result.verdict == "unknown"
    assert result.rows[0].relation == "supported"
    fact = next(item for item in result.source_catalog if item.ordinal == 9)
    context = next(item for item in result.source_contexts if item.fact_id == fact.id)
    assert context.procedural_scopes == ("judicial_bankruptcy",) and context.inherited


@pytest.mark.parametrize("text", [
    "Банкротство не гарантирует полного списания кредитного долга.",
    "При банкротстве могут освободить от части долгов.",
    "Статья 415 ГК РФ предусматривает прощение долга кредитором при соблюдении закона.",
])
def test_negative_partial_and_creditor_forgiveness_are_not_full_discharge_overclaims(monkeypatch, text):
    result, _ = supplied_opinion(monkeypatch, text, {"bankruptcy_basics": text})
    assert result.verdict == "supported"


def test_source_context_does_not_cross_explicit_general_or_mfc_proposition(monkeypatch):
    source = "Для судебного банкротства оцениваются условия закона. " + JUDICIAL_FULL.replace("судебном", "внесудебном")
    result, _ = supplied_opinion(monkeypatch, JUDICIAL_FULL, {"bankruptcy_basics": source})
    assert result.verdict == "unknown"


def test_connected_draft_can_retain_judicial_scope_without_repeating_it(monkeypatch):
    draft = "Речь о судебном банкротстве. При соблюдении условий закона кредитный долг может быть списан полностью."
    result, _ = supplied_opinion(monkeypatch, draft, {"bankruptcy_basics": JUDICIAL_FULL})
    assert result.verdict == "supported"


def test_independent_creditor_topic_resets_judicial_antecedent(monkeypatch):
    source = ("Для судебного банкротства оцениваются условия закона. Кредитор может простить долг. "
              "При соблюдении условий закона кредитный долг может быть списан полностью.")
    result, _ = supplied_opinion(monkeypatch, JUDICIAL_FULL, {"bankruptcy_basics": source})
    assert result.verdict == "unknown"


def test_citing_general_existence_does_not_establish_full_discharge(monkeypatch):
    source = "Закон предусматривает судебное и внесудебное банкротство. " + JUDICIAL_FULL
    result, _ = supplied_opinion(monkeypatch, JUDICIAL_FULL, {"bankruptcy_basics": source}, citation_ordinals=(1,))
    assert result.verdict == "unknown" and result.rows[0].relation == "supported"


def test_discharge_cannot_drop_source_if_condition(monkeypatch):
    source = "Если соблюдены условия закона, при судебном банкротстве кредитный долг может быть списан полностью."
    draft = "При судебном банкротстве кредитный долг может быть списан полностью."
    result, _ = supplied_opinion(monkeypatch, draft, {"bankruptcy_basics": source})
    assert result.verdict == "unknown"


@pytest.mark.parametrize("draft", [
    "При судебном банкротстве при соблюдении условий закона долг может быть списан полностью.",
    "При судебном банкротстве при соблюдении условий закона можно полностью списать кредитный долг.",
])
def test_normal_debt_topic_shortening_and_possible_infinitive_remain_usable(monkeypatch, draft):
    result, _ = supplied_opinion(monkeypatch, draft, {"bankruptcy_basics": JUDICIAL_FULL})
    assert result.verdict == "supported"
