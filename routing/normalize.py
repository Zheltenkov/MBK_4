"""Нормализация свободного ответа клиента в значения, понятные матрице.

Извлекатель отдаёт то, что сказал человек («полтора миллиона», «квартира», «в Химках»).
Матрица оперирует числами и кодами. Этот слой — граница между ними, и он намеренно
консервативен: что не распозналось однозначно, становится None, а не догадкой.
Догадка здесь дороже лишнего вопроса — она уводит клиента в чужой маршрут.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterable

from .ru_dates import (
    age_from_birthdate,
    mentions_someone_else,
    parse_birth_year,
    parse_birthdate,
    parse_spoken_number,
)
from .schema import Matrix


# --------------------------------------------------------------------------- #
# Числа
# --------------------------------------------------------------------------- #
# Numeric conversion and precision are independent. Debt input and spoken
# collection acts share these qualifiers without changing legacy parse_amount.
MONEY_APPROXIMATION = re.compile(
    r"\b(?:примерн\w*|около|приблизительн\w*|ориентировочн\w*)|\bгде[-‐‑]то(?=\s*\d)|"
    # Choosing a placeholder is not reporting a known balance. Bind the choice
    # to its number so ordinary phrases about names or locations stay unrelated.
    r"\b(?:пусть\s+(?:(?:общая\s+)?сумма\s+)?будет|будем\s+считать|"
    r"возьм[её]м|поставим|запишем)\s*"
    r"(?:(?:общую\s+)?сумму(?:\s+долга)?\s+|долг\s+)?(?:(?:ровно|точно)\s+)?(?=\d)", re.I,
)
# Множитель ищется только сразу после числа и только как отдельное слово: иначе
# «200000 Курск» превращается в 200 миллионов, потому что «к» — тоже множитель.
_MULTIPLIER_RE = re.compile(
    r"^\s*(?:"
    r"(?P<milliard>млрд|миллиард\w*)"
    r"|(?P<million>млн|миллион\w*|лям\w*)"
    r"|(?P<thousand>тыс\w*|тысяч\w*|косар\w*|к)"
    r")\b"
)

_MULTIPLIER_FACTORS = {"milliard": 1_000_000_000, "million": 1_000_000, "thousand": 1_000}
# Три написания одного номера. Ветка без префикса обязательна: живой клиент
# 21.08.2026 написал десять цифр без «8», бот его не понял и переспросил номер,
# который уже был на экране.
_PHONE_RE = re.compile(
    r"(?<!\d)(?:"
    r"(?:\+7|8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}"
    r"|7[\s\-()]*9\d{2}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}"
    r"|9\d{2}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}"
    r")(?!\d)"
)


def parse_amount(value: Any) -> int | None:
    """«1 500 000», «1,5 млн», «150к», «300 тыс руб» → int. Непонятное → None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value >= 0 else None
    if not isinstance(value, str):
        return None

    text = value.strip().lower().replace(" ", " ")
    if not text:
        return None

    match = re.search(r"\d[\d\s.,]*", text)
    if not match:
        return None
    raw_number = match.group(0)
    tail = text[match.end():]

    # «1 500 000» — пробелы разделяют разряды; «1,5» / «1.5» — дробная часть.
    cleaned = raw_number.replace(" ", "").replace(",", ".")
    if cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")
    try:
        number = float(cleaned)
    except ValueError:
        return None

    if match_mult := _MULTIPLIER_RE.match(tail):
        group = next(name for name, value in match_mult.groupdict().items() if value)
        number *= _MULTIPLIER_FACTORS[group]

    return int(round(number)) if number >= 0 else None


def parse_phone(value: Any) -> str | None:
    """Российский номер → +7XXXXXXXXXX; произвольный текст телефоном не считается."""
    if not isinstance(value, str):
        return None
    match = _PHONE_RE.search(value)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group())
    if len(digits) == 10 and digits[0] == "9":
        return "+7" + digits
    if len(digits) != 11 or digits[0] not in {"7", "8"}:
        return None
    return "+7" + digits[1:]


# Возраст, привязанный к самому клиенту: «мне 40», «я 40», «мой возраст 40».
_OWN_AGE_RE = re.compile(
    r"\b(?:мне|мой\s+возраст|я)\b\D{0,12}?(\d{1,3})(?!\d)", re.IGNORECASE
)


