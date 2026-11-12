"""Разбор ожидаемого ответа без обращения к модели.

Анкета фиксированная: на каждом ходу известно, о чём именно спросили. На вопрос
«сколько вам полных лет» приходит «37», на «есть ли просрочки» — «нет», на
«в каком городе живёте» — название города. Такие ответы разбирает нормализатор
за микросекунды, и вызов извлекателя к ним ничего не добавляет.

Цена этого вызова — около пяти секунд молчания перед каждой репликой бота
(замер 19.08.2026), и заказчик жалуется именно на неё. Модель нужна там, где
человек ответил не по форме: переспросил, возразил, выдал три факта разом.

Правило простое и намеренно осторожное: сомневаемся — зовём модель. Ошибка
здесь стоит потерянного факта, а он уводит клиента по чужому маршруту, тогда
как лишний вызов стоит лишь времени.
"""
from __future__ import annotations

import re
from typing import Any

from routing import Matrix
from routing.normalize import (
    classify_region_confident,
    normalize_fact,
    parse_enum_exact,
    parse_overdue_duration_words,
    parse_phone,
)
from routing.ru_dates import parse_birthdate


# Короткий ответ по существу в эти рамки укладывается с запасом: «не работаю»,
# «Казань, Татарстан», «3 млн», «квартира в собственности». Всё, что длиннее, —
# это уже рассказ, и в нём почти наверняка есть факты сверх спрошенного.
MAX_ANSWER_CHARS = 45

# Поля, где цифры в ответе законны и ожидаемы.
_NUMERIC_PATHS = frozenset(
    {
        "request.amount",
        "client.age",
        "client.phone",
        "debts.total",
        "employment.income",
        "assets.vehicle.year",
    }
)

# Денежные поля матрицы (routing/matrix.yaml, kind: money). Голое число здесь
# неоднозначно — «400» может значить и 400 рублей, и 400 тысяч, а домножать за
# клиента нельзя. `core._split_bare_money_updates` ловит такое значение и
# просит переспросить вместо того, чтобы молча записать факт.
MONEY_PATHS = frozenset({"request.amount", "debts.total", "employment.income"})

# Вопрос клиента — всегда повод ответить по-человечески, а не просто записать
# факт. Такой ход отдаём модели целиком.
_QUESTION_MARKERS = ("?", "почему", "зачем", "для чего", "а если", "а что")

# Оговорки, после которых короткий ответ перестаёт быть простым: человек
# отвечает не на заданный вопрос, а поправляет сказанное раньше.
_CORRECTION_MARKERS = ("нет,", "не так", "ошибся", "оговорился", "вернее", "точнее")

# Даже при однозначном «да» эти фрагменты требуют содержательной реакции и/или
# несут отдельную бизнес-настройку. Пропуск extractor здесь делает бота не
# только сухим, но и может выбрать залог вопреки прямому отказу клиента.
_CONVERSATIONAL_MARKERS = (
    "без залога",
    "не хочу залог",
    "боюсь",
    "страшно",
    "сомневаюсь",
    "дорого",
)

# Прямой ответ короток и по делу. Четыре слова и больше — это уже «есть
# квартира в Москве», где кроме ответа на вопрос лежат ещё тип объекта и город.
MAX_ANSWER_WORDS = 3

# Поля, значение которых легко узнаётся в свободной речи. Если ответ опознаётся
# ещё и как значение одного из них, значит человек сказал больше спрошенного —
# и разбирать это должна модель, а не мы.
_GREEDY_PATHS = (
    "assets.realty.kind",
    "employment.type",
    "debts.overdue_duration",
)

# Для короткого прямого ответа регион может пропустить extractor, но сам факт
# быстрый путь не создаёт. Сырой город сохраняет core, после чего geo/ проверяет
# его через DaData и локальные справочники. Это не позволяет fallback-классу
# превратить «не помню» в «остальная Россия».
_LOCATION_PATHS = frozenset(
    {
        "client.region_class",
        "assets.realty.region_class",
        "assets.realty_2.region_class",
    }
)
# Куда ложится сырой ответ о месте: класс региона по нему считает geo/.
_RAW_LOCATION_PATH = {
    "client.region_class": "client.region",
    "assets.realty.region_class": "assets.realty.region",
    "assets.realty_2.region_class": "assets.realty_2.region",
}
# Адрес словами длиннее обычного короткого ответа: «Челябинская область
# город Златоуст». Узнаём его по слову типа — область, город, район.
_MAX_ADDRESS_WORDS = 6
_ADDRESS_TYPE_WORDS = frozenset(
    {
        "область", "обл", "край", "республика", "респ", "округ", "ао",
        "город", "г", "район", "р-н", "село", "с", "деревня", "д", "поселок",
        "посёлок", "пос", "пгт", "станица", "хутор", "аул",
    }
)


