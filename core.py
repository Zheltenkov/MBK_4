"""Ядро хода диалога.

ВАЖНОЕ ОТЛИЧИЕ ОТ v3: извлечение фактов идёт ДО генерации ответа, а не после.

В v3 ответ стримился сразу, а разбор реплики уходил в фон — это давало отличную
задержку, потому что решение принимала сама модель и «отставание» фактов на один ход
никак не проявлялось. В v4 вопрос выбирает движок, и та же схема ломается на глазах у
клиента: он говорит «мне 42», а бот следующей репликой снова спрашивает возраст, потому
что на момент планирования этот факт ещё не был записан.

Поэтому ход теперь такой:
    реплика клиента → извлечение → нормализация → движок → инструкция → стрим ответа
Цена — один быстрый вызов извлекателя (temperature=0, короткий ответ) перед стримом.
Она заметно меньше, чем стоимость бота, переспрашивающего только что сказанное.
"""
from __future__ import annotations

import re
import logging
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Iterator
from urllib.parse import urlsplit

import analytics
import delivery
import debt_policy
import debt_workflow
import entry_routing
import geo
import llm_agent
import policy_voice
import policy_grounded_voice
import prompts
import quick_replies
from names_ru import FIRST_NAMES
from dialogue_frame import (
    DialogueFrame, ResponseGoal, contact_blocks_chat, last_collection_field, omit_deferred_discussion_followup,
    should_defer_discussion_followup, omit_explanatory_preference_followup, is_plain_debt_knowledge_request,
)
from context_policy import CONTEXT_OPENING_MESSAGE, context_delivery_held
from context_projection import project_context_state
from onloader import policy as onloader_policy
from onloader.contracts import OnloaderState
from config import AppConfig, load_config
from interpretation import fastpath
from interpretation import (
    FactCandidate,
    FactCandidateExtraction,
    begin_shadow_call,
    record_shadow_error,
    record_shadow_result,
    reduce_fact_candidates,
)
from pii_masking import (
    extract_local_pii_updates,
    is_strict_local_name_answer,
    mask_payload_for_llm,
)
from routing import Matrix, Plan, get_fact, get_matrix, plan as build_plan
from routing.derive import derive_facts
from routing.normalize import normalize_fact, parse_bool
from state import init_state
from utils import (
    _plain_received_field_answer, client_display_name, guard_bubbles, omit_empty_field_receipt_bubbles,
    omit_repeated_availability_tail, set_by_path,
)


# Фиксированная анкета — это 10-14 ходов клиента вместо прежних 3-4. При окне в
# десять записей к блоку активов модель уже не помнит ни сумму, ни тон начала
# разговора — ровно там, где переход должен обратиться по имени и сказать «мы
# почти закончили». Факты передаются отдельно, так что страдает не корректность,
# а живость речи.
HISTORY_WINDOW = 20


@dataclass(frozen=True)
class ExtractorRunResult:
    """Результат orchestration двух контуров интерпретации одного хода."""

    legacy: dict[str, Any]
    semantic: FactCandidateExtraction
    semantic_error: Exception | None
    legacy_called: bool


_PROPERTY_EVIDENCE_MARKERS: dict[str, dict[str, tuple[str, ...]]] = {
    "assets.realty.ownership_form": {
        "private": ("частн", "приватизир", "в собственност"),
        "municipal": ("муниципал", "неприватизир", "соцнайм", "социальн"),
        "other": ("другая форма", "иная форма"),
    },
    "assets.realty.owners": {
        "client_only": (
            "только я",
            "только на мне",
            "только на меня",
            "единолич",
            "один собственник",
            "собственник я",
            # Бой 24.10.2026, ответы о втором объекте: «я единственный
            # собственник», «да один владелец я».
            "единственный собственник",
            "единственная собственница",
            "один владелец",
            "владелец я",
            "оформлена на меня",
            "оформлен на меня",
        ),
        "client_and_spouse": (
            "супруг",
            "муж",
            "жена",
            "на двоих",
        ),
        "others": (
            "доля",
            "родител",
            "родствен",
            "ребен",
            "ребён",
            "другие собственники",
        ),
    },
}
_AFFIRMATIVE_REPLY_RE = re.compile(
    r"^\s*(?:да|ага|верно|правильно|точно|подтверждаю)\b",
    re.IGNORECASE,
)
_ALTERNATIVE_MARKERS = (
    "альтернатив",
    "другой вариант",
    "другие варианты",
    "другой способ",
    "другие способы",
)


# --------------------------------------------------------------------------- #
# Факты
# --------------------------------------------------------------------------- #
def apply_fact_updates(facts: dict[str, Any], fact_updates: list[dict[str, Any]]) -> dict[str, Any]:
    """Применяет извлечённые факты, прогоняя каждый через нормализатор.

    Отбрасываем: неизвестные пути (модель придумала поле), значения с низкой уверенностью
    и всё, что нормализатор не смог опознать. Матрица должна получать либо точное
    значение, либо ничего — «примерно 300–400 тысяч» не факт, а повод переспросить.
    """
    matrix = get_matrix()
    known_paths = set(matrix.fields) | {
        "client.region",
        "client.city",
        # Анкета дату рождения не спрашивает, но клиент называет её сам в
        # ответе про возраст. В CRM это обязательная графа, и точная дата
        # всегда лучше выведенной из числа лет.
        "client.birthdate",
        # Полное ФИО остаётся CRM-контрактом, а это отдельное подтверждённое
        # обращение. Оно извлекается локально и не должно отбрасываться как
        # неизвестное поле только потому, что маршруты по нему не ветвятся.
        "client.display_name",
        "assets.realty.region",
        "assets.realty.city",
        "assets.realty_2.region",
        "assets.realty_2.city",
    }
    updated = deepcopy(facts)

    for update in fact_updates:
        path = update.get("path")
        if not path or path not in known_paths:
            continue
        if update.get("confidence") == "low":
            continue
        value = normalize_fact(path, update.get("value"), matrix)
        if value is not None:
            set_by_path(updated, path, value)
    return updated


# --------------------------------------------------------------------------- #
# Ход
# --------------------------------------------------------------------------- #
def prepare_turn(
    state: dict[str, Any],
    user_message: str,
    config: AppConfig | None = None,
    usage_collector: Any = None,
    shadow_usage_collector: Any = None,
    *,
    submit_delivery: bool = True,
) -> dict[str, Any]:
    """Разбирает реплику клиента и решает, о чём говорить дальше.

    Возвращает НОВОЕ состояние с обновлёнными фактами, планом и (если маршрут найден)
    результатом обращения в микросервис. Ответ клиенту здесь не генерируется.
    """
    config = config or load_config()
    new_state = deepcopy(state)
    # A new turn must never inherit the previous reply's accepted-field metadata.
    new_state.pop("_policy_received_fields", None)
    # Инструкция хода читает только state, а решение «клиент прощается»
    # принимается по реплике. Кладём её сюда, а не тащим параметром через все
    # вызовы: в истории она всё равно окажется на этом же ходу.
    #
    # Подчёркивание — принятый в проекте признак временного ключа: такие ключи
    # живут один ход и в базу не уезжают (`session_store._persistable_state`).
    # Сырая реплика — это ПД в чистом виде, и хранить её незачем.
    new_state["_last_user_message"] = user_message
    matrix = get_matrix()
    # Pin the deterministic questionnaire before any model or legacy derivation runs.
    debt_workflow.select_for_turn(
        new_state, user_message, enabled=config.debt_policy_enabled,
        interpret_contact_refusal=not (config.policy_semantic_enabled and config.policy_voice_enabled),
    )
    debt = debt_policy.parse_policy(new_state)
    if debt is not None and debt.mode == "debt" and debt_policy.transfer_contact_declined(user_message):
        # This records only a negative conversation preference. Mixed fact
        # messages still pass through extraction and the durable delivery hold.
        new_state[debt_policy.POLICY_KEY] = debt.model_copy(update={"transfer_declined": True}).as_state()
    if onloader_policy.dialogue_active(new_state):
        return onloader_policy.prepare(
            new_state, interpret_choice=not (config.policy_semantic_enabled and config.policy_voice_enabled),
        )
    interpretation = new_state.get("interpretation")
    if isinstance(interpretation, dict):
        interpretation["mode"] = config.fact_interpretation_mode

    if user_message:
        # Ответ на переспрос про единицы измерения денежного поля («400
        # тысяч?», брифинг «Голое число в денежном вопросе», 20.09.2026)
        # разбирается первым и снимает пометку сразу, независимо от исхода:
        # второй переспрос подряд запрещён, иначе цикл. Путь остаётся в
        # `money_suppressed_paths` и для обычного разбора этим же ходом —
        # второе голое число подряд на то же поле просто проходит как есть.
        existing_units_pending = (new_state.get("script") or {}).get("units_pending")
        money_suppressed_paths: set[str] = set()
        money_blocked_paths: set[str] = set()
        resolved_units_update: dict[str, Any] | None = None
        if (
            isinstance(existing_units_pending, dict)
            and isinstance(existing_units_pending.get("path"), str)
            and isinstance(existing_units_pending.get("raw"), int)
        ):
            pending_path = existing_units_pending["path"]
            pending_raw = existing_units_pending["raw"]
            new_state.setdefault("script", {}).pop("units_pending", None)
            resolved_value = fastpath.parse_units_confirmation(pending_raw, user_message)
            if resolved_value is not None:
                money_suppressed_paths.add(pending_path)
                resolved_units_update = {
                    "path": pending_path,
                    "value": resolved_value,
                    "confidence": "high",
                }
            elif not existing_units_pending.get(
                "repeated"
            ) and fastpath.units_choice_unanswered(pending_raw, user_message):
                # На «1 тысяча или 1 миллион?» ответили «1000, да» — выбора нет.
                # Мутационный прогон 04.10.2026: такое число записалось рублями
                # вместо миллиона. Спрашиваем ещё один раз, другими словами, и
                # на этом ходу в сумму ничего не пишем; третьего раза не будет.
                money_blocked_paths.add(pending_path)
                new_state["script"]["units_pending"] = {
                    **existing_units_pending,
                    "repeated": True,
                }
            else:
                money_suppressed_paths.add(pending_path)

        # Телефон/дату рождения извлекаем локально до внешней модели. После этого
        # outbound payload можно безопасно обезличить, не теряя нужный факт.
        asked_paths = list((new_state.get("script") or {}).get("asked") or [])
        trusted_local_updates = extract_local_pii_updates(
            user_message,
            expected_paths=asked_paths,
            existing_facts=new_state.get("facts"),
        )
        legacy_heuristic_updates = _extract_explicit_local_updates(user_message)
        # Уход — не ответ на вопрос. Бой 03.10.2026: «До свидания» на вопросе о
        # городе легло в регион, и бот ответил «„До свидания“ не похож на
        # название города или области».
        expected_updates = (
            None
            if client_is_saying_goodbye(user_message)
            else fastpath.parse_expected_answer(
                user_message,
                asked_paths,
                matrix,
            )
        )
        local_updates = [
            *trusted_local_updates,
            *legacy_heuristic_updates,
            *(expected_updates or []),
            *([resolved_units_update] if resolved_units_update else []),
        ]
        local_updates, local_units_pending = _split_bare_money_updates(
            local_updates, user_message, matrix, money_suppressed_paths, money_blocked_paths
        )
        locally_extracted_paths = {
            str(update.get("path")) for update in local_updates if update.get("path")
        }

        # Shadow начинает с тех же исходных facts и доверенных local PII parsers,
        # но не получает legacy marker-эвристики. Иначе сравнение само себя подтвердит.
        base_facts = apply_fact_updates(
            new_state.get("facts", {}),
            trusted_local_updates,
        )
        if local_updates:
            new_state["facts"] = apply_fact_updates(
                new_state.get("facts", {}), local_updates
            )
        payload = build_runtime_payload(new_state, user_message)
        shadow_state = deepcopy(new_state)
        shadow_state["facts"] = base_facts
        shadow_payload = build_runtime_payload(shadow_state, user_message)
        strict_local_name = (
            asked_paths == ["client.name"]
            and is_strict_local_name_answer(user_message)
            and any(
                update.get("path") in {"client.name", "client.display_name"}
                for update in trusted_local_updates
            )
        )
        # Голое приветствие первой репликой: фактов в нём нет, и решение о
        # ветке откладывается на ход (вариант Б, 12.09.2026). Звать извлекатель
        # незачем — это пять секунд молчания перед приветствием в ответ.
        greeting_hold = config.entry_routing_enabled and entry_routing.holds_for_greeting(
            state, user_message, assistant_name=prompts.ASSISTANT_NAME
        )
        # Нажатая кнопка «Другой вопрос» — вопроса ещё нет, фактов тоже:
        # решение ждёт следующей реплики (спека чипсов 14.09.2026).
        other_question_hold = (
            config.entry_routing_enabled
            and config.quick_replies_enabled
            and entry_routing.holds_for_other_question(state, user_message)
        )
        # A whole catalogue value can restate the already-known business region
        # without adding a fact. A different class or any mixed input keeps E.
        known_region_reply = False
        if (debt is not None and debt.mode == "debt"
                and _plain_received_field_answer(user_message, "client.region_class")
                and (state.get("fact_statuses") or {}).get("client.region_class") != "needs_confirmation"):
            location = debt_workflow._region_answer(user_message, asked=True)
            known_region_reply = (location is not None
                                  and location[0] == get_fact(state.get("facts") or {}, "client.region_class"))
        known_contact_reference = debt_workflow.known_phone_reference(new_state, user_message)
        if (
            expected_updates is not None
            or strict_local_name
            or greeting_hold
            or other_question_hold
            or known_region_reply
            or (debt is not None and debt.mode == "debt"
                and (debt_policy.pure_debt_turn_act(user_message, active_debt=True) is not None
                     or is_plain_debt_knowledge_request(user_message)
                     or known_contact_reference))
            or resolved_units_update is not None
        ):
            # Прямой ответ на заданный вопрос уже разобран локально. Вызов
            # извлекателя здесь ничего не добавит, а стоит около пяти секунд
            # молчания перед репликой бота.
            run_shadow = False
            if known_contact_reference:
                # Only E is skipped. G/C/F still receive the original message;
                # formal consent, contact preferences and delivery remain unchanged.
                logging.getLogger(__name__).info("debt_extraction_skipped", extra={"reason": "known_phone_reference"})
            extraction = {"fact_updates": [], "internal_summary": ""}
            shadow_extraction = FactCandidateExtraction()
            shadow_error = None
        else:
            run_shadow = begin_shadow_call(
                new_state,
                mode=config.fact_interpretation_mode,
                max_calls=config.fact_shadow_max_calls_per_session,
            )
            extractor_result = _run_extractors(
                payload,
                shadow_payload,
                config,
                usage_collector=usage_collector,
                shadow_usage_collector=shadow_usage_collector,
                run_shadow=run_shadow,
                matrix=matrix,
            )
            extraction = extractor_result.legacy
            shadow_extraction = extractor_result.semantic
            shadow_error = extractor_result.semantic_error

        # Обезличенный extractor может увидеть маркер «[телефон]». Локально
        # извлечённые ПД имеют приоритет и не должны быть затёрты этим маркером.
        model_updates = [
            update
            for update in extraction.get("fact_updates", [])
            if update.get("path") not in locally_extracted_paths
        ]
        model_updates = _filter_contextual_preference_inferences(
            model_updates,
            user_message,
            new_state.get("chat_history", []),
        )
        model_updates = _redirect_updates_to_second_object(
            model_updates, asked_paths, user_message, matrix
        )
        model_updates = _filter_property_inferences(
            model_updates,
            user_message,
            new_state.get("chat_history", []),
            matrix=matrix,
        )
        model_updates, model_units_pending = _split_bare_money_updates(
            model_updates, user_message, matrix, money_suppressed_paths, money_blocked_paths
        )
        new_state["facts"] = apply_fact_updates(new_state.get("facts", {}), model_updates)
        if summary := extraction.get("internal_summary"):
            new_state["internal_summary"] = summary

        # Голое число денежного поля не ушло в факты — вместо него пометка,
        # которую прочитает следующий ход (`turn_instruction`) и разберёт
        # начало этой же ветки на следующем сообщении клиента.
        units_pending = local_units_pending or model_units_pending
        if units_pending:
            new_state.setdefault("script", {})["units_pending"] = units_pending

        legacy_facts = new_state.get("facts", {})
        semantic_mode = config.fact_interpretation_mode == "semantic"

        # В shadow reducer остаётся диагностическим. В semantic тот же проверенный
        # reduction становится runtime state; при ошибке сохраняется legacy_facts.
        if run_shadow:
            if shadow_error is not None:
                record_shadow_error(new_state, shadow_error)
                if semantic_mode:
                    runtime_interpretation = new_state.setdefault(
                        "interpretation",
                        {},
                    )
                    if isinstance(runtime_interpretation, dict):
                        runtime_interpretation["runtime_source"] = "legacy_fallback"
            else:
                safe_shadow_payload = mask_payload_for_llm(
                    shadow_payload,
                    config.enable_pii_masking,
                )
                record_shadow_result(
                    new_state,
                    base_facts=base_facts,
                    legacy_facts=(
                        legacy_facts
                        if extractor_result.legacy_called
                        else None
                    ),
                    candidates=shadow_extraction.candidates,
                    issues=shadow_extraction.issues,
                    boundaries=shadow_extraction.boundaries,
                    evidence_text=str(
                        safe_shadow_payload.get("latest_user_message", "")
                    ),
                    matrix=matrix,
                )
                if semantic_mode:
                    # Свод заменяет facts целиком из candidates напрямую, в
                    # обход local_updates/model_updates — голое число нужно
                    # отловить здесь же, до reduce_fact_candidates, иначе оно
                    # пройдёт мимо фильтра выше (живая проверка 21.09.2026).
                    semantic_candidates, semantic_units_pending = _split_bare_money_candidates(
                        _redirect_candidates_to_second_object(
                            shadow_extraction.candidates,
                            asked_paths,
                            user_message,
                            matrix,
                        ),
                        user_message,
                        matrix,
                        money_suppressed_paths,
                        money_blocked_paths,
                    )
                    reduction = reduce_fact_candidates(
                        base_facts,
                        new_state.get("fact_statuses", {}),
                        semantic_candidates,
                        evidence_text=str(
                            safe_shadow_payload.get(
                                "latest_user_message",
                                "",
                            )
                        ),
                        matrix=matrix,
                        already_clarified=_clarified_paths(new_state),
                    )
                    new_state["facts"] = reduction.facts
                    if semantic_units_pending:
                        new_state.setdefault("script", {})[
                            "units_pending"
                        ] = semantic_units_pending
                    new_state["fact_statuses"] = {
                        path: status.value
                        for path, status in reduction.statuses.items()
                    }
                    previous_events = new_state.get("fact_events")
                    event_log = (
                        list(previous_events)
                        if isinstance(previous_events, list)
                        else []
                    )
                    event_log.extend(
                        event.model_dump(mode="json")
                        for event in reduction.events
                    )
                    # Session provenance нужен для диагностики, но не должен
                    # бесконечно раздувать публичное состояние.
                    new_state["fact_events"] = event_log[-200:]
                    runtime_interpretation = new_state.setdefault(
                        "interpretation",
                        {},
                    )
                    if isinstance(runtime_interpretation, dict):
                        runtime_interpretation["runtime_source"] = "semantic"
        elif semantic_mode:
            # Однозначный ответ уже проверен локальным parser'ом: это штатный
            # быстрый путь, а не аварийный fallback модели. Состояние может
            # прийти из старой сессии без секции interpretation.
            runtime_interpretation = new_state.setdefault("interpretation", {})
            if isinstance(runtime_interpretation, dict):
                runtime_interpretation["runtime_source"] = "local_fastpath"

        # Развилка на входе (спека 2026-09-09, компоненты 1 и 2). Намерение
        # приходит тем же вызовом извлекателя — ни лишнего вызова, ни лишней
        # секунды. Словарь — страховка, когда модель не звали, она упала или
        # поле не вернула. Shadow не в счёт: там новый извлекатель
        # диагностический и на разговор не влияет.
        if config.entry_routing_enabled and greeting_hold:
            new_state[entry_routing.GREETING_KEY] = True
        elif other_question_hold:
            new_state[entry_routing.OTHER_QUESTION_KEY] = True
        elif config.entry_routing_enabled:
            model_intent = (
                entry_routing.reconcile_intent(
                    shadow_extraction.entry_intent,
                    money_signal=shadow_extraction.mentions_money(),
                )
                if semantic_mode and run_shadow and shadow_error is None
                else None
            )
            entry_routing.route_turn(
                new_state,
                user_message=user_message,
                model_intent=model_intent,
                money_signal=(
                    semantic_mode
                    and run_shadow
                    and shadow_error is None
                    and shadow_extraction.mentions_money()
                ),
                # Решение открыто до первой содержательной реплики, не дольше
                # двух реплик клиента.
                first_message=entry_routing.entry_decision_open(
                    state,
                    assistant_name=prompts.ASSISTANT_NAME,
                    chips_enabled=config.quick_replies_enabled,
                ),
            )

        # Нажатие кнопки приветствия — для аналитики. Событие пишет
        # `commit_turn`, только за состоявшийся ход (спека чипсов 14.09.2026).
        if config.entry_routing_enabled and config.quick_replies_enabled:
            choice = quick_replies.first_message_choice(state, user_message)
            if choice:
                new_state[quick_replies.CHOICE_KEY] = choice

    if not config.entry_routing_enabled:
        # Флаг выключен — разговор ведётся ровно как без развилки, в том числе
        # тот, что попал в ветку, пока флаг был включён: откат полный.
        new_state.pop("entry", None)

    # Сырой ответ сохраняется ДО обогащения: тогда DaData получит хотя бы его,
    # а если и она не справится — текст всё равно уедет в CRM.
    _keep_raw_location(new_state, user_message)
    new_state["facts"] = geo.enrich_locations(
        new_state.get("facts", {}),
        user_message,
        matrix,
        asked_paths=list((new_state.get("script") or {}).get("asked") or []),
    )
    # Согласуем производные факты до планирования: кредитную историю и пропущенный
    # признак недвижимости при подтверждённом частном владении клиентом.
    if debt_policy.debt_delivery_held(new_state):
        received = debt_workflow.reconcile_extraction(state, new_state, user_message)
        new_state["_policy_received_fields"] = sorted(received)
    else:
        new_state["facts"] = derive_facts(new_state.get("facts", {}), fact_statuses=new_state.get("fact_statuses"))
    _track_decline(state, new_state, user_message)
    _return_units_question(state, new_state, matrix)
    _refund_attempts_spent_on_a_correction(state.get("facts", {}), new_state, matrix)
    current_plan = _build_questionnaire_plan(new_state, matrix, config)
    # Попытки и память о заданном вопросе здесь не трогаем: план — это
    # намерение, а не прозвучавший вопрос. Их фиксирует `commit_turn`, когда
    # уже известно, что клиент вопрос увидел (см. `_question_is_voiced`).

    # Маршрут найден и контакты собраны — единственный момент, когда мы идём наружу.
    if (
        submit_delivery
        and current_plan.action == "route_found"
        and not new_state.get("delivery")
        and not entry_routing.lead_blocked(new_state)
    ):
        assert current_plan.matched is not None
        new_state["dialog_phase"] = "delivery_pending"
        session_id = str(new_state.get("session_id") or "")
        result = delivery.submit(
            new_state["facts"],
            current_plan.matched,
            idempotency_key=session_id or None,
        )
        new_state["delivery"] = result.as_dict()

    new_state["dialog_phase"] = _dialog_phase(current_plan, new_state.get("delivery"))
    if (policy := debt_policy.parse_policy(new_state)) is not None:
        new_state["dialog_phase"] = debt_policy.policy_phase(policy, current_plan, new_state.get("facts", {}))
    new_state["plan"] = _plan_snapshot(current_plan)
    new_state["_plan_object"] = current_plan
    return new_state


