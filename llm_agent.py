"""Слой обращения к модели.

Транспорт (ретраи, стриминг, кэш префикса, трассировка) перенесён из v3 без изменений —
он обкатан. Изменилось то, ЧТО отправляется: вместо свода продуктов и стоп-факторов
модель получает короткую инструкцию от планировщика, а вместо большого `StateUpdate`
возвращает только извлечённые факты.
"""
from __future__ import annotations

import http.client
import json
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterator, Literal

from pydantic import ValidationError

import observability
import transport
from assistant_contracts import FactExtraction, FactUpdate
from config import AppConfig
from interpretation import (
    CandidateIssueCode,
    FactCandidate,
    FactCandidateExtraction,
    FactCandidateIssue,
    FactSource,
    SemanticBoundarySignals,
    apply_candidate_policies,
)
from interpretation.prompt import build_interpretation_system_prompt
from pii_masking import mask_payload_for_llm
from policy_content import BANKRUPTCY_EXPLANATION
from prompts import (
    CONVERSATION_SYSTEM_PROMPT,
    EXTRACTION_SYSTEM_PROMPT,
    OPENING_MESSAGE,
    STYLE_EXAMPLES,
    policy_brief_reaction_prompt,
    policy_debt_act_prompt,
)
from routing import Matrix, get_matrix
from routing.entry_intent import EntryIntent

_RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}

EXTRACTOR_MAX_ATTEMPTS = 2

FailureKind = Literal[
    "http", "timeout", "connection", "read", "invalid_json", "invalid_response", "empty_response",
    "invalid_schema", "context", "configuration", "deadline", "unknown",
]
FailurePhase = Literal["open", "read", "decode", "response", "context", "schema", "configuration", "deadline", "unknown"]


@dataclass(frozen=True)
class ModelFailure:
    """Diagnostic metadata contains no provider text, request data or access values."""

    kind: FailureKind
    phase: FailurePhase
    error_type: str
    http_status: int | None = None
    retryable: bool = False


class ModelRequestError(RuntimeError):
    """Keep RuntimeError compatibility while preserving structured failure details."""

    def __init__(self, message: str, failure: ModelFailure):
        super().__init__(message)
        self.failure = failure


_SAFE_ERROR_TYPES = frozenset({
    "HTTPError", "TimeoutError", "URLError", "ConnectionError", "ConnectionResetError",
    "ConnectionAbortedError", "ConnectionRefusedError", "BrokenPipeError", "RemoteDisconnected",
    "IncompleteRead", "HTTPException", "SSLError", "SSLCertVerificationError", "JSONDecodeError",
    "UnicodeDecodeError", "ValueError", "TypeError", "KeyError", "IndexError", "AttributeError",
    "RuntimeError", "OSError", "ValidationError",
})


def model_failure(error: BaseException, *, phase: FailurePhase = "unknown") -> ModelFailure:
    """Classify trusted exception types without copying their arbitrary messages."""
    if isinstance(error, ModelRequestError):
        return error.failure
    name = type(error).__name__
    error_type = name if name in _SAFE_ERROR_TYPES else "OtherError"
    if isinstance(error, urllib.error.HTTPError):
        status = error.code if isinstance(error.code, int) and 100 <= error.code <= 599 else None
        return ModelFailure("http", phase, error_type, status, status in _RETRYABLE)
    cause = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(cause, transport.ResponseDeadlineExceeded):
        return ModelFailure("deadline", phase, "DeadlineExceeded")
    if isinstance(cause, TimeoutError):
        return ModelFailure("timeout", phase, error_type, retryable=True)
    if isinstance(error, (json.JSONDecodeError, UnicodeDecodeError)):
        return ModelFailure("invalid_json", phase, error_type, retryable=True)
    if phase == "context":
        return ModelFailure("context", phase, error_type)
    if phase == "schema":
        return ModelFailure("invalid_schema", phase, error_type)
    if isinstance(error, transport.TRANSPORT_ERRORS):
        kind: FailureKind = "read" if phase == "read" else "connection"
        return ModelFailure(kind, phase, error_type, retryable=True)
    return ModelFailure("unknown", phase, error_type)

# Стрим повторяем скупее, чем `_post`: там повтора ждёт молчаливый извлекатель,
# здесь — живой клиент перед пустым чатом. Одна вторая попытка снимает разовый
# 502 и разовый обрыв, а всё, что не лечится ею, лечится обновлением страницы
# быстрее, чем третьим заходом на 120-секундном потолке ожидания.
STREAM_MAX_ATTEMPTS = 2
STREAM_RETRY_BACKOFF = 0.5
# Ниже этого остатка потолка повтор не начинаем: он всё равно не успеет ответить,
# а клиент уже прождал почти всё отведённое время.
STREAM_RETRY_MIN_BUDGET = 5


def _is_reasoning_model(model: str) -> bool:
    name = (model or "").lower()
    return (
        name.startswith("deepseek/deepseek-v4")
        or "qwen3.7-max" in name
        or "reason" in name
        or "/o1" in name
        or "/o3" in name
    )


def _headers(config: AppConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://local.mbk-human-assistant",
        "X-Title": "MBK Assistant v4",
    }


def _chat_completions_url(config: AppConfig) -> str:
    """Endpoint строится из runtime config, чтобы gateway не был фиктивной настройкой."""
    return f"{config.base_url.rstrip('/')}/chat/completions"


