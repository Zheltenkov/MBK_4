"""Имя спрашивают один раз, дальше анкета идёт без него.

Вторая по частоте точка обрыва в бою — вопрос об имени, 17 диалогов за три
недели. Пока имя не названо, планировщик не пускал дальше ничего: любая
реплика, из которой имя не извлеклось, возвращала тот же вопрос.

Для CRM обязателен только телефон. Имя помечено `contact: true`, поэтому после
снятия шлагбаума оно добирается вместе с телефоном в конце — данные не
теряются, а разговор не встаёт.
"""
from __future__ import annotations

from routing import get_matrix, plan


def test_the_name_is_asked_first_when_nobody_asked_yet():
    result = plan(get_matrix(), {}, attempts={})

    assert [field.path for field in result.fields] == ["client.name"]


def test_after_one_unanswered_ask_the_questionnaire_starts():
    """Раньше здесь снова возвращался вопрос об имени, и так до бесконечности."""
    result = plan(get_matrix(), {}, attempts={"client.name": 1})

    assert result.action == "ask"
    assert "client.name" not in [field.path for field in result.fields]


def test_the_name_comes_back_with_the_phone_at_the_end():
    facts = {
        "request": {"amount": 500_000},
        "client": {"age": 44, "region_class": "msk_mo"},
        "debts": {"has_active_credits": False},
        "employment": {"type": "official", "income": 80_000},
        "assets": {"has_realty": False, "has_vehicle": False},
    }

    result = plan(get_matrix(), facts, max_fields=4, attempts={"client.name": 1})

    assert result.action == "contacts"
    assert {"client.name", "client.phone"} <= {field.path for field in result.fields}


def test_a_named_client_never_sees_the_question_again():
    result = plan(get_matrix(), {"client": {"name": "Иван"}}, attempts={"client.name": 1})

    assert "client.name" not in [field.path for field in result.fields]
