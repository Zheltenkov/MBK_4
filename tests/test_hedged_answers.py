"""«Вроде», «кажется», «наверное» — уточняем один раз, потом принимаем.

Приблизительный ответ reducer держит как AMBIGUOUS и в факты не пишет: «около
трёхсот» не должно уехать в CRM суммой. Но если человек повторяет то же самое
после уточнения, третий заход превращается в допрос.

Ролевой прогон 11.08.2026, роль rambling_pensioner: на «нет, вроде нет
просрочек» бот переспросил, получил раздражённое «да нет же, я же сказала» — и
переспросил снова. Решение заказчика: одно уточнение, дальше принимаем ответ.
"""
from __future__ import annotations

from interpretation import reduce_fact_candidates
from interpretation.contracts import (
    Explicitness,
    FactCandidate,
    FactSource,
    FactSubject,
)


def _hedged(path: str, value: object, evidence: str = "вроде нет просрочек") -> FactCandidate:
    return FactCandidate(
        path=path,
        value=value,
        subject=FactSubject.CLIENT,
        explicitness=Explicitness.AMBIGUOUS,
        source=FactSource.USER_EXPLICIT,
        evidence=evidence,
    )


def test_first_hedged_answer_is_held_for_one_clarification(matrix):
    result = reduce_fact_candidates(
        {},
        {},
        [_hedged("debts.overdue_duration", "no_overdue")],
        evidence_text="вроде нет просрочек",
        matrix=matrix,
    )

    assert result.statuses["debts.overdue_duration"].value == "needs_confirmation"
    assert result.facts == {}


def test_hedged_answer_is_accepted_once_the_question_was_already_asked(matrix):
    """Второй раз то же самое — значит человек так и отвечает. Принимаем."""
    result = reduce_fact_candidates(
        {},
        {},
        [
            _hedged(
                "debts.overdue_duration",
                "no_overdue",
                evidence="я же сказала",
            )
        ],
        evidence_text="да нет же, я же сказала, вроде всё плачу",
        matrix=matrix,
        already_clarified={"debts.overdue_duration"},
    )

    assert result.facts["debts"]["overdue_duration"] == "no_overdue"
    assert result.statuses["debts.overdue_duration"].value != "needs_confirmation"


def test_runtime_passes_the_already_asked_paths():
    """Список уже уточнённых путей берётся из счётчика попыток анкеты.

    Без этой связки правило остаётся мёртвым: reducer не знает, сколько раз бот
    спрашивал. Ровно так уже вышло с потолком попыток для телефона.
    """
    import core

    # 1 — вопрос задан один раз, уточнения ещё не было.
    # 2 — бот переспросил, дальше принимаем ответ как есть.
    state = {
        "script": {
            "attempts": {
                "debts.overdue_duration": 2,
                "employment.income": 1,
                "client.age": 0,
            }
        }
    }

    assert core._clarified_paths(state) == {"debts.overdue_duration"}