def _urlopen(config: AppConfig, request: urllib.request.Request):
    """Выполняет запрос, переиспользуя живое соединение.

    Раньше здесь стоял `urlopen`, а он на каждый вызов открывает новое
    соединение. Через прокси это стоило 1,4 секунды — по замеру 19.08.2026,
    дважды за ход диалога. Пул живёт в transport.py; сигнатура и объект ответа
    прежние, поэтому ретраи и разбор ошибок ниже не менялись.
    """
    request_data = request.data
    if request_data is None:
        body = b""
    elif isinstance(request_data, bytes):
        body = request_data
    else:
        raise TypeError("LLM transport принимает тело запроса только как bytes")

    return transport.POOL.request(
        request.full_url,
        data=body,
        headers={**dict(request.header_items()), "Host": urlsplit(request.full_url).netloc},
        timeout=config.request_timeout,
        proxy_urls=() if config.llm_provider == "polza" else config.proxy_urls,
        method=request.get_method(),
        # Streaming owns its existing inactivity/retry policy; JSON replies use
        # one transport deadline through the final body byte.
        streaming=json.loads(body).get("stream") is True if body else False,
    )


# Что мы выяснили о моделях на ходу. Список имён выше — только первая догадка:
# он был захардкожен, и любая рассуждающая модель не из него возвращала пустой
# ответ, а бот молча падал. Так вели себя qwen3.7-flash и glm-4.7-flash на
# замерах 19.08.2026 — дело было в нас, не в них.
_LEARNED_REASONING: set[str] = set()
# Модели, которые сам параметр подавления не принимают: minimax отвечает на него
# HTTP 400. Без этой памяти такая модель недоступна в принципе.
_REJECTS_REASONING_PARAM: set[str] = set()


def _suppresses_reasoning(model_name: str) -> bool:
    name = (model_name or "").lower()
    if name in _REJECTS_REASONING_PARAM:
        return False
    return _is_reasoning_model(name) or name in _LEARNED_REASONING


def _maybe_suppress_reasoning(payload: dict[str, Any], model_name: str) -> None:
    if _suppresses_reasoning(model_name):
        payload["reasoning"] = {"effort": "none", "exclude": True}
        payload["include_reasoning"] = False
    else:
        payload.pop("reasoning", None)
        payload.pop("include_reasoning", None)


def _learn_reasoning_model(model_name: str) -> bool:
    """Модель вернула пустоту — значит весь ответ ушёл в рассуждение.

    Возвращает False, если это уже известно: повторять попытку бессмысленно.
    """
    name = (model_name or "").lower()
    if not name or name in _LEARNED_REASONING or _is_reasoning_model(name):
        return False
    _LEARNED_REASONING.add(name)
    return True


def _learn_reasoning_param_rejected(model_name: str, detail: str) -> bool:
    """Модель отвергла сам параметр подавления. Больше ей его не шлём."""
    name = (model_name or "").lower()
    if "reasoning" not in detail.lower() or not name:
        return False
    if name in _REJECTS_REASONING_PARAM:
        return False
    _REJECTS_REASONING_PARAM.add(name)
    return True


def _has_content(data: dict[str, Any]) -> bool:
    message = ((data.get("choices") or [{}])[0] or {}).get("message") or {}
    return bool(str(message.get("content") or "").strip())


# --------------------------------------------------------------------------- #
# Сборка сообщений
# --------------------------------------------------------------------------- #
def _context_block(payload: dict[str, Any]) -> str:
    """Что уже известно о клиенте. Только факты — ни маршрутов, ни приоритетов:
    модель не должна рассуждать о том, что за неё уже решил движок."""
    facts = payload.get("facts") or {}
    parts = [
        "Что уже известно о клиенте (используй, НЕ переспрашивай):\n"
        + json.dumps(facts, ensure_ascii=False, indent=2)
    ]
    if summary := payload.get("internal_summary"):
        parts.append(f"Заметка с прошлого хода: {summary}")
    message = str(payload.get("latest_user_message") or "")
    if (re.search(r"\bбанкрот\w*", message, re.I) or
            re.search(r"\bсписани\w*", message, re.I) and
            re.search(r"\b(?:долг\w*|обязательств\w*|реструктуризац\w*)", message, re.I)):
        # Share the same factual basis with the original credit-chat entrypoint.
        # It is reference context for the current topic, separate from routing.
        parts.append("Проверенные справочные факты для ответа по текущему вопросу:\n" + BANKRUPTCY_EXPLANATION)
    return "\n\n".join(parts)


def _history_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for turn in payload.get("short_history", []):
        role = "assistant" if turn.get("role") == "assistant" else "user"
        content = str(turn.get("content", "")).strip()
        if content:
            messages.append({"role": role, "content": content})
    return messages


def _history_text(payload: dict[str, Any], max_messages: int = 6) -> str:
    """Короткая история для извлекателя.

    Извлечение идёт до нового ответа ассистента, поэтому «да»/«нет» можно понять
    только по предыдущему вопросу. В v4 история раньше случайно не попадала в этот
    вызов, и однословные ответы не имели надёжной привязки к полю.
    """
    rendered: list[str] = []
    for message in _history_messages(payload)[-max_messages:]:
        speaker = "Оператор" if message["role"] == "assistant" else "Клиент"
        rendered.append(f"{speaker}: {message['content']}")
    return "\n".join(rendered) or "(история пуста)"