def _is_spelled_out_address(path: str, text: str) -> bool:
    """Ответ о месте, где названы область и город, — до шести слов.

    Бой 06.10.2026, сессия 72b931171e8f: «В каком регионе и городе находится
    второй дом?» — «Челябинская область город Златоуст». Четыре слова уходили
    извлекателю, а он про второй объект не возвращает ничего: поле осталось
    пустым, модель сама попросила телефон и сказала «передаю заявку», заявки
    не было. Без слова типа длинная реплика — рассуждение, а не адрес.
    """
    if path not in _LOCATION_PATHS:
        return False
    words = [word.casefold().strip(".,") for word in text.split()]
    return len(words) <= _MAX_ADDRESS_WORDS and any(
        word in _ADDRESS_TYPE_WORDS for word in words
    )


# Слова уверенности рядом с суммой. «Около» и «примерно» сюда не входят: это
# сомнение, и такой ответ уточняет модель.
_SURE_WORDS_RE = re.compile(
    r"(?<![а-яё])(?:точно|ровно|всего|итого|в\s+сумме|в\s+общем)(?![а-яё])",
    re.IGNORECASE,
)

# Согласие и отказ в живой речи. `parse_bool` рассчитан на значение от
# извлекателя и «нет» узнаёт только в начале строки — а человек пишет «машины
# нет» и «кредитов нет», то есть отрицание в конце. Здесь разбирается сама
# реплика, поэтому список свой.
# Голого «не» здесь намеренно нет: с ним «не знаю» становилось твёрдым отказом
# и закрывало целую ветку анкеты.
_NO_TOKENS = frozenset(
    {"нет", "неа", "нету", "отсутствует", "отсутствуют", "никаких", "ничего"}
)
_YES_TOKENS = frozenset(
    {"да", "есть", "имеется", "имеются", "ага", "угу", "конечно", "верно", "точно"}
)
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_EXTRA_PROPER_NOUN_RE = re.compile(
    r"(?:,\s*|\s+)(?:[А-ЯЁ][а-яё-]{2,}|[A-Za-z][A-Za-z-]{2,})(?:\b|$)",
    re.UNICODE,
)
_COMMA_WORD_RE = re.compile(r",\s*[^\W\d_]", re.UNICODE)

_AGE_WORDS = frozenset({"мне", "возраст", "полный", "полных", "год", "года", "лет", "г"})
_MONEY_WORDS = frozenset(
    {
        "р",
        "руб",
        "рубль",
        "рубля",
        "рублей",
        "тыс",
        "тысяча",
        "тысячи",
        "тысяч",
        "млн",
        "миллион",
        "миллиона",
        "миллионов",
        "млрд",
        "миллиард",
        "миллиарда",
        "миллиардов",
    }
)
_YEAR_WORDS = frozenset({"г", "год", "года", "выпуска"})

# Единицы измерения сверх уже размеченных `_MONEY_WORDS`: «к» как отдельное
# слово («400к» и «400 к» — цифры не входят в _WORD_RE, поэтому оба разбираются
# одинаково) и «косарь» — сленговая тысяча, которой нет среди словарных форм.
_EXTRA_MONEY_UNIT_TOKENS = frozenset({"к"})
_EXTRA_MONEY_UNIT_PREFIXES = ("косар",)


def _has_money_unit_marker(text: str) -> bool:
    """Клиент явно назвал единицу — рубли, тысячи, миллионы, «к» или «косарь»."""
    if "₽" in text:
        return True
    tokens = {word.casefold() for word in _WORD_RE.findall(text)}
    if tokens & _MONEY_WORDS or tokens & _EXTRA_MONEY_UNIT_TOKENS:
        return True
    return any(
        token.startswith(prefix)
        for token in tokens
        for prefix in _EXTRA_MONEY_UNIT_PREFIXES
    )

# Клиент не знает или гадает. Это не ответ, и превращать его в «да» или «нет»
# нельзя: ошибка быстрого разбора необратима — по своим путям фоновый
# извлекатель значение не перепишет.
_UNCERTAIN_MARKERS = (
    "не знаю",
    "не помню",
    "не уверен",
    "не в курсе",
    "затрудняюсь",
    "вроде",
    "наверное",
    "наверно",
    "пожалуй",
    "может ",
    "кажется",
    "может быть",
    "возможно",
    "сложно сказать",
)

_LOCATION_AMBIGUITY_TOKENS = frozenset({"не", "нет", "или", "либо"})

