"""Обезличивание payload перед внешними LLM-вызовами.

Полные данные остаются в server-side state и уходят только в маршрутизирующий
микросервис. Разговорная модель, извлекатель и observability получают копию, где
телефон и точный адрес скрыты, дата рождения заменена возрастом/годом, а из ФИО
оставлено только обращение по имени.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from routing.normalize import parse_phone
from routing.ru_dates import (
    _MONTH_STEMS,
    age_from_birthdate,
    date_words_removed,
    has_birth_marker,
    mentions_someone_else,
    parse_birthdate,
)
from names_ru import FIRST_NAMES, capitalize_name, first_name_from_fio
from utils import client_display_name
from context_policy import context_delivery_held
from debt_policy import debt_delivery_held
from context_projection import project_context_state


_BARE_PRIVATE_PLACEHOLDER = re.compile(
    r"\[\s*(?:имя|фио|фамилия|дата\s+рождения|точный\s+адрес)\s*\]", re.IGNORECASE,
)


def contains_private_placeholder(text: str) -> bool:
    """Recognize closed masking control tokens at the visible-output boundary."""
    labels = (
        "[имя скрыто]", "[ФИО скрыто]", "[фамилия скрыта]", "[часть ФИО скрыта]",
        "[остальная часть ФИО скрыта]", "[телефон]", "[точный адрес скрыт]",
        "[город или регион скрыт]", "[дата рождения скрыта]", "[сведения анкеты]",
    )
    lowered = text.casefold()
    return any(label.casefold() in lowered for label in labels) or _BARE_PRIVATE_PLACEHOLDER.search(text) is not None


# Тот же набор написаний, что и в routing.normalize: номер без «8» — тоже
# номер, и в зарубежную модель он уходить не должен.
_PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:"
    r"(?:\+7|8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}"
    r"|7[\s\-()]*9\d{2}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}"
    r"|9\d{2}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}"
    r")(?!\d)"
)
_DATE_PATTERN = re.compile(
    r"\b(\d{1,2})[./\-](\d{1,2})[./\-](19\d{2}|20[0-2]\d)\b"
    r"|\b(19\d{2}|20[0-2]\d)[./\-](\d{1,2})[./\-](\d{1,2})\b"
)
# Дата словами: «27 марта 1985», «двадцать седьмого марта восемьдесят пятого».
# Шаблон намеренно жадный — отсекает не он, а разбор: заменяется только то, что
# сложилось в правдоподобную дату рождения. Без этого словесная дата уезжала в
# зарубежную модель нетронутой, тогда как цифровая маскировалась с 19.08.2026.
_SPOKEN_DATE_PATTERN = re.compile(
    r"(?:\d{1,2}|[а-яё]+(?:\s+[а-яё]+)?)\s+"
    r"(?:" + "|".join(stem for stem, _ in _MONTH_STEMS) + r")[а-яё]*\s+"
    r"(?:\d{2,4}|[а-яё]+(?:\s+[а-яё]+){0,3})",
    re.IGNORECASE,
)
_NAME_PATTERN = re.compile(
    r"(?P<prefix>(?i:\b(?:меня зовут|меня)\s+))"
    r"(?P<first>[А-ЯЁ][а-яё-]+)"
    r"(?P<rest>(?:\s+[А-ЯЁ][а-яё-]+){1,2})?",
)
_BARE_NAME_PATTERN = re.compile(
    r"^(?:[А-ЯЁ][а-яё-]+|[А-ЯЁ]{2,})"
    r"(?:\s+(?:[А-ЯЁ][а-яё-]+|[А-ЯЁ]{2,})){0,2}$",
)
_NON_NAME_FIRST_WORDS = frozenset(
    {"пока", "потом", "позже", "город", "область", "деревня", "село"}
)
_PLACE_FIRST_WORDS = frozenset(
    {
        "нижний",
        "великий",
        "великие",
        "старый",
        "новый",
        "минеральные",
        "набережные",
        "белая",
        "красное",
        "красная",
    }
)
_COMMON_FIRST_NAMES = frozenset(
    name.casefold()
    for name in (
        "Александр", "Алексей", "Анатолий", "Андрей", "Антон", "Артем",
        "Артём", "Борис", "Валерий", "Василий", "Виктор", "Виталий",
        "Владимир", "Владислав", "Григорий", "Денис", "Дмитрий", "Евгений",
        "Иван", "Илья", "Кирилл", "Константин", "Максим", "Михаил", "Никита",
        "Николай", "Олег", "Павел", "Роман", "Сергей", "Станислав", "Юрий",
        "Алена", "Алёна", "Анастасия", "Анна", "Валентина", "Вера", "Виктория",
        "Галина", "Дарья", "Екатерина", "Елена", "Ирина", "Ксения", "Лариса",
        "Любовь", "Людмила", "Марина", "Мария", "Надежда", "Наталья", "Ольга",
        "Полина", "Светлана", "София", "Софья", "Татьяна", "Юлия",
    )
) | FIRST_NAMES
_LOWERCASE_NAME_WORD_RE = re.compile(r"^[а-яё-]+$")


def _lowercase_name_answer(text: str) -> str | None:
    """«сергей иванович» на вопрос об имени — тоже имя, только без заглавных.

    Бой 19.10.2026: такой ответ не узнавался (шаблон требует заглавную букву),
    уходил модели, и бот обратился к клиенту «Иванович». Строчные слова
    принимаются, только если среди них есть имя из словаря: «не скажу» или
    «привет» именем не станут.
    """
    words = text.split()
    if not 1 <= len(words) <= 3:
        return None
    if not all(_LOWERCASE_NAME_WORD_RE.fullmatch(word) for word in words):
        return None
    if words[0] in _NON_NAME_FIRST_WORDS or words[0] in _PLACE_FIRST_WORDS:
        return None
    if not any(word.replace("ё", "е") in FIRST_NAMES for word in words):
        return None
    return " ".join(capitalize_name(word) for word in words)
_ADDRESS_DETAIL_PATTERN = re.compile(
    # Название улицы — достаточно сильный признак точного адреса. Окончания
    # берём с запасом: клиент пишет «на улице Баумана», «по проспекту Мира», а
    # прежний шаблон знал только именительный падеж — и улица оставалась в
    # тексте нетронутой (аудит 19.08.2026).
    r"\b(?:ул(?:иц\w*)?\.?|просп(?:ект\w*)?\.?|пр-?кт\.?|пер(?:еул\w*)?\.?"
    r"|шоссе|бул(?:ьвар\w*)?\.?|проезд\w*|наб(?:ережн\w*)?\.?|мкр\.?)\s+"
    r"[\wА-Яа-яЁё.\-/ ,]+"
    # «дом» и «квартира» считаются адресом только вместе с номером. Иначе обычная
    # фраза «есть квартира в Москве» превращалась в «есть [точный адрес скрыт]».
    r"|\b(?:д(?:ом)?\.?|кв(?:артира)?\.?)\s*(?:№\s*)?\d+"
    r"[\wА-Яа-яЁё.\-/]*",
    re.IGNORECASE,
)


def extract_local_pii_updates(
    text: str,
    *,
    expected_paths: list[str] | None = None,
    existing_facts: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Детерминированно извлекает ПД, которые нельзя отдавать внешнему extractor.

    Телефон и дата рождения распознаются обычным кодом. Имя берётся только из
    однозначных конструкций «меня зовут ...» / «меня ...»; в остальных случаях
    его по-прежнему может извлечь модель после обезличивания фамилии.
    """
    updates: list[dict[str, Any]] = []
    if phone_match := _PHONE_PATTERN.search(text):
        if phone := parse_phone(phone_match.group()):
            updates.append({"path": "client.phone", "value": phone, "confidence": "high"})

    # Дата рождения: и цифрами, и словами. Из неё берётся не только возраст —
    # сама дата тоже: в CRM это обязательная графа, а точная дата всегда лучше
    # первого января, которое маппинг подставляет по одному числу лет.
    #
    # Но дата в сообщении не обязана быть датой рождения клиента. «Квартиру
    # купил 15 мая 2005» делало клиента двадцатилетним, «жена родилась 5 мая
    # 1990» — тридцатишестилетним. Поэтому дата принимается только как своя:
    # либо про рождение сказано прямо, либо ответ и есть эта дата.
    if born := (parse_birthdate(text) if _date_is_the_clients_own(text) else None):
        updates.append(
            {"path": "client.birthdate", "value": born.isoformat(), "confidence": "high"}
        )
        age = age_from_birthdate(born)
        if 0 < age < 120:
            updates.append({"path": "client.age", "value": age, "confidence": "high"})

    name_match = _NAME_PATTERN.search(text)
    normalized_text = " ".join(text.split()).removesuffix(".")
    expects_name = "client.name" in (expected_paths or [])
    bare_candidate = (
        normalized_text
        if expects_name and _BARE_NAME_PATTERN.fullmatch(normalized_text)
        else None
    )
    if bare_candidate is None and expects_name and name_match is None:
        bare_candidate = _lowercase_name_answer(normalized_text)
    # Одно слово без вводной фразы неотличимо от города («Москва»). Два слова
    # обычно являются ФИО без отчества, но типичные топонимы начинаются с
    # прилагательного («Нижний Новгород», «Минеральные Воды»), а посторонние
    # ответы — со служебного слова («Пока Иван»). Сохраняем legal name целиком,
    # но display_name ниже из многословной строки всё равно не угадываем.
    bare_words = bare_candidate.split() if bare_candidate is not None else []
    bare_first = bare_words[0].casefold() if bare_words else ""
    bare_name = (
        bare_candidate
        if bare_candidate is not None
        and (
            len(bare_words) == 3
            or (len(bare_words) == 1 and bare_first in _COMMON_FIRST_NAMES)
            or (
                len(bare_words) == 2
                and bare_first not in _NON_NAME_FIRST_WORDS
                and bare_first not in _PLACE_FIRST_WORDS
            )
        )
        else None
    )
    if name_match or bare_name:
        first_name = (
            name_match.group("first")
            if name_match
            else str(bare_name).split()[0]
        )
        full_name = (
            f"{first_name}{name_match.group('rest') or ''}"
            if name_match
            else str(bare_name)
        )
        raw_existing_client = (existing_facts or {}).get("client")
        crm_name = (
            raw_existing_client.get("name")
            if isinstance(raw_existing_client, dict)
            else None
        )
        existing_display_name = client_display_name(existing_facts)
        # Подтверждённое обращение не должно затирать полное CRM-ФИО из формы.
        # Если юридического имени ещё нет, сохраняем весь введённый текст.
        if (
            not isinstance(crm_name, str)
            or not crm_name.strip()
            or existing_display_name == crm_name
        ):
            updates.append(
                {"path": "client.name", "value": full_name, "confidence": "high"}
            )
        # Порядок ФИО не стандартизирован даже после «меня зовут»: встречаются
        # и «Иван Иванович Иванов», и «Иванов Иван Иванович». Позицию имени не
        # угадываем — иначе бот обращается по фамилии. Имя из многословной
        # строки берётся только по надёжным признакам: словарь имён, отчество,
        # окончание фамилии (`names_ru.first_name_from_fio`).
        display_name = first_name_from_fio(full_name)
        if display_name is None:
            return updates
        updates.append(
            {
                "path": "client.display_name",
                "value": display_name,
                "confidence": "high",
            }
        )
    return updates


