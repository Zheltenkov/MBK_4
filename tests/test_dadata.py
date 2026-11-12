"""DaData: typed parsing, неоднозначные города и fail-safe классификация."""
from __future__ import annotations

import json
import urllib.error
from dataclasses import replace
from typing import Any

from geo.dadata import AddressCandidate, AddressResolution, DadataClient
from geo.enrichment import enrich_locations


def _candidate(
    *,
    region: str,
    region_fias_id: str,
    region_iso_code: str,
    city: str,
    city_fias_id: str,
) -> AddressCandidate:
    return AddressCandidate(
        value=f"{region}, г {city}",
        unrestricted_value=f"{region}, г {city}",
        country="Россия",
        country_iso_code="RU",
        region=region,
        region_with_type=region,
        region_fias_id=region_fias_id,
        region_iso_code=region_iso_code,
        city=city,
        city_with_type=f"г {city}",
        city_fias_id=city_fias_id,
        settlement=None,
        settlement_with_type=None,
        settlement_fias_id=None,
        fias_id=city_fias_id,
        fias_level=4,
    )


def _settlement_candidate(
    *,
    region: str,
    region_fias_id: str,
    region_iso_code: str,
    settlement: str,
    settlement_fias_id: str,
) -> AddressCandidate:
    return AddressCandidate(
        value=f"{region}, деревня {settlement}",
        unrestricted_value=f"{region}, деревня {settlement}",
        country="Россия",
        country_iso_code="RU",
        region=region,
        region_with_type=region,
        region_fias_id=region_fias_id,
        region_iso_code=region_iso_code,
        city=None,
        city_with_type=None,
        city_fias_id=None,
        settlement=settlement,
        settlement_with_type=f"деревня {settlement}",
        settlement_fias_id=settlement_fias_id,
        fias_id=settlement_fias_id,
        fias_level=6,
    )


def test_unique_city_is_standardized_and_classified_as_moscow_region(matrix):
    khimki = _candidate(
        region="Московская обл",
        region_fias_id="mos-region",
        region_iso_code="RU-MOS",
        city="Химки",
        city_fias_id="khimki",
    )

    facts = enrich_locations(
        {"client": {"city": "Химки"}},
        "Химки",
        matrix,
        resolver=lambda query: AddressResolution(
            status="resolved",
            query=query,
            candidate=khimki,
            candidates=(khimki,),
        ),
    )

    assert facts["client"]["region_class"] == "moscow_mo"
    assert facts["client"]["region"] == "Московская обл"
    assert facts["client"]["city"] == "г Химки"
    assert facts["client"]["location"]["region_fias_id"] == "mos-region"
    assert facts["client"]["location"]["confirmation_required"] is False

    unchanged = enrich_locations(
        facts,
        "дальше",
        matrix,
        resolver=lambda query: (_ for _ in ()).throw(
            AssertionError("канонический адрес не запрашивается повторно")
        ),
    )
    assert unchanged == facts


def test_same_named_city_requires_region_confirmation(matrix):
    sovetsk_ko = _candidate(
        region="Калининградская обл",
        region_fias_id="kaliningrad-region",
        region_iso_code="RU-KGD",
        city="Советск",
        city_fias_id="sovetsk-ko",
    )
    sovetsk_kirov = _candidate(
        region="Кировская обл",
        region_fias_id="kirov-region",
        region_iso_code="RU-KIR",
        city="Советск",
        city_fias_id="sovetsk-kirov",
    )
    resolution = AddressResolution(
        status="ambiguous",
        query="Советск",
        candidates=(sovetsk_ko, sovetsk_kirov),
    )

    pending = enrich_locations(
        {"client": {"city": "Советск"}},
        "Советск",
        matrix,
        resolver=lambda query: resolution,
    )

    assert "region_class" not in pending["client"]
    assert pending["client"]["location"]["status"] == "ambiguous"
    assert len(pending["client"]["location"]["candidates"]) == 2

    confirmed = enrich_locations(
        pending,
        "Калининградская область",
        matrix,
        resolver=lambda query: (_ for _ in ()).throw(
            AssertionError("повторный запрос DaData не нужен")
        ),
    )

    assert confirmed["client"]["region"] == "Калининградская обл"
    assert confirmed["client"]["city"] == "г Советск"
    assert confirmed["client"]["region_class"] == "rf_other"
    assert confirmed["client"]["location"]["confirmation_required"] is False


