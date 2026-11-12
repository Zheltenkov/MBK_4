"""Тесты нормализатора. Главное свойство — не угадывать: непонятное становится None."""
from __future__ import annotations

from datetime import date

import pytest

from routing.normalize import (
    classify_region,
    classify_region_confident,
    normalize_fact,
    parse_age,
    parse_amount,
    parse_bool,
    parse_phone,
    parse_enum,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1 500 000", 1_500_000),
        ("1500000", 1_500_000),
        ("1,5 млн", 1_500_000),
        ("1.5 млн", 1_500_000),
        ("полтора миллиона", None),
        ("150к", 150_000),
        ("300 тыс", 300_000),
        ("300 000 руб", 300_000),
        ("250 косарей", 250_000),
        (500_000, 500_000),
        ("", None),
        ("не знаю", None),
    ],
)
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


def test_amount_multiplier_only_applies_after_the_number():
    """«к» в «Курск» не должно превращать сумму в тысячи."""
    assert parse_amount("200000 Курск") == 200_000


@pytest.mark.parametrize(
    "raw, expected",
    [
        (35, 35),
        ("35", 35),
        ("35 лет", 35),
        ("1990-05-20", 36),
        ("20.05.1990", 36),
        ("вчера", None),
        (0, None),
        (200, None),
    ],
)
def test_parse_age(raw, expected):
    assert parse_age(raw, today=date(2026, 7, 21)) == expected


def test_age_from_birthday_not_yet_reached_this_year():
    assert parse_age("1990-12-31", today=date(2026, 7, 21)) == 35


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Москва", "moscow_mo"),
        ("г. Москва, Тверская", "moscow_mo"),
        ("Химки, Московская область", "moscow_mo"),
        ("Подмосковье", "moscow_mo"),
        ("Казань", "rf_other"),
        ("Новосибирская область", "rf_other"),
        ("", None),
        (None, None),
    ],
)
def test_classify_region(raw, expected):
    assert classify_region(raw) == expected


def test_bare_town_name_is_not_resolved_to_moscow_region():
    """Известное ограничение: без справочника ФИАС «Химки» — обычный регион.
    Подключение ДаData закрывает этот кейс; до тех пор бот переспросит область."""
    assert classify_region("Химки") == "rf_other"


@pytest.mark.parametrize("town", ["Томск", "Омск"])
def test_msk_substring_inside_town_is_not_moscow(town):
    """Сокращение «мск» допустимо только отдельным словом."""
    assert classify_region(town) == "rf_other"


@pytest.mark.parametrize("raw", ["МСК", "живу в мск", "г. Мск"])
def test_standalone_msk_abbreviation_is_moscow(raw):
    assert classify_region(raw) == "moscow_mo"


@pytest.mark.parametrize("raw", ["не знаю", "не хочу говорить"])
def test_unknown_region_is_not_guessed_as_rf(raw):
    assert classify_region(raw) is None


def test_confident_classifier_admits_it_does_not_know(matrix):
    """Главное отличие от classify_region: у него есть ответ «не знаю».

    Голое имя города, которого нет в справочниках, догадкой не заполняется:
    именно на этой догадке клиент уезжал по чужому маршруту.
    """
    assert classify_region_confident("Химки", matrix) is None
    assert classify_region_confident("зачем вам это", matrix) is None
    assert classify_region_confident("не знаю", matrix) is None
    assert classify_region_confident("", matrix) is None


def test_confident_classifier_uses_the_local_directories(matrix):
    """Списки регионов лежат в репозитории: попадание в них — знание, а не догадка."""
    assert classify_region_confident("Барнаул", matrix, include_realty_list=True) == "mbk_list"
    assert (
        classify_region_confident("Майкоп, Адыгея", matrix, include_realty_list=True)
        == "realty_list"
    )
    assert classify_region_confident("Москва", matrix) == "moscow_mo"
    # Место жительства матрица делит только на Москву/МО и остальную РФ, поэтому
    # для клиента попадание города в список значит ровно «это не Москва».
    assert classify_region_confident("Барнаул", matrix) == "rf_other"
    # Субъект назван прямо, хотя слова «республика» в реплике нет.
    assert classify_region_confident("Татарстан", matrix) == "rf_other"


