"""Independent boundaries for deleting region echoes before the first critique."""
from dataclasses import replace
import json

import pytest

import core
from dialogue_frame import DialogueFrame
import policy_voice
from tests.test_policy_grounded_voice import install, semantic_raw, strict_settings
from tests.test_policy_v31_receipts import held_state, offline  # noqa: F401


def brief_frame():
    return DialogueFrame(
        None, "collected_held", known_fields=frozenset({"client.region_class"}),
        plain_reply_fields=frozenset({"client.region_class"}),
    )


@pytest.mark.parametrize("draft", [
    "Приняла, Татарстан, но телефон оставлять не обязательно.",
    "Не приняла, Татарстан.",
    "Хорошо, Казань?",
    "Она сказала «Приняла, Татарстан».",
    "Приняла, Татарстан. Понимаю, как вам непросто.",
    "Приняла, Татарстан. Заявка уже отправлена.",
    "Приняла, Татарстан. Долги точно спишут.",
    "Записала.",
])
def test_unknown_clauses_quotes_polarity_and_bare_recording_are_not_deleted(draft):
    assert policy_voice.normalize_brief_field_reply(draft, brief_frame(), "Да, Татарстан.") == draft


@pytest.mark.parametrize("latest", [
    "Нет, номер не оставлю.", "Казань правильно записали?", "Я в Казани, мне тревожно.",
])
def test_known_region_without_whole_input_proof_cannot_rewrite_the_reply(latest):
    frame = replace(brief_frame(), plain_reply_fields=frozenset())
    draft = "Приняла, Татарстан."
    assert policy_voice.normalize_brief_field_reply(draft, frame, latest) == draft


@pytest.mark.parametrize("channel", ["user", "claims"])
def test_raw_style_acceptance_does_not_bypass_an_invalid_semantic_channel(monkeypatch, channel):
    raw = json.loads(semantic_raw())
    raw[channel] = {"unexpected": "not a valid semantic channel"}
    generations, analyses = install(
        monkeypatch, ["Приняла, Казань.", "Угу."],
        [json.dumps(raw), semantic_raw()],
    )
    config = strict_settings()
    prepared = core.prepare_turn(held_state(), "Да, Татарстан.", config, submit_delivery=False)
    answer = "".join(core.stream_reply(prepared, "Да, Татарстан.", config))
    committed = core.commit_turn(prepared, "Да, Татарстан.", answer)["current_state"]
    assert len(generations) == len(analyses) == 2
    assert analyses[0][0] == "Приняла."
    assert answer == "Угу." and prepared["_policy_spoken"].source == "model"
    assert committed["facts"]["debts"]["total"] == 400000
    assert committed["facts"]["client"]["phone"] == "+70000000001"
    assert not committed.get("delivery")