def test_explicit_new_city_replaces_previous_ambiguity(matrix):
    sovetsk = _candidate(
        region="Калининградская обл",
        region_fias_id="kaliningrad-region",
        region_iso_code="RU-KGD",
        city="Советск",
        city_fias_id="sovetsk-ko",
    )
    moscow = _candidate(
        region="Москва",
        region_fias_id="moscow-region",
        region_iso_code="RU-MOW",
        city="Москва",
        city_fias_id="moscow-city",
    )
    facts = {
        "client": {
            "city": "Москва",
            "location": {
                "status": "ambiguous",
                "query": "Советск",
                "confirmation_required": True,
                "candidates": [sovetsk.as_dict()],
            },
        }
    }

    corrected = enrich_locations(
        facts,
        "нет, теперь Москва",
        matrix,
        resolver=lambda query: AddressResolution(
            status="resolved",
            query=query,
            candidate=moscow,
            candidates=(moscow,),
        ),
    )

    assert corrected["client"]["city"] == "г Москва"
    assert corrected["client"]["region_class"] == "moscow_mo"
    assert corrected["client"]["location"]["query"] == "Москва"


def test_lowercase_region_drops_city_from_previous_not_found_query(matrix):
    moscow = _candidate(
        region="Москва",
        region_fias_id="moscow-region",
        region_iso_code="RU-MOW",
        city="Москва",
        city_fias_id="moscow-city",
    )
    seen_queries: list[str] = []

    def resolve(query: str) -> AddressResolution:
        seen_queries.append(query)
        return AddressResolution(
            status="resolved",
            query=query,
            candidate=moscow,
            candidates=(moscow,),
        )

    corrected = enrich_locations(
        {
            "client": {
                "city": "Старое",
                "region": "москва",
                "location": {
                    "status": "not_found",
                    "query": "Старое",
                    "confirmation_required": True,
                    "candidates": [],
                },
            }
        },
        "москва",
        matrix,
        resolver=resolve,
    )

    assert seen_queries == ["москва"]
    assert corrected["client"]["region_class"] == "moscow_mo"


def test_realty_reuses_already_resolved_residence_city_instead_of_asking_again(matrix):
    """Регрессия прод-бага: клиент уже назвал регион для одноимённого города своего
    места жительства, а бот через несколько ходов заново разгонял ту же
    неоднозначность для недвижимости в том же городе — с точки зрения клиента это
    выглядело как повтор уже отвеченного вопроса.
    """
    sovetsk_ko = _candidate(
        region="Калининградская обл",
        region_fias_id="kaliningrad-region",
        region_iso_code="RU-KGD",
        city="Советск",
        city_fias_id="sovetsk-ko",
    )
    sovetsk_kirov = _candidate(
        region="Кировская обл",
        region_fias_id="kirov-region",
        region_iso_code="RU-KIR",
        city="Советск",
        city_fias_id="sovetsk-kirov",
    )
    resolution = AddressResolution(
        status="ambiguous",
        query="Советск",
        candidates=(sovetsk_ko, sovetsk_kirov),
    )

    # Ход 1: клиент называет город своего проживания — неоднозначность.
    pending = enrich_locations(
        {"client": {"city": "Советск"}},
        "Советск",
        matrix,
        resolver=lambda query: resolution,
    )

    # Ход 2: клиент уточняет регион — client.location разрешён.
    resolved_client = enrich_locations(
        pending,
        "Калининградская область",
        matrix,
        resolver=lambda query: (_ for _ in ()).throw(
            AssertionError("повторный запрос DaData клиенту не нужен")
        ),
    )
    assert resolved_client["client"]["location"]["status"] == "resolved"

    # Ход 3 (много позже): выясняется, что недвижимость в том же городе, регион
    # для неё отдельно не называли. Повторного вопроса про Советск быть не должно.
    resolved_client["assets"] = {"realty": {"city": "Советск"}}
    result = enrich_locations(
        resolved_client,
        "квартира тоже в Советске",
        matrix,
        resolver=lambda query: (_ for _ in ()).throw(
            AssertionError("для того же города недвижимости DaData не нужен повторно")
        ),
    )

    assert result["assets"]["realty"]["location"]["status"] == "resolved"
    assert result["assets"]["realty"]["location"]["confirmation_required"] is False
    assert result["assets"]["realty"]["region"] == "Калининградская обл"
    assert result["assets"]["realty"]["region_class"] == "rf_other"