def _cached_tokens(usage: dict) -> int:
    if not isinstance(usage, dict):
        return 0
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        return int(details.get("cached_tokens", 0) or 0)
    return int(usage.get("cached_tokens", 0) or 0)


def _apply_cache_control(messages: list[dict[str, Any]], enable: bool) -> list[dict[str, Any]]:
    """Кэшируем статический префикс (персона + few-shots) — он байт-в-байт одинаков
    каждый ход. Инструкция хода и факты меняются, их кэшировать нечего."""
    if not enable:
        return messages
    out: list[dict[str, Any]] = []
    cached = 0
    for message in messages:
        if message.get("role") == "system" and cached < 1 and isinstance(message.get("content"), str):
            cached += 1
            out.append({
                "role": "system",
                "content": [{
                    "type": "text",
                    "text": message["content"],
                    "cache_control": {"type": "ephemeral"},
                }],
            })
        else:
            out.append(message)
    return out


def _conversation_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("policy_reply_role") in ("brief_field", "brief_field_repair"):
        # The frame has already proved this small speech task. Keep the same
        # person and history without presenting another credit-intake task.
        instructions = [{"role": "system", "content": policy_brief_reaction_prompt()}]
        if payload.get("policy_reply_role") == "brief_field_repair":
            # G2 must receive the code-owned diagnosis and verified corrective
            # facts even when G1 used the smaller conversational task.
            instructions.append({"role": "system", "content": str(payload.get("turn_instruction", ""))})
        return [
            *instructions,
            *_history_messages(payload),
            {"role": "user", "content": str(payload.get("latest_user_message", ""))},
        ]
    # Only the grounded orchestrator assigns this role after proving the whole
    # incoming debt act. Other messages keep the ordinary questionnaire prompt.
    if payload.get("policy_reply_role") == "debt_act":
        # Keep the proved task and its context in one authoritative system
        # message, so the examples cannot become a second questionnaire task.
        return [
            {"role": "system", "content": "\n\n".join((
                policy_debt_act_prompt(), _context_block(payload), str(payload.get("turn_instruction", "")),
            ))},
            *_history_messages(payload),
            {"role": "user", "content": str(payload.get("latest_user_message", ""))},
        ]
    return [
        {"role": "system", "content": CONVERSATION_SYSTEM_PROMPT + "\n\n" + STYLE_EXAMPLES},
        {"role": "system", "content": _context_block(payload)},
        {"role": "system", "content": str(payload.get("turn_instruction", ""))},
        *_history_messages(payload),
        {"role": "user", "content": str(payload.get("latest_user_message", ""))},
    ]


# --------------------------------------------------------------------------- #
# Транспорт
# --------------------------------------------------------------------------- #
def _with_provider_routing(config: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
    """Просит OpenRouter отдать запрос самому отзывчивому провайдеру модели.

    Модель и промпт не меняются — меняется только, кто их обслужит. Явный выбор
    провайдера в самом запросе не перетираем.
    """
    if not config.provider_sort or "provider" in payload:
        return payload
    return {**payload, "provider": {"sort": config.provider_sort}}


def _post(
    config: AppConfig,
    payload: dict[str, Any],
    *,
    max_attempts: int = 3,
    learn_reasoning_defaults: bool = True,
) -> dict[str, Any]:
    """Выполняет non-stream запрос с управляемой retry-политикой.

    Legacy сохраняет три транспортных попытки. Shadow использует одну, поскольку
    повторная диагностика не должна расходовать бюджет и задерживать клиента.
    """
    payload = _with_provider_routing(config, payload)
    model_name = str(payload.get("model") or "")
    last_failure: ModelFailure | None = None
    attempts = max(1, max_attempts)
    deadline = time.monotonic() + config.request_timeout
    for attempt in range(1, attempts + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            last_failure = ModelFailure("deadline", "open", "DeadlineExceeded")
            break
        # Тело собирается на каждой попытке: между ними мы могли узнать о модели
        # что-то новое — что она рассуждает или что не принимает наш параметр.
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            _chat_completions_url(config), data=body, headers=_headers(config)
        )
        phase: FailurePhase = "open"
        try:
            with _urlopen(replace(config, request_timeout=remaining), request) as response:
                phase = "read"
                raw = response.read()
                if time.monotonic() >= deadline:
                    raise transport.ResponseDeadlineExceeded("model response deadline exhausted")
            phase = "decode"
            data = json.loads(raw)
            phase = "response"
            if not isinstance(data, dict):
                raise ModelRequestError("model response must be an object",
                                        ModelFailure("invalid_response", phase, "TypeError"))
            try:
                has_content = _has_content(data)
            except (AttributeError, TypeError, IndexError, KeyError) as exc:
                raise ModelRequestError("invalid model completion envelope",
                                        ModelFailure("invalid_response", phase, type(exc).__name__)) from None
            if has_content:
                return data
            # Пустой ответ у рассуждающей модели означает, что весь вывод ушёл в
            # рассуждение. Узнаём это один раз и повторяем с подавлением.
            if learn_reasoning_defaults and _learn_reasoning_model(model_name):
                _maybe_suppress_reasoning(payload, model_name)
                continue
            return data
        except urllib.error.HTTPError as exc:
            last_failure = model_failure(exc, phase="open")
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:800]
            except transport.TRANSPORT_ERRORS:
                # HTTP status remains known even when its diagnostic body is torn.
                detail = ""
            if exc.code == 400 and learn_reasoning_defaults and _learn_reasoning_param_rejected(model_name, detail):
                _maybe_suppress_reasoning(payload, model_name)
                continue
            if exc.code not in _RETRYABLE:
                break
        except (*transport.TRANSPORT_ERRORS, json.JSONDecodeError, UnicodeDecodeError) as exc:
            # Не список знакомых классов, а правило: обрыв соединения ронял ход
            # диалога без единого ретрая. Прошлая правка взяла OSError и
            # закрыла RemoteDisconnected с ssl.SSLError, но IncompleteRead из
            # read() осталась снаружи — она HTTPException, а не OSError, то
            # есть тело, оборвавшееся на середине, по-прежнему роняло ход.
            # HTTPError перехвачен выше. Правило см. в
            # transport.TRANSPORT_ERRORS.
            last_failure = model_failure(exc, phase=phase)
        except ModelRequestError:
            raise
        except Exception as exc:
            # Unexpected request/response failures also retain their stage,
            # without propagating provider text into diagnostic exceptions.
            failure = model_failure(exc, phase=phase)
            raise ModelRequestError(f"model request failed ({failure.kind} during {failure.phase})",
                                    failure) from None
        if attempt < attempts:
            backoff = 1.5 * attempt
            # A retry and its backoff spend the original call budget.
            if deadline - time.monotonic() <= backoff:
                break
            time.sleep(backoff)
    failure = last_failure or ModelFailure("empty_response", "response", "RuntimeError")
    message = (f"OpenRouter HTTP {failure.http_status}" if failure.http_status is not None else
               f"model request failed ({failure.kind} during {failure.phase})")
    raise ModelRequestError(message, failure) from None


