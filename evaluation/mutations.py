"""Мутационные прогоны: та же история другими словами.

Одна роль проверяет один способ говорить. Но анкета обязана собрать одни и те
же факты и у человека, который пишет с опечатками и строчными буквами, и у
того, кто отвечает канцеляритом, и у того, кто вываливает всё одной репликой.
Если маршрут поехал от манеры речи — это дефект извлечения, который вшитыми
сценариями не найти никогда: они всегда написаны одним голосом, нашим.

Два вида мутаций:

- **без смены смысла** — факты те же, меняется только способ их произнести.
  Маршрут и собранные факты обязаны совпасть с эталоном;
- **со сменой смысла** — меняется один факт (город, наличие недвижимости).
  Ожидание не пишется руками: его считает сам движок по изменённым фактам,
  поэтому тест проверяет извлечение, а не бизнес-правила матрицы.

"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any

from evaluation.personas import Persona
from routing import Matrix, get_matrix, plan as build_plan
from routing.derive import derive_facts
from utils import set_by_path


@dataclass(frozen=True)
class Mutation:
    """Вариант роли и то, чего от него ждут."""

    id: str
    persona: Persona
    kind: str  # "meaning_preserved" | "meaning_changed"
    expected_route: str | None
    expected_facts: dict[str, Any]
    origin: str


# Манеры речи, которые не меняют ни одного факта. Формулировки намеренно
# описывают ПОВЕДЕНИЕ, а не текст реплик: конкретные слова придумывает модель.
MANNER_MUTATIONS: dict[str, str] = {
    "typos": (
        "Пишешь быстро и небрежно: строчными буквами, без запятых, с опечатками "
        "(«кредт», «квартра», «тыщ»). Смысл не меняешь."
    ),
    "bureaucratic": (
        "Говоришь канцеляритом, как в заявлении: «имею в собственности жилое "
        "помещение», «трудоустроен официально», «задолженность составляет»."
    ),
    "dump_everything": (
        "На первый же вопрос вываливаешь сразу всё, что знаешь о своей ситуации, "
        "одной длинной репликой. Дальше отвечаешь только на переспросы."
    ),
    # Раньше здесь стояло «приблизительно: под миллион». Это меняло сами числа,
    # то есть мутация переставала сохранять смысл и требовала совпадения с
    # эталоном, которому сама же противоречила. Прогон 12.08.2026: три провала
    # из четырёх были этим.
    "numbers_in_words": (
        "Числа называешь словами, но ТОЧНО: «четыреста тысяч», «сорок четыре "
        "года», «девятьсот тысяч». Цифрами не пишешь никогда, округлять и "
        "прикидывать нельзя — суммы называешь ровно те, что в истории."
    ),
    # Три манеры 26.09.2026 — ровно под правки после жалоб заказчика: лид
    # 3497115 («400 общая сумма» ушло в CRM рублями), лид 3498907 («Пока три
    # дня» не легло в корзину срока), лид 3496486 («Ее нет» про просрочку
    # потерялось на сдвоенном вопросе).
    "bare_numbers": (
        "Суммы называешь голыми числами, без слов «тысяч» и «миллион»: вместо "
        "«четыреста тысяч» пишешь «400», вместо «миллион» — «1000». Если бот "
        "переспросит, тысячи ли это, — подтверждаешь: «да, тысяч». Сами числа "
        "ровно те, что в истории."
    ),
    "spoken_durations": (
        "Сроки называешь по-житейски, как в разговоре: «пару недель», «месяца "
        "три», «около полугода», «год с лишним» — но так, чтобы срок из истории "
        "оставался верным. Готовых формулировок вроде «от 3 до 6 месяцев» не "
        "используешь."
    ),
    "short_negations": (
        "На вопросы, есть ли что-то, отвечаешь одним-двумя словами: «нет», "
        "«нету», «её нет», «не имеется». Подробностей сам не добавляешь, "
        "пока не спросят."
    ),
}


def route_for(facts: dict[str, Any], matrix: Matrix | None = None) -> str | None:
    """Маршрут, который движок выбирает по готовым фактам.

    Эталон считается здесь, а не пишется руками: иначе тест начнёт проверять
    мои представления о матрице вместо самой матрицы.

    Имя и производная кредитная история добавляются сами: в эталоне роли их
    нет — имя не влияет на маршрут, а история выводится из ответов про
    просрочку тем же кодом, что и в рантайме.
    """
    active = matrix or get_matrix()
    prepared = json.loads(json.dumps(facts))
    prepared.setdefault("client", {}).setdefault("name", "Клиент")
    prepared = derive_facts(prepared)
    current = build_plan(active, prepared)
    if current.action not in {"route_found", "no_fit", "contacts"}:
        raise ValueError(
            f"эталонные факты неполны: движок всё ещё спрашивает "
            f"{current.field_paths}"
        )
    return current.matched.id if current.matched else None


def preserved_meaning(persona: Persona) -> list[Mutation]:
    """Те же факты, другой голос. Маршрут обязан остаться прежним."""
    expected = route_for(persona.facts)
    return [
        Mutation(
            id=f"{persona.id}__{name}",
            persona=replace(persona, id=f"{persona.id}__{name}", manner=manner),
            kind="meaning_preserved",
            expected_route=expected,
            expected_facts=persona.facts,
            origin=persona.id,
        )
        for name, manner in MANNER_MUTATIONS.items()
    ]


# Подмены одного факта. Ожидание не задаётся: его считает движок.
MEANING_MUTATIONS: dict[str, tuple[str, Any]] = {
    "to_moscow": ("client.region_class", "moscow_mo"),
    "to_regions": ("client.region_class", "rf_other"),
    "no_realty": ("assets.has_realty", False),
}


def changed_meaning(persona: Persona) -> list[Mutation]:
    """Изменён один факт — маршрут обязан пойти туда, куда скажет движок."""
    mutations: list[Mutation] = []
    for name, (path, value) in MEANING_MUTATIONS.items():
        facts = json.loads(json.dumps(persona.facts))
        set_by_path(facts, path, value)
        # Недвижимость исчезла — детали объекта тоже, иначе факты противоречивы.
        if path == "assets.has_realty" and value is False:
            facts.get("assets", {}).pop("realty", None)
        if facts == persona.facts:
            continue
        expected = route_for(facts)
        if expected == route_for(persona.facts):
            # Подмена ничего не изменила — проверять нечего.
            continue
        mutations.append(
            Mutation(
                id=f"{persona.id}__{name}",
                persona=replace(
                    persona,
                    id=f"{persona.id}__{name}",
                    story=_restated_story(persona, path, value),
                    facts=facts,
                ),
                kind="meaning_changed",
                expected_route=expected,
                expected_facts=facts,
                origin=persona.id,
            )
        )
    return mutations


# Города ролей во всех формах, в которых они встречаются в историях. Порядок —
# от длинных к коротким: «Санкт-Петербург» должен совпасть раньше, чем
# «Петербург», иначе город жительства останется прежним, а недвижимость уедет
# в другой — и роль снова начнёт себе противоречить.
_CITY_FORMS = tuple(sorted((
    "Санкт-Петербурге", "Санкт-Петербург", "Петербурге", "Петербург",
    "посёлок под Воронежем", "Воронежем", "Воронеж",
    "Екатеринбурге", "Екатеринбург", "Новосибирске", "Новосибирск",
    "Краснодаре", "Краснодар", "Казани", "Казань", "Самаре", "Самара",
    "Москве", "Москва",
), key=len, reverse=True))
_CITY_WORDS = {"moscow_mo": "Москве", "rf_other": "Самаре"}


def _restated_story(persona: Persona, path: str, value: Any) -> str:
    """Переписывает историю под изменённый факт.

    Сначала я просто дописывал в конец «ВАЖНО: недвижимости нет» — и получал
    историю, которая сама себе противоречит: выше написано «есть квартира в
    Казани». Ролевая модель, разумеется, выбирала первое, и мутация проверяла
    не бота, а мою невнимательность. Прогон 12.08.2026: три провала из четырёх
    оказались именно этим.
    """
    story = persona.story
    if path == "client.region_class":
        for original in _CITY_FORMS:
            story = re.sub(original, _CITY_WORDS[value], story, flags=re.IGNORECASE)
        return story
    if path == "assets.has_realty" and value is False:
        # Предложения про недвижимость вырезаются целиком, а не оспариваются.
        kept = [
            sentence
            for sentence in re.split(r"(?<=[.!?])\s+", story)
            if not re.search(r"квартир|комнат|недвижим|\bдом\b", sentence, re.I)
        ]
        return " ".join(kept).strip() + " Никакой недвижимости у тебя нет."
    return story


def build_mutations(personas: list[Persona]) -> list[Mutation]:
    """Полный набор: только роли с эталонными фактами."""
    mutations: list[Mutation] = []
    for persona in personas:
        if not persona.facts:
            continue
        mutations.extend(preserved_meaning(persona))
        mutations.extend(changed_meaning(persona))
    return mutations
