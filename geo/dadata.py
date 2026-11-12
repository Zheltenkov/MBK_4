"""Типизированный серверный адаптер DaData Suggestions API.

Используем подсказки, а не платную стандартизацию: для Suggestions достаточно
`MBK_DADATA_API`, который уже есть у проекта. Ответ DaData не считается выбором
пользователя автоматически, если найдено несколько разных населённых пунктов.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from collections import OrderedDict

import transport
from dataclasses import dataclass
from functools import lru_cache
from threading import Lock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


logger = logging.getLogger("mbk.geo.dadata")

# Окно бюджета: сутки. Лимит защищает от разогнавшегося цикла, а не
# запрещает работу навсегда.
_BUDGET_WINDOW_SECONDS = 24 * 60 * 60

DEFAULT_ENDPOINT = (
    "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address"
)

ResolutionStatus = Literal["resolved", "ambiguous", "not_found", "unavailable"]

# Столько же, сколько держал прежний @lru_cache над resolve.
_CACHE_MAXSIZE = 512


class _SuggestionData(BaseModel):
    """Используемая часть внешнего ответа; лишние поля DaData нас не связывают."""

    model_config = ConfigDict(extra="ignore")

    country: str | None = None
    country_iso_code: str | None = None
    region: str | None = None
    region_with_type: str | None = None
    region_fias_id: str | None = None
    region_iso_code: str | None = None
    city: str | None = None
    city_with_type: str | None = None
    city_fias_id: str | None = None
    settlement: str | None = None
    settlement_with_type: str | None = None
    settlement_fias_id: str | None = None
    fias_id: str | None = None
    fias_level: int | None = None
    # «2» — центр региона, «3» — центр региона и района. Приходит строкой.
    capital_marker: str | int | None = None


class _Suggestion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: str
    unrestricted_value: str | None = None
    data: _SuggestionData


class _SuggestionEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    suggestions: list[_Suggestion] = Field(default_factory=list)


@dataclass(frozen=True)
class AddressCandidate:
    """Канонический населённый пункт, пригодный для state и выходного JSON."""

    value: str
    unrestricted_value: str | None
    country: str | None
    country_iso_code: str | None
    region: str | None
    region_with_type: str | None
    region_fias_id: str
    region_iso_code: str | None
    city: str | None
    city_with_type: str | None
    city_fias_id: str | None
    settlement: str | None
    settlement_with_type: str | None
    settlement_fias_id: str | None
    fias_id: str | None
    fias_level: int | None
    # Нужен только на разборе ответа DaData (`_prefer_regional_capital`); в
    # state и выходной JSON не уходит, поэтому в `as_dict` его нет.
    is_regional_capital: bool = False

    @classmethod
    def from_suggestion(cls, suggestion: _Suggestion) -> "AddressCandidate | None":
        data = suggestion.data
        # Без идентификатора региона невозможно безопасно классифицировать географию.
        if not data.region_fias_id:
            return None
        return cls(
            value=suggestion.value,
            unrestricted_value=suggestion.unrestricted_value,
            country=data.country,
            country_iso_code=data.country_iso_code,
            region=data.region,
            region_with_type=data.region_with_type,
            region_fias_id=data.region_fias_id,
            region_iso_code=data.region_iso_code,
            city=data.city,
            city_with_type=data.city_with_type,
            city_fias_id=data.city_fias_id,
            settlement=data.settlement,
            settlement_with_type=data.settlement_with_type,
            settlement_fias_id=data.settlement_fias_id,
            fias_id=data.fias_id,
            fias_level=data.fias_level,
            is_regional_capital=str(data.capital_marker) in {"2", "3"},
        )

    @property
    def locality_fias_id(self) -> str:
        """Самый точный известный идентификатор населённого пункта."""
        return (
            self.settlement_fias_id
            or self.city_fias_id
            or self.fias_id
            or self.region_fias_id
        )

    @property
    def locality_with_type(self) -> str | None:
        return self.settlement_with_type or self.city_with_type

    @property
    def display_name(self) -> str:
        parts = [
            part
            for part in (self.region_with_type, self.locality_with_type)
            if part
        ]
        return ", ".join(dict.fromkeys(parts)) or self.value

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "unrestricted_value": self.unrestricted_value,
            "country": self.country,
            "country_iso_code": self.country_iso_code,
            "region": self.region,
            "region_with_type": self.region_with_type,
            "region_fias_id": self.region_fias_id,
            "region_iso_code": self.region_iso_code,
            "city": self.city,
            "city_with_type": self.city_with_type,
            "city_fias_id": self.city_fias_id,
            "settlement": self.settlement,
            "settlement_with_type": self.settlement_with_type,
            "settlement_fias_id": self.settlement_fias_id,
            "fias_id": self.fias_id,
            "fias_level": self.fias_level,
            "display_name": self.display_name,
        }


@dataclass(frozen=True)
class AddressResolution:
    status: ResolutionStatus
    query: str
    candidate: AddressCandidate | None = None
    candidates: tuple[AddressCandidate, ...] = ()
    error: str | None = None


class DadataRequestError(RuntimeError):
    """Контролируемая ошибка внешнего API с признаком допустимости retry."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class DadataClient:
    """Маленький синхронный клиент с timeout и ограниченным retry.

    Вызов происходит один раз после появления нового адресного ответа, поэтому
    синхронный I/O здесь не создаёт фоновых гонок со state диалога.
    """

    _budget_lock = Lock()
    _request_count = 0
    _window_started = 0.0

    def __init__(
        self,
        token: str | None = None,
        *,
        endpoint: str | None = None,
        timeout: float | None = None,
        max_attempts: int | None = None,
    ) -> None:
        self._token = (token or os.getenv("MBK_DADATA_API") or "").strip()
        self._endpoint = (
            endpoint
            or os.getenv("MBK_DADATA_URL")
            or DEFAULT_ENDPOINT
        ).strip()
        self._timeout = timeout or float(os.getenv("MBK_DADATA_TIMEOUT", "5"))
        self._max_attempts = max(
            1,
            max_attempts or int(os.getenv("MBK_DADATA_MAX_ATTEMPTS", "1")),
        )
        self._request_budget = max(
            0,
            int(os.getenv("MBK_DADATA_REQUEST_BUDGET", "1000")),
        )
        # Свой LRU вместо @lru_cache: решение «запоминать или нет» принимается
        # по исходу обращения, а декоратор кэширует любой возврат без разбора.
        self._cache: "OrderedDict[str, AddressResolution]" = OrderedDict()
        self._cache_lock = Lock()

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def resolve(self, query: str) -> AddressResolution:
        """Разрешает адрес; одинаковый текст не расходует повторный API-запрос."""
        normalized_query = " ".join(query.split())
        if not normalized_query:
            return AddressResolution(status="not_found", query="")
        if not self.configured:
            # Обращения не было — запоминать нечего, проверка токена бесплатна.
            return AddressResolution(
                status="unavailable",
                query=normalized_query,
                error="MBK_DADATA_API не задан",
            )

        cached = self._cached(normalized_query)
        if cached is not None:
            return cached

        # Кэш и `resolution.query` держатся слов клиента; в DaData уходит та же
        # фраза с названием региона в начальной форме.
        canonical_query = _canonical_query(normalized_query)
        try:
            envelope = self._request(canonical_query)
        except DadataRequestError as exc:
            logger.warning("DaData недоступна: %s", exc)
            resolution = AddressResolution(
                status="unavailable",
                query=normalized_query,
                error=str(exc),
            )
            # Инвариант кэша: в нём живут только окончательные результаты —
            # разбор адреса или отказ, повтор которого бессмыслен по построению
            # (исчерпан бюджет, битый ответ, 4xx). Повторимую ошибку не помним:
            # десять секунд обрыва связи иначе отравляли популярный город до
            # перезапуска процесса, и все клиенты оттуда оставались без региона,
            # то есть без маршрута. Признак берём у самой ошибки, а не по списку
            # статусов, чтобы будущие временные состояния тоже не залипали.
            if not exc.retryable:
                self._remember(normalized_query, resolution)
            return resolution

        candidates = self._unique_candidates(envelope)
        candidates = self._drop_stray_territories(candidates, canonical_query)
        named_region = self._named_region(candidates, canonical_query)
        if named_region is not None:
            candidates = [named_region]
        else:
            candidates = self._prefer_exact_locality(candidates, canonical_query)
            candidates = self._collapse_single_city(candidates)
            candidates = self._prefer_regional_capital(candidates, canonical_query)
            candidates = self._prefer_town_over_villages(candidates, canonical_query)
        if not candidates:
            resolution = AddressResolution(
                status="not_found",
                query=normalized_query,
            )
        elif len(candidates) == 1:
            resolution = AddressResolution(
                status="resolved",
                query=normalized_query,
                candidate=candidates[0],
                candidates=tuple(candidates),
            )
        else:
            resolution = AddressResolution(
                status="ambiguous",
                query=normalized_query,
                candidates=tuple(candidates),
            )
        self._remember(normalized_query, resolution)
        return resolution

    def _cached(self, query: str) -> AddressResolution | None:
        with self._cache_lock:
            resolution = self._cache.get(query)
            if resolution is not None:
                self._cache.move_to_end(query)
            return resolution

    def _remember(self, query: str, resolution: AddressResolution) -> None:
        with self._cache_lock:
            self._cache[query] = resolution
            self._cache.move_to_end(query)
            while len(self._cache) > _CACHE_MAXSIZE:
                self._cache.popitem(last=False)

    def _request(self, query: str) -> _SuggestionEnvelope:
        # Граница region..settlement исключает улицы/дома и оставляет варианты,
        # между которыми пользователю действительно нужно выбрать населённый пункт.
        payload = {
            "query": query,
            "count": 10,
            "from_bound": {"value": "region"},
            "to_bound": {"value": "settlement"},
            "language": "ru",
            "division": "administrative",
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self._endpoint,
            data=body,
            headers={
                "Accept": "application/json",
                "Authorization": f"Token {self._token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        for attempt in range(1, self._max_attempts + 1):
            try:
                self._consume_budget()
                with transport.direct_urlopen(request, self._timeout) as response:
                    raw: Any = json.loads(response.read() or b"{}")
                return _SuggestionEnvelope.model_validate(raw)
            except urllib.error.HTTPError as exc:
                retryable = exc.code == 429 or exc.code >= 500
                error = DadataRequestError(
                    f"HTTP {exc.code}",
                    retryable=retryable,
                )
            except transport.TRANSPORT_ERRORS as exc:
                # Всё, что не стало DadataRequestError, уходит мимо resolve и
                # enrich_locations и роняет ход целиком: клиент вместо вопроса
                # видит общее «что-то пошло не так». Прежний список
                # (TimeoutError, URLError) не ловил именно обрыв соединения —
                # RemoteDisconnected и ssl.SSLError летят из getresponse(),
                # IncompleteRead из read(). Правило см. в
                # transport.TRANSPORT_ERRORS.
                error = DadataRequestError(
                    str(exc) or type(exc).__name__,
                    retryable=True,
                )
            except (json.JSONDecodeError, ValidationError) as exc:
                raise DadataRequestError(
                    "DaData вернула невалидный JSON",
                    retryable=False,
                ) from exc

            if not error.retryable or attempt >= self._max_attempts:
                raise error
            time.sleep(0.2 * attempt)

        raise DadataRequestError("неизвестная ошибка DaData", retryable=False)

    def _consume_budget(self) -> None:
        """Fail closed при достижении бюджета, но бюджет — суточный.

        Раньше счётчик жил столько же, сколько процесс, и не обнулялся никогда.
        Предохранитель от разогнавшегося цикла превращался в тихую поломку:
        контейнер в проде работает неделями, и после исчерпания лимита ни один
        клиент больше не получал регион — а без региона CRM не присваивает
        категорию, то есть лид выглядел как «не подошёл».

        Сутки — то окно, в котором лимит остаётся защитой: цикл упрётся в него
        за минуты, а нормальный трафик до него не доходит и на следующий день
        начинает считать заново.
        """
        with self._budget_lock:
            now = time.monotonic()
            if now - type(self)._window_started >= _BUDGET_WINDOW_SECONDS:
                type(self)._window_started = now
                type(self)._request_count = 0
            if self._request_count >= self._request_budget:
                raise DadataRequestError(
                    "исчерпан MBK_DADATA_REQUEST_BUDGET",
                    retryable=False,
                )
            type(self)._request_count += 1

    @staticmethod
    def _unique_candidates(envelope: _SuggestionEnvelope) -> list[AddressCandidate]:
        unique: dict[tuple[str, str], AddressCandidate] = {}
        for suggestion in envelope.suggestions:
            candidate = AddressCandidate.from_suggestion(suggestion)
            if candidate is None or candidate.country_iso_code not in (None, "RU"):
                continue
            key = (candidate.region_fias_id, candidate.locality_fias_id)
            unique.setdefault(key, candidate)
        return list(unique.values())

    @staticmethod
    def _drop_stray_territories(
        candidates: list[AddressCandidate],
        query: str,
    ) -> list[AddressCandidate]:
        """СНТ, автодороги и кварталы в ответе на адрес без таких слов — шум.

        Есть рядом настоящий населённый пункт — остаётся только он. Бой
        18–20.10.2026: на «Санкт-Петербурге» бот спрашивал «это город или
        область?» из-за «снт Логоваз Санкт-Петербург» и «автодороги Москва —
        Санкт-Петербург», на «Екатерирбург» — из-за семи автодорог
        «Екатеринбург — …», на «95 регион» предлагал кварталы № 95 в четырёх
        городах.

        Одни территории в ответе — это «не найдено». Иначе по совпавшему слову в
        названии дороги выбирался регион: два участка «Москва — Владивосток
        ж/д» лежат в Забайкальском крае, и общий регион кандидатов принимался
        за ответ клиента. Кто живёт в СНТ, так и пишет — тогда территории
        остаются. Остаются и территории региона, который клиент назвал сам: по
        ним регион определяется верно.
        """
        if not candidates or _TERRITORY_WORDS_RE.search(query):
            return candidates
        places = [candidate for candidate in candidates if not _is_territory(candidate)]
        if places:
            return places
        words = f" {_normalized_text(query)} "
        return [
            candidate
            for candidate in candidates
            if (region := _bare_name(candidate.region or ""))
            and f" {region} " in words
        ]

    @staticmethod
    def _named_region(
        candidates: list[AddressCandidate],
        query: str,
    ) -> AddressCandidate | None:
        """Клиент назвал сам регион, а не одноимённый посёлок или СНТ.

        Бой 02–04.10.2026: на «Татарстан» бот предлагал выбрать между
        республикой, посёлком совхоза «Татарстан» и СНТ «Татарстан», на
        «Севастополь» — между городом и селом в Калужской области. Жители
        посёлка называют его с районом или областью; голое название региона —
        это регион.

        Срабатывает, только когда запрос целиком совпал с названием ровно
        одного региона и других регионов на те же буквы в ответе нет: «Алтай»
        остаётся вопросом — так говорят и о республике, и о крае.
        """
        name = _bare_name(query)
        if not name:
            return None
        regions = [
            candidate
            for candidate in candidates
            if candidate.fias_level == 1
            and _bare_name(candidate.region or "").startswith(name)
        ]
        if len(regions) != 1 or _bare_name(regions[0].region or "") != name:
            return None
        return regions[0]

    @staticmethod
    def _prefer_regional_capital(
        candidates: list[AddressCandidate],
        query: str,
    ) -> list[AddressCandidate]:
        """Голое название столицы региона — это она, а не одноимённая деревня.

        Бой 02–04.10.2026: «Уфа есть в нескольких регионах: Башкортостан,
        Тюменская область и Пермский край», то же с Костромой и Барнаулом —
        17 диалогов из 133 с лишним вопросом. Признак столицы отдаёт сама
        DaData (`capital_marker`), поэтому райцентр «Родники» рядом с
        подмосковным посёлком Родники по-прежнему уточняется: там ошибка
        региона меняет маршрут.
        """
        if len(candidates) < 2:
            return candidates
        name = _bare_name(query)
        capitals = [
            candidate
            for candidate in candidates
            if candidate.is_regional_capital
            and candidate.settlement is None
            and candidate.city
            and _same_place_name(name, _bare_name(candidate.city))
        ]
        return capitals if len(capitals) == 1 else candidates

    @staticmethod
    def _prefer_town_over_villages(
        candidates: list[AddressCandidate],
        query: str,
    ) -> list[AddressCandidate]:
        """Город с этим названием — он, а не одноимённые сёла, если маршрут один.

        Бой 20.10.2026, сессия 8ddfd08b8edd: на «Бахчесарай» (с опечаткой) бот
        спросил, какой из населённых пунктов имеется в виду — в Крыму,
        Татарстане или Башкортостане. Город среди них один, остальное — посёлок
        и деревня.

        Город побеждает, только если среди кандидатов нет столичного региона,
        а он сам не в нём: от Москвы и области зависит маршрут, и там ошибка
        дороже лишнего вопроса — райцентр «Родники» Ивановской области рядом с
        подмосковным посёлком Родники по-прежнему уточняется. Опечатка в одну-
        две буквы названию не мешает: DaData её уже исправила, сверяем мы.
        """
        if len(candidates) < 2:
            return candidates
        name = _bare_name(query)
        if not name:
            return candidates
        towns = [
            candidate
            for candidate in candidates
            if candidate.settlement is None
            and candidate.city
            and _similar_place_name(name, _bare_name(candidate.city))
        ]
        if len(towns) != 1:
            return candidates
        if any(_in_capital_region(candidate) for candidate in candidates):
            return candidates
        return towns

    @staticmethod
    def _prefer_exact_locality(
        candidates: list[AddressCandidate],
        query: str,
    ) -> list[AddressCandidate]:
        """Убирает СНТ/районы с похожим названием, если есть точный город.

        Например, по «Химки» DaData также возвращает СНТ «Химки» и микрорайон
        «Химки (рп Новая Игирма)». Для вопроса о населённом пункте это шум.
        """
        normalized_query = _normalized_text(query)
        exact: list[AddressCandidate] = []
        for candidate in candidates:
            names = [
                name
                for name in (candidate.city, candidate.settlement)
                if name
            ]
            if any(
                re.search(
                    rf"(?<!\w){re.escape(_normalized_text(name))}(?!\w)",
                    normalized_query,
                )
                for name in names
            ):
                exact.append(candidate)
        return exact or candidates

    @staticmethod
    def _collapse_single_city(
        candidates: list[AddressCandidate],
    ) -> list[AddressCandidate]:
        """Схлопывает микрорайоны/СНТ до города, к которому они относятся.

        DaData на «Казань, Республика Татарстан» может вернуть сам город и несколько
        внутригородских территорий. Для маршрутизации это один населённый пункт,
        поэтому повторно спрашивать регион у клиента нельзя.

        Схлопываем каждый город отдельно, а не только случай «весь ответ — один
        город». На «Самара» DaData возвращает и город, и его же «тер Аэропорт Самара»
        с тем же city_fias_id, и одноимённые деревни других регионов. Пока схлопывание
        требовало одного города на весь список, оно тут не срабатывало: в Самарской
        области оставалось два кандидата, ответ «самарская» подходил обоим, и
        подтверждение региона не срабатывало вообще — диалог зацикливался.
        """
        if len(candidates) < 2:
            return candidates

        # Города, у которых в ответе есть собственная запись уровня города: только их
        # подчинённые территории можно безопасно схлопнуть в неё.
        city_level_by_id = {
            candidate.city_fias_id: candidate
            for candidate in candidates
            if candidate.city_fias_id and candidate.settlement is None
        }

        collapsed: list[AddressCandidate] = []
        for candidate in candidates:
            city_level = (
                city_level_by_id.get(candidate.city_fias_id)
                if candidate.city_fias_id
                else None
            )
            if city_level is None:
                collapsed.append(candidate)
                continue
            if city_level not in collapsed:
                collapsed.append(city_level)
        return collapsed


def _normalized_text(value: str) -> str:
    return re.sub(r"[^а-яa-z0-9]+", " ", value.lower().replace("ё", "е")).strip()


# Слова, которые называют тип, а не само место: «г. Уфа», «Приморский край».
_PLACE_TYPE_WORDS = frozenset(
    {"г", "город", "край", "область", "обл", "республика", "респ"}
)


def _bare_name(value: str) -> str:
    """Название без слов типа: «г. Уфа» → «уфа», «Приморский край» → «приморский»."""
    return " ".join(
        word
        for word in _normalized_text(value).split()
        if word not in _PLACE_TYPE_WORDS
    )


def _same_word_form(spoken: str, name: str) -> bool:
    if spoken == name:
        return True
    longest = max(len(spoken), len(name))
    shared = 0
    for left, right in zip(spoken, name):
        if left != right:
            break
        shared += 1
    return shared >= max(2, longest - 2)


def _same_place_name(spoken: str, name: str) -> bool:
    """«калуге» и «калуга», «твери» и «тверь» — одно название в разных падежах.

    На «В каком городе живёте?» отвечают «Калуге» — продолжением вопроса.
    Сравнение буква в букву такой ответ столицей не считало, и бот предлагал
    выбрать между Калужской, Саратовской и Орловской областями (бой
    05.10.2026, сессия 6b4cfc134666). Различаться могут только окончания:
    «Калугино» — уже другое место.
    """
    spoken_words, name_words = spoken.split(), name.split()
    if not spoken_words or len(spoken_words) != len(name_words):
        return False
    return all(_same_word_form(a, b) for a, b in zip(spoken_words, name_words))


def _edit_distance(left: str, right: str) -> int:
    """Расстояние Левенштейна — сколько букв поправить, чтобы слова совпали."""
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        for j, right_char in enumerate(right, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def _similar_place_name(spoken: str, name: str) -> bool:
    """То же название в другом падеже или с опечаткой: «бахчесарай» ≈ «бахчисарай».

    Опечатка допускается одна на пять букв, но не больше двух и только в
    длинном названии с той же первой буквой: «Орёл» и «Осёл» — разные места.
    """
    if _same_place_name(spoken, name):
        return True
    if len(name) < 6 or not spoken or spoken[0] != name[0]:
        return False
    return _edit_distance(spoken, name) <= min(2, len(name) // 5)


# Регионы, где ошибка в населённом пункте меняет маршрут: класс moscow_mo.
_CAPITAL_REGIONS = frozenset({"москва", "московская"})


def _in_capital_region(candidate: AddressCandidate) -> bool:
    return _bare_name(candidate.region or "") in _CAPITAL_REGIONS


# Тип населённого пункта у территорий: «тер 47 км шоссе СПб-Пески» DaData
# отдаёт уровнем города, но это участок дороги, а не место, где живут.
_TERRITORY_TYPE_RE = re.compile(
    r"^(?:тер\b|тер\.|снт\b|днп\b|днт\b|гск\b|кв-л\b|мкр\b|м-в\b|ж/д\b)",
    re.IGNORECASE,
)


def _is_territory(candidate: AddressCandidate) -> bool:
    """СНТ, автодорога, квартал или участок шоссе — не населённый пункт."""
    if candidate.fias_level == _TERRITORY_LEVEL:
        return True
    return bool(_TERRITORY_TYPE_RE.match(candidate.locality_with_type or ""))


# Подсказки DaData ищут по начальной форме. Клиенты пишут как говорят: «г. Бийск
# Алтайского края», «в Ивановской области», «НСО Тогучин» — и получают «не
# нашла такой населённый пункт» на настоящий город (бой 04.10.2026, сессия
# 236397c01d9f: три таких ответа подряд, анкета сошла с плана, лид потерян).
_REGION_FORMS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?:\bв\s+)?\b(\w+ск)(?:ого|ом)\s+кра[яе]\b", re.IGNORECASE), r"\1ий край"),
    (
        re.compile(r"(?:\bв\s+)?\b(\w+[сц]к)ой\s+(?:области|обл\b\.?)", re.IGNORECASE),
        r"\1ая область",
    ),
    (re.compile(r"(?:\bв\s+)?\bреспублик[иеу]\b", re.IGNORECASE), "Республика"),
    (re.compile(r"\bнсо\b", re.IGNORECASE), "Новосибирская область"),
    # «Москва и область» DaData понимает как железную дорогу «Москва —
    # Владивосток» в Забайкальском крае, и клиент из Москвы получал чужой
    # регион (мутационный прогон 04.10.2026). Столица и её область — один
    # класс маршрутов, поэтому достаточно города.
    (
        re.compile(
            r"\bмосква\s*(?:,|\bи\b)?\s*(?:московская\s+)?(?:область|обл\b\.?|мо\b|подмосковье)",
            re.IGNORECASE,
        ),
        "Москва",
    ),
    # Бой 19.10.2026: «В спб» и «Питер» — для DaData это деревни Питер в
    # четырёх областях и «тер 47 км шоссе СПб-Пески»; «Санкт-Петербурге» в
    # предложном падеже тоже искался хуже начальной формы.
    (
        re.compile(
            r"\b(?:спб|с-пб|питер|питере|питера|питеру|питером"
            r"|(?:санкт[\s-]*)?петербург\w*)\b",
            re.IGNORECASE,
        ),
        "Санкт-Петербург",
    ),
    (
        re.compile(
            r"\bСанкт-Петербург\s*(?:,|\bи\b)?\s*(?:ленинградская\s+)?(?:область|обл\b\.?|ло\b)",
            re.IGNORECASE,
        ),
        "Санкт-Петербург",
    ),
)

# Автомобильный код региона: «95 регион», «регион 95», «95-й регион». Бой
# 19.10.2026, сессия 39aa3de35ab3: на «95регион» бот предложил кварталы № 95 в
# Иркутской, Амурской, Белгородской и Ульяновской областях, а «Чеченская
# республика 95регион» не нашлась вовсе.
_REGION_CODE_RE = re.compile(
    r"(?<!\d)(\d{2,3})\s*-?\s*(?:[йы]й\s*)?регион\w*"
    r"|\bрегион\w*\s*(?:№\s*)?(\d{2,3})(?!\d)",
    re.IGNORECASE,
)
_REGION_BY_CODE: dict[int, str] = {
    1: "Республика Адыгея", 2: "Республика Башкортостан", 3: "Республика Бурятия",
    4: "Республика Алтай", 5: "Республика Дагестан", 6: "Республика Ингушетия",
    7: "Кабардино-Балкарская Республика", 8: "Республика Калмыкия",
    9: "Карачаево-Черкесская Республика", 10: "Республика Карелия", 11: "Республика Коми",
    12: "Республика Марий Эл", 13: "Республика Мордовия", 14: "Республика Саха (Якутия)",
    15: "Республика Северная Осетия - Алания", 16: "Республика Татарстан",
    17: "Республика Тыва", 18: "Удмуртская Республика", 19: "Республика Хакасия",
    20: "Чеченская Республика", 21: "Чувашская Республика", 22: "Алтайский край",
    23: "Краснодарский край", 24: "Красноярский край", 25: "Приморский край",
    26: "Ставропольский край", 27: "Хабаровский край", 28: "Амурская область",
    29: "Архангельская область", 30: "Астраханская область", 31: "Белгородская область",
    32: "Брянская область", 33: "Владимирская область", 34: "Волгоградская область",
    35: "Вологодская область", 36: "Воронежская область", 37: "Ивановская область",
    38: "Иркутская область", 39: "Калининградская область", 40: "Калужская область",
    41: "Камчатский край", 42: "Кемеровская область", 43: "Кировская область",
    44: "Костромская область", 45: "Курганская область", 46: "Курская область",
    47: "Ленинградская область", 48: "Липецкая область", 49: "Магаданская область",
    50: "Московская область", 51: "Мурманская область", 52: "Нижегородская область",
    53: "Новгородская область", 54: "Новосибирская область", 55: "Омская область",
    56: "Оренбургская область", 57: "Орловская область", 58: "Пензенская область",
    59: "Пермский край", 60: "Псковская область", 61: "Ростовская область",
    62: "Рязанская область", 63: "Самарская область", 64: "Саратовская область",
    65: "Сахалинская область", 66: "Свердловская область", 67: "Смоленская область",
    68: "Тамбовская область", 69: "Тверская область", 70: "Томская область",
    71: "Тульская область", 72: "Тюменская область", 73: "Ульяновская область",
    74: "Челябинская область", 75: "Забайкальский край", 76: "Ярославская область",
    77: "Москва", 78: "Санкт-Петербург", 79: "Еврейская автономная область",
    82: "Республика Крым", 83: "Ненецкий автономный округ",
    86: "Ханты-Мансийский автономный округ", 87: "Чукотский автономный округ",
    89: "Ямало-Ненецкий автономный округ", 92: "Севастополь", 95: "Чеченская Республика",
}
# Дополнительные коды тех же регионов.
for _base, _extra in {
    2: (102, 702), 16: (116, 716), 21: (121,), 22: (122,), 23: (93, 123, 193),
    24: (84, 88, 124), 25: (125,), 26: (126,), 34: (134,), 36: (136,),
    38: (85, 138), 42: (142,), 47: (147,), 50: (90, 150, 190, 750, 790),
    52: (152,), 54: (154,), 55: (155,), 56: (156,), 59: (81, 159), 61: (161, 761),
    63: (163, 763), 64: (164,), 66: (96, 196), 73: (173,), 74: (174, 774),
    75: (80,), 77: (97, 99, 177, 197, 199, 777, 797, 799, 977), 78: (98, 178, 198),
    86: (186,),
}.items():
    for _code in _extra:
        _REGION_BY_CODE[_code] = _REGION_BY_CODE[_base]


def _replace_region_code(query: str) -> str:
    """Код региона — названием региона; рядом с названием код просто лишний."""
    match = _REGION_CODE_RE.search(query)
    if match is None:
        return query
    rest = " ".join(f"{query[: match.start()]} {query[match.end():]}".split()).strip(" ,.;")
    if re.search(r"[^\W\d_]{3,}", rest):
        return rest
    code = int(match.group(1) or match.group(2))
    return _REGION_BY_CODE.get(code, query)

# Территории планировочной структуры: СНТ, гаражные кооперативы, автодороги.
# DaData подбирает их по любому слову названия.
_TERRITORY_LEVEL = 65
_TERRITORY_WORDS_RE = re.compile(
    r"\b(?:снт|днп|днт|тер|территория|гск|кооператив|садов\w*|дачн\w*|автодорог\w*)\b",
    re.IGNORECASE,
)


def _canonical_query(query: str) -> str:
    query = _replace_region_code(query)
    for pattern, replacement in _REGION_FORMS:
        query = pattern.sub(replacement, query)
    # «Москва, Москва и область» после замены — «Москва, Москва».
    parts: list[str] = []
    for part in (" ".join(piece.split()) for piece in query.split(",")):
        if part and part.casefold() not in {seen.casefold() for seen in parts}:
            parts.append(part)
    return ", ".join(parts)


@lru_cache(maxsize=1)
def get_dadata_client() -> DadataClient:
    """Один клиент на процесс нужен для общего LRU-кэша и счётчика бюджета."""
    return DadataClient()
