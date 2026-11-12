"""Планировщик как обход фиксированной анкеты.

Заказчик 28.07.2026: «забей на приоритеты, собирай хардкор, анкету». Порядок
вопросов больше не зависит от того, какой маршрут сейчас выглядит вероятным;
классификатор вызывается один раз, когда анкета пройдена.
"""
from __future__ import annotations

import pytest

from routing import plan
from routing.engine import get_fact
from utils import set_by_path


ANSWERS = {
    "client.name": "Василий",
    "client.phone": "+79990000000",
    "request.amount": 800_000,
    "client.age": 42,
    "client.region_class": "moscow_mo",
    "debts.has_active_credits": True,
    "debts.total": 400_000,
    "debts.overdue_duration": "no_overdue",
    "employment.type": "official",
    "employment.income": 150_000,
    "assets.has_realty": True,
    "assets.realty.kind": "flat",
    "assets.realty.region_class": "moscow_mo",
    "assets.realty.ownership_form": "private",
    "assets.realty.owners": "client_only",
    "assets.has_vehicle": True,
    "assets.vehicle.year": 2018,
    "assets.vehicle.make": "Kia",
    "assets.vehicle.model": "Rio",
    "assets.vehicle.pledged": False,
}


def walk(matrix, overrides: dict | None = None, stop_at_contacts: bool = True):
    """Прогоняет анкету, отвечая ровно на то, что спросили. Возвращает порядок."""
    answers = {**ANSWERS, **(overrides or {})}
    facts: dict = {}
    asked: list[str] = []
    for _ in range(40):
        result = plan(matrix, facts)
        if result.action == "contacts" and stop_at_contacts:
            return asked, result
        if result.action in {"route_found", "no_fit"}:
            return asked, result
        for path in result.field_paths:
            assert get_fact(facts, path) is None, f"поле {path} спрошено повторно"
            asked.append(path)
            set_by_path(facts, path, answers[path])
    raise AssertionError(f"анкета не завершилась: {asked}")


FULL_ORDER = [
    "client.name",
    "request.amount",
    "client.age",
    "client.region_class",
    "debts.has_active_credits",
    "debts.total",
    "debts.overdue_duration",
    "employment.type",
    "employment.income",
    "assets.has_realty",
    "assets.realty.kind",
    "assets.realty.region_class",
    "assets.realty.ownership_form",
    "assets.realty.owners",
    "assets.has_vehicle",
    "assets.vehicle.year",
    "assets.vehicle.make",
    "assets.vehicle.model",
    "assets.vehicle.pledged",
]


def test_script_order_is_exactly_the_call_centre_form(matrix):
    """Главный тест разворота: порядок вопросов — контракт с заказчиком."""
    asked, _ = walk(matrix)
    assert asked == FULL_ORDER


def test_no_credits_skips_debt_details(matrix):
    asked, _ = walk(matrix, {"debts.has_active_credits": False})
    assert "debts.total" not in asked
    assert "debts.overdue_duration" not in asked
    assert asked[asked.index("debts.has_active_credits") + 1] == "employment.type"


def test_unemployed_is_not_asked_about_income(matrix):
    """`after_in`: «безработный» — непустая строка, обычного after тут мало."""
    asked, _ = walk(matrix, {"employment.type": "unemployed"})
    assert "employment.income" not in asked


def test_no_realty_goes_straight_to_the_vehicle_block(matrix):
    asked, _ = walk(matrix, {"assets.has_realty": False})
    assert not any(path.startswith("assets.realty.") for path in asked)
    assert asked[asked.index("assets.has_realty") + 1] == "assets.has_vehicle"


def test_no_vehicle_ends_the_questionnaire(matrix):
    asked, result = walk(matrix, {"assets.has_vehicle": False})
    assert not any(path.startswith("assets.vehicle.") for path in asked)
    assert result.action == "contacts"


def test_turn_never_exceeds_its_declared_group(matrix):
    """Ход спрашивает ровно то, что объявлено шагом сценария, и не больше.

    До 30.07.2026 правило было «один вопрос за ход». Заказчик его отменил:
    полтора десятка отдельных ходов он назвал перебором и попросил объединить
    вопросы без подвопросов. Но объединять их вправе только сценарий —
    планировщик по-прежнему не может набрать полей от себя.
    """
    groups = {tuple(step.ask) for step in matrix.script}
    facts: dict = {}
    set_by_path(facts, "client.name", "Василий")
    for _ in range(40):
        result = plan(matrix, facts)
        if result.action != "ask":
            break
        paths = tuple(result.field_paths)
        assert any(set(paths) <= set(group) for group in groups), paths
        for path in paths:
            set_by_path(facts, path, ANSWERS[path])


