"""Регион не должен теряться, даже если его не распознал никто.

Андрей 12.08.2026: `residence_region` участвует в матчинге во всех вертикалях и
на их стороне заполнен всегда. У нас он берётся из ответа клиента, и если ни
извлекатель, ни DaData не разобрали реплику, поле уходит пустым — а пустой
регион означает `category: null` для любого клиента.

Поэтому последний рубеж: сырой ответ на вопрос о регионе сохраняется как есть.
CRM разбирает названия в свободной форме, и «Мухосранск» ей полезнее, чем null.
"""
from __future__ import annotations

import pytest

import core


def _asked_region_state() -> dict:
    from state import init_state

    state = init_state()
    state["facts"] = {"client": {"name": "Олег"}}
    state["plan"] = {"action": "ask", "fields": ["client.region_class"]}
    return state


def test_raw_answer_is_kept_when_nothing_recognised_it():
    state = _asked_region_state()

    core._keep_raw_location(state, "Мухосранск")

    assert state["facts"]["client"]["region"] == "Мухосранск"


def test_recognised_location_is_not_overwritten():
    """Разобранный DaData город точнее сырой реплики."""
    state = _asked_region_state()
    state["facts"]["client"]["city"] = "Казань"

    core._keep_raw_location(state, "да вроде Казань, я же говорил")

    assert "region" not in state["facts"]["client"]


def test_question_back_is_not_taken_for_a_city():
    """«А зачем вам это?» — не название населённого пункта."""
    state = _asked_region_state()

    core._keep_raw_location(state, "А зачем вам это?")

    assert "region" not in state["facts"]["client"]


def test_long_rambling_is_not_taken_for_a_city():
    state = _asked_region_state()

    core._keep_raw_location(state, "ну я вообще-то родился в одном месте, "
                                   "а живу давно в другом, и там ещё дача есть")

    assert "region" not in state["facts"]["client"]


def test_nothing_happens_when_the_question_was_about_something_else():
    state = _asked_region_state()
    state["plan"] = {"action": "ask", "fields": ["client.age"]}

    core._keep_raw_location(state, "Мухосранск")

    assert "region" not in state["facts"]["client"]


def test_realty_region_is_kept_too():
    state = _asked_region_state()
    state["plan"] = {"action": "ask", "fields": ["assets.realty.region_class"]}
    state["facts"]["assets"] = {"has_realty": True, "realty": {}}

    core._keep_raw_location(state, "деревня Гадюкино")

    assert state["facts"]["assets"]["realty"]["region"] == "деревня Гадюкино"


def test_our_own_guess_is_replaced_by_a_later_answer():
    """Сырой ответ — догадка, и она не должна пережить настоящий ответ.

    Живой прогон 12.08.2026: в тот же ход, когда спрашивали регион второго
    объекта, бот успел спросить и про собственника. Ответ «Тоже моя, в
    собственности» осел в поле региона, а следующая реплика «Москва» его уже не
    заменила — прежний вариант считал любое непустое значение окончательным. В
    лид уехало `CHAT_REALTY_REGION: Тоже моя, в собственности`.
    """
    state = _asked_region_state()
    core._keep_raw_location(state, "Тоже моя, в собственности")

    core._keep_raw_location(state, "Москва")

    assert state["facts"]["client"]["region"] == "Москва"


def test_a_recognised_city_still_wins_over_a_later_guess():
    """Замена разрешена только нашей же догадке, но не разобранному городу."""
    state = _asked_region_state()
    core._keep_raw_location(state, "Мухосранск")
    state["facts"]["client"]["city"] = "Казань"

    core._keep_raw_location(state, "ну где-то там")

    assert state["facts"]["client"]["region"] == "Мухосранск"


def test_new_city_replaces_a_previous_not_found_location():
    """Неудачный поиск прошлого ответа не должен навсегда блокировать новый."""
    state = _asked_region_state()
    state["facts"]["client"].update(
        {
            "city": "Старое название",
            "region": "Старый регион",
            "region_class": "rf_other",
            "location": {
                "status": "not_found",
                "query": "Старое название",
                "confirmation_required": True,
                "candidates": [],
            },
        }
    )

    core._keep_raw_location(state, "Москва")

    assert state["facts"]["client"] == {"name": "Олег", "region": "Москва"}


