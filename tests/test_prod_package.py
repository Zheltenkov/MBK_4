"""Пакет для передачи: один compose-файл, который поднимает всё сам.

Наш собственный стенд собран из двух стеков (edge с Caddy и цветное приложение)
ради выкатки без простоя. Разработчику МБК эта конструкция не нужна и вредна:
у него перед ботом стоит их сайт и их nginx, TLS держат они, а сине-зелёный
цвет ему нечем переключать. Поэтому для передачи есть отдельный самодостаточный
стек, и эти тесты стерегут то, что в нём легко сломать молча.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")


ROOT = Path(__file__).resolve().parents[1]
PROD_DIR = ROOT / "deploy" / "prod"
COMPOSE = PROD_DIR / "docker-compose.yml"
ENV_TEMPLATE = PROD_DIR / "env.example"
MAIN_ENV_TEMPLATE = ROOT / ".env.example"
NGINX_CONF = ROOT / "frontend" / "nginx.conf"

# Без этих значений бот либо не стартует, либо стартует и делает не то:
# пустой MBK_AUTH_PASSWORD — это ещё и ключ подписи разрешения на сессию.
REQUIRED_IN_TEMPLATE = {
    "OPEN_ROUTER_API_KEY",
    "MBK_AUTH_USERNAME",
    "MBK_AUTH_PASSWORD",
    "MBK_PUBLIC_CHAT",
    "MBK_TRUSTED_PROXY_HOPS",
    "MBK_PG_PASSWORD",
    "MBK_CRM_BASE_URL",
    "MBK_CRM_WORKFLOW_ENABLED",
    "MBK_CRM_LEAD_INTAKE_ENABLED",
    "MBK_CRM_SUBMISSION_MODE",
    "MBK_DADATA_API",
    "MBK_ROUTER_MOCK",
}


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*([A-Z][A-Z0-9_]*)=", line)
        if match:
            keys.add(match.group(1))
    return keys


def _schema_migrations() -> set[str]:
    """Миграции, которые накатываются без параметров.

    002_analytics_readonly_role.sql принимает пароль через `-v`, поэтому
    автоматически его накатить нельзя и он в набор не входит.
    """
    found: set[str] = set()
    for directory in ("analytics/migrations", "storage/migrations"):
        for path in sorted((ROOT / directory).glob("*.sql")):
            if ":'" in path.read_text(encoding="utf-8"):
                continue
            found.add(f"{directory}/{path.name}")
    return found


def test_prod_stack_brings_up_everything_the_bot_needs():
    services = _compose()["services"]

    assert {"postgres", "migrate", "backend", "frontend"} <= set(services), (
        "В стеке передачи не хватает сервиса. Разработчик МБК запускает одну "
        "команду и получает рабочего бота — доустанавливать что-то руками он "
        "не должен."
    )


def test_database_is_never_published_beyond_the_loopback():
    """5432 наружу — это база с персональными данными в открытом интернете."""
    for published in _compose()["services"]["postgres"].get("ports", []):
        assert str(published).startswith("127.0.0.1:"), (
            f"Порт базы {published} опубликован не только на петлю. "
            "Дата-инженер ходит SSH-туннелем, наружу 5432 не нужен никому."
        )


def test_chat_is_published_only_to_the_loopback():
    """Наружу смотрит их сайт, а не наш контейнер: TLS и вход — на их стороне."""
    for published in _compose()["services"]["frontend"].get("ports", []):
        assert str(published).startswith("127.0.0.1:"), (
            f"Порт чата {published} доступен снаружи в обход их nginx: "
            "клиент попадёт к боту, минуя авторизацию МБК."
        )


def test_backend_waits_for_the_database_and_the_migrations():
    backend = _compose()["services"]["backend"]
    conditions = {
        name: spec.get("condition")
        for name, spec in (backend.get("depends_on") or {}).items()
    }

    assert conditions.get("postgres") == "service_healthy"
    assert conditions.get("migrate") == "service_completed_successfully", (
        "Backend стартует до наката миграций: первая же сессия упадёт на "
        "отсутствующей таблице, и сохранится она только в памяти."
    )


def test_every_schema_migration_is_mounted_into_the_migrator():
    mounted = {
        str(volume).split(":")[0].removeprefix("../../")
        for volume in _compose()["services"]["migrate"].get("volumes", [])
    }
    missing = sorted(_schema_migrations() - mounted)

    assert not missing, (
        "Миграции не попадают в контейнер наката: " + ", ".join(missing) + ". "
        "Новый файл миграции надо добавить и в compose — иначе он молча не "
        "накатится на боевой машине."
    )


def test_stack_wires_the_database_itself_instead_of_asking_for_a_dsn():
    """Строку подключения человек собирает руками ровно один раз — с опечаткой.

    Без MBK_SESSIONS_DSN бот работает и выглядит здоровым, но сессии живут в
    памяти: рестарт стирает вместе с ними отметку «лид уже отправлен».
    """
    environment = _compose()["services"]["backend"].get("environment") or {}

    for name in ("MBK_SESSIONS_DSN", "MBK_ANALYTICS_DSN"):
        assert name in environment, (
            f"{name} не задаётся стеком. Значит его должен вписать человек, "
            "а если он этого не сделает — молчаливая деградация вместо ошибки."
        )
        assert "postgres" in str(environment[name])


def test_frontend_proxies_to_the_service_name_the_stack_actually_defines():
    proxy_pass = re.search(r"proxy_pass\s+http://([a-z0-9_-]+):", NGINX_CONF.read_text(encoding="utf-8"))
    assert proxy_pass is not None

    assert proxy_pass.group(1) in _compose()["services"], (
        f"nginx проксирует на «{proxy_pass.group(1)}», а такого сервиса в "
        "стеке нет: статика откроется, а чат будет отдавать 502."
    )


def test_template_for_mbk_asks_for_everything_that_matters():
    missing = sorted(REQUIRED_IN_TEMPLATE - _keys(ENV_TEMPLATE))

    assert not missing, (
        "В шаблоне настроек для МБК нет обязательных значений: "
        + ", ".join(missing)
        + ". То, чего нет в шаблоне, никто не заполнит."
    )


def test_template_for_mbk_does_not_invent_settings_the_app_never_reads():
    """Два шаблона — два места, где значение можно переименовать и забыть."""
    unknown = sorted(_keys(ENV_TEMPLATE) - _keys(MAIN_ENV_TEMPLATE))

    assert not unknown, (
        "В шаблоне для МБК есть значения, которых нет в .env.example: "
        + ", ".join(unknown)
        + ". Либо опечатка, либо основной шаблон отстал."
    )