def _refund_attempts_spent_on_a_correction(
    old_facts: dict[str, Any], new_state: dict[str, Any], matrix: Matrix
) -> None:
    """Возвращает попытку, если клиент ответом исправил другое поле.

    Прод 30.09.2026, лид 3508342: на вопрос о просрочке клиентка ответила
    «1300000» — поправила сумму долга, названную ходом раньше. Это не отказ
    отвечать, но попытка была уже третьей, вопрос о просрочке выпал из анкеты,
    и CRM осталась без кредитной истории.

    Условия узкие намеренно: ни одно спрошенное поле не изменилось, а какое-то
    другое поле анкеты — изменилось. Пустой ответ («Оператор», «не знаю»)
    попыткой остаётся, иначе вопрос не закончился бы никогда.
    """
    script = new_state.get("script") or {}
    script_key = "debt_script" if debt_policy.debt_delivery_held(new_state) and "debt_script" in new_state else "script"
    attempts = (new_state.get(script_key) or {}).get("attempts")
    asked = [path for path in script.get("asked") or [] if path in matrix.fields]
    if not asked or not isinstance(attempts, dict):
        return
    new_facts = new_state.get("facts", {})

    def changed(path: str) -> bool:
        return get_fact(new_facts, path) != get_fact(old_facts, path)

    if any(changed(path) for path in asked):
        return
    if not any(changed(path) for path in matrix.fields if path not in asked):
        return
    for path in asked:
        spent = int(attempts.get(path) or 0)
        if spent > 0:
            attempts[path] = spent - 1


def _build_questionnaire_plan(state: dict[str, Any], matrix: Matrix, config: AppConfig) -> Plan:
    """One policy gate is shared by initial planning and every later replan."""
    policy = debt_policy.parse_policy(state)
    if policy is not None:
        return debt_policy.plan_debt(matrix, state.get("facts", {}), policy,
                                     fact_statuses=state.get("fact_statuses"), attempts=_field_attempts(state),
                                     contact_known=onloader_policy.contact_known(state))
    return build_plan(matrix, state.get("facts", {}), fact_statuses=state.get("fact_statuses"),
                      attempts=_field_attempts(state), skip_settled=not config.second_realty_enabled)


def replan(state: dict[str, Any], config: AppConfig | None = None) -> None:
    """Пересчитывает вопрос хода по фактам, которые появились после разбора.

    Нужен ровно одному сценарию: ответ `realty-qualification` приходит уже
    после того, как план хода посчитан, и открытая им ветка второго объекта
    иначе прозвучала бы ходом позже — посреди просьбы оставить телефон. Живой
    прогон 13.08.2026: бот спросил номер, следом «есть ли ещё недвижимость»,
    потом снова номер. Связность 55 из 100.

    Меняет состояние на месте: вызывающая сторона уже держит его под замком.
    """
    if onloader_policy.dialogue_active(state):
        state.update(onloader_policy.prepare(state))
        return
    config = config or load_config()
    current_plan = _build_questionnaire_plan(state, get_matrix(), config)
    state["dialog_phase"] = _dialog_phase(current_plan, state.get("delivery"))
    if (policy := debt_policy.parse_policy(state)) is not None:
        state["dialog_phase"] = debt_policy.policy_phase(policy, current_plan, state.get("facts", {}))
    state["plan"] = _plan_snapshot(current_plan)
    state["_plan_object"] = current_plan
    # Память о вопросе и счётчик попыток идут за планом, а не за первым его
    # вариантом. Иначе бот спрашивает про второй объект, а короткое «нет»
    # следующего хода быстрый разбор кладёт в поле прошлого плана.
    # Воспроизведено тестом 19.08.2026: `assets.has_vehicle` становится False,
    # хотя спрашивали про недвижимость, и переписать это некому — фоновой
    # правки за быстрым разбором нет. Отдельно записывать здесь не нужно: оба
    # фиксирует `commit_turn` по `_plan_object`, то есть уже по этому плану.



# Ответ на вопрос о регионе, который не разобрал никто. Сохраняем сырым: CRM
# читает названия в свободной форме, и «Мухосранск» ей полезнее, чем пустое
# поле, а пустой регион означает category=null в любой вертикали.
_RAW_LOCATION_FIELDS = {
    "client.region_class": "client",
    "assets.realty.region_class": "assets.realty",
    # Второго объекта здесь намеренно нет. Когда объектов два, догадка начинает
    # путать их между собой: 12.08.2026 клиент раздражённо повторил адрес дома
    # под Тверью, а курсор анкеты стоял уже на московской квартире — и тверской
    # адрес уехал в CRM её регионом. Регион объекта решает его категорию, так
    # что пустое поле честнее чужого адреса. У первого объекта и у самого
    # клиента этой путаницы нет: перепутать не с чем.
}
# Реплика длиннее — это рассуждение, а не название населённого пункта.
_RAW_LOCATION_MAX_CHARS = 60

# По чему узнаём собственный вопрос о месте. План хода называет поле, но реплику
# формулирует модель и вполне может спросить другое — 12.08.2026 в поле региона
# так оказался ответ про зарплату. Верить надо тому, что бот действительно
# сказал, а не тому, что собирался.
_PLACE_QUESTION_MARKERS = (
    "город",
    "регион",
    "населённ",
    "населен",
    "област",
    "деревн",
    "посёл",
    "посел",
    "находится",
    "живёте",
    "живете",
    "прописк",
)


# Слова, по которым видно, что в реплике вообще есть место.
_PLACE_WORD_MARKERS = (
    "город",
    "област",
    "край",
    "район",
    "деревн",
    "село",
    "посёл",
    "посел",
    "республик",
    "округ",
    "станиц",
    "хутор",
)
# И слова, по которым видно, что места в ней нет.
_NON_ANSWER_MARKERS = (
    "не помню",
    "не знаю",
    "не могу",
    "не скажу",
    "не хочу",
    "потом",
    "позже",
    "затрудняюсь",
    # Упоминание города ещё не означает, что клиент назвал его своим. Такие
    # реплики должен разобрать разговорный контур и при необходимости ответить
    # по-человечески, но сырой fallback не имеет права записывать их фактом.
    "это не ",
    "для чего",
    "пожалуй",
    "может ",
    " или ",
)


def _looks_like_a_place(reply: str) -> bool:
    """Есть ли в реплике название места.

    Пустой регион означает `category: null`, но строка без названия не лучше:
    CRM по ней тоже ничего не найдёт, а менеджер увидит в лиде бессмыслицу.
    12.08.2026 живым прогоном так уехало «Пока не могу, не дома».

    Признак названия — либо родовое слово (область, деревня), либо имя
    собственное. Заглавная буква в начале фразы не считается: с неё начинается
    любое предложение.
    """
    lowered = reply.casefold()
    if any(marker in lowered for marker in _NON_ANSWER_MARKERS):
        return False
    if any(marker in lowered for marker in _PLACE_WORD_MARKERS):
        return True
    words = reply.split()
    if len(words) == 1:
        return words[0][:1].isupper()
    return any(word[:1].isupper() for word in words[1:])


def _last_question_was_about_a_place(state: dict[str, Any]) -> bool:
    for message in reversed(state.get("chat_history") or []):
        if message.get("role") != "assistant":
            continue
        text = str(message.get("content", "")).casefold()
        return any(marker in text for marker in _PLACE_QUESTION_MARKERS)
    # Истории нет — первый ход. Полагаемся на план, как раньше.
    return True


def _keep_raw_location(state: dict[str, Any], user_message: str) -> None:
    """Последний рубеж: сохранить ответ клиента про регион как есть.

    Штатно город приходит от извлекателя и нормализуется DaData. Но если реплику
    не разобрал ни один из них, поле уходит в CRM пустым — а адрес прописки `UF_CRM_1452153725`
    участвует в матчинге во всех вертикалях (backend-разработчик, 12.08.2026),
    и без него клиент гарантированно получает category=null.

    Пишем только когда спрашивали именно про регион и ничего не нашлось.
    Вопрос в ответ и длинное рассуждение за название не принимаем: лучше пусто,
    чем «а зачем вам это» в поле региона.
    """
    plan = state.get("plan")
    asked = set(plan.get("fields") or []) if isinstance(plan, dict) else set()
    reply = " ".join((user_message or "").split())
    if not reply or "?" in reply or len(reply) > _RAW_LOCATION_MAX_CHARS:
        return
    if not _last_question_was_about_a_place(state) or not _looks_like_a_place(reply):
        return

    facts = state.setdefault("facts", {})
    # Что мы сюда уже писали сами. Своя догадка настоящему ответу не помеха:
    # в одном ходу бот успевает спросить и регион, и собственника, и первым
    # приходит ответ не на тот вопрос. 12.08.2026 так в лид уехало
    # `CHAT_REALTY_REGION: Тоже моя, в собственности` — следующая реплика
    # «Москва» его уже не заменила.
    guessed = set(state.get("raw_location_paths") or [])
    for field, root in _RAW_LOCATION_FIELDS.items():
        if field not in asked:
            continue
        path = f"{root}.region"

        # DaData могла не найти прежнее название, после чего клиент назвал
        # другое. Старые city/query блокировали новый ответ как будто адрес
        # уже подтверждён. Не трогаем resolved/ambiguous: там сохранён реальный
        # результат или список, на который клиент может отвечать.
        root_facts: Any = facts
        for part in root.split("."):
            if not isinstance(root_facts, dict):
                root_facts = None
                break
            root_facts = root_facts.get(part)
        location = (
            root_facts.get("location")
            if isinstance(root_facts, dict)
            else None
        )
        if (
            isinstance(root_facts, dict)
            and isinstance(location, dict)
            and location.get("status") in {"not_found", "unavailable"}
        ):
            for stale_key in ("city", "region", "region_class", "location"):
                root_facts.pop(stale_key, None)
            guessed.discard(path)

        def known(key: str) -> bool:
            value = get_fact(facts, f"{root}.{key}")
            return isinstance(value, str) and bool(value.strip())

        # Город от DaData или извлекателя точнее любой нашей догадки — и той,
        # что уже лежит в region, тоже. Заменяем только своё и только своим.
        if known("city") or (known("region") and path not in guessed):
            continue
        set_by_path(facts, path, reply)
        guessed.add(path)
    state["raw_location_paths"] = sorted(guessed)