def _drain(response) -> None:
    """Дочитывает остаток ответа, чтобы соединение годилось для повторного
    использования. Ошибку глушим: разговор уже состоялся."""
    try:
        response.read()
    except Exception:  # noqa: BLE001
        pass


def _post_stream(
    config: AppConfig, payload: dict[str, Any], usage_sink: dict | None = None
) -> Iterator[str]:
    """Стрим с повтором ровно до первого отданного куска.

    Тот же 502 или тот же оборванный коннект молчаливый извлекатель переживал
    (`_post`, три попытки), а разговор с живым клиентом ронял ход целиком:
    здесь повтора не было ни одного. Пул в transport.py переоткрывает только
    протухшее соединение ИЗ пула и только до получения ответа — 429/5xx он
    поднимает как `HTTPError`, а обрыв на свежесозданном соединении пробрасывает
    как есть.

    Граница повтора — первый показанный клиенту кусок: начало фразы уже в чате,
    и второй заход приписал бы к нему второе «Здравствуйте». Считать куски надо
    именно тут: наружу, в `_stream`, они уходят через yield, и различить там
    «ещё ничего не показали» невозможно.

    Попытки и бэкофф скромнее, чем у `_post`: разговорный потолок ожидания —
    120 секунд (config.request_timeout), и повторы обязаны уложиться в него же.
    Клиент, сидящий перед пустым чатом, вторых двух минут не переживёт, поэтому
    остаток потолка передаётся следующей попытке как её собственный таймаут, а
    когда остатка нет — повтора не будет.
    """
    payload = {**_with_provider_routing(config, payload), "stream": True}
    model_name = str(payload.get("model") or "")
    deadline = time.monotonic() + config.request_timeout
    delivered = 0
    attempts_left = STREAM_MAX_ATTEMPTS
    last_error: Exception | None = None

    while True:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            _chat_completions_url(config), data=body, headers=_headers(config)
        )
        try:
            response = _urlopen(config, request)
        except urllib.error.HTTPError as exc:
            # Причину отказа провайдер пишет телом ответа, а не кодом. Без этого
            # разбора в логе оставался голый «HTTP Error 403: Forbidden», по
            # которому неотличимы заблокированный адрес сервера, чужой ключ и
            # запрещённая моделью страна. Non-stream ветка тело читала всегда —
            # здесь его молча теряли, то есть хуже всего диагностировался как раз
            # разговор с живым клиентом.
            detail = exc.read().decode("utf-8", errors="replace")[:800]
            # Модель не принимает параметр подавления рассуждения. Узнаём это один
            # раз и повторяем запрос без него — иначе она недоступна навсегда.
            # Это спор о параметре, а не транспортный сбой, поэтому транспортную
            # попытку он не тратит; память о модели гарантирует, что второй раз
            # сюда не попадём.
            if exc.code == 400 and _learn_reasoning_param_rejected(model_name, detail):
                _maybe_suppress_reasoning(payload, model_name)
                continue
            last_error = RuntimeError(f"OpenRouter HTTP {exc.code}: {detail}")
            if exc.code not in _RETRYABLE:
                raise last_error from exc
        except (OSError, http.client.HTTPException) as exc:
            # Правило, а не список кодов: любой сбой транспорта до первого куска
            # повторяем. Обрыв приходит и как `RemoteDisconnected` (OSError), и
            # как `IncompleteRead` (HTTPException) — перечислять их поимённо мы
            # уже один раз попробовали, и `RemoteDisconnected` тогда просочился.
            last_error = exc
        else:
            try:
                for piece in _read_stream(response, usage_sink):
                    delivered += 1
                    yield piece
                return
            except (OSError, http.client.HTTPException) as exc:
                if delivered:
                    raise
                last_error = exc

        attempts_left -= 1
        remaining = deadline - time.monotonic()
        if attempts_left <= 0 or remaining <= STREAM_RETRY_MIN_BUDGET:
            break
        time.sleep(STREAM_RETRY_BACKOFF)
        config = replace(config, request_timeout=int(remaining - STREAM_RETRY_BACKOFF))
    raise RuntimeError(str(last_error))


