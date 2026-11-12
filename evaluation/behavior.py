"""Behavioral regression для разговорного слоя MBK v4.

Routing correctness проверяется unit-тестами. Этот модуль проверяет другой контракт:
реальный ответ модели должен оставаться коротким, не раскрывать внутреннюю кухню,
задавать уместный вопрос и корректно вести terminal phases.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

import core
import llm_agent
import prompts
from config import AppConfig, load_config
from evaluation.coherence import CoherenceTurn, dialog_coherence_score
from routing import get_matrix, plan
from state import init_state


GLOBAL_FORBIDDEN = (
    "матриц",
    "маршрут",
    "приоритет",
    "json",
    "точно одобр",
    "гарантированно одобр",
    "100% одобр",
)

# Эти списки и веса намеренно повторяют исходную эвристику MBK v3.
# Благодаря этому новый результат можно честно сравнивать с эталоном Михаила 94.1/100.
MIKHAIL_BOT_PHRASES = (
    "понял",
    "понятно",
    "спасибо, это важная",
    "это важная деталь",
    "следующий шаг",
    "чтобы двигаться дальше",
    "для корректного подбора",
    "зафиксировал",
    "фиксирую",
    "к этому уже не возвращаюсь",
    "по вашему профилю логично",
    "это уже важная развилка",
    "хорошего дня",
)
MIKHAIL_JARGON = (
    "route",
    "scenario",
    "pipeline",
    "slot",
    "сценарий",
    "маршрут",
    "пайплайн",
    "слот",
    "квалификаци",
    "product_fit",
    "red flag",
    "красный флаг",
    "messages",
    "dialog_phase",
    "fact_update",
    "handoff",
)
MIKHAIL_CONCRETE_HINTS = (
    "примерно",
    "в районе",
    "около",
    "₽",
    "тыс",
    "млн",
    "%",
    "псб",
    "сбер",
    "втб",
    "озон",
    "ставк",
    "скоринг",
    "нбки",
    "окб",
)

# В v4 продукт и внутреннее решение намеренно скрыты до финальной ссылки. Поэтому
# отдельная продуктовая шкала не требует от ответа названий банков, ставок и скоринга.
V4_CANNED_PHRASES = (
    "понял",
    "понятно",
    "отлично, с этим уже можно работать",
    "без этих вводных",
    "рабочего варианта",
    "по выбранному направлению",
    "чтобы двигаться дальше",
    "для корректного подбора",
    "зафиксировал",
    "зафиксирую",
    "фиксирую",
    # Квитирование каждого ответа — главная жалоба заказчика на машинный тон.
    # В анкете полтора десятка вопросов, и «записал» на каждом превращает
    # разговор в протокол.
    "записал",
    "принял",
    "система сама",
    "это важная деталь",
    "следующий шаг",
)


class HistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    content: str


class BehaviorCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    facts: dict[str, Any]
    history: list[HistoryMessage] = Field(default_factory=list)
    latest_user_message: str
    delivery: dict[str, Any] | None = None
    phase: str | None = None
    expected_action: str
    expected_fields: list[str] = Field(default_factory=list)
    required_any: list[str] = Field(default_factory=list)
    forbidden_any: list[str] = Field(default_factory=list)
    max_questions: int = 2
    # Порог связности с учётом history кейса. None — метрика только пишется в
    # артефакт, не валит кейс: пороги выставляем адресно, по мере накопления
    # эталонных прогонов.
    min_coherence_score: int | None = Field(default=None, ge=0, le=100)


@dataclass(frozen=True)
class BehaviorResult:
    case_id: str
    passed: bool
    raw_response: str
    response: str
    bubbles: list[str]
    failures: list[str]
    mikhail_legacy_score: int
    mikhail_legacy_reasons: list[str]
    v4_humanity_score: int
    v4_humanity_reasons: list[str]
    dialog_coherence_score: int
    dialog_coherence_reasons: list[str]
    latency_sec: float


def load_cases(path: Path) -> list[BehaviorCase]:
    """Загружает и строго валидирует eval cases."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("behavior cases должны быть JSON-массивом")
    return [BehaviorCase.model_validate(item) for item in raw]


