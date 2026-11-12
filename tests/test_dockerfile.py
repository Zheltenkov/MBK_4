"""Образ должен содержать весь код, который импортирует приложение.

Dockerfile копирует runtime поимённым allowlist'ом — это осознанное решение
(в образ не должны попадать эвалы, legacy UI и локальные скрипты). Плата за него
в том, что новый пакет легко забыть, и тогда контейнер падает не на сборке, а на
старте: 29.07.2026 так уехал релиз без `analytics/`, backend встал в
Restarting, а прод пришлось поднимать откатом.

Тест проходит по импортам от точки входа и требует, чтобы каждый локальный
модуль был в Dockerfile.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
ENTRYPOINT = "main"

# Не рантайм: в образ не попадают намеренно.
NON_RUNTIME = {"tests", "evaluation", "frontend", "analytics_demo"}


def _local_modules() -> set[str]:
    """Модули и пакеты верхнего уровня, которые есть в репозитории."""
    names = {path.stem for path in ROOT.glob("*.py")}
    names |= {
        path.name
        for path in ROOT.iterdir()
        if path.is_dir() and (path / "__init__.py").exists()
    }
    return names - NON_RUNTIME


def _copied_paths() -> set[str]:
    """Что Dockerfile действительно кладёт в образ."""
    copied: set[str] = set()
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*COPY\s+(\S+)\s", line)
        if not match:
            continue
        source = match.group(1).rstrip("/")
        copied.add(source.removesuffix(".py"))
    return copied


def _imports_of(module: str, local: set[str]) -> set[str]:
    """Локальные импорты одного модуля или пакета."""
    candidates = [ROOT / f"{module}.py", ROOT / module / "__init__.py"]
    source_files = [path for path in candidates if path.exists()]
    package_dir = ROOT / module
    if package_dir.is_dir():
        source_files.extend(package_dir.rglob("*.py"))

    found: set[str] = set()
    for path in source_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return found & local


def _runtime_modules() -> set[str]:
    """Транзитивное замыкание импортов от точки входа."""
    local = _local_modules()
    seen: set[str] = set()
    queue = [ENTRYPOINT]
    while queue:
        module = queue.pop()
        if module in seen:
            continue
        seen.add(module)
        queue.extend(_imports_of(module, local) - seen)
    return seen


def test_dockerfile_copies_every_module_the_app_imports():
    missing = sorted(_runtime_modules() - _copied_paths())

    assert not missing, (
        "Dockerfile не копирует модули, которые импортирует приложение: "
        + ", ".join(missing)
        + ". Контейнер соберётся, но упадёт на старте с ModuleNotFoundError."
    )


def test_dockerfile_does_not_ship_evals_or_tests():
    """Обратная сторона allowlist'а: он не должен незаметно расшириться."""
    copied = _copied_paths()

    assert "tests" not in copied
    assert "evaluation" not in copied
