"""Одноимённые места: столица региона, сам регион и название в косвенном падеже.

Разбор боевых диалогов 02–04.10.2026 (страница /bfl-finish, 133 диалога): в 17
бот переспрашивал место, которое клиент назвал однозначно. «Уфа есть в
нескольких регионах», «есть два Севастополя: город и село в Калужской
области», «Татарстан — сама республика, посёлок совхоза „Татарстан“, СНТ
„Татарстан“»; «г. Бийск Алтайского края» и «НСО Тогучин» не находились вовсе.
В сессии 236397c01d9f после трёх таких ответов подряд анкета сошла с плана и
лид с телефоном не создался.

Ответы DaData здесь — сокращённые копии настоящих (проба 04.10.2026).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from geo.dadata import DadataClient, _canonical_query


def _suggestion(value: str, **data: Any) -> dict[str, Any]:
    return {"value": value, "data": {"country_iso_code": "RU", **data}}


def _city(value: str, region: str, region_id: str, city: str, *, capital: str = "0"):
    return _suggestion(
        value,
        region=region,
        region_with_type=f"{region} обл",
        region_fias_id=region_id,
        city=city,
        city_with_type=f"г {city}",
        city_fias_id=f"{region_id}-{city}",
        fias_id=f"{region_id}-{city}",
        fias_level=4,
        capital_marker=capital,
    )


def _village(value: str, region: str, region_id: str, settlement: str):
    return _suggestion(
        value,
        region=region,
        region_with_type=f"{region} обл",
        region_fias_id=region_id,
        settlement=settlement,
        settlement_with_type=f"деревня {settlement}",
        settlement_fias_id=f"{region_id}-{settlement}",
        fias_id=f"{region_id}-{settlement}",
        fias_level=6,
        capital_marker="0",
    )


def _region(value: str, region: str, region_id: str, **extra: Any):
    return _suggestion(
        value,
        region=region,
        region_with_type=value,
        region_fias_id=region_id,
        fias_id=region_id,
        fias_level=1,
        capital_marker="0",
        **extra,
    )


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


@pytest.fixture
def dadata(monkeypatch):
    """Клиент с подменённой сетью: `answer(suggestions)` задаёт ответ DaData."""
    state: dict[str, Any] = {"suggestions": [], "queries": []}

    def fake_urlopen(request, timeout):
        state["queries"].append(json.loads(request.data.decode("utf-8"))["query"])
        return _Response({"suggestions": state["suggestions"]})

    monkeypatch.setattr("geo.dadata.transport.direct_urlopen", fake_urlopen)
    client = DadataClient(token="test-token", max_attempts=1)

    def answer(suggestions: list[dict[str, Any]]) -> None:
        state["suggestions"] = suggestions

    client.answer = answer  # type: ignore[attr-defined]
    client.queries = state["queries"]  # type: ignore[attr-defined]
    return client


UFA = [
    _city("г Уфа", "Башкортостан", "bash", "Уфа", capital="2"),
    _village("Тюменская обл, Вагайский р-н, деревня Уфа", "Тюменская", "tyu", "Уфа"),
    _village("Пермский край, Кунгурский р-н, деревня Уфа", "Пермский", "perm", "Уфа"),
]


@pytest.mark.parametrize("query", ["Уфа", "г. Уфа", "город Уфа"])
def test_a_regional_capital_beats_same_named_villages(dadata, query):
    dadata.answer(UFA)

    resolution = dadata.resolve(query)

    assert resolution.status == "resolved"
    assert resolution.candidate.region == "Башкортостан"
    assert resolution.candidate.city == "Уфа"


def test_two_cities_of_one_name_go_to_the_regional_capital(dadata):
    dadata.answer(
        [
            _city("г Киров", "Кировская", "kir", "Киров", capital="2"),
            _city("Калужская обл, г Киров", "Калужская", "klu", "Киров", capital="1"),
        ]
    )

    resolution = dadata.resolve("Киров")

    assert resolution.status == "resolved"
    assert resolution.candidate.region == "Кировская"


def test_a_district_centre_is_still_asked_about(dadata):
    """Родники — райцентр Ивановской области и посёлок в Подмосковье. Угадать
    нельзя: от региона зависит маршрут, а столицы среди них нет."""
    dadata.answer(
        [
            _city("Ивановская обл, г Родники", "Ивановская", "iva", "Родники", capital="1"),
            _village("Московская обл, г Раменское, пгт Родники", "Московская", "mos", "Родники"),
        ]
    )

    resolution = dadata.resolve("Родники")

    assert resolution.status == "ambiguous"
    assert {candidate.region for candidate in resolution.candidates} == {
        "Ивановская",
        "Московская",
    }


def test_cities_without_a_capital_among_them_stay_ambiguous(dadata):
    dadata.answer(
        [
            _city("Калининградская обл, г Советск", "Калининградская", "kgd", "Советск"),
            _city("Кировская обл, г Советск", "Кировская", "kir", "Советск"),
        ]
    )

    assert dadata.resolve("Советск").status == "ambiguous"


TATARSTAN = [
    _region("респ Татарстан", "Татарстан", "tat"),
    _suggestion(
        'респ Татарстан, Тукаевский р-н, поселок совхоза "Татарстан"',
        region="Татарстан",
        region_with_type="респ Татарстан",
        region_fias_id="tat",
        settlement='совхоза "Татарстан"',
        settlement_fias_id="tat-sovhoz",
        fias_id="tat-sovhoz",
        fias_level=6,
    ),
    _suggestion(
        "Удмуртская респ, Алнашский р-н, тер Автодорога граница Татарстана",
        region="Удмуртская",
        region_with_type="Удмуртская респ",
        region_fias_id="udm",
        settlement="Автодорога граница Татарстана",
        settlement_fias_id="udm-road",
        fias_id="udm-road",
        fias_level=65,
    ),
]


@pytest.mark.parametrize("query", ["Татарстан", "Республика Татарстан", "респ. Татарстан"])
def test_a_region_named_by_itself_is_the_region(dadata, query):
    dadata.answer(TATARSTAN)

    resolution = dadata.resolve(query)

    assert resolution.status == "resolved"
    assert resolution.candidate.fias_level == 1
    assert resolution.candidate.region == "Татарстан"
    assert resolution.candidate.settlement is None


def test_a_region_beats_settlements_that_carry_its_name(dadata):
    """На «Приморский» DaData отдаёт край и пять посёлков Приморский в разных
    регионах; раньше край отбрасывался как «неточное» совпадение."""
    dadata.answer(
        [
            _region("Приморский край", "Приморский", "pri"),
            _village("Челябинская обл, поселок Приморский", "Челябинская", "che", "Приморский"),
            _village("Самарская обл, поселок Приморский", "Самарская", "sam", "Приморский"),
        ]
    )

    resolution = dadata.resolve("Приморский")

    assert resolution.status == "resolved"
    assert resolution.candidate.region == "Приморский"
    assert resolution.candidate.settlement is None


def test_a_federal_city_beats_a_village_of_the_same_name(dadata):
    dadata.answer(
        [
            _region(
                "г Севастополь",
                "Севастополь",
                "sev",
                city="Севастополь",
                city_with_type="г Севастополь",
                city_fias_id="sev",
            ),
            _village("Калужская обл, село Севастополь", "Калужская", "klu", "Севастополь"),
        ]
    )

    resolution = dadata.resolve("Севастополь")

    assert resolution.status == "resolved"
    assert resolution.candidate.region == "Севастополь"


def test_a_word_shared_by_two_regions_is_still_asked_about(dadata):
    """«Алтай» говорят и о республике, и о крае."""
    dadata.answer(
        [
            _region("респ Алтай", "Алтай", "alt-rep"),
            _region("Алтайский край", "Алтайский", "alt-kray"),
        ]
    )

    assert dadata.resolve("Алтай").status == "ambiguous"


def test_a_village_named_with_its_region_is_not_replaced_by_the_region(dadata):
    dadata.answer(
        [_village("Челябинская обл, поселок Приморский", "Челябинская", "che", "Приморский")]
    )

    resolution = dadata.resolve("Приморский, Челябинская область")

    assert resolution.status == "resolved"
    assert resolution.candidate.settlement == "Приморский"


@pytest.mark.parametrize(
    ("spoken", "sent"),
    [
        ("г.Бийск Алтайского края", "г.Бийск Алтайский край"),
        ("Бийск, Алтайского края", "Бийск, Алтайский край"),
        ("живу в Алтайском крае", "живу Алтайский край"),
        ("Оренбургской области село Шарлык", "Оренбургская область село Шарлык"),
        ("в Липецкой обл.", "Липецкая область"),
        ("Республики Коми", "Республика Коми"),
        ("НСО Тогучин", "Новосибирская область Тогучин"),
        ("Нсо тогучин", "Новосибирская область тогучин"),
        ("Красноярск", "Красноярск"),
        ("Комсомольск-на-Амуре", "Комсомольск-на-Амуре"),
    ],
)
def test_region_is_sent_to_dadata_in_its_dictionary_form(spoken, sent):
    assert _canonical_query(spoken) == sent


def test_the_client_wording_stays_the_cache_key_and_the_reported_query(dadata):
    dadata.answer(
        [_city("Алтайский край, г Бийск", "Алтайский", "alt-kray", "Бийск")]
    )

    resolution = dadata.resolve("Бийск Алтайского края")
    again = dadata.resolve("Бийск Алтайского края")

    assert resolution.status == "resolved"
    assert resolution.query == "Бийск Алтайского края"
    assert again == resolution
    assert dadata.queries == ["Бийск Алтайский край"]


# --------------------------------------------------------------------------- #
# Находки полного мутационного прогона 04.10.2026
# --------------------------------------------------------------------------- #
def _territory(value: str, region: str, region_id: str, name: str):
    return _suggestion(
        value,
        region=region,
        region_with_type=f"{region} край",
        region_fias_id=region_id,
        settlement=name,
        settlement_with_type=f"тер {name}",
        settlement_fias_id=f"{region_id}-{name}",
        fias_id=f"{region_id}-{name}",
        fias_level=65,
    )


RAILWAY = [
    _territory("Забайкальский край, п. ст. Арчикой, тер Москва-Владивосток жд", "Забайкальский", "zab", "Москва-Владивосток жд"),
    _territory("Забайкальский край, п. ст. Кука, тер Москва- Владивосток ж д", "Забайкальский", "zab", "Москва- Владивосток ж д"),
]


def test_a_road_that_shares_a_word_with_the_answer_is_not_the_answer(dadata):
    """«Москва, Москва и область» → «по данным вижу Забайкальский край»: DaData
    нашла два участка железной дороги «Москва — Владивосток», оба в одном
    регионе, и общий регион приняли за ответ."""
    dadata.answer(RAILWAY)

    assert dadata.resolve("Абракадабра Москва Владивосток").status == "not_found"


def test_territories_are_kept_when_the_client_names_one(dadata):
    dadata.answer(RAILWAY)

    assert dadata.resolve("тер Москва-Владивосток жд").status != "not_found"


def test_territories_of_the_region_the_client_named_still_give_the_region(dadata):
    dadata.answer(RAILWAY)

    resolution = dadata.resolve("Арчикой, Забайкальский край")

    assert {candidate.region for candidate in resolution.candidates} == {"Забайкальский"}


@pytest.mark.parametrize(
    ("spoken", "sent"),
    [
        ("Москва и область", "Москва"),
        ("Москва, Москва и область", "Москва"),
        ("Москва и МО", "Москва"),
        ("Москва, Московская область", "Москва"),
        ("Москва и Подмосковье", "Москва"),
        ("Москва, Тверская область", "Москва, Тверская область"),
        ("Химки, Московская область", "Химки, Московская область"),
        ("Уфа, Уфа", "Уфа"),
    ],
)
def test_moscow_with_its_region_is_moscow(spoken, sent):
    assert _canonical_query(spoken) == sent