def test_default_classifier_still_answers_with_the_fallback(matrix):
    """Там, где следом идёт DaData, умолчание rf_other осознанно и остаётся."""
    assert classify_region("Химки", matrix) == "rf_other"
    assert classify_region("не знаю", matrix) is None


def test_region_list_class_requires_filled_directory(matrix):
    """Пока справочник «Списка» пуст, класс realty_list не присваивается никому."""
    assert classify_region("Тверская область", matrix) == "rf_other"


@pytest.mark.parametrize(
    "path, raw, expected",
    [
        ("assets.realty.kind", "квартира", "flat"),
        ("assets.realty.kind", "апартаменты", "apartments"),
        ("assets.realty.kind", "коммерческое помещение", "commercial"),
        ("assets.realty.owners", "я единоличный собственник", "client_only"),
        ("assets.realty.owners", "я и супруга", "client_and_spouse"),
        ("assets.realty.ownership_form", "в частной собственности", "private"),
        ("assets.realty.ownership_form", "муниципальная", "municipal"),
        ("assets.realty.restriction", "без обременений", "none"),
        ("assets.realty.restriction", "квартира в ипотеке", "mortgage"),
        ("assets.realty.restriction", "объект под арестом", "arrest"),
        ("debts.credit_history", "есть просрочки", "has_overdue"),
        ("debts.credit_history", "нет просрочек платежа", "no_overdue"),
        ("debts.overdue_duration", "от 1 до 3 месяцев", "from_1m_to_3m"),
        # С 31.07.2026 «официально» — это найм: отдельным вариантом оно только
        # путало клиентов, а в CRM давало два кода за одно положение дел.
        ("employment.type", "официально по ТК", "official"),
        # Извлекатель иногда отдаёт второе значение матрицы за ту же занятость.
        ("employment.type", "hired", "official"),
        ("employment.type", "свой бизнес", "business"),
        ("employment.type", "безработный", "unemployed"),
        ("employment.type", "самозанятый", "self_employed"),
        ("employment.type", "работаю неофициально", "unofficial"),
        ("employment.type", "пенсионер", "retired"),
        ("debts.credit_history", "были просрочки", "past_overdue"),
        ("debts.overdue_duration", "от 3 до 6 месяцев", "from_3m_to_6m"),
        ("debts.overdue_duration", "более 6 месяцев", "over_6m"),
        ("assets.realty.kind", "что-то непонятное", None),
    ],
)
def test_parse_enum(path, raw, expected):
    assert parse_enum(path, raw) == expected


def test_parse_enum_passes_through_normalized_codes():
    assert parse_enum("assets.realty.kind", "flat") == "flat"


