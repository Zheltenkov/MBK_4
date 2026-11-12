"""Контракты матрицы маршрутов.

Матрица — данные (`matrix.yaml`), а не код. Здесь только её схема: что такое маршрут,
условие и поле. Всё, что бизнес может захотеть поменять (пороги, приоритеты, набор
маршрутов), живёт в YAML и не требует правки Python.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Op = Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "between"]

FieldKind = Literal["money", "int", "bool", "enum", "text"]
RouteFamily = Literal["realty", "debt_relief", "vehicle", "consumer"]


class Condition(BaseModel):
    """Одно условие маршрута — одна ячейка строки в исходной таблице."""

    model_config = ConfigDict(extra="forbid")

    field: str
    op: Op
    value: Any = None
    # «Пустое поле» в списке допустимых значений таблицы: незаполненный факт условие
    # НЕ нарушает. Без этого флага пустой факт делает маршрут UNKNOWN и бот идёт его спрашивать.
    allow_empty: bool = False
    note: str | None = None

    @field_validator("value")
    @classmethod
    def _check_value(cls, value: Any, info) -> Any:
        op = info.data.get("op")
        if op == "between" and (not isinstance(value, list) or len(value) != 2):
            raise ValueError("op=between требует value=[min, max]")
        if op == "in" and not isinstance(value, list):
            raise ValueError("op=in требует value=[...]")
        return value


class Route(BaseModel):
    """Строка матрицы: продукт + сегмент + набор условий + приоритет."""

    model_config = ConfigDict(extra="forbid")

    id: str
    priority: str
    product: str
    segment: str
    # Семейство нужно intent-aware планировщику. Это не решение модели и не
    # дополнительное условие маршрута: клиентское намерение только ограничивает
    # уместные ветки разговора.
    family: RouteFamily
    destination: str | None = None
    category: str | None = None
    landing: str | None = None
    conditions: list[Condition] = Field(default_factory=list)
    # Дополнительные поля после совпадения основных условий, но до сбора контактов.
    # Это точка расширения для BFL-хвоста, состав которого заказчик пока не утвердил.
    post_match_fields: list[str] = Field(default_factory=list)
    # Имя справочника регионов, без которого маршрут нельзя вычислить (колонка «Список»).
    # Пока справочник пуст — маршрут выключается загрузчиком с предупреждением.
    requires_region_list: str | None = None
    enabled: bool = True
    todo: str | None = None

    @property
    def priority_key(self) -> tuple[int, int]:
        """«1-1» → (1, 1). Меньше — важнее."""
        major, _, minor = self.priority.partition("-")
        return int(major), int(minor or 0)


class QuickReply(BaseModel):
    """Кнопка быстрого ответа под вопросом анкеты.

    `label` — что видит человек, `value` — что уходит боту при нажатии обычным
    сообщением. Значение обязано разбираться быстрым разбором без модели —
    это держит tests/test_matrix_quick_replies.py.
    """

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    value: str = Field(min_length=1)


class FieldSpec(BaseModel):
    """Метаданные поля: как его назвать человеку и когда его вообще уместно спрашивать."""

    model_config = ConfigDict(extra="forbid")

    label: str
    kind: FieldKind
    # Подсказка разговорному слою: что именно выяснить. Не готовая реплика — LLM формулирует сам.
    ask: str
    # Зачем мы это спрашиваем. Даёт модели материал объяснить причину клиенту вместо
    # «назовите возраст» — тот самый приём «вопрос + пояснение, зачем» с созвона.
    why: str | None = None
    values: list[str] | None = None
    # Предусловие: не спрашивать, пока это поле не заполнено (и не равно false).
    # Так «какой тип недвижимости» не уедет в один ход с «есть ли недвижимость».
    after: str | None = None
    # Сужение предусловия до конкретных значений родителя. Нужно там, где родитель —
    # enum: «безработный» это непустая строка, поэтому одного `after` мало, иначе
    # безработного спросят про официальный доход.
    after_in: list[str] | None = None
    # Базовый слой: нужен большинству приоритетных маршрутов, спрашивается до ветвления.
    common: bool = False
    # Поле нужно установить до выбора ветки. Это стартовый контактный блок и цель
    # обращения: без них планировщик не начинает квалификацию.
    initial: bool = False
    # Поле контактных данных: нужно перед обращением в микросервис.
    contact: bool = False
    # Можно не спрашивать, если все маршруты, которым это поле нужно, уже отпали.
    # Флаг ставится точечно: без него правило срезает и опорные поля анкеты, а
    # лид уезжает партнёру без сведений, которые тот ждёт независимо от маршрута.
    skip_when_settled: bool = False
    # Возможность отложить отдельное контактное поле до найденного решения. В текущей
    # матрице не используется: по уточнению заказчика имя и телефон собираются в начале.
    deferred: bool = False
    # Кнопки быстрого ответа под этим вопросом (спека 2026-09-14-quick-replies-
    # questionnaire-design.md). Только у закрытых полей и не больше четырёх:
    # виджет больше не рисует, а свободный ответ кнопками не выразить.
    quick_replies: list[QuickReply] | None = None

    @model_validator(mode="after")
    def _check_quick_replies(self) -> "FieldSpec":
        if not self.quick_replies:
            return self
        if self.kind not in {"bool", "enum"}:
            raise ValueError("quick_replies допустимы только у полей bool и enum")
        if len(self.quick_replies) > 4:
            raise ValueError("quick_replies: не больше четырёх кнопок")
        return self


class ScriptStep(BaseModel):
    """Один шаг анкеты: что спросить и какой фразой сгладить переход к нему.

    Порядок шагов и есть весь порядок опроса. Ветвление сюда не пишется — оно
    выражено `after`/`after_in` у самих полей, поэтому шаг с закрытым предусловием
    просто пропускается.
    """

    model_config = ConfigDict(extra="forbid")

    ask: list[str] = Field(min_length=1)
    # Дословная фраза заказчика перед этим шагом. Её подставляет backend, а не
    # модель: формулировки продиктованы и должны звучать одинаково всем клиентам.
    transition: str | None = None
    note: str | None = None


class Matrix(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    regions: dict[str, list[str]] = Field(default_factory=dict)
    # Справочники пар «регион + населённый пункт» можно хранить отдельными CSV,
    # чтобы не раздувать YAML и обновлять бизнес-список без правки Python.
    region_sources: dict[str, str] = Field(default_factory=dict)
    fields: dict[str, FieldSpec] = Field(default_factory=dict)
    # Фиксированная анкета. Пустой сценарий допустим только для тестовых матриц.
    script: list[ScriptStep] = Field(default_factory=list)
    routes: list[Route] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_field_references(self) -> "Matrix":
        """Не даёт ошибке в data-конфигурации проявиться посреди клиентского диалога."""
        known_fields = set(self.fields)
        for route in self.routes:
            referenced = {condition.field for condition in route.conditions}
            referenced.update(route.post_match_fields)
            unknown = sorted(referenced - known_fields)
            if unknown:
                raise ValueError(
                    f"маршрут {route.id!r} ссылается на неизвестные поля: {unknown}"
                )
        return self

    def sorted_routes(self) -> list[Route]:
        return sorted(self.routes, key=lambda r: r.priority_key)
