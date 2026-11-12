"""Инварианты доставки лида: законный ответ клиента не отменяет лид.

Здесь закрывается не отдельное значение, а целый класс промахов — «маппер CRM
отвергает код, который бот сам же и записал». Поэтому проверяется правило, а не
перечисление: берётся КАЖДОЕ значение КАЖДОГО перечислимого поля матрицы,
кладётся в анкету продакшн-путём (`core.apply_fact_updates` →
`routing.derive.derive_facts` — тот же вызов, что делает `prepare_turn`), и
требуется, чтобы лид полностью прошедшего анкету клиента всё равно уехал.

Правило зафиксировано заказчиком, docs/MBK_CRM_BACKEND_CONTRACT.md:
«Неподдерживаемый собственник/доля → realty_owner = null» и «category = null не
отменяет создание лида: lead-intake вызывается обязательно». Код без пары в CRM
обязан становиться пустым полем, а не отказом от лида: лид стоит 150 рублей,
пустое поле не стоит ничего.

Новое значение в matrix.yaml или новое перечислимое поле подхватываются тестом
сами — списка значений здесь нет намеренно.
"""
from __future__ import annotations

from typing import Any

from core import apply_fact_updates
from delivery.crm_mapping import map_first_qualification, map_lead_intake
from routing import get_fact
from routing.derive import derive_facts
from routing.normalize import canonical_enum_value
from utils import set_by_path


# Анкета, пройденная целиком, живой речью — как её отдаёт извлекатель. Телефон
# есть, согласие получено: клиент сделал всё, что от него требуется, и лид
# обязан уехать при любом законном значении любого поля.
COMPLETED_SURVEY: dict[str, str] = {
    "client.name": "Иванов Иван Иванович",
    "client.phone": "+7 999 123-45-67",
    "client.age": "41",
    "request.amount": "1 500 000",
    "debts.has_active_credits": "нет",
    "employment.type": "работаю официально",
    "employment.income": "120 000",
    "assets.has_realty": "да",
    "assets.realty.kind": "квартира",
    "assets.realty.region": "Москва",
    "assets.realty.city": "Москва",
    "assets.realty.ownership_form": "в собственности",
    "assets.realty.owners": "только я",
    "assets.has_vehicle": "нет",
}


def _survey(**answers: Any) -> dict[str, Any]:
    """Собирает факты ровно тем же путём, что и живой ход диалога."""
    updates = [
        {"path": path, "value": value}
        for path, value in {**COMPLETED_SURVEY, **answers}.items()
    ]
    return derive_facts(apply_fact_updates({}, updates))


def _lead(facts: dict[str, Any]):
    return map_lead_intake(
        facts,
        form_filled_out=True,
        site_consent_shown=True,
        site_consent_answer=True,
    )


def _enum_values(matrix) -> list[tuple[str, str]]:
    return [
        (path, value)
        for path, spec in matrix.fields.items()
        if spec.kind == "enum" and spec.values
        for value in spec.values
    ]


def test_no_legal_matrix_value_cancels_the_lead(matrix):
    """Любой законный код анкеты доезжает до CRM, а не убивает обращение."""
    lost: list[str] = []
    for path, value in _enum_values(matrix):
        facts = _survey(**{path: value})
        # Проверяем, что значение действительно легло в факты: иначе тест
        # разбирал бы анкету без ответа и молча зеленел. `hired` → `official` —
        # осознанный свод синонимов (routing/normalize.py, заказчик 12.08.2026).
        if get_fact(facts, path) != canonical_enum_value(path, value):
            # Не всякое значение матрицы приходит от извлекателя: часть полей
            # пишут derive, гео-слой и рабочий поток CRM, и нормализатор их не
            # видит. Домен поля задаёт матрица, поэтому такое значение кладём в
            # дерево напрямую — правило «лид уезжает» держится и для него.
            facts = _survey()
            set_by_path(facts, path, value)
        mapping = _lead(facts)
        if not mapping.ready:
            reasons = "; ".join(f"{i.code} {i.path}" for i in mapping.issues)
            lost.append(f"{path}={value!r}: лид не уехал — {reasons}")
    assert not lost, "законный код матрицы отменяет лид:\n" + "\n".join(lost)


