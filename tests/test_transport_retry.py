"""Транспортные сбои к OpenRouter должны ретраиться, а не ронять ход.

`_post` ловил `URLError`, но обрыв соединения приходит как `RemoteDisconnected`
(это `ConnectionResetError`, то есть `OSError`) и проходил мимо retry. Тем же
путём ходит извлекатель фактов, поэтому один разорванный коннект ронял весь ход
диалога. Обнаружено на ролевых прогонах 11.08.2026.
"""
from __future__ import annotations

import http.client
import json
from dataclasses import replace

import pytest

import llm_agent
from config import AppConfig


CONFIG = AppConfig(openrouter_api_key="sk-or-test")


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


def test_dropped_connection_is_retried(monkeypatch):
    calls = {"n": 0}

    def _flaky(config, request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise http.client.RemoteDisconnected(
                "Remote end closed connection without response"
            )
        return _Response({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(llm_agent, "_urlopen", _flaky)
    monkeypatch.setattr(llm_agent.time, "sleep", lambda _: None)

    result = llm_agent._post(CONFIG, {"model": "test", "messages": []})

    assert calls["n"] == 2
    assert result["choices"][0]["message"]["content"] == "ok"


def test_retries_are_bounded(monkeypatch):
    """Бесконечный ретрай хуже ошибки: клиент ждёт ответа."""
    calls = {"n": 0}

    def _always_broken(config, request):
        calls["n"] += 1
        raise ConnectionResetError("сброшено")

    monkeypatch.setattr(llm_agent, "_urlopen", _always_broken)
    monkeypatch.setattr(llm_agent.time, "sleep", lambda _: None)

    try:
        llm_agent._post(CONFIG, {"model": "test", "messages": []}, max_attempts=2)
    except RuntimeError:
        pass
    else:  # pragma: no cover — тест обязан увидеть ошибку
        raise AssertionError("ожидалась RuntimeError после исчерпания попыток")

    assert calls["n"] == 2


def test_nonstream_http_failure_exposes_safe_metadata_without_provider_body_or_chain(monkeypatch):
    import io
    import urllib.error

    private = "SYNTHETIC_PRIVATE_TRANSPORT_DETAIL"

    def refused(configuration, request):
        raise urllib.error.HTTPError("https://synthetic.invalid/" + private, 503, private,
                                     {"private": private}, io.BytesIO(private.encode()))

    monkeypatch.setattr(llm_agent, "_urlopen", refused)
    with pytest.raises(llm_agent.ModelRequestError) as caught:
        llm_agent._post(CONFIG, {"model": CONFIG.model, "messages": []}, max_attempts=1)
    assert caught.value.failure == llm_agent.ModelFailure("http", "open", "HTTPError", 503, True)
    assert private not in str(caught.value) and private not in repr(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_empty_completed_critic_response_still_accounts_for_provider_usage(monkeypatch):
    usage = []
    calls = []

    def completed(configuration, body, **kwargs):
        calls.append(kwargs)
        return {"choices": [{"message": {"content": ""}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3}}

    monkeypatch.setattr(llm_agent, "_post", completed)
    with pytest.raises(llm_agent.ModelRequestError) as caught:
        llm_agent.generate_policy_critique([], CONFIG, usage_collector=lambda *args: usage.append(args))
    assert caught.value.failure.kind == "empty_response"
    assert usage == [(CONFIG.model, 12, 3, 0)]
    assert calls == [{"max_attempts": 1}]


def test_stream_reports_why_the_provider_refused(monkeypatch):
    """403 без тела ответа диагностировать нечем.

    18.08.2026 на сервере МБК разговор падал с голым «HTTP Error 403:
    Forbidden». По такому сообщению неотличимы заблокированный адрес сервера,
    чужой ключ и запрещённая провайдером страна — а причина всё это время
    лежала в теле ответа. Non-stream ветка его читала, streaming молча теряла,
    то есть хуже всего диагностировался разговор с живым клиентом.
    """
    import io
    import urllib.error

    body = json.dumps(
        {"error": {"message": "Country, region, or territory not supported"}}
    ).encode("utf-8")

    def _forbidden(config, request):
        raise urllib.error.HTTPError(
            request.full_url, 403, "Forbidden", {}, io.BytesIO(body)
        )

    monkeypatch.setattr(llm_agent, "_urlopen", _forbidden)

    with pytest.raises(RuntimeError) as exc:
        list(llm_agent._post_stream(CONFIG, {"model": "test", "messages": []}))

    assert "403" in str(exc.value)
    assert "Country, region, or territory not supported" in str(exc.value)


def _completion(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


def test_reasoning_model_is_recognised_by_its_empty_answer(monkeypatch):
    """Список рассуждающих моделей был захардкожен по именам.

    Модель, которой в списке нет, отправляет весь вывод в рассуждение и
    возвращает пустой ответ — бот падал молча. Так вели себя qwen3.7-flash и
    glm-4.7-flash на замерах 19.08.2026: дело было в нас, не в них. Теперь
    пустой ответ — сам по себе признак, и вторая попытка идёт с подавлением.
    """
    monkeypatch.setattr(llm_agent, "_LEARNED_REASONING", set())
    seen = []

    def _fake(config, request):
        body = json.loads(request.data)
        seen.append("reasoning" in body)
        return _Response(_completion("" if len(seen) == 1 else "готово"))

    monkeypatch.setattr(llm_agent, "_urlopen", _fake)

    data = llm_agent._post(CONFIG, {"model": "vendor/unknown-thinker", "messages": []})

    assert seen == [False, True], "вторая попытка должна подавлять рассуждение"
    assert data["choices"][0]["message"]["content"] == "готово"
    assert "vendor/unknown-thinker" in llm_agent._LEARNED_REASONING


def test_model_that_rejects_the_parameter_is_retried_without_it(monkeypatch):
    """minimax отвечает HTTP 400 на сам параметр подавления.

    Без этой памяти такая модель недоступна в принципе: каждая попытка
    заканчивалась той же ошибкой.
    """
    import io as _io
    import urllib.error

    monkeypatch.setattr(llm_agent, "_REJECTS_REASONING_PARAM", set())
    monkeypatch.setattr(llm_agent, "_LEARNED_REASONING", {"vendor/picky"})
    seen = []

    def _fake(config, request):
        body = json.loads(request.data)
        seen.append("reasoning" in body)
        if seen[-1]:
            raise urllib.error.HTTPError(
                request.full_url, 400, "Bad Request", {},
                _io.BytesIO(b'{"error":{"message":"Reasoning is not supported"}}'),
            )
        return _Response(_completion("готово"))

    monkeypatch.setattr(llm_agent, "_urlopen", _fake)

    payload = {"model": "vendor/picky", "messages": []}
    llm_agent._maybe_suppress_reasoning(payload, "vendor/picky")
    data = llm_agent._post(CONFIG, payload)

    assert seen == [True, False], "вторая попытка должна идти без параметра"
    assert data["choices"][0]["message"]["content"] == "готово"


def test_a_genuinely_empty_answer_is_not_retried_forever(monkeypatch):
    """Известная рассуждающая модель молчит — это не повод крутить попытки."""
    monkeypatch.setattr(llm_agent, "_LEARNED_REASONING", set())
    calls = {"n": 0}

    def _fake(config, request):
        calls["n"] += 1
        return _Response(_completion(""))

    monkeypatch.setattr(llm_agent, "_urlopen", _fake)

    data = llm_agent._post(CONFIG, {"model": "deepseek/deepseek-v4-pro", "messages": []})

    assert calls["n"] == 1
    assert data["choices"][0]["message"]["content"] == ""


def test_extractor_does_not_inherit_the_long_conversation_timeout(monkeypatch):
    """Зависший провайдер держал клиента две минуты.

    Разговорный timeout — 120 секунд, и legacy-извлекатель наследовал его
    целиком. Между тем ход можно продолжить и без извлечения: факты подберёт
    следующий ход, а вот две минуты перед пустым чатом клиент не переживёт.
    """
    from dataclasses import replace as _replace

    config = _replace(CONFIG, request_timeout=120, fact_shadow_timeout=30)
    seen = []

    def _fake(cfg, request):
        seen.append(cfg.request_timeout)
        return _Response(_completion('{"fact_updates": [], "internal_summary": ""}'))

    monkeypatch.setattr(llm_agent, "_urlopen", _fake)
    llm_agent.extract_facts({"facts": {}, "short_history": []}, "", config)

    assert len(seen) == 1
    assert 0 < seen[0] <= 30, "извлекатель получает остаток своего общего срока"


# --------------------------------------------------------------------------- #
# Стрим: повтор допустим ровно до первого показанного клиенту куска
# --------------------------------------------------------------------------- #
class _StreamResponse:
    """SSE-ответ провайдера; при желании обрывается на заданной строке."""

    def __init__(self, pieces: list[str], break_at: int | None = None) -> None:
        self._lines = [
            b"data: " + json.dumps({"choices": [{"delta": {"content": piece}}]}).encode()
            for piece in pieces
        ]
        self._lines.append(b"data: [DONE]")
        self._break_at = break_at
        self.closed = False

    def __iter__(self):
        for index, line in enumerate(self._lines):
            if self._break_at is not None and index == self._break_at:
                raise http.client.IncompleteRead(b"")
            yield line

    def read(self) -> bytes:
        return b""

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        self.closed = True
        return False

    def close(self) -> None:
        self.closed = True


def _http_error(request, code: int, body: bytes):
    import io
    import urllib.error

    return urllib.error.HTTPError(request.full_url, code, "boom", {}, io.BytesIO(body))


def test_stream_retries_provider_failure_before_the_first_chunk(monkeypatch):
    """502 до первого куска — клиент ещё ничего не видел, повтор безопасен.

    Молчаливый извлекатель тот же сбой переживал (`_post`, три попытки), а
    разговор с живым клиентом падал с первого раза: пул поднимает 502 уже как
    `HTTPError`, после получения ответа, и повторять его было некому.
    """
    calls = {"n": 0}

    def _flaky(config, request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(request, 502, b'{"error":"upstream"}')
        return _StreamResponse(["Здравствуйте, ", "чем помочь?"])

    monkeypatch.setattr(llm_agent, "_urlopen", _flaky)
    monkeypatch.setattr(llm_agent.time, "sleep", lambda _: None)

    pieces = list(llm_agent._post_stream(CONFIG, {"model": "test", "messages": []}))

    assert calls["n"] == 2
    assert "".join(pieces) == "Здравствуйте, чем помочь?"


def test_stream_retries_a_connection_dropped_before_the_first_chunk(monkeypatch):
    """Обрыв на свежем соединении: пул переоткрывает только протухшее из пула."""
    calls = {"n": 0}

    def _flaky(config, request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise http.client.RemoteDisconnected("Remote end closed connection")
        return _StreamResponse(["готово"])

    monkeypatch.setattr(llm_agent, "_urlopen", _flaky)
    monkeypatch.setattr(llm_agent.time, "sleep", lambda _: None)

    assert list(llm_agent._post_stream(CONFIG, {"model": "test", "messages": []})) == [
        "готово"
    ]
    assert calls["n"] == 2


def test_stream_never_replays_what_the_client_already_saw(monkeypatch):
    """Обрыв в середине повторять нельзя: начало ответа уже в чате.

    Повтор дал бы клиенту второе «Здравствуйте» посреди фразы. Граница ретрая —
    первый отданный кусок, и считать его надо здесь: наружу, в `_stream`, куски
    уже ушли через yield, и «показали или нет» там уже не различить.
    """
    calls = {"n": 0}

    def _breaks_midway(config, request):
        calls["n"] += 1
        return _StreamResponse(["Здравствуйте, ", "чем помочь?"], break_at=1)

    monkeypatch.setattr(llm_agent, "_urlopen", _breaks_midway)
    monkeypatch.setattr(llm_agent.time, "sleep", lambda _: None)

    seen: list[str] = []
    with pytest.raises(Exception):
        for piece in llm_agent._post_stream(CONFIG, {"model": "test", "messages": []}):
            seen.append(piece)

    assert seen == ["Здравствуйте, "]
    assert calls["n"] == 1, "повтор после показанного куска удвоил бы ответ"


def test_stream_does_not_retry_what_a_repeat_cannot_fix(monkeypatch):
    """401 — не транспортный сбой: повтор только задержит клиента."""
    calls = {"n": 0}

    def _rejected(config, request):
        calls["n"] += 1
        raise _http_error(request, 401, b'{"error":"bad key"}')

    monkeypatch.setattr(llm_agent, "_urlopen", _rejected)
    monkeypatch.setattr(llm_agent.time, "sleep", lambda _: None)

    with pytest.raises(RuntimeError):
        list(llm_agent._post_stream(CONFIG, {"model": "test", "messages": []}))

    assert calls["n"] == 1


def test_stream_retries_are_bounded_by_the_same_timeout(monkeypatch):
    """Живой клиент ждёт перед пустым чатом, поэтому повторы укладываются в тот
    же потолок ожидания, что и одна попытка: 120 секунд молчания и так предел."""
    calls = {"n": 0}

    def _always_broken(config, request):
        calls["n"] += 1
        raise ConnectionResetError("сброшено")

    monkeypatch.setattr(llm_agent, "_urlopen", _always_broken)
    monkeypatch.setattr(llm_agent.time, "sleep", lambda _: None)

    with pytest.raises(RuntimeError):
        list(llm_agent._post_stream(CONFIG, {"model": "test", "messages": []}))
    assert calls["n"] == llm_agent.STREAM_MAX_ATTEMPTS

    # Потолок ожидания уже выбран первой попыткой — второй не будет.
    calls["n"] = 0
    exhausted = replace(CONFIG, request_timeout=0)
    with pytest.raises(RuntimeError):
        list(llm_agent._post_stream(exhausted, {"model": "test", "messages": []}))
    assert calls["n"] == 1
