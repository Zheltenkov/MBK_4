"""A completed plain field update cannot open an unplanned intake topic."""
from dataclasses import replace

import pytest

from dialogue_frame import DialogueFrame, request_fields
import policy_voice
from tests.test_policy_v31_receipts import actual_turn, offline  # noqa: F401


def brief_frame():
    return DialogueFrame(None, "collected_held", known_fields=frozenset({"client.region_class"}),
                         plain_reply_fields=frozenset({"client.region_class"}))


@pytest.mark.parametrize("draft", [
    "Хорошо, Казань. А на кого оформлен долг — на вас лично или есть поручители?",
    "Кто является поручителем?",
    "Уточните, на кого оформлены обязательства.",
    "Можете сообщить, кто является поручителем.",
    "Хотите обсудить что-то ещё?",
])
def test_every_new_request_in_a_code_proven_brief_only_turn_is_rejected(draft):
    assert not policy_voice.validate_reply(draft, None, frame=brief_frame())
    assert "unplanned_collection" in request_fields(draft, brief_frame())


@pytest.mark.parametrize("draft", [
    "Понимаю вас.", "Да, ясно.", "Угу.", "Рада, что удалось всё уточнить.",
    "Ничего больше сообщать не нужно.",
])
def test_free_short_reactions_remain_available(draft):
    assert policy_voice.validate_reply(draft, None, frame=brief_frame())


@pytest.mark.parametrize("quote", ['«Есть поручители?»', '“Есть поручители?”', '"Есть поручители?"'])
def test_the_role_boundary_does_not_reclassify_ordinary_discussion_or_quoted_questions(quote):
    ordinary = replace(brief_frame(), plain_reply_fields=frozenset())
    assert "unplanned_collection" not in request_fields("Хотите обсудить что-то ещё?", ordinary)
    assert not request_fields(f'Вы раньше спросили {quote}. Поняла вас.', brief_frame())


def test_an_untrusted_model_hint_cannot_reopen_questions_after_a_proven_plain_update():
    hinted = replace(brief_frame(), question_goal="debt_explanation", question_goal_origin="model_hint")
    assert hinted.brief_field_reply
    assert not policy_voice.validate_reply("Есть ли поручители?", None, frame=hinted)
    code_question = replace(hinted, question_goal_origin="code")
    assert not code_question.brief_field_reply


@pytest.mark.parametrize("semantic", [False, True])
def test_the_actual_turn_repairs_the_new_question_without_reasking_or_changing_facts(monkeypatch, semantic):
    bad = "Хорошо, Казань. А на кого оформлен долг — на вас лично или есть поручители?"
    answer, spoken, committed, calls, _ = actual_turn(
        monkeypatch, "Поняла. Я в Казани.", [bad, "Понимаю вас."], semantic=semantic,
    )
    assert answer == "Понимаю вас." and len(calls) == 2
    assert spoken.asked_field is None and not committed.get("delivery")
    assert committed["facts"]["debts"]["total"] == 400000
    assert committed["facts"]["client"]["phone"] == "+70000000001"
