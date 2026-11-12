"""ПД остаются в state, но не попадают в LLM payload и observability input."""
from __future__ import annotations

import json
from datetime import date

import pytest

import core
import llm_agent
from config import AppConfig
from pii_masking import (
    contains_private_placeholder,
    extract_local_pii_updates,
    mask_payload_for_llm,
    mask_session_snapshot,
)
from utils import apply_fact_updates


@pytest.mark.parametrize("token", [
    "[имя скрыто]", "[ФИО скрыто]", "[телефон]", "[город или регион скрыт]", "[сведения анкеты]",
])
def test_private_control_tokens_are_recognized_at_the_visible_output_boundary(token):
    assert contains_private_placeholder("Ответ с обозначением " + token + ".")
    assert contains_private_placeholder(token.upper())


def test_placeholder_boundary_preserves_ordinary_language_and_other_bracketed_text():
    assert not contains_private_placeholder("Телефон уже есть, повторять его не нужно.")
    assert not contains_private_placeholder("Это [пример] текста с пояснением.")


CONFIG = AppConfig(openrouter_api_key="sk-or-test", enable_pii_masking=True)


def test_phone_and_exact_address_are_masked_in_all_payload_parts():
    payload = {
        "facts": {
            "client": {
                "name": "Ирина Петрова",
                "display_name": "Ирина",
                "phone": "+7 999 123-45-67",
                "city": "Казань",
                "region": "Казань, ул. Баумана, дом 10",
            }
        },
        "short_history": [
            {"role": "user", "content": "Телефон +7 999 123-45-67, ул. Баумана 10"}
        ],
        "latest_user_message": "Меня Ирина Петрова, номер +7 999 123-45-67",
        "internal_summary": "Ирина Петрова, +7 999 123-45-67",
    }

    safe = mask_payload_for_llm(payload, True)
    serialized = json.dumps(safe, ensure_ascii=False)

    assert "+7 999 123-45-67" not in serialized
    assert "Петрова" not in serialized
    assert "Баумана" not in serialized
    assert safe["facts"]["client"]["name"] == "Ирина"
    assert safe["facts"]["client"]["phone"] == "[телефон]"
    # Исходный server-side payload не мутируется.
    assert payload["facts"]["client"]["phone"] == "+7 999 123-45-67"


def test_phone_and_birth_date_are_extracted_locally():
    updates = extract_local_pii_updates(
        "Меня зовут Василий Петров, телефон 8 (999) 000-11-22, родился 10.05.1985"
    )
    by_path = {item["path"]: item["value"] for item in updates}
    expected_age = date.today().year - 1985 - ((date.today().month, date.today().day) < (5, 10))

    assert by_path["client.name"] == "Василий Петров"
    assert by_path["client.display_name"] == "Василий"
    assert by_path["client.phone"] == "+79990001122"
    assert by_path["client.age"] == expected_age


def test_city_and_realty_words_are_not_mistaken_for_exact_address():
    """Регрессия спокойного сценария: LLM должна видеть Москву и сам факт квартиры."""
    payload = {
        "facts": {"client": {"city": "Москва", "region": "Москва"}},
        "short_history": [
            {"role": "user", "content": "Есть квартира в Москве, полностью моя."},
        ],
        "latest_user_message": "Обычная квартира в Москве",
        "internal_summary": "Квартира клиента находится в Москве.",
    }

    safe = mask_payload_for_llm(payload, True)

    assert safe["facts"]["client"]["region"] == "Москва"
    assert safe["short_history"][0]["content"] == (
        "Есть квартира в Москве, полностью моя."
    )
    assert safe["latest_user_message"] == "Обычная квартира в Москве"
    assert safe["internal_summary"] == "Квартира клиента находится в Москве."


def test_ordinary_phrase_after_u_menya_is_not_extracted_as_name():
    updates = extract_local_pii_updates("Теперь у меня полная картина по условиям")

    assert all(item["path"] != "client.name" for item in updates)