def prepare_case(case: BehaviorCase) -> tuple[dict[str, Any], Any]:
    """Собирает runtime state без вызова extractor — факты кейса уже эталонные."""
    current_plan = plan(get_matrix(), case.facts)
    state = init_state()
    state["facts"] = case.facts
    state["chat_history"] = [message.model_dump() for message in case.history]
    state["delivery"] = case.delivery
    state["dialog_phase"] = case.phase or _phase_for_case(current_plan.action)
    state["_plan_object"] = current_plan
    return state, current_plan


def _phase_for_case(action: str) -> str:
    if action == "contacts":
        return "awaiting_contacts"
    if action == "no_fit":
        return "no_fit"
    return "qualification"


def case_coherence_turns(
    case: BehaviorCase,
    bubbles: list[str],
) -> list[CoherenceTurn]:
    """Восстанавливает разговор кейса как последовательность ходов.

    Behavior eval генерирует один ответ, но history кейса содержит то, что
    клиент говорил раньше. Без неё тональные дефекты этому harness не видны:
    беда упоминается на первом ходу, а неуместная реакция приходит на третьем.
    """
    turns: list[CoherenceTurn] = []
    pending: str | None = None
    for message in case.history:
        if message.role == "user":
            pending = message.content
        elif pending is not None:
            turns.append(CoherenceTurn(user=pending, bubbles=[message.content]))
            pending = None
    turns.append(CoherenceTurn(user=case.latest_user_message, bubbles=bubbles))
    return turns


def evaluate_response(case: BehaviorCase, response: str) -> tuple[list[str], list[str]]:
    """Детерминированные hard checks; тон дополнительно смотрит человек/LLM-judge."""
    bubbles = prompts.split_bubbles(response)
    failures: list[str] = []
    lowered = response.lower()

    if not 1 <= len(bubbles) <= 3:
        failures.append(f"bubble_count={len(bubbles)}, expected 1..3")
    question_count = response.count("?")
    if question_count > case.max_questions:
        failures.append(f"question_count={question_count}, max={case.max_questions}")
    if any(len(bubble) > 360 for bubble in bubbles):
        failures.append("есть пузырь длиннее 360 символов")

    for term in (*GLOBAL_FORBIDDEN, *case.forbidden_any):
        if term.lower() in lowered:
            failures.append(f"запрещённая формулировка: {term}")
    if case.required_any and not any(term.lower() in lowered for term in case.required_any):
        failures.append("нет ожидаемой смысловой опоры: " + " | ".join(case.required_any))
    if re.search(r"\b(?:поле|статус)\s+[a-z_]+(?:\.[a-z_]+)+", lowered):
        failures.append("наружу вышло служебное имя поля")
    return bubbles, failures


def mikhail_legacy_score(bubbles: list[str]) -> tuple[int, list[str]]:
    """Возвращает исходный 0..100 score из v3 без изменения весов.

    Это comparator, а не единственный критерий v4: старый тест поощрял конкретные
    продукты/ставки, которые новый контракт запрещает показывать до ссылки.
    """
    if not bubbles:
        return 0, ["пустой ответ"]

    reasons: list[str] = []
    score = 100
    text = " ".join(bubbles)
    lowered = text.lower()

    if len(bubbles) > 4:
        score -= 15
        reasons.append(f"слишком много пузырей ({len(bubbles)}>4)")

    longest = max(len(bubble) for bubble in bubbles)
    if longest > 280:
        score -= 25
        reasons.append(f"есть пузырь-простыня ({longest} символов)")
    elif longest > 200:
        score -= 10
        reasons.append(f"длинноватый пузырь ({longest})")

    for phrase in MIKHAIL_BOT_PHRASES:
        if phrase in lowered:
            score -= 12
            reasons.append(f"ботовая фраза: «{phrase}»")

    for term in MIKHAIL_JARGON:
        if re.search(rf"(?<![а-яёa-z]){re.escape(term)}", lowered):
            score -= 20
            reasons.append(f"внутренний термин наружу: «{term}»")

    question_count = text.count("?")
    if question_count > 3:
        score -= 10
        reasons.append(f"слишком много вопросов ({question_count})")

    if (
        not any(hint in lowered for hint in MIKHAIL_CONCRETE_HINTS)
        and len(text) > 40
    ):
        score -= 6
        reasons.append("нет конкретики (цифр/ориентиров/названий)")

    return max(0, min(100, score)), reasons


