"""Сценарий опроса как данные: порядок вопросов и его согласованность с матрицей.

Заказчик 28.07.2026 отменил адаптивный опрос по приоритетам и потребовал
фиксированную анкету («забей на приоритеты, собирай хардкор, анкету»). Порядок
живёт в matrix.yaml рядом с формулировками, которые он же и правит, поэтому
рассогласование сценария и матрицы должно ловиться загрузкой, а не в проде.
"""
from __future__ import annotations

import re

import pytest
import yaml

from evaluation.coherence import EVALUATIVE_OPENERS
from routing.loader import MATRIX_PATH, load_matrix


# Заказчик 30.07.2026: «какие-то вопросы сгруппировать, иначе их слишком много,
# там можно спросить сумму, возраст вместе… объединить те, которые под собой не
# несут подвопросов». Условие про подвопросы — его собственное, и оно совпадает
# с уже существующим запретом спрашивать родителя и ребёнка в одном ходу.
EXPECTED_ORDER = [
    ["request.amount", "client.age"],
    ["client.region_class"],
    ["debts.has_active_credits"],
    # Долг и срок просрочки разделены 01.10.2026: спрошенные одной репликой,
    # они трижды теряли срок (лиды 3496486, 3498907, 3508342) — клиент отвечал
    # на половину вопроса. Без срока CRM не собирает кредитную историю.
    ["debts.total"],
    ["debts.overdue_duration"],
    ["employment.type"],
    ["employment.income"],
    ["assets.has_realty"],
    ["assets.realty.kind"],
    ["assets.realty.region_class"],
    ["assets.realty.ownership_form", "assets.realty.owners"],
    ["assets.has_vehicle"],
    ["assets.vehicle.year", "assets.vehicle.make", "assets.vehicle.model"],
    ["assets.vehicle.pledged"],
]


def test_grouped_steps_never_mix_a_parent_with_its_child(matrix):
    """Правило группировки, названное заказчиком: объединять можно только те
    вопросы, которые не тянут за собой подвопросов."""
    parents = {
        spec.after for spec in matrix.fields.values() if spec.after
    }
    for step in matrix.script:
        if len(step.ask) == 1:
            continue
        for path in step.ask:
            assert path not in parents, (
                f"шаг {step.ask}: {path} открывает подвопросы и не может "
                "делить ход с другими"
            )


# Ветка второго объекта спрашивается не всем: она открывается, только если
# realty-qualification сказала, что первый объект под залог не проходит.
# Базовую анкету она не удлиняет, поэтому из контрактных проверок исключается.
SECOND_REALTY_STEPS = {"assets.has_second_realty"}


def _base_steps(matrix) -> list[list[str]]:
    return [
        step.ask
        for step in matrix.script
        if not any(
            path in SECOND_REALTY_STEPS or path.startswith("assets.realty_2.")
            for path in step.ask
        )
    ]


def test_questionnaire_got_shorter(matrix):
    """17 шагов превращали разговор в допрос — на это заказчик и жаловался.

    14, а не 13: срок просрочки вынесен в отдельный вопрос (см. EXPECTED_ORDER).
    Клиенту без кредитов оба шага про долг не задаются вовсе."""
    assert len(_base_steps(matrix)) == 14


def test_script_matches_the_call_centre_form(matrix):
    """Порядок из «Вопросы_порядок.txt» — это контракт с заказчиком."""
    assert _base_steps(matrix) == EXPECTED_ORDER


def test_second_realty_branch_follows_the_first_object(matrix):
    """Спрашивать про второй объект раньше, чем узнали про первый, бессмысленно."""
    order = [step.ask for step in matrix.script]
    first_object = max(
        index for index, ask in enumerate(order)
        if any(path.startswith("assets.realty.") for path in ask)
    )
    branch = min(
        index for index, ask in enumerate(order)
        if "assets.has_second_realty" in ask
    )

    assert first_object < branch


def test_phone_is_not_a_script_step(matrix):
    """Телефон спрашивается после классификации, иначе тон финала выбрать нечем."""
    asked = {path for step in matrix.script for path in step.ask}
    assert "client.phone" not in asked
    assert "client.name" not in asked


def test_transitions_are_declared_where_the_owner_asked_for_them(matrix):
    """Первый набор вопросов сохранён, отдельный переход остался только у активов."""
    transitions = {
        step.ask[0]: step.transition
        for step in matrix.script
        if step.transition
    }
    assert matrix.script[0].ask == ["request.amount", "client.age"]
    assert matrix.script[0].transition is None
    assert set(transitions) == {"assets.has_realty"}
    assert "{name}" in transitions["assets.has_realty"]


def test_transitions_do_not_evaluate_the_answer(matrix):
    """Переход вставляется backend'ом безусловно и не знает, что сказал клиент.

    Поэтому оценочный зачин в нём рано или поздно достаётся человеку, который
    только что сообщил о потере работы. В логах 20260728_200727 это случилось в
    4 сценариях из 15. Смысл фраз заказчика сохранён, оценка убрана.
    """
    for step in matrix.script:
        if not step.transition:
            continue
        lowered = step.transition.casefold()
        for word in EVALUATIVE_OPENERS:
            assert not re.search(rf"(?<![а-яё]){word}", lowered), (
                f"{step.ask[0]}: оценочный зачин «{word}» в переходе"
            )


