"""Обезличивание проверяется правилом, а не списком известных ключей.

Аудит 19.08.2026. Маскировка знала три ключа — имя, телефон, регион, — а анкета
кладёт в факты ещё дату рождения и два адреса. Прогон показал: «10.05.1985» и
«улица Баумана» уходили в зарубежную модель дословно и оставались в снимке на
диске.

В инструкции по установке при этом написано, что «фамилия, телефон, точная дата
рождения и адрес заменяются метками». Половина этой фразы была неправдой.

Тем же промахом раньше утёк телефон из state.delivery.request: маскировали
перечисление, а ПД лежат по всему дереву. Поэтому здесь проверяется инвариант —
обойти КАЖДЫЙ строковый лист и убедиться, что пробы в нём нет. Новое поле в
контракте больше не открывает дыру молча.
"""
from __future__ import annotations

import pytest

from pii_masking import mask_payload_for_llm, mask_session_snapshot
from state import init_state


ANKETA = {
    "full_name": "Ирина Петрова",
    "name": "Ирина",
    "phone": "+79991234567",
    "birth_date": "10.05.1985",
    "registration_address": "Казань, улица Баумана, дом 10, кв 5",
    "living_address": "Казань, улица Баумана, дом 10, кв 5",
    "city": "Казань",
    "desired_amount": 500000,
}

# Что не должно покидать наш сервер ни в каком виде.
PROBES = {
    "10.05.1985": "точная дата рождения",
    "Баумана": "улица",
    "Петрова": "фамилия",
    "79991234567": "телефон",
    "+7 999 123-45-67": "телефон в другом написании",
}


