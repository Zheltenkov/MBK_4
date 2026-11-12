"""Применение результата DaData к фактам диалога."""
from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Callable

from routing import Matrix, get_fact
from routing.normalize import (
    classify_region_confident,
    locality_key as _locality_key,
    normalized_text as _normalized,
    region_key as _region_key,
)

from .dadata import AddressCandidate, AddressResolution, get_dadata_client


Resolver = Callable[[str], AddressResolution]


@dataclass(frozen=True)
class _LocationScope:
    root_path: str
    region_path: str
    city_path: str
    class_path: str
    location_path: str


_SCOPES = (
    _LocationScope(
        root_path="client",
        region_path="client.region",
        city_path="client.city",
        class_path="client.region_class",
        location_path="client.location",
    ),
    _LocationScope(
        root_path="assets.realty",
        region_path="assets.realty.region",
        city_path="assets.realty.city",
        class_path="assets.realty.region_class",
        location_path="assets.realty.location",
    ),
    # Второй объект. Без этого скоупа у него не бывает названия города вовсе:
    # матрица спрашивает «в каком регионе и городе находится этот объект», а
    # ответ ложился только в наш внутренний класс региона. Маппинг лида читает
    # именно название, поэтому в CRM уходил объект без региона — а запрос без
    # региона даёт category=null (проба заказчика 11.08.2026). Обнаружено
    # аудитом 19.08.2026.
    _LocationScope(
        root_path="assets.realty_2",
        region_path="assets.realty_2.region",
        city_path="assets.realty_2.city",
        class_path="assets.realty_2.region_class",
        location_path="assets.realty_2.location",
    ),
)

_MOSCOW_ISO_CODES = {"RU-MOW", "RU-MOS"}
_CAPITAL_REPLY_RE = re.compile(r"\bстолиц(?:а|е|у|ы|ей|ою)?\b", re.IGNORECASE)
_CITY_REPLY_RE = re.compile(r"(?:^|\s)(?:г|город)\s+", re.IGNORECASE)
_SETTLEMENT_REPLY_RE = re.compile(
    r"(?:^|\s)(?:д|деревня|с|село|п|поселок|посёлок|пгт|рп)\s+",
    re.IGNORECASE,
)

# Сколько вариантов вообще показывается клиенту (см. _location_confirmation_instruction).
# Порядковый ответ не может указывать на то, чего человек не видел.
_MAX_OFFERED_CANDIDATES = 5

# Сколько раз уточняем одноимённый населённый пункт, прежде чем передать вопрос
# специалисту. Тот же порог использует планировщик, чтобы шагнуть дальше по анкете.
_MAX_LOCATION_ATTEMPTS = 3

# Порядковый выбор принимаем только в явной форме. Голые цифры сюда не входят
# намеренно: «нужно 2 млн» не должно молча выбрать второй регион из списка.
_ORDINAL_WORDS = ("перв", "втор", "трет", "четверт", "пят")
_ORDINAL_WORD_RE = re.compile(
    r"(?:^|\s)(" + "|".join(_ORDINAL_WORDS) + r")\w*",
    re.IGNORECASE,
)
_ORDINAL_NUMBER_RE = re.compile(
    r"(?:вариант|пункт)\s*(?:№\s*)?([1-5])\b|\b([1-5])\s*(?:вариант|пункт)",
    re.IGNORECASE,
)