def test_collapse_keeps_working_when_other_regions_are_also_suggested():
    """Прод-баг 27.07: диалог зацикливался на подтверждении региона.

    Реальный ответ DaData на «Самара» содержит и сам город, и его внутреннюю
    территорию «тер Аэропорт Самара» с тем же city_fias_id, и одноимённые деревни
    других регионов. Схлопывание срабатывало только когда ВЕСЬ список относился к
    одному городу, поэтому здесь не срабатывало вовсе: в Самарской области
    оставалось два кандидата, ответ «самарская» подходил обоим одинаково, регион не
    разрезолвливался ни на одном ходу, планировщик бесконечно требовал
    client.region_class, а модель начинала задавать вопросы не по плану.
    """
    samara_city = _candidate(
        region="Самарская обл",
        region_fias_id="samara-region",
        region_iso_code="RU-SAM",
        city="Самара",
        city_fias_id="samara-city",
    )
    samara_airport = replace(
        samara_city,
        value="Самарская обл, тер Аэропорт Самара",
        settlement="Аэропорт Самара",
        settlement_with_type="тер Аэропорт Самара",
        settlement_fias_id="samara-airport",
        fias_id="samara-airport",
        fias_level=65,
    )
    samara_tver = _settlement_candidate(
        region="Тверская обл",
        region_fias_id="tver-region",
        region_iso_code="RU-TVE",
        settlement="Самара",
        settlement_fias_id="samara-tver",
    )

    collapsed = DadataClient._collapse_single_city(
        [samara_city, samara_airport, samara_tver]
    )

    assert collapsed == [samara_city, samara_tver]


def test_realty_region_class_distinguishes_mbk_and_credit_club_lists(matrix):
    """Два списка из исходного файла ведут в два разных маршрута.

    Самара есть в листе «МБК» → это 1-2 и никаких дополнительных вопросов.
    Майкоп есть только в листе «Кредит.Клаб» → это 1-3, где допустима просрочка.
    """
    samara = _candidate(
        region="Самарская обл",
        region_fias_id="samara-region",
        region_iso_code="RU-SAM",
        city="Самара",
        city_fias_id="samara-city",
    )
    maykop = _candidate(
        region="Адыгея Респ",
        region_fias_id="adygea-region",
        region_iso_code="RU-AD",
        city="Майкоп",
        city_fias_id="maykop-city",
    )

    for candidate, expected in ((samara, "mbk_list"), (maykop, "realty_list")):
        facts = enrich_locations(
            {"assets": {"realty": {"city": candidate.city}}},
            candidate.city or "",
            matrix,
            resolver=lambda query, c=candidate: AddressResolution(
                status="resolved",
                query=query,
                candidate=c,
                candidates=(c,),
            ),
        )
        assert facts["assets"]["realty"]["region_class"] == expected, candidate.city


def test_ordinal_reply_selects_candidate_from_the_offered_list(matrix):
    """Человек в чате отвечает «первый» — это осмысленный ответ, а не мусор.

    Без этого любой ответ без буквального названия региона оставлял неоднозначность,
    бот задавал тот же вопрос снова, и диалог зацикливался.
    """
    kaliningrad = _candidate(
        region="Калининградская обл",
        region_fias_id="kaliningrad-region",
        region_iso_code="RU-KGD",
        city="Советск",
        city_fias_id="sovetsk-ko",
    )
    kirov = _candidate(
        region="Кировская обл",
        region_fias_id="kirov-region",
        region_iso_code="RU-KIR",
        city="Советск",
        city_fias_id="sovetsk-kirov",
    )
    resolution = AddressResolution(
        status="ambiguous",
        query="Советск",
        candidates=(kaliningrad, kirov),
    )
    pending = enrich_locations(
        {"client": {"city": "Советск"}},
        "Советск",
        matrix,
        resolver=lambda query: resolution,
    )

    for reply, expected_region in (
        ("первый", "Калининградская обл"),
        ("второй вариант", "Кировская обл"),
        ("вариант 2", "Кировская обл"),
    ):
        confirmed = enrich_locations(pending, reply, matrix, resolver=lambda query: resolution)
        assert confirmed["client"]["location"]["status"] == "resolved", reply
        assert confirmed["client"]["region"] == expected_region, reply


def test_bare_numbers_in_speech_never_pick_a_candidate(matrix):
    """«Нужно 2 млн» не должно молча выбрать второй регион из списка."""
    kaliningrad = _candidate(
        region="Калининградская обл",
        region_fias_id="kaliningrad-region",
        region_iso_code="RU-KGD",
        city="Советск",
        city_fias_id="sovetsk-ko",
    )
    kirov = _candidate(
        region="Кировская обл",
        region_fias_id="kirov-region",
        region_iso_code="RU-KIR",
        city="Советск",
        city_fias_id="sovetsk-kirov",
    )
    resolution = AddressResolution(
        status="ambiguous",
        query="Советск",
        candidates=(kaliningrad, kirov),
    )
    pending = enrich_locations(
        {"client": {"city": "Советск"}},
        "Советск",
        matrix,
        resolver=lambda query: resolution,
    )

    for reply in ("нужно 2 млн", "мне 42 года", "2"):
        still = enrich_locations(pending, reply, matrix, resolver=lambda query: resolution)
        assert still["client"]["location"]["status"] == "ambiguous", reply


