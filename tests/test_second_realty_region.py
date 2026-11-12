"""Регион второго объекта обязан доехать до CRM.

Аудит 19.08.2026. Матрица спрашивает у второго объекта «в каком регионе и
городе находится этот объект», но кладёт ответ только в `region_class` — наш
внутренний код маршрута. Названия города у второго объекта не бывает никогда:
поля нет ни в матрице, ни в скоупах гео, ни в списке известных путей.

А маппинг лида читает именно название. Когда квалифицирующим становится второй
объект, в CRM уходит тип объекта без региона — а по пробе от 11.08.2026 запрос
без региона даёт `category=null`. То есть самая дорогая, залоговая ветка
систематически отдаёт лид без категории.

Прежние тесты этого не видели, потому что записывали факты второго объекта
руками, вместе с городом, — в форме, которую продакшн собрать не может.
Поэтому здесь факты собираются только из путей, известных системе.
"""
from __future__ import annotations

import pytest

from delivery.crm_mapping import map_first_qualification, map_lead_intake
from routing import get_matrix


@pytest.fixture(scope="module")
def matrix():
    return get_matrix()


def test_every_path_the_mapping_reads_is_reachable(matrix):
    """Инвариант: маппинг не должен читать то, чего система не собирает.

    Именно это расхождение и породило дефект: `_location_text` читает
    `city`/`region`/`location`, а анкета для второго объекта их не наполняет.
    """
    from geo import enrichment
    from interpretation import reducer

    known = set(matrix.fields)
    known |= set(reducer._RAW_LOCATION_PATHS)
    for scope in enrichment._SCOPES:
        known |= {scope.region_path, scope.city_path, scope.class_path, scope.location_path}

    roots = {"assets.realty", "assets.realty_2"}
    missing = [
        f"{root}.{leaf}"
        for root in roots
        for leaf in ("city", "region")
        if f"{root}.{leaf}" not in known
    ]

    assert not missing, (
        "маппинг лида читает пути, которые системе неоткуда взять: "
        + ", ".join(missing)
    )


def _facts_after_the_client_named_the_city(matrix) -> dict:
    """Факты ровно того вида, который собирает продакшн.

    Город второго объекта не вписывается руками: клиент называет его в ответ на
    вопрос анкеты, извлекатель кладёт название в `region`, а DaData
    стандартизирует. Именно эта цепочка и была разорвана.
    """
    from geo.enrichment import enrich_locations
    from geo.dadata import AddressCandidate, AddressResolution

    moscow = AddressCandidate(
        value="г Москва",
        unrestricted_value="г Москва",
        country="Россия",
        country_iso_code="RU",
        region="г Москва",
        region_with_type="г Москва",
        region_fias_id="msk",
        region_iso_code="RU-MOW",
        city="Москва",
        city_with_type="г Москва",
        city_fias_id="msk-city",
        settlement=None,
        settlement_with_type=None,
        settlement_fias_id=None,
        fias_id="msk-city",
        fias_level=4,
    )

    facts = {
        "client": {"name": "Сергей Иванов", "phone": "+79990000000", "city": "Тверь"},
        "request": {"amount": 2_000_000},
        "assets": {
            "has_realty": True,
            "realty": {
                "kind": "house",
                "region_class": "rf_other",
                "ownership_form": "private",
                "owners": "client_only",
                "city": "Тверь",
                "region": "Тверская обл",
            },
            "has_second_realty": True,
            "realty_2": {
                "kind": "flat",
                "ownership_form": "private",
                "owners": "client_only",
                # Всё, что есть после ответа клиента: название, как он его сказал.
                "region": "Москва",
            },
        },
    }
    return enrich_locations(
        facts,
        "Москва",
        matrix,
        resolver=lambda query: AddressResolution(
            status="resolved", query=query, candidate=moscow, candidates=(moscow,)
        ),
    )


def test_the_second_object_gets_a_real_city_from_the_answer(matrix):
    facts = _facts_after_the_client_named_the_city(matrix)

    second = facts["assets"]["realty_2"]
    assert second.get("city"), "город второго объекта не разобран"
    assert second.get("region_class") == "moscow_mo", "класс региона не выведен"


def test_lead_carries_the_region_of_the_qualifying_object(matrix):
    mapping = map_lead_intake(
        _facts_after_the_client_named_the_city(matrix),
        form_filled_out=True,
        site_consent_shown=False,
    )

    assert mapping.ready
    assert mapping.request is not None
    payload = mapping.request.model_dump(by_alias=True, exclude_none=True)
    region = payload.get("CHAT_REALTY_REGION")

    assert region, "лид уходит без региона объекта — CRM вернёт category=null"
    assert "Москва" in region, f"регион должен быть у квалифицирующего объекта, а не {region!r}"


def test_qualification_carries_the_region_of_the_qualifying_object(matrix):
    mapping = map_first_qualification(_facts_after_the_client_named_the_city(matrix))

    assert mapping.request is not None
    payload = mapping.request.model_dump(by_alias=True, exclude_none=True)
    region = payload.get("CHAT_REALTY_REGION")

    assert region, "квалификация уходит без региона объекта"
    assert "Москва" in region


def test_both_objects_are_described_to_the_extractor(matrix):
    """Модель должна знать про город обоих объектов, а не только первого.

    Каталог извлекателя строится из матрицы плюс список сырых географических
    полей. Второго объекта в этом списке не было — значит модель не могла
    извлечь его город даже теоретически.
    """
    from interpretation.prompt import _RAW_LOCATION_FIELDS

    described = {str(item["path"]) for item in _RAW_LOCATION_FIELDS}
    for root in ("assets.realty", "assets.realty_2"):
        for leaf in ("region", "city"):
            assert f"{root}.{leaf}" in described, f"{root}.{leaf} не описан извлекателю"


def test_region_class_of_both_objects_is_derived_not_guessed():
    """Класс региона выводит гео по ответу DaData, а не модель.

    У первого объекта это было закреплено, у второго — нет: модель имела право
    проставить категорию объекта догадкой, а от неё зависит маршрут.
    """
    from interpretation.prompt import _EXCLUDED_MODEL_PATHS
    from interpretation.reducer import _DERIVED_ONLY_PATHS

    for path in ("assets.realty.region_class", "assets.realty_2.region_class"):
        assert path in _EXCLUDED_MODEL_PATHS, f"{path} модель писать не должна"
        assert path in _DERIVED_ONLY_PATHS, f"{path} принимается только от гео"
