"""A whole already-known debt region needs no independent fact-extraction call."""
from copy import deepcopy
from dataclasses import replace

import pytest

import core
import debt_policy
import policy_grounded_voice
from interpretation import FactCandidateExtraction
from tests.test_policy_grounded_voice import install, semantic_raw, strict_settings
from tests.test_policy_v31_receipts import held_state


@pytest.fixture
def extracted(monkeypatch):
    calls = []

    def extract(*args, **kwargs):
        calls.append(True)
        return core.ExtractorRunResult({"fact_updates": [], "internal_summary": ""},
            FactCandidateExtraction(), None, True)

    monkeypatch.setattr(core, "_run_extractors", extract)
    monkeypatch.setattr(core.geo, "enrich_locations", lambda facts, *a, **k: deepcopy(facts))
    monkeypatch.setattr(core.delivery, "submit", lambda *a, **k: pytest.fail("No business sends"))
    return calls


@pytest.mark.parametrize("mode", ["legacy", "shadow", "semantic"])
@pytest.mark.parametrize("message", ["Да, Татарстан.", "Казань.", "Татарстан", "Казань, Татарстан"])
def test_neutral_known_region_preserves_facts_without_extraction(extracted, message, mode):
    initial = held_state()
    initial["facts"]["client"]["region"] = "Казань"
    before = deepcopy(initial)
    config = replace(strict_settings(), fact_interpretation_mode=mode)

    prepared = core.prepare_turn(initial, message, config, submit_delivery=False)

    assert extracted == []
    assert prepared["facts"] == before["facts"]
    assert prepared["chat_history"] == before["chat_history"]
    assert initial == before
    assert prepared["plan"]["fields"] == []
    assert not prepared.get("delivery")


@pytest.mark.parametrize("message", [
    "Да, Татарстан?", '"Да, Татарстан."', "«Казань»", "Наверное, Татарстан.",
    "Да, Татарстан. Долг 500000 рублей.", "Казань, но долг 500000.",
    "Мой брат в Казани.", "Казань. Мне страшно.", "Незнакомая область.",
    "Вы правильно поняли, что я в Казани?", "Нет, я в Самаре.", "Москва.",
])
def test_unproven_or_changed_context_keeps_extraction(extracted, message):
    initial = held_state()
    initial["facts"]["client"]["region"] = "Казань"

    core.prepare_turn(initial, message, strict_settings(), submit_delivery=False)

    assert extracted == [True]


def test_missing_region_does_not_become_known_from_a_fastpath_guess(extracted):
    initial = held_state()
    initial["facts"]["client"].pop("region_class")
    initial["script"]["asked"] = []

    core.prepare_turn(initial, "Да, Татарстан.", strict_settings(), submit_delivery=False)

    assert extracted == [True]


def test_normal_entry_carries_the_plain_role_across_privacy_masking(monkeypatch, extracted):
    calls, _ = install(monkeypatch, ["Понимаю."], [semantic_raw()])
    original = policy_grounded_voice.render
    frames = []

    def capture(*args, **kwargs):
        frames.append(kwargs["frame"])
        return original(*args, **kwargs)

    monkeypatch.setattr(policy_grounded_voice, "render", capture)
    initial = held_state()
    initial["facts"]["client"]["region"] = "Казань"
    config = strict_settings()
    prepared = core.prepare_turn(initial, "Да, Татарстан.", config, submit_delivery=False)

    text = "".join(core.stream_reply(prepared, "Да, Татарстан.", config))
    committed = core.commit_turn(prepared, "Да, Татарстан.", text)["current_state"]

    assert extracted == []
    assert text == "Понимаю." and len(calls) == 1
    assert frames[0].plain_reply_fields == frozenset({"client.region_class"})
    assert frames[0].brief_field_reply
    # This frame is built at core's raw-input boundary, before generate_reply
    # applies its own outbound privacy processing.
    assert committed["facts"] == initial["facts"]
    assert committed["chat_history"][-2:] == [
        {"role": "user", "content": "Да, Татарстан."},
        {"role": "assistant", "content": "Понимаю."},
    ]


def test_a_region_waiting_for_confirmation_still_uses_extraction(extracted):
    initial = held_state()
    initial["fact_statuses"] = {"client.region_class": "needs_confirmation"}

    core.prepare_turn(initial, "Да, Татарстан.", strict_settings(), submit_delivery=False)

    assert extracted == [True]


def test_explicit_local_residence_keeps_the_existing_correction_resolver(extracted):
    initial = held_state()
    initial["facts"]["client"]["region"] = "Казань"

    prepared = core.prepare_turn(initial, "Я в Самаре.", strict_settings(), submit_delivery=False)

    assert extracted == []
    assert prepared["facts"]["client"]["region"] == "Самара"
    assert prepared["facts"]["client"]["region_class"] == "rf_other"
    assert "client.region_class" in prepared["_policy_received_fields"]
    assert prepared["facts"]["debts"]["total"] == 400000
    assert initial["facts"]["client"]["region"] == "Казань"


def test_known_business_class_does_not_invent_a_missing_raw_place(extracted):
    initial = held_state()
    assert "region" not in initial["facts"]["client"]

    prepared = core.prepare_turn(initial, "Да, Татарстан.", strict_settings(), submit_delivery=False)

    assert extracted == []
    assert prepared["facts"] == initial["facts"]
    assert "region" not in prepared["facts"]["client"]


def test_an_explicit_changed_class_still_uses_existing_extraction_and_correction(extracted):
    initial = held_state()

    prepared = core.prepare_turn(initial, "Я в Москве.", strict_settings(), submit_delivery=False)

    assert extracted == [True]
    assert prepared["facts"]["client"]["region_class"] == "moscow_mo"
    assert prepared["facts"]["client"]["region"] == "Москва"


def test_ordinary_questionnaire_retains_its_extraction_policy(extracted):
    initial = held_state()
    initial.pop(debt_policy.POLICY_KEY)

    core.prepare_turn(initial, "Да, Татарстан.",
        replace(strict_settings(), debt_policy_enabled=False), submit_delivery=False)

    assert extracted == [True]