def _run_extractors(
    legacy_payload: dict[str, Any],
    shadow_payload: dict[str, Any],
    config: AppConfig,
    *,
    usage_collector: Any,
    shadow_usage_collector: Any,
    run_shadow: bool,
    matrix: Matrix,
) -> ExtractorRunResult:
    """Выбирает extractor по режиму и сохраняет legacy как fallback.

    ``shadow`` параллельно запускает оба контура ради сравнения. ``semantic``
    сначала вызывает только новый extractor и обращается к legacy лишь после
    semantic-ошибки. ``legacy`` не вызывает новый контур.
    """

    def legacy_call() -> dict[str, Any]:
        try:
            return llm_agent.extract_facts(
                legacy_payload,
                "",
                config,
                usage_collector=usage_collector,
            )
        except Exception:  # noqa: BLE001 — legacy-сбой не роняет диалог
            return {"fact_updates": [], "internal_summary": ""}

    if not run_shadow:
        return ExtractorRunResult(
            legacy=legacy_call(),
            semantic=FactCandidateExtraction(),
            semantic_error=None,
            legacy_called=True,
        )

    if config.fact_interpretation_mode == "semantic":
        try:
            candidate_extraction = llm_agent.extract_fact_candidates(
                shadow_payload,
                config,
                shadow_usage_collector,
                matrix=matrix,
            )
        except Exception as exc:  # noqa: BLE001 — аварийно сохраняем старый путь
            return ExtractorRunResult(
                legacy=legacy_call(),
                semantic=FactCandidateExtraction(),
                semantic_error=exc,
                legacy_called=True,
            )
        return ExtractorRunResult(
            legacy={"fact_updates": [], "internal_summary": ""},
            semantic=candidate_extraction,
            semantic_error=None,
            legacy_called=False,
        )

    # Отдельный маленький pool нужен только в диагностическом shadow-режиме.
    # Время хода близко к max(legacy, shadow), а не к сумме сетевых задержек.
    with ThreadPoolExecutor(
        max_workers=2,
        thread_name_prefix="mbk-fact-shadow",
    ) as executor:
        legacy_future = executor.submit(legacy_call)
        shadow_future = executor.submit(
            llm_agent.extract_fact_candidates,
            shadow_payload,
            config,
            shadow_usage_collector,
            matrix=matrix,
        )
        extraction = legacy_future.result()
        try:
            candidate_extraction = shadow_future.result()
        except Exception as exc:  # noqa: BLE001 — shadow не влияет на runtime
            return ExtractorRunResult(
                legacy=extraction,
                semantic=FactCandidateExtraction(),
                semantic_error=exc,
                legacy_called=True,
            )
        return ExtractorRunResult(
            legacy=extraction,
            semantic=candidate_extraction,
            semantic_error=None,
            legacy_called=True,
        )


def build_runtime_payload(state: dict[str, Any], latest_user_message: str) -> dict[str, Any]:
    """Payload одного хода. История — БЕЗ текущей реплики клиента, она передаётся отдельно."""
    context_readonly = context_delivery_held(state) or debt_policy.debt_delivery_held(state)
    model_state = project_context_state(state) if context_readonly else state
    result = {
        # Original facts remain local just long enough to redact known PII in dialogue text.
        "facts": state.get("facts", {}) if context_readonly else model_state.get("facts", {}),
        "short_history": model_state.get("chat_history", [])[-HISTORY_WINDOW:],
        "latest_user_message": latest_user_message,
        "internal_summary": model_state.get("internal_summary", ""),
        "turn_instruction": turn_instruction(model_state),
        # Маскировщику нужен контекст вопроса: голое «Иван Иванов» является
        # ФИО только на шаге имени, а «Нижний Новгород» на шаге региона — нет.
        "expected_paths": list((state.get("script") or {}).get("asked") or []),
    }
    if context_readonly:
        result["current_context_readonly"] = True
        result = mask_payload_for_llm(result, True)
    return result


def _filter_property_inferences(
    updates: list[dict[str, Any]],
    user_message: str,
    history: list[dict[str, Any]],
    *,
    matrix: Matrix,
) -> list[dict[str, Any]]:
    """Не принимает форму/владельцев без явного текстового подтверждения.

    LLM иногда превращает бытовое «квартира моя» сразу в частную единоличную
    собственность. Для квалификации 1-1 это критичная подмена двух разных фактов.
    Короткое «да» считается доказательством только вместе с предыдущим вопросом.
    """
    evidence = user_message.lower()
    if _AFFIRMATIVE_REPLY_RE.search(user_message):
        previous_assistant = next(
            (
                str(item.get("content", ""))
                for item in reversed(history)
                if item.get("role") == "assistant"
            ),
            "",
        )
        evidence = f"{evidence}\n{previous_assistant.lower()}"

    filtered: list[dict[str, Any]] = []
    for update in updates:
        path = str(update.get("path", ""))
        # Второй объект проверяется теми же словами, что и первый: «квартира
        # моя» не значит частную единоличную собственность ни у одного из них.
        marker_map = _PROPERTY_EVIDENCE_MARKERS.get(
            path.replace(_SECOND_OBJECT_PREFIX, _FIRST_OBJECT_PREFIX, 1)
        )
        if marker_map is None:
            filtered.append(update)
            continue

        normalized = normalize_fact(path, update.get("value"), matrix)
        markers = marker_map.get(str(normalized), ())
        if markers and any(marker in evidence for marker in markers):
            filtered.append(update)
    return filtered


_FIRST_OBJECT_PREFIX = "assets.realty."
_SECOND_OBJECT_PREFIX = "assets.realty_2."
# Сырые место и город второго объекта живут вне матрицы (см. interpretation/prompt.py).
_SECOND_OBJECT_RAW_FIELDS = {"assets.realty_2.region", "assets.realty_2.city"}
# «Первая тоже частная» на вопросе о втором объекте — клиент говорит о первом.
_FIRST_OBJECT_NAMED_RE = re.compile(r"\bперв(?:ая|ый|ое|ой|ую|ом|ого)\b", re.IGNORECASE)


def _second_object_redirects(
    paths: list[str],
    asked_paths: list[str],
    user_message: str,
    matrix: Matrix,
) -> dict[str, str]:
    """Какие поля первого объекта на самом деле ответ о втором.

    Бой 22–24.10.2026 (29aa94b4a6aa, adf61fafade0): бот спрашивал о форме
    собственности и собственниках второго объекта, клиент отвечал «Да частная
    собстенность», «Мой», «Только я», а извлекатель клал ответ в поля первого
    объекта — 6 ответов из 8 на пробе по боевой базе. Второй объект оставался
    пустым, план повторял вопрос, модель считала его отвеченным, уходила с
    плана и объявляла заявку переданной, а лида не было.

    Переносим, только когда спрошен один второй объект и клиент не назвал
    первый словами. Поле, которое модель уже положила во второй объект сама,
    побеждает: значит, клиент развёл объекты, и первый тоже трогать нельзя.
    """
    if not asked_paths or not all(path.startswith(_SECOND_OBJECT_PREFIX) for path in asked_paths):
        return {}
    if _FIRST_OBJECT_NAMED_RE.search(user_message or ""):
        return {}
    present = set(paths)
    redirects: dict[str, str] = {}
    for path in paths:
        if not path.startswith(_FIRST_OBJECT_PREFIX):
            continue
        target = _SECOND_OBJECT_PREFIX + path[len(_FIRST_OBJECT_PREFIX):]
        if target in present or (target not in matrix.fields and target not in _SECOND_OBJECT_RAW_FIELDS):
            continue
        redirects[path] = target
    return redirects


def _redirect_updates_to_second_object(
    updates: list[dict[str, Any]],
    asked_paths: list[str],
    user_message: str,
    matrix: Matrix,
) -> list[dict[str, Any]]:
    redirects = _second_object_redirects(
        [str(update.get("path", "")) for update in updates], asked_paths, user_message, matrix
    )
    if not redirects:
        return updates
    return [
        {**update, "path": redirects[update["path"]]} if update.get("path") in redirects else update
        for update in updates
    ]


def _redirect_candidates_to_second_object(
    candidates: list[FactCandidate],
    asked_paths: list[str],
    user_message: str,
    matrix: Matrix,
) -> list[FactCandidate]:
    redirects = _second_object_redirects(
        [candidate.path for candidate in candidates], asked_paths, user_message, matrix
    )
    if not redirects:
        return candidates
    return [
        candidate.model_copy(update={"path": redirects[candidate.path]})
        if candidate.path in redirects
        else candidate
        for candidate in candidates
    ]