def is_strict_local_name_answer(text: str) -> bool:
    """Можно ли считать всю реплику только ответом на вопрос об имени.

    Такой ответ уже целиком разобран локально, поэтому отдельный extractor не
    добавит фактов, а лишь задержит следующий вопрос. Любой хвост, число,
    вопрос или пояснение ломает fullmatch и остаётся модельному контуру.
    """
    normalized = " ".join((text or "").split()).removesuffix(".")
    return bool(
        normalized
        and (
            _BARE_NAME_PATTERN.fullmatch(normalized)
            or _NAME_PATTERN.fullmatch(normalized)
            or _lowercase_name_answer(normalized)
        )
    )


def _date_is_the_clients_own(text: str) -> bool:
    """Отличает свою дату рождения от чужой и от даты события."""
    if mentions_someone_else(text):
        return False
    if has_birth_marker(text):
        return True
    # Ответ на вопрос о возрасте — это сама дата: «27 марта 1985», «мне
    # 27.03.1985», «двадцать седьмого марта восемьдесят пятого». Считаем то,
    # что осталось ПОСЛЕ даты: у даты словами слова и есть, и подсчёт всей
    # строки отвергал именно тот случай, ради которого всё делалось.
    return len(date_words_removed(text)) <= 1


def _mask_spoken_date_to_year(match: re.Match[str], birth: bool = True) -> str:
    """Оставляет модели год и прячет день с месяцем — как у цифровой даты."""
    born = parse_birthdate(match.group(0))
    if born is None:
        return match.group(0)
    return f"{born.year} год рождения" if birth else str(born.year)