def test_shared_ownership_from_live_speech_reaches_crm():
    """Сквозная проба словами клиента, а не подставленными кодами.

    «Доля в квартире» с «долями у родителей» — законный ответ: нормализатор
    сводит его к `other`/`others`, и ровно это предписывает извлекателю промпт.
    Кода в CRM для таких значений нет, и по контракту поля уезжают пустыми —
    но сам лид уехать обязан.
    """
    facts = _survey(
        **{
            "assets.realty.kind": "доля в квартире",
            "assets.realty.owners": "доли с родителями",
        },
    )
    assert get_fact(facts, "assets.realty.kind") == "other"
    assert get_fact(facts, "assets.realty.owners") == "others"

    mapping = _lead(facts)

    assert mapping.ready is True, [i.message for i in mapping.issues]
    assert mapping.request is not None
    payload = mapping.request.as_api_dict(exclude_none=True)
    # Неподдерживаемое значение — пустое поле, а не догадка о типе объекта.
    assert "UF_CRM_1452154011" not in payload
    assert "CHAT_OWNER" not in payload
    # Всё остальное, что клиент рассказал, на месте.
    assert payload["PHONE"] == "79991234567"
    assert payload["SUMM"] == 1_500_000
    assert payload["CHAT_REALTY_REGION"] == "Москва"


def test_second_realty_object_obeys_the_same_rule(matrix):
    """Вытеснивший первый объект второй тоже не имеет права отменять лид.

    Второй объект уезжает в ключах первого (Андрей, 12.08.2026) и читается теми
    же таблицами CRM — значит и промах у него ровно тот же.
    """
    second = {
        "assets.has_second_realty": "да",
        "assets.realty_2.region_class": "moscow_mo",
        "assets.realty_2.ownership_form": "private",
        "assets.realty_2.kind": "commercial",
        "assets.realty_2.owners": "client_and_spouse",
    }
    # Опора: второй объект действительно занимает слот первого. Без неё цикл
    # ниже проверял бы квартиру из базовой анкеты и всегда зеленел.
    control = _lead(_survey(**second))
    assert control.request is not None
    payload = control.request.as_api_dict(exclude_none=True)
    assert payload["UF_CRM_1452154011"] == 3075
    assert payload["CHAT_OWNER"] == "BOTH"

    lost: list[str] = []
    for path, value in _enum_values(matrix):
        if not path.startswith("assets.realty_2."):
            continue
        mapping = _lead(_survey(**{**second, path: value}))
        if not mapping.ready:
            reasons = "; ".join(f"{i.code} {i.path}" for i in mapping.issues)
            lost.append(f"{path}={value!r}: лид не уехал — {reasons}")
    assert not lost, "второй объект отменяет лид:\n" + "\n".join(lost)


def test_qualification_is_a_slice_of_the_lead(matrix):
    """Квалификация и лид читают одни факты — расходиться им нельзя.

    С 31.08.2026 CRM считает категорию дважды одним кодом: по
    first-qualification (бот по ней решает про офер и согласие) и заново по
    lead-intake (категория в Битриксе). Каждое поле квалификации обязано
    совпадать с тем же полем лида при любом законном ответе клиента, иначе
    клиенту покажут один продукт, а лид уйдёт в другой.

    Квалификация неподдерживаемый код кладёт в null, это закреплено тестом
    test_crm_mapping.py::test_unsupported_realty_owner_and_type_are_sent_as_null.
    Если тот же факт отменяет лид, клиент уходит с определённой категорией и
    без обращения — самый дорогой возможный исход.
    """
    disagreements: list[str] = []
    for path, value in _enum_values(matrix):
        facts = _survey(**{path: value})
        qualification = map_first_qualification(facts)
        lead = _lead(facts)
        if qualification.ready and not lead.ready:
            disagreements.append(
                f"{path}={value!r}: квалификация принимает, лид-интейк отменяет"
            )
            continue
        if qualification.request is None or lead.request is None:
            continue
        sent = qualification.request.as_api_dict()
        created = lead.request.as_api_dict()
        for field, sent_value in sent.items():
            if created.get(field) != sent_value:
                disagreements.append(
                    f"{path}={value!r}: {field} = {sent_value!r} в квалификации "
                    f"против {created.get(field)!r} в лиде"
                )
    assert not disagreements, "мапперы CRM расходятся:\n" + "\n".join(disagreements)