def _filter_contextual_preference_inferences(
    updates: list[dict[str, Any]],
    user_message: str,
    history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Не превращает любой отказ в согласие посмотреть другие продукты.

    ``explore_alternatives`` имеет смысл только как ответ на явный вопрос про
    другие варианты или как самостоятельная фраза клиента об альтернативах.
    Фраза «новый кредит не хочу» задаёт goal, но не открывает кредитные ветки.
    """
    previous_assistant = next(
        (
            str(item.get("content", ""))
            for item in reversed(history)
            if item.get("role") == "assistant"
        ),
        "",
    )
    context = f"{user_message}\n{previous_assistant}".casefold()
    alternatives_are_discussed = any(
        marker in context for marker in _ALTERNATIVE_MARKERS
    )
    return [
        update
        for update in updates
        if update.get("path") != "preferences.explore_alternatives"
        or alternatives_are_discussed
    ]


def _return_units_question(
    old_state: dict[str, Any],
    new_state: dict[str, Any],
    matrix: Matrix,
) -> None:
    """Возвращает переспрос единиц, если клиент ответил про другое.

    Бой 05.10.2026, сессия bf1df88ab543: «150» → «150 тысяч?» → «46лет». Бот
    ответил «150 тысяч — поняла», но подтверждения не было, сумма не
    записалась, и через ход прозвучало «Какая сумма вам нужна?». Число уже
    названо — честнее принять ответ про возраст и вернуться к единицам, чем
    спрашивать сумму с нуля. Один раз: дальше её спросит анкета.

    Пустой ответ («не знаю») переспрос не возвращает: человек ничего не
    сообщил, и вопрос о единицах он уже слышал.
    """
    pending = (old_state.get("script") or {}).get("units_pending")
    if not isinstance(pending, dict) or pending.get("repeated"):
        return
    path, raw = pending.get("path"), pending.get("raw")
    if not isinstance(path, str) or not isinstance(raw, int):
        return
    script = new_state.setdefault("script", {})
    if isinstance(script.get("units_pending"), dict):
        return
    new_facts = new_state.get("facts") or {}
    if get_fact(new_facts, path) not in (None, ""):
        return
    old_facts = old_state.get("facts") or {}
    answered_other = any(
        other != path and get_fact(new_facts, other) != get_fact(old_facts, other)
        for other in (*matrix.fields, *_RAW_PLACE_PATHS)
    )
    if not answered_other:
        return
    script["units_pending"] = {
        "path": path,
        "raw": raw,
        "repeated": True,
        "answered_other": True,
    }


# Город и регион пишутся сырым текстом раньше, чем по ним посчитан класс.
_RAW_PLACE_PATHS = (
    "client.region",
    "client.city",
    "assets.realty.region",
    "assets.realty.city",
    "assets.realty_2.region",
    "assets.realty_2.city",
)


def _split_bare_money_updates(
    updates: list[dict[str, Any]],
    user_message: str,
    matrix: Matrix,
    suppressed_paths: set[str],
    blocked_paths: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Отделяет от обновлений голое число денежного поля без единицы измерения.

    Общая проверка для legacy-путей извлечения — быстрого разбора и ответа
    модели: `prepare_turn` вызывает её и для `local_updates`, и для
    `model_updates`, так что логика не дублируется. Semantic-контур сюда не
    попадает вовсе — у него свой список кандидатов другого типа и свой свод,
    см. `_split_bare_money_candidates` рядом. Подозрительное значение не идёт
    в `apply_fact_updates` — вызывающий сохранит путь и число в
    `script.units_pending` и переспросит об этом же на этом же ходу.

    `suppressed_paths` — поле, для которого пометка уже снята этим же ходом
    (только что обработанный ответ на переспрос, каким бы он ни был): второй
    переспрос подряд запрещён, поэтому здесь голое число проходит как есть,
    даже если снова без единицы.

    `blocked_paths` — поле, по которому переспрос «тысяч или миллионов?»
    остался без ответа и звучит второй раз: на этом ходу в него не пишется
    ничего, пометку держит вызывающий.
    """
    kept: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    for update in updates:
        path = update.get("path")
        if blocked_paths and path in blocked_paths:
            continue
        if pending is None and path not in suppressed_paths:
            raw = fastpath.bare_money_update(update, user_message, matrix)
            if raw is not None:
                pending = {"path": path, "raw": raw}
                continue
        kept.append(update)
    return kept, pending


def _split_bare_money_candidates(
    candidates: list[FactCandidate],
    user_message: str,
    matrix: Matrix,
    suppressed_paths: set[str],
    blocked_paths: set[str] | None = None,
) -> tuple[list[FactCandidate], dict[str, Any] | None]:
    """То же самое, что `_split_bare_money_updates`, но для semantic-контура.

    Живая проверка 21.09.2026 (semantic-режим, реальный извлекатель): на
    вопрос про долг и просрочку клиент ответил «400 общая сумма», и 400 всё
    равно ушло в факты. Причина — `new_state["facts"] = reduction.facts`
    (свод `interpretation.reduce_fact_candidates` из `shadow_extraction.candidates`)
    полностью заменяет факты в обход `local_updates`/`model_updates`, а значит
    и в обход `_split_bare_money_updates`. Кандидат здесь — не dict, а
    `FactCandidate`, поэтому список обходится отдельной функцией, но сама
    проверка подозрительного значения та же самая, `fastpath.bare_money_update`
    — логика не дублируется, дублируется только перебор другого типа списка.
    """
    kept: list[FactCandidate] = []
    pending: dict[str, Any] | None = None
    for candidate in candidates:
        if blocked_paths and candidate.path in blocked_paths:
            continue
        if pending is None and candidate.path not in suppressed_paths:
            raw = fastpath.bare_money_update(
                {"path": candidate.path, "value": candidate.value},
                user_message,
                matrix,
            )
            if raw is not None:
                pending = {"path": candidate.path, "raw": raw}
                continue
        kept.append(candidate)
    return kept, pending


def _extract_explicit_local_updates(message: str) -> list[dict[str, Any]]:
    """Детерминированно извлекает очевидный тип объекта без LLM-догадок."""
    lowered = " ".join(message.casefold().split())
    kind: str | None = None
    if "апартамент" in lowered:
        kind = "apartments"
    elif "коммерческ" in lowered:
        kind = "commercial"
    elif any(
        marker in lowered
        for marker in (
            "есть квартира",
            "квартира есть",
            "это квартира",
            "квартира в ",
            "обычная квартира",
            "у меня квартира",
        )
    ):
        kind = "flat"
    elif any(
        marker in lowered
        for marker in (
            "есть дом",
            "дом есть",
            "это дом",
            "дом в ",
            "коттедж",
            "таунхаус",
        )
    ):
        kind = "house"

    if kind is None:
        return []
    return [
        {
            "path": "assets.realty.kind",
            "value": kind,
            "confidence": "high",
        }
    ]


def turn_instruction(state: dict[str, Any]) -> str:
    """Инструкция разговорному слою на этот ход.

    Если микросервис отказал или не ответил, инструкция та же, что при отсутствии
    маршрута: клиенту нужно честно сказать, что решения нет. Движок мог считать клиента
    подходящим — последнее слово всё равно за сервисом.
    """
    if onloader_policy.dialogue_active(state):
        return onloader_policy.reply(state)
    current_plan: Plan | None = state.get("_plan_object")
    if (policy := debt_policy.parse_policy(state)) is not None and current_plan is not None:
        return debt_policy.render_debt_reply(policy, current_plan, state.get("facts", {}))
    if current_plan is None:
        return prompts.OPENING_INSTRUCTION
    if context_delivery_held(state) and current_plan.action in {"route_found", "no_fit"}:
        return (
            "Уточнение сведений завершено. Коротко объясни, что отправка по этому "
            "обращению ещё не подтверждена. Не говори, что заявка отправлена или "
            "партнёр её принял. Не запрашивай заново уже известный телефон."
        )

    # Клиент пока только поздоровался — решение о ветке отложено на ход.
    if state.get(entry_routing.GREETING_KEY):
        return prompts.GREETING_INSTRUCTION

    if state.get(entry_routing.OTHER_QUESTION_KEY):
        return prompts.OTHER_QUESTION_INSTRUCTION

    # Ветки развилки говорят своё и анкету не ведут. Проверка выше всех
    # анкетных веток: подтверждение города, отказ от телефона, «нет решения»
    # — всё это про анкету, которой в ветке нет. Прощание остаётся прощанием.
    scenario = entry_routing.current_scenario(state)
    if scenario != entry_routing.QUESTIONNAIRE:
        if client_is_saying_goodbye(state.get("_last_user_message") or ""):
            return _leaving_instruction(state.get("_last_user_message") or "")
        if scenario == entry_routing.EXISTING:
            return prompts.EXISTING_CLIENT_INSTRUCTION
        return prompts.OFF_TOPIC_INSTRUCTION

    # Клиент сказал, что подбор ему не нужен: анкету не ведём, пока он сам не
    # вернётся к делу (`_track_decline`). Прощание остаётся прощанием.
    if _client_declined(state) and current_plan.action in {"ask", "contacts"}:
        if client_is_saying_goodbye(state.get("_last_user_message") or ""):
            return _leaving_instruction(state.get("_last_user_message") or "")
        return prompts.declined_instruction(current_plan)

    if units_instruction := _units_confirmation_instruction(state):
        return units_instruction

    if location_instruction := _location_confirmation_instruction(
        state,
        current_plan,
    ):
        return location_instruction

    phase = state.get("dialog_phase")
    crm_workflow = state.get("crm_workflow") or {}
    crm_status = crm_workflow.get("status")

    if crm_status == "awaiting_site_consent":
        # Согласие фиксирует страница по кнопке. Модель об этом только
        # рассказывает, ссылку добавляет backend.
        from crm_dialog_workflow import ask_site_consent_in_chat

        if not ask_site_consent_in_chat():
            if client_is_on_page(state, crm_workflow.get("offer_url")):
                return prompts.SITE_CONSENT_SAME_PAGE_INSTRUCTION
            return prompts.SITE_CONSENT_LINK_INSTRUCTION
        return prompts.SITE_CONSENT_INSTRUCTION
    if crm_status == "submitted":
        return prompts.CRM_SUBMITTED_INSTRUCTION
    if crm_status in {
        "qualification_retry_pending",
        "submission_retry_pending",
    }:
        return prompts.CRM_RETRY_PENDING_INSTRUCTION
    if crm_status in {
        "qualification_error",
        "submission_error",
        "outcome_unknown",
    }:
        return prompts.CRM_ERROR_INSTRUCTION
    # Терминальный ход без телефона означает одно: человек отказался его дать,
    # и анкета закрылась по потолку попыток. Проверка стоит ВЫШЕ веток фазы
    # намеренно: prepare_turn к этому моменту уже проставил survey_completed, и
    # общая ветка возвращала «поблагодари и скажи, что данные собраны». Модель,
    # видя историю из сплошных просьб, просила номер ещё раз — и получалось
    # «уточните номер» вплотную к «без номера заявку дальше не передать».
    if not _has_phone(state) and (
        current_plan.action in {"route_found", "no_fit"}
        or phase in {"survey_completed", "no_fit", "link_sent"}
    ):
        return prompts.REFUSED_CONTACT_INSTRUCTION

    if phase == "link_sent":
        return prompts.POST_LINK_INSTRUCTION
    if phase == "survey_completed":
        return prompts.SURVEY_COMPLETED_INSTRUCTION
    if phase == "delivery_error":
        return prompts.DELIVERY_ERROR_INSTRUCTION

    delivery_result = state.get("delivery") or {}
    if phase == "no_fit" or (
        current_plan.action == "route_found" and delivery_result.get("status") == "no_fit"
    ):
        return prompts.turn_instruction(Plan(action="no_fit"))

    # Прощание проверяем самым последним: до этой строки уже отработали все
    # ветки со своей инструкцией хода (терминальные фазы, статусы CRM, отказ
    # от телефона) — у каждой свой тон, и общее прощание бы его стёрло. Ревью
    # 09.09.2026 поймало живьём: «Спасибо» после ошибки доставки
    # (dialog_phase="delivery_error") перебивало DELIVERY_ERROR_INSTRUCTION,
    # потому что явный список исключений забыл про эту фазу. Проверка здесь,
    # последней по порядку, не может забыть новую ветку выше — приоритет
    # держит сам порядок кода, а не список, который нужно помнить обновлять.
    if client_is_saying_goodbye(state.get("_last_user_message") or ""):
        return _leaving_instruction(state.get("_last_user_message") or "")

    return prompts.turn_instruction(current_plan)


def _has_phone(state: dict[str, Any]) -> bool:
    phone = ((state.get("facts") or {}).get("client") or {}).get("phone")
    return isinstance(phone, str) and bool(phone.strip())


def stream_reply(
    state: dict[str, Any],
    user_message: str,
    config: AppConfig | None = None,
    usage_collector: Any = None,
    critic_usage_collector: Any = None,
    factual_usage_collector: Any = None,
) -> Iterator[str]:
    config = config or load_config()
    if config.policy_voice_enabled and (onloader_policy.dialogue_active(state) or debt_policy.debt_delivery_held(state)):
        yield _policy_conversation(state, user_message, config, usage_collector, critic_usage_collector,
                                   factual_usage_collector)
        return
    if onloader_policy.dialogue_active(state):
        yield onloader_policy.reply(state)
        return
    if debt_policy.debt_delivery_held(state):
        yield _debt_reply(state)
        return
    payload = build_runtime_payload(state, user_message)
    yield from llm_agent.stream_conversation(payload, config, usage_collector=usage_collector)


def stream_opening(
    state: dict[str, Any],
    config: AppConfig | None = None,
    usage_collector: Any = None,
) -> Iterator[str]:
    if onloader_policy.dialogue_active(state):
        yield onloader_policy.reply(state)
        return
    if debt_policy.debt_delivery_held(state):
        yield _debt_reply(state)
        return
    if context_delivery_held(state):
        yield CONTEXT_OPENING_MESSAGE
        return
    config = config or load_config()
    payload = build_runtime_payload(state, "")
    yield from llm_agent.stream_opening(payload, config, usage_collector=usage_collector)


def _debt_reply(state: dict[str, Any]) -> str:
    policy = debt_policy.parse_policy(state)
    assert policy is not None
    current_plan = state.get("_plan_object")
    if not isinstance(current_plan, Plan):
        current_plan = debt_policy.plan_debt(get_matrix(), state.get("facts", {}), policy,
                                             fact_statuses=state.get("fact_statuses"),
                                             attempts=_field_attempts(state),
                                             contact_known=onloader_policy.contact_known(state))
    return debt_policy.render_debt_reply(policy, current_plan, state.get("facts", {}))


def _policy_conversation(state: dict[str, Any], message: str, config: AppConfig,
                         usage_collector: Any = None, critic_usage_collector: Any = None,
                         factual_usage_collector: Any = None) -> str:
    """Reuse the conversation model, retaining the policy's authoritative question and holds."""
    current_plan = state.get("_plan_object")
    policy = debt_policy.parse_policy(state)
    if onloader_policy.dialogue_active(state):
        expected = None
        metadata = onloader_policy.public_metadata(state) or {}
        status = str(metadata.get("status") or "unavailable")
        fallback = ("Решение по предложению можно выбрать кнопками под ним. "
                    "Для отказа используйте кнопку «Не передавать»." if status == "awaiting_consent" else
                    onloader_policy.reply(state))
    else:
        assert policy is not None
        current_plan = current_plan if isinstance(current_plan, Plan) else _build_questionnaire_plan(state, get_matrix(), config)
        expected = policy_voice.question_field(
            "intent" if policy.mode == "clarify" else next(iter(current_plan.field_paths), None)
        )
        status = str(state.get("dialog_phase") or "collecting_debt_context")
        fallback = _debt_reply(state)
    known: set[str] = set()
    # Confirmed routing is also context for speech. A clarification has not
    # established this goal and must remain free to ask about it.
    if policy is not None and policy.mode == "debt":
        known.add("intent")
    facts = state.get("facts") or {}
    for path in ("debts.total", "client.region_class", "client.name", "client.phone"):
        value = get_fact(facts, path)
        valid = value not in (None, "") and not debt_policy._requires_confirmation(state.get("fact_statuses"), path)
        if path == "debts.total":
            valid = valid and debt_policy.debt_band(value) != "unknown"
        if path == "client.region_class":
            valid = valid and value in ("moscow_mo", "rf_other")
        if valid:
            known.add(path)
    if onloader_policy.contact_known(state):
        known.add("client.phone")
    # A stored candidate is not a confirmed answer. Follow the plan's uncertainty.
    if isinstance(current_plan, Plan):
        known.difference_update(item.path for item in current_plan.fields if item.needs_confirmation)
    if policy is not None and policy.amount_observation is not None and policy.amount_observation.kind != "exact":
        known.discard("debts.total")
    refused = frozenset({"client.phone"}) if policy is not None and policy.phone_collection == "declined" else frozenset()
    is_onloader = onloader_policy.dialogue_active(state)
    actual_offer = onloader_policy.read_state(state)
    question_goal: ResponseGoal | None = None
    if (is_onloader and actual_offer is not None and re.search(
            r"\b(?:зачем|для\s+чего|объясн\w*|поясн\w*|что\s+(?:это\s+)?значит)\b", message, re.I)
            and (len(message.strip()) <= 12 or re.search(r"\bэт\w*|сведен|данн|контакт|номер|телефон|переда|нужно", message, re.I)
                 or re.fullmatch(
                     r"\s*(?:(?:да|нет|ну|а)[.!?,\s]+)?(?:объясни(?:те)?|поясни(?:те)?)"
                     r"(?:[\s,]+(?:мне|пожалуйста|проще|попроще|понятнее|нормально|"
                     r"по[-‑– ]человечески|простыми\s+словами))*[.!?\s]*", message, re.I))):
        question_goal = "offer_information_purpose"
    elif (is_onloader and actual_offer is not None and re.search(
            r"\bкому\b[^.!?]{0,90}\b(?:данн\w*|сведен\w*|контакт\w*|номер|телефон|"
            r"заявк\w*|уйд\w*|переда\w*|отправ\w*)\b|"
            r"\b(?:кто|како[йя])\b[^.!?]{0,45}\b(?:получател\w*|получит|компани\w*)\b", message, re.I)):
        # A public fact from the current card remains answerable during an
        # interpretation outage. This goal authorizes no user decision or action.
        question_goal = "offer_recipient"
    elif (re.search(r"\b(?:зачем|почему)\b", message, re.I) and (expected == "debts.total" or re.search(
            r"\b(?:сумм\w*|долг\w*|задолжен\w*)\b", message, re.I))):
        question_goal = "debt_amount_purpose"
    elif (not is_onloader and re.search(r"\b(?:реструктуризац\w*|банкротств\w*|списани\w*)\b", message, re.I)
          and re.search(r"\b(?:чем|что\s+такое|объясн\w*|поясн\w*|не\s+понял[аи]?|"
                        r"можно\s+(?:ли\s+)?без)\b", message, re.I)):
        # A request to understand debt options has its own reply goal. The
        # pending intake step remains available after the explanation.
        question_goal = "debt_explanation"
    frame = DialogueFrame(expected, status, frozenset(known), refused,
                          mode="onloader" if is_onloader else "debt",
                          offer_present=actual_offer is not None,
                          transfer_blocked=policy is not None and policy.mode == "debt" and actual_offer is None and debt_policy.debt_delivery_held(state),
                          known_phone_reference=debt_workflow.known_phone_reference(state, message),
                          offer_status=actual_offer.status if actual_offer is not None else None,
                          offer_recipient_name=actual_offer.offer.partner_name if actual_offer is not None else None,
                          conversation_declined=(onloader_policy.chat_declined(state) if is_onloader else
                                                 policy is not None and policy.transfer_declined),
                          conversation_paused=_conversation_semantic_paused(state),
                          closing=client_is_saying_goodbye(message),
                          question_goal=question_goal,
                          received_fields=frozenset(
                              field for path in (state.get("_policy_received_fields") or ())
                              if isinstance(path, str) and (field := policy_voice.question_field(path)) is not None
                          ),
                          confirming=isinstance(current_plan, Plan) and any(
                              item.needs_confirmation for item in current_plan.fields),
                          require_question=expected is not None and not client_is_saying_goodbye(message)
                          and re.search(r"\?|^(?:а\s+)?(?:зачем|почему|как|что|чем|можно|нужно|объясните|расскажите|подскажите)\b",
                                        message.strip(), re.I) is None)
    previous_reply = next((str(item.get("content") or "") for item in reversed(state.get("chat_history") or [])
                           if item.get("role") == "assistant" and str(item.get("content") or "").strip()), "")
    # Preserve only the field-value role across privacy masking. The original
    # value stays inside this boundary and grants no collection or action.
    frame = replace(frame, plain_reply_fields=frozenset(
        field for path in frame.received_fields | frame.known_fields
        if (field := policy_voice.question_field(path)) is not None
        and _plain_received_field_answer(message, field)
    ))
    # The latest field value leaves the optional discussion question pending.
    # This is a turn-local speech role, without changing intake, facts or consent.
    frame = replace(frame, defer_discussion_followup=should_defer_discussion_followup(
        frame, previous_reply=previous_reply, user_message=message,
    ))
    # Bare yes/no answers refer to the immediate question, not every operation
    # that happens to be active in the session. Free-form questions grant no target.
    if is_onloader and not frame.conversation_declined and actual_offer is not None \
            and actual_offer.status == "awaiting_consent" \
            and previous_reply.strip() == onloader_policy.reply(state).strip():
        frame = replace(frame, reply_target="transfer")
    elif expected == "client.phone":
        prior_frame = replace(frame, closing=False)
        if last_collection_field(previous_reply, prior_frame) == "client.phone":
            frame = replace(frame, reply_target="phone")
    def redact_draft(draft: str) -> str:
        # Keep original facts at the trusted boundary. The revision sees only
        # the same context-aware redaction already used for model messages.
        masked = mask_payload_for_llm({
            "facts": facts, "latest_user_message": draft, "current_context_readonly": True,
        }, True)
        return str(masked["latest_user_message"])

    def finalize_reply(text: str) -> str:
        # Apply local name protection before the critic sees the final visible body.
        text = omit_deferred_discussion_followup(text, frame)
        text = omit_explanatory_preference_followup(text, frame, user_message=message)
        text = omit_repeated_availability_tail(
            text, recent_replies=[str(turn.get("content") or "")
                                  for turn in (state.get("chat_history") or [])[-10:]
                                  if isinstance(turn, dict) and turn.get("role") == "assistant"],
            user_message=message,
        )
        if frame.question_goal is None and not frame.contact_declined and not frame.confirming:
            # Only a complete service receipt after a proven plain field answer
            # is omitted. Every unmatched bubble retains the model's wording.
            without_receipt = omit_empty_field_receipt_bubbles(
                text, received_fields=frame.received_fields, user_message=message,
                known_region="client.region_class" in frame.known_fields)
            # Composed cleanup must leave speech for the normal critic and
            # hard guards. Keep the model's receipt when it is the whole body.
            if without_receipt.strip():
                text = without_receipt
        text = re.sub(r"(?mi)^[ \t]*\[(?:имя скрыто|ФИО скрыто|сведения анкеты)\][ \t]*[,.:!—-]?[ \t]*", "", text)
        text = "\n\n".join(_drop_borrowed_name(prompts.split_bubbles(text), state))
        known_name = (client_display_name(state.get("facts")) or "").casefold().replace("ё", "е")

        def confirmed_address(match: re.Match[str]) -> str:
            candidate = match.group(2).casefold().replace("ё", "е")
            return match.group(0) if candidate not in FIRST_NAMES or candidate == known_name else ""

        cleaned = re.sub(r"(^|,\s+)([А-ЯЁ][а-яё]+)(?=[,.!?])", confirmed_address, text)
        cleaned = cleaned.lstrip(", ")
        # Removing an invented leading address must leave a valid sentence
        # before both deterministic validation and semantic assessment.
        return cleaned[:1].upper() + cleaned[1:]

    if config.policy_semantic_enabled:
        grounded = policy_grounded_voice.render(
            build_runtime_payload(state, message), config, frame=frame, fallback=fallback,
            usage_collector=usage_collector, draft_redactor=redact_draft,
            critic_usage_collector=critic_usage_collector, finalize_reply=finalize_reply,
            factual_usage_collector=factual_usage_collector,
        )
        applied = _apply_conversation_semantics(state, grounded, expected_offer=actual_offer)
        spoken = (grounded.spoken if applied else replace(
            grounded.spoken, text="Предложение изменилось. Обновите его перед выбором решения.",
            asked_field=None, source="fallback", reason="semantic_offer_changed",
        ))
    else:
        spoken = policy_voice.render(build_runtime_payload(state, message), config, expected=expected,
                                     status=status, fallback=fallback, usage_collector=usage_collector, frame=frame,
                                     fallback_question=frame.allowed_field, draft_redactor=redact_draft,
                                     critic_usage_collector=critic_usage_collector, finalize_reply=finalize_reply,
                                     factual_usage_collector=factual_usage_collector)
    # Only a validated speech result can be committed. It never enters durable business state.
    state["_policy_spoken"] = spoken
    session_id = str(state.get("session_id") or "")
    if session_id:
        analytics.get_recorder().emit(analytics.EVENTS.POLICY_VOICE_RENDERED, session_id, {
            "version": policy_grounded_voice.VERSION if config.policy_semantic_enabled else policy_voice.VERSION,
            "source": spoken.source, "reason": spoken.reason,
            "question_field": spoken.asked_field, "model": config.model,
            "critic_model": spoken.critic_model, "critic_calls": spoken.critic_calls,
            "code_user_acts": list(spoken.code_user_acts),
        })
    return spoken.text


def _conversation_semantic_paused(state: dict[str, Any]) -> bool:
    """A degraded interpretation suppresses persuasion without asserting a refusal."""
    current = onloader_policy.read_state(state)
    if current is not None:
        preference = state.get(onloader_policy.CHAT_KEY)
        return bool(current.decision is None and isinstance(preference, dict)
                    and preference.get("presentation_id") == current.presentation_id
                    and preference.get("paused") is True)
    preference = state.get("conversation_semantics")
    return isinstance(preference, dict) and preference.get("paused") is True


def _apply_conversation_semantics(state: dict[str, Any], result: policy_grounded_voice.GroundedReply, *,
                                  expected_offer: OnloaderState | None) -> bool:
    """Apply only conversational preferences, leaving formal consent and delivery untouched."""
    current = onloader_policy.read_state(state)
    if current is not None or expected_offer is not None:
        state["_semantic_onloader_choice"] = True
    # Private offer identity never leaves the server. Public-frame equality alone
    # cannot distinguish two presentations carrying the same displayed label.
    if current != expected_offer:
        return False
    if current is not None:
        if current.decision is None:
            state[onloader_policy.CHAT_KEY] = {
                "presentation_id": current.presentation_id,
                "declined": result.effective_frame.conversation_declined,
                "paused": result.paused,
            }
    else:
        policy = debt_policy.parse_policy(state)
        if policy is not None:
            if "decline_phone" in result.applied_user_acts:
                policy = policy.model_copy(update={"phone_collection": "declined"})
            # An admitted negative preference is durable without an offer.
            # Keep both refusals independent; this grants no consent or delivery.
            if policy.mode == "debt" and "decline_transfer" in result.applied_user_acts:
                policy = policy.model_copy(update={"transfer_declined": True})
            state[debt_policy.POLICY_KEY] = policy.as_state()
        state["conversation_semantics"] = {"paused": result.paused}
    return True


def initialize_debt_questionnaire(state: dict[str, Any]) -> dict[str, Any]:
    """Persist the first voiced question before the selected page accepts an answer."""
    policy = debt_policy.parse_policy(state)
    if policy is None:
        raise ValueError("debt policy required")
    current_plan = debt_policy.plan_debt(get_matrix(), state.get("facts", {}), policy)
    state["_plan_object"] = current_plan
    state["plan"] = _plan_snapshot(current_plan)
    state["dialog_phase"] = debt_policy.policy_phase(policy, current_plan, state.get("facts", {}))
    return _commit_debt_turn(state, "")["current_state"]


def _commit_debt_turn(state: dict[str, Any], user_message: str, full_reply: str | None = None) -> dict[str, Any]:
    """Record checked speech and count only the question actually voiced."""
    new_state = deepcopy(state)
    spoken = new_state.pop("_policy_spoken", None)
    new_state.pop("_policy_received_fields", None)
    validated = isinstance(spoken, policy_voice.SpokenPolicyReply) and spoken.text == full_reply
    reply = spoken.text if validated else _debt_reply(new_state)
    current_plan = new_state.pop("_plan_object", None)
    policy = debt_policy.parse_policy(new_state)
    assert policy is not None
    for key in (entry_routing.GREETING_KEY, entry_routing.OTHER_QUESTION_KEY,
                entry_routing.TRANSITION_KEY, quick_replies.CHOICE_KEY):
        new_state.pop(key, None)
    history = new_state.setdefault("chat_history", [])
    if user_message:
        history.append({"role": "user", "content": user_message})
    history.append({"role": "assistant", "content": reply})
    if not validated or spoken.asked_field is not None:
        _count_attempts(new_state, current_plan)
    elif spoken.unbound_financial_question:
        # Keep a FAQ's outstanding question, but never bind an answer to a different
        # financial question to the previous debt cursor.
        script = new_state.setdefault("script", {})
        script["asked"] = [path for path in script.get("asked", []) if path != "debts.total"]
    new_state["message_count"] = state.get("message_count", 0) + 1
    session_id = str(new_state.get("session_id") or "")
    if session_id:
        analytics.get_recorder().emit(analytics.EVENTS.DEBT_POLICY_TURN, session_id, {
            "version": policy.version, "mode": policy.mode, "reason": policy.reason,
            "phase": new_state.get("dialog_phase"),
            "band": debt_policy.debt_band(get_fact(new_state.get("facts", {}), "debts.total"))
            if policy.amount_observation is None or policy.amount_observation.kind == "exact" else "unknown",
        })
    _record_transcript(new_state, user_message, [reply])
    return {"messages": [reply], "current_state": new_state}


def commit_turn(state: dict[str, Any], user_message: str, full_reply: str) -> dict[str, Any]:
    """Подшивает реплики в историю и добавляет детерминированный хвост со ссылкой.

    Ссылка отдаётся шаблоном, а не моделью: «поправленный» или дописанный URL уводит
    клиента в никуда, и заметить это в проде почти невозможно.
    """
    if onloader_policy.dialogue_active(state):
        spoken = state.get("_policy_spoken")
        text = spoken.text if isinstance(spoken, policy_voice.SpokenPolicyReply) and spoken.text == full_reply else None
        return onloader_policy.commit(state, user_message, text)
    if debt_policy.debt_delivery_held(state):
        return _commit_debt_turn(state, user_message, full_reply)
    # Признак «разговор закончен» нужен запасной фразе: в середине анкеты она
    # обещает продолжить, а после отправки лида такое обещание — ложь.
    bubbles = guard_bubbles(
        prompts.split_bubbles(full_reply),
        conversation_over=state.get("dialog_phase") in _TERMINAL_PHASES,
        scenario=entry_routing.current_scenario(state),
    ) or [full_reply.strip() or "…"]
    new_state = deepcopy(state)
    current_plan = new_state.pop("_plan_object", None)
    entry_transition = new_state.pop(entry_routing.TRANSITION_KEY, None)
    greeting = bool(new_state.pop(entry_routing.GREETING_KEY, False))
    other_question = bool(new_state.pop(entry_routing.OTHER_QUESTION_KEY, False))
    quick_reply_choice = new_state.pop(quick_replies.CHOICE_KEY, None)
    # В ветках развилки анкета не звучит: ни предохранитель финала, ни
    # переходные фразы, ни просьба о контактах, ни счётчик попыток. На голом
    # приветствии и на кнопке «Другой вопрос» — тоже: сначала человек скажет,
    # с чем пришёл.
    questionnaire = entry_routing.questionnaire_active(new_state) and not (
        greeting or other_question
    )
    if questionnaire:
        bubbles = _guard_terminal_bubbles(bubbles, current_plan, new_state)
        bubbles = _hold_premature_finish(bubbles, current_plan, new_state)
    history = new_state.setdefault("chat_history", [])
    if user_message:
        history.append({"role": "user", "content": user_message})

    # Всё, что backend добавляет к реплике модели ради вопроса анкеты, решается
    # одной проверкой. На пустом opening-ходе оставляем только приветствие:
    # контактный вопрос можно присоединить лишь после реальной первой реплики
    # клиента. На прощании не добавляем ни просьбу, ни переходную фразу —
    # иначе клиент на «Да, спасибо» получает «теперь про активы…» и следом
    # «я на связи» (ревью 09.09.2026).
    units_turn = questionnaire and isinstance(
        (new_state.get("script") or {}).get("units_pending"), dict
    )
    if units_turn:
        # Ход переспроса единиц: в реплике один вопрос — про них. Мутационный
        # прогон 01.10.2026: «1 млн? | В каком городе живёте?» — клиент
        # ответил про город, переспрос потерялся, а на следующем круге его
        # «Нет» про недвижимость записало сумму 1000 рублей. Вопрос плана на
        # этом ходу не звучит, поэтому не считается ни заданным, ни попыткой.
        bubbles = _only_the_first_question(bubbles)
        new_state.setdefault("script", {})["asked"] = []
    elif questionnaire and _question_is_voiced(user_message):
        declined = _client_declined(new_state)
        backend_asks = not declined and _backend_adds_contact_request(current_plan)
        if backend_asks or any(prompts.asks_something(bubble) for bubble in bubbles):
            # Попытка засчитывается здесь, до сборки контактной просьбы: её
            # формулировка зависит от номера попытки, и текущая уже должна
            # войти в счёт — так же, как было, пока счётчик жил в prepare_turn.
            _count_attempts(new_state, current_plan)
            if not declined:
                bubbles = _prepend_transition(bubbles, current_plan, new_state)
                bubbles = _merge_contact_request(
                    bubbles,
                    current_plan,
                    user_message=user_message,
                    phone_attempts=_field_attempts(new_state).get("client.phone", 0),
                )
        else:
            # Модель ответила на реплику клиента и вопроса анкеты не задала:
            # про списания с карты, про чужой банк, на отказ. Попытка за такой
            # ход не засчитывается, переходная фраза не ставится, а быстрый
            # разбор не ждёт ответа на вопрос, которого не было. Бой
            # 20.10.2026, сессия e79066285842: пока бот отвечал про списания,
            # у шести полей анкеты сгорели все попытки, а посреди разговора
            # о карте вылезло «Теперь про активы… Мы почти закончили».
            new_state.setdefault("script", {})["asked"] = []
    # Именно здесь, а не до вставок: переход и контактный пузырь тоже начинаются
    # с имени, и раньше правило их не видело.
    bubbles = _drop_borrowed_name(bubbles, new_state)
    bubbles = _reduce_repeated_name(bubbles, state)
    followup = _delivery_followup(new_state) or _site_consent_followup(new_state)
    if followup:
        bubbles = _drop_followup_duplicates(bubbles, followup, without_contact=not _has_phone(new_state))
    for bubble in [*bubbles, *followup]:
        history.append({"role": "assistant", "content": bubble})

    new_state["message_count"] = state.get("message_count", 0) + 1
    # Шаг анкеты пишется только там, где анкета идёт: иначе ветки «обрывались
    # бы на сумме» в воронке заказчика. Ветку отмечает `entry_routed`.
    if questionnaire:
        _record_turn_analytics(new_state, current_plan)
    entry_routing.record_transition(new_state, entry_transition)
    entry_routing.record_cancel_request(new_state, user_message)
    quick_replies.record_choice(new_state, quick_reply_choice)
    # Клиент попрощался в ветке — разговор в ней закончен.
    if not questionnaire and client_is_saying_goodbye(user_message):
        entry_routing.close_branch(new_state)
    _record_transcript(new_state, user_message, [*bubbles, *followup])
    return {"messages": [*bubbles, *followup], "current_state": new_state}


def _backend_adds_contact_request(current_plan: Plan | None) -> bool:
    """Вопрос этого хода задаёт сам backend — просьба об имени или телефоне."""
    if current_plan is None or current_plan.action not in {"ask", "contacts"}:
        return False
    return bool({"client.name", "client.phone"} & set(current_plan.field_paths))


def _field_attempts(state: dict[str, Any]) -> dict[str, int]:
    script_key = "debt_script" if debt_policy.debt_delivery_held(state) and "debt_script" in state else "script"
    return dict((state.get(script_key) or {}).get("attempts") or {})


def _clarified_paths(state: dict[str, Any]) -> set[str]:
    """Поля, о которых бот уже ПЕРЕспрашивал.

    Приблизительный ответ держится ровно одно уточнение: если человек повторил
    то же самое, значит он так и отвечает. Список берётся из того же счётчика
    попыток, что и потолок вопросов, — иначе правило осталось бы мёртвым, как
    уже случилось с потолком для телефона.

    Порог именно два. Единица означает «вопрос задан один раз», и уточнения ещё
    не было: с порогом в единицу оговорка принималась бы сразу, а заказчик
    просил одно уточнение.
    """
    return {
        path
        for path, count in _field_attempts(state).items()
        if int(count or 0) >= 2
    }


def _remember_asked(state: dict[str, Any], current_plan: Plan) -> None:
    """Запоминает, о чём анкета спросила этим ходом.

    Быстрый разбор (interpretation/fastpath.py) считает эту запись описанием
    реально заданного вопроса и кладёт короткий ответ прямо в названное поле,
    минуя модель; фоновой правки за ним не будет, так что расхождение
    необратимо. Поэтому запись делается там же, где засчитывается попытка, —
    в `commit_turn`, по окончательному плану хода и только если вопрос
    прозвучал. План, пересчитанный `replan`, сюда попадает сам: он лежит в
    `_plan_object`.
    """
    script = state.setdefault("script", {})
    script["asked"] = (
        list(current_plan.field_paths)
        if current_plan.action in {"ask", "contacts"}
        else []
    )


def _question_is_voiced(user_message: str) -> bool:
    """Прозвучит ли на этом ходу вопрос из плана.

    План считается на каждом ходу, но вопрос звучит не на каждом. На
    приветственном ходе (реплика пустая) бот только здоровается — контактную
    просьбу к приветствию не приклеиваем. На прощании модель по
    `prompts.FAREWELL_INSTRUCTION` отвечает прощанием без вопросов, а
    `_merge_contact_request` не добавляет просьбу о контактах.

    Проверка та же, что внутри `_merge_contact_request`, и это намеренно: все
    решения хода о прозвучавшем вопросе должны расходиться одинаково, иначе
    счётчик засчитает попытку за просьбу, которую просьба же и не добавила.
    """
    return bool(user_message) and not client_is_saying_goodbye(user_message)


def _count_attempts(state: dict[str, Any], current_plan: Plan | None) -> None:
    """Считает, сколько раз подряд анкета просит одно и то же поле.

    Считаем именно попытки, а не ходы: поле, на которое клиент ответил, из плана
    уходит и счётчик к нему больше не возвращается. Растёт он только там, где
    ответ не лёг в state — либо клиент отвечает не на то, либо reducer придержал
    значение как приблизительное.

    Без этого счётчика курсор встаёт на поле навсегда: 29.07.2026 так вышло
    шесть ходов подряд, анкета не завершилась и обращение не попало в CRM.

    Контактный ход считается наравне с анкетным. Сначала здесь стояло только
    ``ask``, и потолок попыток для телефона, добавленный в планировщик, не
    срабатывал никогда: увеличивать счётчик было некому. Ролевой прогон
    11.08.2026 показал результат — один и тот же запрос номера три раза подряд
    дословно.

    Вызывать только для хода, где вопрос действительно прозвучал
    (`_question_is_voiced`). Раньше вызов стоял в `prepare_turn`, и попытка
    засчитывалась за план, а не за вопрос. Ревью 09.09.2026 нашло два следствия:
    имя получало попытку на приветствии, где его не спрашивали, и после неё
    выпадало из знакомства — бот не спрашивал имя ни разу; а три «Спасибо»
    подряд доводили до потолка сумму и возраст, которых бот не называл.
    """
    if current_plan is None:
        return
    _remember_asked(state, current_plan)
    if current_plan.action not in {"ask", "contacts"}:
        return
    script_key = "debt_script" if debt_policy.debt_delivery_held(state) and "debt_script" in state else "script"
    attempts = state.setdefault(script_key, {}).setdefault("attempts", {})
    for path in current_plan.field_paths:
        attempts[path] = int(attempts.get(path) or 0) + 1


def _record_turn_analytics(state: dict[str, Any], current_plan: Plan | None) -> None:
    """Отмечает шаг воронки. Никогда не влияет на ответ клиенту.

    `turn_completed` несёт номер шага анкеты — по нему собирается отвал, которым
    заказчик мерит разворот («80% доходят до конца»).

    `qualification_completed` возникает один раз, когда сценарий исчерпан и план
    ушёл из `ask`. Признак `matched` разделяет пункты 2 и 3 заказчика:
    завершённые и успешно завершённые. Анкета без маршрута тоже завершена —
    иначе знаменатель воронки потеряет тех, кому решения не нашлось.
    """
    if current_plan is None:
        return
    session_id = str(state.get("session_id") or "")
    if not session_id:
        return

    recorder = analytics.get_recorder()
    matched = current_plan.matched
    recorder.emit(
        analytics.EVENTS.TURN_COMPLETED,
        session_id,
        {
            "step": current_plan.step,
            "action": current_plan.action,
            "field": current_plan.field_paths[0] if current_plan.field_paths else None,
            "turn": state.get("message_count"),
        },
    )

    if current_plan.action == "ask":
        return
    script = state.setdefault("analytics", {})
    if script.get("qualification_recorded"):
        return
    script["qualification_recorded"] = True
    recorder.emit(
        analytics.EVENTS.QUALIFICATION_COMPLETED,
        session_id,
        {
            "matched": matched is not None,
            "route_id": matched.route.id if matched else None,
            "priority": matched.route.priority if matched else None,
            "turns": state.get("message_count"),
        },
        dedup_key=f"{session_id}:qualification_completed",
    )


def _record_transcript(
    state: dict[str, Any],
    user_message: str,
    bubbles: list[str],
) -> None:
    """Складывает ход в базу заказчика. Никогда не влияет на ответ клиенту.

    Пишется то же, что видел клиент: его реплика и отданные пузыри — включая
    детерминированный хвост со ссылкой, иначе переписка обрывается на самом
    интересном месте. Номер телефона заменяется отпечатком внутри recorder'а.
    """
    session_id = str(state.get("session_id") or "")
    if not session_id:
        return
    analytics.get_transcripts().record_turn(
        session_id,
        int(state.get("message_count") or 0),
        user_message,
        bubbles,
    )


def _prepend_transition(
    bubbles: list[str],
    current_plan: Plan | None,
    state: dict[str, Any],
) -> list[str]:
    """Ставит перед репликой модели сглаживающую фразу шага анкеты.

    Переходы из сценария пишет backend, а не модель, например «теперь про
    активы». Формальное вступление начального шага в сценарии отключено.
    Каждый оставшийся переход звучит один раз за диалог — повтор на том же
    шаге, если клиент переспросил, выглядел бы как заевшая пластинка.

    Вызывается только на ходу, где вопрос анкеты действительно звучит
    (`_question_is_voiced` в `commit_turn`). Шаг помечается отправленным
    здесь, поэтому звать функцию на прощании нельзя: фраза не прозвучит, а
    отметка останется, и вернувшийся клиент дословную фразу заказчика уже не
    услышит.
    """
    if current_plan is None or not current_plan.transition:
        return bubbles

    script_state = state.setdefault("script", {})
    sent = script_state.setdefault("transitions_sent", [])
    step = current_plan.step
    if step in sent:
        return bubbles
    sent.append(step)

    # Бюджет пузырей — три. Переход занимает один, поэтому реплику модели режем.
    # Режем с середины, а не с конца: модель отвечает в порядке «реакция →
    # пояснение → вопрос» (так её учит STYLE_EXAMPLES), и прежнее `bubbles[:2]`
    # выбрасывало именно вопрос — самое ценное в ходе анкеты.
    #
    # Ролевой прогон 19.08.2026: клиент дважды спросил «а зачем это нужно?» и
    # дважды не получил ответа, а на одном ходу бот выдал одно утверждение без
    # вопроса вовсе. Шаги с переходной фразой проходят все диалоги, так что
    # доставалось каждому.
    if len(bubbles) <= 2:
        return [current_plan.transition, *bubbles]
    return [current_plan.transition, bubbles[0], bubbles[-1]]


def _awaits_site_consent(state: dict[str, Any] | None) -> bool:
    """Backend ждёт от клиента да/нет по консультации."""
    if not isinstance(state, dict):
        return False
    workflow = state.get("crm_workflow") or {}
    return workflow.get("status") == "awaiting_site_consent"


def _guard_terminal_bubbles(
    bubbles: list[str],
    current_plan: Plan | None,
    state: dict[str, Any] | None = None,
) -> list[str]:
    """Не позволяет модели продолжить анкету после решения state machine.

    Исключение — тупик, в котором движок сам просит телефон: этот вопрос
    санкционирован планом, и вырезать его нельзя, иначе клиент увидит отказ
    без единой возможности оставить контакт.

    Второе исключение — согласие на консультацию. Его тоже просит backend, а не
    модель: CRM ответила `need_site_consent`, и без ответа клиента лид не
    создаётся вовсе. Анкета к этому моменту пройдена, план стоит в
    `route_found`, и guard молча вырезал этот вопрос вместе с остальными.
    Живой диалог 19.08.2026: клиент дважды спросил «где оно?» и дважды получил
    «Спасибо, данные собраны», а в CRM не ушло ничего.
    """
    if current_plan is None or current_plan.action not in {"route_found", "no_fit"}:
        return bubbles
    if current_plan.action == "no_fit" and current_plan.fields:
        return bubbles
    if _awaits_site_consent(state):
        return bubbles

    declarative = [bubble for bubble in bubbles if "?" not in bubble]
    if declarative:
        return declarative
    return ["Спасибо, данные собраны."]


# Модель объявляет анкету законченной, пока план ещё спрашивает: «Заявку
# передала, скоро с вами свяжутся», «Подбор завершён, передам данные
# специалисту», «Сейчас подберу варианты и пришлю ссылку» — и просит телефон
# вне очереди. Лид при этом не создаётся. С 01.10.2026 так потеряно шесть
# заявок, пять — с телефоном (90a41292d3ba, 236397c01d9f, 72b931171e8f,
# 43094ae508e1, 554bb65d6b2a, e0837cd8a8f9); четыре — на вопросах о
# собственности и втором объекте, когда ответ не разобрался.
_PREMATURE_FINISH_RE = re.compile(
    r"заявк\w*\s+(?:уже\s+)?передал"
    r"|переда(?:ю|м|ём|ем|ла|л)\s+(?:вашу\s+|ваши\s+)?(?:заявк|данные|ситуаци)"
    r"|подбер[уеё]\w*\s+(?:\w+\s+)?вариант"
    r"|пришл[юёе]\w*\s+(?:вам\s+)?ссылк"
    r"|подбор\s+заверш|анкет\w*\s+(?:заполнен|заверш)|на\s+этом\s+вс[её]"
    r"|(?:какой|ваш)\s+номер|номер\w*\s+телефон|оставьте\s+(?:\w+\s+)?номер"
    r"|вс[её]\s+зафиксировал",
    re.IGNORECASE,
)

# Вопросы матрицы написаны о клиенте в третьем лице («сколько лет клиенту»):
# это рамка для модели. Сторож задаёт вопрос сам, поэтому переводит его на «вы».
_TO_SECOND_PERSON = (
    (r"\bсколько лет клиенту\b", "сколько вам полных лет"),
    (r"\bготов ли клиент\b", "готовы ли вы"),
    (r"\bклиент явно предпочитает\b", "вы предпочитаете"),
    (r"\bклиенту\b", "вам"),
    (r"\bклиента\b", "вас"),
    (r"\bклиент\b", "вы"),
    (r"\bживёт\b", "живёте"),
    (r"\bработает\b", "работаете"),
)
# Вопросы о втором объекте говорят «этот объект» — без предыдущей реплики
# непонятно, какой.
_SECOND_OBJECT = (
    (r"\bэтот объект\b", "второй объект"),
    (r"\bэтого объекта\b", "второго объекта"),
    (r"\bза объект\b", "за второй объект"),
)


def _plan_question(current_plan: Plan) -> str:
    """Вопрос плана словами backend: на случай, когда модель его не задала."""
    field = current_plan.fields[0]
    question = field.ask
    for third, second in _TO_SECOND_PERSON:
        question = re.sub(third, second, question)
    if field.path.startswith("assets.realty_2."):
        for this, second in _SECOND_OBJECT:
            question = re.sub(this, second, question)
    return f"Остался ещё вопрос: {question}?"


def _hold_premature_finish(
    bubbles: list[str],
    current_plan: Plan | None,
    state: dict[str, Any],
) -> list[str]:
    """Не даёт объявить заявку отправленной, пока анкета не закончена.

    Вырезает пузыри с «заявку передала», «подбор завершён», «пришлю ссылку»
    и просьбой о телефоне вне очереди. Остался вопрос — реплика идёт без
    вырезанного. Не осталось — вместо неё звучит вопрос плана: прощальное
    «Будьте на связи!» перед вопросом анкеты читается как сбой. Ход, где
    вопрос задаёт backend (имя, телефон), и ход после отказа клиента не
    трогает.
    """
    if current_plan is None or current_plan.action != "ask" or not current_plan.fields:
        return bubbles
    if _backend_adds_contact_request(current_plan) or _client_declined(state):
        return bubbles
    kept = [bubble for bubble in bubbles if not _PREMATURE_FINISH_RE.search(bubble)]
    if len(kept) == len(bubbles):
        return bubbles
    if any(prompts.asks_something(bubble) for bubble in kept):
        return kept
    return [_plan_question(current_plan)]


def _merge_contact_request(
    bubbles: list[str],
    current_plan: Plan | None,
    *,
    user_message: str = "",
    phone_attempts: int = 0,
) -> list[str]:
    """Добавляет контактный запрос детерминированным текстом, а не полагается на модель.

    Две разные точки хода: bootstrap (``action="ask"``, собираем имя в начале
    разговора) и момент перед выдачей ссылки (``action="contacts"``, маршрут уже
    найден и не хватает только телефона). Телефон намеренно не входит в bootstrap —
    заказчик описал это явно: «уточните номер телефона, я пришлю ссылку» звучит уже
    после того, как решение найдено, а не в первом сообщении.

    ``phone_attempts`` — номер этой попытки спросить телефон, текущая уже
    учтена (``state["script"]["attempts"]["client.phone"]`` после
    `_count_attempts` в `commit_turn`). Всё состояние сюда намеренно не
    передаётся: формулировке нужно только это число.
    """
    if client_is_saying_goodbye(user_message):
        # Человек уходит. Просьба представиться поверх прощания — это то, на
        # чём обрывались диалоги: «Спасибо» → «подскажите, как к вам обращаться?»
        return bubbles

    if current_plan is None or current_plan.action not in {"ask", "contacts"}:
        return bubbles

    contact_paths = {"client.name", "client.phone"} & set(current_plan.field_paths)
    if current_plan.action == "contacts":
        # Тон зависит от вердикта классификатора. Пообещать ссылку с решением, а
        # потом его не найти — риск, который заказчик назвал отдельно: «до этого ты
        # человеку пообещал, что есть решение, такой будет негативчик».
        found = current_plan.matched is not None
        if contact_paths == {"client.phone"}:
            request = _phone_request(found, phone_attempts)
        elif contact_paths == {"client.name"}:
            # Телефон уже есть, а имени нет. Сюда имя приходит один раз —
            # второй попыткой за диалог: первая всегда уходит в ветку
            # знакомства ниже, а после второй планировщик имени не ждёт
            # (`routing.planner._MAX_NAME_ATTEMPTS`) и тем же ходом отдаёт
            # заявку. Прежняя третья реплика «не хотите называть имя — ничего
            # страшного» ничего не спрашивала, но заявку держала до следующей
            # реплики клиента — и теряла её, если он уходил (бой 02.10.2026).
            # Вопрос открытый: на прежнее «Продолжим?» отвечали «да», которое
            # не имя (ревью 09.09.2026).
            request = "Как вас зовут? Достаточно одного имени."
        elif contact_paths == {"client.name", "client.phone"}:
            request = "Как к вам обращаться и какой номер телефона указать в заявке?"
        else:
            return bubbles
    elif contact_paths == {"client.name", "client.phone"}:
        request = (
            "Как к вам обращаться и какой контактный номер телефона указать в заявке?"
        )
    elif contact_paths == {"client.name"}:
        # Ветка знакомства (routing.planner.plan, ветвь initial) спрашивает имя
        # соло только один раз: после первой же неудачной попытки поле выходит
        # из initial навсегда и возвращается позже вместе с телефоном, на
        # контактном шаге выше. Второй раз сюда не попасть — ветвление по числу
        # попыток тут было бы обманом читателя, поэтому текст один.
        request = "Подскажите, как к вам обращаться?"
    elif contact_paths == {"client.phone"}:
        request = (
            "Какой контактный номер телефона указать в заявке?"
        )
    else:
        return bubbles

    # В контактной фазе принудительно собираем реакцию модели в один пузырь:
    # пользователь видит один связный ответ, а не два последовательных сообщения.
    reaction = _safe_contact_reaction(bubbles)
    return [f"{reaction} {request}".strip()]


# Просьба о телефоне по номеру попытки. Бой 19–28.09.2026: около 13% законченных
# анкет обрывались здесь — на «зачем номер?» бот до трёх раз подряд отвечал
# теми же словами про «обязательную проверку», и это читалось как давление.
# Телефон требуется оформлению заявки. Общение в чате остаётся доступным,
# поэтому приглашение не связывает помощь с передачей номера.
_PHONE_REQUESTS_FOUND = (
    "На какой номер телефона оформить заявку?",
    "Номер будет указан в заявке. Оставите его здесь?",
    "Если решите оформить заявку, напишите номер здесь. Обсудить ситуацию можно и без него.",
)
_PHONE_REQUESTS_NOT_FOUND = (
    "Оставите номер телефона для заявки?",
    "Для оформления заявки понадобится контактный номер. Можете написать его здесь.",
    "Если захотите оформить заявку, оставьте номер. Обсудить ситуацию можно и в чате.",
)


def _phone_request(found: bool, attempt: int) -> str:
    requests = _PHONE_REQUESTS_FOUND if found else _PHONE_REQUESTS_NOT_FOUND
    return requests[min(max(attempt, 1), len(requests)) - 1]


def _safe_contact_reaction(bubbles: list[str]) -> str:
    """Оставляет только реакцию модели, отбрасывая её самовольный вопрос."""
    text = " ".join(bubble.strip() for bubble in bubbles if bubble.strip())
    sentences = re.split(r"(?<=[.!?…])\s+", text)
    contact_markers = (
        "как к вам обращаться",
        "как вас зовут",
        "номер телефона",
        "контактный номер",
        "прежде чем продолжим",
        # Модель часто сама начинает финальную фразу теми же словами, что и
        # детерминированный запрос ниже. Без этих маркеров клиент читает
        # «посмотрю, что можно подобрать» дважды подряд.
        "подобрать",
        "пришлю ссылку",
        "вернусь с результатом",
        "оставьте",
        # Повторная просьба (`_phone_request`) сама называет причину и просит
        # номер. Модель на «зачем номер?» отвечала теми же словами, и клиент
        # читал «Без номера не получится… Без номера решение получить не
        # выйдет…» — живая проба 01.10.2026.
        "напишите номер",
        "номер нужен",
        "результат появится",
        "результат будет",
        # Мутационный прогон 04.10.2026: «Данные собрала. Без него дальше
        # двигаться не получится — напишите, пожалуйста. Уточните, пожалуйста,
        # номер телефона…» — «него» повисало, просьба звучала дважды.
        "без него",
        "ваш номер",
        "напишите, пожалуйста",
    )
    # Берём до двух фраз участия, а не одну. Момент самый чувствительный в
    # анкете: человек решает, отдавать ли телефон, и часто перед этим признаётся
    # в долгах. Сочувствие из двух фраз теряло вторую — ту, ради которой
    # говорилась первая: «Переживать тут не о чем. Ситуация обычная, разберёмся
    # спокойно» превращалось в «Переживать тут не о чем».
    # Просьба с голым местоимением осмысленна только вместе с предложением,
    # которое мы только что вырезали. На собранном стенде 20.08.2026 клиент
    # прочитал «Пётр, данных достаточно. Напишите его, пожалуйста» — «его» там
    # не о чем, потому что фразу про номер убрал предохранитель выше.
    orphan_request = re.compile(
        r"^(?:напишите|оставьте|укажите|продиктуйте|введите|отправьте|скиньте)"
        r"\s+(?:его|её|ее|их)",
        re.IGNORECASE,
    )
    kept: list[str] = []
    for sentence in sentences:
        if len(kept) >= 2:
            break
        lowered = sentence.lower()
        if contact_blocks_chat(sentence, phone_context=True):
            continue
        if "без номера" in lowered and not re.search(
            r"\bбез\s+номера\s+(?:можем|можно)\s+(?:обсуд\w*|продолж\w*|ответ\w*)", sentence, re.I,
        ):
            # Preserve an affirmative offer to chat, while omitting application-request echoes.
            continue
        if orphan_request.match(sentence.strip()):
            continue
        if "?" not in sentence and not any(
            marker in lowered for marker in contact_markers
        ):
            # Приветствие уже было отдельным opening-сообщением. Здесь нужен ответ
            # по существу, даже если модель решила снова начать разговор с нуля.
            cleaned = re.sub(
                (
                    r"^(?:(?:здравствуй(?:те)?|привет(?:ствую)?|"
                    r"доброе\s+утро|добрый\s+(?:день|вечер))"
                    r"\s*[,!.—:;-]*\s*)+"
                ),
                "",
                sentence.strip(),
                flags=re.IGNORECASE,
            )
            # Omit a standalone receipt before the backend's question, while
            # preserving meaningful acknowledgements as the model phrased them.
            if re.fullmatch(r"(?:понял[аи]?(?:\s+вас)?|принял[аи]?|хорошо)[.!…\s]*", cleaned, re.I):
                continue
            # «Понял вас.» после срезов выше — голая точка. Фраза без единой
            # буквы или цифры не реакция: клиент читал «. Прежде чем
            # продолжим…» (ревью 09.09.2026).
            if re.search(r"\w", cleaned):
                # Заглавная только у первой фразы: вторая продолжает мысль.
                kept.append(cleaned[0].upper() + cleaned[1:] if not kept else cleaned)
    if kept:
        return " ".join(kept)
    # A missing reaction does not need a stock acknowledgement before the question.
    return ""


def _name_address_pattern(name: str) -> re.Pattern[str]:
    """Обращение в начале реплики — целиком или любым словом имени.

    Раньше искалось только имя целиком, как оно лежит в фактах. Но в фактах
    «Валентина Петровна», а модель пишет «Валентина, …», и правило молчало: на
    ролевом прогоне 11.08.2026 обращение шло пять ходов подряд. Разделитель
    после имени обязателен, поэтому «Тестирование» под «Тест» не попадает.
    """
    forms = {name, *(part for part in name.split() if len(part) >= 3)}
    alternation = "|".join(
        re.escape(form) for form in sorted(forms, key=len, reverse=True)
    )
    return re.compile(
        rf"^(?:{alternation})(?:\s*[,!—:-]\s*|\s+)",
        re.IGNORECASE,
    )


def _drop_borrowed_name(bubbles: list[str], state: dict[str, Any]) -> list[str]:
    """Снимает обращение именем из примера промпта, если клиента зовут иначе.

    Бой 02.10 и 04.10.2026: клиентки назвались «Ольга Владимировна» и «Ираида
    Николаевна», а бот ответил «Василий, какая сумма вам нужна?» — дословно
    строкой из `prompts.STYLE_EXAMPLES`. «Меня зовут Ольга, а не Василий».
    """
    example = prompts.STYLE_EXAMPLE_NAME
    facts = state.get("facts") or {}
    known = (get_fact(facts, "client.name"), client_display_name(facts))
    if any(
        isinstance(name, str) and example.casefold() in name.casefold()
        for name in known
    ):
        return bubbles

    address = _name_address_pattern(example)
    cleaned: list[str] = []
    for bubble in bubbles:
        stripped = address.sub("", bubble, count=1).strip()
        if stripped == bubble.strip() or not stripped:
            cleaned.append(bubble)
            continue
        cleaned.append(stripped[0].upper() + stripped[1:])
    return cleaned


def _reduce_repeated_name(
    bubbles: list[str],
    state: dict[str, Any],
) -> list[str]:
    """Имя клиента звучит не чаще раза за ход и не два хода подряд.

    Заказчик 30.07.2026 прислал скриншот, где обращение шло дважды подряд:
    сначала переходная фраза «Антон, сейчас за пару минут соберу…», сразу за ней
    «Антон, какая сумма вам нужна…». Правило существовало, но применялось до
    того, как backend приклеит переход и контактный пузырь, и внутрь одного хода
    не заглядывало вообще.

    Первое обращение сохраняется, остальные снимаются. Порядок пузырей значит,
    что при переходе имя остаётся в нём: это дословная фраза заказчика, и она
    звучит раз за диалог.
    """
    if not bubbles:
        return bubbles

    name = client_display_name(state.get("facts"))
    if not name:
        return bubbles

    name_prefix = _name_address_pattern(name)
    previous_turn_bubbles: list[str] = []
    for item in reversed(state.get("chat_history") or []):
        if item.get("role") == "user":
            break
        if item.get("role") == "assistant":
            previous_turn_bubbles.append(str(item.get("content") or "").strip())
    # Прошлый ход уже назвал клиента по имени — этот начинает с нуля.
    allowance = 0 if any(
        name_prefix.search(bubble) for bubble in previous_turn_bubbles
    ) else 1

    adjusted: list[str] = []
    for bubble in bubbles:
        if not name_prefix.search(bubble):
            adjusted.append(bubble)
            continue
        if allowance > 0:
            allowance -= 1
            adjusted.append(bubble)
            continue
        stripped = name_prefix.sub("", bubble, count=1).strip()
        adjusted.append(stripped[0].upper() + stripped[1:] if stripped else bubble)
    return adjusted


# Хвост про отсутствующий телефон и то, как модель его перефразирует. Ключ —
# признак самого хвоста, значения — признаки реплики, которая говорит то же
# самое своими словами.
_NO_PHONE_TAIL_MARKER = "заявку дальше не передать"
_NO_PHONE_ECHOES = (
    ("заявк", "не переда"),
    ("заявк", "передать не"),
    ("заявк", "не смогу переда"),
)


def _drop_followup_duplicates(
    bubbles: list[str],
    followup: list[str],
    *,
    without_contact: bool = False,
) -> list[str]:
    """Снимает реплики модели, повторяющие детерминированный хвост.

    Ролевой прогон 11.08.2026: «Без него заявку дальше передать не получится» и
    сразу за ним «Без номера телефона заявку дальше не передать». Инструкция
    просит модель этого не говорить, но полагаться только на неё нельзя.

    Снимается лишь пересказ хвоста; реакция, добавляющая своё («понимаю, дело
    ваше»), остаётся. Нейтральный финал без контакта сам завершает ответ,
    дополнительная реплика перед ним не обязательна.
    """
    tail = " ".join(followup).casefold()
    if not without_contact and _NO_PHONE_TAIL_MARKER not in tail:
        return bubbles

    kept = [
        bubble
        for bubble in bubbles
        if not any(
            all(marker in bubble.casefold() for marker in markers)
            for markers in _NO_PHONE_ECHOES
        )
    ]
    # The neutral closing is sufficient when contact was not provided. Retaining
    # a removed obligation merely to fill a bubble would reintroduce pressure.
    return kept if without_contact else kept or bubbles[:1]


# Фазы, после которых говорить больше не о чем: анкета собрана, ссылка отдана
# или решения не нашлось. Ожидание согласия сюда НЕ входит — там бот ждёт от
# клиента ответа по существу.
_TERMINAL_PHASES = frozenset({"survey_completed", "no_fit", "link_sent"})

# Поддакивания и прощания. Всё, что человек пишет, когда разговор уже кончился.
_ACKNOWLEDGEMENTS = frozenset(
    {
        "ага", "угу", "да", "ну да", "ок", "окей", "хорошо", "ладно", "понял",
        "поняла", "понятно", "ясно", "спасибо", "спс", "благодарю", "пока",
        "до свидания", "всего доброго", "всего хорошего", "до связи", "принято",
        "отлично", "супер", "класс", "хорошего дня", "и вам", "взаимно",
    }
)
_ACK_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _previous_client_message(state: dict[str, Any]) -> str:
    for item in reversed(state.get("chat_history") or []):
        if item.get("role") == "user":
            return str(item.get("content") or "")
    return ""


def _empty_conversation_ack(message: str) -> bool:
    """An acknowledgement has no question, new value or substantive continuation."""
    text = " ".join((message or "").split())
    if not text or "?" in text or any(ch.isdigit() for ch in text):
        return False
    # An acknowledgement word does not make an attached emotion disposable.
    remainder = re.sub(r"[👍👌✅][\ufe0f\U0001f3fb-\U0001f3ff]*", "", text)
    if any(unicodedata.category(character).startswith("S") for character in remainder):
        return False
    words = [word.casefold() for word in _ACK_WORD_RE.findall(text)]
    if not words:
        # An affective emoji or punctuation is meaningful input. Only explicit
        # acknowledgement symbols share the quiet-tail lifecycle.
        return text.strip(". !\ufe0f\U0001f3fb\U0001f3fc\U0001f3fd\U0001f3fe\U0001f3ff") in {"👍", "👌", "✅"}
    return len(words) <= 3 and (
        " ".join(words) in _ACKNOWLEDGEMENTS
        or all(word in _ACKNOWLEDGEMENTS for word in words)
    )


def _last_conversation_pair(state: dict[str, Any]) -> tuple[str, str]:
    """Read the whole visible assistant turn and the request it answered."""
    replies: list[str] = []
    for item in reversed(state.get("chat_history") or []):
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "")
        if item.get("role") == "assistant":
            if content.strip():
                replies.append(content)
        elif item.get("role") == "user":
            if replies:
                return "\n\n".join(reversed(replies)), content
            # Quiet acknowledgements leave the last answered pair available.
            # A departure or new substantive message ends that adjacency.
            if client_is_saying_goodbye(content) or not _empty_conversation_ack(content):
                return "", ""
    return "\n\n".join(reversed(replies)), ""