def _replace_whole(text: str, original: str, replacement: str) -> str:
    """Заменяет вхождение целым словом, а не подстрокой.

    Голый `str.replace` маскировал фамилию внутри любого слова, которое с неё
    начинается. На тестовом лиде 10.08.2026 клиент назвался «Тестов Тест
    Тестович», фамилия «Тест» нашлась внутри обращения «Тестов», и модель
    получила в истории «[фамилия скрыта]ов» — а потом сказала это клиенту.

    Границы ставятся только там, где токен начинается или заканчивается буквой
    или цифрой: номер вида «+79990000000» иначе перестал бы совпадать вовсе.
    """
    prefix = r"(?<!\w)" if original[:1].isalnum() else ""
    suffix = r"(?!\w)" if original[-1:].isalnum() else ""
    # Регистр не важен: клиент пишет «сергей иванович» с маленькой буквы, а в
    # фактах — «Сергей Иванович». Ролевой прогон 20.10.2026: строчное
    # отчество не маскировалось, и модель обратилась к клиенту «Иванович».
    pattern = re.compile(prefix + re.escape(original) + suffix, re.IGNORECASE)
    return pattern.sub(lambda _: replacement, text)


def _mask_tree(value: Any, masker: "Masker") -> Any:
    """Прогоняет каждый строковый лист структуры через mask_text.

    Общее правило вместо перечня полей: телефон, точная дата и улица с домом
    распознаются по виду, а не по имени ключа. Числа, перечислимые коды и
    названия городов остаются нетронутыми — им маскировать нечего.
    """
    if isinstance(value, str):
        return masker.mask_text(value)
    if isinstance(value, dict):
        return {key: _mask_tree(item, masker) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask_tree(item, masker) for item in value]
    if isinstance(value, tuple):
        return tuple(_mask_tree(item, masker) for item in value)
    return value


