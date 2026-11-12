"""Main-entry style repairs retain human freedom and diagnostic provenance."""
import json
import socket

import pytest

import core
from dialogue_frame import DialogueFrame
import prompts
from tests.test_policy_debt_continuation_independent import held_state
from tests.test_policy_v22_independent import actual_turn
from tests.test_policy_voice import debt_state


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Independent V31 checks cannot use the network or send leads")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(core.delivery, "submit", forbidden)


@pytest.mark.parametrize("latest,receipt", [
    ("Поняла. Я в Казани.", "Казань приняла."),
    ("Да, Татарстан.", "Хорошо, регион ясен."),
])
def test_the_actual_sole_clerical_receipt_gets_one_free_model_repair(monkeypatch, latest, receipt):
    state = held_state()
    natural = "Здесь многое зависит от того, посильно ли вам сейчас платить по графику."
    answer, prepared, committed, calls = actual_turn(
        monkeypatch, state, latest, replies=[receipt, natural],
    )
    spoken = prepared["_policy_spoken"]
    assert answer == natural and spoken.source == "model"
    assert len([call for call in calls if call["model"] == "synthetic-speaker"]) == 2
    assert all(item.verdict.accept for item in spoken.critic_assessments)
    first, second = spoken.diagnostic_assessments
    assert getattr(first, "code_style_issues", ()) and first.effective_verdict.accept
    assert first.effective_verdict == spoken.critic_assessments[0].verdict
    assert first.code_content_issues == () and second.code_content_issues == ()
    assert not getattr(second, "code_style_issues", ()) and second.effective_verdict.accept
    assert committed["facts"] == state["facts"] and not committed.get("delivery")
    # Inspect the messages sent by the normal orchestrator, not just a prompt constant.
    critiques = [call for call in calls if call["model"] == "synthetic-analysis"]
    for call in critiques:
        system = call["messages"][0]["content"]
        assert "Три канала независимы" in system
        assert "user.acts —" in system and "claims.requests" in system
        assert "Проверка фактов — самостоятельная задача перед извлечением" not in system


def test_failed_style_repair_preserves_the_first_safe_model_reply_with_a_bounded_call_count(monkeypatch):
    state = held_state()
    receipt = "Хорошо, регион ясен."
    answer, prepared, committed, calls = actual_turn(
        monkeypatch, state, "Да, Татарстан.", replies=[receipt, ""],
    )
    assert answer == receipt and prepared["_policy_spoken"].source == "model"
    assert len([call for call in calls if call["model"] == "synthetic-speaker"]) == 2
    assert len(calls) <= 4
    assert committed["facts"] == state["facts"] and not committed.get("delivery")


def test_a_requested_confirmation_is_not_mislabelled_as_an_unrequested_receipt(monkeypatch):
    state = held_state()
    receipt = "Да, Казань приняла."
    answer, _, committed, calls = actual_turn(
        monkeypatch, state, "Казань записали?", replies=[receipt],
    )
    assert answer == receipt and committed["facts"] == state["facts"]
    assert len([call for call in calls if call["model"] == "synthetic-speaker"]) == 1


def test_a_brief_supportive_response_does_not_require_an_intake_question_or_legal_lecture(monkeypatch):
    state = held_state()
    natural = "Понимаю, с таким количеством долгов легко растеряться."
    answer, _, committed, calls = actual_turn(
        monkeypatch, state, "Я растерялась и переживаю.", replies=[natural],
    )
    assert answer == natural and committed["facts"] == state["facts"]
    assert len([call for call in calls if call["model"] == "synthetic-speaker"]) == 1


def test_a_receipt_beside_useful_content_keeps_the_existing_body_cleanup_and_does_not_force_regeneration(monkeypatch):
    state = held_state()
    useful = "Условия реструктуризации нужно обсуждать со своим кредитором."
    answer, _, committed, calls = actual_turn(
        monkeypatch, state, "Да, Татарстан.", replies=["Хорошо, регион ясен.\n\n" + useful],
    )
    assert answer == useful and committed["facts"] == state["facts"]
    assert len([call for call in calls if call["model"] == "synthetic-speaker"]) == 1


def test_the_field_update_goal_keeps_capability_limits_without_requiring_a_legal_body_or_new_question():
    frame = DialogueFrame(None, "collected_held", defer_discussion_followup=True)
    instruction = prompts.policy_turn_instruction(frame)
    assert '"new_application_available": false' in instruction
    assert "Перейди к короткому полезному пояснению" not in instruction
    messages = prompts.policy_critic_messages(
        frame, draft="Можно спокойно продолжить разговор.", history=[], latest_user_message="Да, Татарстан.",
    )
    data = json.loads(messages[-1]["content"])
    assert data["task"]["primary_goal"] == "respond_to_field_update"


def test_the_first_city_answer_remains_a_proven_plain_field_reply_after_privacy_masking(monkeypatch):
    state = debt_state(debts={"total": 400000}, client={"name": "Тест", "phone": "+70000000401"})
    state["chat_history"] = [{"role": "assistant", "content": "В каком городе и регионе вы живёте?"}]
    natural = "Здесь многое зависит от того, посильно ли вам сейчас платить по графику."
    answer, prepared, committed, calls = actual_turn(
        monkeypatch, state, "Казань.", replies=["Казань отметила.", natural],
        updates=[{"path": "client.region_class", "value": "rf_other"}],
    )
    assert answer == natural and prepared["_policy_spoken"].source == "model"
    critiques = [call for call in calls if call["model"] == "synthetic-analysis"]
    assert len(critiques) == 2
    latest = json.loads(critiques[0]["messages"][-1]["content"])["latest_user_message"]
    assert "Казань" not in latest and "скрыт" in latest
    assert committed["facts"]["client"]["region_class"] == "rf_other"
    assert committed["facts"]["client"]["phone"] == state["facts"]["client"]["phone"]
    assert not committed.get("delivery")