# В bool fast path недостаточно увидеть одно «нет»: важно убедиться, что отказ
# относится к заданному объекту. Иначе «машины нет» на вопрос о недвижимости
# закрывает недвижимость. Всё, чего нет в этом маленьком контракте, разбирает
# extractor вместе с субъектом и контекстом.
_DIRECT_BOOL_PHRASES = frozenset(
    {
        "да",
        "есть",
        "имеется",
        "имеются",
        "ага",
        "угу",
        "конечно",
        "верно",
        "точно",
        "да есть",
        "конечно да",
        "нет",
        "неа",
        "нету",
        "отсутствует",
        "отсутствуют",
        "никаких",
        "ничего",
        "точно нет",
    }
)
_PATH_BOOL_PHRASES: dict[str, frozenset[str]] = {
    "debts.has_active_credits": frozenset(
        {"кредитов нет", "кредита нет", "долгов нет", "займов нет"}
    ),
    "assets.has_realty": frozenset(
        {
            "недвижимости нет",
            "жилья нет",
            "квартиры нет",
            "дома нет",
        }
    ),
    "assets.has_vehicle": frozenset(
        {"машины нет", "автомобиля нет", "авто нет", "транспорта нет"}
    ),
}

# Согласие и отказ, сказанные не словами «да»/«нет». На вопрос «машина в
# кредите или залоге, или чистая» кнопки анкеты отвечают «В кредите», «В
# залоге» и «Чистая» (quick_replies в matrix.yaml) — без этой таблицы нажатие
# уходило бы модели.
_PATH_BOOL_VALUES: dict[str, dict[str, bool]] = {
    "assets.vehicle.pledged": {
        "в кредите": True,
        "в залоге": True,
        "чистая": False,
        "чистый": False,
    },
}

_NEGATION_TOKENS = frozenset({"не", "нет", "без"})
_OWNER_TOKENS = frozenset({"мой", "моя", "моё", "мое", "наша", "наш", "собственник"})
_MONEY_GROUPED_RE = re.compile(r"^\d{1,3}(?:[\s\u00a0]\d{3})+(?:[.,]\d+)?$")
_MONEY_DECIMAL_RE = re.compile(r"^\d+[.,]\d+$")


# Пара «сумма долга и просрочка» задаётся одной репликой, и голое отрицание
# отвечает только про просрочку: сумму так не называют. Прод 19.09.2026 (лид
# 3496486): клиент ответил «Ее нет», извлекатель не записал ничего, лид ушёл в
# CRM без кредитной истории, и правила не отнесли его к потребу.
_PAIR_NEGATION_ANSWER = {
    frozenset({"debts.total", "debts.overdue_duration"}): (
        "debts.overdue_duration",
        "no_overdue",
    ),
}
_NEGATION_EXTRA_TOKENS = frozenset({"её", "ее", "их", "его", "у", "меня"})

# Тот же ход «сумма долга + срок просрочки» одной репликой, но словами срока,
# а не отрицанием — прод 22.09.2026, лид 3498907 (см. `_pair_overdue_duration`).
_PAIR_DURATION_PATHS = frozenset({"debts.total", "debts.overdue_duration"})


_YES_BEFORE_DURATION_RE = re.compile(
    r"^(?:да|есть|да,?\s+есть|ага|угу)\s*[,.—-]?\s+", re.IGNORECASE
)


def _pair_turn_disqualified(lowered: str) -> bool:
    """Вопрос, поправка, неуверенность или возражение — ходу с двумя полями
    здесь делать нечего, вся реплика целиком уходит модели."""
    return any(
        marker in lowered
        for markers in (
            _QUESTION_MARKERS,
            _CORRECTION_MARKERS,
            _UNCERTAIN_MARKERS,
            _CONVERSATIONAL_MARKERS,
        )
        for marker in markers
    )


def _pair_negation(
    message: str, expected_paths: list[str]
) -> list[dict[str, Any]] | None:
    """Отрицание на ходу с двумя вопросами — только там, где оно однозначно."""
    target = _PAIR_NEGATION_ANSWER.get(frozenset(expected_paths))
    if target is None or len(expected_paths) != 2:
        return None
    text = " ".join((message or "").split())
    if not text or len(text) > MAX_ANSWER_CHARS or re.search(r"\d", text):
        return None
    if _pair_turn_disqualified(text.casefold()):
        return None
    path, value = target
    tokens = [word.casefold() for word in _WORD_RE.findall(text)]
    negation = any(token in _NO_TOKENS for token in tokens) and all(
        token in _NO_TOKENS
        or token in _NEGATION_EXTRA_TOKENS
        or token.startswith("просроч")
        for token in tokens
    )
    if not negation and parse_enum_exact(path, text) != value:
        return None
    return [{"path": path, "value": value, "confidence": "high"}]


