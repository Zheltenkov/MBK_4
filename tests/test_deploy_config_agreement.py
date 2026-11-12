"""Настройки не должны расходиться между собой и со схемой развёртывания.

Оба случая, из-за которых файл появился, ломают уже поднятый прод, а не сборку,
и потому обычными тестами не ловятся.

1. Health-check backend'а ходил к самому себе голым `urllib.request.urlopen`.
   Тот молча читает системные HTTP_PROXY/HTTPS_PROXY, а прокси у нас нужен
   ровно одному адресату — зарубежному провайдеру модели. Пропишет
   администратор прокси системно — и проверка здоровья пойдёт за границу
   спрашивать про наш же localhost. Ответа не будет, docker посчитает
   контейнер больным, frontend с `depends_on: service_healthy` не поднимется
   вовсе, и выкатка откатится на полностью исправном образе.

2. `MBK_TRUSTED_PROXY_HOPS` в .env.example стоял 1, а в deploy/prod/env.example
   2. Звеньев в обеих схемах два (у нас Caddy → nginx фронтенда, у МБК их nginx
   → наш nginx), и единица означает, что бот принимает за адрес посетителя
   адрес собственного nginx. Всех посетителей — одним адресом: после десяти
   диалогов лимит закрывает чат сразу всем. .env.example назван полным списком
   настроек, и тот, кто ставит бота по нему, получает эту поломку в подарок.
"""
from __future__ import annotations

import re
import urllib.request
from email.message import Message
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
COMPOSE_FILES = (
    ROOT / "docker-compose.yml",
    ROOT / "docker-compose.edge.yml",
    ROOT / "deploy" / "prod" / "docker-compose.yml",
)
MAIN_ENV_TEMPLATE = ROOT / ".env.example"
PROD_ENV_TEMPLATE = ROOT / "deploy" / "prod" / "env.example"
NGINX_CONF = ROOT / "frontend" / "nginx.conf"
ACTIVE_SNIPPET = ROOT / "deploy" / "active" / "upstream.caddy"

HOPS = "MBK_TRUSTED_PROXY_HOPS"

# Прокси, которого нет. Если проверка здоровья его послушается, соединение
# уйдёт на этот хост вместо приложения — это и ловится.
FOREIGN_PROXY = "http://proxy.invalid:3128"


# --------------------------------------------------------------------------- #
# 1. Health-check не ходит через системный прокси
# --------------------------------------------------------------------------- #
def _python_health_checks() -> dict[str, str]:
    """Все проверки здоровья, написанные на питоне, — из Dockerfile и compose.

    Перечислены файлы, но не проверки: новая проверка в любом из них попадает
    под правило сама.
    """
    found: dict[str, str] = {}

    dockerfile = DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", " ")
    for match in re.finditer(r'HEALTHCHECK[^\n]*?CMD\s+python\s+-c\s+"(.+?)"', dockerfile):
        found["Dockerfile"] = match.group(1)

    for path in COMPOSE_FILES:
        # Ветка поставки не содержит файлов нашего стенда: они вычищены вместе
        # со стенограммами и адресом сервера. Сверять нечего — но и падать
        # незачем, проверка касается тех файлов, которые есть.
        if not path.exists():
            continue
        services = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("services") or {}
        for name, spec in services.items():
            test = ((spec or {}).get("healthcheck") or {}).get("test")
            if isinstance(test, list) and "python" in test and "-c" in test:
                found[f"{path.relative_to(ROOT).as_posix()}:{name}"] = test[-1]
    return found


def test_health_checks_written_in_python_are_actually_collected():
    """Страж самого теста: молча опустевший список ничего не проверяет."""
    collected = _python_health_checks()

    assert "Dockerfile" in collected
    # В ветке поставки compose нашего стенда вычищен вместе с остальным нашим,
    # поэтому ждём столько проверок, сколько файлов реально есть.
    expected = sum(1 for path in (DOCKERFILE, *COMPOSE_FILES) if path.exists())
    assert len(collected) >= expected, (
        "Проверки здоровья на питоне перестали находиться: " + ", ".join(collected)
    )