def test_children_never_share_a_turn_with_their_parent(matrix):
    facts: dict = {"client": {"name": "Василий"}}
    for path in ("request.amount", "client.age", "client.region_class",
                 "debts.has_active_credits", "debts.total", "debts.overdue_duration",
                 "employment.type", "employment.income"):
        set_by_path(facts, path, ANSWERS[path])

    result = plan(matrix, facts)

    assert result.field_paths == ["assets.has_realty"]
    assert "assets.realty.kind" in result.forbidden_paths
    assert "assets.realty.owners" in result.forbidden_paths


def test_phone_is_never_requested_mid_questionnaire(matrix):
    """Телефон — не шаг сценария. Иначе backend приклеит просьбу о номере
    посреди анкеты, а тон финала будет нечем выбрать."""
    facts: dict = {}
    for _ in range(40):
        result = plan(matrix, facts)
        if result.action != "ask":
            break
        assert "client.phone" not in result.field_paths
        for path in result.field_paths:
            set_by_path(facts, path, ANSWERS[path])


def test_objection_does_not_change_what_is_asked(matrix):
    """Возражение отрабатывается словами, а не составом анкеты.

    Страховка от «полезного» возврата фильтрации в опрос: заказчик прямо просил
    не менять маршрут на ходу («просто задача тупо собрать анкету»).
    """
    asked, _ = walk(matrix, {})
    refused, _ = walk(
        matrix,
        {},
    )
    assert asked == refused

    facts: dict = {"client": {"name": "Василий"}, "preferences": {"collateral_allowed": False}}
    for path in ("request.amount", "client.age", "client.region_class",
                 "debts.has_active_credits", "debts.total", "debts.overdue_duration",
                 "employment.type", "employment.income"):
        set_by_path(facts, path, ANSWERS[path])

    assert plan(matrix, facts).field_paths == ["assets.has_realty"]


def test_collateral_refusal_excludes_asset_routes_at_classification(matrix):
    """Отказ от залога не меняет вопросы, но исключает залоговые маршруты в финале."""
    facts: dict = {}
    for path, value in ANSWERS.items():
        set_by_path(facts, path, value)

    allowed = plan(matrix, facts)
    assert allowed.action == "route_found"
    assert allowed.matched is not None and allowed.matched.route.family == "realty"

    set_by_path(facts, "preferences.collateral_allowed", False)
    refused = plan(matrix, facts)
    assert refused.action == "route_found"
    assert refused.matched is not None
    assert refused.matched.route.family == "consumer"


def test_contacts_tone_is_confident_when_a_route_matched(matrix):
    facts: dict = {}
    for path, value in ANSWERS.items():
        if path == "client.phone":
            continue
        set_by_path(facts, path, value)

    result = plan(matrix, facts)

    assert result.action == "contacts"
    assert result.field_paths == ["client.phone"]
    assert result.matched is not None


def test_contacts_tone_is_soft_when_nothing_matched(matrix):
    """Заказчик отдельно отметил риск: пообещать решение и не найти его."""
    facts: dict = {}
    for path, value in ANSWERS.items():
        if path == "client.phone":
            continue
        set_by_path(facts, path, value)
    # Возраст вне всех окон + нет активов + чистая история → ни один маршрут не проходит.
    set_by_path(facts, "client.age", 70)
    set_by_path(facts, "request.amount", 100_000)
    set_by_path(facts, "assets.has_realty", False)
    set_by_path(facts, "assets.has_vehicle", False)
    set_by_path(facts, "debts.has_active_credits", False)
    set_by_path(facts, "debts.total", 0)

    result = plan(matrix, facts)

    assert result.action == "contacts"
    assert result.field_paths == ["client.phone"]
    assert result.matched is None


def test_dead_end_closes_honestly_once_the_phone_is_known(matrix):
    facts: dict = {}
    for path, value in ANSWERS.items():
        set_by_path(facts, path, value)
    set_by_path(facts, "client.age", 70)
    set_by_path(facts, "request.amount", 100_000)
    set_by_path(facts, "assets.has_realty", False)
    set_by_path(facts, "assets.has_vehicle", False)
    set_by_path(facts, "debts.has_active_credits", False)
    set_by_path(facts, "debts.total", 0)

    result = plan(matrix, facts)

    assert result.action == "no_fit"
    assert result.field_paths == []


def test_transition_is_attached_to_its_step(matrix):
    facts: dict = {"client": {"name": "Василий"}}

    first = plan(matrix, facts)
    assert first.field_paths == ["request.amount", "client.age"]
    assert first.transition is None

    for path in ("request.amount", "client.age", "client.region_class",
                 "debts.has_active_credits", "debts.total", "debts.overdue_duration",
                 "employment.type", "employment.income"):
        set_by_path(facts, path, ANSWERS[path])

    assets = plan(matrix, facts)
    assert assets.field_paths == ["assets.has_realty"]
    assert assets.transition and "про активы" in assets.transition
    assert "{name}" not in assets.transition
    assert "Василий" in assets.transition


