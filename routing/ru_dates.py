r"""Возраст и дата рождения, названные живой речью.

Анкета спрашивает полные годы, но человек отвечает как ему привычно: «сорок
шесть», «родился в 1985», «27 марта 1985», «двадцать седьмого марта
восемьдесят пятого». До 20.08.2026 всё это уходило во внешний экстрактор, а
цифровая дата разбиралась хуже, чем никак: `\b\d{1,3}\b` в разборе возраста
брал ПЕРВОЕ число строки, и «27 марта 1985» превращалось в 27 лет. Клиенту
сорок один, в CRM уезжало двадцать семь — молча, с высокой уверенностью.

Отсюда правила, которые держит модуль:

* Дата разбирается ПЕРЕД голым числом. Если в строке есть месяц или четыре
  цифры года, число рядом с ними — не возраст.
* Двузначный год («в восемьдесят пятом») распознаётся только при явном
  признаке рождения. Иначе «85» — это восемьдесят пять лет, и так и надо.
* Всё, что не сложилось в правдоподобный возраст (18–120 лет), возвращает
  None: бот переспросит. Выдуманная дата хуже отсутствующей — её потом никто
  не отличит от настоящей.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any


# Разумные границы: младше восемнадцати заявок не бывает, старше ста двадцати
# лет не живут. Те же числа, что в остальном коде разбора возраста.
MIN_AGE = 18
MAX_AGE = 120

# Количественные числительные. Падежи не выводим по правилам — их немного, и
# перечисление честнее регулярки, которая «почти» ловит.
_CARDINALS: dict[str, int] = {
    "ноль": 0,
    "один": 1, "одна": 1, "одного": 1,
    "два": 2, "две": 2, "двух": 2,
    "три": 3, "трёх": 3, "трех": 3,
    "четыре": 4, "четырёх": 4, "четырех": 4,
    "пять": 5, "пяти": 5,
    "шесть": 6, "шести": 6,
    "семь": 7, "семи": 7,
    "восемь": 8, "восьми": 8,
    "девять": 9, "девяти": 9,
    "десять": 10, "десяти": 10,
    "одиннадцать": 11, "одиннадцати": 11,
    "двенадцать": 12, "двенадцати": 12,
    "тринадцать": 13, "тринадцати": 13,
    "четырнадцать": 14, "четырнадцати": 14,
    "пятнадцать": 15, "пятнадцати": 15,
    "шестнадцать": 16, "шестнадцати": 16,
    "семнадцать": 17, "семнадцати": 17,
    "восемнадцать": 18, "восемнадцати": 18,
    "девятнадцать": 19, "девятнадцати": 19,
    "двадцать": 20, "двадцати": 20,
    "тридцать": 30, "тридцати": 30,
    "сорок": 40, "сорока": 40,
    "пятьдесят": 50, "пятидесяти": 50,
    "шестьдесят": 60, "шестидесяти": 60,
    "семьдесят": 70, "семидесяти": 70,
    "восемьдесят": 80, "восьмидесяти": 80,
    "девяносто": 90, "девяноста": 90,
    "сто": 100, "ста": 100,
    "двести": 200, "двухсот": 200,
    "триста": 300, "трёхсот": 300, "трехсот": 300,
    "четыреста": 400, "четырёхсот": 400,
    "пятьсот": 500, "пятисот": 500,
    "шестьсот": 600, "шестисот": 600,
    "семьсот": 700, "семисот": 700,
    "восемьсот": 800, "восьмисот": 800,
    "девятьсот": 900, "девятисот": 900,
}

# Порядковые. Ими называют день («двадцать седьмого») и год («восемьдесят
# пятого», «тысяча девятьсот восемьдесят пятого»).
_ORDINALS: dict[str, int] = {
    "первое": 1, "первого": 1, "первый": 1,
    "второе": 2, "второго": 2, "второй": 2,
    "третье": 3, "третьего": 3, "третий": 3,
    "четвёртое": 4, "четвертое": 4, "четвёртого": 4, "четвертого": 4,
    "четвёртый": 4, "четвертый": 4,
    "пятое": 5, "пятого": 5, "пятый": 5,
    "шестое": 6, "шестого": 6, "шестой": 6,
    "седьмое": 7, "седьмого": 7, "седьмой": 7,
    "восьмое": 8, "восьмого": 8, "восьмой": 8,
    "девятое": 9, "девятого": 9, "девятый": 9,
    "десятое": 10, "десятого": 10, "десятый": 10,
    "одиннадцатое": 11, "одиннадцатого": 11, "одиннадцатый": 11,
    "двенадцатое": 12, "двенадцатого": 12, "двенадцатый": 12,
    "тринадцатое": 13, "тринадцатого": 13, "тринадцатый": 13,
    "четырнадцатое": 14, "четырнадцатого": 14, "четырнадцатый": 14,
    "пятнадцатое": 15, "пятнадцатого": 15, "пятнадцатый": 15,
    "шестнадцатое": 16, "шестнадцатого": 16, "шестнадцатый": 16,
    "семнадцатое": 17, "семнадцатого": 17, "семнадцатый": 17,
    "восемнадцатое": 18, "восемнадцатого": 18, "восемнадцатый": 18,
    "девятнадцатое": 19, "девятнадцатого": 19, "девятнадцатый": 19,
    "двадцатое": 20, "двадцатого": 20, "двадцатый": 20,
    "тридцатое": 30, "тридцатого": 30, "тридцатый": 30,
    "сотое": 100, "сотого": 100,
    "сороковое": 40, "сорокового": 40, "сороковой": 40,
    "пятидесятое": 50, "пятидесятого": 50,
    "шестидесятое": 60, "шестидесятого": 60,
    "семидесятое": 70, "семидесятого": 70,
    "восьмидесятое": 80, "восьмидесятого": 80,
    "девяностое": 90, "девяностого": 90,
    # Предложный падеж — то, как год называют чаще всего: «в восемьдесят пятом».
    "первом": 1, "втором": 2, "третьем": 3,
    "четвёртом": 4, "четвертом": 4, "пятом": 5, "шестом": 6, "седьмом": 7,
    "восьмом": 8, "девятом": 9, "десятом": 10,
    "одиннадцатом": 11, "двенадцатом": 12, "тринадцатом": 13,
    "четырнадцатом": 14, "пятнадцатом": 15, "шестнадцатом": 16,
    "семнадцатом": 17, "восемнадцатом": 18, "девятнадцатом": 19,
    "двадцатом": 20, "тридцатом": 30, "сороковом": 40, "пятидесятом": 50,
    "шестидесятом": 60, "семидесятом": 70, "восьмидесятом": 80,
    "девяностом": 90,
    # Двухтысячный стоит особняком: «две тысячи первого» складывается само,
    # а он — одно слово.
    "двухтысячный": 2000, "двухтысячного": 2000, "двухтысячном": 2000,
}

_THOUSAND = {"тысяча", "тысячи", "тысяч", "тысячу"}

# Месяц по основе: падежей у него шесть, а основа одна.
_MONTH_STEMS: list[tuple[str, int]] = [
    ("январ", 1), ("феврал", 2), ("март", 3), ("апрел", 4),
    ("ма", 5), ("июн", 6), ("июл", 7), ("август", 8),
    ("сентябр", 9), ("октябр", 10), ("ноябр", 11), ("декабр", 12),
]

# Признак, что названный год — год рождения, а не что-то ещё. Нужен только
# двузначному году: «в восемьдесят пятом» — рождение, «85» — возраст.
_BIRTH_MARKERS = (
    "родил", "рождени", "рожден", "г.р", "гр.", "др ", "день рождения",
)

# Дата в сообщении не обязательно про клиента. «Жена родилась 5 мая 1990» и
# «сын родился в 2007» — это чужие даты, а разбор считал их возрастом клиента
# и молча уводил анкету: тридцатишестилетняя жена делала клиента моложе на
# десять лет. Признак родства отменяет разбор целиком: лучше переспросить.
_THIRD_PARTY_MARKERS = (
    "жен", "муж", "супруг", "сын", "доч", "мам", "мать", "пап", "отец",
    "брат", "сестр", "тёщ", "тещ", "тест", "свекров", "ребён", "ребен",
    "дет", "внук", "бабушк", "дедушк", "друг", "знаком", "партнёр", "партнер",
    "созаём", "созаем", "поручител", "клиент", "коллег",
)


def date_words_removed(text: str) -> list[str]:
    """Слова, оставшиеся после выброса самой даты.

    Из строки убираются числа, числительные и месяц — всё, чем дата
    записывается. Остаток показывает, ответ это на вопрос о возрасте или
    рассказ о чём-то другом, где дата попалась рядом.
    """
    rest: list[str] = []
    for word in _words(text):
        if word in _CARDINALS or word in _ORDINALS or word in _THOUSAND:
            continue
        if word.isdigit() or _month_from_word(word) is not None:
            continue
        if len(word) < 2 or word in _DATE_FILLER:
            continue
        rest.append(word)
    return rest


# Слова, которые стоят рядом с датой и ничего к ней не добавляют.
_DATE_FILLER = frozenset({"года", "год", "году", "мне", "числа", "от", "в", "с"})


def mentions_someone_else(text: str) -> bool:
    """Есть ли в сообщении чужой человек, к которому может относиться дата."""
    lowered = text.lower()
    return any(marker in lowered for marker in _THIRD_PARTY_MARKERS)


def has_birth_marker(text: str) -> bool:
    """Сказано ли прямо, что речь о рождении."""
    return _has_birth_marker(text)

_WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)
# Цифровая дата: 27.03.1985, 27/03/1985, 27-03-1985, а также ISO 1985-03-27.
_NUMERIC_DATE_RE = re.compile(
    r"(?<!\d)(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})(?!\d)"
    r"|(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"
)
# Дата с месяцем словом: «27 марта 1985», «27 марта 85 года».
_SPOKEN_DATE_RE = re.compile(
    r"(?<!\d)(\d{1,2})\s+([а-яё]+)\s+(\d{2,4})(?!\d)", re.IGNORECASE
)
_FOUR_DIGIT_YEAR_RE = re.compile(r"(?<!\d)(1[89]\d{2}|20[0-2]\d)(?!\d)")


def _words(text: str) -> list[str]:
    return [word.lower() for word in _WORD_RE.findall(text)]


def _month_from_word(word: str) -> int | None:
    lowered = word.lower()
    for stem, number in _MONTH_STEMS:
        if lowered.startswith(stem):
            # «мар» отдельно от «март» не бывает, а «ма» совпало бы с «марта»:
            # проверяем самые длинные основы первыми — список уже так собран,
            # кроме мая, у которого основа короче некуда.
            if stem == "ма" and not re.fullmatch(r"ма[йея]", lowered):
                continue
            return number
    return None


def _numeric_value(words: list[str]) -> int | None:
    """Сумма подряд идущих числительных: «тысяча девятьсот восемьдесят пять»."""
    total = 0
    seen = False
    for word in words:
        if word in _THOUSAND:
            total = (total or 1) * 1000
            seen = True
        elif word in _CARDINALS:
            total += _CARDINALS[word]
            seen = True
        elif word in _ORDINALS:
            total += _ORDINALS[word]
            seen = True
        elif word.isdigit():
            total += int(word)
            seen = True
        else:
            continue
    return total if seen else None


def parse_spoken_number(text: str) -> int | None:
    """Число, названное словами. «сорок шесть» → 46."""
    return _numeric_value(_words(text))


def _has_birth_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _BIRTH_MARKERS)


# «в восемьдесят пятом», «с девяносто первого» — так называют год и только
# год. Про возраст по-русски так не говорят: «мне в восемьдесят пятом» не
# скажет никто, поэтому предлог вместе с порядковым числительным — такой же
# однозначный признак, как слово «родился».
_YEAR_PREPOSITION_RE = re.compile(r"(?:^|\s)[вс]\s+(?=[а-яё\d])", re.IGNORECASE)


def _named_as_a_year(text: str) -> bool:
    if not _YEAR_PREPOSITION_RE.search(text):
        return False
    words = _words(text)
    tail = [word for word in words if word in _ORDINALS or word in _CARDINALS]
    return bool(tail) and tail[-1] in _ORDINALS


def _plausible_birth_year(year: int, today: date) -> bool:
    age = today.year - year
    return MIN_AGE <= age <= MAX_AGE


def _expand_two_digit_year(value: int, today: date) -> int | None:
    """«восемьдесят пятого» → 1985. Двадцатые годы дают 20xx."""
    for century in (1900, 2000):
        year = century + value
        if _plausible_birth_year(year, today):
            return year
    return None


def parse_birthdate(text: Any, today: date | None = None) -> date | None:
    """Полная дата рождения из свободной речи, иначе None.

    Разбирается только ПОЛНАЯ дата — день, месяц и год. Один год датой не
    считается: 1 января ставит маппинг CRM осознанно и по просьбе заказчика, а
    здесь это было бы догадкой, неотличимой от сказанного клиентом.
    """
    if not isinstance(text, str):
        return None
    current = today or date.today()

    if match := _NUMERIC_DATE_RE.search(text):
        groups = match.groups()
        if groups[0]:
            day, month, year = int(groups[0]), int(groups[1]), int(groups[2])
        else:
            year, month, day = int(groups[3]), int(groups[4]), int(groups[5])
        return _valid_birthdate(day, month, year, current)

    if match := _SPOKEN_DATE_RE.search(text):
        day = int(match.group(1))
        month = _month_from_word(match.group(2))
        raw_year = int(match.group(3))
        if month is not None:
            year = (
                raw_year
                if raw_year > 100
                else (_expand_two_digit_year(raw_year, current) or 0)
            )
            return _valid_birthdate(day, month, year, current)

    return _spoken_date_in_words(text, current)


def _spoken_date_in_words(text: str, today: date) -> date | None:
    """«двадцать седьмого марта восемьдесят пятого года»."""
    words = _words(text)
    month_at: int | None = None
    month: int | None = None
    for index, word in enumerate(words):
        number = _month_from_word(word)
        if number is not None and word not in _CARDINALS and word not in _ORDINALS:
            month_at, month = index, number
            break
    if month is None or month_at is None:
        return None

    day = _numeric_value(words[:month_at])
    year_value = _numeric_value(words[month_at + 1:])
    if day is None or year_value is None:
        return None
    year = year_value if year_value > 100 else (_expand_two_digit_year(year_value, today) or 0)
    return _valid_birthdate(day, month, year, today)


def _valid_birthdate(day: int, month: int, year: int, today: date) -> date | None:
    try:
        born = date(year, month, day)
    except ValueError:
        return None
    if born > today:
        return None
    return born if _plausible_birth_year(born.year, today) else None


def age_from_birthdate(born: date, today: date | None = None) -> int:
    current = today or date.today()
    return current.year - born.year - (
        (current.month, current.day) < (born.month, born.day)
    )


def parse_birth_year(text: Any, today: date | None = None) -> int | None:
    """Год рождения без дня и месяца: «родился в 1985», «в восемьдесят пятом».

    Четырёхзначный год говорит сам за себя. Двузначный — только рядом со
    словом о рождении: иначе «85» это восемьдесят пять лет, и трактовать их
    как год значило бы менять смысл ответа.
    """
    if not isinstance(text, str):
        return None
    current = today or date.today()

    if match := _FOUR_DIGIT_YEAR_RE.search(text):
        year = int(match.group(1))
        if _plausible_birth_year(year, current):
            return year

    if not _has_birth_marker(text) and not _named_as_a_year(text):
        return None
    value = _numeric_value(_words(text))
    if value is None:
        return None
    if value > 100:
        return value if _plausible_birth_year(value, current) else None
    return _expand_two_digit_year(value, current)
