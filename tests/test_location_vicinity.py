"""«Под Воронежем» — это Воронежская область, а не повод держать человека.

Ролевой прогон 28.09.2026 и бой: на «дом в деревне под Воронежем» бот
требовал название деревни, дважды слово в слово повторил «Без точного
названия не смогу учесть локацию» и прочитал нотацию «„под Воронежем“ — это
направление, а не название». DaData на «под Воронежем» не находит ничего:
город стоит в творительном падеже.

Теперь из «под X», «около X», «рядом с X» берётся город X в начальной форме,
по нему — область, и класс считается по области: деревня рядом с городом не
равна городу, в списки маршрутов она не попадает. А если адрес так и не
найден, повторный вопрос звучит иначе, чем первый.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import core
from geo.dadata import AddressCandidate, AddressResolution
from geo.enrichment import _vicinity_city_names, enrich_locations


def _city(region: str, iso: str, city: str) -> AddressCandidate:
    return AddressCandidate(
        value=f"{region}, г {city}",
        unrestricted_value=f"{region}, г {city}",
        country="Россия",
        country_iso_code="RU",
        region=region,
        region_with_type=region,
        region_fias_id=f"fias-{iso}",
        region_iso_code=iso,
        city=city,
        city_with_type=f"г {city}",
        city_fias_id=f"fias-{city}",
        settlement=None,
        settlement_with_type=None,
        settlement_fias_id=None,
        fias_id=f"fias-{city}",
        fias_level=4,
    )


VORONEZH = _city("Воронежская обл", "RU-VOR", "Воронеж")
MOSCOW = _city("г Москва", "RU-MOW", "Москва")


def _resolver(known: dict[str, AddressCandidate]):
    asked: list[str] = []

    def resolve(query: str) -> AddressResolution:
        asked.append(query)
        candidate = known.get(query)
        if candidate is None:
            return AddressResolution(status="not_found", query=query)
        return AddressResolution(
            status="resolved", query=query, candidate=candidate, candidates=(candidate,)
        )

    resolve.asked = asked  # type: ignore[attr-defined]
    return resolve


@pytest.mark.parametrize(
    "said, city",
    [
        ("под Воронежем", "Воронеж"),
        ("Под Воронежем, в деревне", "Воронеж"),
        ("деревня под тверью в тверской области", "Тверь"),
        ("около Воронежа", "Воронеж"),
        ("недалеко от Твери", "Тверь"),
        ("рядом с Самарой", "Самара"),
        ("под Москвой", "Москва"),
        ("под Уфой", "Уфа"),
    ],
)
def test_city_is_taken_out_of_a_vicinity_phrase(said, city):
    assert city in _vicinity_city_names(said)


@pytest.mark.parametrize("said", ["Воронеж", "Воронежская область", "в деревне у нас"])
def test_no_vicinity_no_guess(said):
    assert _vicinity_city_names(said) == []


def test_house_near_a_city_gets_the_region_of_the_city(matrix):
    """Воронеж в списке маршрутов, но деревня под ним — нет: класс по области."""
    facts = enrich_locations(
        {"assets": {"realty": {"region": "под Воронежем"}}},
        "под Воронежем",
        matrix,
        resolver=_resolver({"Воронеж": VORONEZH}),
    )

    realty = facts["assets"]["realty"]
    assert realty["region"] == "Воронежская обл"
    assert realty["region_class"] == "rf_other"
    assert not realty.get("city")
    assert realty["location"]["confirmation_required"] is False


def test_the_city_itself_still_counts_as_the_city(matrix):
    """Без «под» Воронеж — это сам Воронеж, и список маршрутов его знает."""
    facts = enrich_locations(
        {"assets": {"realty": {"city": "Воронеж"}}},
        "Воронеж",
        matrix,
        resolver=_resolver({"Воронеж": VORONEZH}),
    )

    assert facts["assets"]["realty"]["region_class"] != "rf_other"


def test_near_moscow_is_moscow_region(matrix):
    facts = enrich_locations(
        {"client": {"region": "под Москвой"}},
        "под Москвой",
        matrix,
        resolver=_resolver({"Москва": MOSCOW, "Московская область": MOSCOW}),
    )

    assert facts["client"]["region_class"] == "moscow_mo"


def test_unknown_city_near_leaves_the_question_open(matrix):
    facts = enrich_locations(
        {"assets": {"realty": {"region": "под Нигдеградом"}}},
        "под Нигдеградом",
        matrix,
        resolver=_resolver({}),
    )

    location = facts["assets"]["realty"]["location"]
    assert location["confirmation_required"] is True


def test_answer_that_did_not_help_is_counted(matrix):
    """«Зачем?» адреса не даёт. Без счётчика следующий вопрос звучал тем же
    текстом, и модель повторяла его слово в слово."""
    facts = {
        "assets": {
            "realty": {
                "region": "в деревне у нас",
                "location": {
                    "status": "not_found",
                    "query": "в деревне у нас",
                    "confirmation_required": True,
                    "candidates": [],
                },
            }
        }
    }

    updated = enrich_locations(facts, "А зачем это нужно?", matrix, resolver=_resolver({}))

    assert updated["assets"]["realty"]["location"]["attempts"] == 1


def _state(attempts: int) -> dict:
    return {
        "facts": {
            "assets": {
                "realty": {
                    "location": {
                        "status": "not_found",
                        "confirmation_required": True,
                        "candidates": [],
                        "attempts": attempts,
                    }
                }
            }
        }
    }


PLAN = SimpleNamespace(action="ask", field_paths=["assets.realty.region_class"], fields=[])


def test_second_ask_about_the_place_sounds_different_and_accepts_the_region():
    first = core._location_confirmation_instruction(_state(0), PLAN)
    second = core._location_confirmation_instruction(_state(1), PLAN)

    assert first != second
    lowered = second.lower()
    assert "не повторяй" in lowered
    assert "достаточно области" in lowered
    assert "направление" in lowered  # запрет нотации назван прямо


def test_first_ask_already_allows_the_region_only():
    assert "достаточно области" in core._location_confirmation_instruction(_state(0), PLAN).lower()


def test_turn_about_something_else_is_not_counted(matrix):
    facts = {
        "assets": {
            "realty": {
                "region": "в деревне у нас",
                "location": {
                    "status": "not_found",
                    "query": "в деревне у нас",
                    "confirmation_required": True,
                    "candidates": [],
                },
            }
        }
    }

    updated = enrich_locations(
        facts, "Нет", matrix, resolver=_resolver({}), asked_paths=["assets.has_vehicle"]
    )

    assert not updated["assets"]["realty"]["location"].get("attempts")


def test_near_moscow_is_the_moscow_region_not_the_city(matrix):
    """«Под Москвой» — Московская область: у городов федерального значения
    окрестности лежат в соседнем субъекте, и в CRM уходит именно он."""
    mo = AddressCandidate(
        value="Московская обл",
        unrestricted_value="Московская обл",
        country="Россия",
        country_iso_code="RU",
        region="Московская",
        region_with_type="Московская обл",
        region_fias_id="fias-mo",
        region_iso_code="RU-MOS",
        city=None,
        city_with_type=None,
        city_fias_id=None,
        settlement=None,
        settlement_with_type=None,
        settlement_fias_id=None,
        fias_id="fias-mo",
        fias_level=1,
    )
    facts = enrich_locations(
        {"assets": {"realty": {"region": "под Москвой"}}},
        "под Москвой",
        matrix,
        resolver=_resolver({"Московская область": mo, "Москва": MOSCOW}),
    )

    realty = facts["assets"]["realty"]
    assert realty["region"] == "Московская обл"
    assert realty["region_class"] == "moscow_mo"


# --------------------------------------------------------------------------- #
# «В Казани» — это Казань. Мутационный прогон 01.10.2026: на «В Казани,
# Татарстан» DaData находила только республику, квартира получала класс
# «остальная РФ» вместо списка городов, и клиент терял залоговый маршрут.
# --------------------------------------------------------------------------- #
from geo.enrichment import _in_city_queries  # noqa: E402

KAZAN = _city("респ Татарстан", "RU-TA", "Казань")


@pytest.mark.parametrize(
    "said, fixed",
    [
        ("В Казани, Татарстан", "Казань, Татарстан"),
        ("в Москве", "Москва"),
        ("в Туле", "Тула"),
        ("в Воронеже", "Воронеж"),
        ("в Твери", "Тверь"),
        ("во Владимире", "Владимир"),
        ("в Чебоксарах", "Чебоксары"),
    ],
)
def test_city_in_the_prepositional_case_gets_its_dictionary_form(said, fixed):
    assert fixed in [query for _, query in _in_city_queries(said)]


@pytest.mark.parametrize("said", ["Казань, Татарстан", "Татарстан", "под Воронежем"])
def test_no_preposition_no_rewrite(said):
    assert _in_city_queries(said) == []


def test_flat_in_kazan_is_the_city_not_just_the_republic(matrix):
    resolver = _resolver({"Казань, Татарстан": KAZAN})
    said = enrich_locations(
        {"assets": {"realty": {"region": "В Казани, Татарстан"}}},
        "В Казани, Татарстан.",
        matrix,
        resolver=resolver,
    )
    plain = enrich_locations(
        {"assets": {"realty": {"region": "Казань, Татарстан"}}},
        "Казань, Татарстан",
        matrix,
        resolver=_resolver({"Казань, Татарстан": KAZAN}),
    )

    assert said["assets"]["realty"]["city"] == "г Казань"
    assert said["assets"]["realty"]["region_class"] == plain["assets"]["realty"]["region_class"]
    assert said["assets"]["realty"]["location"]["confirmation_required"] is False


def test_unknown_place_after_a_preposition_falls_back_to_the_plain_lookup(matrix):
    resolver = _resolver({})
    enrich_locations(
        {"assets": {"realty": {"region": "в деревне у нас"}}}, "в деревне у нас", matrix, resolver=resolver
    )

    assert resolver.asked[-1] == "в деревне у нас"


def test_prepositional_city_without_the_preposition_is_fixed_too(matrix):
    """Разборщик мог убрать «в» и сохранить «Казани, Татарстан». DaData на это
    отдаёт садовое товарищество в черте города — населённый пункт, не город."""
    snt = AddressCandidate(
        value="респ Татарстан, г Казань, тер снт Ромашка",
        unrestricted_value="респ Татарстан, г Казань, тер снт Ромашка",
        country="Россия",
        country_iso_code="RU",
        region="Татарстан",
        region_with_type="респ Татарстан",
        region_fias_id="fias-RU-TA",
        region_iso_code="RU-TA",
        city="Казань",
        city_with_type="г Казань",
        city_fias_id="fias-Казань",
        settlement="снт Ромашка",
        settlement_with_type="тер снт Ромашка",
        settlement_fias_id="fias-snt",
        fias_id="fias-snt",
        fias_level=6,
    )
    resolver = _resolver({"Казани, Татарстан": snt, "Казань, Татарстан": KAZAN})

    facts = enrich_locations(
        {"assets": {"realty": {"city": "Казани", "region": "Татарстан"}}},
        "В Казани, Татарстан.",
        matrix,
        resolver=resolver,
    )

    assert facts["assets"]["realty"]["city"] == "г Казань"


def test_a_plain_city_costs_one_lookup(matrix):
    """Сочи, Химки, Мытищи кончаются как предложный падеж, но это города:
    обычный поиск их находит, и лишних запросов быть не должно."""
    sochi = _city("Краснодарский край", "RU-KDA", "Сочи")
    resolver = _resolver({"Сочи": sochi})

    enrich_locations({"assets": {"realty": {"city": "Сочи"}}}, "Сочи", matrix, resolver=resolver)

    assert resolver.asked == ["Сочи"]


# --------------------------------------------------------------------------- #
# Разборщик сократил «под Воронежем» до «Воронеж» (мутационный прогон 04.10.2026)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "realty",
    [{"city": "Воронеж"}, {"region": "Воронеж"}, {"city": "Воронеж", "region": "Воронежская область"}],
)
def test_the_client_wording_beats_the_shortened_city(matrix, realty):
    """Клиент: «дом в деревне под Воронежем». В фактах от этого остаётся
    «Воронеж» — и дом получал класс города из списка маршрутов."""
    facts = enrich_locations(
        {"assets": {"realty": dict(realty)}},
        "Да, дом в деревне под Воронежем, я одна собственница",
        matrix,
        resolver=_resolver({"Воронеж": VORONEZH, "Воронеж, Воронежская область": VORONEZH}),
    )

    realty_facts = facts["assets"]["realty"]
    assert realty_facts["region"] == "Воронежская обл"
    assert realty_facts["region_class"] == "rf_other"
    assert not realty_facts.get("city")


def test_a_house_near_the_city_the_client_lives_in_is_not_that_city(matrix):
    """Живёт в Воронеже, дом под Воронежем: место жительства не переносится."""
    resolver = _resolver({"Воронеж": VORONEZH})
    facts = enrich_locations({"client": {"city": "Воронеж"}}, "Воронеж", matrix, resolver=resolver)
    facts["assets"] = {"realty": {"city": "Воронеж"}}

    facts = enrich_locations(facts, "дом в деревне под Воронежем", matrix, resolver=resolver)

    assert facts["client"]["city"] == "г Воронеж"
    assert facts["assets"]["realty"]["region_class"] == "rf_other"
    assert not facts["assets"]["realty"].get("city")


def test_a_vicinity_of_another_city_in_the_message_is_ignored(matrix):
    """«Под Воронежем» в реплике не трогает объект, названный в другом городе."""
    facts = enrich_locations(
        {"assets": {"realty": {"city": "Воронеж"}}},
        "Квартира в Воронеже, а работаю под Липецком",
        matrix,
        resolver=_resolver(
            {"Воронеж": VORONEZH, "Липецк": _city("Липецкая обл", "RU-LIP", "Липецк")}
        ),
    )

    assert facts["assets"]["realty"]["region_class"] != "rf_other"