def test_bootstrap_asks_only_the_name(matrix):
    result = plan(matrix, {})
    assert result.field_paths == ["client.name"]


def _completed(overrides: dict) -> dict:
    """Полностью пройденная анкета с точечными правками — вход классификатора."""
    facts: dict = {}
    for path, value in {**ANSWERS, **overrides}.items():
        set_by_path(facts, path, value)
    return facts


def test_self_employed_qualifies_for_the_main_consumer_route(matrix):
    """Решение заказчика 27.07.2026: самозанятый идёт в «Потреб» 4-1.

    Исходная таблица перечисляет только «Бизнес / Официально / Найм», поэтому без
    этого теста расхождение выглядит как ошибка переноса и его снова «исправят».
    """
    facts = _completed({
        "employment.type": "self_employed",
        "request.amount": 600_000,
        "assets.has_realty": False,
        "assets.has_vehicle": False,
    })

    result = plan(matrix, facts)

    assert result.action == "route_found"
    assert result.matched is not None and result.matched.id == "consumer"


def test_each_region_list_leads_to_its_own_realty_route(matrix):
    """Требование заказчика 27.07.2026: у 1-2 и 1-3 разные списки регионов.

    Город из листа «МБК» закрывает 1-2, город только из листа «Кредит.Клаб» —
    уходит в 1-3.
    """
    mbk = _completed({
        "client.region_class": "rf_other",
        "assets.realty.region_class": "mbk_list",
    })
    assert plan(matrix, mbk).matched is not None
    assert plan(matrix, mbk).matched.id == "realty_rf"

    credit_club = _completed({
        "client.region_class": "rf_other",
        "assets.realty.region_class": "realty_list",
    })
    assert plan(matrix, credit_club).matched is not None
    assert plan(matrix, credit_club).matched.id == "credit_club"


def test_geo_deadlock_advances_after_max_attempts(matrix):
    """R1: при линейной анкете зависший регион делает диалог бесконечным.

    Раньше это маскировалось живыми маршрутами других семей — планировщик просто
    уходил в другую ветку. Теперь курсор встал бы на регионе навсегда, клиент не
    дошёл бы до телефона и лид не создался бы вовсе.
    """
    facts: dict = {
        "client": {
            "name": "Василий",
            "city": "Советск",
            "location": {
                "status": "ambiguous",
                "query": "Советск",
                "confirmation_required": True,
                "attempts": 3,
                "candidates": [{"display_name": "Калининградская обл, г Советск"}],
            },
        },
        "request": {"amount": 800_000},
    }
    set_by_path(facts, "client.age", 42)

    result = plan(matrix, facts)

    assert result.field_paths != ["client.region_class"], (
        "после трёх неудачных уточнений анкета обязана идти дальше"
    )
    assert result.field_paths == ["debts.has_active_credits"]


@pytest.mark.parametrize(
    "credit_history",
    ["has_overdue", "no_overdue", "past_overdue", "ready_bankruptcy"],
)
def test_bfl_accepts_every_credit_history_answer(matrix, credit_history):
    """Решение заказчика 28.07.2026: у БФЛ проходят все четыре ответа.

    В исходной таблице у 2-x перечислены только «Есть просрочки» и «Нет
    просрочек платежа», но проходят они одинаково — значит поле не отсекает,
    а просто должно быть заполнено. Клиент, сказавший «готов оформить
    банкротство», раньше единственный не попадал в банкротство вообще.
    """
    facts = _completed({
        "client.region_class": "moscow_mo",
        "assets.has_realty": False,
        "assets.has_vehicle": False,
        "employment.type": "unemployed",
        "debts.has_active_credits": True,
        "debts.total": 800_000,
        "debts.overdue_duration": "over_6m",
        "debts.credit_history": credit_history,
    })

    result = plan(matrix, facts)

    assert result.action == "route_found"
    assert result.matched is not None and result.matched.id == "bfl_msk"


@pytest.mark.parametrize("credit_history", ["past_overdue", "ready_bankruptcy"])
def test_consumer_still_rejects_bad_credit_history(matrix, credit_history):
    """Потреб не трогали: там «нет просрочек» — настоящий фильтр."""
    facts = _completed({
        "request.amount": 600_000,
        "client.region_class": "moscow_mo",
        "assets.has_realty": False,
        "assets.has_vehicle": False,
        "employment.type": "official",
        "debts.has_active_credits": True,
        "debts.total": 100_000,
        "debts.overdue_duration": "no_overdue",
        "debts.credit_history": credit_history,
    })

    result = plan(matrix, facts)

    assert result.matched is None