@dataclass
class Masker:
    """Маскировщик, построенный из фактов одной сессии."""

    direct: dict[str, str] = field(default_factory=dict)
    first_name: str | None = None
    direct_first: bool = False

    @classmethod
    def from_facts(cls, facts: dict[str, Any] | None) -> "Masker":
        masker = cls()
        if not isinstance(facts, dict):
            return masker
        raw_client = facts.get("client")
        client: dict[str, Any] = raw_client if isinstance(raw_client, dict) else {}

        name = client.get("name")
        if isinstance(name, str) and name.strip():
            parts = name.strip().split()
            masker.first_name = client_display_name(facts)
            if len(parts) > 1:
                masker.direct[name] = (
                    f"{masker.first_name} [остальная часть ФИО скрыта]"
                    if masker.first_name
                    else "[ФИО скрыто]"
                )
                for part in parts:
                    if masker.first_name and part.casefold() == masker.first_name.casefold():
                        continue
                    if len(part) >= 2:
                        masker.direct[part] = "[часть ФИО скрыта]"

        phone = client.get("phone")
        if isinstance(phone, str) and phone.strip():
            masker.direct[phone] = "[телефон]"
            digits = re.sub(r"\D", "", phone)
            if len(digits) == 11 and digits[0] in {"7", "8"}:
                for variant in (digits, "+" + digits, "+7" + digits[1:], "8" + digits[1:]):
                    masker.direct[variant] = "[телефон]"

        region = client.get("region")
        city = client.get("city")
        if isinstance(region, str) and _contains_exact_address(region):
            safe_region = (
                str(city).strip()
                if isinstance(city, str) and city.strip()
                else region.split(",")[0].strip()
            )
            masker.direct[region] = f"{safe_region} (точный адрес скрыт)"
        return masker

    def mask_text(self, text: str) -> str:
        if not text:
            return text
        # Hide known whole values before generic patterns can split an FIO or
        # consume useful text following a known address in private context.
        if self.direct_first:
            text = self._mask_direct(text)
        masked = _PHONE_PATTERN.sub("[телефон]", text)
        # Про рождение сказано — так и пишем; не сказано — оставляем голый
        # год. Иначе дата покупки квартиры превращалась в «2005 год
        # рождения», и модель читала возраст клиента из ремонта.
        birth = _date_is_the_clients_own(masked)
        masked = _DATE_PATTERN.sub(
            lambda match: _mask_date_to_year(match, birth), masked
        )
        masked = _SPOKEN_DATE_PATTERN.sub(
            lambda match: _mask_spoken_date_to_year(match, birth), masked
        )
        masked = _NAME_PATTERN.sub(_mask_name_match, masked)
        masked = _ADDRESS_DETAIL_PATTERN.sub("[точный адрес скрыт]", masked)
        return self._mask_direct(masked)

    def _mask_direct(self, text: str) -> str:
        for original in sorted(self.direct, key=len, reverse=True):
            if original.casefold() in text.casefold():
                text = _replace_whole(text, original, self.direct[original])
        return text

    def mask_facts(self, facts: dict[str, Any]) -> dict[str, Any]:
        # Сначала общее правило по всему дереву, потом точечные исключения.
        # Раньше здесь было только перечисление трёх ключей — имя, телефон,
        # регион, — а анкета кладёт в факты ещё дату рождения и два адреса.
        # Аудит 19.08.2026: «10.05.1985» и «улица Баумана» уходили в зарубежную
        # модель дословно. Перечисление молчит про всё, чего в нём нет; правило
        # закрывает и те поля, которых в контракте ещё не существует.
        masked = _mask_tree(facts, self)
        raw_client = masked.get("client")
        if not isinstance(raw_client, dict):
            return masked
        client: dict[str, Any] = raw_client

        name = client.get("name")
        if isinstance(name, str) and name.strip():
            if self.first_name:
                client["name"] = self.first_name
            else:
                # Подтверждённого имени нет — для модели имени просто НЕТ.
                # Литерал «[ФИО скрыто]» она принимала за имя и копировала в
                # обращение: «[ФИО скрыто], какая сумма вам нужна?» доехало до
                # живого клиента (QA 28.08.2026). Отсутствие ключа безопасно:
                # план вопросов строит код по немаскированным фактам, повторно
                # имя не спросится.
                client.pop("name", None)
        if client.get("phone"):
            client["phone"] = "[телефон]"
        region = client.get("region")
        city = client.get("city")
        if isinstance(region, str) and _contains_exact_address(region):
            safe_region = (
                str(city).strip()
                if isinstance(city, str) and city.strip()
                else region.split(",")[0].strip()
            )
            client["region"] = f"{safe_region} (точный адрес скрыт)"
        return masked

    def mask_history(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {**message, "content": self.mask_text(str(message.get("content", "")))}
            for message in history
        ]

    def mask_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Возвращает обезличенную копию runtime payload."""
        masked = copy.copy(payload)
        facts = payload.get("facts")
        if isinstance(facts, dict):
            masked["facts"] = self.mask_facts(facts)
        history = payload.get("short_history")
        if isinstance(history, list):
            masked["short_history"] = self.mask_history(history)
        if "latest_user_message" in payload:
            latest = str(payload.get("latest_user_message", ""))
            expected_paths = payload.get("expected_paths")
            asks_name = isinstance(expected_paths, list) and "client.name" in expected_paths
            normalized_latest = " ".join(latest.split())
            if (
                asks_name
                and len(normalized_latest.split()) > 1
                and _BARE_NAME_PATTERN.fullmatch(normalized_latest)
            ):
                masked["latest_user_message"] = "[ФИО скрыто]"
            else:
                masked["latest_user_message"] = self.mask_text(latest)
        summary = payload.get("internal_summary")
        if isinstance(summary, str):
            masked["internal_summary"] = self.mask_text(summary)
        return masked


def _mask_name_match(match: re.Match[str]) -> str:
    rest = match.group("rest")
    suffix = " [фамилия скрыта]" if rest else ""
    return f"{match.group('prefix')}{match.group('first')}{suffix}"


def _mask_date_to_year(match: re.Match[str], birth: bool = True) -> str:
    groups = match.groups()
    year = groups[2] or groups[3]
    if not year:
        return "[дата рождения]"
    return f"{year} год рождения" if birth else str(year)


def _contains_exact_address(value: str) -> bool:
    """Отличает полный адрес от безопасного названия города/региона.

    Город нужен модели и маршрутизатору, поэтому «Москва» или «Московская область»
    не маскируются. Скрываем строку только при наличии улицы/дома/квартиры либо
    характерной пары «часть адреса, номер».
    """
    if _ADDRESS_DETAIL_PATTERN.search(value):
        return True
    return "," in value and re.search(r"\b\d+[а-яё]?\b", value, re.IGNORECASE) is not None


# Пути персональных данных — ровно те, что читает `Masker.from_facts`. Список
# обязан оставаться синхронным с ней: заведи маскировщик новым путём здесь, но
# не научи `from_facts` его маскировать — и история подсунет в словарь замен
# то, что сама же не умеет корректно закрыть.
_PERSONAL_FACT_PATHS = ("client.name", "client.phone", "client.region")


def _historical_direct_replacements(
    events: list[Any], current_facts: dict[str, Any]
) -> dict[str, str]:
    """Словарь замен для значений персональных полей, звучавших за сессию.

    `Masker.from_facts` строится только по текущим фактам. Если поле сменило
    значение (`fact_events[i].previous_value` или более ранний `value` того же
    пути), прежнее значение в текущих фактах уже не найти. Здесь оно берётся
    из журнала событий, а правила маскирования не дублируются: для каждого
    прежнего значения собирается синтетический факт и прогоняется через тот
    же `Masker.from_facts`, что и текущие данные, — сюда вливается только его
    словарь замен `direct`.
    """
    raw_client = current_facts.get("client")
    current_client: dict[str, Any] = raw_client if isinstance(raw_client, dict) else {}
    # Текущее обращение по имени передаётся в синтетический факт, чтобы
    # `from_facts` по-прежнему не маскировал ту часть прежнего полного ФИО,
    # которая совпадает с именем, которым сейчас обращаются к клиенту, —
    # тем же способом, каким он уже щадит её в текущих фактах.
    current_display_name = client_display_name(current_facts)
    current_city = current_client.get("city")

    seen_values: dict[str, set[str]] = {path: set() for path in _PERSONAL_FACT_PATHS}
    for event in events:
        if not isinstance(event, dict):
            continue
        path = event.get("path")
        if path not in seen_values:
            continue
        for key in ("value", "previous_value"):
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                seen_values[path].add(value)

    direct: dict[str, str] = {}
    for value in seen_values["client.name"]:
        synthetic = {"client": {"name": value, "display_name": current_display_name}}
        direct.update(Masker.from_facts(synthetic).direct)
    for value in seen_values["client.phone"]:
        direct.update(Masker.from_facts({"client": {"phone": value}}).direct)
    for value in seen_values["client.region"]:
        synthetic = {"client": {"region": value, "city": current_city}}
        direct.update(Masker.from_facts(synthetic).direct)
    return direct


def _context_masker(facts: dict[str, Any]) -> Masker:
    """Remember original private values before closing the model fact projection."""
    masker = Masker.from_facts(facts)
    masker.direct_first = True
    private_values: list[tuple[str, str]] = []
    # Keep the meaning of the answer while hiding the actual personal value.
    if masker.first_name:
        private_values.append((masker.first_name, "[имя скрыто]"))
    client = facts.get("client")
    if isinstance(client, dict):
        labels = {
            "name": "[имя скрыто]", "display_name": "[имя скрыто]",
            "phone": "[телефон]",
            "registration_address": "[точный адрес скрыт]", "residence_address": "[точный адрес скрыт]",
            "region": "[город или регион скрыт]", "city": "[город или регион скрыт]",
            "birthdate": "[дата рождения скрыта]",
        }
        for field, label in labels.items():
            raw = client.get(field)
            if isinstance(raw, str) and raw.strip():
                private_values.append((raw, label))
    labels_by_value: dict[str, str] = {}
    for raw, label in private_values:
        key = raw.casefold()
        # A shared value such as a person's name and a city cannot identify its field.
        previous = labels_by_value.get(key, label)
        labels_by_value[key] = label if previous == label else "[сведения анкеты]"
    # Override existing case variants too, before adding the remaining aliases.
    for raw in masker.direct:
        if (label := labels_by_value.get(raw.casefold())) is not None:
            masker.direct[raw] = label
    masker.direct.update(labels_by_value)
    return masker


def mask_payload_for_llm(payload: dict[str, Any], enable: bool) -> dict[str, Any]:
    """Единая точка обезличивания для conversation и extraction вызовов."""
    context = payload.get("current_context_readonly") is True
    if not enable and not context:
        return payload
    facts = payload.get("facts")
    typed = facts if isinstance(facts, dict) else {}
    masker = _context_masker(typed) if context else Masker.from_facts(typed)
    masked = masker.mask_payload(payload)
    if context:
        # Strip raw fields only AFTER redacting their original values in text/history.
        masked["facts"] = project_context_state({"facts": typed})["facts"]
    return masked


def mask_text_with_facts(text: str, facts: dict[str, Any] | None) -> str:
    """Маскирует одну логируемую реплику теми же правилами, что LLM payload."""
    return Masker.from_facts(facts).mask_text(text)


def mask_session_snapshot(
    state: dict[str, Any], anketa: dict[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Готовит обезличенный диагностический snapshot.

    Снапшоты в v1 не восстанавливаются после рестарта, поэтому хранить в них
    телефон, полное ФИО и точный адрес нет функциональной необходимости.
    """
    facts = state.get("facts")
    typed_facts = facts if isinstance(facts, dict) else {}
    private_context = context_delivery_held(state) or debt_delivery_held(state)
    masker = _context_masker(typed_facts) if private_context else Masker.from_facts(typed_facts)
    # `Masker.from_facts` знает только ТЕКУЩЕЕ значение персональных полей.
    # Если за сессию имя, телефон или адрес поменялись (reducer перезаписывает
    # `client.name` новым значением — interpretation/reducer.py), прежнее
    # значение маскировщику неизвестно и уходит в снимок открытым текстом: и
    # в `fact_events.previous_value`, и в реплике клиента из `chat_history`,
    # где он это прежнее значение и назвал. Ревью 10.09.2026: состояние с
    # `facts.client.name = "Ирина"` и событием, где `previous_value = "Ирина
    # Петрова"`, оставляло фамилию читаемой — старый тест не ловил это только
    # потому, что в нём текущее и прежнее значение случайно совпадали.
    #
    # Чиним одним механизмом, а не двумя заплатками на evidence и на
    # chat_history по отдельности: словарь замен основного маскировщика
    # пополняется вариантами из истории фактов ДО того, как он применяется к
    # снимку целиком, — дальше факты, chat_history, fact_events и
    # `_last_user_message` маскируются как обычно, одним и тем же `masker`.
    raw_events = state.get("fact_events")
    if isinstance(raw_events, list):
        masker.direct.update(_historical_direct_replacements(raw_events, typed_facts))
    safe_state = copy.deepcopy(state)
    if private_context:
        safe_state = project_context_state(safe_state)
    # Current-context authority/digests are server metadata, not model/diagnostic data.
    safe_state.pop("customer_context", None)
    safe_state.pop("onloader", None)
    projected_facts = safe_state.get("facts") if private_context else typed_facts
    safe_state["facts"] = masker.mask_facts(projected_facts if isinstance(projected_facts, dict) else {})
    history = state.get("chat_history")
    if isinstance(history, list):
        safe_state["chat_history"] = masker.mask_history(history)
    summary = state.get("internal_summary")
    if isinstance(summary, str):
        safe_state["internal_summary"] = masker.mask_text(summary)
    # Реплика хода лежит в state сырой: по ней `core.turn_instruction` решает,
    # прощается клиент или отвечает. Ключ временный и в базу не уезжает, но
    # снимок на диске берёт state целиком и переписывается каждый ход — а
    # именно в реплике клиент и называет ФИО с телефоном. Тот же промах уже
    # стоил утечки 18.08.2026, см. комментарий про delivery ниже.
    last_message = state.get("_last_user_message")
    if isinstance(last_message, str):
        safe_state["_last_user_message"] = masker.mask_text(last_message)
    # Журнал смысловой интерпретации: `evidence` — дословный фрагмент реплики,
    # а `value` и `previous_value` — сами значения фактов, то есть ФИО и
    # телефон. Сначала здесь скрывалось только `evidence`, и проверка 10.09.2026
    # нашла в снимке полное ФИО в `previous_value`: модель предложила имя, и
    # reducer записал, что было до него. Обход слепой, как у delivery ниже:
    # новое поле события не должно открывать новую утечку.
    events = safe_state.get("fact_events")
    if isinstance(events, list):
        safe_state["fact_events"] = _mask_deep(events, masker)
    # Defense in depth: production extractor уже видит masked payload, но snapshot
    # не должен полагаться только на корректность одного вызывающего кода.
    interpretation = safe_state.get("interpretation")
    if isinstance(interpretation, dict):
        last_result = interpretation.get("last_result")
        if isinstance(last_result, dict):
            candidates = last_result.get("candidates")
            if isinstance(candidates, list):
                for candidate in candidates:
                    if not isinstance(candidate, dict):
                        continue
                    for key in ("evidence", "value"):
                        value = candidate.get(key)
                        if isinstance(value, str):
                            candidate[key] = masker.mask_text(value)

    # Исходящий payload лида. Здесь ПД лежат намеренно — CRM нужен настоящий
    # телефон, — но в снимок на диске они попадать не должны. Обнаружено 18.08.2026:
    # снимки, скопированные из контейнера, унесли реальный номер клиента в чужой
    # репозиторий. Поэтому обход слепой: любая строка внутри delivery проходит
    # через mask_text, и новый ключ в контракте не создаёт новую утечку.
    delivery = safe_state.get("delivery")
    if delivery is not None:
        safe_state["delivery"] = _mask_deep(delivery, masker)

    safe_anketa = _mask_anketa(anketa, masker) if isinstance(anketa, dict) else None
    return safe_state, safe_anketa


