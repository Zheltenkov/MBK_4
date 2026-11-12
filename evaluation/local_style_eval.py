"""Bounded real-model dialogue evaluation through the core, with synthetic service boundaries."""
from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator
from contextlib import contextmanager, ExitStack
from copy import deepcopy
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
import hashlib
import json
import os
import re
from pathlib import Path
import time
from typing import Any, Literal
from unittest.mock import patch
from urllib.parse import urlsplit

import analytics
import core
import debt_policy
import llm_agent
import prompts
import policy_voice
import policy_voice_critic
import policy_semantic_analysis
import policy_semantics
import policy_factual_audit
from config import AppConfig, load_config
from evaluation.behavior import mikhail_legacy_score, v4_humanity_score
from evaluation.coherence import CoherenceTurn, dialog_coherence_score
from evaluation.personas import Persona, persona_by_id
from evaluation.roleplay import _client_reply, client_model
from geo.enrichment import _in_city_queries
from onloader.contracts import OnloaderOffer, OnloaderState
from pydantic import SecretStr
from pii_masking import mask_session_snapshot
from routing.normalize import classify_region_confident
from state import init_state
from usage_tracker import SessionUsage
from utils import set_by_path
from routing import get_fact

ROOT = Path(__file__).resolve().parents[1]
Json = dict[str, Any]
TONE_RUBRIC_VERSION = "natural-tone-v1"
RECEIPT_STREAK_LIMIT = 3
RECEIPT_PREFIX_LIMIT = 2
MEASUREMENT_CONTRACT_VERSION = "stage11-request-and-refusal-scope-v2"
CLIENT_STIMULUS_CONTRACT_VERSION = "served-question-act-client-only-v1"
CRITIC_TRACE_CONTRACT_VERSION = "distinct-semantic-critic-v1"
MODEL_DIAGNOSTIC_CONTRACT_VERSION = "private-safe-model-failure-v2-schema-codes"
SERVICE_BOUNDARY_VERSION = "local-geography-stored-city-parity-v2"


def local_evaluation_config(config: AppConfig) -> AppConfig:
    """Budget the whole diagnostic turn without shrinking F to the speaker's cap."""
    token_limit = policy_factual_audit.MAX_REPLY_TOKENS if config.policy_factual_audit_enabled else 650
    seconds_limit = 40 if config.policy_factual_audit_enabled else 30
    # Production model-role caps still constrain each request. A lower operator
    # limit remains binding, including when the local wrapper enables F first.
    return replace(config, debt_policy_enabled=True, enable_pii_masking=True,
                   max_tokens=min(config.max_tokens, token_limit),
                   request_timeout=min(config.request_timeout, seconds_limit),
                   fact_interpretation_mode="legacy")


def manual_review_contract() -> Json:
    """Keep semantic tone acceptance separate from the unchanged automatic gates."""
    return {
        "version": "stage11-status-and-intro-v1",
        "status": "pending",
        "required_reviewers": ["executor", "root", "independent_critic"],
        "scope": "all_eight_complete_dialogues_with_two_postterminal_turns",
        "criteria": {
            "unrequested_fact_status_receipts": "pending",
            "formal_intro_overpromise": "pending",
            "meaningful_current_turn_response": "pending",
            "emotion_confirmation_and_refusal_ack_preserved": "pending",
        },
        "automatic_contract_pass_does_not_accept_these_criteria": True,
        "customer_style_acceptance": "pending",
    }


@dataclass(frozen=True)
class StyleCase:
    id: str
    kind: Literal["normal", "debt", "onloader"]
    persona: Persona
    known_contact: bool = False
    detours: tuple["Detour", ...] = ()
    targeted: bool = False


@dataclass(frozen=True)
class Detour:
    id: str
    trigger: Literal["debt_known", "terminal", "phone_requested", "onloader_first", "onloader_explained"]
    message: str
    answer_groups: tuple[tuple[str, ...], ...] = ()


def cases() -> list[StyleCase]:
    return [
        StyleCase("normal_anxious", "normal", persona_by_id("anxious_overdue")),
        StyleCase("normal_skeptic", "normal", persona_by_id("unmasking_attempt")),
        StyleCase("debt_known_contact", "debt", Persona(
            id="debt_known_contact", description="Долги, контакт уже известен", story=
            "Ирина. Имя и телефон уже есть в анкете, повторять их не нужно. Вы хотите обсудить списание "
            "или реструктуризацию ваших долгов 400 тысяч рублей. Живёте в Казани. Вы тревожитесь, "
            "но хотите разобраться. Неизвестные условия не выдумывайте.", manner=
            "Отвечайте по одному вопросу. Один раз спросите, зачем уточнять сумму. Затем спросите, "
            "чем реструктуризация отличается от списания. Не давайте согласия на передачу.",
            expects_completion=False), True),
        StyleCase("debt_free_speech", "debt", Persona(
            id="debt_free_speech", description="Неуверенная сумма и вопросы о процессе", story=
            "Сергей из Москвы. Хотите разобраться со своими долгами: сначала говорите примерно "
            "250–300 тысяч, при уточнении сообщаете 280 тысяч рублей. На вопрос об имени — Сергей. "
            "Телефон оставлять не хотите. Про другие сведения говорите, что не знаете.", manner=
            "Пишите свободно и коротко. Спросите, можно ли обойтись без банкротства. На повторные "
            "вопросы отреагируйте естественно. Не выдумывайте новые факты.", expects_completion=False)),
        StyleCase("onloader_known_contact", "onloader", Persona(
            id="onloader_known_contact", description="Предложение в том же чате без повторного телефона", story=
            "Номер уже передан в текущей анкете. В чате показано предложение тестовой компании. "
            "Вы хотите понять, зачем нужен телефон повторно, кому уйдут данные и можно ли отказаться. "
            "Вы НЕ нажимали кнопок согласия и не хотите выполнять передачу в этом разговоре.", manner=
            "Спросите о повторном телефоне и компании. Потом ответьте коротко «да» на пояснение, "
            "это обычная реплика, а не разрешение. Попросите объяснить человеческими словами.",
            expects_completion=False), True),
    ]


def targeted_cases() -> list[StyleCase]:
    original = cases()
    return [
        replace(original[2], id="targeted_debt_detours", targeted=True, detours=(
            Detour("why_debt", "debt_known", "Я уже сказала сумму. Зачем вам её знать?", (("масштаб", "ситуац", "долг", "нагруз"),)),
            Detour("restructuring_difference", "terminal", "Чем реструктуризация отличается от списания?",
                   (("услов", "график", "платеж", "платёж", "срок"), ("спис", "банкрот", "освобожд"))),
        )),
        replace(original[3], id="targeted_phone_refusal", targeted=True, detours=(
            Detour("phone_no", "phone_requested", "Номер оставлять не хочу. Можно без него?", (("можно", "без", "не обяз", "вопрос"),)),
            Detour("faq_after_no", "terminal", "А можно пока просто объяснить, что такое реструктуризация?",
                   (("услов", "график", "платеж", "платёж", "срок"),)),
        )),
        replace(original[4], id="targeted_onloader_no", targeted=True, detours=(
            Detour("text_yes", "onloader_first", "Да. А кому именно уйдут данные?", (("тестов",),)),
            Detour("text_no", "onloader_explained", "Нет, не разрешаю передачу. Никаких кнопок нажимать не хочу."),
            Detour("faq_after_no", "terminal", "Можно просто пояснить, для чего нужны эти сведения?",
                   (("консультац", "связ", "обращен", "обращён", "заявк", "ситуац"),)),
        )),
    ]