def parse_age(value: Any, today: date | None = None) -> int | None:
    r"""Полные годы из ответа клиента, как бы он их ни назвал.

    Порядок разбора важнее самих правил, потому что ответ бывает
    противоречивым, и решает то, что проверяется первым.

    1. Полная дата рождения. Раньше первым шёл поиск `\b\d{1,3}\b`, то есть
       первое число строки, и «27 марта 1985» давало двадцать семь лет вместо
       сорока одного — молча и с высокой уверенностью.
    2. Голое одно-трёхзначное число. Оно сильнее четырёхзначного года:
       «мне 46, работаю с 1998 года» — это сорок шесть лет, а не двадцать
       восемь. Посторонний год в ответе иначе тихо переписывал возраст.
    3. Год рождения без дня и месяца: «родился в 1985», «в восемьдесят пятом».
    4. Числительные словами: «сорок шесть».

    Дата, которая относится к другому человеку, не читается вовсе: «жена
    родилась 5 мая 1990» — это не возраст клиента. Голое число при этом
    остаётся в силе, потому что «мне 46, а жене 40» начинается со своего.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        age = int(value)
        return age if 0 < age < 120 else None
    if not isinstance(value, str):
        return None

    text = value.strip()
    current = today or date.today()
    about_someone_else = mentions_someone_else(text)

    if not about_someone_else:
        born = _parse_date(text) or parse_birthdate(text, current)
        if born is not None:
            age = age_from_birthdate(born, current)
            return age if 0 < age < 120 else None

    if about_someone_else and (
        _parse_date(text)
        or parse_birthdate(text, current)
        or parse_birth_year(text, current)
    ):
        # Число здесь — часть чужой даты: «жена родилась 5 мая 1990» иначе
        # даёт клиенту пять лет. Свой возраст рядом с чужой датой не назван.
        return None

    if about_someone_else:
        # Рядом с чужим возрастом «первое число строки» — уже не правило:
        # «сыну 12, мне 40» начинается с сына. Берём только число, привязанное
        # к самому клиенту, а без него переспрашиваем.
        own = _OWN_AGE_RE.search(text)
        if own and 0 < int(own.group(1)) < 120:
            return int(own.group(1))
        return None

    bare = re.search(r"(?<!\d)(\d{1,3})(?!\d)", text)
    if bare and 0 < int(bare.group(1)) < 120:
        return int(bare.group(1))

    year = parse_birth_year(text, current)
    if year is not None:
        # Без дня и месяца точный возраст неизвестен на единицу — берём
        # прожитые полные годы по концу года, как считает сам человек.
        age = current.year - year
        return age if 0 < age < 120 else None

    # Цифр в ответе нет вовсе — значит число названо словами.
    spoken = parse_spoken_number(text)
    if spoken is None:
        return None
    return spoken if 0 < spoken < 120 else None


def normalize_birthdate(value: Any, today: date | None = None) -> str | None:
    """Дата рождения в ISO. Всё, что не разобралось в дату, отбрасывается.

    Поле необязательное: в чате его никто не спрашивает, оно приходит либо с
    формы сайта, либо когда клиент сам назвал дату в ответе про возраст.
    Поэтому лучше пусто, чем выдумка, — пустую графу видно, а выдуманную
    дату от настоящей уже не отличить.
    """
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        return None
    current = today or date.today()
    born = _parse_date(value.strip()) or parse_birthdate(value, current)
    return born.isoformat() if born else None


def _parse_date(text: str) -> date | None:
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        year, month, day = (int(part) for part in iso.groups())
    else:
        ru = re.fullmatch(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})", text)
        if not ru:
            return None
        day, month, year = (int(part) for part in ru.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Регионы
# --------------------------------------------------------------------------- #
# Осознанное ограничение: распознаём только явное упоминание Москвы/области.
# «Химки» сами по себе в moscow_mo не попадут — для этого нужен справочник ФИАС
# (ДаData), он в задаче отдельным пунктом. Лучше переспросить, чем угадать регион.
_MOSCOW_MARKERS = ("москв", "московская область", "подмосков")
# «мск» поддерживаем только как отдельное слово. Поиск обычной подстрокой ошибочно
# относил Томск и Омск к Москве.
_MOSCOW_ABBREVIATION_RE = re.compile(r"(?<!\w)мск(?!\w)", re.IGNORECASE)
_UNKNOWN_REGION_MARKERS = (
    "не знаю",
    "не помню",
    "не скажу",
    "не хочу говорить",
    "без разницы",
)


# Субъект, названный клиентом прямо. Это доказательство: догадываться не о чем,
# даже если сам населённый пункт не разобран. Москву ловят маркеры выше.
_NAMED_SUBJECT_RE = re.compile(
    r"\b(?:обл(?:асть)?|респ(?:ублика)?|край|округ|санкт[- ]петербург)\b",
    re.IGNORECASE,
)

# Типы населённых пунктов, которые заведомо не город. Нужны офлайн-разбору: лист
# «МБК» задан только названиями, поэтому «деревня Самара» без DaData отличима от
# города Самара исключительно по типу, названному клиентом.
_NON_CITY_TYPES = (
    "деревня", "дер", "д",
    "село", "с",
    "поселок", "пос", "п", "пгт", "рп", "дп",
    "хутор", "х",
    "аул", "станица", "ст",
    "снт", "тер",
)


def classify_region(value: Any, matrix: Matrix | None = None) -> str | None:
    """Строка адреса → класс региона матрицы.

    Классы: `moscow_mo`, `mbk_list`, `realty_list` (перечни из матрицы) и `rf_other`.
    Нераспознанное становится `rf_other` — осознанное умолчание для путей, где
    догадку ещё исправит DaData или уточняющий вопрос. Там, где исправлять некому,
    нужен `classify_region_confident`: он умеет ответить «не знаю».
    """
    if not isinstance(value, str) or not value.strip():
        return None

    # Отказ или отсутствие ответа — это UNKNOWN, а не «вся остальная РФ».
    if any(marker in value.strip().lower() for marker in _UNKNOWN_REGION_MARKERS):
        return None

    confident = classify_region_confident(value, matrix, include_realty_list=True)
    return confident if confident is not None else "rf_other"


def classify_region_confident(
    value: Any,
    matrix: Matrix | None = None,
    *,
    include_realty_list: bool = False,
) -> str | None:
    """Класс региона, подтверждённый самим текстом. `None` — честное «не знаю».

    Отличие от `classify_region` ровно одно: здесь нет умолчания. Класс
    возвращается только при доказательстве — Москва названа прямо, населённый
    пункт найден в справочниках `routing/data/*.csv` либо клиент сам назвал
    субъект. Всё остальное — «не знаю»: умолчание, которое некому исправить,
    уводит клиента в чужой маршрут, а это дороже лишнего вопроса.

    `include_realty_list`: попадание в списки — характеристика объекта
    недвижимости, месту жительства клиента оно говорит лишь «это не Москва».
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().lower()

    if any(marker in text for marker in _UNKNOWN_REGION_MARKERS):
        return None

    if any(marker in text for marker in _MOSCOW_MARKERS) or _MOSCOW_ABBREVIATION_RE.search(text):
        return "moscow_mo"

    if matrix is not None:
        # Порядок повторяет приоритет маршрутов: список МБК (1-2) раньше
        # списка Кредит.Клаб (1-3). Город может присутствовать в обоих.
        for list_name in ("mbk_list", "realty_list"):
            if text_matches_location_list(value, matrix.regions.get(list_name, [])):
                return list_name if include_realty_list else "rf_other"
        if _names_known_subject(value, matrix):
            return "rf_other"

    if _NAMED_SUBJECT_RE.search(text):
        return "rf_other"

    return None