def _conversation_farewell_tail(state: dict[str, Any]) -> bool:
    """Silent user acknowledgements do not erase a farewell already in history."""
    for item in reversed(state.get("chat_history") or []):
        if not isinstance(item, dict):
            continue
        text = str(item.get("content") or "")
        if item.get("role") == "assistant":
            if re.fullmatch(r"\s*(?:пожалуйста[!,. ]*)?(?:до\s+(?:свидания|связи)|"
                            r"хорошего\s+дня|всего\s+(?:доброго|хорошего))[.! ]*", text, re.I):
                return True
        elif item.get("role") == "user":
            if client_is_saying_goodbye(text):
                return True
            if not _empty_conversation_ack(text):
                return False
    return False


def conversation_is_over(state: dict[str, Any], user_message: str) -> bool:
    """Пора ли замолчать: разговор закончен, а клиент просто поддакивает.

    Ролевой прогон 19.08.2026: после финала клиент двенадцать раз ответил «Ага»
    и двенадцать раз получил новое прощание — «хорошего дня», «до связи»,
    «берегите себя». Живой человек так не прощается, а каждая такая реплика
    стоит вызова модели.

    Замолкаем только на пустых поддакиваниях. Вопрос, новые сведения и номер,
    который клиент передумал давать, по-прежнему получают ответ: разговор
    закончен для нас, но не обязательно для него.
    """
    reply, previous_request = _last_conversation_pair(state)
    acknowledged = _empty_conversation_ack(user_message)
    # Answering the visible question takes precedence over a retained terminal
    # phase or a courtesy bubble following that question. This grants no action.
    if (acknowledged and prompts.asks_something(reply)
            and (not client_is_saying_goodbye(user_message) or parse_bool(user_message) is True)):
        return False
    policy = debt_policy.parse_policy(state)
    policy_chat = (policy is not None and policy.mode == "debt") or onloader_policy.dialogue_active(state)
    # A complete FAQ answer needs no second availability invitation on "ага".
    # The pending questionnaire remains intact for a later volunteered fact.
    if (policy_chat and acknowledged and not client_is_saying_goodbye(user_message)
            and prompts.asks_something(previous_request) and not prompts.asks_something(reply)):
        return True
    # Ветка развилки своей конечной фазы не имеет: разговор в ней кончается
    # прощанием (ролевой прогон 11.09.2026 — четыре «Спасибо», четыре
    # прощания подряд).
    if (
        state.get("dialog_phase") not in _TERMINAL_PHASES
        and not entry_routing.branch_closed(state)
    ):
        # Посреди анкеты молчим только на второй уход подряд: на первый бот
        # уже попрощался. Ролевой прогон 04.10.2026 — на «ничего не надо»,
        # «ничего не нужно», «ничего не нужно» три одинаковых «буду на связи».
        if client_is_saying_goodbye(user_message) and client_is_saying_goodbye(
            _previous_client_message(state)
        ):
            return True
        debt_completed = policy is not None and policy.mode != "clarify" and state.get("dialog_phase") in {
            "collected_held", "contact_declined_held", "incomplete_held", "below_local_floor_held", "cancelled_held",
        }
        onloader = onloader_policy.read_state(state) if onloader_policy.dialogue_active(state) else None
        onloader_completed = onloader is not None and (
            onloader.status in {"queued", "declined", "rejected"} or onloader_policy.chat_declined(state)
        )
        if not debt_completed and not onloader_completed and not _conversation_farewell_tail(state):
            return False
    # The completion is conversational only. FAQ, fresh facts and decision actions remain usable.
    return acknowledged


