"""Сквозная проверка реальных локальных диалогов через HTTP/SSE.

Модуль намеренно ходит в тот же API, что и frontend: так в проверку попадают
extractor, state machine, deterministic guardrails, маршрутизация и usage accounting.
CRM-адаптер здесь не вызывается; локальный запуск обязан работать в router mock mode.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from evaluation.behavior import mikhail_legacy_score, v4_humanity_score
from evaluation.coherence import CoherenceTurn, dialog_coherence_score
from prompts import OPENING_MESSAGE


JsonDict = dict[str, Any]


class DialogScenario(BaseModel):
    """Входной контракт одного воспроизводимого разговора."""

    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    messages: list[str] = Field(min_length=1)
    expected_route_id: str
    expected_facts: dict[str, Any] = Field(default_factory=dict)
    turn_expectations: list["DialogTurnExpectation"] = Field(
        default_factory=list
    )
    # Порог связности всего разговора. В отличие от min_humanity_score он
    # оценивает не отдельный ход, а уместность реплик относительно контекста.
    min_coherence_score: int | None = Field(default=None, ge=0, le=100)


class DialogTurnExpectation(BaseModel):
    """Hard checks конкретного ответа после N-й реплики клиента."""

    model_config = ConfigDict(extra="forbid")

    user_turn: int = Field(ge=1)
    required_any: list[str] = Field(default_factory=list)
    forbidden_any: list[str] = Field(default_factory=list)
    max_questions: int = Field(default=2, ge=0)
    min_humanity_score: int | None = Field(default=None, ge=0, le=100)
    expected_facts: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class DialogTurn:
    """Сохранённый клиентский ход и итоговый публичный state."""

    user: str | None
    assistant_bubbles: list[str]
    assistant_text: str
    latency_sec: float
    mikhail_legacy_score: int
    mikhail_legacy_reasons: list[str]
    v4_humanity_score: int
    v4_humanity_reasons: list[str]
    public_state: JsonDict


@dataclass(frozen=True)
class DialogResult:
    """Полный артефакт сценария, включая проверки и фактический расход."""

    scenario_id: str
    description: str
    session_id: str
    passed: bool
    failures: list[str]
    turns: list[DialogTurn]
    final_state: JsonDict
    route_id: str | None
    usage: JsonDict
    safety: JsonDict
    mean_mikhail_legacy_score: float
    mean_v4_humanity_score: float
    dialog_coherence_score: int
    dialog_coherence_reasons: list[str]


def load_dialog_scenarios(path: Path) -> list[DialogScenario]:
    """Загружает сценарии со строгой валидацией схемы."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("dialog cases должны быть JSON-массивом")
    return [DialogScenario.model_validate(item) for item in raw]


