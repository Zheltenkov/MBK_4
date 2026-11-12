"""Ответы про второй объект обязаны приводиться к кодам матрицы.

Аудит 19.08.2026. Таблицы нормализации заведены на пути первого объекта
(`assets.realty.*`), а анкета второго спрашивает ровно то же самое по путям
`assets.realty_2.*`. Совпадения по строке нет — и ответ клиента «в
собственности» уезжал в CRM сырым текстом.

Цена прямая. Маппинг лида не знает такого значения и возвращает issue
`unknown_enum`, то есть залоговый лид не собирается вовсе. А сторож
`_qualifying_realty_root` сравнивает тип второго объекта с типом первого:
сырое «квартира» никогда не равно коду `flat`, поэтому вторая квартира
вытесняла первую даже там, где вытеснять запрещено.

Соседнюю дыру того же корня (регион второго объекта) закрыл коммит 0d90e90.
Поэтому проверяем не три пути списком, а правило: у любого перечислимого поля
матрицы обязана быть нормализация, и третий объект не должен заводить дыру
заново.
"""
from __future__ import annotations

import pytest

from core import apply_fact_updates
from delivery.crm_mapping import _qualifying_realty_root, map_lead_intake
from routing import get_matrix
from routing.normalize import normalize_fact


@pytest.fixture(scope="module")
def matrix():
    return get_matrix()


# Ответы человеческими словами — ровно так их приносит извлекатель.
ANKETA_ANSWERS = [
    ("kind", "квартира", "flat"),
    ("ownership_form", "в собственности", "private"),
    ("owners", "только я", "client_only"),
]


def test_no_enum_field_leaks_a_raw_answer(matrix):
    """Инвариант: перечислимое поле матрицы либо код, либо None — но не текст.

    Это и есть корень дефекта: хвост `normalize_fact` разбирал по `spec.kind`
    только money/int/bool/text, а enum без таблицы возвращал значение как есть.
    Проверка перечислением путей поймала бы realty_2 и снова пропустила бы
    realty_3.
    """
    samples = ["в собственности", "квартира", "только я", "затрудняюсь ответить"]
    leaked = []
    for path, spec in matrix.fields.items():
        if spec.kind != "enum":
            continue
        for sample in samples:
            value = normalize_fact(path, sample, matrix)
            if value is not None and value not in spec.values:
                leaked.append(f"{path}: {sample!r} -> {value!r}")

    assert not leaked, "перечислимые поля отдают не код матрицы: " + "; ".join(leaked)


@pytest.mark.parametrize("leaf, answer, code", ANKETA_ANSWERS)
def test_second_object_is_normalized_like_the_first(matrix, leaf, answer, code):
    """Анкета второго объекта — копия первой, значит и коды у них общие."""
    assert normalize_fact(f"assets.realty.{leaf}", answer, matrix) == code
    assert normalize_fact(f"assets.realty_2.{leaf}", answer, matrix) == code


@pytest.mark.parametrize("leaf, answer, code", ANKETA_ANSWERS)
def test_a_third_object_would_not_reopen_the_hole(matrix, leaf, answer, code):
    """Третий объект не должен ждать трёх новых строк в таблице.

    Полей `assets.realty_3.*` в матрице нет — и именно поэтому проверка тут:
    правило обязано работать до того, как поля появятся.
    """
    assert normalize_fact(f"assets.realty_3.{leaf}", answer, matrix) == code


def _facts_from_the_extractor() -> dict:
    """Факты, собранные продакшн-цепочкой, а не руками.

    Руками записанные коды прячут дефект: нормализатор в этом случае не
    вызывается вовсе.
    """
    facts = {
        "client": {
            "name": "Олег",
            "age": 46,
            "phone": "+79991234567",
            "region_class": "moscow_mo",
        },
        "request": {"amount": 900_000},
        "debts": {"has_active_credits": False, "total": 0},
        "employment": {"type": "official", "income": 110_000},
        "assets": {
            "has_realty": True,
            "realty": {
                "kind": "house",
                "city": "Воронеж",
                "region_class": "rf_other",
                "ownership_form": "private",
                "owners": "client_only",
            },
            "has_second_realty": True,
            "has_vehicle": False,
        },
    }
    updates = [
        {"path": "assets.realty_2.city", "value": "Москва"},
        {"path": "assets.realty_2.region_class", "value": "moscow_mo"},
        {"path": "assets.realty_2.kind", "value": "квартира"},
        {"path": "assets.realty_2.ownership_form", "value": "в собственности"},
        {"path": "assets.realty_2.owners", "value": "только я"},
    ]
    return apply_fact_updates(facts, updates)


def test_lead_of_the_second_object_reaches_crm():
    """Залоговая ветка обязана собирать лид, а не падать на unknown_enum."""
    facts = _facts_from_the_extractor()

    assert _qualifying_realty_root(facts) == "assets.realty_2"

    mapping = map_lead_intake(
        facts,
        form_filled_out=True,
        site_consent_shown=False,
        site_consent_answer=None,
    )

    assert mapping.ready, [f"{issue.code} {issue.path}" for issue in mapping.issues]
    assert mapping.request is not None
    assert mapping.request.realty_type.value == 190  # квартира
    assert mapping.request.chat_owner == "CLIENT"


def test_a_second_flat_does_not_push_out_the_first_one():
    """Сырой текст ломал и защиту от вытеснения.

    Тип второго объекта сравнивается с типом первого. Пока «квартира» не
    сводилась к `flat`, вторая квартира считалась другим объектом и занимала
    ключи первой — половина второго объекта вытесняла целый первый.
    """
    facts = _facts_from_the_extractor()
    facts["assets"]["realty"]["kind"] = "flat"

    assert _qualifying_realty_root(facts) == "assets.realty"