def _names_known_subject(value: str, matrix: Matrix) -> bool:
    """Клиент назвал субъект своим именем: «Татарстан», «Адыгея», «Алтайский».

    Отдельный перечень субъектов заводить не нужно: колонка региона тех же
    справочников — уже готовый список. Слова «республика» в реплике может не быть,
    а субъект при этом назван однозначно.
    """
    text = region_key(value)
    for entries in matrix.regions.values():
        for entry in entries:
            region = entry.split("\t", 1)[0] if "\t" in entry else entry
            if region and contains_word(text, region_key(region)):
                return True
    return False


def text_matches_location_list(value: str, configured: Iterable[str]) -> bool:
    """Сопоставляет свободный текст адреса со справочником без вызова DaData.

    Справочники лежат в репозитории парами «регион<TAB>город», поэтому сам факт
    попадания в список проверяется офлайн. DaData нужна для канонизации и разбора
    одноимённых городов, но не для принадлежности к списку.
    """
    # «им.» раскрывается так же, как в locality_key, иначе «ГП им. Свердлова»
    # из справочника не совпадёт с тем же названием в тексте клиента.
    normalized = re.sub(r"\bим\b", "имени", normalized_text(value))
    region_text = region_key(value)

    for entry in configured:
        if "\t" not in entry:
            # Обратная совместимость с прежним region-only форматом.
            if contains_word(region_text, region_key(entry)):
                return True
            continue
        region, locality = entry.split("\t", 1)
        key = locality_key(locality)
        if not contains_word(normalized, key):
            continue
        if region:
            if contains_word(region_text, region_key(region)):
                return True
            continue
        # Пустой регион — формат листа «МБК»: там заданы только города, поэтому
        # одноимённая деревня или село прошли бы по одному названию. Без DaData
        # отличить их можно лишь по типу, который клиент назвал сам.
        if not _mentioned_as_non_city(normalized, key):
            return True
    return False


def _mentioned_as_non_city(text: str, locality: str) -> bool:
    """«деревня Самара» — это не город Самара из списка МБК."""
    types = "|".join(_NON_CITY_TYPES)
    pattern = rf"(?<!\w)(?:{types})\s+{re.escape(locality)}(?!\w)"
    return re.search(pattern, text) is not None


def contains_word(haystack: str, needle: str) -> bool:
    """Сравнение по границам слова: «Тульская область» — это не город Тула."""
    if not needle or not haystack:
        return False
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


def normalized_text(value: str) -> str:
    return re.sub(r"[^а-яa-z0-9]+", " ", value.lower().replace("ё", "е")).strip()


def region_key(value: str) -> str:
    """Сводит «Тверская область» и DaData `Тверская обл` к одному ключу."""
    value = re.sub(r"\([^)]*\)", " ", value)
    normalized = normalized_text(value)
    normalized = normalized.replace("калиниградская", "калининградская")
    normalized = normalized.replace("умуртская", "удмуртская")
    normalized = re.sub(
        r"\b(?:обл(?:асть)?|респ(?:ублика)?|край|автономный|автономная|округ|ао|г|город)\b",
        " ",
        normalized,
    )
    return " ".join(normalized.split())


