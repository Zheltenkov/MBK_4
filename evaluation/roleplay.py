"""Прогон разговора, где клиента играет отдельная модель.

Отличие от `evaluation/dialogs.py` одно, но решающее: там реплики клиента вшиты
в сценарий, здесь их порождает модель с ролью. Вшитый сценарий проверяет тот
путь, который мы сами и придумали, и потому не показателен — живой человек
отвечает не теми словами, путается и спрашивает лишнее.

Метрики те же (`v4_humanity_score` поштучно, `dialog_coherence_score` целиком),
поэтому числа сравнимы с прежними прогонами.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import llm_agent
from config import AppConfig, load_config
from evaluation.behavior import v4_humanity_score
from evaluation.coherence import CoherenceTurn, dialog_coherence_score
from evaluation.dialogs import LocalDialogClient
from evaluation.personas import Persona

JsonDict = dict[str, Any]

# Клиента играет НЕ та модель, что ведёт разговор: одна и та же склонна
# подыгрывать себе, и оценка выходит завышенной.
DEFAULT_CLIENT_MODEL = "openai/gpt-5.4-mini"


@dataclass
class RoleplayTurn:
    user: str | None
    bubbles: list[str]
    latency_sec: float
    humanity_score: int
    humanity_reasons: list[str]


@dataclass
class RoleplayResult:
    persona_id: str
    description: str
    session_id: str
    turns: list[RoleplayTurn]
    finished: bool
    route_id: str | None
    crm_status: str | None
    coherence_score: int
    coherence_reasons: list[str]
    facts: JsonDict = field(default_factory=dict)
    violations: list[str] = field(default_factory=list)
    usage: JsonDict = field(default_factory=dict)

    @property
    def mean_humanity(self) -> float:
        scores = [turn.humanity_score for turn in self.turns if turn.bubbles]
        return round(sum(scores) / len(scores), 2) if scores else 0.0

    @property
    def passed(self) -> bool:
        return not self.violations


def client_model() -> str:
    return (os.getenv("MBK_EVAL_CLIENT_MODEL") or DEFAULT_CLIENT_MODEL).strip()


def _client_reply(
    persona: Persona,
    history: list[JsonDict],
    config: AppConfig,
) -> str:
    """Следующая реплика клиента. Роль модель не видит нашей матрицы и промптов."""
    payload = {
        "model": client_model(),
        "messages": [
            {"role": "system", "content": persona.system_prompt()},
            *history,
        ],
        "temperature": 0.9,
        "max_tokens": 120,
    }
    response = llm_agent._post(config, payload, max_attempts=3)
    content = (response.get("choices") or [{}])[0].get("message", {}).get("content")
    return (content or "").strip()


# Фазы, после которых говорить больше не о чем. Те же, что у бота в core.
_TERMINAL_PHASES = {"survey_completed", "no_fit", "link_sent"}


def _is_finished(state: JsonDict) -> bool:
    # Собственный признак продукта — первым. Прежние два условия видели только
    # успех: созданный лид или подобранный маршрут. Клиенту, которому решения
    # не нашлось, прогон записывал «анкета не дошла до конца», хотя разговор
    # отработал целиком и бот уже попрощался.
    if state.get("dialog_phase") in _TERMINAL_PHASES:
        return True
    # Ветка развилки кончается прощанием, а не фазой анкеты.
    if (state.get("entry") or {}).get("closed"):
        return True
    crm_lead = state.get("crm_lead") or {}
    if crm_lead.get("status") in {"submitted", "rejected"}:
        return True
    delivery = state.get("delivery") or {}
    if delivery.get("status") in {"completed", "link", "no_fit"}:
        return True
    return bool(state.get("closed"))


def _route_id(state: JsonDict) -> str | None:
    plan = state.get("plan") or {}
    matched = plan.get("matched")
    return matched if isinstance(matched, str) else None


def run_roleplay(
    client: LocalDialogClient,
    persona: Persona,
    *,
    config: AppConfig | None = None,
) -> RoleplayResult:
    config = config or load_config()
    session = client.create_session()
    session_id = session["session_id"]

    turns: list[RoleplayTurn] = []
    # История глазами роли: наш бот для неё — собеседник, то есть "user".
    history: list[JsonDict] = []

    started = time.perf_counter()
    opening = client.stream_turn(session_id, None)
    bubbles = opening.get("bubbles") or []
    score, reasons = v4_humanity_score(bubbles)
    turns.append(
        RoleplayTurn(
            user=None,
            bubbles=bubbles,
            latency_sec=round(time.perf_counter() - started, 2),
            humanity_score=score,
            humanity_reasons=reasons,
        )
    )
    history.append({"role": "user", "content": "\n".join(bubbles)})

    state: JsonDict = opening.get("state") or {}
    finished = False
    after_finish = persona.post_finish_turns
    for _ in range(persona.max_turns + persona.post_finish_turns):
        reply = _client_reply(persona, history, config)
        if not reply:
            break
        history.append({"role": "assistant", "content": reply})

        started = time.perf_counter()
        turn = client.stream_turn(session_id, reply)
        bubbles = turn.get("bubbles") or []
        score, reasons = v4_humanity_score(bubbles)
        turns.append(
            RoleplayTurn(
                user=reply,
                bubbles=bubbles,
                latency_sec=round(time.perf_counter() - started, 2),
                humanity_score=score,
                humanity_reasons=reasons,
            )
        )
        history.append({"role": "user", "content": "\n".join(bubbles)})

        state = turn.get("state") or state
        if finished or _is_finished(state):
            finished = True
            if after_finish <= 0:
                break
            after_finish -= 1

    coherence, coherence_reasons = dialog_coherence_score(
        [CoherenceTurn(user=turn.user, bubbles=turn.bubbles) for turn in turns]
    )

    result = RoleplayResult(
        persona_id=persona.id,
        description=persona.description,
        session_id=session_id,
        turns=turns,
        finished=finished,
        route_id=_route_id(state),
        crm_status=(state.get("crm_workflow") or {}).get("status"),
        coherence_score=coherence,
        coherence_reasons=coherence_reasons,
        facts=state.get("facts") or {},
        usage=client.usage(session_id),
    )
    result.violations = _violations(result, persona)
    return result


def _flatten(facts: JsonDict, prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in (facts or {}).items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten(value, path))
        else:
            flat[path] = value
    return flat


# Пути, которые нужны для расчёта эталонного маршрута, но сверке не подлежат.
#
# Здесь был `employment.type`: на «работаю по найму» модель писала то `hired`,
# то `official`, и сверка недетерминированного поля держала бы вечно красный
# тест. 12.08.2026 заказчик выбрал `official`, синонимы сводятся в
# `routing/normalize`, и поле снова можно проверять — исключение снято.
IGNORED_FACT_PATHS: frozenset[str] = frozenset()


def compare_facts(expected: JsonDict, actual: JsonDict) -> tuple[int, int, list[str]]:
    """Сколько эталонных фактов бот собрал из живого разговора.

    Сравниваются только пути эталона: всё лишнее, что модель добыла сверх, —
    не ошибка. Телефон сравнивается по цифрам: клиент диктует его как хочет.
    """
    wanted = {
        path: value
        for path, value in _flatten(expected).items()
        if path not in IGNORED_FACT_PATHS
    }
    got = _flatten(actual)
    mismatches: list[str] = []
    for path, value in sorted(wanted.items()):
        collected = got.get(path)
        if path.endswith("phone"):
            value = re.sub(r"\D", "", str(value))
            collected = re.sub(r"\D", "", str(collected or ""))
        if collected != value:
            mismatches.append(f"{path}: ждали {value!r}, собрали {collected!r}")
    return len(wanted) - len(mismatches), len(wanted), mismatches


def _violations(result: RoleplayResult, persona: Persona) -> list[str]:
    """Нарушения, которые видно без человека: запреты роли и провал метрик."""
    problems: list[str] = []
    spoken = " ".join(
        bubble for turn in result.turns for bubble in turn.bubbles
    ).casefold()
    for phrase in persona.forbidden_any:
        if phrase.casefold() in spoken:
            problems.append(f"запрещённая формулировка: «{phrase}»")
    if not result.finished and persona.expects_completion:
        problems.append(
            f"анкета не дошла до конца за {persona.max_turns} ходов"
        )
    if result.coherence_score < 90:
        problems.append(f"связность {result.coherence_score} < 90")
    if result.mean_humanity < 90:
        problems.append(f"человечность {result.mean_humanity} < 90")
    return problems


def write_report(results: list[RoleplayResult], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "client_model": client_model(),
        "personas": [
            {
                "persona_id": result.persona_id,
                "description": result.description,
                "session_id": result.session_id,
                "passed": result.passed,
                "violations": result.violations,
                "finished": result.finished,
                "route_id": result.route_id,
                "crm_status": result.crm_status,
                "turns": len(result.turns),
                "mean_humanity": result.mean_humanity,
                "coherence_score": result.coherence_score,
                "coherence_reasons": result.coherence_reasons,
                "cost_usd": result.usage.get("total_cost_usd"),
                "dialog": [
                    {
                        "user": turn.user,
                        "bot": turn.bubbles,
                        "humanity": turn.humanity_score,
                        "humanity_reasons": turn.humanity_reasons,
                        "latency_sec": turn.latency_sec,
                    }
                    for turn in result.turns
                ],
            }
            for result in results
        ],
    }
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
