"""Прямой ответ на заданный вопрос разбирается без модели.

Замер 19.08.2026: вызов извлекателя — 4,8 с молчания перед каждой репликой
бота, и заказчик жалуется именно на эту паузу. Анкета фиксированная, так что на
каждом ходу известно, о чём спросили: «сколько вам лет» → «37» разбирается
нормализатором за микросекунды.

Правило намеренно осторожное. Потерянный факт уводит клиента по чужому
маршруту, лишний вызов стоит только времени — поэтому во всех сомнительных
случаях ход отдаётся модели.
"""
from __future__ import annotations

import pytest

import core
from interpretation.fastpath import (
    MONEY_PATHS,
    bare_money_update,
    parse_expected_answer,
    parse_units_confirmation,
)
from routing import get_matrix
from state import init_state


@pytest.fixture(scope="module")
def matrix():
    return get_matrix()


def _value(message, paths, matrix):
    updates = parse_expected_answer(message, paths, matrix)
    return None if updates is None else updates[0]["value"]


@pytest.mark.parametrize(
    "message, path, expected",
    [
        ("37", "client.age", 37),
        ("42 года", "client.age", 42),
        ("3 млн", "request.amount", 3_000_000),
        ("нет", "debts.has_active_credits", False),
        ("неа", "assets.has_realty", False),
        ("кредитов нет", "debts.has_active_credits", False),
        ("машины нет", "assets.has_vehicle", False),
        ("не работаю", "employment.type", "unemployed"),
        ("89213456789", "client.phone", "+79213456789"),
    ],
)
def test_direct_answers_need_no_model(message, path, expected, matrix):
    assert _value(message, [path], matrix) == expected


@pytest.mark.parametrize(
    "message, path, why",
    [
        ("есть квартира в Москве", "assets.has_realty", "тип объекта и город сверх ответа"),
        ("да, на 2 млн", "debts.has_active_credits", "сумма долга сверх ответа"),
        ("а зачем вам это?", "client.age", "вопрос клиента, нужна человеческая реакция"),
        ("нет, мне 43", "client.age", "поправка сказанного раньше"),
        ("42 года, Москва", "client.age", "возраст и город в одной реплике"),
        ("42 года, москва", "client.age", "город может быть со строчной буквы"),
        ("42, казань", "client.age", "город после запятой со строчной буквы"),
        ("2020 toyota", "assets.vehicle.year", "год и марка латиницей"),
        ("да, тойота", "assets.has_vehicle", "наличие и марка автомобиля"),
        ("квартира, москва", "assets.realty.kind", "тип и город объекта"),
        (
            "частная, моя",
            "assets.realty.ownership_form",
            "форма собственности и владелец",
        ),
        ("42, вернее 43", "client.age", "исправление в конце реплики"),
        ("42, точнее 43", "client.age", "уточнение старого значения"),
        ("42 ошибся 43", "client.age", "явное исправление без запятой"),
        ("да, но дорого", "assets.has_realty", "возражение требует реакции"),
        ("да, боюсь", "assets.has_realty", "эмоция требует реакции"),
        ("да, сомневаюсь", "assets.has_realty", "сомнение требует реакции"),
        ("да, без залога", "assets.has_realty", "отказ от залога — отдельный факт"),
        ("машины нет", "assets.has_realty", "отказ относится к другому объекту"),
        ("недвижимости нет", "assets.has_vehicle", "отказ относится к другому объекту"),
        ("кредитов нет", "assets.has_vehicle", "отказ относится к другому объекту"),
        ("не квартира", "assets.realty.kind", "отрицание нельзя превращать в квартиру"),
        ("это не квартира", "assets.realty.kind", "отрицание enum внутри фразы"),
        (
            "не частная",
            "assets.realty.ownership_form",
            "отрицание нельзя превращать в частную собственность",
        ),
        ("42 43", "client.age", "два возможных возраста"),
        ("42/43", "client.age", "альтернативные значения возраста"),
        ("да тойота", "assets.has_vehicle", "наличие и марка без запятой"),
        ("есть тойота", "assets.has_vehicle", "наличие и марка без запятой"),
        ("квартира москва", "assets.realty.kind", "тип и город без запятой"),
        ("частная моя", "assets.realty.ownership_form", "форма и владелец без запятой"),
        ("да волнуюсь", "assets.has_realty", "эмоция вне короткого списка"),
        ("да в москве", "assets.has_realty", "наличие и регион"),
        ("42 года", "request.amount", "возраст нельзя записывать суммой"),
        ("мне 43", "request.amount", "возраст нельзя записывать суммой"),
        ("42 года", "employment.income", "возраст нельзя записывать доходом"),
        ("2015 рублей", "assets.vehicle.year", "сумму нельзя записывать годом"),
        ("+7 999 123-45-67", "request.amount", "телефон нельзя записывать суммой"),
        ("10.05.1985", "request.amount", "дату нельзя записывать суммой"),
        ("работаю официально, доход 150 тысяч", "employment.type", "доход сверх ответа"),
        (
            "ну как сказать, вроде есть немного, но я не уверен точно",
            "debts.has_active_credits",
            "размытый рассказ, а не ответ",
        ),
    ],
)
def test_anything_beyond_a_direct_answer_goes_to_the_model(message, path, why, matrix):
    assert parse_expected_answer(message, [path], matrix) is None, why