def locality_key(value: str) -> str:
    normalized = normalized_text(value)
    normalized = re.sub(
        r"^(?:городской округ|гп|рп|пгт|дп|г|с|п|пос|дер|д|аул|тер)\s+",
        "",
        normalized,
    )
    normalized = re.sub(r"\bим\b", "имени", normalized)
    return " ".join(normalized.split())


# Классы, которые существуют только для объекта недвижимости: место жительства
# клиента матрица делит лишь на Москву/МО и остальную РФ.
_REALTY_ONLY_REGION_CLASSES = frozenset({"mbk_list", "realty_list"})


# --------------------------------------------------------------------------- #
# Перечислимые значения
# --------------------------------------------------------------------------- #
# Ключ — подстрока в ответе клиента, значение — код матрицы. Порядок важен:
# первое совпадение выигрывает, поэтому длинные и однозначные варианты идут выше.
_REALTY_KIND = [
    # Комната и доля идут ПЕРЕД «квартир» намеренно: «комнату в коммунальной
    # квартире» и «доля в квартире» ловились подстрокой «квартир» и
    # записывались как целая квартира. Клиент с комнатой уходил на залоговый
    # маршрут, который комнаты не принимает, а в CRM ехал realty_type
    # «квартира» — то есть ложные данные о его имуществе.
    ("комнат", "room"),
    ("доля", "other"),
    ("доли", "other"),
    ("апартамент", "apartments"),
    ("квартир", "flat"),
    ("коммерч", "commercial"),
    ("нежил", "commercial"),
    ("дом", "house"),
    ("таунхаус", "house"),
    ("участ", "other"),
    ("земл", "other"),
]

_OWNERSHIP_FORM = [
    ("частн", "private"),
    ("собственност", "private"),
    ("муниципал", "municipal"),
    ("неприватизир", "municipal"),
    ("социальн", "municipal"),
]

_OWNERS = [
    ("единолич", "client_only"),
    ("только я", "client_only"),
    ("один собственник", "client_only"),
    ("супруг", "client_and_spouse"),
    ("жена", "client_and_spouse"),
    ("муж", "client_and_spouse"),
    ("родител", "others"),
    ("дол", "others"),
    ("родствен", "others"),
]

_REALTY_RESTRICTION = [
    ("без обремен", "none"),
    ("нет обремен", "none"),
    ("ипотек", "mortgage"),
    ("в залоге", "pledge"),
    ("под залог", "pledge"),
    ("арест", "arrest"),
]

_CREDIT_HISTORY = [
    ("нет просроч", "no_overdue"),
    ("без просроч", "no_overdue"),
    ("были просроч", "past_overdue"),
    ("есть просроч", "has_overdue"),
    ("готов оформить банкрот", "ready_bankruptcy"),
    ("готов к банкрот", "ready_bankruptcy"),
]

_OVERDUE_DURATION = [
    ("нет просроч", "no_overdue"),
    ("без просроч", "no_overdue"),
    ("до 1 месяц", "up_to_1m"),
    ("до месяц", "up_to_1m"),
    ("от 1 до 3", "from_1m_to_3m"),
    ("от 3 до 6", "from_3m_to_6m"),
    ("1-3", "from_1m_to_3m"),
    ("1–3", "from_1m_to_3m"),
    ("3-6", "from_3m_to_6m"),
    ("3–6", "from_3m_to_6m"),
    ("больше 6", "over_6m"),
    ("более 6", "over_6m"),
    ("свыше 6", "over_6m"),
]

# --------------------------------------------------------------------------- #
# Срок просрочки живым языком
# --------------------------------------------------------------------------- #
# Прод 22.09.2026, лид 3498907: бот спросил сумму долга и срок просрочки одной
# репликой, клиент ответил «Пока три дня». Таблица маркеров выше знает только
# формулировки самой анкеты («до месяца», «от 1 до 3») — живой ответ в неё не
# попал, факт не записался, и бот сам сочинил вопрос, которого нет ни в
# анкете, ни в инструкциях.
#
# Числительные словами разбирает `parse_spoken_number` (routing/ru_dates.py) —
# свой словарь здесь не заводим. Своя пара — только «полтора»/«пара»: их в
# том словаре нет, а без них «полтора месяца» и «пару недель» не разобрать.
_DURATION_TOKEN_RE = re.compile(r"[а-яё]+|\d+", re.IGNORECASE)

# Дни на единицу — с запасом ниже погрешности точность здесь не нужна: пороги
# корзин (30/90/180 дней) далеко от границ календарного месяца и года.
_DURATION_DAYS_PER_UNIT = {"day": 1, "week": 7, "month": 30, "year": 365}

