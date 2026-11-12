"""Контракты обмена с моделью.

В v4 модель больше не решает, что клиенту подходит, — это делает `routing/`. Поэтому
от большого `StateUpdate` из v3 остался только разбор фактов: маршрут, фаза диалога и
статусы полей теперь вычисляются детерминированно и в контракте не нужны.

Контракт строгий (`extra="forbid"`) намеренно: заказчику была обещана гарантия, что
данные не теряются и не подменяются. Ремонт мелких отклонений модели делает явный
слой-адаптер в `llm_agent`, а не молчаливое проглатывание лишних ключей здесь.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


FactValue = str | int | float | bool | None


class FactUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    value: FactValue
    confidence: Literal["high", "medium", "low"] = "high"


class FactExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_updates: list[FactUpdate] = Field(default_factory=list)
    internal_summary: str = ""