# --------------------------------------------------------------------------- #
# Срок просрочки живым языком (брифинг «Срок просрочки живым языком», прод
# 22.09.2026, лид 3498907: «Пока три дня» не разобралось вовсе, а бот сам
# сочинил уточняющий вопрос, которого нет в анкете).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw, expected",
    [
        # Реальные ответы клиентов за 30 дней (раздел «Повод» брифинга).
        ("три дня", "up_to_1m"),
        ("две недели", "up_to_1m"),
        ("1 месяц", "up_to_1m"),
        ("около года", "over_6m"),
        ("2 года", "over_6m"),
        ("больше года", "over_6m"),
        ("меньше полугода", "from_3m_to_6m"),
        ("просрочка 2 недели", "up_to_1m"),
        # Границы корзин.
        ("30 дней", "up_to_1m"),
        ("31 день", "from_1m_to_3m"),
        ("3 месяца", "from_1m_to_3m"),
        ("6 месяцев", "from_3m_to_6m"),
        ("7 месяцев", "over_6m"),
        ("полгода", "from_3m_to_6m"),
        ("год", "over_6m"),
        # По одному примеру на каждую корзину из раздела «Что сделать».
        ("неделя", "up_to_1m"),
        ("пару недель", "up_to_1m"),
        ("полтора месяца", "from_1m_to_3m"),
        ("2 месяца", "from_1m_to_3m"),
        ("6 недель", "from_1m_to_3m"),
        ("пару месяцев", "from_1m_to_3m"),
        ("4 месяца", "from_3m_to_6m"),
    ],
)
def test_parse_enum_reads_overdue_duration_from_plain_speech(raw, expected):
    assert parse_enum("debts.overdue_duration", raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        # «Больше» ровно на границе корзины уводит в следующую: «больше
        # полугода» — это не «от 3 до 6 месяцев». Мутационный прогон 26.09.2026.
        ("больше полугода", "over_6m"),
        ("более полугода", "over_6m"),
        ("свыше полугода", "over_6m"),
        ("дольше полугода", "over_6m"),
        ("полгода с лишним", "over_6m"),
        ("больше 6 месяцев", "over_6m"),
        ("больше шести месяцев", "over_6m"),
        ("больше трёх месяцев", "from_3m_to_6m"),
        ("больше месяца", "from_1m_to_3m"),
        # Внутри корзины «больше» ничего не меняет.
        ("больше двух недель", "up_to_1m"),
        ("больше четырёх месяцев", "from_3m_to_6m"),
        # «Меньше» и «почти» границу не переходят.
        ("меньше месяца", "up_to_1m"),
        ("почти полгода", "from_3m_to_6m"),
    ],
)
def test_longer_than_a_bucket_boundary_moves_to_the_next_bucket(raw, expected):
    assert parse_enum("debts.overdue_duration", raw) == expected


def test_bare_number_without_a_unit_is_not_guessed_as_overdue_duration():
    """«Три» без единицы измерения может значить и дни, и месяцы — не гадаем."""
    assert parse_enum("debts.overdue_duration", "три") is None


def test_amount_named_alongside_duration_is_not_read_as_duration_alone():
    """«160000, больше года просрочка» несёт и сумму, и срок — решает модель."""
    assert parse_enum("debts.overdue_duration", "160000, больше года просрочка") is None


def test_no_overdue_markers_still_win_over_duration_words():
    """Существующее поведение `no_overdue` не должно тронуться новым разбором."""
    assert parse_enum("debts.overdue_duration", "нет просрочек") == "no_overdue"


@pytest.mark.parametrize(
    "raw, expected",
    [("да", True), ("есть", True), ("нет", False), ("нету", False), ("не знаю", None), (True, True)],
)
def test_parse_bool(raw, expected):
    assert parse_bool(raw) == expected


def test_normalize_fact_routes_by_path(matrix):
    assert normalize_fact("request.amount", "1,5 млн") == 1_500_000
    assert normalize_fact("client.region_class", "Химки, Московская область", matrix) == "moscow_mo"
    assert normalize_fact("assets.has_realty", "да") is True
    assert normalize_fact("assets.vehicle.year", "2015") == 2015
    assert normalize_fact("assets.vehicle.year", "1800") is None
    assert normalize_fact("debts.credit_history", "есть просрочки") == "has_overdue"


def test_client_region_does_not_accept_realty_only_class(matrix):
    matrix.regions["realty_list"] = ["Татарстан\tКазань"]

    assert normalize_fact("client.region_class", "realty_list", matrix) is None
    assert normalize_fact("client.region_class", "Казань, Татарстан", matrix) == "rf_other"
    assert normalize_fact("assets.realty.region_class", "realty_list", matrix) == "realty_list"


def test_unrecognized_value_becomes_none_not_a_guess():
    """Лучше лишний вопрос, чем чужой маршрут из-за выдуманного значения."""
    assert normalize_fact("assets.realty.kind", "ну там всякое") is None
    assert normalize_fact("request.amount", "сколько дадите") is None


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("+7 999 123-45-67", "+79991234567"),
        ("8 (999) 123-45-67", "+79991234567"),
        ("позвоните позже", None),
        ("[телефон]", None),
    ],
)
def test_parse_phone_is_strict(raw, expected):
    assert parse_phone(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("хочу оформить банкротство", "debt_relief"),
        ("нужно рефинансирование", "refinance"),
        ("нужны деньги", "cash"),
    ],
)
def test_goal_is_normalized(raw, expected):
    assert normalize_fact("client.goal", raw) == expected