# These gates intentionally do not import the runtime's speech validator or question observer.
# Agreement with an independent oracle matters more than reusing its implementation.
_COLLECTION = re.compile(r"\?|\b(?:подскажите|укажите|назовите|напишите|сообщите|оставьте|уточните)\b", re.I)
_FIELD_REQUESTS = {
    "client.phone": re.compile(r"телефон|номер|контакт.{0,60}связ|(?:остав|напиш).{0,25}контакт", re.I),
    "client.name": re.compile(r"как.{0,15}обращаться|как.{0,15}зовут|ваше\s+имя", re.I),
    "client.age": re.compile(r"сколько.{0,15}лет|ваш.{0,8}возраст", re.I),
    "client.region_class": re.compile(r"город|регион|где\s+(?:жив[её]те|находитесь)|пропис", re.I),
    "debts.total": re.compile(r"(?:какая|сколько|сумм|размер|всего|общая).{0,65}(?:долг|задолж)|(?:долг|задолж).{0,40}(?:сколько|какая|сумм)", re.I),
    "debts.overdue_duration": re.compile(r"просроч|сколько.{0,12}месяц.{0,18}(?:не\s+плат|задерж)", re.I),
    "debts.has_active_credits": re.compile(r"(?:есть|имеются).{0,30}(?:действующ|активн).{0,18}(?:кредит|займ)", re.I),
    "request.amount": re.compile(r"(?:сумм|сколько|размер).{0,50}(?:нужн|получить|рассчитыва|хотите\s+(?:взять|кредит))", re.I),
    "employment.income": re.compile(r"доход|зарплат|зарабатыва", re.I),
    "employment.type": re.compile(r"(?:вы\s+)?работаете|занятост|свой\s+бизнес", re.I),
    "assets.has_realty": re.compile(r"недвижим|квартир.{0,30}собствен|собствен.{0,30}квартир", re.I),
    "assets.has_vehicle": re.compile(r"машин|автомобил", re.I),
}
_NEGATION = re.compile(r"\b(?:не|ничего\s+не|никому\s+не|нельзя|невозможно)\s+(?:\w+\s+){0,2}$", re.I)
_ACTION = re.compile(r"\b(?:переда(?:ю|[её]м|л[аи]?)|отправ(?:ляю|ляем|ил[аи]?|лю|им)|передам|передадим)\b.{0,40}(?:данн|заявк|сведени|контакт)|(?:данн|заявк|сведени).{0,35}(?:передан|отправлен|доставлен|принят[ао]?\b)", re.I)
_RECORDED = re.compile(r"(?:согласие|разрешение|отказ|решение).{0,20}(?P<record_verb>сохран[её]н|получен|записан|зафиксирован|принял[аи]?)", re.I)
_PRIVACY_PROMISE = re.compile(r"(?:больше|кроме).{0,30}никто.{0,35}(?:доступ|увид|получ)|никто.{0,20}не\s+будет\s+звон|звонить.{0,35}не\s+будут", re.I)
_PHONE_PRESSURE = re.compile(r"без.{0,18}(?:номер|телефон).{0,80}(?:помочь\s+не\s+получится|не\s+смогу\s+помочь|помощь\s+невозможна|не\s+передать|не\s+продолжить|обязательн.{0,20}услов)|(?:номер|телефон).{0,25}обязательн.{0,15}услов", re.I)
_TRANSFER_NAG = re.compile(r"(?:разрешаете|согласны).{0,60}(?:переда|данн)|нажмите.{0,20}(?:[«\"]?да\b|разрешаю|подтверд)", re.I)
_ANAPHORIC_REQUEST = re.compile(r"\b(?:напишите|укажите|оставьте|сообщите|назовите|пришлите|напишете|укажете|оставите)\s+(?:мне\s+)?его\b", re.I)
_CHAT_CONTINUATION = re.compile(r"\bпродолжим\s+(?:прямо\s+)?здесь\b|\bпродолжим\s+(?:общение\s+)?в\s+чате\b", re.I)
_CONTACT_CONDITION = re.compile(r"(?:если|после\s+того\s+как|при\s+условии).{0,50}(?:номер|телефон|контакт)", re.I)
_TRANSFER_PRESSURE = re.compile(r"без\s+передачи\s+(?:данных|контактов|сведений).{0,50}(?:консультация\s+невозможна|не\s+(?:смогу|получится|продолжить))", re.I)
_CANCELLATION_CLAIM = re.compile(r"передачу\s+(?:отменя\w*|отменил\w*|остановил\w*)|(?:отменя\w*|отменил\w*|остановил\w*)\s+передачу", re.I)
_CONSENT_THANKS = re.compile(r"\b(?:спасибо|благодарю)\s+за\s+(?:ваше\s+)?(?:согласие|разрешение)\b", re.I)
_NO_REAL_CALLS = re.compile(r"никаких\s+(?:реальных\s+)?звонков.{0,50}не\s+будет", re.I)

# Tone criteria are independent of the runtime validator and the original scores.
# These finite patterns measure the previously observed pressure classes before refusal.
_CONTACT_WORD = re.compile(r"\b(?:номер\w*|телефон\w*)\b", re.I)
_PHONE_OBLIGATION = re.compile(r"\bобязател\w*|\bчтобы\s+(?:я\s+|мы\s+)?мог(?:ла|ли|у)\s+продолж|\bчтобы\s+продолж|без.{0,25}(?:номер\w*|телефон\w*|него).{0,85}\bне\s+(?:смогу|сможем|получится|выйдет|продолжить)", re.I)
_NARROW_CALLBACK = re.compile(r"обратн\w*\s+звон|перезвон|позвон|связаться", re.I)
_GENERAL_CONTINUATION = re.compile(r"продолж|решени|результат|помощ", re.I)
_APPLICATION_QUALIFIER = re.compile(r"для\s+(?:оформления|подачи|отправки)\s+заявки", re.I)
_EXPLICIT_CHAT_AVAILABLE = re.compile(r"\bвопросы\s+можно\s+(?:обсудить\s+)?здесь\b|\bможем\s+продолжить\s+(?:общение\s+)?здесь\b|\bобщаться\s+здесь\s+мы\s+можем\b", re.I)
_DECLARATIVE_PHONE_REQUEST = re.compile(r"\b(?:мне|нам)\s+нужен\s+ваш\s+(?:номер(?:\s+телефона)?|телефон)\b", re.I)
_APPLICATION_CONSTRAINT = re.compile(r"\b(?:анкету|заявку)\s+(?:оформить|подать|отправить)\s+не\s+(?:получится|выйдет)\s*$", re.I)
_ACK_CONFIRMATION_QUESTION = re.compile(r"^\s*(?:а\s+)?(?:вы\s+)?(?:уже\s+)?(?:учли|записали|зафиксировали)\b[^?]{0,70}\?", re.I)
_ACK_VERBS = frozenset({"приняла", "принял", "поняла", "понял", "зафиксировала", "зафиксировал",
                        "фиксирую", "записала", "записал", "учла", "учел", "учёл", "вижу", "увидела",
                        "ясен", "ясно", "понятно", "знаю", "записано", "записаны", "принято"})
_ACK_WORDS = _ACK_VERBS | frozenset({"спасибо", "хорошо", "отлично", "да", "ага", "я", "уже", "теперь",
    "всё", "все", "это", "ваш", "ваше", "ваши", "вашу", "имя", "сумма", "сумму", "суммы", "долг",
    "долга", "город", "города", "регион", "региону", "региона", "область", "номер", "телефон", "данные",
    "и", "эту", "этот", "эти", "данную", "у", "меня", "есть",
    "тысяч", "тысячи", "тыс", "рублей", "рубля", "руб", "миллион", "миллиона", "миллионов"})


def neutral_receipt_prefix_count(bubbles: list[str], message: str = "") -> int:
    """Count only a closed, content-free prefix; meaningful acknowledgement remains unrestricted."""
    count = 0
    supplied_words = set(re.findall(r"[а-яё]+|\d+", message.casefold()))
    for clause in re.split(r"(?<=[.!?])\s+|\n+", "\n".join(bubbles)):
        if not clause.strip():
            continue
        if "?" in clause:
            break
        raw_words = re.findall(r"[А-Яа-яЁё]+|\d+", clause)
        words = [word.casefold() for word in raw_words]
        if words and words[0] not in _ACK_WORDS and re.match(r"^[А-ЯЁ][а-яё]+,", clause.strip()):
            raw_words = raw_words[1:]
            words = words[1:]
        # An echoed capitalized city/name supplied by this user adds no new proposition.
        echoed_names = {raw.casefold() for raw in raw_words if raw[:1].isupper() and raw.casefold() in supplied_words}
        receipt = bool(words and all(word in _ACK_WORDS or word.isdigit() or word in echoed_names for word in words)
                       and (any(word in _ACK_VERBS for word in words)
                            or set(words).issubset({"спасибо", "хорошо", "отлично", "ага", "да"})))
        if not receipt:
            break
        count += 1
    return count


