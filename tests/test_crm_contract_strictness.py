"""Кто пишет поле — тот и отвечает за его имя.

04.09.2026 CRM добавила в ответ квалификации поле `result.widget_denis`.
Контракт бота объявлен с `extra="forbid"`, разбор ответа падал, и три дня
каждая завершённая анкета оставалась без лида — девятнадцать клиентов.

Правило, которым это закрыто, простое и несимметричное:

* **ответы** CRM разбираем терпимо (`extra="ignore"`) — сервер вправе
  добавлять поля когда угодно, и читатель чужого API обязан их не замечать;
* **запросы** остаются строгими (`extra="forbid"`) — их собираем мы, и
  опечатка в имени поля должна падать на тестах, а не уезжать в CRM молча.

Проверка структурная, а не по одному случаю: она обходит все модели контракта
и требует правильного режима у каждой. Тест на `widget_denis` в
`test_crm_workflow.py` доказывает, что конкретный инцидент закрыт; этот —
что следующая добавленная модель не приедет со строгим разбором ответа.
"""
from __future__ import annotations

import inspect

import pytest
from pydantic import BaseModel

from delivery import crm_contracts


def _models() -> list[type[BaseModel]]:
    return [
        obj
        for _, obj in inspect.getmembers(crm_contracts, inspect.isclass)
        if issubclass(obj, BaseModel)
        and obj.__module__ == crm_contracts.__name__
        and not obj.__name__.startswith("_")
    ]


def _extra(model: type[BaseModel]) -> str | None:
    return model.model_config.get("extra")


RESPONSES = [m for m in _models() if m.__name__.endswith(("Response", "Result"))]
REQUESTS = [m for m in _models() if m.__name__.endswith("Request")]


def test_the_contract_has_models_of_both_kinds():
    """Страховка на сам обход: пустые списки сделали бы тесты ниже зелёными."""
    assert RESPONSES, "не найдено ни одной модели ответа CRM"
    assert REQUESTS, "не найдено ни одной модели запроса в CRM"


@pytest.mark.parametrize("model", RESPONSES, ids=lambda m: m.__name__)
def test_a_response_model_ignores_unknown_fields(model):
    """Иначе любое новое поле на стороне CRM снова остановит все лиды."""
    assert _extra(model) == "ignore", (
        f"{model.__name__} разбирает ответ CRM строго. Сервер добавит поле — и "
        "квалификация начнёт падать целиком, как 04.09.2026 с widget_denis. "
        "Наследуйте от _CrmResponseModel."
    )


@pytest.mark.parametrize("model", REQUESTS, ids=lambda m: m.__name__)
def test_a_request_model_refuses_unknown_fields(model):
    """Запрос собираем мы: лишнее поле здесь — опечатка, а не новость от CRM."""
    assert _extra(model) == "forbid", (
        f"{model.__name__} принимает произвольные поля. Опечатка в имени уедет "
        "в CRM молча, и поле просто не доедет до карточки."
    )


def test_an_unknown_field_really_passes_through_a_response():
    """Режим проверен поведением, а не только объявлением."""
    parsed = crm_contracts.LeadIntakeResponse.model_validate(
        {"result": {"leadID": 42}, "success": True, "поле_из_будущего": "есть"}
    )

    assert parsed.result.lead_id == 42


def test_an_unknown_field_in_a_request_still_raises():
    with pytest.raises(Exception):
        crm_contracts.FirstQualificationRequest.model_validate(
            {"опечатка_в_имени": 1}
        )
