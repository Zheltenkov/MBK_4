"""Independent normal-entry checks for a separate, bounded factual task."""
from dataclasses import replace
import json
import socket

import pytest

import core
from config import AppConfig
from dialogue_frame import DialogueFrame
import llm_agent
from policy_content import content_issues
import policy_factual_audit as factual
from tests.test_policy_debt_continuation_independent import held_state
from tests import test_policy_v22_independent as entry
from tests.test_policy_voice import debt_state


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Independent V32 tests cannot use the network or send leads")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(core.delivery, "submit", forbidden)


def factual_turn(monkeypatch, state, *, replies, verdicts, styles=None,
                 latest="Чем реструктуризация отличается от банкротства?"):
    """Keep the normal G/C HTTP boundary and independently control the F adapter."""
    original_config = entry.AppConfig
    monkeypatch.setattr(entry, "AppConfig", lambda **kwargs: replace(
        original_config(**kwargs), policy_factual_audit_enabled=True,
    ))
    remaining = iter(verdicts)
    calls = []

    def audit(payload, draft, frame, config, **kwargs):
        assert config.policy_factual_audit_enabled
        verdict = next(remaining)
        calls.append((draft, verdict))
        reason = verdict if verdict is not None else "model_unavailable"
        return factual.FactualAssessment(verdict, config.extractor_model, reason, True)

    monkeypatch.setattr(factual, "assess", audit)
    if styles is not None:
        original_envelope = entry.envelope
        raw_styles = iter(styles)

        def raw_critic():
            value = original_envelope()
            value["style"] = next(raw_styles)
            return value

        monkeypatch.setattr(entry, "envelope", raw_critic)
    result = entry.actual_turn(monkeypatch, state, latest, replies=replies)
    assert len(calls) <= 2
    assert len(result[3]) + len(calls) <= 6
    return result, calls


@pytest.mark.parametrize("formal", ["Хорошо, Казань.", "Спасибо за уточнение."])
def test_the_actual_formal_confirmations_can_be_repaired_into_a_free_short_nod(monkeypatch, formal):
    state = held_state()
    answer, prepared, committed, calls = entry.actual_turn(
        monkeypatch, state, "Да, Татарстан.", replies=[formal, "Угу."],
    )
    expected = "Хорошо." if formal == "Хорошо, Казань." else formal
    assert answer == expected and prepared["_policy_spoken"].source == "model"
    assert len(calls) == 2 and prepared["_policy_spoken"].critic_calls == 1
    assert committed["facts"] == state["facts"] and not committed.get("delivery")


def test_a_short_nonclerical_nod_is_allowed_without_a_new_question_or_forced_lecture(monkeypatch):
    state = held_state()
    answer, _, committed, calls = entry.actual_turn(monkeypatch, state, "Да, Татарстан.", replies=["Угу."])
    assert answer == "Угу." and len(calls) == 2
    assert committed["facts"] == state["facts"] and not committed.get("delivery")


def test_factual_unsupported_cannot_become_the_first_safe_reply_even_when_code_and_c_are_clean(monkeypatch):
    state = held_state()
    bad = "Банкротство разрешено каждому, у кого долг 400 тысяч рублей."
    assert content_issues(bad, mode="debt", question_goal="debt_explanation") == ()
    (answer, _, committed, _), audits = factual_turn(
        monkeypatch, state, replies=[bad] * 2, verdicts=["unsupported", "unknown"],
    )
    assert answer != bad and audits
    assert committed["facts"] == state["facts"] and not committed.get("delivery")


@pytest.mark.parametrize("verdict", ["unknown", None])
def test_a_factual_abstention_or_outage_does_not_silently_approve_a_new_factual_reply(monkeypatch, verdict):
    state = held_state()
    candidate = "Реструктуризация может изменить срок договора."
    (answer, _, committed, _), audits = factual_turn(
        monkeypatch, state, replies=[candidate] * 2, verdicts=[verdict] * 2,
    )
    assert answer != candidate and audits
    assert committed["facts"] == state["facts"] and not committed.get("delivery")