def test_explicit_collateral_refusal_is_normalized():
    assert normalize_fact("preferences.collateral_allowed", "нет") is False


@pytest.mark.parametrize(
    ("path", "raw", "expected"),
    [
        ("preferences.realty_collateral_allowed", "нет", False),
        ("preferences.vehicle_collateral_allowed", "да", True),
        ("preferences.preferred_collateral", "лучше под машину", "vehicle"),
        ("preferences.preferred_collateral", "предпочитаю недвижимость", "realty"),
        ("preferences.preferred_collateral", "без разницы", "any"),
    ],
)
def test_product_specific_collateral_preferences_are_normalized(
    path,
    raw,
    expected,
):
    assert normalize_fact(path, raw) == expected


@pytest.mark.parametrize(
    ("path", "raw", "expected"),
    [
        ("employment.income", "80 тысяч", 80_000),
        ("employment.income", "150000", 150_000),
        ("debts.has_active_credits", "да", True),
        ("debts.has_active_credits", "нет", False),
        ("assets.vehicle.make", "  Kia  ", "Kia"),
        ("assets.vehicle.model", "Rio", "Rio"),
    ],
)
def test_new_questionnaire_fields_are_typed_by_matrix_kind(path, raw, expected, matrix):
    """Новые поля анкеты не перечислены поимённо в normalize_fact.

    Тип берётся из `kind` матрицы, иначе «80 тысяч» уехало бы в CRM строкой,
    а «да» так и не стало бы булевым.
    """
    assert normalize_fact(path, raw, matrix) == expected


@pytest.mark.parametrize(
    "answer",
    [
        "комната",
        "комната в коммуналке",
        "комнату в коммунальной квартире",
        "у меня комната в общежитии",
    ],
)
def test_a_room_is_a_room_and_not_a_flat(matrix, answer):
    """Комната превращалась в квартиру или терялась вовсе.

    Найдено 12.08.2026 при сверке эталонных фактов роли с живым прогоном: в
    матрице значение `room` есть, а нормализатор не выдавал его никогда.
    «Комнату в коммунальной квартире» ловила подстрока «квартир» и записывала
    flat — клиент с комнатой уходил на залоговый маршрут, который комнаты не
    принимает, а в CRM ехал realty_type «квартира».
    """
    assert normalize_fact("assets.realty.kind", answer, matrix) == "room"


@pytest.mark.parametrize(
    "answer",
    ["доля в квартире", "у меня только доля", "1/3 доли в квартире"],
)
def test_a_share_is_not_a_whole_flat(matrix, answer):
    """Доля — не квартира: правила залога у них разные."""
    assert normalize_fact("assets.realty.kind", answer, matrix) != "flat"


def test_a_plain_flat_is_still_a_flat(matrix):
    assert normalize_fact("assets.realty.kind", "квартира", matrix) == "flat"
    # Сленг раньше не опознавался вовсе и уходил модели. С 28.09.2026 «двушка»,
    # «однушка», «N-комнатная квартира» — квартира (tests/test_dialogs_0928.py).
    assert normalize_fact("assets.realty.kind", "двушка в панельке", matrix) == "flat"


def test_every_realty_kind_of_the_matrix_fits_the_internal_contract(matrix):
    """Значение из матрицы обязано пройти через наш же контракт лида.

    12.08.2026: в матрицу добавили `room`, а Literal в delivery/contracts.py о
    нём не знал. Клиент с комнатой ронял CRM-workflow прямо на проде —
    ValidationError на RealtyPayload. Нашёл мутационный прогон, не юнит-тесты:
    они проверяли нормализатор и матрицу по отдельности.
    """
    from delivery.contracts import RealtyPayload

    for kind in matrix.fields["assets.realty.kind"].values:
        RealtyPayload(kind=kind)