# Что может стоять перед квитанцией, не делая её содержательной: обращение по
# имени, местоимение, короткое согласие. «Павел, понял.» — та же квитанция.
_RECEIPT_LEAD_IN = re.compile(
    r"^(?:[А-ЯЁ][а-яё]{2,}\s*,\s*|(?:я|мы|всё|все|ага|да|ок|хорошо|отлично|ясно|спасибо)\b[\s,]+)+",
    re.IGNORECASE,
)


# Насколько глубоко в реплику может залезть квитанция. «Сумму зафиксировал» —
# два слова, «тип жилья я понял, но мне нужно регион» — четыре, и уже несёт
# содержание. Граница проходит между ними.
_RECEIPT_DEPTH = 3


def _opens_a_bubble(bubbles: list[str], phrase: str) -> bool:
    """Квитанция ли это, или слово внутри содержательной фразы.

    Запрет писался против пузыря-квитанции («Понял. Чтобы двигаться дальше…»):
    в анкете полтора десятка вопросов, и приём каждого ответа превращает
    разговор в протокол. Срабатывал он подстрокой и поэтому наказывал за любое
    употребление слова — включая «тип жилья я понял, но мне нужно регион», где
    бот как раз признаёт уже известное, то есть делает то, чего мы добиваемся.

    Различаем по позиции и длине: квитанция коротка и стоит в начале. Проверка
    по одному лишь началу пузыря оказалась слишком мягкой — она освобождала
    «Сумму зафиксировал.», а это ровно та квитанция, на которую жаловался
    заказчик.
    """
    for bubble in bubbles:
        head = bubble.strip().lstrip("—–- ")
        # Сравниваем и с самим началом, и с началом после зачина: регэксп зачина
        # не отличает «Павел,» от «Зафиксировал,» — оба выглядят как слово с
        # заглавной и запятой. Проверка обоих вариантов снимает неоднозначность.
        for opening in (head, _RECEIPT_LEAD_IN.sub("", head)):
            words = opening.casefold().split()
            for start in range(min(_RECEIPT_DEPTH, len(words))):
                if " ".join(words[start:]).startswith(phrase):
                    return True
    return False


def v4_humanity_score(bubbles: list[str]) -> tuple[int, list[str]]:
    """Оценивает разговорность в рамках нового продуктового контракта MBK v4."""
    if not bubbles:
        return 0, ["пустой ответ"]

    reasons: list[str] = []
    score = 100
    text = " ".join(bubbles)
    lowered = text.lower()

    if len(bubbles) > 3:
        score -= 15
        reasons.append(f"слишком много пузырей ({len(bubbles)}>3)")

    longest = max(len(bubble) for bubble in bubbles)
    if longest > 300:
        score -= 25
        reasons.append(f"есть пузырь-простыня ({longest} символов)")
    elif longest > 220:
        score -= 10
        reasons.append(f"длинноватый пузырь ({longest})")

    for phrase in V4_CANNED_PHRASES:
        if _opens_a_bubble(bubbles, phrase):
            score -= 10
            reasons.append(f"шаблонная формулировка: «{phrase}»")

    for term in MIKHAIL_JARGON:
        if re.search(rf"(?<![а-яёa-z]){re.escape(term)}", lowered):
            score -= 20
            reasons.append(f"внутренний термин наружу: «{term}»")

    question_count = text.count("?")
    if question_count > 2:
        score -= 10
        reasons.append(f"слишком много вопросов ({question_count})")

    return max(0, min(100, score)), reasons