def _pre_refusal_phone_coercion(text: str, previous_reply: str) -> bool:
    """Scope necessary contact to its operation, without converting it into a prerequisite for chat."""
    contact_context = _CONTACT_WORD.search(text) or ("client.phone" in requested_fields(previous_reply))
    if not contact_context:
        return False
    chat_available = bool(_EXPLICIT_CHAT_AVAILABLE.search(text))
    for clause in re.split(r"(?<=[.!?])\s+|\n+", text):
        for match in _PHONE_OBLIGATION.finditer(clause):
            if _NEGATION.search(clause[:match.start()]):
                continue
            narrow_call_only = _NARROW_CALLBACK.search(clause) and not _GENERAL_CONTINUATION.search(clause)
            # Qualify this obligation itself, not an unrelated application mention nearby.
            qualified_obligation = re.match(r"обязател\w*\s+", clause[match.start():], re.I)
            application_only = chat_available and (
                bool(qualified_obligation and _APPLICATION_QUALIFIER.match(
                    clause[match.start() + qualified_obligation.end():]))
                or bool(re.fullmatch(r"\s*для\s+(?:оформления|подачи|отправки)\s+заявки\s+(?:номер|телефон)\s+",
                                     clause[:match.start()], re.I))
                or bool(_APPLICATION_CONSTRAINT.search(match.group()))
            )
            if not narrow_call_only and not application_only:
                return True
    return False


def natural_tone_contracts(message: str, bubbles: list[str], *, refused: bool,
                           prior_dialog: list[Json] | None = None, previous_reply: str = "") -> list[Json]:
    """Register finite pre-refusal and repetitive-receipt failures, preserving isolated natural language."""
    findings: list[Json] = []
    text = " ".join(bubbles)
    if not refused and _pre_refusal_phone_coercion(text, previous_reply):
        findings.append({"code": "phone_pressure_before_refusal", "field": "client.phone",
                         "severity": "hard", "evidence": text[:600]})
    closing_courtesy = re.fullmatch(r"\s*(?:спасибо|понятно|ага|до\s+свидания)[.! ]*", message, re.I)
    current = 0 if closing_courtesy else neutral_receipt_prefix_count(bubbles, message)
    # A single acknowledgement is natural. Repeated leading receipts turn intake into a protocol.
    if current >= RECEIPT_PREFIX_LIMIT:
        findings.append({"code": "duplicated_perfunctory_ack", "field": None,
                         "severity": "hard", "evidence": text[:600]})
    streak = int(current > 0 and not _ACK_CONFIRMATION_QUESTION.search(message))
    if streak:
        for row in reversed(prior_dialog or []):
            if (row.get("user") is None or not row.get("bot")
                    or _ACK_CONFIRMATION_QUESTION.search(row["user"])
                    or not neutral_receipt_prefix_count(row["bot"], row["user"])):
                break
            streak += 1
    if streak >= RECEIPT_STREAK_LIMIT:
        findings.append({"code": "repeated_perfunctory_ack", "field": None, "severity": "hard",
                         "evidence": text[:600], "consecutive_receipt_turns": streak})
    return findings


def requested_fields(text: str) -> set[str]:
    fields: set[str] = set()
    for clause in re.split(r"(?<=[.!?])\s+|\n+", text):
        if not _COLLECTION.search(clause):
            continue
        if re.match(r"^\s*(?:а\s+)?(?:зачем|почему|что\s+(?:значит|такое)|чем\s+отлича)", clause, re.I):
            continue
        if re.search(r"\bвы\s+спрашиваете\b|(?:объясню|поясню|расскажу).{0,35}(?:зачем|почему|как|что)", clause, re.I):
            continue
        # Reassurance about an already known contact is not an imperative to supply it.
        if re.search(r"(?:не\s+(?:нужно|надо|буду|спрашиваю|требуется)|необязательно).{0,30}(?:телефон|номер)|(?:телефон|номер).{0,30}(?:не\s+нуж|не\s+надо)", clause, re.I):
            clause = re.sub(r"[^?]*(?:телефон|номер)[^?]*(?=\?|$)", "", clause, flags=re.I)
        clause_fields = {path for path, pattern in _FIELD_REQUESTS.items() if pattern.search(clause)}
        if re.search(r"\b(?:вы\s+)?жив[её]те\s+в\b.{0,55}\bили\b", clause, re.I):
            clause_fields.add("client.region_class")
        property_scope = bool(re.search(r"квартир|недвижим|этот\s+дом|объект|этого\s+дома", clause, re.I))
        vehicle_scope = bool(re.search(r"машин|автомобил|авто\b", clause, re.I))
        if property_scope and "client.region_class" in clause_fields:
            clause_fields.discard("client.region_class")
            clause_fields.add("assets.realty.region_class")
        if vehicle_scope and re.search(r"залог|кредит|чистая", clause, re.I):
            clause_fields.discard("assets.has_vehicle")
            clause_fields.add("assets.vehicle.pledged")
        if property_scope and re.search(r"залог|ипотек", clause, re.I):
            clause_fields.discard("assets.has_realty")
            clause_fields.add("assets.realty.restriction")
        # Presence, ownership form, and owners are separate matrix attributes.
        if property_scope and re.search(r"частн.{0,25}собствен|муниципальн|форм.{0,20}собствен", clause, re.I):
            clause_fields.discard("assets.has_realty")
            clause_fields.add("assets.realty.ownership_form")
        if re.search(r"кто\s+(?:является\s+)?собственник|кто\s+владеет|другие\s+владельцы|супруг.{0,30}владел", clause, re.I):
            clause_fields.discard("assets.has_realty")
            clause_fields.add("assets.realty.owners")
        fields.update(clause_fields)
    # Resolve this polite anaphora only against an explicit phone antecedent in the same reply.
    if _CONTACT_WORD.search(text) and re.search(r"\bможете\s+оставить\s+его\b[^?]{0,45}\?", text, re.I):
        fields.add("client.phone")
    # A personalized need statement is also a request, without requiring a question mark.
    if _DECLARATIVE_PHONE_REQUEST.search(text):
        fields.add("client.phone")
    return fields


def _reference_settled_status(statuses: Json, path: str) -> bool:
    if path not in statuses:
        return True
    value = statuses[path]
    if isinstance(value, dict):
        value = value.get("status")
    return isinstance(value, str) and value in ("known", "confirmed")


def _known_fields(state: Json, case: StyleCase) -> set[str]:
    known = set()
    statuses = state.get("fact_statuses") or {}
    for path in (*_FIELD_REQUESTS, "assets.realty.region_class", "assets.realty.restriction", "assets.vehicle.pledged",
                 "assets.realty.ownership_form", "assets.realty.owners"):
        value = get_fact(state.get("facts") or {}, path)
        status = statuses.get(path)
        status = status.get("status") if isinstance(status, dict) else status
        if value is not None and value != "" and status not in {"unknown", "needs_confirmation", "ambiguous"}:
            known.add(path)
    # Confirmed self-ownership implies presence, without guessing a missing ownership form.
    facts = state.get("facts") or {}
    owners = get_fact(facts, "assets.realty.owners")
    if (get_fact(facts, "assets.has_realty") is None
            and all(_reference_settled_status(statuses, path) for path in
                    ("assets.has_realty", "assets.realty.ownership_form", "assets.realty.owners"))
            and "assets.realty.owners" in known
            and get_fact(facts, "assets.realty.ownership_form") in (None, "private")
            and (owners == "client_only" or owners == "client_and_spouse")):
        known.add("assets.has_realty")
    observation = (state.get("questionnaire_policy") or {}).get("amount_observation")
    if isinstance(observation, dict) and observation.get("kind") in {"range", "unknown"}:
        known.discard("debts.total")
    if case.known_contact:
        known.add("client.phone")
    return known


def phone_refused(message: str, previous_reply: str) -> bool:
    explicit = re.search(r"(?:не\s+хочу|не\s+буду|не\s+дам|не\s+оставлю|не\s+оставляю|без).{0,30}(?:телефон|номер)|(?:телефон|номер).{0,25}(?:не\s+дам|не\s+хочу|не\s+буду|не\s+оставляю)", message, re.I)
    contextual = "client.phone" in requested_fields(previous_reply) and bool(re.search(r"^\s*(?:нет|не\s+хочу|не\s+буду|не\s+оставлю)\b", message, re.I))
    return bool(explicit or contextual)