def _read_stream(response, usage_sink: dict | None) -> Iterator[str]:
    """Разбирает SSE-тело ответа в куски текста."""
    with response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                _drain(response)
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if usage_sink is not None and isinstance(chunk.get("usage"), dict):
                usage_sink.update(chunk["usage"])
            delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
            if piece := delta.get("content"):
                yield piece
        else:
            # Цикл дошёл до конца сам — тело прочитано, соединение вернётся в
            # пул. Выход по `[DONE]` оставляет хвост непрочитанным, и такое
            # соединение переиспользовать нельзя: следующий вызов получил бы
            # чужие байты. Поэтому дочитываем остаток.
            _drain(response)


def _stream(
    name: str,
    messages: list[dict[str, Any]],
    config: AppConfig,
    usage_collector: Callable[..., None] | None,
) -> Iterator[str]:
    messages = _apply_cache_control(messages, config.enable_prompt_cache)
    body: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "stream_options": {"include_usage": True},
    }
    _maybe_suppress_reasoning(body, config.model)

    usage_sink: dict = {}
    collected: list[str] = []
    error: str | None = None
    with observability.generation(
        name=name,
        model=config.model,
        input_messages=messages,
        model_parameters={
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "stream": True,
        },
    ) as generation:
        try:
            for piece in _post_stream(config, body, usage_sink):
                collected.append(piece)
                yield piece
        except BaseException as exc:  # noqa: BLE001 — ловим всё, чтобы залогировать ошибку
            error = repr(exc)
            raise
        finally:
            observability.finalize_generation(
                generation,
                output="".join(collected),
                usage=observability.usage_from_openrouter(usage_sink),
                error=error,
            )
            if usage_collector and usage_sink:
                try:
                    usage_collector(
                        config.model,
                        int(usage_sink.get("prompt_tokens", 0) or 0),
                        int(usage_sink.get("completion_tokens", 0) or 0),
                        _cached_tokens(usage_sink),
                    )
                except Exception:  # noqa: BLE001 — учёт не должен валить пайплайн
                    pass


def stream_opening(
    payload: dict[str, Any],
    config: AppConfig,
    usage_collector: Callable[..., None] | None = None,
) -> Iterator[str]:
    """Отдаёт утверждённое приветствие без LLM-вызова и вариативности текста."""
    del payload, config, usage_collector
    yield OPENING_MESSAGE


def stream_conversation(
    payload: dict[str, Any],
    config: AppConfig,
    usage_collector: Callable[..., None] | None = None,
) -> Iterator[str]:
    """Живой ответ токен за токеном."""
    safe_payload = mask_payload_for_llm(payload, config.enable_pii_masking)
    yield from _stream(
        "conversation", _conversation_messages(safe_payload), config, usage_collector
    )


def generate_reply(payload: dict[str, Any], config: AppConfig, *,
                   usage_collector: Callable[..., None] | None = None,
                   max_attempts: int = 3) -> str:
    """Не-стримовый разговорный ответ (тесты, эвал, фолбэк)."""
    safe_payload = mask_payload_for_llm(payload, config.enable_pii_masking)
    messages = _conversation_messages(safe_payload)
    return _generate_text(messages, config, usage_collector=usage_collector, max_attempts=max_attempts)


def generate_policy_critique(messages: list[dict[str, str]], config: AppConfig, *,
                             usage_collector: Callable[..., None] | None = None,
                             token_limit: int = 300,
                             generation_name: Literal["policy_voice_critic", "policy_factual_audit"] = "policy_voice_critic",
                             reasoning_effort: Literal["high", "none"] | None = None,
                             response_schema: dict[str, Any] | None = None) -> str:
    """Request one bounded diagnostic response from already-redacted messages."""
    if reasoning_effort is not None and generation_name != "policy_factual_audit":
        raise ValueError("Reasoning override is scoped to the factual task")
    if response_schema is not None and generation_name != "policy_factual_audit":
        raise ValueError("Native schema override is scoped to the factual task")
    cap = 2048 if generation_name == "policy_factual_audit" else 1000
    config = replace(config, temperature=0.0, max_tokens=min(config.max_tokens, max(1, min(token_limit, cap))))
    reasoning_options: dict[str, Literal["high", "none"]] = (
        {"reasoning_effort": reasoning_effort} if reasoning_effort is not None else {}
    )
    return _generate_text(messages, config, usage_collector=usage_collector, max_attempts=1,
                          generation_name=generation_name, json_response=True,
                          response_schema=response_schema, **reasoning_options)


