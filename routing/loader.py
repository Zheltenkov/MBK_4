"""Загрузка матрицы из YAML с валидацией и явными предупреждениями."""
from __future__ import annotations

import csv
import logging
from functools import lru_cache
from pathlib import Path

import yaml

from .schema import Matrix


logger = logging.getLogger("mbk.routing")

MATRIX_PATH = Path(__file__).resolve().parent / "matrix.yaml"


def load_matrix(path: Path | str | None = None) -> Matrix:
    """Читает и валидирует матрицу. Маршруты, зависящие от незаполненного справочника
    регионов, выключаются — с предупреждением в лог, а не молча."""
    source = Path(path) if path else MATRIX_PATH
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    _load_region_sources(raw, source)
    matrix = Matrix.model_validate(raw)

    _validate_field_references(matrix)

    for route in matrix.routes:
        list_name = route.requires_region_list
        if list_name and not matrix.regions.get(list_name):
            route.enabled = False
            logger.warning(
                "Маршрут %s (%s) выключен: справочник регионов «%s» пуст. %s",
                route.id, route.priority, list_name, route.todo or "",
            )
    return matrix


def _load_region_sources(raw: dict, matrix_path: Path) -> None:
    """Подмешивает CSV whitelist в `regions` как пары `регион<TAB>город`.

    Исходный файл содержит два разных листа, и формат у них разный. «Кредит.Клаб» —
    пары «город + регион», где проверять нужно именно пару. «МБК» — только города,
    без региона: пустая часть региона означает «в любом регионе».
    """
    sources = raw.get("region_sources") or {}
    regions = raw.setdefault("regions", {})
    base_dir = matrix_path.resolve().parent

    for list_name, relative_path in sources.items():
        source_path = (base_dir / str(relative_path)).resolve()
        if not source_path.is_relative_to(base_dir):
            raise ValueError(f"Справочник {list_name!r} выходит за каталог routing")
        if not source_path.is_file():
            raise ValueError(f"Справочник {list_name!r} не найден: {source_path}")

        pairs: list[str] = []
        seen: set[str] = set()
        with source_path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.reader(stream):
                if not row:
                    continue
                city = row[0].strip()
                region = row[1].strip() if len(row) > 1 else ""
                if not city or city.casefold() == "город":
                    continue
                pair = f"{region}\t{city}"
                key = pair.casefold()
                if key not in seen:
                    seen.add(key)
                    pairs.append(pair)
        regions[list_name] = pairs


def _validate_field_references(matrix: Matrix) -> None:
    """Условие не должно ссылаться на поле, которого нет в описании полей —
    иначе бот не сможет его спросить и маршрут навсегда останется UNKNOWN."""
    known = set(matrix.fields)
    problems: list[str] = []
    for route in matrix.routes:
        for cond in route.conditions:
            if cond.field not in known:
                problems.append(f"{route.id}: условие ссылается на неизвестное поле «{cond.field}»")
    for name, spec in matrix.fields.items():
        if spec.after and spec.after not in known:
            problems.append(f"поле {name}: after ссылается на неизвестное поле «{spec.after}»")
        if spec.after_in is not None:
            if not spec.after:
                problems.append(f"поле {name}: after_in задан без after")
            else:
                parent_values = matrix.fields.get(spec.after)
                allowed = set(parent_values.values or []) if parent_values else set()
                unknown = sorted(set(spec.after_in) - allowed)
                if unknown:
                    problems.append(
                        f"поле {name}: after_in содержит значения вне «{spec.after}»: {unknown}"
                    )

    for index, step in enumerate(matrix.script):
        for path in step.ask:
            if path not in known:
                problems.append(
                    f"сценарий, шаг {index + 1}: неизвестное поле «{path}»"
                )
    if problems:
        raise ValueError("Матрица несогласованна:\n  " + "\n  ".join(problems))


@lru_cache(maxsize=1)
def get_matrix() -> Matrix:
    """Кэшированная матрица для рантайма. В тестах зови load_matrix() напрямую."""
    return load_matrix()