def update_known_ledger(known: set[str], state: Json, case: StyleCase, message: str) -> None:
    known.update(_known_fields(state, case))
    observation = (state.get("questionnaire_policy") or {}).get("amount_observation")
    correction = re.search(r"ошиб|поправ|неверн|неточн|уточн|точно\s+не\s+помню|не\s+уверен", message, re.I)
    numeric_range = re.search(r"\d[\d\s]*(?:тыс\w*)?\s*(?:[-–—]|до)\s*\d", message, re.I)
    if isinstance(observation, dict) and observation.get("kind") in {"range", "unknown"} and (correction or numeric_range):
        # The reference remembers confirmed facts across detours, but an explicit uncertainty reopens them.
        known.discard("debts.total")


def turn_contracts(case: StyleCase, state: Json, message: str, bubbles: list[str], *,
                   known: set[str], refused: bool, detour: Detour | None = None,
                   previous_reply: str = "", transfer_refused: bool = False,
                   prior_dialog: list[Json] | None = None) -> list[Json]:
    text = "\n".join(bubbles)
    fields = requested_fields(text)
    # Resolve an explicit follow-up pronoun only when the preceding request has one contact antecedent.
    if requested_fields(previous_reply) == {"client.phone"} and _ANAPHORIC_REQUEST.search(text):
        fields.add("client.phone")
    findings = natural_tone_contracts(message, bubbles, refused=refused, prior_dialog=prior_dialog,
                                      previous_reply=previous_reply)

    def fail(code: str, field: str | None = None) -> None:
        findings.append({"code": code, "field": field, "severity": "hard", "evidence": text[:600]})

    for field in sorted(fields & known):
        fail("known_fact_requested_again", field)
    if case.kind in {"debt", "onloader"}:
        allowed = set((state.get("plan") or {}).get("fields") or [])
        for field in sorted(fields - allowed):
            fail("unplanned_collection_field", field)
    blocking_contact = re.search(r"без\s+(?:номера|телефона)\s+(?:не\s+получится|никак|нельзя)", text, re.I)
    narrow_callback = re.search(r"связаться|позвонить|перезвон|обратн.{0,8}звон", text, re.I)
    if refused and ("client.phone" in fields or _PHONE_PRESSURE.search(text) or _CONTACT_CONDITION.search(text)
                    or (blocking_contact and not narrow_callback)):
        fail("phone_pressure_after_refusal", "client.phone")
    if transfer_refused and _TRANSFER_NAG.search(text):
        fail("consent_pressure_after_verbal_refusal")
    if transfer_refused and _TRANSFER_PRESSURE.search(text):
        fail("consent_pressure_after_verbal_refusal")
    for clause in re.split(r"[.!?;]\s*|\bно\b", text, flags=re.I):
        match = _ACTION.search(clause)
        receipt = (state.get("onloader") or {}).get("receipt_id")
        if match:
            verb = re.search(r"переда\w*|отправ\w*|достав\w*|принят\w*", match.group(), re.I)
            verb_start = match.start() + (verb.start() if verb is not None else 0)
            if not _NEGATION.search(clause[:verb_start]) and not receipt and not state.get("delivery"):
                fail("unsupported_delivery_claim")
        recorded = _RECORDED.search(clause)
        if recorded and not _NEGATION.search(clause[:recorded.start("record_verb")]) and (state.get("onloader") or {}).get("decision") is None:
            fail("unsupported_decision_recording_claim")
        if _CANCELLATION_CLAIM.search(clause) and (state.get("onloader") or {}).get("decision") is None:
            fail("unsupported_cancellation_claim")
        thanks = _CONSENT_THANKS.search(clause)
        if thanks and not _NEGATION.search(clause[:thanks.start()]) and (state.get("onloader") or {}).get("decision") is None:
            fail("unsupported_decision_recording_claim")
        if _PRIVACY_PROMISE.search(clause) or _NO_REAL_CALLS.search(clause):
            fail("unsupported_privacy_or_call_promise")
    if detour is not None:
        lowered = text.casefold().replace("ё", "е")
        for group in detour.answer_groups:
            # An explicit unconditional continuation in this chat answers "can we without a phone?".
            if (detour.id == "phone_no" and _CHAT_CONTINUATION.search(text)
                    and not _CONTACT_CONDITION.search(text) and not _PHONE_PRESSURE.search(text)
                    and "client.phone" not in fields):
                continue
            if not any(fragment.casefold().replace("ё", "е") in lowered for fragment in group):
                fail("controlled_question_unanswered")
                break
        if previous_reply and detour.answer_groups and SequenceMatcher(None, previous_reply.casefold(), text.casefold()).ratio() >= .85:
            fail("controlled_detour_repeated_previous_prompt")
    return findings


def refusal_state_contracts(case: StyleCase, state: Json, *, observed_refusal: bool) -> list[Json]:
    """Measure the committed application's refusal state separately from the user's intent."""
    if case.kind != "onloader" or not observed_refusal or core.onloader_policy.chat_declined(state):
        return []
    return [{"code": "contact_refusal_not_latched", "field": "onloader_dialogue.declined",
             "severity": "hard", "evidence": "Observed verbal refusal, but actual bound application preference is absent."}]


def recovered_name_question_contracts(bubbles: list[str], voice: Json) -> list[Json]:
    """Check the observed truncation boundary without treating internal metadata as a spoken question."""
    reason = str(voice.get("reason") or "")
    typed_or_recovered = reason == "model_question_recovered" or reason.endswith("_typed_question")
    if not typed_or_recovered or voice.get("question_field") != "client.name":
        return []
    if "client.name" in requested_fields("\n".join(bubbles)):
        return []
    return [{"code": "planned_question_missing_from_reply", "field": "client.name", "severity": "hard",
             "evidence": "A name-question recovery was recorded, but the served bubbles contain no name request."}]


def terminal_reason(case: StyleCase, state: Json, user_message: str) -> str | None:
    phase = str(state.get("dialog_phase") or "")
    if phase.endswith("_held") or phase in {"survey_completed", "no_fit", "link_sent"}:
        return phase
    if state.get("closed") is True or (state.get("entry") or {}).get("closed") is True:
        return "closed"
    if case.kind == "normal" and (state.get("plan") or {}).get("action") in {"route_found", "no_fit"}:
        return "intake_ready_without_delivery"
    hypothetical_query = re.fullmatch(r"\s*(?:а\s+)?если\b[^.!?]+\?\s*", user_message, re.I)
    if case.kind == "onloader" and not hypothetical_query and re.search(r"(?:не\s+хочу|не\s+разрешаю|не\s+буду).{0,30}(?:переда|нажим)|^\s*(?:нет|не\s+передавать)\s*[.!]?$", user_message, re.I):
        return "verbal_refusal_conversation_only"
    if re.fullmatch(r"\s*(?:спасибо|до\s+свидания|всего\s+доброго|ничего\s+не\s+нужно|на\s+этом\s+вс[её])\s*[.!]*\s*", user_message, re.I):
        return "user_departure"
    return None


def choose_detour(case: StyleCase, state: Json, previous_reply: str, used: set[str],
                  terminal: str | None, user_turns: int, *, served_question_field: str | None = None) -> Detour | None:
    for detour in case.detours:
        if detour.id in used:
            continue
        ready = {
            "debt_known": "debts.total" in _known_fields(state, case),
            "terminal": terminal is not None,
            "phone_requested": "client.phone" in requested_fields(previous_reply) or served_question_field == "client.phone",
            "onloader_first": user_turns == 0,
            "onloader_explained": user_turns >= 1,
        }[detour.trigger]
        if ready:
            return detour
    return None


def control_trigger_evidence(detour: Detour | None, previous_turn: Json) -> Json | None:
    """Producer metadata can drive the client, while reply correctness stays independently judged."""
    if detour is None or detour.trigger != "phone_requested":
        return None
    visible_reply = "\n".join(previous_turn["bot"])
    observed = "client.phone" in requested_fields(visible_reply)
    return {"source": "independent_text_observer" if observed else "served_question_act",
            "field": "client.phone", "served_question_field": previous_turn.get("voice_question_field"),
            "visible_reply": visible_reply, "independent_text_request_observed": observed,
            "manual_confirmation": "not_required" if observed else "pending",
            "automatic_coverage_certified": observed}