def _generate_text(messages: list[dict[str, str]], config: AppConfig, *,
                   usage_collector: Callable[..., None] | None,
                   max_attempts: int, generation_name: str = "conversation",
                   json_response: bool = False, reasoning_effort: Literal["high", "none"] | None = None,
                   response_schema: dict[str, Any] | None = None) -> str:
    """Share transport and accounting while keeping the two model roles explicit."""
    body: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
    }
    if response_schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "policy_factual_audit", "strict": True, "schema": response_schema},
        }
        # Require a schema-capable endpoint while retaining operator routing.
        body = _with_provider_routing(config, body)
        if config.llm_provider == "openrouter":
            body["provider"] = {**body.get("provider", {}), "require_parameters": True}
    elif json_response:
        body["response_format"] = {"type": "json_object"}
    _maybe_suppress_reasoning(body, config.model)
    if reasoning_effort is not None:
        # The factual task needs a bounded reasoning budget. Its private
        # reasoning is excluded from replies and never added to chat history.
        body["reasoning"] = {"effort": reasoning_effort, "exclude": True}
        body["include_reasoning"] = False

    with observability.generation(
        name=generation_name,
        model=config.model,
        input_messages=messages,
        model_parameters={"temperature": config.temperature, "max_tokens": config.max_tokens},
    ) as generation:
        try:
            if reasoning_effort is not None:
                # A per-task reasoning override must not alter defaults learned
                # for the same model's extraction or conversation requests.
                data = _post(config, body, max_attempts=max_attempts, learn_reasoning_defaults=False)
            else:
                data = _post(config, body) if max_attempts == 3 else _post(config, body, max_attempts=max_attempts)
        except Exception as exc:
            # Diagnostic traces use structured categories instead of provider text.
            error = (repr(model_failure(exc)) if generation_name in {"policy_voice_critic", "policy_factual_audit"}
                     else repr(exc))
            observability.finalize_generation(generation, error=error)
            raise
        # A completed response can contain billable usage even with an empty reply.
        if usage_collector is not None:
            usage = data.get("usage") or {}
            try:
                usage_collector(config.model, int(usage.get("prompt_tokens", 0) or 0),
                                int(usage.get("completion_tokens", 0) or 0), _cached_tokens(usage))
            except Exception:
                # Accounting cannot discard a successfully generated client reply.
                pass
        try:
            content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
        except (AttributeError, TypeError, IndexError, KeyError) as exc:
            failure = ModelFailure("invalid_response", "response", type(exc).__name__)
            observability.finalize_generation(generation, error=repr(failure),
                                              usage=observability.usage_from_openrouter(data.get("usage")))
            raise ModelRequestError("invalid model completion envelope", failure) from None
        if not content or not isinstance(content, str):
            kind: FailureKind = "empty_response" if not content else "invalid_response"
            failure = ModelFailure(kind, "response", "RuntimeError")
            observability.finalize_generation(generation, error=repr(failure),
                                              usage=observability.usage_from_openrouter(data.get("usage")))
            raise ModelRequestError("empty conversation content from model" if not content else
                                    "model completion content must be text", failure)
        observability.finalize_generation(
            generation,
            output=content,
            usage=observability.usage_from_openrouter(data.get("usage")),
        )
        return str(content)


# --------------------------------------------------------------------------- #
# Извлекатель
# --------------------------------------------------------------------------- #
def _strip_code_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_json(raw: str) -> dict[str, Any]:
    text = _strip_code_fence(raw)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("ответ извлекателя — не JSON-объект")
    return value


def _keep_model_fields(value: Any, model_cls: type) -> Any:
    """Отбрасывает безобидные лишние ключи модели, оставляя сам контракт строгим."""
    if not isinstance(value, dict):
        return value
    allowed = set(model_cls.model_fields)
    return {key: item for key, item in value.items() if key in allowed}


def _sanitize(value: dict[str, Any]) -> dict[str, Any]:
    clean = _keep_model_fields(value, FactExtraction)
    if isinstance(clean.get("fact_updates"), list):
        clean["fact_updates"] = [
            _keep_model_fields(item, FactUpdate) for item in clean["fact_updates"]
        ]
    return clean


def _parse_candidate_extraction(
    value: dict[str, Any],
    matrix: Matrix,
) -> FactCandidateExtraction:
    """Валидирует элементы независимо, сохраняя валидную часть batch.

    Невалидный кандидат не должен уничтожать остальные факты одного хода. При этом
    top-level контракт и лимит batch остаются строгими, а источник всегда назначает
    adapter, не модель.
    """
    raw_candidates = value.get("candidates")
    if not isinstance(raw_candidates, list):
        raise ValueError("поле candidates должно быть JSON-массивом")
    if len(raw_candidates) > 12:
        raise ValueError("число candidates превышает лимит 12")

    allowed = set(FactCandidate.model_fields) - {"source"}
    candidates: list[FactCandidate] = []
    issues: list[FactCandidateIssue] = []
    for index, raw_candidate in enumerate(raw_candidates):
        raw_path = (
            raw_candidate.get("path")
            if isinstance(raw_candidate, dict)
            else None
        )
        try:
            if not isinstance(raw_candidate, dict):
                raise ValueError("кандидат должен быть JSON-объектом")
            item = {
                key: item
                for key, item in raw_candidate.items()
                if key in allowed
                and not (
                    item is None
                    and key
                    in {
                        "operation",
                        "explicitness",
                        "precision",
                        "evidence_basis",
                        "subject",
                    }
                )
            }
            item["value"] = _repair_candidate_json_scalar(item, matrix)
            candidate = FactCandidate.model_validate(item).model_copy(
                update={"source": FactSource.USER_EXPLICIT}
            )
        except (ValidationError, ValueError) as exc:
            issues.append(
                FactCandidateIssue(
                    raw_index=index,
                    path=raw_path if isinstance(raw_path, str) else None,
                    code=CandidateIssueCode.INVALID_SCHEMA,
                    message=str(exc)[:500],
                )
            )
            continue
        candidates.append(candidate)

    raw_boundaries = value.get("boundaries", {})
    try:
        boundaries = SemanticBoundarySignals.model_validate(raw_boundaries)
    except ValidationError as exc:
        boundaries = SemanticBoundarySignals()
        issues.append(
            FactCandidateIssue(
                code=CandidateIssueCode.INVALID_SCHEMA,
                message=f"invalid semantic boundaries: {str(exc)[:450]}",
            )
        )

    filtered, policy_issues = apply_candidate_policies(
        candidates,
        matrix,
        boundaries=boundaries,
    )
    return FactCandidateExtraction(
        candidates=filtered,
        boundaries=boundaries,
        issues=[*issues, *policy_issues],
        entry_intent=_parse_entry_intent(value.get("entry_intent")),
    )


