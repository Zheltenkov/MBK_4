"""Какой сценарий ведёт разговор: решение по первой реплике и возврат в анкету."""
from __future__ import annotations

import pytest

import entry_routing as er
from routing.entry_intent import EntryIntent as I


@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        (I.EXISTING, er.EXISTING),
        (I.OFF_TOPIC, er.OFF_TOPIC),
        (I.MONEY, er.QUESTIONNAIRE),
        # fail-open: непонятное ведёт в анкету, как без развилки
        (I.UNCLEAR, er.QUESTIONNAIRE),
    ],
)
def test_first_message_picks_the_scenario(intent, expected):
    assert er.next_scenario(None, intent, first_message=True) == expected


@pytest.mark.parametrize("intent", list(I))
def test_questionnaire_never_leaves_after_the_first_message(intent):
    """Иначе слово «заявка» на пятом вопросе перекинет разговор в другую ветку."""
    assert (
        er.next_scenario(er.QUESTIONNAIRE, intent, first_message=False)
        == er.QUESTIONNAIRE
    )


@pytest.mark.parametrize("branch", [er.EXISTING, er.OFF_TOPIC])
def test_money_always_returns_to_the_questionnaire(branch):
    assert er.next_scenario(branch, I.MONEY, first_message=False) == er.QUESTIONNAIRE


@pytest.mark.parametrize("intent", [I.UNCLEAR, I.OFF_TOPIC, I.EXISTING])
def test_existing_branch_is_left_only_for_money(intent):
    """Ветка держит запрет второго лида: уйти из неё в «не наш профиль» значило
    бы снять запрет с человека, у которого заявка уже есть."""
    assert er.next_scenario(er.EXISTING, intent, first_message=False) == er.EXISTING


def test_off_topic_turns_into_existing_when_asked_about_an_application():
    assert (
        er.next_scenario(er.OFF_TOPIC, I.EXISTING, first_message=False) == er.EXISTING
    )


@pytest.mark.parametrize("intent", [I.UNCLEAR, I.OFF_TOPIC])
def test_off_topic_stays_off_topic_otherwise(intent):
    assert er.next_scenario(er.OFF_TOPIC, intent, first_message=False) == er.OFF_TOPIC


def test_missing_or_broken_entry_reads_as_questionnaire():
    assert er.current_scenario({}) == er.QUESTIONNAIRE
    assert er.current_scenario({"entry": "мусор"}) == er.QUESTIONNAIRE
    assert er.current_scenario({"entry": {"scenario": "unknown"}}) == er.QUESTIONNAIRE
    assert er.questionnaire_active({}) is True


def test_both_branches_block_the_lead():
    assert er.lead_blocked({"entry": {"scenario": er.EXISTING}}) is True
    assert er.lead_blocked({"entry": {"scenario": er.OFF_TOPIC}}) is True
    assert er.lead_blocked({"entry": {"scenario": er.QUESTIONNAIRE}}) is False
    assert er.lead_blocked({}) is False


def test_model_intent_wins_over_the_dictionary():
    state: dict = {}

    er.route_turn(
        state, user_message="Здравствуйте", model_intent=I.OFF_TOPIC, first_message=True
    )

    assert state["entry"] == {
        "scenario": er.OFF_TOPIC,
        "intent": "off_topic",
        "source": "model",
    }
    assert state[er.TRANSITION_KEY]["scenario"] == er.OFF_TOPIC


def test_dictionary_decides_when_the_model_is_silent():
    state: dict = {}

    er.route_turn(
        state,
        user_message="Оставлял заявку вчера, когда позвонит менеджер?",
        model_intent=None,
        first_message=True,
    )

    assert state["entry"]["scenario"] == er.EXISTING
    assert state["entry"]["source"] == "dictionary"


def test_unclear_first_message_is_recorded_as_questionnaire():
    """Разрез по сценариям нужен и для анкеты: иначе знаменатель не собрать."""
    state: dict = {}

    er.route_turn(state, user_message="Здравствуйте", model_intent=None, first_message=True)

    assert state["entry"] == {
        "scenario": er.QUESTIONNAIRE,
        "intent": "unclear",
        "source": "dictionary",
    }
    assert state[er.TRANSITION_KEY]["scenario"] == er.QUESTIONNAIRE


def test_session_already_in_the_questionnaire_is_not_reclassified():
    """Сессия, начатая до включения флага, не разворачивается посреди анкеты."""
    state: dict = {}

    er.route_turn(
        state,
        user_message="А что с моей заявкой?",
        model_intent=I.EXISTING,
        first_message=False,
    )

    assert "entry" not in state
    assert er.TRANSITION_KEY not in state


def test_staying_in_a_branch_is_not_a_transition():
    state = {"entry": {"scenario": er.EXISTING, "intent": "existing", "source": "model"}}

    er.route_turn(state, user_message="ну и ладно", model_intent=I.UNCLEAR, first_message=False)

    assert state["entry"]["scenario"] == er.EXISTING
    assert er.TRANSITION_KEY not in state


def test_return_to_the_questionnaire_is_a_transition():
    state = {"entry": {"scenario": er.OFF_TOPIC, "intent": "off_topic", "source": "model"}}

    er.route_turn(
        state,
        user_message="а вообще мне нужны деньги",
        model_intent=I.MONEY,
        first_message=False,
    )

    assert state["entry"]["scenario"] == er.QUESTIONNAIRE
    assert state[er.TRANSITION_KEY]["scenario"] == er.QUESTIONNAIRE


def test_off_topic_contradicted_by_money_facts_becomes_unclear():
    """Модель сказала «не наш профиль», а в том же ответе сама нашла деньги —
    это сомнение, и оно ведёт в анкету."""
    assert er.reconcile_intent(I.OFF_TOPIC, money_signal=True) is I.UNCLEAR


def test_off_topic_without_money_facts_stays_off_topic():
    assert er.reconcile_intent(I.OFF_TOPIC, money_signal=False) is I.OFF_TOPIC


@pytest.mark.parametrize("intent", [I.EXISTING, I.MONEY, I.UNCLEAR])
def test_other_intents_are_not_touched(intent):
    """existing важнее money: сумма в «оставлял заявку на 500 тысяч» его не отменяет."""
    assert er.reconcile_intent(intent, money_signal=True) is intent


def test_silent_model_stays_silent():
    assert er.reconcile_intent(None, money_signal=True) is None