def _initial(case: StyleCase) -> Json:
    state = init_state()
    state["session_id"] = "synthetic-style-" + case.id
    if case.known_contact and case.kind == "debt":
        state["facts"].setdefault("client", {}).update(name="Ирина", phone="+70000000001")
    if case.kind == "debt":
        policy = debt_policy.select_policy({}, "Хочу списать свои долги", enabled=True)
        assert policy is not None
        state[debt_policy.POLICY_KEY] = policy.as_state()
        state = core.initialize_debt_questionnaire(state)
    elif case.kind == "onloader":
        now = time.time()
        binding = OnloaderState(status="awaiting_consent", binding_digest="a" * 64,
            operation_ref="synthetic-style-operation", delivery_handle=SecretStr("synthetic-opaque-contact-handle"),
            authorization_expires_at=now + 300, offer_expires_at=now + 300,
            offer=OnloaderOffer(offer_id="synthetic-offer", version="1", consent_version="local-v1",
                               partner_name="Тестовая компания"),
            presentation_id="synthetic-presentation", event_id="synthetic-event")
        state["onloader"] = binding.storage_value()
    return state


def local_geography(facts: Json, message: str, matrix: Any, *, asked_paths: list[str] | None = None) -> Json:
    """Resolve only local-catalogue classes, retaining property scope and extracted city facts."""
    result = deepcopy(facts)
    for scope in ("client", "assets.realty", "assets.realty_2"):
        path = scope + ".region_class"
        if get_fact(result, path) is not None:
            continue
        stored_city = get_fact(result, scope + ".city")
        queries = [stored_city, get_fact(result, scope + ".region")]
        if path in (asked_paths or []) and "?" not in message:
            queries.insert(0, message.strip())
        classes = set()
        for query in queries:
            if not isinstance(query, str) or not query.strip():
                continue
            forms = [query, *[canonical for canonical, _ in _in_city_queries(query.strip().rstrip(".!"))]]
            # Match the real adapter's bare-oblique fallback only for an extracted city.
            # A confirmation such as "Да, именно там" is never turned into a location.
            if query == stored_city:
                forms.extend(canonical for canonical, _ in _in_city_queries("в " + query.strip().rstrip(".!")))
            for form in forms:
                region = classify_region_confident(form, matrix, include_realty_list=scope.startswith("assets."))
                if region is not None:
                    classes.add(region)
        if len(classes) == 1:
            set_by_path(result, path, classes.pop())
    return result


@contextmanager
def service_boundaries(config: AppConfig, *, request_limit: int = 200, seconds_limit: int = 1200) -> Iterator[Json]:
    """Allow the configured LLM transport, while preventing every delivery/Geo service call."""
    if not 1 <= request_limit <= 500 or not 1 <= seconds_limit <= 1800:
        raise ValueError("Evaluation budget exceeds local bounds")
    origin = urlsplit(config.base_url)
    if origin.scheme != "https" or not origin.hostname:
        raise ValueError("An HTTPS model endpoint is required")
    allowed = origin.scheme, origin.hostname, origin.port or 443
    counters: Json = {"model_http_requests": 0, "model_transport_failures": 0, "delivery_attempts": 0,
                      "model_open_attempts": [], "attempt_count_scope": "logical_pool_requests_physical_retries_unknown"}
    started, actual_open = time.monotonic(), llm_agent._urlopen

    def model_open(settings: AppConfig, request: Any) -> Any:
        target = urlsplit(request.full_url)
        if (target.scheme, target.hostname, target.port or 443) != allowed:
            raise RuntimeError("Non-model endpoint blocked")
        if counters["model_http_requests"] >= request_limit or time.monotonic() - started >= seconds_limit:
            raise RuntimeError("Local model budget exhausted")
        counters["model_http_requests"] += 1
        # Record a logical opening before calling transport. Body reads remain untouched.
        attempt: Json = {"ordinal": counters["model_http_requests"], "model": settings.model,
                         "timeout_seconds": settings.request_timeout, "phase": "open", "outcome": "pending",
                         "http_status": None, "failure": None}
        counters["model_open_attempts"].append(attempt)
        opened_at = time.perf_counter()
        try:
            response = actual_open(settings, request)
            status = getattr(response, "status", None)
            attempt["http_status"] = status if type(status) is int and 100 <= status <= 599 else None
            attempt["outcome"] = "opened"
            return response
        except Exception as exc:
            counters["model_transport_failures"] += 1
            attempt["outcome"] = "failed"
            failure = llm_agent.model_failure(exc, phase="open")
            attempt["failure"] = _failure_trace(failure)
            attempt["http_status"] = failure.http_status
            raise
        finally:
            attempt["elapsed_ms"] = max(0, round((time.perf_counter() - opened_at) * 1000))

    def no_delivery(*args: Any, **kwargs: Any) -> Any:
        counters["delivery_attempts"] += 1
        raise RuntimeError("Delivery is forbidden in style evaluation")

    recorder, transcripts = analytics.get_recorder(), analytics.get_transcripts()
    analytics.set_recorder(None)
    analytics.set_transcripts(None)
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {"MBK_ROUTER_MOCK": "1", "MBK_CRM_LEAD_INTAKE_ENABLED": "0",
                                                       "MBK_CRM_WORKFLOW_ENABLED": "0"}))
            stack.enter_context(patch.object(core.geo, "enrich_locations", local_geography))
            stack.enter_context(patch.object(core.delivery, "submit", no_delivery))
            stack.enter_context(patch.object(llm_agent, "_urlopen", model_open))
            yield counters
    finally:
        analytics.set_recorder(recorder)
        analytics.set_transcripts(transcripts)


def _actor(persona: Persona, history: list[Json], config: AppConfig, usage: SessionUsage) -> str:
    actual_post = llm_agent._post

    def tracked(settings: AppConfig, payload: Json, **kwargs: Any) -> Json:
        response = actual_post(settings, payload, **kwargs)
        tokens = response.get("usage") or {}
        usage.add(str(payload["model"]), "client_actor", int(tokens.get("prompt_tokens") or 0),
                  int(tokens.get("completion_tokens") or 0))
        return response

    with patch.object(llm_agent, "_post", tracked):
        return _client_reply(persona, history, config)


def _failure_trace(failure: llm_agent.ModelFailure | None) -> Json | None:
    """Copy the closed runtime diagnostic fields without serializing provider exceptions."""
    if failure is None:
        return None
    return {"kind": failure.kind, "phase": failure.phase, "error_type": failure.error_type,
            "http_status": failure.http_status, "retryable": failure.retryable}


@dataclass(frozen=True)
class TurnExecution:
    state: Json
    prepared: Json
    bubbles: list[str]
    voice: Json
    latency: float


