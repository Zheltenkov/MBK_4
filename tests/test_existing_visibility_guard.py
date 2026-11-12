"""An honest application limitation remains the answer, including with a second bubble."""
from __future__ import annotations

import pytest

import core
import entry_routing
from state import init_state
from utils import _BRANCH_FALLBACK, guard_bubbles


TAIL = "Если у вас появился новый вопрос по деньгам или долгам — могу помочь разобраться прямо здесь."


@pytest.mark.parametrize("limitation", [
    "Статус вашей заявки я отсюда не вижу.",
    "Я отсюда не вижу статус вашей заявки.",
    "Статус вашей заявки отсюда не виден.",
    "Мне отсюда не виден статус вашей заявки.",
    "Статус вашей заявки мне недоступен.",
])
def test_a_whole_negative_visibility_statement_keeps_the_models_both_bubbles(limitation):
    bubbles = [limitation, TAIL]
    assert guard_bubbles(bubbles, scenario=entry_routing.EXISTING) == bubbles


def test_existing_commit_preserves_the_complete_model_answer_without_starting_intake():
    state = init_state()
    state["entry"] = {"scenario": entry_routing.EXISTING, "intent": "existing", "source": "dictionary"}
    bubbles = ["Статус вашей заявки я отсюда не вижу.", TAIL]
    committed = core.commit_turn(state, "Что с моей заявкой?", "\n\n".join(bubbles))
    assert committed["messages"] == bubbles
    assert not (committed["current_state"].get("script") or {}).get("asked")


@pytest.mark.parametrize("bubble", [
    "Статус вашей заявки я отсюда не вижу, но проверю вашу заявку.",
    "Статус вашей заявки я отсюда не вижу. Соединю вас с менеджером.",
    "Статус вашей заявки я отсюда не вижу, но удалю номер.",
    "Не вижу проблем: статус вашей заявки одобрен.",
    "Статус вашей заявки я вижу.",
    "Я не могу не видеть статус вашей заявки.",
    "Не то чтобы статус вашей заявки я не вижу.",
    "Если статус вашей заявки я не вижу, проверю вашу заявку.",
    "Если статус вашей заявки я не вижу.",
    "Статус вашей заявки я отсюда не вижу?",
    "«Статус вашей заявки я отсюда не вижу».",
    "Он сказал: статус вашей заявки я не вижу.",
    "Статус вашей заявки я не вижу, но сейчас уже вижу.",
    "Статус вашей заявки я не вижу — менеджер перезвонит сегодня.",
])
def test_a_negative_fragment_cannot_authorize_a_whole_mixed_or_nonasserted_claim(bubble):
    assert bubble not in guard_bubbles([bubble], scenario=entry_routing.EXISTING)


@pytest.mark.parametrize("claim", [
    "Проверю вашу заявку.", "Соединю вас с менеджером.", "Удалю ваш номер.",
])
def test_an_honest_bubble_does_not_exempt_another_false_promise(claim):
    honest = "Статус вашей заявки я отсюда не вижу."
    result = guard_bubbles([honest, claim], scenario=entry_routing.EXISTING)
    assert honest in result
    assert claim not in result


def test_other_guards_still_reject_a_false_guarantee_appended_to_the_limitation():
    bubble = "Статус вашей заявки я отсюда не вижу, но кредит вам точно одобрят."
    assert bubble not in guard_bubbles([bubble], scenario=entry_routing.EXISTING)


@pytest.mark.parametrize("discarded", [
    "Проверю вашу заявку.",
    "Статус вашей заявки я отсюда не вижу — сейчас проверю подробности.",
    "Статус вашей заявки мне из этого окна не виден.",
])
def test_a_cut_existing_promise_cannot_leave_only_an_unrelated_help_tail(discarded):
    result = guard_bubbles([discarded, TAIL], scenario=entry_routing.EXISTING)
    assert result == [_BRANCH_FALLBACK[entry_routing.EXISTING][0], TAIL]


def test_existing_commit_retains_a_meaningful_limit_after_a_false_promise_is_cut():
    state = init_state()
    state["entry"] = {"scenario": entry_routing.EXISTING, "intent": "existing", "source": "dictionary"}
    committed = core.commit_turn(state, "Проверите заявку?", "Проверю вашу заявку.\n\n" + TAIL)
    assert committed["messages"] == [_BRANCH_FALLBACK[entry_routing.EXISTING][0], TAIL]


@pytest.mark.parametrize("honest", [
    "Статус вашей заявки я отсюда не вижу.", _BRANCH_FALLBACK[entry_routing.EXISTING][0],
])
def test_a_cut_promise_does_not_duplicate_an_already_validated_limitation(honest):
    assert guard_bubbles([honest, "Проверю вашу заявку.", TAIL], scenario=entry_routing.EXISTING) == [honest, TAIL]


@pytest.mark.parametrize("unsafe", [
    "Удалю номер.", "Никто не позвонит.", "Вы сами оставили заявку на подбор.",
    "Сайт полностью безопасен, отключите защиту.", "Кредит вам точно одобрят.",
])
def test_other_cut_reasons_do_not_acquire_a_status_preface(unsafe):
    assert guard_bubbles([unsafe, TAIL], scenario=entry_routing.EXISTING) == [TAIL]


@pytest.mark.parametrize("scenario", [None, entry_routing.QUESTIONNAIRE, entry_routing.OFF_TOPIC])
def test_other_scenarios_keep_their_existing_partial_cut_behavior(scenario):
    assert guard_bubbles(["Проверю вашу заявку.", TAIL], scenario=scenario) == [TAIL]