@pytest.mark.parametrize(
    "reply",
    [
        "это не Москва",
        "для чего Москва",
        "пожалуй Москва",
        "может Москва",
        "Москва или Казань",
    ],
)
def test_city_mention_without_a_definite_answer_is_not_kept(reply):
    state = _asked_region_state()

    core._keep_raw_location(state, reply)

    assert "region" not in state["facts"]["client"]


def _with_last_question(state: dict, question: str) -> dict:
    state["chat_history"] = [
        {"role": "user", "content": "здравствуйте"},
        {"role": "assistant", "content": question},
    ]
    return state


def test_answer_to_another_question_never_lands_in_the_region():
    """Живой прогон 12.08.2026: в поле региона оказался ответ про зарплату.

    План хода называет поле, но реплику формулирует модель, и она может
    спросить другое — а «последний рубеж» верил плану на слово. В лид уехало
    `CHAT_REALTY_REGION: Да, официально. 120 тысяч в месяц`. Спрашиваем нашу же
    последнюю реплику: было ли это вообще вопросом про место.
    """
    state = _with_last_question(
        _asked_region_state(),
        "Вы официально трудоустроены? Какой у вас сейчас ежемесячный доход?",
    )

    core._keep_raw_location(state, "Да, официально. 120 тысяч в месяц.")

    assert "region" not in state["facts"]["client"]


def test_answer_to_a_place_question_is_still_kept():
    state = _with_last_question(
        _asked_region_state(),
        "А в каком городе или населённом пункте вы живёте?",
    )

    core._keep_raw_location(state, "Мухосранск")

    assert state["facts"]["client"]["region"] == "Мухосранск"


@pytest.mark.parametrize(
    "reply",
    [
        "Пока не могу, не дома.",
        "не помню точно название",
        "не знаю",
        "потом скажу",
    ],
)
def test_a_refusal_is_not_a_place(reply):
    """Живой прогон 12.08.2026: «Пока не могу, не дома» уехало в CHAT_REALTY_REGION.

    Вопрос про место бот задал, реплика короткая и без знака вопроса — прежние
    проверки её пропускали. Пустой регион плох, но строка, в которой нет
    названия, не лучше: CRM по ней тоже ничего не найдёт, а менеджер увидит
    в лиде бессмыслицу.
    """
    state = _with_last_question(
        _asked_region_state(), "А в каком городе находится этот дом?"
    )

    core._keep_raw_location(state, reply)

    assert "region" not in state["facts"]["client"]


@pytest.mark.parametrize(
    "reply",
    [
        "Мухосранск",
        "Казань, Татарстан",
        "Тверская область",
        "деревня Лукино",
        "живу в Москве",
    ],
)
def test_a_real_answer_still_gets_through(reply):
    state = _with_last_question(
        _asked_region_state(), "А в каком городе вы живёте?"
    )

    core._keep_raw_location(state, reply)

    assert state["facts"]["client"]["region"] == reply


def test_the_guess_is_never_written_to_a_property():
    """Догадка допустима про клиента, но не про объект.

    Живой прогон 12.08.2026: клиент раздражённо повторил адрес дома под Тверью,
    а курсор анкеты стоял уже на московской квартире — и тверской адрес уехал
    в CRM регионом московской квартиры. Регион объекта определяет его
    категорию: пустое поле честнее, чем чужой адрес, а `residence_region`
    клиента такой цены не имеет — он участвует в матчинге, но объект собой не
    подменяет.
    """
    from state import init_state

    state = init_state()
    state["facts"] = {"assets": {"realty_2": {"kind": "flat"}}}
    state["plan"] = {"action": "ask", "fields": ["assets.realty_2.region_class"]}
    _with_last_question(state, "А в каком городе находится эта квартира?")

    core._keep_raw_location(state, "Михайловское, Калининский район")

    assert "region" not in state["facts"]["assets"]["realty_2"]