def test_failed_disambiguation_is_counted_for_a_softer_reask(matrix):
    """Счётчик попыток нужен, чтобы второй раз спросить иначе, а не тем же текстом."""
    kaliningrad = _candidate(
        region="Калининградская обл",
        region_fias_id="kaliningrad-region",
        region_iso_code="RU-KGD",
        city="Советск",
        city_fias_id="sovetsk-ko",
    )
    kirov = _candidate(
        region="Кировская обл",
        region_fias_id="kirov-region",
        region_iso_code="RU-KIR",
        city="Советск",
        city_fias_id="sovetsk-kirov",
    )
    resolution = AddressResolution(
        status="ambiguous",
        query="Советск",
        candidates=(kaliningrad, kirov),
    )
    facts = enrich_locations(
        {"client": {"city": "Советск"}},
        "Советск",
        matrix,
        resolver=lambda query: resolution,
    )
    assert facts["client"]["location"].get("attempts", 0) == 0

    facts = enrich_locations(facts, "да", matrix, resolver=lambda query: resolution)
    assert facts["client"]["location"]["attempts"] == 1

    facts = enrich_locations(facts, "ну не знаю", matrix, resolver=lambda query: resolution)
    assert facts["client"]["location"]["attempts"] == 2


def _unavailable(query: str) -> AddressResolution:
    return AddressResolution(
        status="unavailable",
        query=query,
        error="исчерпан MBK_DADATA_REQUEST_BUDGET",
    )


def test_offline_fallback_still_recognises_both_region_lists(matrix):
    """Сбой DaData не должен молча выбрасывать региональную недвижимость из матрицы.

    Оба справочника лежат локально, поэтому попадание города в список можно
    проверить без внешнего сервиса. Раньше фолбэк умел распознать только Москву,
    и любой отказ DaData отправлял всех региональных клиентов в rf_other —
    то есть мимо маршрутов 1-2 и 1-3.
    """
    cases = (
        ("Барнаул, Алтайский край", "mbk_list"),
        ("Майкоп, Республика Адыгея", "realty_list"),
        ("Советск, Калининградская область", "rf_other"),
        ("Москва", "moscow_mo"),
    )
    for text, expected in cases:
        facts = enrich_locations(
            {"assets": {"realty": {"city": text}}},
            text,
            matrix,
            resolver=_unavailable,
        )
        realty = facts["assets"]["realty"]
        assert realty["location"]["status"] == "unavailable", text
        assert realty["region_class"] == expected, text


def test_offline_fallback_keeps_a_city_the_directory_knows(matrix):
    """Город из списка — уверенный локальный ответ, даже если субъект не назван.

    Раньше класс считался только когда в реплике встречалось слово
    «область/край/республика». «Барнаул» такого слова не содержит, поэтому при
    аварии DaData объект из списка МБК терял класс и маршрут 1-2 ему не доставался.
    """
    facts = enrich_locations(
        {"assets": {"realty": {"city": "Барнаул"}}},
        "Барнаул",
        matrix,
        resolver=_unavailable,
    )

    assert facts["assets"]["realty"]["region_class"] == "mbk_list"


def test_offline_fallback_keeps_a_republic_named_without_the_word_republic(matrix):
    """«Майкоп, Адыгея» — субъект назван, догадываться не о чем."""
    facts = enrich_locations(
        {"client": {"city": "Майкоп", "region": "Адыгея"}},
        "Майкоп, Адыгея",
        matrix,
        resolver=_unavailable,
    )

    assert facts["client"]["region_class"] == "rf_other"


def test_offline_fallback_does_not_confuse_a_region_with_a_similar_city(matrix):
    """«Тульская область» без города не должна засчитаться как город Тула."""
    facts = enrich_locations(
        {"assets": {"realty": {"region": "Тульская область"}}},
        "Тульская область",
        matrix,
        resolver=_unavailable,
    )

    assert facts["assets"]["realty"]["region_class"] == "rf_other"


def test_offline_fallback_does_not_take_a_village_for_the_city_of_the_same_name(matrix):
    """Лист «МБК» — список городов без регионов, и это его слабое место.

    Без DaData «деревня Самара» в Тверской области нельзя отличить от города
    Самара по одному названию, поэтому опираемся на тип населённого пункта,
    прямо названный клиентом.
    """
    facts = enrich_locations(
        {"assets": {"realty": {"city": "деревня Самара", "region": "Тверская область"}}},
        "деревня Самара, Тверская область",
        matrix,
        resolver=_unavailable,
    )

    assert facts["assets"]["realty"]["region_class"] == "rf_other"