def test_conversation_transport_receives_only_masked_messages(monkeypatch):
    captured: dict = {}

    def fake_post(config, body):
        captured.update(body)
        return {
            "choices": [{"message": {"content": "Понял."}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    monkeypatch.setattr(llm_agent, "_post", fake_post)
    payload = {
        "facts": {"client": {"name": "Ирина Петрова", "phone": "+79991234567"}},
        "short_history": [],
        "latest_user_message": "Мой номер +79991234567",
        "internal_summary": "",
        "turn_instruction": "Ответь коротко.",
    }

    assert llm_agent.generate_reply(payload, CONFIG) == "Понял."
    serialized = json.dumps(captured["messages"], ensure_ascii=False)
    assert "+79991234567" not in serialized
    assert "Петрова" not in serialized
    assert "[телефон]" in serialized


def test_full_name_shows_the_model_only_the_first_name():
    payload = {
        "facts": {"client": {"name": "Иванов Иван Иванович"}},
        "short_history": [],
        "latest_user_message": "",
        "internal_summary": "",
    }

    safe = mask_payload_for_llm(payload, True)
    serialized = json.dumps(safe, ensure_ascii=False)

    assert "Иванов" not in serialized
    assert "Иванович" not in serialized
    assert safe["facts"]["client"]["name"] == "Иван"


def test_full_name_without_a_determinable_first_name_is_fully_masked():
    payload = {
        "facts": {"client": {"name": "Тестэ Аватар Шмыг"}},
        "short_history": [],
        "latest_user_message": "",
        "internal_summary": "",
    }

    safe = mask_payload_for_llm(payload, True)
    serialized = json.dumps(safe, ensure_ascii=False)

    assert "Тестэ" not in serialized
    assert "Шмыг" not in serialized
    # Плейсхолдер модель принимала за имя и обращалась «[ФИО скрыто], …»
    # (QA 28.08.2026) — без подтверждённого имени ключа в фактах просто нет.
    assert "name" not in safe["facts"]["client"]


@pytest.mark.parametrize(
    "bare_name",
    [
        "Иван Иванов",
        "Иванов Иван Иванович",
    ],
)
def test_bare_full_name_answer_is_masked_before_external_extractor(
    bare_name,
    monkeypatch,
):
    monkeypatch.setenv("MBK_FACT_INTERPRETATION_MODE", "legacy")
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *args, **kwargs: {"fact_updates": [], "internal_summary": ""},
    )
    state = {
        "facts": {},
        "script": {"asked": ["client.name"]},
        "chat_history": [],
    }

    prepared = core.prepare_turn(state, bare_name, submit_delivery=False)
    safe = mask_payload_for_llm(
        {
            "facts": prepared["facts"],
            "short_history": [],
            "latest_user_message": bare_name,
        },
        True,
    )

    assert bare_name not in json.dumps(safe, ensure_ascii=False)
    assert prepared["facts"]["client"]["name"] == bare_name


@pytest.mark.parametrize("reply", ["Москва", "Нижний Новгород", "Пока Иван"])
def test_ambiguous_bare_reply_is_not_saved_as_a_name(reply, monkeypatch):
    monkeypatch.setattr(
        core.llm_agent,
        "extract_facts",
        lambda *args, **kwargs: {"fact_updates": [], "internal_summary": ""},
    )
    state = {
        "facts": {},
        "script": {"asked": ["client.name"]},
        "chat_history": [],
    }

    prepared = core.prepare_turn(state, reply, submit_delivery=False)

    assert not prepared["facts"].get("client", {}).get("name")


def test_common_one_word_name_is_preserved_and_safe_for_addressing():
    updates = extract_local_pii_updates(
        "Иван",
        expected_paths=["client.name"],
    )
    by_path = {item["path"]: item["value"] for item in updates}

    assert by_path == {"client.name": "Иван", "client.display_name": "Иван"}


@pytest.mark.parametrize(
    "full_name",
    [
        "Дмитрий Иванов",
        "Василий Петров",
        "Виталий Сидоров",
        "Анатолий Кузнецов",
        "Юрий Орлов",
    ],
)
def test_two_word_fio_with_iy_name_is_preserved_and_addressed_by_the_name(full_name):
    updates = extract_local_pii_updates(
        full_name,
        expected_paths=["client.name"],
    )
    by_path = {item["path"]: item["value"] for item in updates}

    assert by_path["client.name"] == full_name
    assert by_path["client.display_name"] == full_name.split()[0]


def test_name_question_context_masks_bare_fio_even_without_seeded_facts():
    safe = mask_payload_for_llm(
        {
            "facts": {},
            "expected_paths": ["client.name"],
            "latest_user_message": "Иван Иванов",
        },
        True,
    )

    assert safe["latest_user_message"] == "[ФИО скрыто]"


def test_preferred_name_does_not_overwrite_crm_full_name():
    existing = {"client": {"name": "Иванов Иван Иванович"}}

    updates = extract_local_pii_updates(
        "Меня зовут Ваня",
        existing_facts=existing,
    )
    by_path = {item["path"]: item["value"] for item in updates}
    merged = apply_fact_updates(existing, updates)

    assert "client.name" not in by_path
    assert by_path["client.display_name"] == "Ваня"
    assert merged["client"] == {
        "name": "Иванов Иван Иванович",
        "display_name": "Ваня",
    }


@pytest.mark.parametrize(
    "full_name",
    [
        "Иванов Иван Иванович",
        "Иван Иванович Иванов",
    ],
)
def test_explicit_mult_word_fio_is_addressed_by_the_first_name_in_any_order(full_name):
    updates = extract_local_pii_updates(
        f"Меня зовут {full_name}",
        expected_paths=["client.name"],
    )
    by_path = {item["path"]: item["value"] for item in updates}

    assert by_path["client.name"] == full_name
    assert by_path["client.display_name"] == "Иван"


def test_prepare_turn_skips_fio_extractor_without_using_the_surname(monkeypatch):
    def unexpected_extractor(*args, **kwargs):
        raise AssertionError("строгое ФИО уже сохранено локально")

    monkeypatch.setattr(core.llm_agent, "extract_facts", unexpected_extractor)
    monkeypatch.setattr(core.llm_agent, "extract_fact_candidates", unexpected_extractor)
    state = {
        "facts": {},
        "script": {"asked": ["client.name"]},
        "chat_history": [],
    }

    prepared = core.prepare_turn(
        state,
        "Меня зовут Иванов Иван Иванович",
        config=AppConfig(
            openrouter_api_key="sk-or-test",
            fact_interpretation_mode="semantic",
        ),
        submit_delivery=False,
    )

    assert prepared["facts"]["client"]["name"] == "Иванов Иван Иванович"
    # Обращение — по имени, а не по фамилии, которая стоит первой.
    assert prepared["facts"]["client"]["display_name"] == "Иван"


def test_prepare_turn_keeps_preferred_name_and_skips_redundant_extractor(monkeypatch):
    from config import AppConfig

    def unexpected_extractor(*args, **kwargs):
        raise AssertionError("строгое имя уже полностью разобрано локально")

    monkeypatch.setattr(core.llm_agent, "extract_facts", unexpected_extractor)
    monkeypatch.setattr(core.llm_agent, "extract_fact_candidates", unexpected_extractor)
    state = {
        "facts": {"client": {"name": "Иванов Иван Иванович"}},
        "script": {"asked": ["client.name"]},
        "chat_history": [],
    }

    prepared = core.prepare_turn(
        state,
        "Меня зовут Ваня",
        config=AppConfig(
            openrouter_api_key="sk-or-test",
            fact_interpretation_mode="semantic",
        ),
        submit_delivery=False,
    )

    assert prepared["facts"]["client"] == {
        "name": "Иванов Иван Иванович",
        "display_name": "Ваня",
    }


def test_extractor_transport_receives_masked_history_and_latest_message(monkeypatch):
    captured: dict = {}

    def fake_post(config, body, **kwargs):
        captured.update(body)
        return {
            "choices": [{"message": {"content": '{"fact_updates": [], "internal_summary": ""}'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    monkeypatch.setattr(llm_agent, "_post", fake_post)
    payload = {
        "facts": {"client": {"phone": "+79991234567"}},
        "short_history": [
            {"role": "assistant", "content": "Назовите телефон"},
            {"role": "user", "content": "+79991234567"},
        ],
        "latest_user_message": "+79991234567",
        "internal_summary": "",
    }

    result = llm_agent.extract_facts(payload, "", CONFIG)
    serialized = json.dumps(captured["messages"], ensure_ascii=False)

    assert result["fact_updates"] == []
    assert "+79991234567" not in serialized
    assert "[телефон]" in serialized


def test_persisted_session_snapshot_is_redacted():
    state = {
        "facts": {
            "client": {
                "name": "Ирина Петрова",
                "phone": "+79991234567",
                "city": "Казань",
                "region": "Казань, улица Баумана, дом 10",
            }
        },
        "chat_history": [
            {"role": "user", "content": "Телефон +79991234567"}
        ],
        "fact_events": [
            {
                "path": "client.phone",
                "evidence": "Мой телефон +79991234567",
            }
        ],
        "interpretation": {
            "last_result": {
                "candidates": [
                    {
                        "path": "client.region",
                        "value": "Казань, улица Баумана, дом 10",
                        "evidence": "Адрес Казань, улица Баумана, дом 10",
                    }
                ]
            }
        },
        "internal_summary": "Ирина Петрова, +79991234567",
    }
    anketa = {
        "full_name": "Ирина Петрова",
        "phone": "+79991234567",
        "living_address": "Казань, улица Баумана, дом 10",
    }

    safe_state, safe_anketa = mask_session_snapshot(state, anketa)
    serialized = json.dumps({"state": safe_state, "anketa": safe_anketa}, ensure_ascii=False)

    assert "+79991234567" not in serialized
    assert "Петрова" not in serialized
    assert "Баумана" not in serialized
    assert safe_state["fact_events"][0]["evidence"] == "Мой телефон [телефон]"
    shadow_candidate = safe_state["interpretation"]["last_result"]["candidates"][0]
    assert "Баумана" not in shadow_candidate["value"]
    assert "Баумана" not in shadow_candidate["evidence"]
    assert state["facts"]["client"]["phone"] == "+79991234567"


def test_surname_is_not_masked_inside_a_longer_word():
    """Замена шла подстрокой, без границ слова.

    На тестовом лиде 10.08.2026 клиент назвался «Тестов Тест Тестович»:
    фамилия «Тест» нашлась внутри обращения «Тестов», и модель получила в
    истории «[фамилия скрыта]ов» — а затем повторила это клиенту в лицо.
    Маскировать надо целые слова, иначе любая фамилия, оказавшаяся началом
    другого слова, ломает текст разговора.
    """
    payload = {
        "facts": {"client": {"name": "Тестов Тест Тестович"}},
        "short_history": [{"role": "assistant", "content": "Тестов, уточните адрес"}],
        "latest_user_message": "Тестов слушает",
        "internal_summary": "",
    }

    safe = mask_payload_for_llm(payload, True)
    serialized = json.dumps(safe, ensure_ascii=False)

    assert "[фамилия скрыта]ов" not in serialized
    assert "Тестович" not in serialized


def test_phone_is_still_masked_although_it_starts_with_a_plus():
    """Границы слова не должны отключить маскировку номера: «+» не буква."""
    payload = {
        "facts": {"client": {"phone": "+79991234567"}},
        "short_history": [{"role": "user", "content": "мой номер +79991234567"}],
        "latest_user_message": "",
        "internal_summary": "",
    }

    safe = mask_payload_for_llm(payload, True)

    assert "+79991234567" not in json.dumps(safe, ensure_ascii=False)


def test_outgoing_lead_payload_is_masked_in_the_snapshot():
    """Снимок унёс настоящий телефон клиента в чужой репозиторий.

    18.08.2026 в git заказчика нашлись два файла logs/sessions/*.json с
    незакрытым номером: маскировка знала про facts, историю и evidence, но не
    про state.delivery — исходящий payload лида, где телефон обязан быть
    настоящим. Контейнер скопировали целиком, вместе с накопленными снимками.
    """
    state = {
        "facts": {"client": {"name": "Андрей Смирнов", "phone": "+79295995335"}},
        "chat_history": [],
        "delivery": {
            "request": {
                "client": {
                    "name": "Андрей Смирнов",
                    "phone": "+79295995335",
                    "residence_address": "Москва, улица Тверская, дом 7",
                },
                "crm": {"PHONE": "79295995335", "PHONE_DEAL": "79295995335"},
            },
            "thanks_url": "https://www.mbk.ru/thanks",
        },
    }

    safe_state, _ = mask_session_snapshot(state, None)
    serialized = json.dumps(safe_state, ensure_ascii=False)

    assert "79295995335" not in serialized
    assert "Смирнов" not in serialized
    assert "Тверская" not in serialized
    # Полезное для разбора остаётся читаемым.
    assert safe_state["delivery"]["thanks_url"] == "https://www.mbk.ru/thanks"
    # Оригинал в памяти не тронут: из него строится настоящий запрос в CRM.
    assert state["delivery"]["request"]["client"]["phone"] == "+79295995335"


def test_unconfirmed_name_disappears_from_masked_facts_without_a_placeholder():
    """QA 28.08.2026: литерал «[ФИО скрыто]» модель приняла за имя клиента.

    Имя здесь не определяется ничем — ни словарём, ни отчеством, ни окончанием
    фамилии, — поэтому модель не видит ни имени, ни заглушки.
    """
    from pii_masking import Masker

    facts = {"client": {"name": "Тестэ Аватар Шмыг", "phone": "+79991234567"}}
    masked = Masker.from_facts(facts).mask_facts(facts)

    assert "name" not in masked["client"]
    assert "[ФИО" not in str(masked)


def test_first_name_from_a_full_name_with_patronymic_reaches_the_model():
    """Бой 19.10.2026: имя перед отчеством — надёжный признак, по нему бот
    обращается к клиенту. Фамилия и отчество модели по-прежнему не видны."""
    from pii_masking import Masker

    facts = {"client": {"name": "Тестов Тест Аватарович", "phone": "+79991234567"}}
    masked = Masker.from_facts(facts).mask_facts(facts)

    assert masked["client"]["name"] == "Тест"
    assert "Тестов" not in str(masked)
    assert "Аватарович" not in str(masked)


def test_confirmed_display_name_stays_in_masked_facts():
    from pii_masking import Masker

    facts = {
        "client": {"name": "Тестов Тест Аватарович", "display_name": "Тест"}
    }
    masked = Masker.from_facts(facts).mask_facts(facts)

    assert masked["client"]["name"] == "Тест"


def test_mask_placeholders_are_scrubbed_from_client_bubbles():
    from utils import guard_bubbles

    bubbles = guard_bubbles(["[ФИО скрыто], какая сумма вам нужна?"])

    assert bubbles == ["Какая сумма вам нужна?"]