def test_transitions_keep_the_owner_key_phrases(matrix):
    """Новый отказ от вступления не меняет сохранённый переход к активам."""
    transitions = {step.ask[0]: step.transition for step in matrix.script if step.transition}

    assert "request.amount" not in transitions
    assert "теперь про активы" in transitions["assets.has_realty"]
    assert "почти закончили" in transitions["assets.has_realty"]


def test_conditional_children_declare_their_parent(matrix):
    """Подвопросы открываются только после родителя — это и есть ветвление анкеты."""
    expected_parents = {
        "debts.total": "debts.has_active_credits",
        "debts.overdue_duration": "debts.has_active_credits",
        "employment.income": "employment.type",
        "assets.realty.kind": "assets.has_realty",
        "assets.realty.region_class": "assets.has_realty",
        "assets.realty.ownership_form": "assets.has_realty",
        "assets.realty.owners": "assets.has_realty",
        "assets.vehicle.year": "assets.has_vehicle",
        "assets.vehicle.make": "assets.has_vehicle",
        "assets.vehicle.model": "assets.has_vehicle",
        "assets.vehicle.pledged": "assets.has_vehicle",
    }
    for path, parent in expected_parents.items():
        assert matrix.fields[path].after == parent, path


def test_income_is_gated_by_employment_value_not_just_presence(matrix):
    """«Безработный» — непустая строка, поэтому одного after мало: нужен after_in."""
    spec = matrix.fields["employment.income"]
    assert spec.after == "employment.type"
    assert spec.after_in is not None
    assert "unemployed" not in spec.after_in
    assert "retired" not in spec.after_in
    assert "official" in spec.after_in


def test_every_condition_field_is_reachable_by_the_script(matrix):
    """Главная проверка: условие на поле, которого анкета не спрашивает, делает
    маршрут навсегда UNKNOWN. При адаптивном планировщике это самоисправлялось."""
    asked = {path for step in matrix.script for path in step.ask}
    for route in matrix.routes:
        if not route.enabled:
            continue
        for condition in route.conditions:
            spec = matrix.fields[condition.field]
            reachable = (
                condition.field in asked
                or spec.initial
                or spec.contact
                or condition.allow_empty
                or condition.field in _DERIVED_PATHS
            )
            assert reachable, f"{route.id}: {condition.field} не спрашивается и не выводится"


# Поля, которые анкета не спрашивает напрямую: их заполняет сервер.
# client.region_class — из DaData, credit_history/total — из ответов анкеты.
_DERIVED_PATHS = {"client.region_class", "assets.realty.region_class", "debts.credit_history"}


def _patched_matrix(tmp_path, mutate):
    raw = yaml.safe_load(MATRIX_PATH.read_text(encoding="utf-8"))
    mutate(raw)
    patched = tmp_path / "matrix.yaml"
    patched.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    # Справочники лежат рядом с исходной матрицей, поэтому копируем путь источников.
    for name, relative in (raw.get("region_sources") or {}).items():
        source = MATRIX_PATH.parent / relative
        target = patched.parent / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    return patched


def test_unknown_field_in_script_fails_loading(tmp_path):
    def mutate(raw):
        raw["script"].append({"ask": ["client.favourite_colour"]})

    with pytest.raises(ValueError, match="сценар"):
        load_matrix(_patched_matrix(tmp_path, mutate))


def test_after_in_outside_parent_values_fails_loading(tmp_path):
    def mutate(raw):
        raw["fields"]["employment.income"]["after_in"] = ["freelancer"]

    with pytest.raises(ValueError, match="after_in"):
        load_matrix(_patched_matrix(tmp_path, mutate))


def test_a_room_matches_no_realty_route(matrix):
    """Комната — законное значение, но залоговые маршруты её не принимают.

    До 12.08.2026 нормализатор выдавал `room`, а в перечислении матрицы его не
    было: факт получал значение вне списка. Ни один маршрут комнату не берёт —
    это верно по бизнесу, но теперь видно явно, а в CRM уезжает честный тип
    объекта, а не «квартира».
    """
    from routing import plan as build_plan

    facts = {
        "client": {"name": "Марина", "age": 29, "region_class": "moscow_mo",
                   "phone": "+79990000000"},
        "request": {"amount": 350_000},
        "debts": {"has_active_credits": False, "total": 0,
                  "credit_history": "no_overdue"},
        "employment": {"type": "hired", "income": 70_000},
        "assets": {
            "has_realty": True,
            "has_vehicle": False,
            "realty": {
                "region_class": "moscow_mo",
                "kind": "room",
                "ownership_form": "private",
                "owners": "client_only",
            },
        },
    }

    current = build_plan(matrix, facts)

    realty = [s for s in current.statuses if s.id.startswith("realty")]
    assert realty and all(s.state == "FAIL" for s in realty)
    assert current.matched is None or not current.matched.id.startswith("realty")


def test_room_is_a_declared_value(matrix):
    assert "room" in matrix.fields["assets.realty.kind"].values