@pytest.mark.parametrize(
    "message",
    ["Казань", "Москва", "Казань, Татарстан", "москва", "казань", "г москва"],
)
def test_plain_region_answer_skips_the_extractor(message, matrix):
    """Город всё равно разбирает geo/, но extractor для одного названия не нужен.

    Сырой регион сохраняется локально, а класс определяет только geo/.
    """
    assert parse_expected_answer(message, ["client.region_class"], matrix) == [
        {"path": "client.region", "value": message, "confidence": "high"}
    ]


@pytest.mark.parametrize(
    "message",
    [
        "не помню",
        "а зачем вам это?",
        "Москва, квартира моя",
        "Москва, мне 42",
        "это не Москва",
        "для чего Москва",
        "пожалуй Москва",
        "может Москва",
        "Москва или Казань",
    ],
)
def test_ambiguous_region_answer_still_goes_to_the_model(message, matrix):
    assert parse_expected_answer(message, ["client.region_class"], matrix) is None


def test_region_correction_goes_to_the_model(matrix):
    assert (
        parse_expected_answer(
            "не Москва, Казань",
            ["client.region_class"],
            matrix,
        )
        is None
    )


def test_second_realty_region_is_parsed_like_the_first(matrix):
    """Раньше ответ о втором объекте отдавали извлекателю, а он на голое
    название города не возвращал ничего (бой 05.10.2026, tests/test_dialogs_1005.py)."""
    assert parse_expected_answer(
        "Москва",
        ["assets.realty_2.region_class"],
        matrix,
    ) == [{"path": "assets.realty_2.region", "value": "Москва", "confidence": "high"}]


def test_plain_region_is_enriched_without_calling_extractor(monkeypatch):
    state = init_state({"full_name": "Иван", "desired_amount": "1 млн", "age": 42})
    state = core.prepare_turn(state, "", submit_delivery=False)
    # Память о заданном вопросе фиксирует commit_turn, и только за вопрос,
    # который клиент увидел. Поэтому ход доводится до конца с непустой
    # репликой: на пустой (приветственной) вопрос анкеты не звучит.
    state = core.commit_turn(state, "нужен кредит", "В каком городе вы живёте?")[
        "current_state"
    ]
    assert state["script"]["asked"] == ["client.region_class"]

    def unexpected_extractor(*args, **kwargs):
        raise AssertionError("для однозначного названия города extractor не нужен")

    monkeypatch.setattr(core.llm_agent, "extract_facts", unexpected_extractor)
    prepared = core.prepare_turn(state, "Москва", submit_delivery=False)

    assert prepared["facts"]["client"]["region"].endswith("Москва")
    assert prepared["facts"]["client"]["region_class"] == "moscow_mo"


def test_lowercase_region_is_enriched_without_calling_extractor(monkeypatch):
    state = init_state({"full_name": "Иван", "desired_amount": "1 млн", "age": 42})
    state = core.prepare_turn(state, "", submit_delivery=False)
    # Город — ответ на вопрос анкеты, а не первая реплика: первую разбирает
    # развилка на входе, и ей извлекатель нужен. Без этого хода тест при живом
    # ключе OpenRouter ходил в модель и проходил её ответом, а без ключа падал.
    state = core.commit_turn(state, "нужен кредит", "В каком городе вы живёте?")[
        "current_state"
    ]

    def unexpected_extractor(*args, **kwargs):
        raise AssertionError("строчный город не требует extractor")

    monkeypatch.setattr(core.llm_agent, "extract_facts", unexpected_extractor)
    monkeypatch.setattr(core.llm_agent, "extract_fact_candidates", unexpected_extractor)
    prepared = core.prepare_turn(state, "москва", submit_delivery=False)

    assert prepared["facts"]["client"]["region_class"] == "moscow_mo"


