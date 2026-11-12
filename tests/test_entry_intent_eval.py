"""Оценка развилки по корпусу: считается без сети, модель зовётся только с --live."""
from __future__ import annotations

import run_entry_intent_eval
from evaluation.entry_intent import EntryCase, score_entry_predictions
from routing.entry_intent import EntryIntent as I


CASES = [
    EntryCase("m1", "нужны деньги", I.MONEY, count=3),
    EntryCase("m2", "кредит", I.MONEY),
    EntryCase("e1", "оставлял заявку", I.EXISTING),
    EntryCase("u1", "здравствуйте", I.UNCLEAR, count=5),
    EntryCase("o1", "карта сбера", I.OFF_TOPIC),
]


def test_money_sent_to_a_branch_is_the_hard_failure():
    summary = score_entry_predictions(
        CASES,
        {"m1": I.OFF_TOPIC, "m2": I.UNCLEAR, "e1": I.EXISTING, "u1": I.UNCLEAR, "o1": I.OFF_TOPIC},
    )

    # money → unclear не потеря: оба ведут в анкету
    assert summary.lost_money == ("m1",)
    assert summary.lost_money_dialogs == 3
    assert summary.diverted_unclear == ()
    assert summary.correct == 3
    assert summary.total == 5
    assert summary.confusion["money"]["off_topic"] == 1


def test_unclear_led_away_is_reported_separately():
    summary = score_entry_predictions(CASES, {"u1": I.EXISTING})

    assert summary.diverted_unclear == ("u1",)


def test_missing_prediction_reads_as_unclear():
    summary = score_entry_predictions(CASES, {})

    assert summary.lost_money == ()
    assert summary.confusion["money"]["unclear"] == 2
    assert summary.correct == 1


def test_without_live_flag_the_model_is_never_called(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("без --live модель не вызывается")

    monkeypatch.setattr(run_entry_intent_eval, "extract_fact_candidates", forbidden)

    assert run_entry_intent_eval.main([]) == 0
    assert "корпус" in capsys.readouterr().out


def test_live_classification_reconciles_intent_like_production(monkeypatch):
    from interpretation.contracts import FactCandidateExtraction, SemanticBoundarySignals

    monkeypatch.setattr(
        run_entry_intent_eval,
        "extract_fact_candidates",
        lambda *a, **k: FactCandidateExtraction(
            entry_intent=I.OFF_TOPIC,
            boundaries=SemanticBoundarySignals(goal="cash"),
        ),
    )

    row = run_entry_intent_eval._classify(
        EntryCase("x", "хочу закрыть кредит в другом банке", I.MONEY), None, None
    )

    assert row["model"] == "unclear"
    assert row["effective"] == "unclear"