def _parse_entry_intent(raw: Any) -> EntryIntent | None:
    """Терпимо: мусор в поле равен его отсутствию — тогда решает словарь.

    Поле добавочное. Сломанное намерение не должно стоить хода фактов: факты
    и границы из того же ответа остаются в силе. Issue не пишется намеренно —
    у списка issues потолок 12, и лишняя запись могла бы уронить весь ответ.
    """
    if not isinstance(raw, str):
        return None
    try:
        return EntryIntent(raw.strip().casefold())
    except ValueError:
        return None


_PLAIN_NUMBER = re.compile(r"-?\d+(?:\.0+)?")
# Разряды через пробел/неразрывный пробел/запятую — обычное форматирование модели
# ("1 000 000", "1,000,000"), не догадка о смысле речи: каждая группа строго из
# трёх цифр, иначе это не разделитель разрядов, а что-то другое.
_GROUPED_NUMBER = re.compile(r"-?\d{1,3}(?:[  ,]\d{3})+(?:\.0+)?")


def _repair_candidate_json_scalar(
    candidate: dict[str, Any],
    matrix: Matrix,
) -> Any:
    """Строго исправляет `"false"`/`"600000"`/`"1 000 000"`, но не понимает живую речь."""
    value = candidate.get("value")
    operation = candidate.get("operation", "set")
    if operation == "clear" and isinstance(value, str):
        if value.strip().casefold() in {"null", "none"}:
            return None

    path = candidate.get("path")
    spec = matrix.fields.get(path) if isinstance(path, str) else None
    if spec is None or not isinstance(value, str):
        return value
    stripped = value.strip()
    if spec.kind == "bool":
        if stripped.casefold() == "true":
            return True
        if stripped.casefold() == "false":
            return False
        return value
    if spec.kind in {"money", "int"}:
        if _PLAIN_NUMBER.fullmatch(stripped):
            return int(float(stripped))
        if _GROUPED_NUMBER.fullmatch(stripped):
            digits_only = re.sub(r"[  ,]", "", stripped)
            return int(float(digits_only))
    return value