# Основа слова → единица. Основа «лет» — неправильное множественное число
# «года» после чисел вроде «7 лет», отдельной строкой, потому что «лет» не
# начинается на «год».
_DURATION_UNIT_STEMS: list[tuple[str, str]] = [
    ("недел", "week"),
    ("месяц", "month"),
    ("год", "year"),
    ("лет", "year"),
]
_DURATION_DAY_WORDS = frozenset({"день", "дня", "дней", "дни"})
# Слитное «полгода» и его родительный падеж «полугода» («меньше полугода») —
# основа «год» их не ловит, у обоих слов впереди «пол-».
_DURATION_HALF_YEAR_WORDS = frozenset({"полгода", "полугода"})
_DURATION_PAIR_WORDS = frozenset({"пара", "пару", "пары"})
_DURATION_HALF_WORDS = frozenset({"полтора", "полторы"})

# Слова вокруг срока, которые не меняют корзину и не считаются «вторым
# фактом»: клиент уточняет или оговаривается про сам срок, а не называет
# что-то ещё. Слов согласия («да», «есть») тут намеренно нет: рядом со сроком
# они читаются двусмысленно, и решать такой ответ должна модель.
_DURATION_FILLER_WORDS = frozenset(
    {
        "пока", "уже", "около", "почти", "примерно", "приблизительно",
        "меньше", "менее", "всего", "чуть",
        "длится", "тянется", "составляет", "срок",
        "просрочка", "просрочки", "просрочен", "просрочена", "просрочено",
    }
)

# «Больше полугода» — строго дольше границы: без этого срок, названный ровно
# на пороге корзины (30/90/180 дней), оставался в корзине ниже, и «больше
# полугода» читалось как «от 3 до 6 месяцев». «Меньше» и «почти» границу не
# переходят, поэтому остались в словах-заполнителях.
_DURATION_LONGER_WORDS = frozenset({"больше", "более", "свыше", "дольше", "лишним"})
_DURATION_LONGER_PHRASE_RE = re.compile(r"\bс\s+лишним\b")


def _duration_unit(token: str) -> str | None:
    if token in _DURATION_DAY_WORDS:
        return "day"
    for stem, unit in _DURATION_UNIT_STEMS:
        if token.startswith(stem):
            return unit
    return None


def parse_overdue_duration_words(value: Any) -> str | None:
    """«три дня», «полгода», «7 месяцев» → корзина `debts.overdue_duration`.

    Понимает дни/недели/месяцы/годы, число цифрами или словами. Голое число
    без единицы («три») не трогаем — единица может быть любой, а гадать
    дороже переспроса. Если рядом со сроком осталось непонятое слово или
    отдельная цифра (например сумма долга в той же реплике), это второй
    факт — отдаём его модели, а не режем реплику пополам.
    """
    if not isinstance(value, str):
        return None
    text = _DURATION_LONGER_PHRASE_RE.sub(" лишним ", value.strip().lower())
    if not text.strip():
        return None
    tokens = _DURATION_TOKEN_RE.findall(text)
    if not tokens:
        return None

    unit_index: int | None = None
    unit: str | None = None
    count: float | None = None

    for index, token in enumerate(tokens):
        if token in _DURATION_HALF_YEAR_WORDS:
            # «Полгода» считаем шестью месяцами, а не половиной года из 365
            # дней: иначе «полгода» (182,5 дня) перескакивало бы через
            # порог в 180 дней в чужую корзину.
            unit_index, unit, count = index, "month", 6.0
            break
        candidate = _duration_unit(token)
        if candidate is not None:
            unit_index, unit = index, candidate
            break

    if unit is None or unit_index is None:
        return None

    consumed = {unit_index}
    if count is None:
        count = 1.0
        if unit_index > 0:
            prior = tokens[unit_index - 1]
            if prior.isdigit():
                count = float(prior)
                consumed.add(unit_index - 1)
            elif prior in _DURATION_PAIR_WORDS:
                count = 2.0
                consumed.add(unit_index - 1)
            elif prior in _DURATION_HALF_WORDS:
                count = 1.5
                consumed.add(unit_index - 1)
            else:
                spoken = parse_spoken_number(prior)
                if spoken is not None:
                    count = float(spoken)
                    consumed.add(unit_index - 1)

    longer = any(
        index not in consumed and token in _DURATION_LONGER_WORDS
        for index, token in enumerate(tokens)
    )
    leftover = [
        token
        for index, token in enumerate(tokens)
        if index not in consumed
        and token not in _DURATION_FILLER_WORDS
        and token not in _DURATION_LONGER_WORDS
    ]
    if leftover:
        return None

    days = count * _DURATION_DAYS_PER_UNIT[unit]
    if longer:
        days += 1
    if days <= 30:
        return "up_to_1m"
    if days <= 90:
        return "from_1m_to_3m"
    if days <= 180:
        return "from_3m_to_6m"
    return "over_6m"