class LocalDialogClient:
    """Минимальный cookie-aware клиент локального FastAPI и SSE."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._cookie_jar = urllib.request.HTTPCookieProcessor()
        # Локальный стенд никогда не должен уходить через системный HTTP_PROXY:
        # некоторые прокси закрывают запрос к 127.0.0.1 без HTTP-ответа.
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            self._cookie_jar,
        )

    def login(self, username: str, password: str) -> None:
        """Получает HttpOnly cookie, не сохраняя учётные данные в артефактах."""
        response = self._json_request(
            "POST",
            "/api/auth/login",
            {"username": username, "password": password},
        )
        if response.get("ok") is not True:
            raise RuntimeError("локальная авторизация не подтверждена")

    def create_session(self) -> JsonDict:
        """Создаёт чистую сессию без старой анкеты."""
        return self._json_request("POST", "/api/session", {"anketa": None})

    def stream_turn(self, session_id: str, message: str | None) -> JsonDict:
        """Читает SSE до terminal event done и возвращает клиентские пузыри/state."""
        query = ""
        if message:
            query = "?" + urllib.parse.urlencode({"message": message})
        request = urllib.request.Request(
            f"{self._base_url}/api/session/{session_id}/stream{query}",
            headers={"Accept": "text/event-stream"},
            method="GET",
        )

        events: list[tuple[str, JsonDict]] = []
        with self._opener.open(request, timeout=180) as response:
            event_name = "message"
            data_lines: list[str] = []
            while True:
                raw_line = response.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8").rstrip("\r\n")
                if line.startswith("event:"):
                    event_name = line.removeprefix("event:").strip()
                elif line.startswith("data:"):
                    data_lines.append(line.removeprefix("data:").lstrip())
                elif not line:
                    if data_lines:
                        payload = json.loads("\n".join(data_lines))
                        events.append((event_name, payload))
                        if event_name == "done":
                            break
                    event_name = "message"
                    data_lines = []

        error_payload = next(
            (payload for name, payload in events if name == "error"),
            None,
        )
        if error_payload:
            raise RuntimeError(f"SSE error: {error_payload.get('message')}")

        # Ход собирается из ВСЕХ событий `bubbles` по порядку. С 19.08.2026 пузыри
        # уходят по одному, отдельными событиями, чтобы появляться в чате с
        # паузой, — и клиент, бравший последнее событие, видел только
        # заключительную реплику. Тихо страдали все измерения: проверяющий
        # связность считал, что бот не ответил на вопрос, хотя ответ был в
        # первом пузыре, а запреты диалоговых сценариев проверялись по обрубку.
        # Тем же правилом живёт наш веб-интерфейс: он дописывает пришедшее в
        # конец переписки, а не заменяет им прежнее.
        collected: list[str] = []
        saw_bubbles = False
        for name, payload in events:
            if name != "bubbles":
                continue
            chunk = payload.get("bubbles")
            if not isinstance(chunk, list):
                continue
            saw_bubbles = True
            collected.extend(str(item) for item in chunk)

        state_payload = next(
            (payload for name, payload in reversed(events) if name == "state"),
            {},
        )
        # Пустой список законен: после финала бот молчит на поддакивания
        # клиента. Отсутствие самого события — по-прежнему ошибка протокола.
        if not saw_bubbles:
            raise RuntimeError("SSE не вернул клиентские bubbles")
        return {
            "bubbles": collected,
            "state": state_payload.get("state") or {},
            "delivery": state_payload.get("delivery"),
        }

    def usage(self, session_id: str) -> JsonDict:
        """Получает серверный token/cost accounting завершённой сессии."""
        return self._json_request("GET", f"/api/session/{session_id}/usage")

    def end_session(self, session_id: str) -> None:
        """Просит backend сохранить финальный лог сессии."""
        self._json_request("POST", f"/api/session/{session_id}/end", {})

    def _json_request(
        self,
        method: str,
        path: str,
        payload: JsonDict | None = None,
    ) -> JsonDict:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with self._opener.open(request, timeout=30) as response:
            value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"{path} вернул не JSON-объект")
        return value


def run_dialog_scenario(
    client: LocalDialogClient,
    scenario: DialogScenario,
) -> DialogResult:
    """Проводит один разговор и проверяет его конечное состояние."""
    created = client.create_session()
    session_id = str(created["session_id"])
    turns: list[DialogTurn] = []

    # Opening проверяется тем же HTTP/SSE путём, а не чтением константы напрямую.
    opening = _timed_turn(client, session_id, None)
    turns.append(opening)

    # Скрипт отвечает естественными фразами и может сообщать несколько фактов за ход.
    for message in scenario.messages:
        turn = _timed_turn(client, session_id, message)
        turns.append(turn)
        if bool(turn.public_state.get("closed")):
            break

    final_state = turns[-1].public_state
    usage = client.usage(session_id)
    client.end_session(session_id)
    coherence_score, coherence_reasons = dialog_coherence_score(
        _coherence_turns(turns)
    )
    failures = _validate_dialog(scenario, turns, final_state, coherence_score)
    route_id = _route_id(final_state)
    scored_turns = turns[1:]

    return DialogResult(
        scenario_id=scenario.id,
        description=scenario.description,
        session_id=session_id,
        passed=not failures,
        failures=failures,
        turns=turns,
        final_state=final_state,
        route_id=route_id,
        usage=usage,
        safety={
            "router_mocked": bool((final_state.get("delivery") or {}).get("mocked")),
            "crm_workflow_enabled": bool(
                (final_state.get("runtime") or {}).get(
                    "crm_workflow_enabled"
                )
            ),
            "crm_lead_intake_enabled": bool(
                (final_state.get("runtime") or {}).get(
                    "crm_lead_intake_enabled"
                )
            ),
            "crm_lead_status": (
                (final_state.get("crm_lead") or {}).get("status")
            ),
            "crm_lead_intake_invoked": (
                (final_state.get("crm_lead") or {}).get("status")
                != "not_sent"
            ),
            "crm_reason": (
                "Runtime flags и crm_lead state проверены в финальном public state."
            ),
        },
        mean_mikhail_legacy_score=_mean(
            [turn.mikhail_legacy_score for turn in scored_turns]
        ),
        mean_v4_humanity_score=_mean(
            [turn.v4_humanity_score for turn in scored_turns]
        ),
        dialog_coherence_score=coherence_score,
        dialog_coherence_reasons=coherence_reasons,
    )


def _coherence_turns(turns: list[DialogTurn]) -> list[CoherenceTurn]:
    """Отбрасывает opening: он не реакция на клиента, а приветствие бота."""
    return [
        CoherenceTurn(user=turn.user, bubbles=turn.assistant_bubbles)
        for turn in turns
        if turn.user
    ]


def _timed_turn(
    client: LocalDialogClient,
    session_id: str,
    message: str | None,
) -> DialogTurn:
    """Измеряет полный client-observed latency одного SSE-хода."""
    started = time.perf_counter()
    response = client.stream_turn(session_id, message)
    latency = round(time.perf_counter() - started, 3)
    bubbles = response["bubbles"]
    legacy_score, legacy_reasons = mikhail_legacy_score(bubbles)
    humanity_score, humanity_reasons = v4_humanity_score(bubbles)
    return DialogTurn(
        user=message,
        assistant_bubbles=bubbles,
        assistant_text="\n\n".join(bubbles),
        latency_sec=latency,
        mikhail_legacy_score=legacy_score,
        mikhail_legacy_reasons=legacy_reasons,
        v4_humanity_score=humanity_score,
        v4_humanity_reasons=humanity_reasons,
        public_state=response["state"],
    )


def _validate_dialog(
    scenario: DialogScenario,
    turns: list[DialogTurn],
    final_state: JsonDict,
    coherence_score: int,
) -> list[str]:
    """Проверяет opening, факты, маршрут, mock-safety и повторяемость фраз."""
    failures: list[str] = []
    if turns[0].assistant_bubbles != [OPENING_MESSAGE]:
        failures.append("opening отличается от утверждённой фразы")

    facts = final_state.get("facts") or {}
    for path, expected in scenario.expected_facts.items():
        actual = _get_by_path(facts, path)
        if actual != expected:
            failures.append(f"{path}={actual!r}, expected={expected!r}")

    actual_route = _route_id(final_state)
    if actual_route != scenario.expected_route_id:
        failures.append(
            f"route_id={actual_route!r}, expected={scenario.expected_route_id!r}"
        )

    delivery = final_state.get("delivery") or {}
    if delivery.get("status") != "completed" or delivery.get("mocked") is not True:
        failures.append("финал не подтверждён локальным router mock")

    runtime = final_state.get("runtime") or {}
    if runtime.get("crm_workflow_enabled") is not False:
        failures.append("safety: CRM workflow должен быть выключен")
    if runtime.get("crm_lead_intake_enabled") is not False:
        failures.append("safety: CRM lead-intake должен быть выключен")
    crm_lead = final_state.get("crm_lead") or {}
    if crm_lead.get("status") != "not_sent":
        failures.append(
            f"safety: crm_lead.status={crm_lead.get('status')!r}, "
            "expected='not_sent'"
        )

    for expectation in scenario.turn_expectations:
        if expectation.user_turn >= len(turns):
            failures.append(
                f"turn {expectation.user_turn}: сценарий завершился раньше проверки"
            )
            continue
        checked_turn = turns[expectation.user_turn]
        lowered = checked_turn.assistant_text.casefold()
        if expectation.required_any and not any(
            marker.casefold() in lowered
            for marker in expectation.required_any
        ):
            failures.append(
                f"turn {expectation.user_turn}: нет ожидаемой смысловой опоры: "
                + " | ".join(expectation.required_any)
            )
        for marker in expectation.forbidden_any:
            if marker.casefold() in lowered:
                failures.append(
                    f"turn {expectation.user_turn}: запрещённая формулировка "
                    f"{marker!r}"
                )
        question_count = checked_turn.assistant_text.count("?")
        if question_count > expectation.max_questions:
            failures.append(
                f"turn {expectation.user_turn}: вопросов {question_count}, "
                f"максимум {expectation.max_questions}"
            )
        if (
            expectation.min_humanity_score is not None
            and checked_turn.v4_humanity_score
            < expectation.min_humanity_score
        ):
            failures.append(
                f"turn {expectation.user_turn}: человечность "
                f"{checked_turn.v4_humanity_score}, минимум "
                f"{expectation.min_humanity_score}"
            )
        turn_facts = checked_turn.public_state.get("facts") or {}
        for path, expected in expectation.expected_facts.items():
            actual = _get_by_path(turn_facts, path)
            if actual != expected:
                failures.append(
                    f"turn {expectation.user_turn}: {path}={actual!r}, "
                    f"expected={expected!r}"
                )

    # Три одинаковых начала подряд уже воспринимаются как скрипт, даже если каждое
    # сообщение по отдельности корректно.
    openings = [_opening_token(turn.assistant_text) for turn in turns[1:]]
    for first, second, third in zip(openings, openings[1:], openings[2:]):
        if first and first == second == third:
            failures.append(f"три ответа подряд начинаются одинаково: {first!r}")
            break

    if (
        scenario.min_coherence_score is not None
        and coherence_score < scenario.min_coherence_score
    ):
        failures.append(
            f"связность разговора {coherence_score}, минимум "
            f"{scenario.min_coherence_score}"
        )
    return failures


def _route_id(state: JsonDict) -> str | None:
    delivery = state.get("delivery") or {}
    request = delivery.get("request") or {}
    routing = request.get("routing") or {}
    value = routing.get("route_id")
    return str(value) if value else None


def _get_by_path(data: JsonDict, path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _opening_token(text: str) -> str:
    words = text.strip().lower().split()
    return words[0].strip(".,!?:;—-") if words else ""


def _mean(values: list[int]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


def load_local_credentials(root: Path) -> tuple[str, str]:
    """Читает локальные credentials из .env, не возвращая их в отчёт."""
    load_dotenv(root / ".env")
    username = (os.getenv("MBK_AUTH_USERNAME") or "").strip()
    password = (os.getenv("MBK_AUTH_PASSWORD") or "").strip()
    if not username or not password:
        raise RuntimeError("MBK_AUTH_USERNAME/MBK_AUTH_PASSWORD не настроены")
    return username, password


def write_dialog_report(results: list[DialogResult], output: Path) -> None:
    """Сохраняет общий индекс и каждый разговор отдельным JSON-файлом."""
    output.mkdir(parents=True, exist_ok=True)
    for result in results:
        artifact = output / f"{result.scenario_id}.json"
        artifact.write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    total_cost = sum(float(result.usage.get("total_cost_usd") or 0) for result in results)
    total_calls = sum(int(result.usage.get("total_calls") or 0) for result in results)
    summary = {
        "n": len(results),
        "passed": sum(result.passed for result in results),
        "total_openrouter_calls": total_calls,
        "total_estimated_cost_usd": round(total_cost, 6),
        "mean_mikhail_legacy_score": round(
            sum(result.mean_mikhail_legacy_score for result in results) / len(results),
            2,
        )
        if results
        else None,
        "mean_v4_humanity_score": round(
            sum(result.mean_v4_humanity_score for result in results) / len(results),
            2,
        )
        if results
        else None,
        "mean_dialog_coherence_score": round(
            sum(result.dialog_coherence_score for result in results) / len(results),
            2,
        )
        if results
        else None,
        # Свод по причинам показывает, какой дефект связности массовый, а какой
        # разовый: без этого 97 против 100 ничего не сообщает.
        "coherence_reasons": sorted(
            {
                reason
                for result in results
                for reason in result.dialog_coherence_reasons
            }
        ),
        "scenarios": [
            {
                "id": result.scenario_id,
                "passed": result.passed,
                "route_id": result.route_id,
                "failures": result.failures,
                "cost_usd": result.usage.get("total_cost_usd"),
                "calls": result.usage.get("total_calls"),
                "mean_mikhail_legacy_score": result.mean_mikhail_legacy_score,
                "mean_v4_humanity_score": result.mean_v4_humanity_score,
                "dialog_coherence_score": result.dialog_coherence_score,
                "dialog_coherence_reasons": result.dialog_coherence_reasons,
            }
            for result in results
        ],
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