def enrich_locations(
    facts: dict[str, Any],
    latest_user_message: str,
    matrix: Matrix,
    *,
    resolver: Resolver | None = None,
    asked_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Стандартизирует новые адресные ответы и сохраняет неоднозначность в state.

    `asked_paths` — о чём бот спрашивал прошлым ходом. Попытка уточнить адрес
    засчитывается, только если спрашивал про это место: иначе ходы о другом
    довели бы до «подключу специалиста» невпопад. `None` — не проверять.
    """
    updated = deepcopy(facts)
    resolve = resolver or get_dadata_client().resolve
    client_scope = _SCOPES[0]

    for scope in _SCOPES:
        location = get_fact(updated, scope.location_path)
        if (
            isinstance(location, dict)
            and location.get("status") in {"not_found", "unavailable"}
        ):
            current_query = _location_query(updated, scope)
            previous_query = str(location.get("query") or "")
            if (
                location.get("confirmation_required")
                and latest_user_message.strip()
                and _normalized(current_query) == _normalized(previous_query)
                and (asked_paths is None or scope.class_path in asked_paths)
            ):
                # Ответ не дал нового адреса («зачем?», «не помню»). Считаем
                # попытку, как у неоднозначного города: без счётчика вопрос
                # звучал тем же текстом, и модель повторяла его слово в слово
                # (ролевой прогон 28.09.2026).
                attempts = _nonnegative_int(location.get("attempts")) + 1
                location["attempts"] = attempts
                if attempts == _MAX_LOCATION_ATTEMPTS:
                    location["handoff_required"] = True
                elif attempts > _MAX_LOCATION_ATTEMPTS:
                    location["handoff_required"] = False
                continue
            if current_query and _normalized(current_query) != _normalized(
                previous_query
            ):
                root = _nested_dict(updated, scope.root_path)
                latest = _normalized(latest_user_message)
                region = _normalized(str(root.get("region") or ""))
                city = _normalized(str(root.get("city") or ""))
                # Новый raw fastpath мог добавить region рядом со старым city
                # из неудачного запроса. Оставляем только компонент, который
                # клиент назвал сейчас, иначе DaData получит смешанный адрес.
                if latest and region == latest and city != latest:
                    root.pop("city", None)
                elif latest and city == latest and region != latest:
                    root.pop("region", None)
                root.pop("region_class", None)
                root.pop("location", None)
                location = None
        if isinstance(location, dict) and location.get("status") == "ambiguous":
            selected = _select_candidate(location.get("candidates"), latest_user_message)
            if selected is not None:
                _apply_candidate(
                    updated,
                    scope,
                    selected,
                    matrix,
                    query=str(location.get("query") or ""),
                )
                continue
            current_query = _location_query(updated, scope)
            previous_query = str(location.get("query") or "")
            if current_query and _normalized(current_query) != _normalized(
                previous_query
            ):
                # Extractor уже увидел явную поправку («нет, теперь Москва»).
                # Старый список Советсков к ней не относится: удаляем только
                # ambiguity metadata и ниже резолвим новый city/region.
                root = _nested_dict(updated, scope.root_path)
                root.pop("location", None)
                location = None
            elif latest_user_message.strip():
                # Клиент ответил, но выбрать вариант по ответу не удалось. Считаем
                # попытки, чтобы следующий вопрос прозвучал иначе, а не тем же текстом.
                attempts = _nonnegative_int(location.get("attempts")) + 1
                location["attempts"] = attempts
                if attempts == _MAX_LOCATION_ATTEMPTS:
                    # Ровно на этом ходу сдаёмся и говорим про специалиста. Дальше
                    # анкета идёт без региона, иначе диалог не закончится никогда.
                    location["handoff_required"] = True
                elif attempts > _MAX_LOCATION_ATTEMPTS:
                    location["handoff_required"] = False
                continue

        query = _location_query(updated, scope)
        if not query:
            continue
        if (
            isinstance(location, dict)
            and location.get("status") == "resolved"
            and location.get("canonical_query") == query
        ):
            continue
        if isinstance(location, dict) and location.get("query") == query:
            continue

        if scope is not client_scope and _SAME_PLACE_RE.match(_normalized(query)):
            same_place = _same_place(updated, scope)
            if same_place is not None:
                _apply_candidate(updated, scope, same_place, matrix, query=query)
                continue

        # Окрестности проверяются раньше, чем «тот же город, что у клиента»:
        # живёт в Воронеже, дом «в деревне под Воронежем» — это не Воронеж.
        vicinity = _vicinity_city_names(query) or _vicinity_named_in_message(
            latest_user_message, query
        )
        if not vicinity and scope is not client_scope:
            reused = _reuse_client_resolution(updated, client_scope, scope)
            if reused is not None:
                _apply_candidate(updated, scope, reused, matrix, query=query)
                continue

        if vicinity:
            region_only = _vicinity_region(vicinity, resolve)
            if region_only is not None:
                _apply_candidate(updated, scope, region_only, matrix, query=query)
                continue

        resolution = _resolve_in_city(query, resolve) or resolve(query)
        if _is_not_a_city(resolution):
            # Предлог мог убрать разборщик: «Казани, Татарстан». Обычный поиск
            # отдал не город — пробуем начальную форму первого слова.
            resolution = _resolve_in_city(f"в {query}", resolve) or resolution
        if resolution.status == "resolved" and resolution.candidate is not None:
            _apply_candidate(
                updated,
                scope,
                resolution.candidate,
                matrix,
                query=resolution.query,
            )
        elif resolution.status == "ambiguous":
            default_candidate = _default_candidate(resolution) or _region_consensus(
                resolution
            )
            if default_candidate is not None:
                _apply_candidate(
                    updated,
                    scope,
                    default_candidate,
                    matrix,
                    query=resolution.query,
                )
            else:
                _apply_ambiguity(updated, scope, resolution)
        else:
            _apply_unavailable(updated, scope, resolution, matrix)

    return updated


# «Где находится дом?» — «Где и живу», «Там же». Бой 05.10.2026, сессия
# a4dd48655572: бот ответил «мне нужен именно населённый пункт с областью или
# краем», хотя город клиент назвал двумя минутами раньше.
_SAME_PLACE_RE = re.compile(
    r"^(?:(?:да|ну)\s+)?(?:"
    r"там\s+же(?:\s+где\s+(?:и\s+)?(?:я\s+)?(?:живу|прописан\w*))?"
    r"|тут\s+же|здесь\s+же|тоже\s+(?:там|тут|здесь)"
    r"|(?:там\s+)?где\s+(?:и\s+)?(?:я\s+)?(?:и\s+)?(?:живу|прописан\w*)"
    r"|в\s+том\s+же\s+(?:городе|селе|поселке|месте|доме)"
    r"|по\s+месту\s+(?:жительства|прописки)"
    r")$"
)


def _same_place(
    facts: dict[str, Any],
    scope: _LocationScope,
) -> AddressCandidate | None:
    """Место, на которое клиент сослался словами «там же».

    Для первого объекта это место жительства. Для второго — первый объект, а
    если его адрес не разобран, то место жительства: спрашивали только что про
    недвижимость, и «там же» читается как «там же, где первая».
    """
    client_scope, realty_scope = _SCOPES[0], _SCOPES[1]
    references = (
        (realty_scope, client_scope) if scope.root_path == "assets.realty_2" else (client_scope,)
    )
    for reference in references:
        location = get_fact(facts, reference.location_path)
        # Адрес есть только у разобранного места: у неоднозначного и
        # ненайденного `_candidate_from_dict` вернёт None.
        candidate = _candidate_from_dict(location) if isinstance(location, dict) else None
        if candidate is not None:
            return candidate
    return None


def _reuse_client_resolution(
    facts: dict[str, Any],
    client_scope: _LocationScope,
    scope: _LocationScope,
) -> AddressCandidate | None:
    """Не переспрашивает уже решённую неоднозначность одноимённого города.

    Раньше `client.location` и `assets.realty.location` были полностью независимыми:
    если клиент подтвердил регион для своего города, а объект недвижимости назвали
    в том же городе без отдельного региона, второй скоуп всё равно гонял DaData
    заново и мог заново упереться в ту же неоднозначность — с точки зрения клиента
    это выглядело как повторный вопрос про регион, который он уже назвал.
    """
    client_location = get_fact(facts, client_scope.location_path)
    if not isinstance(client_location, dict) or client_location.get("status") != "resolved":
        return None

    scope_city = get_fact(facts, scope.city_path)
    client_city = get_fact(facts, client_scope.city_path)
    if not isinstance(scope_city, str) or not isinstance(client_city, str):
        return None
    # `client.city` уже стандартизирован DaData («г Советск»), необработанный ответ
    # второго скоупа — ещё нет («Советск»). Сравниваем через `_locality_key`, а не
    # `_normalized`, иначе префикс типа населённого пункта ломает совпадение.
    if _locality_key(scope_city) != _locality_key(client_city):
        return None

    # Если для этого скоупа назвали свой регион, доверяем ему, а не месту жительства.
    scope_region = get_fact(facts, scope.region_path)
    if isinstance(scope_region, str) and scope_region.strip():
        return None

    return _candidate_from_dict(client_location)


# «Дом в деревне под Воронежем» — прод и ролевой прогон 28.09.2026. DaData на
# «под Воронежем» не находит ничего (город в творительном падеже), и бот
# требовал название деревни, повторяя одну фразу слово в слово. Из «под X»,
# «около X», «рядом с X» берём город X в начальной форме и по нему — область.
# Сам город не подставляем: деревня рядом с Воронежем — не Воронеж, и в списки
# маршрутов по городам она не входит.
_VICINITY_RE = re.compile(
    r"(?<![а-яё])(под|около|возле|рядом\s+со?|недалеко\s+от|в\s+районе)"
    r"\s+([а-яё][а-яё\-]+)",
    re.IGNORECASE,
)
# После «под», «рядом с» — творительный падеж, после «около», «возле»,
# «недалеко от», «в районе» — родительный. Окончание → начальная форма.
_INSTRUMENTAL_ENDINGS = (("ью", "ь"), ("ой", "а"), ("ей", "я"), ("ем", ""), ("ом", ""))
_GENITIVE_ENDINGS = (("ы", "а"), ("и", "ь"), ("и", "я"), ("а", ""), ("я", "ь"))
_MAX_VICINITY_LOOKUPS = 3


def _vicinity_city_names(text: str) -> list[str]:
    """«под Воронежем» → ["Воронеж"]: возможные начальные формы города."""
    match = _VICINITY_RE.search(text or "")
    if match is None:
        return []
    preposition = match.group(1).split()[0].lower()
    word = match.group(2)
    endings = (
        _INSTRUMENTAL_ENDINGS if preposition in {"под", "рядом"} else _GENITIVE_ENDINGS
    )
    names: list[str] = []
    for ending, replacement in endings:
        lowered = word.lower()
        if lowered.endswith(ending) and len(lowered) > len(ending) + 1:
            name = (lowered[: -len(ending)] + replacement).capitalize()
            if name not in names:
                names.append(name)
    return names[:_MAX_VICINITY_LOOKUPS]


def _vicinity_named_in_message(message: str, query: str) -> list[str]:
    """Клиент сказал «под Воронежем», а разборщик оставил от этого «Воронеж».

    Мутационный прогон 04.10.2026: дом «в деревне под Воронежем» получил
    класс города из списка. Пока «Воронеж» без области был неоднозначным, бот
    переспрашивал и клиент поправлял; с выбором столицы региона по умолчанию
    эта страховка исчезла. Слова клиента сильнее сокращения разборщика — но
    только когда они про тот же город, что и запрос.
    """
    parts = {_locality_key(part) for part in query.split(",") if part.strip()}
    return [name for name in _vicinity_city_names(message) if _locality_key(name) in parts]


# «Под Москвой» — это Московская область, а не город Москва: у городов
# федерального значения окрестности лежат в соседнем субъекте.
_FEDERAL_SURROUNDINGS = {
    "москва": "Московская область",
    "санкт-петербург": "Ленинградская область",
    "севастополь": "Республика Крым",
}


# «Где квартира?» — «В Казани». Город в предложном падеже DaData не узнаёт:
# на «В Казани, Татарстан» находилась только республика, квартира получала
# класс «остальная РФ» вместо списка городов, и клиент терял залоговый маршрут
# (мутационный прогон 01.10.2026). Пробуем начальную форму: «Казани» → «Казань».
_IN_CITY_RE = re.compile(r"^(?:в|во)\s+([а-яё][а-яё\-]+)(.*)$", re.IGNORECASE)
_PREPOSITIONAL_ENDINGS = (
    ("ах", "ы"),
    ("ях", "и"),
    ("и", "ь"),
    ("е", "а"),
    ("е", ""),
    ("и", "я"),
)
_MAX_IN_CITY_LOOKUPS = 3


def _in_city_queries(query: str) -> list[tuple[str, str]]:
    """«В Казани, Татарстан» → [("Казань", "Казань, Татарстан"), …]."""
    match = _IN_CITY_RE.match((query or "").strip())
    if match is None:
        return []
    word, rest = match.group(1).lower(), match.group(2)
    variants: list[tuple[str, str]] = []
    for ending, replacement in _PREPOSITIONAL_ENDINGS:
        if word.endswith(ending) and len(word) > len(ending) + 1:
            name = (word[: -len(ending)] + replacement).capitalize()
            if all(name != known for known, _ in variants):
                variants.append((name, f"{name}{rest}"))
    return variants[:_MAX_IN_CITY_LOOKUPS]


def _resolve_in_city(query: str, resolve: Resolver) -> AddressResolution | None:
    """Разрешение с городом в начальной форме — если такой город нашёлся."""
    for name, fixed in _in_city_queries(query):
        resolution = resolve(fixed)
        if any(
            _locality_key(candidate.city or "") == _locality_key(name)
            for candidate in _found(resolution)
        ):
            return resolution
    return None


def _is_not_a_city(resolution: AddressResolution) -> bool:
    """Нашёлся ровно один адрес, и это не город, а что-то внутри или мимо."""
    candidate = resolution.candidate
    if resolution.status != "resolved" or candidate is None:
        return False
    return bool(candidate.settlement) or not candidate.city


def _found(resolution: AddressResolution) -> list[AddressCandidate]:
    if resolution.candidates:
        return list(resolution.candidates)
    return [resolution.candidate] if resolution.candidate is not None else []


def _region_only(candidate: AddressCandidate) -> AddressCandidate:
    """Тот же субъект, но без населённого пункта."""
    region = candidate.region_with_type or candidate.region
    return replace(
        candidate,
        value=region or "",
        unrestricted_value=region,
        city=None,
        city_with_type=None,
        city_fias_id=None,
        settlement=None,
        settlement_with_type=None,
        settlement_fias_id=None,
        fias_id=candidate.region_fias_id,
        fias_level=1,
    )


def _vicinity_region(
    names: list[str], resolve: Resolver
) -> AddressCandidate | None:
    """Область города из «под X»: кандидат без населённого пункта."""
    for name in names:
        surroundings = _FEDERAL_SURROUNDINGS.get(name.lower())
        if surroundings is not None:
            found = _found(resolve(surroundings))
            if found:
                return _region_only(found[0])
            continue
        for candidate in _found(resolve(name)):
            if _locality_key(candidate.city or "") == _locality_key(name):
                return _region_only(candidate)
    return None


# Голое «Крым» DaData разрешает в село Крым Ростовской области — и бот на «живу
# в Крыму» ответил клиентке «вижу, вы в селе Крым Ростовской области, а не на
# полуострове» (прод 28.09.2026). Так говорят о республике; село называют с
# районом или областью, и такой адрес сюда не подпадает.
_CRIMEA_RE = re.compile(
    r"^(?:в\s+)?(?:(?:республик[аеи]|респ\.?|рк|полуостр[оа]в[еа]?)\s+)?крым[уа]?$",
    re.IGNORECASE,
)
_CRIMEA = "Республика Крым"


def _location_query(facts: dict[str, Any], scope: _LocationScope) -> str:
    values: list[str] = []
    for path in (scope.city_path, scope.region_path):
        value = get_fact(facts, path)
        if isinstance(value, str):
            normalized = " ".join(value.split())
            if _CRIMEA_RE.match(normalized):
                normalized = _CRIMEA
            if normalized and normalized not in values:
                values.append(normalized)
    return ", ".join(values)


def _apply_candidate(
    facts: dict[str, Any],
    scope: _LocationScope,
    candidate: AddressCandidate,
    matrix: Matrix,
    *,
    query: str,
) -> None:
    root = _nested_dict(facts, scope.root_path)
    root["region_class"] = _classify_candidate(
        candidate,
        matrix,
        include_realty_list=scope.root_path == "assets.realty",
    )
    root["region"] = candidate.region_with_type or candidate.region
    root["city"] = candidate.locality_with_type
    canonical_query = _location_query(facts, scope)
    root["location"] = {
        "status": "resolved",
        "query": query or None,
        "canonical_query": canonical_query,
        "confirmation_required": False,
        **candidate.as_dict(),
    }


def _apply_ambiguity(
    facts: dict[str, Any],
    scope: _LocationScope,
    resolution: AddressResolution,
) -> None:
    root = _nested_dict(facts, scope.root_path)
    # Старый класс нельзя оставлять после нового неоднозначного ответа.
    root.pop("region_class", None)
    root["location"] = {
        "status": "ambiguous",
        "query": resolution.query,
        "confirmation_required": True,
        "candidates": [candidate.as_dict() for candidate in resolution.candidates],
    }


def _apply_unavailable(
    facts: dict[str, Any],
    scope: _LocationScope,
    resolution: AddressResolution,
    matrix: Matrix,
) -> None:
    root = _nested_dict(facts, scope.root_path)
    root["location"] = {
        "status": resolution.status,
        "query": resolution.query,
        "confirmation_required": True,
        "error": resolution.error,
        "candidates": [],
    }
    # Справочники регионов лежат в репозитории, поэтому при сбое API классификатор
    # всё ещё может ответить по существу — но только когда он в ответе уверен.
    local_class = classify_region_confident(
        resolution.query,
        matrix,
        include_realty_list=scope.root_path == "assets.realty",
    )
    if local_class is not None:
        root["region_class"] = local_class
    else:
        # «Не знаю» — не повод оставить прежний класс: он относится к прежнему
        # адресу. Устаревшая «остальная Россия» у подмосковного города увела бы
        # клиента по чужому маршруту, а это дороже лишнего вопроса.
        root.pop("region_class", None)


def _select_candidate(raw_candidates: Any, reply: str) -> AddressCandidate | None:
    if not isinstance(raw_candidates, list) or not reply.strip():
        return None
    normalized_reply = _normalized(reply)
    candidates = [
        candidate
        for raw in raw_candidates
        if isinstance(raw, dict)
        if (candidate := _candidate_from_dict(raw)) is not None
    ]

    # «Первый», «второй вариант» — нормальный ответ живого человека на список.
    # Без него любая реплика без буквального названия региона оставляла
    # неоднозначность, и бот спрашивал то же самое снова.
    ordinal = _ordinal_choice(reply)
    if ordinal is not None and ordinal < min(len(candidates), _MAX_OFFERED_CANDIDATES):
        return candidates[ordinal]

    # «В столице» — однозначное подтверждение Москвы как субъекта РФ, а не одной
    # из одноимённых деревень. Раньше ни один marker не совпадал, и бот снова
    # спрашивал регион через несколько ходов.
    if _CAPITAL_REPLY_RE.search(normalized_reply):
        capital = [
            candidate
            for candidate in candidates
            if candidate.region_iso_code == "RU-MOW"
            and _normalized(candidate.city or candidate.region or "") == "москва"
        ]
        if len(capital) == 1:
            return capital[0]

    # Явное «город Москва» должно сравниваться только с полем city. Иначе голое
    # имя «Москва» одновременно совпадает с городом и деревнями.
    if _CITY_REPLY_RE.search(normalized_reply):
        return _select_by_locality_kind(candidates, normalized_reply, kind="city")
    if _SETTLEMENT_REPLY_RE.search(normalized_reply):
        return _select_by_locality_kind(candidates, normalized_reply, kind="settlement")

    # Сначала учитываем явно названный субъект. Иначе слово «Казань» совпадает и
    # с городом в Татарстане, и с одноимёнными деревнями Кировской области,
    # несмотря на фразу клиента «Республика Татарстан».
    normalized_region_reply = _region_key(reply)
    region_matched = [
        candidate
        for candidate in candidates
        if any(
            marker and re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", normalized_region_reply)
            for marker in {
                _region_key(value)
                for value in (
                    candidate.region,
                    candidate.region_with_type,
                )
                if value
            }
        )
    ]
    if len(region_matched) == 1:
        return region_matched[0]
    if region_matched:
        candidates = region_matched

    matched: list[AddressCandidate] = []
    for candidate in candidates:
        markers = {
            _normalized(value)
            for value in (
                candidate.region,
                candidate.region_with_type,
                candidate.city,
                candidate.city_with_type,
                candidate.settlement,
                candidate.settlement_with_type,
                candidate.display_name,
            )
            if value
        }
        if any(marker and marker in normalized_reply for marker in markers):
            matched.append(candidate)
    return matched[0] if len(matched) == 1 else None


def _nonnegative_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _ordinal_choice(reply: str) -> int | None:
    """«Второй вариант» → индекс 1. Возвращает None, если выбора в реплике нет."""
    match = _ORDINAL_NUMBER_RE.search(reply)
    if match:
        digits = match.group(1) or match.group(2)
        return int(digits) - 1

    normalized = _normalized(reply)
    word = _ORDINAL_WORD_RE.search(normalized)
    if word:
        return _ORDINAL_WORDS.index(word.group(1))
    return None


def _default_candidate(resolution: AddressResolution) -> AddressCandidate | None:
    """Применяет согласованную бизнес-policy для голого названия «Москва».

    Без области «Москва» почти всегда означает столицу. Поэтому уникальный кандидат
    RU-MOW выбирается сразу; «Москва, Тверская область» сюда не попадёт и останется
    обычным подтверждением одноимённого населённого пункта.
    """
    if not _is_bare_moscow_query(resolution.query):
        return None
    capital = [
        candidate
        for candidate in resolution.candidates
        if candidate.region_iso_code == "RU-MOW"
        and _normalized(candidate.city or candidate.region or "") == "москва"
    ]
    return capital[0] if len(capital) == 1 else None


def _region_consensus(resolution: AddressResolution) -> AddressCandidate | None:
    """Регион известен, хотя населённый пункт — нет.

    «Деревня под Тверью» даёт десяток разных деревень, и все они в Тверской
    области. Уточнять название незачем: маршрут выбирается по региону, и он
    определён с первого ответа. 12.08.2026 живой прогон на этом терял четыре
    хода — бот трижды переспрашивал, перечислял чужие деревни и сдавался, а
    регион всё равно оставался пустым.

    Населённый пункт при этом не выдумывается: в фактах остаётся только регион.
    Подставить первую попавшуюся деревню значило бы отправить в CRM адрес,
    которого клиент не называл.
    """
    if not resolution.candidates:
        return None
    regions = {
        candidate.region_fias_id
        for candidate in resolution.candidates
        if candidate.region_fias_id
    }
    if len(regions) != 1:
        return None
    first = resolution.candidates[0]
    return replace(
        first,
        value=first.region_with_type or first.region or first.value,
        unrestricted_value=first.region_with_type or first.unrestricted_value,
        city=None,
        city_with_type=None,
        city_fias_id=None,
        settlement=None,
        settlement_with_type=None,
        settlement_fias_id=None,
        fias_id=first.region_fias_id,
        fias_level=1,
    )


def _is_bare_moscow_query(query: str) -> bool:
    parts = {
        re.sub(r"^(?:г|город)\s+", "", _normalized(part))
        for part in query.split(",")
        if _normalized(part)
    }
    return parts == {"москва"}


def _select_by_locality_kind(
    candidates: list[AddressCandidate],
    normalized_reply: str,
    *,
    kind: str,
) -> AddressCandidate | None:
    """Выбирает только среди городов либо только среди поселений."""
    matched: list[AddressCandidate] = []
    for candidate in candidates:
        values = (
            (candidate.city, candidate.city_with_type)
            if kind == "city"
            else (candidate.settlement, candidate.settlement_with_type)
        )
        markers = {_normalized(value) for value in values if value}
        # Полное название с типом и голое имя оба допустимы: тип уже зафиксирован
        # самой репликой («город …» / «деревня …»).
        if any(marker and marker in normalized_reply for marker in markers):
            matched.append(candidate)
    return matched[0] if len(matched) == 1 else None


def _candidate_from_dict(raw: dict[str, Any]) -> AddressCandidate | None:
    region_fias_id = raw.get("region_fias_id")
    if not isinstance(region_fias_id, str) or not region_fias_id:
        return None
    return AddressCandidate(
        value=str(raw.get("value") or raw.get("display_name") or ""),
        unrestricted_value=_optional_str(raw.get("unrestricted_value")),
        country=_optional_str(raw.get("country")),
        country_iso_code=_optional_str(raw.get("country_iso_code")),
        region=_optional_str(raw.get("region")),
        region_with_type=_optional_str(raw.get("region_with_type")),
        region_fias_id=region_fias_id,
        region_iso_code=_optional_str(raw.get("region_iso_code")),
        city=_optional_str(raw.get("city")),
        city_with_type=_optional_str(raw.get("city_with_type")),
        city_fias_id=_optional_str(raw.get("city_fias_id")),
        settlement=_optional_str(raw.get("settlement")),
        settlement_with_type=_optional_str(raw.get("settlement_with_type")),
        settlement_fias_id=_optional_str(raw.get("settlement_fias_id")),
        fias_id=_optional_str(raw.get("fias_id")),
        fias_level=raw.get("fias_level") if isinstance(raw.get("fias_level"), int) else None,
    )


def _classify_candidate(
    candidate: AddressCandidate,
    matrix: Matrix,
    *,
    include_realty_list: bool,
) -> str:
    if candidate.region_iso_code in _MOSCOW_ISO_CODES:
        return "moscow_mo"
    if include_realty_list:
        # Порядок повторяет приоритет маршрутов: 1-2 (список МБК) проверяется
        # раньше 1-3 (список Кредит.Клаб). Город может быть в обоих списках —
        # тогда он должен достаться более приоритетному маршруту.
        for list_name in ("mbk_list", "realty_list"):
            if _candidate_in_location_list(
                candidate,
                matrix.regions.get(list_name, []),
            ):
                return list_name
    return "rf_other"


def _nested_dict(facts: dict[str, Any], path: str) -> dict[str, Any]:
    current = facts
    for part in path.split("."):
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    return current


def _candidate_in_location_list(
    candidate: AddressCandidate,
    configured: list[str],
) -> bool:
    candidate_regions = {
        _region_key(value)
        for value in (
            candidate.region,
            candidate.region_with_type,
            candidate.region_fias_id,
            candidate.region_iso_code,
        )
        if value
    }
    candidate_localities = {
        _locality_key(value)
        for value in (
            candidate.city,
            candidate.city_with_type,
            candidate.settlement,
            candidate.settlement_with_type,
        )
        if value
    }

    for entry in configured:
        if "\t" not in entry:
            # Обратная совместимость с прежним region-only форматом.
            if _region_key(entry) in candidate_regions:
                return True
            continue
        region, locality = entry.split("\t", 1)
        # Пустой регион — формат листа «МБК»: там заданы только города, поэтому
        # населённый пункт проверяется без привязки к субъекту.
        if region and _region_key(region) not in candidate_regions:
            continue
        configured_locality = _locality_key(locality)
        if any(
            _same_locality(configured_locality, candidate_locality)
            for candidate_locality in candidate_localities
        ):
            return True
    return False


def _same_locality(configured: str, candidate: str) -> bool:
    if configured == candidate:
        return True
    # В source встречаются уточнения «Маркова мкр Березовый». DaData может вернуть
    # базовый населённый пункт «Маркова» — считаем это той же записью по границе слова.
    return configured.startswith(candidate + " ") or candidate.startswith(configured + " ")


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None