@pytest.mark.parametrize(
    ("message", "path"),
    [
        ("500", "request.amount"),
        ("300", "debts.total"),
        ("150", "employment.income"),
    ],
)
def test_small_money_without_units_goes_to_the_model(message, path, matrix):
    assert parse_expected_answer(message, [path], matrix) is None


@pytest.mark.parametrize(
    ("message", "path"),
    [
        ("квартира псков", "assets.realty.kind"),
        ("квартира орёл", "assets.realty.kind"),
        ("квартира залог", "assets.realty.kind"),
        ("частная арест", "assets.realty.ownership_form"),
    ],
)
def test_compound_enum_answers_go_to_the_model(message, path, matrix):
    assert parse_expected_answer(message, [path], matrix) is None


@pytest.mark.parametrize(
    ("message", "path", "expected"),
    [
        ("квартира", "assets.realty.kind", "flat"),
        ("частная", "assets.realty.ownership_form", "private"),
        ("официально", "employment.type", "official"),
        ("работаю официально", "employment.type", "official"),
    ],
)
def test_exact_enum_phrase_keeps_the_fast_path(message, path, expected, matrix):
    assert _value(message, [path], matrix) == expected


def test_paired_step_goes_to_the_model(matrix):
    """Шаг «сумма и возраст» разложить без модели нельзя.

    Взять половину — значит переспросить то, что человек уже сказал.
    """
    assert parse_expected_answer(
        "800 тысяч, мне 42", ["request.amount", "client.age"], matrix
    ) is None


def test_unrecognized_answer_goes_to_the_model(matrix):
    assert parse_expected_answer("ага, вроде того", ["client.age"], matrix) is None


def test_nothing_asked_means_nothing_parsed(matrix):
    assert parse_expected_answer("нет", [], matrix) is None


@pytest.mark.parametrize(
    "message, path",
    [
        ("Тестик меня величать", "client.name"),
        ("Иван", "client.name"),
        ("Тойота", "assets.vehicle.make"),
    ],
)
def test_free_text_always_goes_to_the_model(message, path, matrix):
    """У свободного текста нет неверного значения — подходит любая строка.

    На вопрос об имени клиент ответил «Тестик меня величать», и именем стала бы
    вся фраза целиком. Такие поля разбирает модель.
    """
    assert parse_expected_answer(message, [path], matrix) is None


def test_phone_is_the_exception_among_text_fields(matrix):
    """У телефона строгий формат: непохожее на номер отвергается."""
    assert parse_expected_answer("89213456789", ["client.phone"], matrix) is not None
    assert parse_expected_answer("позже скажу", ["client.phone"], matrix) is None


@pytest.mark.parametrize(
    "message, why",
    [
        ("конечно нет", "согласие и отказ в одной фразе"),
        ("да нет", "разговорный отказ, который выглядит как согласие"),
        ("да нет, наверное", "то же самое с оговоркой"),
        ("не знаю", "клиент не знает — это не «нет»"),
        ("не помню", "то же самое"),
        ("не уверен", "то же самое"),
        ("затрудняюсь ответить", "то же самое"),
        ("вроде нет", "предположение, а не ответ"),
        ("наверное да", "то же самое"),
        ("кажется есть", "то же самое"),
    ],
)
def test_ambiguous_yes_or_no_goes_to_the_model(message, why, matrix):
    """Раньше «конечно нет» записывалось как ДА, а «не знаю» — как НЕТ.

    Разбор «да/нет» падал в parse_bool, а тот ищет согласие подстрокой: в
    «конечно нет» находилось «конечно» и побеждало. Обратная сторона — голое
    «не» в списке отказов, из-за которого «не знаю» становилось твёрдым «нет» и
    закрывало целую ветку анкеты.

    Ошибка быстрого разбора необратима: по своим путям фон не перезаписывает.
    Поэтому любая двусмысленность уходит модели.
    """
    assert parse_expected_answer(message, ["assets.has_realty"], matrix) is None, why