def _mask_deep(value: Any, masker: Masker) -> Any:
    """Прогоняет каждую строку вложенной структуры через mask_text.

    Ключ `phone` скрывается по имени, не разбирая значение: в CRM телефон
    уезжает голыми цифрами (`79295995335`), а такую строку текстовый шаблон
    отличить от суммы или идентификатора не может.
    """
    if isinstance(value, str):
        return masker.mask_text(value)
    if isinstance(value, dict):
        return {
            key: "[телефон]"
            if "phone" in str(key).lower() and isinstance(item, str) and item
            else _mask_deep(item, masker)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_mask_deep(item, masker) for item in value]
    return value


def _mask_anketa(anketa: dict[str, Any], masker: Masker) -> dict[str, Any]:
    safe = copy.deepcopy(anketa)
    for key, value in list(safe.items()):
        if not isinstance(value, str):
            continue
        normalized_key = key.lower()
        if "phone" in normalized_key or "телефон" in normalized_key:
            safe[key] = "[телефон]"
        elif normalized_key in {"full_name", "name", "фио"}:
            safe[key] = value.strip().split()[0] if value.strip() else ""
        elif "address" in normalized_key or "адрес" in normalized_key:
            safe[key] = value.split(",")[0].strip() + " (точный адрес скрыт)"
        elif "birth" in normalized_key or "рожд" in normalized_key:
            safe[key] = _DATE_PATTERN.sub(_mask_date_to_year, value)
        else:
            safe[key] = masker.mask_text(value)
    return safe