def _leaves(value, path=""):
    """Каждый строковый лист дерева вместе с путём до него."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _leaves(item, f"{path}[{index}]")


def _offenders(tree) -> list[str]:
    found = []
    for path, text in _leaves(tree):
        for probe, what in PROBES.items():
            if probe in text:
                found.append(f"{path}: {what} — {text[:60]}")
    return found


RAW_MESSAGE = "Меня зовут Ирина Петрова, телефон +7 999 123-45-67"


@pytest.fixture
def seeded_state():
    state = init_state(ANKETA)
    state["chat_history"] = [{"role": "user", "content": RAW_MESSAGE}]
    # Реплика хода: `core.prepare_turn` кладёт её в state сырой, чтобы
    # `turn_instruction` узнал прощание. Ревью 09.09.2026: в `chat_history` та
    # же строка маскировалась, а здесь лежала как есть — и уезжала в снимок.
    state["_last_user_message"] = RAW_MESSAGE
    # Журнал смысловой интерпретации. Проверка 10.09.2026 живым ходом: модель
    # предложила имя «Ирина», reducer записал событие с прежним значением —
    # полным ФИО из локального разбора, — и оно легло в снимок дословно:
    # маскировалось только `evidence`, а `previous_value` и `value` — нет.
    state["fact_events"] = [
        {
            "path": "client.name",
            "operation": "set",
            "status": "confirmed",
            "source": "semantic",
            "subject": "client",
            "evidence": RAW_MESSAGE,
            "previous_value": "Ирина Петрова",
            "value": "Ирина",
            "reason": "candidate_accepted",
        },
        {
            "path": "client.phone",
            "operation": "set",
            "status": "confirmed",
            "source": "semantic",
            "subject": "client",
            "evidence": None,
            "previous_value": None,
            "value": "79991234567",
            "reason": "candidate_accepted",
        },
    ]
    state["delivery"] = {
        "request": {"client": {"phone": "+79991234567", "name": "Ирина Петрова"}},
    }
    return state


def test_nothing_personal_reaches_the_foreign_model(seeded_state):
    payload = {
        "facts": seeded_state["facts"],
        "short_history": seeded_state["chat_history"],
        "latest_user_message": "Живу на улице Баумана, дом 10",
    }

    offenders = _offenders(mask_payload_for_llm(payload, True))

    assert not offenders, "в зарубежную модель уходят ПД:\n" + "\n".join(offenders)


def test_nothing_personal_stays_in_the_snapshot_on_disk(seeded_state):
    safe_state, safe_anketa = mask_session_snapshot(seeded_state, ANKETA)

    offenders = _offenders(safe_state) + _offenders(safe_anketa)

    assert not offenders, "в снимке на диске остались ПД:\n" + "\n".join(offenders)


def test_masking_keeps_what_the_bot_needs(seeded_state):
    """Обезличивание не должно съесть то, без чего бот не работает."""
    payload = mask_payload_for_llm(
        {"facts": seeded_state["facts"], "short_history": [], "latest_user_message": ""},
        True,
    )
    facts = payload["facts"]

    assert facts["client"]["name"] == "Ирина", "обращение по имени остаётся"
    assert facts["request"]["amount"] == 500000, "сумма не ПД"
    assert "Казань" in str(facts["client"].get("city") or facts["client"].get("region") or "")


def _state_with_history(*, current_name: str, previous_name: str | None) -> dict:
    """Состояние, где текущее и прежнее значение персонального поля РАЗНЫЕ.

    В `seeded_state` выше текущее `client.name` — тоже полное ФИО (анкета
    заполняет и `full_name`, и `name`), поэтому фамилия и без починки
    попадала в словарь замен через текущие факты. Ревью 10.09.2026: старый
    тест зазеленел не потому, что механизм работал, а потому, что текущее и
    прежнее значение совпали. Здесь анкеты нет вовсе — факты собраны вручную,
    и текущее имя сознательно короче того, что было раньше.
    """
    state = init_state()
    state["facts"] = {
        "client": {"name": current_name, "display_name": current_name},
    }
    events: list[dict] = []
    if previous_name is not None:
        events.append(
            {
                "path": "client.name",
                "operation": "set",
                "status": "confirmed",
                "source": "semantic",
                "subject": "client",
                "evidence": previous_name,
                "previous_value": previous_name,
                "value": current_name,
                "reason": "candidate_accepted",
            }
        )
    state["fact_events"] = events
    state["chat_history"] = []
    return state


def test_previous_full_name_from_fact_events_does_not_leak():
    """Прежнее ФИО из `previous_value` не должно остаться в снимке."""
    state = _state_with_history(current_name="Ирина", previous_name="Ирина Петрова")

    safe_state, _ = mask_session_snapshot(state, None)

    dumped = str(safe_state)
    assert "Петрова" not in dumped, "прежняя фамилия осталась в снимке"
    # Текущее обращение по имени — не ПД и должно остаться читаемым.
    assert "Ирина" in dumped


def test_previous_full_name_from_chat_history_does_not_leak():
    """Тот же корень бьёт не только журнал фактов, но и chat_history.

    Клиент написал полное ФИО голым текстом (ответ на «как вас зовут?», без
    «меня зовут»), reducer сохранил только короткое имя для обращения — и
    фамилия, названная в реплике, маскировщику текущих фактов не видна.
    """
    state = _state_with_history(current_name="Ирина", previous_name="Ирина Петрова")
    state["chat_history"] = [
        {"role": "user", "content": "Ирина Петрова"},
        {"role": "assistant", "content": "Приятно познакомиться, Ирина!"},
    ]

    safe_state, _ = mask_session_snapshot(state, None)

    dumped = str(safe_state)
    assert "Петрова" not in dumped, "фамилия из chat_history осталась в снимке"
    assert "Приятно познакомиться, Ирина!" in str(safe_state["chat_history"])


def test_previous_phone_from_fact_events_does_not_leak():
    """Тот же механизм для телефона: сменился номер — старый не должен остаться."""
    state = init_state()
    state["facts"] = {"client": {"phone": "79991234567"}}
    state["fact_events"] = [
        {
            "path": "client.phone",
            "operation": "set",
            "status": "confirmed",
            "source": "semantic",
            "subject": "client",
            "evidence": None,
            "previous_value": "79997654321",
            "value": "79991234567",
            "reason": "correction",
        }
    ]
    state["chat_history"] = []

    safe_state, _ = mask_session_snapshot(state, None)

    dumped = str(safe_state)
    assert "79997654321" not in dumped, "прежний телефон остался в снимке"
    assert "79991234567" not in dumped, "текущий телефон тоже должен маскироваться"


def test_message_without_pii_passes_through_untouched():
    """Контроль: обычная реплика без персональных данных не должна пострадать."""
    state = _state_with_history(current_name="Ирина", previous_name="Ирина Петрова")
    plain = "Хочу оформить рефинансирование на пятьсот тысяч рублей"
    state["chat_history"] = [{"role": "user", "content": plain}]

    safe_state, _ = mask_session_snapshot(state, None)

    assert safe_state["chat_history"][0]["content"] == plain


def test_the_raw_turn_message_never_reaches_the_file_on_disk(
    seeded_state, tmp_path, monkeypatch
):
    """Тот же снимок, но не в памяти, а файлом — как его увидел ревьюер.

    `mask_session_snapshot` проверяется выше на структуре; здесь проверяется
    весь путь до диска: `SessionStore.persist` → `_write_snapshot` →
    `logs/sessions/<sid>.json`. Именно этот файл 18.08.2026 уехал в чужой
    репозиторий, и именно его читают, когда разбирают диалог руками.
    """
    import session_store
    from session_store import Session, SessionStore
    from usage_tracker import SessionUsage

    directory = tmp_path / "sessions"
    directory.mkdir()
    monkeypatch.setattr(session_store, "SESSIONS_DIR", directory)

    session = Session(
        id="pii0000disk1",
        state=seeded_state,
        anketa=ANKETA,
        usage=SessionUsage(session_id="pii0000disk1"),
    )
    SessionStore().persist(session)

    text = (directory / "pii0000disk1.json").read_text(encoding="utf-8")

    for probe, what in PROBES.items():
        assert probe not in text, f"в файле снимка осталось: {what}"