def extract_facts(
    payload: dict[str, Any],
    assistant_reply: str,
    config: AppConfig,
    usage_collector: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Разбор хода в факты. При синтаксическом сбое JSON — одна повторная попытка.

    Эмпирика прода v3: на длинных выходах часть ответов приходит с пропущенной запятой
    или незакрытой скобкой. Ремонт на уровне Pydantic тут не спасает — он работает уже
    с распарсенным словарём, поэтому повтор делается на уровне запроса.
    """
    model = config.extractor_model or config.model
    safe_payload = mask_payload_for_llm(payload, config.enable_pii_masking)
    base_content = (
        f"{_context_block(safe_payload)}\n\n"
        f"Недавняя история диалога:\n{_history_text(safe_payload)}\n\n"
        f"Последняя реплика клиента:\n{safe_payload.get('latest_user_message', '')}\n\n"
        f"Ответ оператора:\n{assistant_reply}\n\n"
        "Верни только JSON-объект с fact_updates и internal_summary."
    )

    last_error: Exception | None = None
    for attempt in range(1, EXTRACTOR_MAX_ATTEMPTS + 1):
        content = base_content if attempt == 1 else (
            base_content
            + "\n\nКРИТИЧНО: предыдущий ответ был синтаксически невалидным JSON. "
              "Верни СТРОГО валидный JSON-объект — без markdown-обёртки, без комментариев, "
              "со всеми запятыми. Никакого текста до или после JSON."
        )
        messages = [
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]
        try:
            return _extract_attempt(config, model, messages, usage_collector, attempt)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            last_error = exc
            if attempt >= EXTRACTOR_MAX_ATTEMPTS:
                break
        except RuntimeError as exc:
            last_error = exc
            if "empty extractor" not in str(exc) or attempt >= EXTRACTOR_MAX_ATTEMPTS:
                raise
    assert last_error is not None
    raise last_error


def _extractor_config(config: AppConfig) -> AppConfig:
    """Конфигурация с коротким потолком ожидания для извлечения фактов."""
    return replace(
        config,
        request_timeout=min(config.request_timeout, config.fact_shadow_timeout),
    )


def _extract_attempt(
    config: AppConfig,
    model: str,
    messages: list[dict[str, str]],
    usage_collector: Callable[..., None] | None,
    attempt: int,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
        "max_tokens": 900,
        "response_format": {"type": "json_object"},
    }
    _maybe_suppress_reasoning(body, model)

    name = "extraction" if attempt == 1 else f"extraction_retry_{attempt}"
    with observability.generation(
        name=name,
        model=model,
        input_messages=messages,
        model_parameters={"temperature": 0.0, "max_tokens": 900, "response_format": "json_object"},
    ) as generation:
        try:
            # Тот же потолок, что у семантического извлекателя. Без него legacy
            # наследовал разговорный timeout в 120 секунд: зависший провайдер
            # держал клиента перед пустым чатом две минуты, притом что ход всё
            # равно можно продолжить без извлечения — факты подберёт следующий.
            data = _post(_extractor_config(config), body, max_attempts=2)
        except Exception as exc:
            observability.finalize_generation(generation, error=repr(exc))
            raise
        usage = data.get("usage") or {}
        if usage_collector and usage:
            try:
                usage_collector(
                    model,
                    int(usage.get("prompt_tokens", 0) or 0),
                    int(usage.get("completion_tokens", 0) or 0),
                    _cached_tokens(usage),
                )
            except Exception:  # noqa: BLE001
                pass
        content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
        if not content:
            observability.finalize_generation(generation, error="empty extractor content")
            raise RuntimeError("empty extractor content from model")
        try:
            parsed = _extract_json(str(content))
            validated = FactExtraction.model_validate(_sanitize(parsed)).model_dump()
        except Exception as exc:
            observability.finalize_generation(
                generation, output=str(content), error=f"validate: {exc}"
            )
            raise
        observability.finalize_generation(
            generation, output=validated, usage=observability.usage_from_openrouter(usage)
        )
        return validated


def extract_fact_candidates(
    payload: dict[str, Any],
    config: AppConfig,
    usage_collector: Callable[..., None] | None = None,
    *,
    matrix: Matrix | None = None,
) -> FactCandidateExtraction:
    """Один fail-closed shadow-вызов semantic extractor.

    Функция не применяет кандидаты к state и не делает parse retry. Даже если модель
    вернула `source=dadata`, adapter принудительно заменяет его на user_explicit.
    """
    active_matrix = matrix or get_matrix()
    model = (
        config.fact_interpretation_model
        or config.extractor_model
        or config.model
    )
    safe_payload = mask_payload_for_llm(payload, config.enable_pii_masking)
    answer_shape = (
        '{"boundaries": {...}, "candidates": [...], "entry_intent": "..."}'
        if config.entry_routing_enabled
        else '{"boundaries": {...}, "candidates": [...]}'
    )
    content = (
        f"{_context_block(safe_payload)}\n\n"
        f"Недавняя история диалога:\n{_history_text(safe_payload)}\n\n"
        f"Последняя реплика клиента:\n"
        f"{safe_payload.get('latest_user_message', '')}\n\n"
        f"Верни только JSON-объект {answer_shape}."
    )
    messages = [
        {
            "role": "system",
            "content": build_interpretation_system_prompt(
                active_matrix,
                with_entry_intent=config.entry_routing_enabled,
            ),
        },
        {"role": "user", "content": content},
    ]
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
        "max_tokens": config.fact_shadow_max_tokens,
        "response_format": {"type": "json_object"},
    }
    _maybe_suppress_reasoning(body, model)

    # Shadow получает отдельный короткий timeout и не наследует долгий conversation
    # timeout. Это удерживает p95 даже при деградации экспериментальной модели.
    shadow_config = _extractor_config(config)
    with observability.generation(
        name="interpretation_shadow",
        model=model,
        input_messages=messages,
        model_parameters={
            "temperature": 0.0,
            "max_tokens": config.fact_shadow_max_tokens,
            "response_format": "json_object",
            "max_attempts": 1,
        },
    ) as generation:
        try:
            data = _post(shadow_config, body, max_attempts=1)
        except Exception as exc:
            observability.finalize_generation(generation, error=repr(exc))
            raise

        usage = data.get("usage") or {}
        if usage_collector and usage:
            try:
                usage_collector(
                    model,
                    int(usage.get("prompt_tokens", 0) or 0),
                    int(usage.get("completion_tokens", 0) or 0),
                    _cached_tokens(usage),
                )
            except Exception:  # noqa: BLE001 — usage не влияет на разговор
                pass

        raw_content = (
            ((data.get("choices") or [{}])[0].get("message") or {}).get(
                "content"
            )
        )
        if not raw_content:
            observability.finalize_generation(
                generation,
                error="empty interpretation shadow content",
            )
            raise RuntimeError("empty interpretation shadow content from model")

        try:
            parsed = _extract_json(str(raw_content))
            extraction = _parse_candidate_extraction(parsed, active_matrix)
        except Exception as exc:
            observability.finalize_generation(
                generation,
                output=str(raw_content),
                error=f"validate: {exc}",
            )
            raise

        observability.finalize_generation(
            generation,
            output=extraction.model_dump(mode="json"),
            usage=observability.usage_from_openrouter(usage),
        )
        return extraction