# Слова, которые прощаются сами по себе, без пары. Подмножество
# `_ACKNOWLEDGEMENTS`: «ага» и «ок» посреди анкеты означают «продолжай», а
# «спасибо» и «пока» — «я ухожу». «Досвидания» слитно — тоже целая фраза одним
# токеном; с пробелом «до» и «свидания» распадаются на два слова и сюда не
# входят, см. `_FAREWELL_PHRASES` ниже.
_FAREWELL_STANDALONE_WORDS = frozenset(
    {"спасибо", "спс", "благодарю", "пока", "прощайте", "досвидания", "удачи"}
)

# Прощания, которые распознаются только парой соседних слов: «до» и «всего» —
# обрывки, а не прощание. Ревью 09.09.2026 поймало живьём: одиночное «До»
# (например, отправленное отдельным пузырём как начало «До какого числа...»)
# распознавалось как прощание, потому что «до» само по себе лежало в списке
# слов. То же верно для «свидания» и «доброго» — тоже половинки фразы.
_FAREWELL_PHRASES = frozenset({"до свидания", "всего доброго", "всего хорошего"})


# Отказ словами: «ничего не надо», «тогда ничего не нужно», «извините, не
# нужно, спасибо», «я передумала». Голые «не надо» и «не нужно» сюда не входят —
# так отвечают на вопрос анкеты («страховку не надо»), а не уходят.
# Бой 20.10.2026, сессия e79066285842: «Отказываюсь от кредита», «Мне не нужен
# кредит», «Кредит не нужен», «Мне деньги не нужны» — и восемь раз подряд
# «какая сумма вам нужна?». «Кредит не нужен» — тот же уход, что и «ничего не
# надо»; «нода» — частая опечатка «надо».
_NOT_NEEDED = r"не\s+(?:нужен|нужна|нужны|нужно|надо|нода)"
_WHAT_IS_NOT_NEEDED = r"(?:кредит\w*|деньг\w*|денег|займ\w*|заём|ничего|ничо|нечего)"
_REFUSAL_RE = re.compile(
    rf"\b(?:ничего|ничо|нечего)\s+(?:(?:мне|уже|больше)\s+)*{_NOT_NEEDED}\b"
    rf"|\b(?:уже|больше)\s+{_NOT_NEEDED}\b"
    rf"|\b{_NOT_NEEDED}\s+(?:спасибо|спс|благодарю)\b"
    rf"|\b(?:спасибо|спс|благодарю)\s+{_NOT_NEEDED}\b"
    rf"|\b{_WHAT_IS_NOT_NEEDED}\s+(?:(?:мне|уже|больше|вообще|совсем)\s+)*{_NOT_NEEDED}\b"
    rf"|\b{_NOT_NEEDED}\s+(?:(?:мне|уже|больше|вообще|совсем)\s+)*{_WHAT_IS_NOT_NEEDED}"
    r"|\bпередумал[аи]?\b"
    r"|\bотказываюсь\b"
)
# Только эти слова могут стоять в отказе. Любое другое («страховку», «залог»,
# «кроме», «списать») значит, что человек говорит о чём-то конкретном и
# разговор идёт: «кредит не нужен, нужно списать долги» — не уход.
_REFUSAL_WORDS = frozenset(
    {
        "ничего", "ничо", "нечего", "не", "надо", "нода", "нужно", "нужен",
        "нужна", "нужны", "уже", "больше", "мне", "вообще", "совсем",
        "спасибо", "спс", "благодарю", "извините", "простите", "нет", "тогда",
        "пока", "я", "передумал", "передумала", "передумали", "все", "ладно",
        "понятно", "ясно", "ок", "ну", "буду", "брать", "до", "свидания",
        "досвидания", "кредит", "кредита", "кредиты", "кредитов", "деньги",
        "денег", "займ", "займа", "займы", "заём", "отказываюсь", "от", "вы",
        "понимаете", "что", "же",
    }
)
_REFUSAL_MAX_WORDS = 7