def run_live_cases(
    cases: list[BehaviorCase], config: AppConfig | None = None
) -> list[BehaviorResult]:
    """Запускает реальные ответы текущей conversation model по фиксированным состояниям."""
    runtime_config = config or load_config()
    runtime_config = replace(runtime_config, max_tokens=min(runtime_config.max_tokens, 500))
    results: list[BehaviorResult] = []

    for case in cases:
        state, current_plan = prepare_case(case)
        contract_failures: list[str] = []
        if current_plan.action != case.expected_action:
            contract_failures.append(
                f"action={current_plan.action}, expected={case.expected_action}"
            )
        if current_plan.field_paths != case.expected_fields:
            contract_failures.append(
                f"fields={current_plan.field_paths}, expected={case.expected_fields}"
            )

        # Генерируем ответ только после проверки deterministic части, но сохраняем
        # обе группы ошибок в одном артефакте.
        started = time.perf_counter()
        payload = core.build_runtime_payload(state, case.latest_user_message)
        raw_response = llm_agent.generate_reply(payload, runtime_config)
        latency = time.perf_counter() - started

        # Проверяем именно финальный текст после серверных deterministic guardrails.
        # Это тот ответ, который получает frontend, включая обязательный контактный
        # вопрос и технический хвост завершения/ссылки.
        committed = core.commit_turn(state, case.latest_user_message, raw_response)
        bubbles = [str(item) for item in committed["messages"]]
        response = "\n\n".join(bubbles)
        _, response_failures = evaluate_response(case, response)
        legacy_score, legacy_reasons = mikhail_legacy_score(bubbles)
        humanity_score, humanity_reasons = v4_humanity_score(bubbles)
        coherence_score, coherence_reasons = dialog_coherence_score(
            case_coherence_turns(case, bubbles)
        )
        coherence_failures = (
            [
                f"связность {coherence_score}, минимум {case.min_coherence_score}"
            ]
            if case.min_coherence_score is not None
            and coherence_score < case.min_coherence_score
            else []
        )
        failures = [*contract_failures, *response_failures, *coherence_failures]
        results.append(
            BehaviorResult(
                case_id=case.id,
                passed=not failures,
                raw_response=raw_response,
                response=response,
                bubbles=bubbles,
                failures=failures,
                mikhail_legacy_score=legacy_score,
                mikhail_legacy_reasons=legacy_reasons,
                v4_humanity_score=humanity_score,
                v4_humanity_reasons=humanity_reasons,
                dialog_coherence_score=coherence_score,
                dialog_coherence_reasons=coherence_reasons,
                latency_sec=round(latency, 3),
            )
        )
    return results


def write_report(
    results: list[BehaviorResult],
    output: Path,
    *,
    model: str | None = None,
) -> None:
    """Пишет воспроизводимый JSON-артефакт для сравнения prompt/model версий."""
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "conversation_model": model,
        "n": len(results),
        "passed": sum(result.passed for result in results),
        "mikhail_reference_score": 94.1,
        "mean_mikhail_legacy_score": round(
            sum(result.mikhail_legacy_score for result in results) / len(results), 2
        )
        if results
        else None,
        "mean_v4_humanity_score": round(
            sum(result.v4_humanity_score for result in results) / len(results), 2
        )
        if results
        else None,
        "mean_dialog_coherence_score": round(
            sum(result.dialog_coherence_score for result in results) / len(results), 2
        )
        if results
        else None,
        "results": [asdict(result) for result in results],
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