@pytest.mark.parametrize(
    "message, expected",
    [
        ("нет", False),
        ("неа", False),
        ("нету", False),
        ("недвижимости нет", False),
        ("да", True),
        ("есть", True),
        ("да, есть", True),
        ("конечно", True),
    ],
)
def test_a_plain_answer_still_works(message, expected, matrix):
    """Ужесточение не должно съесть обычные ответы."""
    updates = parse_expected_answer(message, ["assets.has_realty"], matrix)
    assert updates is not None, "простой ответ должен разбираться без модели"
    assert updates[0]["value"] is expected


def test_bare_no_on_the_debt_and_overdue_turn_means_no_overdue(matrix):
    """Прод 19.09.2026, лид 3496486: бот спросил сумму долга и просрочку одной
    репликой, клиент ответил «Ее нет». Факт потерялся, лид уехал без кредитной
    истории, и правила CRM не отнесли клиента к потребу.
    """
    pair = ["debts.total", "debts.overdue_duration"]
    expected = [
        {"path": "debts.overdue_duration", "value": "no_overdue", "confidence": "high"}
    ]
    for text in ("Ее нет", "нет", "Просрочек нет", "нету", "никаких просрочек"):
        assert parse_expected_answer(text, pair, matrix) == expected, text


def test_the_pair_turn_leaves_everything_but_a_bare_no_to_the_model(matrix):
    """Отрицание однозначно только потому, что сумму так не называют."""
    pair = ["debts.total", "debts.overdue_duration"]
    for text in ("300000", "около 300 тысяч", "Не знаю", "Да", "есть, полгода"):
        assert parse_expected_answer(text, pair, matrix) is None, text
    # Другие пары вопросов по-прежнему целиком за моделью.
    assert parse_expected_answer("нет", ["request.amount", "client.age"], matrix) is None


def test_bare_duration_words_on_the_debt_and_overdue_turn_write_only_duration(matrix):
    """Прод 22.09.2026, лид 3498907: бот спросил сумму долга и срок просрочки
    одной репликой, клиент ответил «Пока три дня». Извлекатель факт не
    записал вовсе, а бот сам сочинил уточнение, которого нет ни в анкете, ни
    в инструкциях. Сумма при этом остаётся нетронутой — реплика её не несёт.
    """
    pair = ["debts.total", "debts.overdue_duration"]
    assert parse_expected_answer("Пока три дня", pair, matrix) == [
        {"path": "debts.overdue_duration", "value": "up_to_1m", "confidence": "high"}
    ]


def test_bare_duration_without_a_unit_on_the_pair_turn_goes_to_the_model(matrix):
    """«Три» без единицы — может быть и три дня, и три месяца, не гадаем."""
    pair = ["debts.total", "debts.overdue_duration"]
    assert parse_expected_answer("Пока три", pair, matrix) is None


def test_amount_and_duration_together_on_the_pair_turn_go_to_the_model(matrix):
    """Сумма и срок сразу — второй факт в реплике, решает модель."""
    pair = ["debts.total", "debts.overdue_duration"]
    assert parse_expected_answer("160000, больше года просрочка", pair, matrix) is None


# --------------------------------------------------------------------------- #
# Голое число денежного поля без единицы измерения (брифинг «Голое число в
# денежном вопросе», прод 20.09.2026, лид 3497115: «400 общая сумма» → 400
# рублей вместо переспроса).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", sorted(MONEY_PATHS))
def test_bare_number_without_a_unit_is_suspicious_on_all_three_fields(path, matrix):
    update = {"path": path, "value": "400", "confidence": "high"}
    assert bare_money_update(update, "400 общая сумма", matrix) == 400


@pytest.mark.parametrize("path", sorted(MONEY_PATHS))
def test_zero_is_never_suspicious(path, matrix):
    update = {"path": path, "value": "0", "confidence": "high"}
    assert bare_money_update(update, "0", matrix) is None