def _pair_overdue_duration(
    message: str, expected_paths: list[str]
) -> list[dict[str, Any]] | None:
    """Срок просрочки словами на ходе «сумма долга + срок просрочки».

    Прод 22.09.2026, лид 3498907: бот спросил про сумму долга и срок
    просрочки одной репликой, клиент ответил «Пока три дня». Извлекатель
    факт не записал вовсе, и бот сам сочинил уточнение, которого нет ни в
    анкете, ни в инструкциях. Сумму эта ветка не трогает и не проверяет —
    словами так не называют, а если рядом со сроком нашлось что-то ещё
    непонятое (в том числе отдельная цифра — например, сумма долга в той же
    реплике), `parse_overdue_duration_words` сама откажется в пользу модели.
    """
    if frozenset(expected_paths) != _PAIR_DURATION_PATHS or len(expected_paths) != 2:
        return None
    text = " ".join((message or "").split())
    if not text or len(text) > MAX_ANSWER_CHARS:
        return None
    if _pair_turn_disqualified(text.casefold()):
        return None
    bucket = parse_overdue_duration_words(text)
    if bucket is None:
        return None
    return [{"path": "debts.overdue_duration", "value": bucket, "confidence": "high"}]


def parse_expected_answer(
    message: str,
    expected_paths: list[str],
    matrix: Matrix | None = None,
) -> list[dict[str, Any]] | None:
    """Разбирает прямой ответ на заданный вопрос.

    Возвращает список обновлений в том же виде, что и извлекатель, либо None —
    «не берусь, зовите модель».

    Работает только когда спрошено ровно одно поле. Шаги, где вопросов два
    сразу («сумма и возраст»), отданы модели: разложить один ответ на два поля
    без неё нельзя, а взять половину — значит переспросить то, что человек уже
    сказал. Исключения — `_pair_negation` и `_pair_overdue_duration`: два
    узких случая пары «сумма долга + срок просрочки», где половина ответа
    однозначна и без модели.
    """
    if len(expected_paths) != 1:
        return _pair_negation(message, expected_paths) or _pair_overdue_duration(
            message, expected_paths
        )
    if not _is_parseable_field(expected_paths[0], matrix):
        return None

    text = " ".join((message or "").split())
    if not text or len(text) > MAX_ANSWER_CHARS:
        return None

    if expected_paths[0] == "debts.overdue_duration":
        # A reinforcing "Нет," is still one complete answer to this question.
        # Parse it before the general comma guard would send it back to the model.
        denial = re.sub(r"^(?:нет|нету)\s*,\s*", "", text, flags=re.IGNORECASE)
        if parse_enum_exact("debts.overdue_duration", denial) == "no_overdue":
            return [{"path": "debts.overdue_duration", "value": "no_overdue", "confidence": "high"}]
        # Вопрос звучит «есть ли просрочка и как давно», и «да, около
        # полугода» отвечает на обе его половины. Согласие перед сроком —
        # часть ответа, а не второй факт: запятая и лишнее слово иначе
        # отдавали реплику модели, и та сочиняла уточнение «просрочка
        # непрерывная или с перерывами?» (мутационный прогон 01.10.2026).
        # Голое «да» срока не называет и остаётся модели.
        answer = _YES_BEFORE_DURATION_RE.sub("", text, count=1).strip(" .!")
        if answer:
            text = answer

    if expected_paths[0] in MONEY_PATHS:
        # «80 тыс точно», «всего 150000» — число названо уверенно, слово
        # вокруг него ничего не меняет. Модель такую реплику на вопрос о
        # долге относила к нужной сумме, и не записывалось ничего (бой
        # 05.10.2026, сессия 5ad064af05cd — лид без суммы долга).
        text = _SURE_WORDS_RE.sub(" ", text)
        text = " ".join(text.split()).strip(" ,.:;—-")
        if not text:
            return None

    lowered = text.casefold()
    if any(marker in lowered for marker in _QUESTION_MARKERS):
        return None
    if any(marker in lowered for marker in _CORRECTION_MARKERS):
        return None
    # «Не знаю», «вроде нет», «наверное да» — это не ответ, а предположение.
    # Записать его значением нельзя: переписать потом будет некому.
    if any(marker in lowered for marker in _UNCERTAIN_MARKERS):
        return None
    if any(marker in lowered for marker in _CONVERSATIONAL_MARKERS):
        return None
    if "но" in {word.casefold() for word in _WORD_RE.findall(text)}:
        return None

    path = expected_paths[0]
    if path in _LOCATION_PATHS:
        location_tokens = {word.casefold() for word in _WORD_RE.findall(text)}
        if location_tokens & _LOCATION_AMBIGUITY_TOKENS:
            return None
    # Цифры там, где их не ждут, означают попутный факт: «да, на 2 млн» — это
    # ответ про наличие кредитов И сумма долга. Половину записать нельзя.
    # Срок просрочки — исключение: «3 дня», «от 1 до 3 месяцев» без цифр не
    # сказать. Что цифра относится к сроку, а не к сумме, ниже проверит сам
    # разбор срока: голое «1300000» он не примет.
    if (
        path not in _NUMERIC_PATHS
        and path != "debts.overdue_duration"
        and re.search(r"\d", text)
    ):
        return None

    if len(text.split()) > MAX_ANSWER_WORDS and not _is_spelled_out_address(path, text):
        return None

    if _COMMA_WORD_RE.search(text):
        tokens = {word.casefold() for word in _WORD_RE.findall(text)}
        simple_bool = _is_bool_field(path, matrix) and tokens <= (
            _NO_TOKENS | _YES_TOKENS
        )
        if path not in _LOCATION_PATHS and not simple_bool:
            return None

    if not _numeric_shape_matches(path, text, matrix):
        return None

    if path in _LOCATION_PATHS:
        if _carries_another_fact(path, text, matrix):
            return None
        # Пишем сырой текст явно, а не через fallback с проверкой заглавной
        # буквы: люди регулярно отвечают «москва»/«казань» со строчной. Ни
        # класс, ни маршрут здесь не угадываются — строку ещё проверит geo/.
        # Второй объект разбирается так же, как первый. Раньше его ответ
        # намеренно отдавали извлекателю, а тот на голое «Краснодар» не
        # возвращает ничего: вопрос повторялся, попытки сгорали, и лид уходил
        # без региона второго объекта (бой 05.10.2026, сессия a4dd48655572).
        return [
            {"path": _RAW_LOCATION_PATH[path], "value": text, "confidence": "high"}
        ]

    if _is_bool_field(path, matrix):
        # Только свой разбор, без запасного parse_bool. Тот ищет согласие
        # подстрокой, и в «конечно нет» находил «конечно» — твёрдый отказ
        # клиента записывался как согласие.
        value = _simple_bool(path, text)
    elif _is_enum_field(path, matrix):
        value = parse_enum_exact(path, text)
    else:
        value = normalize_fact(path, text, matrix)
    if value is None:
        return None

    if not _is_bool_field(path, matrix) and _enum_negates_detected_value(
        path, text, value
    ):
        return None

    if _carries_another_fact(path, text, matrix):
        return None

    return [{"path": path, "value": value, "confidence": "high"}]