def execute_core_turn(state: Json, message: str, config: AppConfig, usage: SessionUsage) -> TurnExecution:
    """Match main's pre-model conversation guard and its user-only silent-history write."""
    started = time.perf_counter()
    if core.conversation_is_over(state, message):
        silent = deepcopy(state)
        silent.setdefault("chat_history", []).append({"role": "user", "content": message})
        return TurnExecution(silent, silent, [], {"source": "lifecycle_silence", "reason": "conversation_over",
                                                 "candidates": []}, time.perf_counter() - started)
    prepared = core.prepare_turn(state, message, config, usage_collector=usage.collector("extractor"),
                                 submit_delivery=False)
    candidates: list[str] = []
    assessments: list[Json] = []
    semantic_analyses: list[Json] = []
    actual_generate = llm_agent.generate_reply
    actual_assess = policy_voice_critic.assess
    actual_analyze = policy_semantic_analysis.analyze

    def observe_candidate(*args: Any, **kwargs: Any) -> str:
        candidate = actual_generate(*args, **kwargs)
        candidates.append(candidate)
        return candidate

    def record_assessment(assessment: policy_voice_critic.CriticAssessment) -> None:
        assessments.append({"model": assessment.model, "reason": assessment.reason,
                            "attempted": assessment.attempted,
                            "verdict": assessment.verdict.model_dump() if assessment.verdict is not None else None,
                            "failure": _failure_trace(assessment.failure), "elapsed_ms": assessment.elapsed_ms,
                            "schema_issues": [{"code": issue.code, "field": issue.field, "count": issue.count}
                                              for issue in assessment.schema_issues]})
    def observe_assessment(*args: Any, **kwargs: Any) -> policy_voice_critic.CriticAssessment:
        assessment = actual_assess(*args, **kwargs)
        record_assessment(assessment)
        return assessment

    def observe_analysis(*args: Any, **kwargs: Any) -> policy_semantic_analysis.SemanticAnalysis:
        result = actual_analyze(*args, **kwargs)
        record_assessment(result.assessment)
        decision = (policy_semantics.evaluate_semantics(result.authority, result.semantics)
                    if result.authority is not None else None)
        semantic_analyses.append({
            "user_valid": decision.user_valid if decision is not None else False,
            "claims_valid": decision.claims_valid if decision is not None else False,
            "user_error": result.semantics.user_error,
            "claims_error": result.semantics.claims_error,
            "need_error": result.semantics.need_error,
            "need_schema_issues": [{"code": issue.code, "field": issue.field, "count": issue.count}
                                   for issue in result.semantics.need_schema_issues],
            "code_content_issues": list(result.code_content_issues),
            "effective_verdict": result.effective_verdict.model_dump()
            if result.effective_verdict is not None else None,
            "issues": list(decision.issues) if decision is not None else ["context_unavailable"],
            "no_detected_violations": decision.safe if decision is not None else False,
            "applied_user_acts": [act.kind for act in decision.applied_user_acts] if decision is not None else [],
            "claim_predicates": [claim.predicate for claim in result.semantics.claims.claims]
            if result.semantics.claims is not None else [],
            "classification_accuracy_independently_verified": False,
        })
        return result

    with (patch.object(llm_agent, "generate_reply", observe_candidate),
          patch.object(policy_voice_critic, "assess", observe_assessment),
          patch.object(policy_semantic_analysis, "analyze", observe_analysis)):
        reply = "".join(core.stream_reply(prepared, message, config,
                                        usage_collector=usage.collector("conversation"),
                                        critic_usage_collector=usage.collector("policy_voice_critic"),
                                        factual_usage_collector=usage.collector("policy_factual_audit")))
    spoken = prepared.get("_policy_spoken")
    voice = {"source": getattr(spoken, "source", None), "reason": getattr(spoken, "reason", None),
             "question_field": getattr(spoken, "asked_field", None), "candidates": candidates,
             "critic_assessments": assessments, "critic_model": getattr(spoken, "critic_model", None),
             "semantic_analyses": semantic_analyses,
             "factual_assessments": [
                 {"verdict": item.verdict, "model": item.model, "reason": item.reason,
                  "wire_version": item.wire_version,
                  "attempted": item.attempted, "elapsed_ms": item.elapsed_ms,
                  "failure": _failure_trace(item.failure),
                  "rows": [row.model_dump(mode="json") for row in item.rows],
                  "source_catalog": [{"id": fact.id, "source_id": fact.source_id,
                                      "ordinal": fact.ordinal, "text": fact.text}
                                     for fact in item.source_catalog],
                  "schema_issues": [{"code": issue.code, "field": issue.field, "count": issue.count}
                                    for issue in item.schema_issues]}
                 for item in getattr(spoken, "factual_assessments", ())
             ],
             "diagnostic_assessments": [
                 {"code_content_issues": list(item.code_content_issues),
                  **({"code_style_issues": list(item.code_style_issues)}
                     if getattr(item, "code_style_issues", ()) else {}),
                  "effective_verdict": item.effective_verdict.model_dump()
                  if item.effective_verdict is not None else None}
                 for item in getattr(spoken, "diagnostic_assessments", ())
             ],
             "code_user_acts": list(getattr(spoken, "code_user_acts", ())),
             "critic_calls": getattr(spoken, "critic_calls", 0)}
    committed = core.commit_turn(prepared, message, reply)
    return TurnExecution(committed["current_state"], prepared, committed["messages"], voice,
                         time.perf_counter() - started)