def test_a_previously_f_supported_reply_survives_a_later_failed_optional_style_repair(monkeypatch):
    state = held_state()
    first = "Реструктуризация может изменить срок договора."
    revised = "При реструктуризации можно изменить график платежей."
    (answer, prepared, committed, _), audits = factual_turn(
        monkeypatch, state, replies=[first, revised], verdicts=["supported", "unknown"],
        styles=[{"accept": False, "flags": ["unrequested_receipt"]}, {"accept": True, "flags": []}],
    )
    assert answer == first and prepared["_policy_spoken"].source == "model"
    assert [verdict for _, verdict in audits] == ["supported", "unknown"]
    assert committed["facts"] == state["facts"] and not committed.get("delivery")


def test_factual_unavailability_cannot_erase_the_clients_explicit_phone_refusal(monkeypatch):
    state = debt_state(debts={"total": 400000}, client={"region_class": "rf_other", "name": "Тест"})
    candidate = "Реструктуризация может изменить срок договора."
    (answer, _, committed, _), audits = factual_turn(
        monkeypatch, state, replies=[candidate] * 2, verdicts=[None] * 2,
        latest="Телефон не оставлю. Чем реструктуризация отличается от банкротства?",
    )
    assert answer and audits
    assert committed["questionnaire_policy"]["phone_collection"] == "declined"
    assert "phone" not in committed["facts"]["client"]
    assert committed["facts"]["debts"]["total"] == 400000 and not committed.get("delivery")


def test_missing_factual_rows_do_not_pass_and_private_history_never_leaves_the_f_adapter(monkeypatch):
    calls = []

    def empty_rows(messages, config, **kwargs):
        calls.append(messages)
        return json.dumps({"rows": []})

    monkeypatch.setattr(llm_agent, "generate_policy_critique", empty_rows)
    result = factual.assess(
        {"latest_user_message": "PRIVATE_REQUEST", "short_history": [{"role": "user", "content": "PRIVATE_HISTORY"}],
         "facts": {"client": {"phone": "+70000000401", "name": "PRIVATE_NAME"}}},
        "Доступность банкротства зависит от условий закона.",
        DialogueFrame(None, "collected_held", question_goal="debt_explanation"),
        AppConfig(openrouter_api_key="unused", model="synthetic-speaker", extractor_model="synthetic-analysis"),
    )
    assert result.verdict is None and result.reason == "invalid_schema" and len(calls) == 1
    wire = json.loads(calls[0][-1]["content"])
    assert set(wire) == {"wire_version", "draft_units", "source_catalog"}
    assert wire["wire_version"] == factual.WIRE_VERSION
    assert "PRIVATE_" not in json.dumps(calls) and "+70000000401" not in json.dumps(calls)


def test_more_than_four_factual_paragraphs_abstains_without_truncation_or_a_model_call(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Overflowing F input must not spend a model call")

    monkeypatch.setattr(llm_agent, "generate_policy_critique", forbidden)
    result = factual.assess(
        {}, "\n\n".join(["Реструктуризация зависит от условий договора."] * 5),
        DialogueFrame(None, "collected_held", question_goal="debt_explanation"),
        AppConfig(openrouter_api_key="unused", model="synthetic-speaker", extractor_model="synthetic-analysis"),
    )
    assert result.verdict is None and result.reason == "input_limit" and not result.attempted


def test_all_no_assertion_rows_are_an_abstention_instead_of_a_supported_answer(monkeypatch):
    def nonassertions(messages, config, **kwargs):
        wire = json.loads(messages[-1]["content"])
        return json.dumps({"rows": [{"unit_id": unit["id"], "relation": "no_assertion", "source_ids": [],
                                     "mismatch": "none"}
                                    for unit in wire["draft_units"]]})

    monkeypatch.setattr(llm_agent, "generate_policy_critique", nonassertions)
    result = factual.assess(
        {}, "Банкротство зависит от условий закона.",
        DialogueFrame(None, "collected_held", question_goal="debt_explanation"),
        AppConfig(openrouter_api_key="unused", model="synthetic-speaker", extractor_model="synthetic-analysis"),
    )
    assert result.verdict == "unknown" and result.attempted