# Целое от 1 до 999 включительно — типичный «голый» ответ денежного поля:
# клиент называет тысячи или миллионы как есть, а не сотнями рублей. Порог с
# запасом ниже минимальных сумм маршрутов (150 000 и выше), поэтому реальная
# сумма в рублях сюда случайно не попадает; 1000 и больше уже не трогаем —
# решение брифинга «Голое число в денежном вопросе», 20.09.2026.
_BARE_MONEY_MAX = 999
# У суммы кредита и долга подозрительны и четыре цифры. Прод 30.09.2026, лид
# 3508342: на «какая сумма нужна» клиентка ответила «1300», имея в виду 1,3
# миллиона, и в лид ушло 1300 рублей. Кому правда нужно 2500 рублей, на
# переспрос ответит «рублей» — один лишний вопрос дешевле неверной суммы в
# CRM. Доход четырьмя цифрами миллионами не бывает, там порог прежний.
_BARE_MONEY_MAX_BY_PATH = {"request.amount": 9999, "debts.total": 9999}


def bare_money_update(
    update: dict[str, Any], raw_text: str, matrix: Matrix | None
) -> int | None:
    """Голое число денежного поля без единицы измерения — сигнал переспросить.

    Прод 20.09.2026, лид 3497115: на вопрос про долг клиент ответил «400 общая
    сумма», бот записал 400 рублей — для долга это бессмыслица. Домножать на
    тысячу за клиента нельзя, а поверить голому числу значит соврать в лид,
    поэтому такое значение не должно уйти в факты (см.
    `core._split_bare_money_updates`, который вызывает эту функцию и для
    быстрого разбора, и для ответа модели-извлекателя — единая проверка).

    Ноль не подозрителен: «долгов нет» отвечают именно нулём. Значение с
    любой явной единицей — «400 рублей», «400 тысяч», «400к», «400 ₽» —
    тоже не подозрительно: клиент уже сказал, что имел в виду.
    """
    path = update.get("path")
    if path not in MONEY_PATHS:
        return None
    value = normalize_fact(path, update.get("value"), matrix)
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    if (doubled := _double_unit_raw(value, raw_text)) is not None:
        return doubled
    if not 1 <= value <= _BARE_MONEY_MAX_BY_PATH.get(path, _BARE_MONEY_MAX):
        return None
    if _unit_is_named_for(value, raw_text):
        return None
    return value