@pytest.mark.parametrize("path", ["request.amount", "debts.total"])
@pytest.mark.parametrize("raw", ["1000", "1300", "2500", "9999"])
def test_four_digit_amount_and_debt_are_suspicious_too(path, raw, matrix):
    """Прод 30.09.2026, лид 3508342: на «какая сумма нужна» клиентка ответила
    «1300», имея в виду 1,3 миллиона, и в лид ушло 1300 рублей. Раньше порог
    был 999 — «1000 и больше не трогаем»."""
    update = {"path": path, "value": raw, "confidence": "high"}
    assert bare_money_update(update, raw, matrix) == int(raw)


@pytest.mark.parametrize("path", ["request.amount", "debts.total"])
def test_five_digits_are_taken_as_rubles(path, matrix):
    update = {"path": path, "value": "10000", "confidence": "high"}
    assert bare_money_update(update, "10000", matrix) is None


def test_four_digit_income_is_taken_as_rubles(matrix):
    """Доход 5000 миллионами не бывает: для дохода порог прежний, до 999."""
    update = {"path": "employment.income", "value": "5000", "confidence": "high"}
    assert bare_money_update(update, "5000", matrix) is None


def test_four_digit_amount_with_a_unit_is_not_suspicious(matrix):
    update = {"path": "request.amount", "value": "2500", "confidence": "high"}
    assert bare_money_update(update, "2500р", matrix) is None


def test_a_non_money_path_is_never_suspicious(matrix):
    update = {"path": "client.age", "value": "40", "confidence": "high"}
    assert bare_money_update(update, "40", matrix) is None


@pytest.mark.parametrize(
    "raw_text",
    [
        "400 рублей",
        "400 руб",
        "400 тысяч",
        "400 тыс",
        "400к",
        "400 к",
        "400 млн",
        "400 миллиона",
        "400 ₽",
        "400 косарей",
    ],
)
def test_an_explicit_unit_in_the_raw_reply_is_not_suspicious(raw_text, matrix):
    """Значение факта не домножено (extractor мог его не масштабировать), но
    единица названа в самой реплике — записываем как есть, не переспрашиваем.
    """
    update = {"path": "debts.total", "value": "400", "confidence": "high"}
    assert bare_money_update(update, raw_text, matrix) is None


@pytest.mark.parametrize("word", ["да", "ага", "тыс", "тысяч", "верно", "именно"])
def test_confirmation_agreement_multiplies_by_a_thousand(word):
    assert parse_units_confirmation(400, word) == 400_000


@pytest.mark.parametrize("word", ["рублей", "нет"])
def test_confirmation_refusal_keeps_the_raw_value(word):
    assert parse_units_confirmation(400, word) == 400


@pytest.mark.parametrize(
    "message",
    ["500", "500 тысяч", "полмиллиона", "не знаю", "да нет"],
)
def test_confirmation_anything_else_defers_to_the_regular_parse(message):
    assert parse_units_confirmation(400, message) is None


@pytest.mark.parametrize(
    "word", ["миллион", "млн", "миллиона", "да, миллиона", "второе", "1300 — миллиона"]
)
def test_four_digit_choice_takes_millions(word):
    """На «1300 рублей или 1,3 миллиона?» единицу называет клиент."""
    assert parse_units_confirmation(1300, word) == 1_300_000


@pytest.mark.parametrize(
    "word", ["рублей", "нет, рублей", "тысяч", "первое", "2500 рублей", "руб"]
)
def test_four_digit_choice_takes_rubles(word):
    """Кому правда нужно 2500 рублей, так и отвечает — и остаётся 2500."""
    assert parse_units_confirmation(2500, word) == 2500


@pytest.mark.parametrize("answer", ["да", "нет", "верно", "Да, 1500", "1500", "не знаю"])
def test_yes_and_no_do_not_answer_a_choice(answer):
    """Разбор 04.10.2026: вопрос «5 тысяч или 5 миллионов?» — выбор из двух,
    «да» и «нет» на него ничего не выбирают, домножать по ним нельзя."""
    assert parse_units_confirmation(1500, answer) is None


@pytest.mark.parametrize("answer", ["5 тысяч", "5 миллионов", "Да, 2500", "500"])
def test_another_number_goes_to_the_regular_parse(answer):
    assert parse_units_confirmation(5000, answer) is None


@pytest.mark.parametrize("word", ["млн", "миллион"])
def test_millions_do_not_confirm_a_three_digit_guess(word):
    """«400 тысяч?» — «миллион»: это не согласие на 400 тысяч."""
    assert parse_units_confirmation(400, word) is None
