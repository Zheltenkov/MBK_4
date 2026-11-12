"""A safe brief style repair edits its masked predecessor, never a rejected action request."""
from dataclasses import replace
import json

import pytest

from dialogue_frame import DialogueFrame
import policy_voice
import policy_grounded_voice
from tests.test_policy_grounded_voice import install, semantic_raw, strict_settings
from tests.test_policy_v31_receipts import offline  # noqa: F401
from tests.test_policy_v34_brief_render import http_turn


@pytest.mark.usefixtures("real_policy_voice_critic")
@pytest.mark.parametrize("semantic", [False, True])
def test_the_actual_soft_style_http_repair_receives_the_masked_first_reply_to_edit(monkeypatch, semantic):
    first = "Казань известна."
    result, generations, calls = http_turn(
        monkeypatch, semantic=semantic, first_region=True, replies=(first, "Понимаю."),
        critic_flags=("unrequested_receipt",),
    )
    assert result.bubbles == ["Понимаю."] and len(generations) == 2 and len(calls) == 4
    g1_systems = [m["content"] for m in generations[0]["messages"] if m["role"] == "system"]
    g2_systems = [m["content"] for m in generations[1]["messages"] if m["role"] == "system"]
    assert len(g1_systems) == 1 and len(g2_systems) == 2
    assert '"previous_reply"' not in g1_systems[0]
    # A passive property stays unnormalized. Its disputed C1 therefore retains
    # the actual G2 quoted-edit privacy contract rather than bypassing that test.
    assert "Казань" not in generations[0]["messages"][-1]["content"]
    assert "скрыт" in generations[0]["messages"][-1]["content"]
    edit = g2_systems[1]
    previous = json.loads(edit[edit.index('{"previous_reply"'):])["previous_reply"]
    assert "известна" in previous and "скрыт" in previous and "Казань" not in previous
    assert first not in edit


@pytest.mark.usefixtures("real_policy_voice_critic")
@pytest.mark.parametrize("semantic", [False, True])
def test_a_hard_rejected_phone_question_keeps_the_old_repair_without_quoting_the_bad_request(monkeypatch, semantic):
    bad = "На какой номер вам позвонить?"
    result, generations, _ = http_turn(
        monkeypatch, semantic=semantic, first_region=True, replies=(bad, "Понимаю."),
    )
    assert result.bubbles == ["Понимаю."] and len(generations) == 2
    systems = [m["content"] for m in generations[1]["messages"] if m["role"] == "system"]
    assert len(systems) == 2
    assert bad not in systems[1] and '"previous_reply"' not in systems[1]


@pytest.mark.parametrize("changes,draft,flags", [
    ({"question_goal": "debt_explanation"}, "Хорошо, Казань.", ("unrequested_receipt",)),
    ({"refused_fields": frozenset({"client.phone"})}, "Хорошо, Казань.", ("unrequested_receipt",)),
    ({}, "Понимаю.", ("unrequested_receipt",)),
    ({}, "Хорошо, Казань. Можно разбираться спокойно.", ("unrequested_receipt",)),
    ({}, "Хорошо, Казань.", ("unrequested_receipt", "factual_audit_unavailable")),
])
def test_nonbrief_or_noncode_style_diagnoses_cannot_enter_the_quoted_style_path(changes, draft, flags):
    frame = replace(DialogueFrame(None, "collected_held", known_fields=frozenset({"client.region_class"}),
                                  plain_reply_fields=frozenset({"client.region_class"})), **changes)
    first = policy_voice.SpokenPolicyReply(draft, None, "model", "synthetic-safe")
    revision = policy_voice._revision_payload(
        {"latest_user_message": "[город или регион скрыт]."}, draft, frame, first, None, flags,
    )
    assert '"previous_reply"' not in revision["turn_instruction"]


def test_a_failed_style_source_redaction_preserves_first_safe_without_sending_the_unmasked_edit(monkeypatch):
    first = "Казань известна."
    calls, _ = install(monkeypatch, [first], [semantic_raw(style_flags=("unrequested_receipt",))])
    frame = DialogueFrame(None, "collected_held", known_fields=frozenset({"client.region_class"}),
                          plain_reply_fields=frozenset({"client.region_class"}))

    redactions = []

    def unavailable_redactor(draft):
        redactions.append(draft)
        raise ValueError("synthetic redaction failure")

    result = policy_grounded_voice.render(
        {"latest_user_message": "[город или регион скрыт]."}, strict_settings(), frame=frame,
        fallback="Понимаю.", draft_redactor=unavailable_redactor,
    )
    assert result.spoken.text == first and result.spoken.source == "model"
    assert len(calls) == result.spoken.critic_calls == 1
    assert redactions == [first]