# Заказчик 31.07.2026 свёл вопрос к трём вариантам: найм, бизнес, не работает.
# Остальные значения из списка не убраны намеренно — люди отвечают своими
# словами, и «работаю неофициально» надо услышать: именно оно закрывает Потреб,
# и подменять его наймом значило бы молча изменить, кому доступен продукт.
# Порядок важен: первое совпадение выигрывает, поэтому «неофициальн» стоит до
# «официальн».
_EMPLOYMENT = [
    ("самозанят", "self_employed"),
    ("неофициальн", "unofficial"),
    ("не официальн", "unofficial"),
    ("пенси", "retired"),
    ("безработ", "unemployed"),
    ("не работа", "unemployed"),
    # Тексты кнопок анкеты (quick_replies в matrix.yaml). Быстрый разбор
    # сравнивает ответ с маркером целиком по числу слов — без своих маркеров
    # «свой бизнес» и «по найму» уходили бы модели.
    ("свой бизнес", "business"),
    ("бизнес", "business"),
    ("ип", "business"),
    # «Официальное трудоустройство» и «найм» — одно и то же; отдельным вариантом
    # оно только путало клиентов, поэтому сворачиваем. Какой из двух кодов
    # считать верным, решил заказчик 12.08.2026 — см. _CANONICAL_SYNONYMS.
    ("официальн", "official"),
    ("по тк", "official"),
    ("по найм", "official"),
    ("найм", "official"),
    ("наём", "official"),
    ("работаю по договор", "official"),
    ("серая", "unofficial"),
]

# Одно положение дел — одно значение, даже если матрица принимает два.
#
# Матрица оставляет и `hired`, и `official`: условие маршрута принимает оба, и
# сузить перечисление значило бы отбрасывать корректный ответ извлекателя, а не
# исправлять его. Но в CRM это разные коды — 3082 и 376, — и на «работаю по
# найму» модель писала то одно, то другое, размазывая одну и ту же занятость по
# двум строкам отчёта. Заказчик 12.08.2026 выбрал `official`.
#
# Свод живёт здесь, а не в промпте: промпт — просьба, а это правило.
_CANONICAL_SYNONYMS: dict[str, dict[str, str]] = {
    "employment.type": {"hired": "official"},
}


def canonical_enum_value(path: str, value: str) -> str:
    """Сводит принимаемые матрицей синонимы к одному значению."""
    return _CANONICAL_SYNONYMS.get(path, {}).get(value, value)

_CLIENT_GOAL = [
    ("банкрот", "debt_relief"),
    ("списать долг", "debt_relief"),
    ("разобраться с долг", "debt_relief"),
    ("долгов", "debt_relief"),
    ("рефинанс", "refinance"),
    ("объединить кредит", "refinance"),
    ("получить деньги", "cash"),
    ("нужны деньги", "cash"),
    ("кредит", "cash"),
    ("займ", "cash"),
]

_PREFERRED_COLLATERAL = [
    ("недвиж", "realty"),
    ("квартир", "realty"),
    ("автомоб", "vehicle"),
    ("машин", "vehicle"),
    ("без разницы", "any"),
    ("любой", "any"),
]

_ENUMS: dict[str, list[tuple[str, str]]] = {
    "assets.realty.kind": _REALTY_KIND,
    "assets.realty.ownership_form": _OWNERSHIP_FORM,
    "assets.realty.owners": _OWNERS,
    "assets.realty.restriction": _REALTY_RESTRICTION,
    "debts.credit_history": _CREDIT_HISTORY,
    "debts.overdue_duration": _OVERDUE_DURATION,
    "employment.type": _EMPLOYMENT,
    "client.goal": _CLIENT_GOAL,
    "preferences.preferred_collateral": _PREFERRED_COLLATERAL,
}

# Анкета второго объекта — дословная копия первой: те же вопросы, те же
# варианты ответа, отличается только номер в пути. Поэтому правило разбора
# ищется по пути без номера, а не по точному совпадению.
#
# Появилось по находке аудита 19.08.2026: таблиц на
# `assets.realty_2.*` не было, и «в собственности» уезжало в CRM сырым текстом —
# lead-intake такого значения не знает, залоговый лид не собирался вовсе, а
# сторож вытеснения сравнивал сырой ответ с кодом первого объекта, и вторая
# квартира занимала ключи первой. Три строчки в `_ENUMS` закрыли бы ровно
# сегодняшний случай и дождались бы того же самого от третьего объекта.
_OBJECT_INDEX_RE = re.compile(r"_\d+(?=\.|$)")


def base_field_path(path: str) -> str:
    """`assets.realty_2.owners` → `assets.realty.owners`."""
    return _OBJECT_INDEX_RE.sub("", path)


def _enum_table(path: str) -> list[tuple[str, str]] | None:
    table = _ENUMS.get(path)
    if table is not None:
        return table
    # Запасной путь берётся, только если у поля без номера правило есть: иначе
    # ничего не меняется и выдумывать нормализацию мы не начинаем.
    return _ENUMS.get(base_field_path(path))


_YES = ("да", "есть", "имеется", "true", "конечно", "ага", "верно", "владею")
_NO = ("нет", "не имеется", "false", "отсутств", "нету")


def parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text:
        return None
    if any(text.startswith(marker) for marker in _NO):
        return False
    if any(marker in text for marker in _YES):
        return True
    return None