# Тысячи, названные дважды: «110000 тыс руб» буквально — 110 миллионов. Бой
# 19.10.2026, сессия e0837cd8a8f9: так и записалось, хотя человек явно имел в
# виду 110 тысяч. От десяти миллионов такое значение переспрашивается тем же
# выбором, что и четыре цифры: «110 тысяч или 110 миллионов?».
_DOUBLE_UNIT_FLOOR = 10_000_000
_DOUBLE_UNIT_RE = re.compile(
    r"(?<![\d\s])(\d{1,3}(?:\s\d{3})+|\d{4,})\s*(?:тыс\w*|т\.?\s?р\.?|тр\b|т\b)",
    re.IGNORECASE,
)


def _double_unit_raw(value: int, text: str) -> int | None:
    """Число цифрами с «тыс» после него, давшее заведомо лишние тысячи."""
    if value < _DOUBLE_UNIT_FLOOR:
        return None
    for match in _DOUBLE_UNIT_RE.finditer(text or ""):
        digits = int(re.sub(r"\s", "", match.group(1)))
        if digits >= UNITS_CHOICE_FROM and digits * 1000 == value:
            return digits
    return None


_UNIT_NEIGHBOUR_CHARS = 16
_CLAUSE_BREAK_RE = re.compile(r"[,.;!?\n]")


def _unit_is_named_for(value: int, text: str) -> bool:
    """Названа ли единица именно у этого числа.

    Мутационный прогон 05.10.2026: «доход 58 тысяч, нужна сумма 150» — «тысяч»
    относилось к доходу, а сумма 150 записалась рублями: единицу искали во
    всей реплике. Теперь смотрим слово сразу после числа и сразу перед ним
    («тысяч 500»), в пределах той же части фразы. Числа в реплике цифрами нет
    («сто пятьдесят») — судим по реплике целиком, как раньше.
    """
    places = list(re.finditer(rf"(?<!\d){value}(?!\d)", text))
    if not places:
        return _has_money_unit_marker(text)
    for place in places:
        after = _CLAUSE_BREAK_RE.split(text[place.end() : place.end() + _UNIT_NEIGHBOUR_CHARS])[0]
        before = _CLAUSE_BREAK_RE.split(
            text[max(0, place.start() - _UNIT_NEIGHBOUR_CHARS) : place.start()]
        )[-1]
        next_word = _WORD_RE.search(after)
        previous_words = _WORD_RE.findall(before)
        if "₽" in after[:3]:
            return True
        if next_word and _has_money_unit_marker(next_word.group()):
            return True
        if previous_words and _has_money_unit_marker(previous_words[-1]):
            return True
    return False


# Ответ на переспрос «400 тысяч?» — свой маленький словарь согласия и отказа:
# выбор здесь не «да/нет» по существу вопроса, а «домножить» или «оставить как
# есть», поэтому общий `_simple_bool` не подходит.
_UNITS_CONFIRM_MULTIPLY = frozenset({"да", "ага", "тыс", "тысяч", "верно", "именно"})
# «Нет, только 500 рублей» (бой 18.10.2026, сессия 4ad270d4ba4f) — тоже «как
# есть»: слова-уточнения ответа не меняют.
_UNITS_CONFIRM_AS_IS = frozenset(
    {"рублей", "рубля", "рубль", "руб", "нет", "только", "просто", "ровно"}
)

# С четырёх цифр переспрос — выбор из двух: «5 тысяч или 5 миллионов?». На
# странице отказа по кредиту просят и 5000 рублей, и «1300» в смысле 1,3 млн
# (прод 30.09.2026, лид 3508342; разбор 04.10.2026), угадывать за клиента
# нельзя ни в одну сторону. «Да» и «нет» на такой вопрос ничего не выбирают.
UNITS_CHOICE_FROM = 1000
_UNITS_CHOICE_MILLIONS = frozenset(
    {"млн", "миллион", "миллиона", "миллионов", "лям", "ляма", "лямов", "второе"}
)
_UNITS_CHOICE_RUBLES = frozenset(
    {"рублей", "рубля", "рубль", "руб", "тыс", "тысяч", "тысячи", "тысяча", "первое"}
)
_UNITS_CHOICE_FILLERS = frozenset({"да", "ага", "нет", "верно", "именно", "конечно"})