def _is_refusal(words: list[str]) -> bool:
    """Клиент словами отказывается продолжать — уходит, а не отвечает."""
    if len(words) > _REFUSAL_MAX_WORDS:
        return False
    normalized = [word.replace("ё", "е") for word in words]
    if not all(word in _REFUSAL_WORDS for word in normalized):
        return False
    return bool(_REFUSAL_RE.search(" ".join(normalized)))


# «Отстань», «я не просила помощи», «не туда ткнула» — человек не уходит
# вежливо, он просит оставить его в покое. Список узкий: каждое выражение
# значит это само по себе, без догадок о тоне.
_DISMISSAL_RE = re.compile(
    r"\bотстан\w*|\bотвал\w*|\bотвяж\w*|\bотцеп\w*"
    r"|\bне\s+просил[аи]?\s+(?:помощи|помочь|помогать|вас|ничего|об\s+этом)\b"
    r"|\bне\s+туда\s+(?:ткнул|тыкнул|нажал|попал|заш[её]л|зашл|кликнул)\w*"
    r"|\b(?:ткнул|тыкнул|нажал|попал|заш[её]л|зашл|кликнул|открыл)\w*\s+(?:случайно|не\s+туда)\b"
    r"|\bслучайно\s+(?:ткнул|тыкнул|нажал|попал|заш[её]л|зашл|кликнул|открыл)\w*"
    r"|\b(?:иди|идите|пош[её]л|пошла|пошли|катись|катитесь)\b.{0,12}?"
    r"(?:\bна\s*х(?:у[йяю]\w*|ер\w*|рен\w*)?(?![а-яё])|\bв\s+ж\w*|\bлесом\b|\bк\s+ч[её]рт\w*|\bвон\b)"
    r"|\bна(?:хер|хрен|фиг|х)\b.{0,10}?\b(?:пош[её]л|пошла|пошли|иди|идите)\b"
)
# «Не интересует» — уход только целой репликой: «не интересует залог, хочу без
# залога» — разговор по делу.
_NOT_INTERESTED_RE = re.compile(
    r"^(?:(?:мне|меня|нас|это|уже|вообще|нет|спасибо)\s+)*"
    r"(?:не\s*интерес\w+|неинтересно)"
    r"(?:\s+(?:мне|меня|это|вообще|спасибо))*$"
)
_DISMISSAL_MAX_WORDS = 8


def client_is_dismissing(user_message: str) -> bool:
    """Клиент просит оставить его в покое или пишет, что попал сюда случайно."""
    text = " ".join((user_message or "").split()).casefold()
    if not text or "?" in text or any(ch.isdigit() for ch in text):
        return False
    words = _ACK_WORD_RE.findall(text)
    if not words or len(words) > _DISMISSAL_MAX_WORDS:
        return False
    return bool(_DISMISSAL_RE.search(text) or _NOT_INTERESTED_RE.match(" ".join(words)))


def client_declines(user_message: str) -> bool:
    """Клиент отказался от подбора: «кредит не нужен», «отстань», «не туда ткнула».

    Не просто прощание: на «спасибо, пока» человек уходит, но может вернуться
    к тому же разговору, а здесь он сказал, что разговор ему не нужен. Такой
    отказ помнится (`script.declined`), пока клиент сам не заговорит о деле.
    """
    text = " ".join((user_message or "").split())
    if not text or "?" in text or any(ch.isdigit() for ch in text):
        return False
    words = [word.casefold() for word in _ACK_WORD_RE.findall(text)]
    return bool(words) and (_is_refusal(words) or client_is_dismissing(text))