# «Двухкомнатная квартира» — квартира. Таблица ищет «комнат» раньше
# «квартир» (иначе комната в коммуналке становилась квартирой), и
# прилагательное «N-комнатная» ловилось как комната. Прод 28.09.2026:
# «Квартира 4 комнатная» ушла в CRM комнатой, а от типа объекта зависит
# залоговая категория. Комната как самостоятельное слово («комната в
# трёхкомнатной квартире») остаётся комнатой, доля — долей.
_ROOM_COUNT_RE = re.compile(
    r"(?:\d+|одно|двух|трех|трёх|четырех|четырёх|пяти|шести|много)"
    r"[\s.\-]*(?:х[\s.\-]*)?комнатн"
)
_FLAT_SLANG = (
    "однушк",
    "двушк",
    "трешк",
    "трёшк",
    "четырешк",
    "четырёшк",
    "евродвушк",
    "евротрешк",
    "евротрёшк",
    "студи",
)
_ROOM_NOUN_RE = re.compile(r"(?<![а-яё])комнат(?:а|у|ы|ой|е)?(?![а-яё])")


def _room_count_flat(base: str, text: str) -> bool:
    if base != "assets.realty.kind":
        return False
    if _ROOM_NOUN_RE.search(text) or "доля" in text or "доли" in text:
        return False
    return bool(_ROOM_COUNT_RE.search(text)) or any(
        slang in text for slang in _FLAT_SLANG
    )


# «Вовремя оплачиваю» — это «просрочки нет». Прод 28.09.2026: бот ответил
# «ответ „вовремя оплачиваю“ не совсем то, что мне нужно» и спросил про
# просрочку ещё раз. Отрицание перед «вовремя» («не всегда плачу вовремя»),
# упоминание прошлых или текущих просрочек и цифры (второй факт в той же
# реплике) отдаём модели: однозначным такой ответ уже не назвать.
_ON_TIME_RE = re.compile(
    r"(?<![а-яё])(?:вовремя|в срок|исправно|без задерж\w*|без опоздан\w*)"
)
_NEVER_OVERDUE_RE = re.compile(r"(?<![а-яё])не\s+просрочива")
_NEGATED_PREFIX_RE = re.compile(r"(?<![а-яё])не\s+(?:[а-яё]+\s+){0,2}$")
_OVERDUE_MENTIONED_RE = re.compile(r"просрочк|просрочен|долг")


def _paid_on_time(base: str, text: str) -> bool:
    if base not in {"debts.overdue_duration", "debts.credit_history"}:
        return False
    if re.search(r"\d", text) or _OVERDUE_MENTIONED_RE.search(text):
        return False
    if _NEVER_OVERDUE_RE.search(text):
        return True
    match = _ON_TIME_RE.search(text)
    if match is None:
        return False
    return _NEGATED_PREFIX_RE.search(text[: match.start()]) is None


# «Просрочек нет» — слова в обратном порядке, маркер «нет просроч» их не видит.
_NO_OVERDUE_RE = re.compile(
    r"^(?:просроч\w*\s+(?:нет|нету|не\s+было|не\s+имею)"
    r"|(?:нет|нету|без)\s+просроч\w*)$"
)


def _special_enum(base: str, text: str) -> str | None:
    """Ответы, которые таблица подстрок читает неверно или не читает вовсе."""
    if base in {"debts.overdue_duration", "debts.credit_history"} and _NO_OVERDUE_RE.match(
        text.strip(" .!")
    ):
        return "no_overdue"
    if _room_count_flat(base, text):
        return "flat"
    # «Доля в двухкомнатной квартире» — доля. Таблица находила «комнат» в
    # прилагательном раньше, чем «доля», и записывала комнату.
    if (
        base == "assets.realty.kind"
        and re.search(r"(?<![а-яё])дол[яиюе](?![а-яё])", text)
        and _ROOM_COUNT_RE.search(text)
    ):
        return "other"
    if _paid_on_time(base, text):
        return "no_overdue"
    return None


def parse_enum(path: str, value: Any) -> str | None:
    """Свободный текст → код матрицы для перечислимого поля."""
    table = _enum_table(path)
    if table is None or not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text:
        return None
    base = base_field_path(path)
    # Синоним сводится до поиска: `hired` — законное значение матрицы, но в
    # таблице его больше нет, и без этой строки он не опознавался бы вовсе.
    text = canonical_enum_value(base, text)
    # Уже нормализованное значение пропускаем как есть.
    if text in {code for _, code in table}:
        return text
    special = _special_enum(base, text)
    if special is not None:
        return special
    for marker, code in table:
        if marker in text:
            return canonical_enum_value(base, code)
    # Формулировки анкеты таблица уже знает («до месяца», «от 1 до 3»);
    # живой ответ («три дня», «полгода») разбирает отдельный парсер срока —
    # только для этого поля, только когда таблица маркеров не подошла
    # (значит, «нет просрочки» она бы уже отдала выше).
    if base == "debts.overdue_duration":
        return parse_overdue_duration_words(text)
    return None


