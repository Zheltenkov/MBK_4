"""Кнопки вопросов анкеты: заданы в матрице и разбираются без модели.

Спека docs/superpowers/specs/2026-09-14-quick-replies-questionnaire-design.md.
Нажатие кнопки приходит боту обычным сообщением. Если быстрый разбор его не
узнаёт, ход зовёт модель — несколько секунд молчания на ответе, который
человек дал одним нажатием. Поэтому здесь проверяется каждая кнопка матрицы.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from interpretation import fastpath
from routing import FieldSpec


EXPECTED = {
    ("debts.has_active_credits", "Да, есть"): True,
    ("debts.has_active_credits", "Нет"): False,
    # Срок просрочки спрашивается отдельным вопросом с 01.10.2026 (лид 3508342).
    ("debts.overdue_duration", "Просрочек нет"): "no_overdue",
    ("debts.overdue_duration", "До месяца"): "up_to_1m",
    ("debts.overdue_duration", "1–3 месяца"): "from_1m_to_3m",
    ("debts.overdue_duration", "Больше 3 месяцев"): "from_3m_to_6m",
    ("employment.type", "По найму"): "official",
    ("employment.type", "Свой бизнес"): "business",
    ("employment.type", "Не работаю"): "unemployed",
    ("assets.has_realty", "Да, есть"): True,
    ("assets.has_realty", "Нет"): False,
    ("assets.realty.kind", "Квартира"): "flat",
    ("assets.realty.kind", "Дом"): "house",
    ("assets.realty.kind", "Апартаменты"): "apartments",
    ("assets.realty.kind", "Коммерческая"): "commercial",
    ("assets.has_second_realty", "Да, есть"): True,
    ("assets.has_second_realty", "Нет"): False,
    ("assets.has_vehicle", "Да, есть"): True,
    ("assets.has_vehicle", "Нет"): False,
    ("assets.vehicle.pledged", "В кредите"): True,
    ("assets.vehicle.pledged", "В залоге"): True,
    ("assets.vehicle.pledged", "Чистая"): False,
}


def _chips(matrix) -> set[tuple[str, str]]:
    return {
        (path, chip.value)
        for path, spec in matrix.fields.items()
        for chip in spec.quick_replies or []
    }


def test_chips_are_exactly_the_agreed_set(matrix):
    assert _chips(matrix) == set(EXPECTED)


def test_label_is_the_text_that_is_sent(matrix):
    """Человек видит в своей реплике ровно то, что нажал."""
    for spec in matrix.fields.values():
        for chip in spec.quick_replies or []:
            assert chip.label == chip.value


@pytest.mark.parametrize("path, value", sorted(EXPECTED))
def test_every_chip_is_parsed_without_the_model(matrix, path, value):
    assert fastpath.parse_expected_answer(value, [path], matrix) == [
        {"path": path, "value": EXPECTED[(path, value)], "confidence": "high"}
    ]


def test_chips_live_only_on_questions_asked_alone(matrix):
    """Спрошено два поля — быстрый разбор не берётся, кнопка ответила бы на половину."""
    asked_alone = {step.ask[0] for step in matrix.script if len(step.ask) == 1}
    for path, spec in matrix.fields.items():
        if spec.quick_replies:
            assert path in asked_alone, path


def test_no_more_than_four_chips():
    chips = [{"label": str(i), "value": str(i)} for i in range(5)]

    with pytest.raises(ValidationError):
        FieldSpec(label="x", kind="bool", ask="x", quick_replies=chips)


def test_free_answer_fields_get_no_chips():
    with pytest.raises(ValidationError):
        FieldSpec(
            label="x",
            kind="money",
            ask="x",
            quick_replies=[{"label": "100 тысяч", "value": "100 тысяч"}],
        )


def test_empty_chip_text_is_rejected():
    with pytest.raises(ValidationError):
        FieldSpec(
            label="x",
            kind="bool",
            ask="x",
            quick_replies=[{"label": "", "value": "Да"}],
        )