def test_offline_fallback_never_upgrades_client_residence_to_a_realty_list(matrix):
    """Списки — характеристика объекта. Место жительства делится только на Москву и РФ."""
    facts = enrich_locations(
        {"client": {"city": "Барнаул", "region": "Алтайский край"}},
        "Барнаул, Алтайский край",
        matrix,
        resolver=_unavailable,
    )

    assert facts["client"]["region_class"] == "rf_other"


def test_explicit_republic_selects_city_over_same_named_villages(matrix):
    kazan_tatarstan = _candidate(
        region="Татарстан",
        region_fias_id="tatarstan-region",
        region_iso_code="RU-TA",
        city="Казань",
        city_fias_id="kazan-city",
    )
    kazan_kirov = _settlement_candidate(
        region="Кировская обл",
        region_fias_id="kirov-region",
        region_iso_code="RU-KIR",
        settlement="Казань",
        settlement_fias_id="kazan-village",
    )
    pending = enrich_locations(
        {"client": {"city": "Казань"}},
        "Казань",
        matrix,
        resolver=lambda query: AddressResolution(
            status="ambiguous",
            query=query,
            candidates=(kazan_tatarstan, kazan_kirov),
        ),
    )

    confirmed = enrich_locations(
        pending,
        "Я имею в виду Казань в Республике Татарстан",
        matrix,
        resolver=lambda query: (_ for _ in ()).throw(
            AssertionError("явный регион выбирается из сохранённых кандидатов")
        ),
    )

    assert confirmed["client"]["region_class"] == "rf_other"
    assert confirmed["client"]["region"] == "Татарстан"
    assert confirmed["client"]["city"] == "г Казань"
    assert confirmed["client"]["location"]["status"] == "resolved"


def test_client_region_never_uses_realty_only_location_class(matrix):
    kazan = _candidate(
        region="Татарстан",
        region_fias_id="tatarstan-region",
        region_iso_code="RU-TA",
        city="Казань",
        city_fias_id="kazan-city",
    )
    # Изолируем именно список Кредит.Клаб: Казань есть и в листе «МБК».
    matrix.regions["mbk_list"] = []
    matrix.regions["realty_list"] = ["Татарстан\tКазань"]

    facts = enrich_locations(
        {
            "client": {"city": "Казань"},
            "assets": {"realty": {"city": "Казань"}},
        },
        "Живу в Казани, квартира тоже в Казани",
        matrix,
        resolver=lambda query: AddressResolution(
            status="resolved",
            query=query,
            candidate=kazan,
            candidates=(kazan,),
        ),
    )

    assert facts["client"]["region_class"] == "rf_other"
    assert facts["assets"]["realty"]["region_class"] == "realty_list"


def test_dadata_collapses_subdivisions_of_the_same_city():
    kazan = _candidate(
        region="Татарстан",
        region_fias_id="tatarstan-region",
        region_iso_code="RU-TA",
        city="Казань",
        city_fias_id="kazan-city",
    )
    kazan_snt = replace(
        kazan,
        value="г Казань, тер СНТ Татарстан",
        settlement="СНТ Татарстан",
        settlement_with_type="тер СНТ Татарстан",
        settlement_fias_id="kazan-snt",
        fias_id="kazan-snt",
        fias_level=65,
    )

    collapsed = DadataClient._collapse_single_city([kazan, kazan_snt])

    assert collapsed == [kazan]


def test_moscow_capital_confirmation_selects_city_not_same_named_villages(matrix):
    moscow_city = _candidate(
        region="Москва",
        region_fias_id="moscow-city",
        region_iso_code="RU-MOW",
        city="Москва",
        city_fias_id="moscow-city",
    )
    moscow_village = _settlement_candidate(
        region="Тверская обл",
        region_fias_id="tver-region",
        region_iso_code="RU-TVE",
        settlement="Москва",
        settlement_fias_id="moscow-village",
    )
    resolution = AddressResolution(
        status="ambiguous",
        query="Москва",
        candidates=(moscow_city, moscow_village),
    )

    for confirmation in ("Да, в столице", "город Москва"):
        pending = enrich_locations(
            {"client": {"city": "Москва"}},
            "Москва",
            matrix,
            resolver=lambda query: resolution,
        )
        confirmed = enrich_locations(
            pending,
            confirmation,
            matrix,
            resolver=lambda query: (_ for _ in ()).throw(
                AssertionError("подтверждение выбирается из сохранённых кандидатов")
            ),
        )

        assert confirmed["client"]["region"] == "Москва"
        assert confirmed["client"]["city"] == "г Москва"
        assert confirmed["client"]["region_class"] == "moscow_mo"
        assert confirmed["client"]["location"]["confirmation_required"] is False