def _client_declined(state: dict[str, Any]) -> bool:
    return bool((state.get("script") or {}).get("declined"))


def _track_decline(
    old_state: dict[str, Any],
    new_state: dict[str, Any],
    user_message: str,
) -> None:
    """Запоминает отказ от подбора и снимает его, когда клиент вернулся к делу.

    Бой 20.10.2026, сессия e79066285842: «Отказываюсь от кредита», «мне не
    нужен кредит», «деньги не нужны» — а между отказами клиентка писала о
    списаниях с карты, и бот каждый раз заканчивал реплику вопросом «какая
    сумма вам нужна?». Прощание срабатывало только на саму реплику отказа.
    Возвращение к делу — любой новый факт анкеты: сумма, город, долг.
    """
    if not user_message:
        return
    script = new_state.setdefault("script", {})
    if client_declines(user_message):
        script["declined"] = True
    elif script.get("declined") and (new_state.get("facts") or {}) != (
        old_state.get("facts") or {}
    ):
        script.pop("declined", None)


def _leaving_instruction(user_message: str) -> str:
    """Прощание и просьба отстать звучат по-разному."""
    if client_is_dismissing(user_message):
        return prompts.DISMISSAL_INSTRUCTION
    return prompts.FAREWELL_INSTRUCTION


def client_is_saying_goodbye(user_message: str) -> bool:
    """Клиент прощается или отказывается, а не отвечает на вопрос.

    Боевая переписка 08.09.2026: на «Спасибо» и «До свидания» бот отвечал
    просьбой представиться. Распознаём так же строго, как поддакивания после
    финала: любой вопрос, любая цифра и любое лишнее слово означают, что
    разговор продолжается. Разбор 04.10.2026: на «ничего не надо» бот задавал
    следующий вопрос анкеты — отказ словами теперь тоже уход.
    """
    text = " ".join((user_message or "").split())
    if not text:
        return False
    if "?" in text or any(ch.isdigit() for ch in text):
        return False

    words = [word.casefold() for word in _ACK_WORD_RE.findall(text)]
    if not words:
        return False
    if _is_refusal(words) or client_is_dismissing(text):
        return True
    if len(words) > 3:
        return False

    bigrams = [f"{a} {b}" for a, b in zip(words, words[1:])]
    has_phrase = any(bigram in _FAREWELL_PHRASES for bigram in bigrams)
    # Слово из пары («до», «свидания», «всего», «доброго»/«хорошего») считается
    # закрытым, только если рядом с ним нашлась именно его пара — иначе оно
    # ничем не отличается от случайного слова вроде «Иван».
    paired_words = {
        word
        for a, b in zip(words, words[1:])
        if f"{a} {b}" in _FAREWELL_PHRASES
        for word in (a, b)
    }

    def is_covered(word: str) -> bool:
        return (
            word in _FAREWELL_STANDALONE_WORDS
            or word in _ACKNOWLEDGEMENTS
            or word in paired_words
        )

    if not all(is_covered(word) for word in words):
        return False
    return has_phrase or any(word in _FAREWELL_STANDALONE_WORDS for word in words)


def _site_consent_followup(state: dict[str, Any]) -> list[str]:
    """Ссылка на офер — один раз за диалог, детерминированным пузырём.

    Отметка о том, что ссылка ушла, живёт в состоянии сессии: повторная выдача
    того же адреса на каждый ход выглядит как заедание.
    """
    workflow = state.get("crm_workflow") or {}
    if workflow.get("status") != "awaiting_site_consent":
        return []
    url = workflow.get("offer_url")
    if not url or workflow.get("offer_link_sent"):
        return []
    workflow["offer_link_sent"] = True
    state["crm_workflow"] = workflow
    name = client_display_name(state.get("facts"))
    if client_is_on_page(state, url):
        return prompts.site_consent_same_page_message(name)
    return prompts.site_consent_link_message(name, str(url))


def client_is_on_page(state: dict[str, Any], url: object) -> bool:
    """Открыт ли чат на той самой странице, куда ведёт ссылка.

    Бой 18–20.10.2026: шестерым клиентам со страницы `/bfl-finish` бот прислал
    «подтвердите заявку кнопкой на этой странице» со ссылкой на ту же
    `/bfl-finish` — человек уже сидел на ней и не понимал, куда идти.
    Страница чата приходит из виджета (`attribution.landing`); «www.» и
    параметры адреса не различают страницы.
    """
    landing = str(((state.get("attribution") or {}).get("landing")) or "")
    if not landing or not url:
        return False
    here, there = urlsplit(landing), urlsplit(str(url))

    def host(parts: Any) -> str:
        return (parts.hostname or "").lower().removeprefix("www.")

    return host(here) == host(there) and here.path.rstrip("/") == there.path.rstrip("/")


def _delivery_followup(state: dict[str, Any]) -> list[str]:
    delivery_result = state.get("delivery") or {}
    if delivery_result.get("status") == "completed" and not delivery_result.get("sent"):
        delivery_result["sent"] = True
        state["delivery"] = delivery_result
        state["dialog_phase"] = "survey_completed"
        # Ветку выбирает ответ CRM, а не модель: она не видит ни категории,
        # ни ссылки, и обещать звонок менеджера ей нельзя.
        workflow = state.get("crm_workflow") or {}
        phone = ((state.get("facts") or {}).get("client") or {}).get("phone")
        return prompts.survey_completed_message(
            thanks_url=workflow.get("thanks_url"),
            has_phone=bool(isinstance(phone, str) and phone.strip()),
        )
    if delivery_result.get("status") != "link" or delivery_result.get("sent"):
        return []
    delivery_result["sent"] = True
    state["delivery"] = delivery_result
    state["dialog_phase"] = "link_sent"
    name = client_display_name(state.get("facts"))
    return prompts.link_message(name, str(delivery_result.get("url")))


def _dialog_phase(current_plan: Plan, delivery_result: dict[str, Any] | None) -> str:
    """Детерминированно переводит план и результат доставки в фазу диалога."""
    delivery_state = delivery_result or {}
    status = delivery_state.get("status")
    if status == "completed":
        return "survey_completed"
    if status == "link":
        return "link_sent" if delivery_state.get("sent") else "link_ready"
    if status == "no_fit":
        return "no_fit"
    if status == "error":
        return "delivery_error"
    if current_plan.action == "contacts":
        return "awaiting_contacts"
    if current_plan.action == "no_fit":
        return "no_fit"
    return "qualification"


def _plan_snapshot(current_plan: Plan) -> dict[str, Any]:
    """Сериализуемый след решения — для UI, логов и разбора «почему бот спросил это»."""
    return {
        "action": current_plan.action,
        "fields": current_plan.field_paths,
        # Номер шага анкеты вместо прежнего «маршрута, ради которого спрашиваем»:
        # при фиксированном сценарии такого маршрута нет, а шаг — то, по чему
        # считается отвал по вопросам.
        "step": current_plan.step,
        "matched": current_plan.matched.id if current_plan.matched else None,
        "routes": [
            {
                "id": status.id,
                "priority": status.priority,
                "state": status.state,
                "missing": status.missing,
                "failed": status.failed,
            }
            for status in current_plan.statuses
        ],
    }


# Сколько раз уточняем регион, прежде чем передать вопрос человеку. Порог нужен,
# чтобы диалог не мог зациклиться на клиенте, который не может назвать свою область.
_MAX_LOCATION_ATTEMPTS = 3


def _next_ask(current_plan: Plan) -> str:
    """Формулировки полей текущего шага — чтобы анкета не потеряла ход."""
    return "; ".join(item.ask for item in current_plan.fields)


def _units_confirmation_instruction(state: dict[str, Any]) -> str | None:
    """Голое число денежного поля без единицы — по образцу уточнения региона.

    Прод 20.09.2026, лид 3497115: клиент ответил «400 общая сумма» на вопрос
    про долг, бот записал 400 рублей. Домножать на тысячу за клиента нельзя,
    а поверить голому числу — соврать в лид, поэтому вместо факта в
    `script.units_pending` осталась пометка (`_split_bare_money_updates`), и
    бот переспрашивает коротко, не повторяя вопрос целиком.
    """
    pending = (state.get("script") or {}).get("units_pending")
    if not isinstance(pending, dict):
        return None
    path, raw = pending.get("path"), pending.get("raw")
    if not isinstance(path, str) or not isinstance(raw, int):
        return None
    matrix = get_matrix()
    label = matrix.fields[path].label if path in matrix.fields else path
    if pending.get("answered_other"):
        return (
            f"Клиент назвал «{label}» числом {raw} без единицы. Ты спросила, "
            "тысячи ли это, а он ответил про другое — этот его ответ уже "
            "записан. Сначала коротко, без вопроса, подтверди то, что он "
            "сказал сейчас. Потом одной фразой вернись к сумме: "
            f"«{_units_question(raw, repeated=True)}» Не говори, что сумму "
            "поняла или записала: она ещё не подтверждена. Больше ничего не "
            "спрашивай в этой реплике."
        )
    if pending.get("repeated"):
        return (
            f"Ты спросила про «{label}», тысячи это или миллионы, а клиент не "
            f"выбрал: снова назвал {raw} без единицы или просто согласился. "
            "Не повторяй прошлый вопрос слово в слово. Спроси одной фразой: "
            f"«{_units_question(raw, repeated=True)}» Больше ничего не "
            "спрашивай в этой реплике."
        )
    head = (
        f"Клиент назвал «{label}» голым числом {raw} — без рублей, тысяч или "
        "миллионов, и домножать за него нельзя. Переспроси коротко, не "
        f"повторяя вопрос целиком: «{_units_question(raw)}». "
    )
    if raw >= fastpath.UNITS_CHOICE_FROM:
        # Выбор из двух, а не догадка: просят и 5000 рублей, и «1300» в
        # смысле 1,3 млн. Вопрос «5 млн?» человеку с пятью тысячами звучит
        # насмешкой (разбор диалогов 04.10.2026).
        head += "Назови оба варианта именно так и не добавляй третьих. "
    else:
        head += "Называй только это число и не предлагай других вариантов. "
    return head + (
        "Больше ничего не спрашивай в этой реплике: следующий вопрос подождёт "
        "ответа."
    )


def _only_the_first_question(bubbles: list[str]) -> list[str]:
    """Обрезает реплику после первого вопроса; без вопроса — как есть."""
    for index, bubble in enumerate(bubbles):
        if "?" in bubble:
            head, _, _ = bubble.partition("?")
            return [*bubbles[:index], f"{head.strip()}?"]
    return bubbles


def _plural(number: int, one: str, few: str, many: str) -> str:
    tail = abs(number) % 100
    if 11 <= tail <= 14:
        return many
    tail %= 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def _units_question(raw: int, *, repeated: bool = False) -> str:
    """Переспрос единиц: «400 тысяч?», «5 тысяч или 5 миллионов?».

    До тысячи рубли не предлагаются: 400 рублей в сумме или долге — заведомо
    не то. Четыре цифры бывают и рублями, и тысячами тысяч (прод 30.09.2026,
    лид 3508342: «1300» — это 1,3 млн), поэтому звучат оба варианта. «1300
    тысяч» человек не скажет, отсюда миллионы; некруглое число в первом
    варианте остаётся рублями — «1,3 тысячи» тоже никто не говорит.
    """
    if raw < fastpath.UNITS_CHOICE_FROM:
        return f"А {raw} — это тысяч?" if repeated else f"{raw} тысяч?"
    whole, rest = divmod(raw, 1000)
    if rest == 0:
        small = f"{whole} {_plural(whole, 'тысяча', 'тысячи', 'тысяч')}"
        big = f"{whole} {_plural(whole, 'миллион', 'миллиона', 'миллионов')}"
    else:
        small = f"{raw} {_plural(raw, 'рубль', 'рубля', 'рублей')}"
        millions = f"{raw / 1000:.3f}".rstrip("0").rstrip(".").replace(".", ",")
        big = f"{millions} миллиона"
    if repeated:
        # Второй раз — рублями с обеих сторон: «1000 рублей» уже не спутать.
        rubles = f"{raw} {_plural(raw, 'рубль', 'рубля', 'рублей')}"
        return f"Уточню, чтобы не ошибиться: это {rubles} или {big} рублей?"
    return f"{small} или {big}?"


def _location_confirmation_instruction(
    state: dict[str, Any],
    current_plan: Plan,
) -> str | None:
    """Не отдаёт модели право угадывать вариант одноимённого города."""
    if current_plan.action != "ask":
        return None
    planned = set(current_plan.field_paths)
    scopes = (
        ("client.region_class", "client.location", "место проживания"),
        (
            "assets.realty.region_class",
            "assets.realty.location",
            "местонахождение недвижимости",
        ),
    )
    for field_path, location_path, label in scopes:
        location = _get_nested(state.get("facts", {}), location_path)
        if not isinstance(location, dict) or not location.get("confirmation_required"):
            continue
        # Обычно уточнение уместно только когда регион и есть вопрос хода. Но на
        # ходу, где мы сдаёмся, планировщик уже шагнул дальше по анкете — а сказать
        # про специалиста всё равно нужно, иначе клиент не поймёт, что произошло.
        if field_path not in planned and not location.get("handoff_required"):
            continue
        attempts = location.get("attempts")
        attempts = attempts if isinstance(attempts, int) else 0
        if attempts >= _MAX_LOCATION_ATTEMPTS:
            # Дальше спрашивать бессмысленно: клиент трижды ответил, а регион так и
            # не определился. Угадывать нельзя — он меняет маршрут, поэтому вопрос
            # уходит человеку. Заглушка: реальную передачу подключит платформа.
            following = (
                f"\nСледующий вопрос анкеты задай в том же сообщении: {_next_ask(current_plan)}."
                if current_plan.fields
                else ""
            )
            return (
                f"Регион для «{label}» выяснить не удалось: клиент отвечал несколько "
                "раз, но однозначно определить населённый пункт не получается.\n"
                "Больше НЕ спрашивай про регион и не перечисляй варианты. Спокойно "
                "скажи, что подключишь специалиста, который разберётся с адресом. "
                "Не обещай одобрение, сроки или конкретное решение и не называй "
                f"продукт. Не упоминай базу, систему, справочник или сервис.{following}"
            )
        repeated = attempts >= 1
        candidates = location.get("candidates")
        if isinstance(candidates, list) and candidates:
            labels = [
                str(candidate.get("display_name"))
                for candidate in candidates[:5]
                if isinstance(candidate, dict) and candidate.get("display_name")
            ]
            if labels:
                variants = "; ".join(labels)
                if repeated:
                    # Живой человек не зачитывает один и тот же список дважды.
                    # Второй заход звучит проще и даёт клиенту лёгкий способ ответить.
                    return (
                        f"Клиент уже отвечал про «{label}», но по ответу всё ещё "
                        f"нельзя понять регион. Варианты: {variants}.\n"
                        "Не зачитывай список заново и не повторяй прошлую формулировку. "
                        "Спокойно, без укора, попроси назвать область, край или "
                        "республику — и подскажи, что можно просто ответить «первый» "
                        "или «второй» по порядку вариантов выше. Не упоминай базу, "
                        "систему, справочник или сервис. Не выбирай вариант "
                        "самостоятельно и не задавай другие вопросы."
                    )
                return (
                    f"Для «{label}» возможны одноимённые населённые пункты: {variants}.\n"
                    "Коротко объясни это обычными словами и попроси клиента назвать "
                    "нужный регион. Не упоминай базу, систему, справочник или сервис. "
                    "Не выбирай вариант самостоятельно и не задавай другие вопросы."
                )
        # Название деревни человек может не помнить и не хотеть называть, а
        # для маршрута хватает области. Ролевой прогон 28.09.2026: бот держал
        # клиентку на названии деревни, дважды повторил одну фразу слово в
        # слово и прочитал нотацию «„под Воронежем“ — это направление, а не
        # название». Попытки теперь считаются и здесь (geo.enrich_locations).
        if repeated:
            return (
                f"Клиент уже отвечал про «{label}», но место так и не ясно.\n"
                "Не повторяй прошлую формулировку и не читай нотаций вроде "
                "«это направление, а не название». Если клиент спрашивает, "
                "зачем, — ответь одной короткой фразой: от места зависит, какие "
                "варианты доступны. Если не помнит или не хочет называть "
                "населённый пункт — скажи, что достаточно области или ближайшего "
                "города, и попроси только это. Не упоминай базу, систему, поле, "
                "справочник или сервис и не задавай другие вопросы."
            )
        return (
            f"Для «{label}» нужен населённый пункт вместе с областью, краем или "
            "республикой. Попроси клиента уточнить регион обычными словами; если "
            "точного названия он не знает, достаточно области или ближайшего "
            "города. Не упоминай базу, систему, поле, справочник или сервис и не "
            "задавай другие вопросы."
        )
    return None


def _get_nested(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


__all__ = [
    "apply_fact_updates",
    "build_runtime_payload",
    "commit_turn",
    "init_state",
    "prepare_turn",
    "stream_opening",
    "stream_reply",
    "turn_instruction",
]