def run_case(case: StyleCase, config: AppConfig, *, max_turns: int = 22,
             checkpoint: Callable[[Json], None] | None = None,
             stop_requested: Callable[[], bool] | None = None) -> Json:
    if not 1 <= max_turns <= 22:
        raise ValueError("Turn limit must be 1..22")
    state, turns, actor_history = _initial(case), [], []
    usage = SessionUsage("synthetic-style-" + case.id)
    opening_started = time.perf_counter()
    try:
        opening = list(core.stream_opening(state, config, usage_collector=usage.collector("opening")))
    except Exception as failure:
        return {"id": case.id, "kind": case.kind, "dialog": [], "error_class": type(failure).__name__,
                "coherence_score": None, "coherence_reasons": ["opening unavailable"], "mean_humanity": None,
                "usage": usage.summary(), "user_turns": 0, "delivery_present": False}
    opening_bubbles = prompts.split_bubbles("".join(opening))
    state["chat_history"] = [{"role": "assistant", "content": text} for text in opening_bubbles]
    actor_history.append({"role": "user", "content": "\n".join(opening_bubbles)})
    turns.append(_turn(None, opening_bubbles, time.perf_counter() - opening_started, state))
    error = None
    stopped = False
    actor_ended = False
    terminal = None
    terminal_turn = None
    post_terminal_turns = 0
    used_detours: set[str] = set()
    known = _known_fields(state, case)
    refused = False
    transfer_refused = False
    violations: list[Json] = []
    for _ in range(max_turns):
        if terminal is not None and post_terminal_turns >= 2:
            break
        if stop_requested is not None and stop_requested():
            stopped = True
            break
        try:
            previous_reply = "\n".join(turns[-1]["bot"])
            detour = choose_detour(case, state, previous_reply, used_detours, terminal, len(turns) - 1,
                                   served_question_field=turns[-1].get("voice_question_field"))
            trigger_evidence = control_trigger_evidence(detour, turns[-1])
            if detour is not None:
                message, message_source = detour.message, "controlled_detour"
                used_detours.add(detour.id)
            elif terminal is not None:
                message, message_source = ("Спасибо.", "Ага.")[post_terminal_turns], "postterminal_tail"
            else:
                message, message_source = _actor(case.persona, actor_history, config, usage), "stochastic_actor"
            if not message:
                actor_ended = True
                break
            was_terminal = terminal is not None
            refused = refused or phone_refused(message, previous_reply)
            if re.search(r"(?:передумал|теперь\s+хочу|вс[её][- ]таки\s+оставлю).{0,30}(?:телефон|номер)", message, re.I):
                refused = False
            if case.kind == "onloader" and terminal_reason(case, state, message) == "verbal_refusal_conversation_only":
                transfer_refused = True
            if re.search(r"(?:передумал|теперь\s+хочу).{0,30}переда", message, re.I):
                transfer_refused = False
            actor_history.append({"role": "assistant", "content": message})
            executed = execute_core_turn(state, message, config, usage)
            update_known_ledger(known, executed.prepared, case, message)
            state, bubbles = executed.state, executed.bubbles
            findings = turn_contracts(case, executed.prepared, message, bubbles, known=known, refused=refused,
                                      detour=detour, previous_reply=previous_reply, transfer_refused=transfer_refused,
                                      prior_dialog=turns)
            findings.extend(refusal_state_contracts(case, state, observed_refusal=transfer_refused))
            findings.extend(recovered_name_question_contracts(bubbles, executed.voice))
            for finding in findings:
                finding["turn"] = len(turns)
            violations.extend(findings)
            row = _turn(message, bubbles, executed.latency, state, voice=executed.voice)
            row.update(message_source=message_source, contract_id=detour.id if detour else None,
                       control_trigger_evidence=trigger_evidence,
                       rubric_failures=findings, known_fields=sorted(known), phone_refused=refused,
                       observed_transfer_refusal=transfer_refused,
                       neutral_receipt_prefixes=neutral_receipt_prefix_count(bubbles, message),
                       transfer_refused_conversationally=core.onloader_policy.chat_declined(state))
            turns.append(row)
            actor_history.append({"role": "user", "content": "\n".join(bubbles)})
            if was_terminal:
                post_terminal_turns += 1
            if terminal is None and (reason := terminal_reason(case, state, message)) is not None:
                terminal, terminal_turn = reason, len(turns) - 1
            if checkpoint is not None:
                checkpoint({"id": case.id, "kind": case.kind, "dialog": deepcopy(turns),
                            "user_turns": len(turns) - 1, "usage": usage.summary(), "execution_status": "running",
                            "terminal_reason": terminal, "post_terminal_turns": post_terminal_turns,
                            "rubric_failures": deepcopy(violations)})
        except Exception as failure:
            # The class is enough to classify transport failures. No provider body/key is saved.
            error = type(failure).__name__
            break
    coherence, reasons = dialog_coherence_score([CoherenceTurn(user=row["user"], bubbles=row["bot"]) for row in turns])
    safe_state, _ = mask_session_snapshot(state, None)
    fallback_count = sum(row["voice_source"] == "fallback" for row in turns)
    model_reasons = [str(row["voice_reason"] or "") for row in turns if row["voice_source"] == "model"]
    reviewed_count = sum(reason.startswith(("model_revised", "model_critic_accepted", "model_critic_revised"))
                         for reason in model_reasons)
    review_degraded_count = sum("_degraded" in reason for reason in model_reasons)
    mixed_question_count = sum(reason.endswith("_typed_question") or reason == "typed_question_recovered"
                               for reason in model_reasons)
    critic_model_rows = [row for row in turns if str(row.get("voice_reason") or "").startswith("model_critic_")]
    # A producer's success label alone cannot prove that the distinct model accepted the final body.
    critic_checks_complete = all(
        bool(row["semantic_critic_assessments"])
        and row["semantic_critic_assessments"][-1]["attempted"] is True
        and (row["semantic_critic_assessments"][-1].get("verdict") or {}).get("accept") is True
        and bool(row["semantic_critic_assessments"][-1].get("model"))
        and str(row["semantic_critic_assessments"][-1].get("model") or "").casefold() == (config.effective_policy_semantic_model or "").casefold()
        and str(row["semantic_critic_assessments"][-1].get("model") or "").casefold() != config.model.casefold()
        for row in critic_model_rows)
    critic_trace = [assessment for row in turns for assessment in row["semantic_critic_assessments"]]
    historical_review = any(reason.startswith(("model_critic_", "model_revised", "review_", "critic_unavailable_degraded"))
                            for reason in model_reasons)
    advisory = case.kind != "normal" and (any(reason.startswith("model_advisory_") for reason in model_reasons)
                                         or (policy_voice.STYLE_DIAGNOSTICS_ADVISORY and not historical_review))
    full_voice_review = (bool(model_reasons) and reviewed_count == len(model_reasons) and fallback_count == 0
                         and critic_checks_complete and mixed_question_count == 0
                         if case.kind != "normal" and not advisory else None)
    # Advisory verdicts are observations, never a style-acceptance certificate.
    voice_quality_eligible = None if advisory else full_voice_review is not False
    active_policy_rows = [row for row in turns if row["user"] is not None and row["bot"]]
    voice_hard_contracts_eligible = (
        bool(model_reasons) and all(row["voice_source"] == "model" and row.get("voice_candidates")
                                   for row in active_policy_rows)
        and fallback_count == 0 and mixed_question_count == 0
        if advisory else voice_quality_eligible)
    control_coverage_certified = all((row.get("control_trigger_evidence") or {}).get(
        "automatic_coverage_certified", True) for row in turns)
    completion_met = terminal is not None and post_terminal_turns >= 2
    missing_detours = [detour.id for detour in case.detours if detour.id not in used_detours]
    goal_completion_met = terminal is not None and terminal not in {"user_departure", "incomplete_held"}
    contract_passed = (completion_met and (goal_completion_met or not case.persona.expects_completion)
                       and terminal != "incomplete_held" and not violations and not missing_detours
                       and error is None and not stopped)
    return {"id": case.id, "kind": case.kind, "dialog": turns, "error_class": error,
            "targeted": case.targeted, "rubric_failures": violations,
            "contract_passed": contract_passed,
            "hard_contract_acceptance_eligible": bool(contract_passed and voice_hard_contracts_eligible
                                                     and control_coverage_certified),
            "completion_met": completion_met, "terminal_reason": terminal, "terminal_turn": terminal_turn,
            "goal_completion_met": goal_completion_met,
            "post_terminal_turns": post_terminal_turns, "missing_controlled_detours": missing_detours,
            "termination": "terminal_with_two_followups" if completion_met else
                           "actor_ended" if actor_ended else "turn_limit" if not stopped and error is None else "interrupted",
            "human_review_required": True,
            "control_coverage_automatically_certified": control_coverage_certified,
            "control_coverage_manual_review_required": not control_coverage_certified,
            "stopped_early": stopped, "voice_fallback_count": fallback_count,
            "voice_model_count": len(model_reasons), "voice_reviewed_count": reviewed_count,
            "voice_review_degraded_count": review_degraded_count, "voice_mixed_question_count": mixed_question_count,
            "semantic_critic_attempts": sum(item["attempted"] for item in critic_trace),
            "semantic_critic_accepted": sum((item.get("verdict") or {}).get("accept") is True for item in critic_trace),
            "semantic_critic_rejected": sum((item.get("verdict") or {}).get("accept") is False for item in critic_trace),
            "semantic_critic_unavailable": sum(item.get("verdict") is None for item in critic_trace),
            "voice_advisory_model_count": sum(reason.startswith("model_advisory_") for reason in model_reasons),
            "voice_advisory_disputed_count": sum(reason.startswith("model_advisory_disputed") for reason in model_reasons),
            "voice_advisory_unavailable_count": sum(reason.startswith("model_advisory_unavailable") for reason in model_reasons),
            "voice_hard_contracts_eligible": voice_hard_contracts_eligible,
            "voice_eligibility_scope": "hard_contracts_only_manual_style_pending" if advisory else
                                       "legacy_unannotated_hard_contracts" if case.kind == "normal" else "historical_voice_review",
            "full_voice_review_completed": full_voice_review, "voice_quality_eligible": voice_quality_eligible,
            "execution_status": "failed" if error else "stopped" if stopped else
                                "incomplete" if not completion_met or missing_detours else
                                "completed_with_fallbacks" if fallback_count else
                                "completed_with_review_degradation" if review_degraded_count else
                                "completed_without_full_voice_review" if voice_quality_eligible is False else
                                "completed_with_hard_voice_contract_failures" if not voice_hard_contracts_eligible else
                                "completed_pending_control_validation" if not control_coverage_certified else
                                "completed_advisory_manual_style_pending" if advisory else "completed",
            "coherence_score": coherence, "coherence_reasons": reasons,
            "mean_humanity": round(sum(row["humanity"] for row in turns) / len(turns), 2),
            "mean_humanity_nonempty": round(sum(row["humanity"] for row in turns if row["bot"]) /
                                             max(1, sum(bool(row["bot"]) for row in turns)), 2),
            "usage": usage.summary(), "final_phase": state.get("dialog_phase"),
            "final_policy": (state.get("questionnaire_policy") or {}).get("mode"),
            "onloader_decision": (state.get("onloader") or {}).get("decision"),
            "delivery_present": bool(state.get("delivery")),
            "final_facts": safe_state.get("facts", {}),
            "known_contact": case.known_contact, "user_turns": len(turns) - 1}


def _turn(user: str | None, bubbles: list[str], latency: float, state: Json, *, voice: Json | None = None) -> Json:
    humanity, humanity_reasons = v4_humanity_score(bubbles)
    legacy, legacy_reasons = mikhail_legacy_score(bubbles)
    voice = voice or {}
    policy = state.get("questionnaire_policy") or {}
    observation = policy.get("amount_observation")
    safe_observation = ({key: observation.get(key) for key in ("kind", "amount", "lower", "upper")}
                        if isinstance(observation, dict) else None)
    return {"user": user, "bot": bubbles, "latency_sec": round(latency, 3),
            "humanity": humanity, "humanity_reasons": humanity_reasons,
            "legacy_score": legacy, "legacy_reasons": legacy_reasons,
            "voice_source": voice.get("source"), "voice_reason": voice.get("reason"),
            "voice_candidates": voice.get("candidates", []),
            "semantic_critic_assessments": voice.get("critic_assessments", []),
            "semantic_analyses": voice.get("semantic_analyses", []),
            "effective_diagnostic_assessments": voice.get("diagnostic_assessments", []),
            "factual_audit_assessments": voice.get("factual_assessments", []),
            "voice_code_user_acts": voice.get("code_user_acts", []),
            "semantic_critic_model": voice.get("critic_model"),
            "semantic_critic_declared_calls": voice.get("critic_calls", 0),
            "voice_question_field": voice.get("question_field"),
            "phase": state.get("dialog_phase"), "asked": list((state.get("script") or {}).get("asked") or []),
            "planned_fields": list((state.get("plan") or {}).get("fields") or []),
            "phone_collection": policy.get("phone_collection"), "amount_observation": safe_observation,
            "application_offer_declined": core.onloader_policy.chat_declined(state),
            "debt_attempts": dict((state.get("debt_script") or {}).get("attempts") or {})}