@pytest.mark.parametrize("where", sorted(_python_health_checks()))
def test_health_check_asks_the_container_itself_and_never_a_proxy(where, monkeypatch):
    """Куда уйдёт соединение, если системный прокси всё-таки настроен.

    Настоящей сети здесь нет: подменяется нижний слой urllib и записывается
    хост, к которому проверка собралась подключиться.
    """
    code = _python_health_checks()[where]
    connected: list[str] = []

    class _Response:
        # `code`/`msg`/`info` спрашивает сам urllib (HTTPErrorProcessor),
        # `status` — проверка здоровья.
        status = 200
        code = 200
        msg = "OK"
        url = "http://localhost:8000/api/health"

        def info(self) -> Message:
            return Message()

        def read(self) -> bytes:
            return b'{"status":"ok"}'

        def close(self) -> None:
            pass

        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *exc) -> bool:
            return False

    def fake_http_open(handler, request):
        connected.append(request.host)
        return _Response()

    monkeypatch.setattr(urllib.request.HTTPHandler, "http_open", fake_http_open)
    # Эмулируем контейнер (python:3.12-slim) с системным прокси: на Windows
    # getproxies читает реестр, и тест зависел бы от машины разработчика.
    monkeypatch.setattr(
        urllib.request, "getproxies", lambda: {"http": FOREIGN_PROXY, "https": FOREIGN_PROXY}
    )
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
    # urlopen кэширует опенер в модуле — без сброса подмена выше могла бы не
    # примениться, и тест оказался бы зелёным на сломанной проверке.
    monkeypatch.setattr(urllib.request, "_opener", None, raising=False)

    with pytest.raises(SystemExit) as exit_info:
        exec(compile(code, f"<healthcheck {where}>", "exec"), {"__name__": "healthcheck"})

    assert exit_info.value.code == 0, f"{where}: проверка не признала живой ответ здоровым"
    assert connected and all("proxy.invalid" not in host for host in connected), (
        f"Health-check {where} уходит через системный прокси ({connected}). "
        "Он спрашивает про собственный localhost, и спрашивать надо напрямую: "
        "urllib.request.build_opener(urllib.request.ProxyHandler({}))."
    )


# --------------------------------------------------------------------------- #
# 2. Число доверенных звеньев одно и то же везде
# --------------------------------------------------------------------------- #
def _declared(path: Path, name: str) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(rf"\s*{name}=(.*)", line)
        if match:
            return match.group(1).strip()
    return None


def _hops_of_our_stand() -> int:
    """Звено — это прокси, который дописывает X-Forwarded-For.

    Считаем по самим конфигам, а не по памяти: так число в шаблоне привязано к
    схеме, а не к тому, что кто-то однажды написал.
    """
    hops = 0
    if re.search(
        r"reverse_proxy frontend-(blue|green):80",
        ACTIVE_SNIPPET.read_text(encoding="utf-8"),
    ):
        hops += 1  # Caddy дописывает X-Forwarded-For сам, отдельной строки не требует.
    nginx = NGINX_CONF.read_text(encoding="utf-8")
    if "proxy_pass http://backend" in nginx and "$proxy_add_x_forwarded_for" in nginx:
        hops += 1
    return hops


def test_both_env_templates_declare_the_same_number_of_trusted_hops():
    main = _declared(MAIN_ENV_TEMPLATE, HOPS)
    prod = _declared(PROD_ENV_TEMPLATE, HOPS)

    assert main is not None and prod is not None, f"{HOPS} пропал из шаблона настроек"
    assert main == prod, (
        f"{HOPS} разъехался: .env.example={main}, deploy/prod/env.example={prod}. "
        "Кто ставит бота по .env.example — он назван полным списком настроек, — "
        "возьмёт чужое значение и получит закрытый для всех чат после десяти "
        "диалогов."
    )


def test_trusted_hops_matches_the_number_of_proxies_in_front_of_the_app():
    if not ACTIVE_SNIPPET.exists() or not NGINX_CONF.exists():
        pytest.skip("схема нашего стенда в ветке поставки отсутствует")
    declared = _declared(MAIN_ENV_TEMPLATE, HOPS)

    assert int(declared or 0) == _hops_of_our_stand() == 2, (
        f"{HOPS}={declared}, а перед приложением {_hops_of_our_stand()} звена. "
        "Адрес берётся из X-Forwarded-For справа: недосчитались звена — бот "
        "видит адрес собственного nginx и считает всех посетителей одним."
    )