def test_bare_moscow_defaults_to_capital_without_confirmation(matrix):
    moscow_city = _candidate(
        region="Москва",
        region_fias_id="moscow-city",
        region_iso_code="RU-MOW",
        city="Москва",
        city_fias_id="moscow-city",
    )
    moscow_village = _settlement_candidate(
        region="Тверская обл",
        region_fias_id="tver-region",
        region_iso_code="RU-TVE",
        settlement="Москва",
        settlement_fias_id="moscow-village",
    )

    facts = enrich_locations(
        {"client": {"city": "Москва"}},
        "живу в Москве",
        matrix,
        resolver=lambda query: AddressResolution(
            status="ambiguous",
            query=query,
            candidates=(moscow_city, moscow_village),
        ),
    )

    assert facts["client"]["region_class"] == "moscow_mo"
    assert facts["client"]["city"] == "г Москва"
    assert facts["client"]["location"]["status"] == "resolved"
    assert facts["client"]["location"]["confirmation_required"] is False


def test_bare_moscow_defaults_for_residence_and_realty_in_same_turn(matrix):
    """Регрессия этапа 1: бот не должен отдельно уточнять две одинаковые Москвы."""
    moscow_city = _candidate(
        region="Москва",
        region_fias_id="moscow-city",
        region_iso_code="RU-MOW",
        city="Москва",
        city_fias_id="moscow-city",
    )
    moscow_village = _settlement_candidate(
        region="Тверская обл",
        region_fias_id="tver-region",
        region_iso_code="RU-TVE",
        settlement="Москва",
        settlement_fias_id="moscow-village",
    )

    facts = enrich_locations(
        {
            "client": {"city": "Москва"},
            "assets": {"realty": {"city": "Москва"}},
        },
        "Живу в Москве, квартира тоже в Москве",
        matrix,
        resolver=lambda query: AddressResolution(
            status="ambiguous",
            query=query,
            candidates=(moscow_city, moscow_village),
        ),
    )

    assert facts["client"]["region_class"] == "moscow_mo"
    assert facts["assets"]["realty"]["region_class"] == "moscow_mo"
    assert facts["client"]["location"]["confirmation_required"] is False
    assert facts["assets"]["realty"]["location"]["confirmation_required"] is False


def test_moscow_with_explicit_other_region_is_not_defaulted_to_capital(matrix):
    moscow_city = _candidate(
        region="Москва",
        region_fias_id="moscow-city",
        region_iso_code="RU-MOW",
        city="Москва",
        city_fias_id="moscow-city",
    )
    moscow_village = _settlement_candidate(
        region="Тверская обл",
        region_fias_id="tver-region",
        region_iso_code="RU-TVE",
        settlement="Москва",
        settlement_fias_id="moscow-village",
    )

    facts = enrich_locations(
        {"client": {"city": "Москва", "region": "Тверская область"}},
        "Москва, Тверская область",
        matrix,
        resolver=lambda query: AddressResolution(
            status="ambiguous",
            query=query,
            candidates=(moscow_city, moscow_village),
        ),
    )

    assert "region_class" not in facts["client"]
    assert facts["client"]["location"]["status"] == "ambiguous"
    assert facts["client"]["location"]["confirmation_required"] is True


def test_bare_city_is_not_guessed_when_dadata_is_unavailable(matrix):
    facts = enrich_locations(
        {"client": {"city": "Химки", "region_class": "rf_other"}},
        "Химки",
        matrix,
        resolver=lambda query: AddressResolution(
            status="unavailable",
            query=query,
            error="timeout",
        ),
    )

    assert "region_class" not in facts["client"]
    assert facts["client"]["location"]["status"] == "unavailable"
    assert facts["client"]["location"]["confirmation_required"] is True