def source_hashes() -> dict[str, str]:
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
        "core.py", "policy_voice.py", "prompts.py", "llm_agent.py", "debt_policy.py", "debt_workflow.py",
        "pii_masking.py", "config.py", "onloader/policy.py", "dialogue_frame.py", "state.py",
        "entry_routing.py", "routing/normalize.py", "routing/derive.py", "evaluation/personas.py",
        "routing/matrix.yaml",
        "geo/enrichment.py",
        "policy_voice_critic.py", "policy_content.py",
        "policy_grounded_voice.py", "policy_semantics.py", "policy_semantic_analysis.py", "policy_factual_audit.py",
        "main.py", "session_store.py",
        "transport.py", "utils.py",
        "evaluation/local_style_eval.py", "evaluation/roleplay.py", "evaluation/behavior.py", "evaluation/coherence.py")}


def _safe_case_accounting(case_id: str, result: Json) -> Json:
    """Keep paid-call accounting observable even when the subsequent disk checkpoint fails."""
    usage = result["usage"]
    return {"event": "case_model_accounting", "case_id": case_id,
            "checkpoint_status": "pending", "calls": usage["total_calls"],
            "estimated_cost_usd": usage["total_cost_usd"],
            "by_role": {role: {"calls": values["calls"], "estimated_cost_usd": values["cost_usd"]}
                        for role, values in usage["by_role"].items()}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--max-turns", type=int, default=22)
    parser.add_argument("--include-targeted", action="store_true")
    parser.add_argument("--label", default="current")
    parser.add_argument("--max-model-requests", type=int, default=200)
    parser.add_argument("--max-seconds", type=int, default=1200)
    parser.add_argument("--stop-file", type=Path, help="Stop before the next client turn when this file exists")
    options = parser.parse_args()
    if options.out.exists():
        parser.error("Use a new output path to preserve the earlier run")
    selected = cases() + targeted_cases() if options.include_targeted or options.ids else cases()
    if options.ids:
        requested = set(options.ids)
        selected = [case for case in selected if case.id in requested]
        if requested != {case.id for case in selected}:
            parser.error("Unknown local style case")
    if not 12 <= options.max_turns <= 22:
        parser.error("--max-turns must be 12..22 for terminal dialogue evaluation")
    config = local_evaluation_config(load_config())
    before = source_hashes()
    options.out.parent.mkdir(parents=True, exist_ok=True)
    results: list[Json] = []
    stop_path = options.stop_file or options.out.with_suffix(".stop")

    def save_progress(rows: list[Json], status: str) -> None:
        temporary = options.out.with_suffix(options.out.suffix + ".tmp")
        temporary.write_text(json.dumps({"version": 1, "label": options.label, "execution_status": status,
                              "source_sha256": before, "cases": rows, "speech_model": config.model,
                              "speech_model_real": True, "extractor_model_real": True,
                              "manual_review_contract": manual_review_contract(),
                              "measurement_contract_version": MEASUREMENT_CONTRACT_VERSION,
                              "client_stimulus_contract_version": CLIENT_STIMULUS_CONTRACT_VERSION,
                              "critic_trace_contract_version": CRITIC_TRACE_CONTRACT_VERSION,
                              "model_diagnostic_contract_version": MODEL_DIAGNOSTIC_CONTRACT_VERSION,
                              "semantic_diagnostics_mode": "advisory" if policy_voice.STYLE_DIAGNOSTICS_ADVISORY else "strict",
                              "service_boundary_version": SERVICE_BOUNDARY_VERSION,
                              "service_boundary_synthetic": True}, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(options.out)

    with service_boundaries(config, request_limit=options.max_model_requests, seconds_limit=options.max_seconds) as boundary:
        for case in selected:
            if stop_path.exists():
                break
            result = run_case(case, config, max_turns=options.max_turns, stop_requested=stop_path.exists,
                              checkpoint=lambda partial: save_progress([*results, partial], "running"))
            results.append(result)
            print(json.dumps(_safe_case_accounting(case.id, result)), flush=True)
            save_progress(results, "running")
    report = {"version": 1, "label": options.label, "speech_model": config.model,
              "extractor_model": config.extractor_model or config.model, "client_model": client_model(),
              "speech_model_real": True, "extractor_model_real": True, "service_boundary_synthetic": True,
              "service_boundary_version": SERVICE_BOUNDARY_VERSION,
              "transport_requests": boundary, "source_sha256": before, "source_unchanged": before == source_hashes(),
              "max_turns": options.max_turns, "cases": results,
              "rubric_version": "dialogue-state-contracts-and-natural-tone-v2", "human_review_required": True,
              "manual_review_contract": manual_review_contract(),
              "measurement_contract_version": MEASUREMENT_CONTRACT_VERSION,
              "client_stimulus_contract_version": CLIENT_STIMULUS_CONTRACT_VERSION,
              "critic_trace_contract_version": CRITIC_TRACE_CONTRACT_VERSION,
              "model_diagnostic_contract_version": MODEL_DIAGNOSTIC_CONTRACT_VERSION,
              "semantic_diagnostics_mode": "advisory" if policy_voice.STYLE_DIAGNOSTICS_ADVISORY else "strict",
            "policy_voice_pipeline": {"speaker_model": config.model,
                                      "critic_model": config.effective_policy_semantic_model if config.policy_semantic_enabled else config.extractor_model,
                                        "maximum_speech_calls": 2, "maximum_critic_calls": 2,
                                        "semantic_authority": "advisory" if policy_voice.STYLE_DIAGNOSTICS_ADVISORY else "strict",
                                        "critic_verdicts_are_not_customer_replies": True},
              "tone_rubric": {"version": TONE_RUBRIC_VERSION,
                               "receipt_streak_limit": RECEIPT_STREAK_LIMIT,
                               "receipt_prefix_limit": RECEIPT_PREFIX_LIMIT,
                               "single_acknowledgement_allowed": True,
                               "original_style_scores_unchanged": True},
              "hard_contract_acceptance_eligible": bool(results) and len(results) == len(selected)
                  and all(row.get("hard_contract_acceptance_eligible", False) for row in results),
              "quality_acceptance_eligible": "advisory" not in policy_voice.VERSION and bool(results) and len(results) == len(selected)
                  and all(row.get("contract_passed") and row.get("voice_quality_eligible")
                          and row.get("control_coverage_automatically_certified", True) for row in results),
              "quality_acceptance_eligibility_scope": "manual_style_pending" if policy_voice.STYLE_DIAGNOSTICS_ADVISORY
                                                      else "historical_voice_review",
              "execution_status": "stopped" if stop_path.exists() else
                                  "completed_with_contract_failures" if any(not row.get("contract_passed") for row in results) else
                                  "completed_with_review_degradation" if any(row.get("voice_quality_eligible") is False for row in results)
                                  else "completed_pending_control_validation" if any(
                                      row.get("control_coverage_manual_review_required") for row in results)
                                  else "completed_with_hard_voice_contract_failures" if any(
                                      row.get("voice_hard_contracts_eligible") is False for row in results)
                                  else "completed_advisory_manual_style_pending" if policy_voice.STYLE_DIAGNOSTICS_ADVISORY
                                  else "completed",
              "limitations": ["core-level evaluation; no browser or HTTP/session ownership proof",
                              "synthetic clients and service boundaries; no production calls",
                              "heuristic style scores require a blind human reading",
                              "state-aware pattern gates have bounded language coverage; contract pass is not human acceptance",
                              "actor cost uses configured local price table; provider billing may differ"]}
    options.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"cases": len(results), "errors": sum(bool(row["error_class"]) for row in results),
                      "requests": boundary["model_http_requests"], "source_unchanged": report["source_unchanged"]}))
    return int(not report["source_unchanged"] or boundary["delivery_attempts"] > 0 or stop_path.exists()
               or not report["hard_contract_acceptance_eligible"])


if __name__ == "__main__":
    raise SystemExit(main())
