"""
Учёт токенов и стоимости.

Подключается к llm_agent через usage_collector-колбэк (см. llm_agent.py). На каждый вызов
к OpenRouter (стрим разговорной модели и не-стрим извлекателя) ловим usage из ответа,
аккумулируем в SessionUsage и считаем стоимость по тарифной таблице PRICING.

PRICING выражено в USD за миллион токенов. Цифры — ориентировочные плейсхолдеры под
текущие тарифы OpenRouter; обнови, когда сверишься со страницей модели на openrouter.ai
(там цена видна прямо на карточке модели).
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Iterable


# USD за 1 000 000 токенов. Сверено по OpenRouter /api/v1/models 2026-06-03.
# "cached_input" — input_cache_read, ставка за чтение из кэша.
# "cache_write" — input_cache_write, справочно; OpenRouter usage в текущих ответах
# отдаёт cached_tokens/read, но не отдельные write-токены, поэтому расчёт ниже его не применяет.
# Если не задана — берём input (консервативно, не занижаем стоимость).
# Если модели нет в таблице — стоимость не считается, но токены всё равно копятся.
PRICING_PER_MILLION_TOKENS: dict[str, dict[str, float]] = {
    "qwen/qwen3.7-max":          {"input": 1.25,  "output": 3.75, "cached_input": 0.25,    "cache_write": 1.5625},
    "qwen/qwen3.6-plus":         {"input": 0.325, "output": 1.95},
    "anthropic/claude-opus-4.7": {"input": 5.00,  "output": 25.0, "cached_input": 0.50,    "cache_write": 6.25},
    "anthropic/claude-haiku-4.5":{"input": 1.00,  "output": 5.00, "cached_input": 0.10,    "cache_write": 1.25},
    "deepseek/deepseek-v4-pro":  {"input": 0.435, "output": 0.87, "cached_input": 0.003625},
    "deepseek/deepseek-v4-flash":{"input": 0.14,  "output": 0.28, "cached_input": 0.0014},
    "deepseek/deepseek-v4.1-flash":{"input": 0.10, "output": 0.60, "cached_input": 0.0025},
    "openai/gpt-5.4-mini":       {"input": 0.75,  "output": 4.50, "cached_input": 0.075},
}


@dataclass
class UsageEvent:
    model: str
    # "conversation" | "extraction" | "interpretation_shadow" | "opening" | "other"
    role: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    total_tokens: int
    cost_usd: float
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


_warned_models: set[str] = set()


def _warn_unknown_model(model: str) -> None:
    """Предупреждает один раз на модель: иначе зальёт лог на каждом вызове."""
    if model in _warned_models:
        return
    _warned_models.add(model)
    logging.getLogger(__name__).warning(
        "usage: у модели %s нет тарифа, расходы по ней считаются нулём. "
        "Добавьте её в PRICING_PER_MILLION_TOKENS.",
        model,
    )


def _compute_cost_usd(model: str, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> float:
    rates = PRICING_PER_MILLION_TOKENS.get(model)
    if not rates:
        # Молчаливый ноль однажды уже ввёл в заблуждение: прогон на новой модели
        # выглядел бесплатным просто потому, что её забыли добавить в таблицу.
        # Считать всё равно нечем, но знать об этом надо.
        _warn_unknown_model(model)
        return 0.0
    cached = max(0, min(int(cached_tokens or 0), int(prompt_tokens or 0)))
    fresh_input = int(prompt_tokens or 0) - cached
    cached_rate = rates.get("cached_input", rates["input"])
    return (
        fresh_input * rates["input"]
        + cached * cached_rate
        + int(completion_tokens or 0) * rates["output"]
    ) / 1_000_000


@dataclass
class SessionUsage:
    """Аккумулятор расхода для одной сессии."""
    session_id: str
    events: list[UsageEvent] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def add(self, model: str, role: str, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> UsageEvent:
        cost = _compute_cost_usd(model, prompt_tokens, completion_tokens, cached_tokens)
        event = UsageEvent(
            model=model,
            role=role,
            prompt_tokens=int(prompt_tokens or 0),
            completion_tokens=int(completion_tokens or 0),
            cached_tokens=int(cached_tokens or 0),
            total_tokens=int((prompt_tokens or 0) + (completion_tokens or 0)),
            cost_usd=cost,
        )
        with self._lock:
            self.events.append(event)
        return event

    def collector(self, role: str):
        """Возвращает callback под llm_agent (model, prompt_tokens, completion_tokens, cached_tokens)."""
        def _cb(model: str, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> None:
            self.add(model, role, prompt_tokens, completion_tokens, cached_tokens)
        return _cb

    def export_events(self) -> list[dict]:
        """Плоский список событий для хранилища сессий.

        Хранится сырой расход, а не свёртка: иначе после рестарта диалог
        продолжил бы копить поверх агрегата, и разобрать стоимость по вызовам
        стало бы невозможно.
        """
        with self._lock:
            events = list(self.events)
        return [
            {
                "model": ev.model,
                "role": ev.role,
                "prompt_tokens": ev.prompt_tokens,
                "completion_tokens": ev.completion_tokens,
                "cached_tokens": ev.cached_tokens,
                "total_tokens": ev.total_tokens,
                "cost_usd": ev.cost_usd,
                "at": ev.at.isoformat(),
            }
            for ev in events
        ]

    @classmethod
    def restore(cls, session_id: str, rows: Iterable[dict]) -> "SessionUsage":
        usage = cls(session_id=session_id)
        for row in rows:
            usage.events.append(
                UsageEvent(
                    model=row["model"],
                    role=row["role"],
                    prompt_tokens=int(row.get("prompt_tokens") or 0),
                    completion_tokens=int(row.get("completion_tokens") or 0),
                    cached_tokens=int(row.get("cached_tokens") or 0),
                    total_tokens=int(row.get("total_tokens") or 0),
                    cost_usd=float(row.get("cost_usd") or 0.0),
                    at=_parse_moment(row.get("at")),
                )
            )
        return usage

    def summary(self) -> dict:
        with self._lock:
            events = list(self.events)
        by_role: dict[str, dict] = {}
        by_model: dict[str, dict] = {}
        total_input = total_output = total_cached = 0
        total_cost = 0.0
        for ev in events:
            for bucket, key in ((by_role, ev.role), (by_model, ev.model)):
                slot = bucket.setdefault(key, {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "cost_usd": 0.0, "calls": 0})
                slot["prompt_tokens"] += ev.prompt_tokens
                slot["completion_tokens"] += ev.completion_tokens
                slot["cached_tokens"] += ev.cached_tokens
                slot["cost_usd"] += ev.cost_usd
                slot["calls"] += 1
            total_input += ev.prompt_tokens
            total_output += ev.completion_tokens
            total_cached += ev.cached_tokens
            total_cost += ev.cost_usd
        return {
            "session_id": self.session_id,
            "total_calls": len(events),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cached_tokens": total_cached,
            "cache_hit_ratio": round(total_cached / total_input, 3) if total_input else 0.0,
            "total_tokens": total_input + total_output,
            "total_cost_usd": round(total_cost, 6),
            "by_role": {k: _round_costs(v) for k, v in by_role.items()},
            "by_model": {k: _round_costs(v) for k, v in by_model.items()},
        }


def _parse_moment(raw: object) -> datetime:
    """Время события после round-trip. Битое значение не повод терять расход."""
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _round_costs(slot: dict) -> dict:
    slot = dict(slot)
    slot["cost_usd"] = round(slot["cost_usd"], 6)
    return slot


def aggregate(usages: Iterable[SessionUsage]) -> dict:
    """Свёртка по нескольким сессиям — для глобальных дашбордов."""
    total = {"sessions": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    for u in usages:
        s = u.summary()
        total["sessions"] += 1
        total["calls"] += s["total_calls"]
        total["input_tokens"] += s["total_input_tokens"]
        total["output_tokens"] += s["total_output_tokens"]
        total["cost_usd"] += s["total_cost_usd"]
    total["cost_usd"] = round(total["cost_usd"], 6)
    return total


# Разделители в имени модели: «qwen/qwen3.7-max». В имени переменной окружения
# ни один из них невозможен, там остаётся только подчёркивание.
_MODEL_SEPARATORS = re.compile(r"[_/.\-]+")


def _pricing_lookup_key(model: str) -> str:
    """Имя модели без разделителей и регистра — по нему и сверяемся.

    Регистр приходится игнорировать не для красоты: в Windows имена переменных
    окружения приводятся к верхнему, и на машине разработчика переопределение
    цены иначе адресует другую модель, чем на сервере.
    """
    return _MODEL_SEPARATORS.sub("_", model).lower()


# Глобальная функция для конфигурации PRICING из переменных окружения, если хочется
# обновлять цены без правок кода (опционально, можно не использовать).
def override_pricing_from_env(prefix: str = "PRICE_") -> None:
    """Формат: PRICE_qwen_qwen3_7_max_INPUT=3.0 / PRICE_qwen_qwen3_7_max_OUTPUT=9.0

    Имя модели в переменной пишется подчёркиваниями — ни точки, ни слэша в имени
    переменной быть не может. Обратно подчёркивание однозначно не разворачивается:
    «qwen_qwen3_7_max» это и `qwen/qwen3.7-max`, и `qwen/qwen3-7-max`. Поэтому
    сначала ищем модель в таблице, считая подчёркивание любым разделителем —
    пока модель известна, точка в её имени роли не играет.

    Прежнее правило (первое подчёркивание — слэш, остальные — дефисы) точку
    получить не могло вовсе: цена уходила во вновь заведённую несуществующую
    модель, настоящая оставалась со старой, и расхождение вылезало не в логах,
    а на счёте от провайдера. Для по-настоящему новой модели это правило и
    осталось запасным.
    """
    known = {_pricing_lookup_key(model): model for model in PRICING_PER_MILLION_TOKENS}
    for key, value in os.environ.items():
        if not key.startswith(prefix) or not (key.endswith("_INPUT") or key.endswith("_OUTPUT")):
            continue
        try:
            direction = "input" if key.endswith("_INPUT") else "output"
            model_key = key[len(prefix):-len("_INPUT") if direction == "input" else -len("_OUTPUT")]
            model = known.get(_pricing_lookup_key(model_key))
            if model is None:
                # PRICE_vendor_new_model → "vendor/new-model"
                model = model_key.replace("_", "/", 1).replace("_", "-").lower()
                known[_pricing_lookup_key(model)] = model
            PRICING_PER_MILLION_TOKENS.setdefault(model, {"input": 0.0, "output": 0.0})
            PRICING_PER_MILLION_TOKENS[model][direction] = float(value)
        except (ValueError, IndexError):
            continue
