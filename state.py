"""Состояние диалога и посев фактов из анкеты.

Факты хранятся ровно в тех путях, которыми оперирует матрица, — отдельной «внутренней»
схемы больше нет. За счёт этого движок, извлекатель и лид в микросервис говорят на одном
языке, и переклада между ними, в котором можно что-то потерять, просто не существует.
"""
from __future__ import annotations

from typing import Any

from routing import Matrix, get_matrix
from routing.normalize import normalize_birthdate, normalize_fact
from utils import normalize_display_name, set_by_path


# Мягкий потолок диалога. Жёстко не обрываем: клиент может задать пару вопросов уже
# после того, как ссылка отправлена.
SOFT_MESSAGE_LIMIT = 40

# Анкета с сайта → пути матрицы. Слева — поля формы, справа — то, что понимает движок.
ANKETA_MAP: dict[str, str] = {
    "full_name": "client.name",
    "phone": "client.phone",
    "birth_date": "client.age",
    "age": "client.age",
    "living_address": "client.region_class",
    "region": "client.region_class",
    "desired_amount": "request.amount",
    "debts_total": "debts.total",
    "employment_type": "employment.type",
    "overdue_duration": "debts.overdue_duration",
    "goal": "client.goal",
}


def seed_facts(anketa: dict[str, Any] | None, matrix: Matrix | None = None) -> dict[str, Any]:
    """Переносит анкету в факты, прогоняя каждое значение через нормализатор.

    Нераспознанное молча отбрасывается: пусть бот переспросит, чем поедет по чужому
    маршруту из-за кривого значения в форме.
    """
    facts: dict[str, Any] = {}
    if not anketa:
        return facts

    matrix = matrix or get_matrix()
    for source, path in ANKETA_MAP.items():
        if source not in anketa:
            continue
        value = normalize_fact(path, anketa.get(source), matrix)
        if value is not None:
            set_by_path(facts, path, value)

    # CRM по-прежнему получает полное ФИО ровно в client.name. Отдельное поле
    # формы ``name`` используем как предпочтительное обращение, но только если
    # это одно слово. Если full_name отсутствует, старый контракт с одним
    # ``name`` остаётся рабочим и для CRM.
    raw_name = anketa.get("name")
    seeded_client = facts.get("client")
    has_crm_name = isinstance(seeded_client, dict) and bool(seeded_client.get("name"))
    if not has_crm_name:
        value = normalize_fact("client.name", raw_name, matrix)
        if value is not None:
            set_by_path(facts, "client.name", value)
    if display_name := normalize_display_name(raw_name):
        set_by_path(facts, "client.display_name", display_name)

    # Полные адреса сохраняются ровно как ввёл клиент. DaData-нормализация живёт
    # отдельно и не должна уничтожить строку, которую ожидает CRM.
    # Дата рождения — единственное поле формы, у которого есть свой формат:
    # CRM принимает только ДД-ММ-ГГГГ. Хранилась она строкой как есть, и
    # привычное «10.05.1985» роняло проверку всего лида — заявка не уходила
    # вовсе из-за одного необязательного поля. Приводим к ISO сразу.
    if raw_birthdate := anketa.get("birth_date"):
        if normalized_birthdate := normalize_birthdate(raw_birthdate):
            set_by_path(facts, "client.birthdate", normalized_birthdate)

    raw_fields = (
        ("registration_address", "client.registration_address"),
        ("living_address", "client.residence_address"),
        ("living_address", "client.region"),
        ("city", "client.city"),
    )
    for source, path in raw_fields:
        if raw := anketa.get(source):
            set_by_path(facts, path, str(raw))

    return facts


def init_state(anketa: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "facts": seed_facts(anketa),
        # Внутренний слой качества фактов. Публичный lead-контракт по-прежнему
        # строится только из `facts`, поэтому добавление метаданных его не меняет.
        "fact_statuses": {},
        "fact_events": [],
        "pending_question": None,
        # Production по умолчанию использует legacy. Режим semantic включается
        # явно и при ошибке одного хода безопасно откатывается на legacy.
        "interpretation": {
            "mode": "legacy",
            "runtime_source": "legacy",
            "last_status": "not_run",
            "last_error": None,
            "shadow_calls": 0,
            "last_result": None,
            "aggregate": {
                "turns": 0,
                "successes": 0,
                "errors": 0,
                "exact_turns": 0,
                "plan_matches": 0,
                "agreement_paths": 0,
                "legacy_only_paths": 0,
                "shadow_only_paths": 0,
                "conflict_paths": 0,
                "needs_confirmation": 0,
                "candidate_issues": 0,
                "invalid_schema_issues": 0,
                "domain_policy_issues": 0,
            },
        },
        # Рекламные метки страницы, с которой открыт чат. Заполняются при
        # создании сессии и дальше не меняются: разговор их не производит.
        "attribution": None,
        "chat_history": [],
        "message_count": 0,
        "dialog_phase": "qualification",
        "internal_summary": "",
        "plan": None,
        "delivery": None,
        "crm_lead": {
            "status": "not_sent",
            "lead_id": None,
            "form_filled_out": None,
            "last_attempt_at": None,
            "submitted_at": None,
            "last_error": None,
            "resumed_after_submission": False,
        },
        "crm_workflow": {
            "status": "not_started",
            "category": None,
            "qualified": None,
            "need_site_consent": None,
            "site_consent_answer": None,
            "thanks_url": None,
            "last_error": None,
        },
    }


def should_close_dialog(state: dict[str, Any]) -> bool:
    return state.get("message_count", 0) >= SOFT_MESSAGE_LIMIT