def parse_units_confirmation(raw: int, message: str) -> int | None:
    """Разбирает ответ на переспрос голого числа денежного поля.

    Возвращает готовое значение факта либо None — «это не прямой ответ на
    переспрос, пусть решает обычный разбор реплики» (клиент назвал другое
    число, единицу измерения или что угодно ещё). До тысячи переспрос звучит
    «400 тысяч?»: согласие — ``raw * 1000``, отказ — ``raw``. С тысячи это
    выбор «5 тысяч или 5 миллионов?»: ответ называет единицу, а «да» и «нет»
    ничего не значат. Цифра в ответе всегда означает новое число, а не
    подтверждение старого, — иначе «500 тысяч» на «400 тысяч?» тоже стало бы
    согласием и записало 400 000 вместо названных 500 000.

    Пометку `script.units_pending` снимает вызывающий (`core.prepare_turn`) в
    любом случае, независимо от результата: второй переспрос подряд запрещён.
    """
    text = " ".join((message or "").split())
    if not text or len(text) > MAX_ANSWER_CHARS:
        return None
    # То же число рядом с ответом — не новое значение: «Да, 400», «1300
    # рублей». Любое другое число разбирается обычным порядком.
    numbers = re.findall(r"\d+", text)
    if numbers and numbers != [str(raw)]:
        return None
    tokens = {word.casefold() for word in _WORD_RE.findall(text) if not word.isdigit()}
    if not tokens:
        return None
    if raw >= UNITS_CHOICE_FROM:
        chosen = tokens - _UNITS_CHOICE_FILLERS
        if not chosen:
            return None
        if chosen <= _UNITS_CHOICE_MILLIONS:
            return raw * 1000
        if chosen <= _UNITS_CHOICE_RUBLES:
            return raw
        return None
    if tokens <= _UNITS_CONFIRM_MULTIPLY:
        return raw * 1000
    if tokens <= _UNITS_CONFIRM_AS_IS:
        return raw
    return None


def units_choice_unanswered(raw: int, message: str) -> bool:
    """На «5 тысяч или 5 миллионов?» не выбрали: «да», «5000», «5000, да».

    Другое число в ответе — уже новое значение, его разберёт обычный порядок.
    Любые слова сверх «да» и «нет» — тоже: «тысяч, конечно, пять тысяч рублей»
    выбор сделан, просто своими словами, и обычный разбор его понимает
    (мутационный прогон 04.10.2026 — такой ответ сочли пустым, сумма не
    записалась, и бот спросил её ещё дважды).
    """
    if raw < UNITS_CHOICE_FROM or parse_units_confirmation(raw, message) is not None:
        return False
    if re.findall(r"\d+", message or "") not in ([], [str(raw)]):
        return False
    words = {
        word.casefold() for word in _WORD_RE.findall(message or "") if not word.isdigit()
    }
    return words <= _UNITS_CHOICE_FILLERS | {"ну"}


def _carries_another_fact(path: str, text: str, matrix: Matrix | None) -> bool:
    """Опознаётся ли ответ как значение какого-то другого поля.

    «Есть квартира в Москве» — ответ на вопрос о наличии недвижимости, но в нём
    же тип объекта и город. Записать одну треть и переспросить остальное хуже,
    чем потратить вызов модели: человек уже всё сказал.
    """
    # «42 года, Москва» не похоже ни на один enum из _GREEDY_PATHS, но всё
    # равно содержит второй факт. Собственное имя после первого ответа —
    # консервативный сигнал отдать всю реплику модели. Это заодно покрывает
    # «2020 Toyota» и другие короткие составные ответы.
    if path not in _LOCATION_PATHS and _EXTRA_PROPER_NOUN_RE.search(text):
        return True
    if path not in _LOCATION_PATHS and classify_region_confident(text, matrix) is not None:
        return True
    tokens = {word.casefold() for word in _WORD_RE.findall(text)}
    if path != "assets.realty.owners" and tokens & _OWNER_TOKENS:
        return True
    greedy_paths = _GREEDY_PATHS
    if path == "client.age":
        # «42 года» — тот же вопрос, разбор срока словами (22.09.2026) стал
        # понимать голое «<число> года/лет» и как годы жизни, и как срок
        # просрочки: единица измерения у них совпадает дословно. Анкета их
        # никогда не путает — это два разных вопроса в разных ходах, — так
        # что здесь это ложное срабатывание, а не второй факт в ответе.
        greedy_paths = tuple(p for p in _GREEDY_PATHS if p != "debts.overdue_duration")
    return any(
        other != path and normalize_fact(other, text, matrix) is not None
        for other in greedy_paths
    )