def test_dadata_client_deduplicates_identical_suggestions(monkeypatch):
    response = {
        "suggestions": [
            {
                "value": "Московская обл, г Химки",
                "unrestricted_value": "Московская обл, г Химки",
                "data": {
                    "country": "Россия",
                    "country_iso_code": "RU",
                    "region": "Московская",
                    "region_with_type": "Московская обл",
                    "region_fias_id": "mos-region",
                    "region_iso_code": "RU-MOS",
                    "city": "Химки",
                    "city_with_type": "г Химки",
                    "city_fias_id": "khimki",
                    "fias_id": "khimki",
                    "fias_level": 4,
                },
            },
            {
                "value": "Московская обл, г Химки",
                "data": {
                    "country_iso_code": "RU",
                    "region_fias_id": "mos-region",
                    "region_iso_code": "RU-MOS",
                    "city": "Химки",
                    "city_fias_id": "khimki",
                    "fias_id": "khimki",
                },
            },
            {
                "value": "Иркутская обл, Нижнеилимский р-н, мкр Химки",
                "data": {
                    "country_iso_code": "RU",
                    "region": "Иркутская",
                    "region_with_type": "Иркутская обл",
                    "region_fias_id": "irkutsk-region",
                    "region_iso_code": "RU-IRK",
                    "city": "Новая Игирма",
                    "city_fias_id": "novaya-igirma",
                    "settlement": "Химки (рп Новая Игирма)",
                    "settlement_fias_id": "khimki-microdistrict",
                    "fias_id": "khimki-microdistrict",
                },
            },
        ]
    }

    class _Response:
        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(response).encode("utf-8")

    calls = 0

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        return _Response()

    monkeypatch.setattr(
        "geo.dadata.transport.direct_urlopen",
        fake_urlopen,
    )
    client = DadataClient(token="test-token", max_attempts=1)

    resolution = client.resolve("Химки")
    cached = client.resolve("Химки")

    assert resolution.status == "resolved"
    assert resolution.candidate is not None
    assert resolution.candidate.city == "Химки"
    assert cached == resolution
    assert calls == 1


def _khimki_response() -> dict[str, Any]:
    return {
        "suggestions": [
            {
                "value": "Московская обл, г Химки",
                "unrestricted_value": "Московская обл, г Химки",
                "data": {
                    "country": "Россия",
                    "country_iso_code": "RU",
                    "region": "Московская",
                    "region_with_type": "Московская обл",
                    "region_fias_id": "mos-region",
                    "region_iso_code": "RU-MOS",
                    "city": "Химки",
                    "city_with_type": "г Химки",
                    "city_fias_id": "khimki",
                    "fias_id": "khimki",
                    "fias_level": 4,
                },
            },
        ]
    }


class _JsonResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def __enter__(self) -> "_JsonResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_dadata_client_does_not_cache_temporary_failure(monkeypatch):
    """Десять секунд аварии сети не должны отравлять город до перезапуска процесса.

    Регион определяет маршрут: если разовый обрыв связи залипает в кэше, все
    клиенты из популярного города уезжают без региона, пока жив процесс.
    Поэтому повторимая по смыслу ошибка (обрыв, таймаут, 5xx, 429) обязана
    рассасываться сама — следующий вопрос про тот же город идёт в DaData заново.
    """
    calls = 0

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.URLError("connection reset")
        return _JsonResponse(_khimki_response())

    monkeypatch.setattr("geo.dadata.transport.direct_urlopen", fake_urlopen)
    client = DadataClient(token="test-token", max_attempts=1)

    during_outage = client.resolve("Химки")
    assert during_outage.status == "unavailable"
    assert calls == 1

    # Авария кончилась — тот же текст обязан снова дойти до DaData.
    after_outage = client.resolve("Химки")
    assert after_outage.status == "resolved"
    assert after_outage.candidate is not None
    assert after_outage.candidate.city == "Химки"
    assert calls == 2

    # А удачный ответ по-прежнему кэшируется: бюджет запросов не должен страдать.
    assert client.resolve("Химки") == after_outage
    assert calls == 2


def test_dadata_client_caches_non_retryable_failure(monkeypatch):
    """Отказ, повтор которого бессмыслен по построению, остаётся fail-closed.

    HTTP 4xx (кроме 429) и исчерпанный MBK_DADATA_REQUEST_BUDGET сами не
    «пройдут»: долбить ими DaData на каждое сообщение клиента — трата бюджета и
    задержка в диалоге. Граница проходит по признаку retryable самой ошибки,
    а не по списку статусов, чтобы будущие временные состояния не залипали.
    """
    calls = 0

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(
            "https://suggestions.dadata.ru", 403, "Forbidden", {}, None
        )

    monkeypatch.setattr("geo.dadata.transport.direct_urlopen", fake_urlopen)
    client = DadataClient(token="test-token", max_attempts=1)

    first = client.resolve("Химки")
    second = client.resolve("Химки")

    assert first.status == "unavailable"
    assert second == first
    assert calls == 1


def test_business_region_list_accepts_human_readable_region_name(matrix):
    configured = matrix.model_copy(deep=True)
    configured.regions["realty_list"] = ["Калининградская область"]
    sovetsk = _candidate(
        region="Калининградская обл",
        region_fias_id="kaliningrad-region",
        region_iso_code="RU-KGD",
        city="Советск",
        city_fias_id="sovetsk",
    )

    facts = enrich_locations(
        {"assets": {"realty": {"city": "Советск"}}},
        "Советск",
        configured,
        resolver=lambda query: AddressResolution(
            status="resolved",
            query=query,
            candidate=sovetsk,
            candidates=(sovetsk,),
        ),
    )

    assert facts["assets"]["realty"]["region_class"] == "realty_list"