def parse_enum_exact(path: str, value: Any) -> str | None:
    """Разбирает только ответ, целиком совпавший с одним enum-маркером.

    Обычный ``parse_enum`` намеренно ищет маркер внутри свободного текста и
    подходит для результата extractor. В fast path это теряло хвост составной
    реплики: «квартира Псков» становилась только квартирой. Здесь лишнее слово
    означает, что требуется полный модельный разбор.
    """
    table = _enum_table(path)
    if table is None or not isinstance(value, str):
        return None
    text = value.strip().casefold()
    if not text:
        return None
    base = base_field_path(path)
    canonical = canonical_enum_value(base, text)
    codes = {code for _, code in table}
    if canonical in codes:
        return canonical
    # Короткий ответ целиком — «двушка», «плачу вовремя»: второго факта в нём
    # нет, и модель для него не нужна.
    if len(text.split()) <= 4:
        special = _special_enum(base, text)
        if special is not None:
            return special
    phrases = [text]
    if base == "employment.type" and text.startswith("работаю "):
        phrases.append(text.removeprefix("работаю ").strip())
    for phrase in phrases:
        phrase_words = phrase.split()
        for marker, code in table:
            marker_words = marker.casefold().split()
            if len(phrase_words) != len(marker_words):
                continue
            if all(
                word.startswith(stem)
                for word, stem in zip(phrase_words, marker_words, strict=True)
            ):
                return canonical_enum_value(base, code)
    if base == "debts.overdue_duration":
        words = parse_overdue_duration_words(text)
        if words is not None:
            return words
        # Формулировки самой анкеты и кнопок («от 1 до 3 месяцев») длиннее
        # маркера на слово «месяцев», поэтому пословное сравнение их не берёт.
        for marker, code in table:
            rest = text.removeprefix(marker).strip()
            if text.startswith(marker) and rest in _OVERDUE_UNIT_TAILS:
                return code
    return None


_OVERDUE_UNIT_TAILS = frozenset({"", "месяц", "месяца", "месяцев", "мес", "мес."})


# --------------------------------------------------------------------------- #
# Единая точка входа
# --------------------------------------------------------------------------- #
def normalize_fact(path: str, value: Any, matrix: Matrix | None = None) -> Any:
    """Приводит одно значение к виду, в котором его понимает движок.

    Возврат None означает «не распознали» — факт не записывается, и бот переспросит.
    """
    if value is None:
        return None
    if path in ("request.amount", "debts.total"):
        return parse_amount(value)
    if path == "client.age":
        return parse_age(value)
    if path == "client.birthdate":
        return normalize_birthdate(value)
    if path == "client.region_class":
        if isinstance(value, str) and value in ("moscow_mo", "rf_other"):
            return value
        if value in _REALTY_ONLY_REGION_CLASSES:
            return None
        normalized_region = classify_region(value, matrix)
        # Списки регионов — характеристика объекта, но не места проживания клиента.
        return (
            "rf_other"
            if normalized_region in _REALTY_ONLY_REGION_CLASSES
            else normalized_region
        )
    if base_field_path(path) == "assets.realty.region_class":
        if isinstance(value, str) and (
            value in ("moscow_mo", "rf_other") or value in _REALTY_ONLY_REGION_CLASSES
        ):
            return value
        return classify_region(value, matrix)
    if path == "assets.vehicle.year":
        year = parse_amount(value)
        return year if year and 1900 <= year <= date.today().year + 1 else None
    if path in (
        "assets.has_realty",
        "assets.has_vehicle",
        "assets.vehicle.pledged",
        "preferences.collateral_allowed",
        "preferences.realty_collateral_allowed",
        "preferences.vehicle_collateral_allowed",
        "preferences.explore_alternatives",
    ):
        return parse_bool(value)
    if _enum_table(path) is not None:
        return parse_enum(path, value)
    if path == "client.phone":
        return parse_phone(value)
    if path == "client.name":
        text = str(value).strip()
        return text or None

    # Для остальных полей тип берём из матрицы. Списки путей выше описывают
    # особые случаи (регионы, возраст, год выпуска); всё, что ведёт себя обычно
    # для своего kind, не обязано в них перечисляться — иначе каждое новое поле
    # анкеты молча уезжает в CRM сырой строкой.
    spec = matrix.fields.get(path) if matrix is not None else None
    if spec is not None:
        if spec.kind == "money":
            return parse_amount(value)
        if spec.kind == "int":
            return parse_amount(value)
        if spec.kind == "bool":
            return parse_bool(value)
        if spec.kind == "text":
            text = str(value).strip()
            return text or None
        if spec.kind == "enum":
            # Сюда попадает только перечислимое поле, для которого правила нет
            # вовсе. Раньше такой ответ возвращался как есть — и свободный текст
            # клиента уезжал в CRM вместо кода. Молчание дороже лишнего
            # вопроса, но дешевле лида, который CRM не примет.
            return None
    return value