def _numeric_shape_matches(
    path: str,
    text: str,
    matrix: Matrix | None,
) -> bool:
    """Не позволяет одной и той же цифре стать возрастом, суммой или годом.

    Нормализаторы намеренно терпимы к свободному тексту, потому что получают
    уже извлечённое моделью значение. Fast path видит сырую реплику, поэтому
    обязан проверить лексическое окружение числа до вызова normalize_fact.
    """
    if path not in _NUMERIC_PATHS or path == "client.phone" or not re.search(r"\d", text):
        return True
    if path == "client.age" and parse_birthdate(text) is not None:
        return True
    # Телефон и дата сначала извлекаются локальным PII-контуром. Тот же набор
    # цифр не должен параллельно становиться суммой, доходом или годом.
    if parse_phone(text) is not None or parse_birthdate(text) is not None:
        return False
    if "/" in text:
        return False

    digit_groups = re.findall(r"\d+", text)
    if path in {"client.age", "assets.vehicle.year"} and len(digit_groups) != 1:
        return False
    if path in {"request.amount", "debts.total", "employment.income"} and len(digit_groups) > 1:
        numeric_part = re.sub(r"[^\d\s\u00a0.,]", "", text).strip()
        if not (
            _MONEY_GROUPED_RE.fullmatch(numeric_part)
            or _MONEY_DECIMAL_RE.fullmatch(numeric_part)
        ):
            return False
    words = {word.casefold() for word in _WORD_RE.findall(text)}
    if path in {"request.amount", "debts.total", "employment.income"}:
        parsed = normalize_fact(path, text, matrix)
        # Голое «150» в кредитном контексте почти всегда означает тысячи, но
        # код прочитает 150 рублей. Без единицы измерения не гадаем.
        if (
            isinstance(parsed, int)
            and parsed < 10_000
            and not words & _MONEY_WORDS
        ):
            return False
    allowed = {
        "client.age": _AGE_WORDS,
        "request.amount": _MONEY_WORDS,
        "debts.total": _MONEY_WORDS,
        "employment.income": _MONEY_WORDS,
        "assets.vehicle.year": _YEAR_WORDS,
    }.get(path)
    return allowed is None or words <= allowed


# Виды полей, у которых ответ либо распознаётся однозначно, либо не
# распознаётся вовсе. Свободный текст сюда не входит: у него нет неверного
# значения, любая строка «подходит». На вопрос об имени пришло «Тестик меня
# величать» — и именем стала вся фраза целиком.
_PARSEABLE_KINDS = frozenset({"bool", "enum", "int", "money"})


def _is_parseable_field(path: str, matrix: Matrix | None) -> bool:
    # Телефон — исключение среди текстовых: у него есть строгий формат, и
    # `parse_phone` отвергает всё, что на номер не похоже.
    if path == "client.phone":
        return True
    spec = (matrix.fields if matrix else {}).get(path)
    return bool(spec and spec.kind in _PARSEABLE_KINDS)


def _is_bool_field(path: str, matrix: Matrix | None) -> bool:
    spec = (matrix.fields if matrix else {}).get(path)
    return bool(spec and spec.kind == "bool")


def _is_enum_field(path: str, matrix: Matrix | None) -> bool:
    spec = matrix.fields.get(path) if matrix else None
    return bool(spec and spec.kind == "enum")


def _simple_bool(path: str, text: str) -> bool | None:
    """Согласие или отказ, сказанные в любом порядке слов.

    Возвращает None, если в реплике есть и то и другое: «да нет наверное»
    разбирать без модели нельзя.
    """
    words = [word.casefold() for word in _WORD_RE.findall(text)]
    phrase = " ".join(words)
    mapped = _PATH_BOOL_VALUES.get(path, {}).get(phrase)
    if mapped is not None:
        return mapped
    if phrase not in _DIRECT_BOOL_PHRASES and phrase not in _PATH_BOOL_PHRASES.get(
        path, frozenset()
    ):
        return None
    tokens = set(words)
    says_no = bool(tokens & _NO_TOKENS)
    says_yes = bool(tokens & _YES_TOKENS)
    if says_no == says_yes:
        return None
    return not says_no


def _enum_negates_detected_value(path: str, text: str, value: Any) -> bool:
    """Не принимает enum, найденный внутри его отрицания."""
    tokens = {word.casefold() for word in _WORD_RE.findall(text)}
    if not tokens & _NEGATION_TOKENS:
        return False
    # Эти отрицательные формулировки сами являются значениями справочника.
    if path == "employment.type" and value == "unemployed":
        return False
    if path in {"debts.credit_history", "debts.overdue_duration"} and value == "no_overdue":
        return False
    return True