def test_credit_club_list_matches_city_and_region_pair(matrix):
    # Майкоп есть только в листе «Кредит.Клаб», поэтому проверяет именно парный
    # справочник: города вроде Казани и Калининграда лежат ещё и в листе «МБК».
    maykop = _candidate(
        region="Адыгея Респ",
        region_fias_id="adygea-region",
        region_iso_code="RU-AD",
        city="Майкоп",
        city_fias_id="maykop",
    )

    facts = enrich_locations(
        {"assets": {"realty": {"city": "Майкоп"}}},
        "Майкоп",
        matrix,
        resolver=lambda query: AddressResolution(
            status="resolved",
            query=query,
            candidate=maykop,
            candidates=(maykop,),
        ),
    )

    assert facts["assets"]["realty"]["region_class"] == "realty_list"


def test_city_outside_list_does_not_pass_on_region_alone(matrix):
    sovetsk = _candidate(
        region="Калининградская обл",
        region_fias_id="kaliningrad-region",
        region_iso_code="RU-KGD",
        city="Советск",
        city_fias_id="sovetsk",
    )

    facts = enrich_locations(
        {"assets": {"realty": {"city": "Советск"}}},
        "Советск",
        matrix,
        resolver=lambda query: AddressResolution(
            status="resolved",
            query=query,
            candidate=sovetsk,
            candidates=(sovetsk,),
        ),
    )

    assert facts["assets"]["realty"]["region_class"] == "rf_other"


def _tver_villages() -> list[AddressCandidate]:
    """Что DaData отдаёт на «деревня под Тверью»: разные деревни, один регион."""
    return [
        _settlement_candidate(
            region="Тверская обл",
            region_fias_id="tver-region",
            region_iso_code="RU-TVE",
            settlement=name,
            settlement_fias_id=f"tver-{index}",
        )
        for index, name in enumerate(("Облужье", "Облино", "Будово", "Орша"))
    ]


def test_one_region_for_all_candidates_needs_no_clarification(matrix):
    """Деревню знать незачем, если регион у всех вариантов один.

    Живой прогон 12.08.2026: клиент сказал «деревня под Тверью», бот трижды
    переспрашивал название, перечислял чужие деревни и в итоге сдавался —
    четыре хода впустую, а регион так и оставался пустым. Между тем маршрут
    выбирается по региону, и он был известен с первого ответа.
    """
    resolution = AddressResolution(
        status="ambiguous", query="деревня под Тверью", candidates=_tver_villages()
    )

    facts = enrich_locations(
        {"client": {"city": "деревня под Тверью"}},
        "деревня под Тверью",
        matrix,
        resolver=lambda query: resolution,
    )

    location = facts["client"]["location"]
    assert location["status"] == "resolved"
    assert location["confirmation_required"] is False
    assert location["region_with_type"] == "Тверская обл"
    assert facts["client"]["region_class"] == "rf_other"


def test_the_settlement_is_not_invented_when_it_stays_unknown(matrix):
    """Регион определён, но деревня — нет. Придумывать её нельзя."""
    resolution = AddressResolution(
        status="ambiguous", query="деревня под Тверью", candidates=_tver_villages()
    )

    facts = enrich_locations(
        {"client": {"city": "деревня под Тверью"}},
        "деревня под Тверью",
        matrix,
        resolver=lambda query: resolution,
    )

    location = facts["client"]["location"]
    assert location["settlement"] is None
    assert location["city"] is None


def test_different_regions_still_require_a_question(matrix):
    """Воронеж бывает в трёх областях — тут переспросить обязательно."""
    resolution = AddressResolution(
        status="ambiguous",
        query="Воронеж",
        candidates=[
            _candidate(
                region="Воронежская обл",
                region_fias_id="vrn",
                region_iso_code="RU-VOR",
                city="Воронеж",
                city_fias_id="vrn-city",
            ),
            _settlement_candidate(
                region="Алтайский край",
                region_fias_id="alt",
                region_iso_code="RU-ALT",
                settlement="Воронеж",
                settlement_fias_id="alt-vrn",
            ),
        ],
    )

    facts = enrich_locations(
        {"client": {"city": "Воронеж"}},
        "Воронеж",
        matrix,
        resolver=lambda query: resolution,
    )

    assert facts["client"]["location"]["status"] == "ambiguous"
    assert facts["client"]["location"]["confirmation_required"] is True
