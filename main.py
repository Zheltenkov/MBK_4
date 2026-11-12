"""
MBK assistant — FastAPI backend.

Заменяет Streamlit-приложение. Бизнес-логика переиспользуется как есть из core.py,
llm_agent.py, prompts.py, state.py, lead_delivery.py, observability.py.

Эндпоинты:
  GET  /api/health
  POST /api/session                            — создать сессию (опционально с анкетой)
  GET  /api/session/{id}                       — текущий state (история, факты, фаза)
  GET  /api/session/{id}/stream                — SSE: либо opening (без message), либо turn (с message)
  GET  /api/session/{id}/usage                 — токены и стоимость по этой сессии
  GET  /api/usage/global                       — суммарно по всем активным сессиям
  POST /api/session/{id}/end                   — закрыть сессию (сохранить summary)

SSE-события в /stream:
  event: chunk    — { "text": "..." }                 каждый чанк токенов
  event: bubbles  — { "bubbles": ["...", "..."], "quick_replies": [{label, value}] }
                    после стрима — разбивка на пузыри; кнопки — только если есть
  event: state    — { "state": {...}, "lead": {...} } после фоновой extract-фазы
  event: error    — { "message": "..." }              ошибка
  event: done     — {}                                стрим закрылся

Двухфазная архитектура сохранена: пузыри отдаются клиенту сразу, извлекатель работает в фоне,
state-событие приходит с задержкой 2–5 сек. Между ходами future ждётся неблокирующе.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from threading import Lock
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, ValidationError
from sse_starlette.sse import EventSourceResponse

import analytics
import usage_tracker
import delivery
import quick_replies
from config import AppConfig, load_config, quick_replies_active
from context_attachment import ContextAttachmentCoordinator, ContextAttachmentError
from context_policy import CONTEXT_OPENING_MESSAGE, CONTEXT_STATE_KEY, context_delivery_held, public_context_metadata
from debt_policy import POLICY_KEY, DebtPolicyState, debt_delivery_held
import debt_workflow
from customer_context import CrmLookupClient, CurrentContextService
from onloader import policy as onloader_policy
from onloader.contracts import OnloaderDecision, OnloaderError
from onloader.service import OnloaderService
from core import (
    commit_turn,
    conversation_is_over,
    initialize_debt_questionnaire,
    prepare_turn,
    replan,
    stream_opening,
    stream_reply,
)
from crm_dialog_workflow import CrmDialogWorkflow
from delivery.crm import CrmClassificationClient
from delivery.crm_submission_gate import LeadSubmissionGate, TEST_FULL_NAME
from lead_intake_coordinator import LeadIntakeCoordinator, coordinator_for_store
from logger import drop_stale_dialog_logs, log_dialog, log_summary
from pii_masking import mask_session_snapshot, mask_text_with_facts
from prompts import OPENING_MESSAGE
from session_store import Session, SessionStore
from state import should_close_dialog
from storage import build_session_repository
from storage.sessions import SessionPersistenceError


logger = logging.getLogger("mbk.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


# --------------------------------------------------------------------------- #
# Жизненный цикл и зависимости
# --------------------------------------------------------------------------- #
store: SessionStore | None = None
lead_intake_coordinator: LeadIntakeCoordinator | None = None
crm_dialog_workflow: CrmDialogWorkflow | None = None
abandoned_lead_stop: asyncio.Event | None = None
abandoned_lead_task: asyncio.Task[None] | None = None
session_retention_task: asyncio.Task[None] | None = None
site_consent_task: asyncio.Task[None] | None = None
current_context_service: CurrentContextService | None = None
current_context_lookup: CrmLookupClient | None = None
onloader_service: OnloaderService | None = None


def configure_onloader(service: OnloaderService) -> None:
    """Inject a local backend-owned simulator. No issuer or commercial sender is exposed."""
    global onloader_service
    onloader_service = service


def _onloader_available() -> bool:
    return (_env_flag("MBK_ONLOADER_LOCAL_ENABLED") and onloader_service is not None
            and not _env_flag("MBK_CRM_LEAD_INTAKE_ENABLED") and delivery.is_mocked())


def configure_current_context(service: CurrentContextService, *, lookup: CrmLookupClient | None = None) -> None:
    """Trusted ASGI composition root injects its authenticated source; never an HTTP issuer.

    No production form ownership endpoint has been agreed yet. Enabling an env flag
    alone therefore cannot invent a backend resolver or grant access to CRM data.
    """
    global current_context_service, current_context_lookup
    current_context_service, current_context_lookup = service, lookup


def _current_context_available() -> bool:
    # Phase one is deliberately read-context-only. Production delivery is a later gate.
    return (_env_flag("MBK_CURRENT_CONTEXT_ENABLED") and current_context_service is not None
            and not _env_flag("MBK_CRM_LEAD_INTAKE_ENABLED") and delivery.is_mocked())


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store, lead_intake_coordinator, crm_dialog_workflow
    global abandoned_lead_stop, abandoned_lead_task, session_retention_task
    global site_consent_task
    delivery.validate_config()
    # Аналитика необязательна: без MBK_ANALYTICS_DSN recorder остаётся выключенным
    # и приложение поднимается как раньше. Падение Postgres не должно мешать
    # старту — по той же причине, по которой оно не мешает диалогу.
    analytics.set_recorder(analytics.build_recorder())
    if analytics.get_recorder().enabled:
        logger.info("Analytics: события пишутся в Postgres")
    # Переписка — та же база, отдельный выключатель. Таблицу заказчик читает
    # сам; номер телефона заменяется отпечатком до записи.
    analytics.set_transcripts(analytics.build_transcript_recorder())
    if analytics.get_transcripts().enabled:
        logger.info("Analytics: переписка пишется в dialog_messages")
    store = SessionStore(
        max_extract_workers=4,
        repository=build_session_repository(),
    )
    crm_client = CrmClassificationClient()
    submission_gate = LeadSubmissionGate.from_env()
    # Через store, а не напрямую: право отправить лид занимается в общей базе.
    # В окне сине-зелёной выкатки старый и новый контейнер держат одну и ту же
    # сессию, и at-most-once в памяти процесса пропускал два POST'а.
    lead_intake_coordinator = coordinator_for_store(
        crm_client,
        store,
        submission_gate=submission_gate,
    )
    # Разбор прерванных отправок включается до первого подъёма сессии: иначе
    # сессии, поднятые следующей строкой, прошли бы мимо него.
    _recover_interrupted_submissions(store, lead_intake_coordinator)
    # Разговоры, начатые прежним процессом, продолжаются: выкатка больше не
    # обнуляет анкету на середине. Это же возвращает в поле зрения sweeper'а
    # брошенные лиды, ожидавшие отправки, и статус at-most-once автомата —
    # без него рестарт между in_flight и submitted означал дубль лида в CRM.
    restored = await store.restore()
    if restored:
        logger.info("Восстановлено сессий после рестарта: %s", restored)
    crm_dialog_workflow = CrmDialogWorkflow(
        crm_client,
        lead_intake_coordinator,
        submission_gate,
        persist=store.persist,
        enabled=_env_flag("MBK_CRM_WORKFLOW_ENABLED"),
        # Конверсионная ветка: спросить про второй объект, если первый под залог
        # не проходит. Отдельным флагом — она удлиняет анкету тем клиентам, у
        # кого сработала, и включать её надо осознанно.
        # Читаем из конфигурации, а не из env напрямую: то же значение решает,
        # дорасспрашивать ли объект в анкете (core), и два независимых читателя
        # одной переменной рано или поздно разъедутся.
        second_realty_enabled=load_config().second_realty_enabled,
        replan=replan,
    )
    _recover_interrupted_dialogs(store, crm_dialog_workflow)
    abandoned_lead_stop = asyncio.Event()
    if (
        crm_client.lead_intake_enabled
        and _env_flag("MBK_CRM_ABANDONED_LEADS_ENABLED")
    ):
        abandoned_lead_task = asyncio.create_task(
            _run_abandoned_lead_scheduler(abandoned_lead_stop),
            name="mbk-abandoned-leads",
        )
        logger.info(
            "CRM abandoned-lead scheduler enabled: inactivity=%ss",
            int(lead_intake_coordinator.inactivity.total_seconds()),
        )
    session_retention_task = asyncio.create_task(
        _run_session_retention(abandoned_lead_stop),
        name="mbk-session-retention",
    )
    site_consent_task = asyncio.create_task(
        _run_site_consent_scheduler(abandoned_lead_stop),
        name="mbk-site-consent",
    )
    # Подгрузить переопределения цен из env, если есть.
    usage_tracker.override_pricing_from_env()
    logger.info("MBK backend started. Conversation model=%s, extractor=%s",
                load_config().model, load_config().extractor_model or "(same)")
    yield
    if abandoned_lead_stop is not None:
        abandoned_lead_stop.set()
    if abandoned_lead_task is not None:
        await abandoned_lead_task
    if session_retention_task is not None:
        await session_retention_task
    if site_consent_task is not None:
        await site_consent_task
    if store is not None:
        store.executor.shutdown(wait=False, cancel_futures=True)
    # Досылаем накопленное перед остановкой: очередь аналитики и переписки
    # живёт в фоновом потоке, и при выкатке последние ходы иначе теряются.
    for recorder in (analytics.get_recorder(), analytics.get_transcripts()):
        close = getattr(recorder.sink, "close", None)
        if callable(close):
            await asyncio.to_thread(close)


app = FastAPI(title="MBK assistant", version="1.0.0", lifespan=lifespan)


@app.exception_handler(SessionPersistenceError)
async def session_persistence_failure(request: Request, error: SessionPersistenceError) -> JSONResponse:
    conflict = error.reason == "conflict"
    return JSONResponse(status_code=409 if conflict else 503, content={
        "detail": "session_storage_conflict" if conflict else "session_storage_unavailable",
    })


def _cors_origins() -> list[str]:
    """Домены, которым разрешён кросс-доменный доступ с cookie.

    По умолчанию — никому. Фронтенд ходит на тот же домен через Caddy, и
    кросс-доменный доступ ему не нужен вовсе.

    Звёздочка отбрасывается намеренно. Раньше стояло `allow_origins=["*"]`
    вместе с `allow_credentials=True`, а Starlette в этой комбинации
    подставляет в ответ origin запрашивающего и разрешает cookie: любой
    посторонний сайт мог из браузера залогиненного оператора дёргать наш API и
    читать ответы. Если чат когда-нибудь встроят на чужой домен — впишите его
    сюда поимённо, звёздочка так и останется запрещённой.
    """
    raw = os.getenv("CORS_ALLOWED_ORIGINS", "")
    return [
        origin.strip()
        for origin in raw.split(",")
        if origin.strip() and origin.strip() != "*"
    ]


def _store() -> SessionStore:
    assert store is not None, "SessionStore не инициализирован"
    return store


# --------------------------------------------------------------------------- #
# Контракты ввода
# --------------------------------------------------------------------------- #
class CreateSessionBody(BaseModel):
    anketa: dict[str, Any] | None = None
    # Рекламные метки лендинга. Приходят из браузера, поэтому принимаются
    # свободной формой и фильтруются по закрытому списку в attribution.py.
    utm: dict[str, Any] | None = None
    context_expected: StrictBool = False
    onloader_expected: StrictBool = False
    requested_scenario: Literal["debt"] | None = None


class AttachContextBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    capability: SecretStr = Field(min_length=1, max_length=4096, repr=False)


class ReconcileOnloaderBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class LoginBody(BaseModel):
    username: str
    password: str


class SiteConsentBody(BaseModel):
    """Тело запроса согласия. Формат продиктован заказчиком дословно."""

    session_id: str


# Единственное, что клиент видит при внутреннем сбое. Раньше в SSE уходил текст
# исключения целиком, а фронтенд показывает его красной плашкой: туда попадало
# тело ответа OpenRouter (до 800 знаков) и детали подключения к базе. Подробности
# остаются в логах вместе с session_id — там они и нужны.
# Сколько ждать, если по этой сессии уже идёт ход. Ждать без ограничения нельзя:
# наш фронтенд второй запрос не шлёт, но в открытом режиме десяток параллельных
# стримов на одну сессию держал бы столько же соединений до таймаута модели.
TURN_LOCK_TIMEOUT_SECONDS = 90.0

CLIENT_ERROR_MESSAGE = (
    "Что-то пошло не так на моей стороне. Напишите, пожалуйста, ещё раз."
)

AUTH_COOKIE_NAME = "mbk_auth"
AUTH_TTL_SECONDS = int(os.getenv("MBK_AUTH_TTL_SECONDS", "43200"))


# Пути, открытые клиенту при MBK_PUBLIC_CHAT=1. Список — белый, а не чёрный:
# новый служебный endpoint должен закрываться сам, без правки этого места.
# `/api/usage/global` сюда не входит намеренно: сводный расход по всем диалогам
# наша цифра, а не клиентская.
_PUBLIC_CHAT_PREFIXES = ("/api/session",)

# Сколько разговоров с одного адреса в час. Открытый чат — это открытый расход
# на модель: один диалог стоит около 3,3 ₽, и без потолка любой желающий может
# крутить его в цикле. Значение подобрано с запасом для живого человека.
DEFAULT_PUBLIC_SESSIONS_PER_HOUR = 10

_session_starts: dict[str, list[float]] = {}
_session_starts_lock = Lock()


def reset_session_rate_limit() -> None:
    """Сбрасывает окно лимита. Нужен тестам, в рантайме не вызывается."""
    with _session_starts_lock:
        _session_starts.clear()


# Живой человек не отвечает на анкетный вопрос десятью тысячами знаков. Длина
# реплики ничем не ограничивалась и ехала в промпт целиком, а в открытом режиме
# это прямой рычаг на наш счёт за модель: длина умножается на сорок сообщений
# диалога. Режем молча — обрывать разговор ошибкой из-за длины хуже.
DEFAULT_MAX_MESSAGE_CHARS = 2000


def _clamp_message(message: str) -> str:
    limit = max(200, int(_read_env_value("MBK_MAX_MESSAGE_CHARS")
                         or str(DEFAULT_MAX_MESSAGE_CHARS)))
    if len(message) <= limit:
        return message
    logger.info("Реплика клиента обрезана: %s -> %s символов", len(message), limit)
    return message[:limit]


SESSION_GRANT_COOKIE = "mbk_session_grant"
DEBT_SESSION_GRANT_COOKIE = "mbk_debt_session_grant"
# Сессия живёт максимум сутки (см. SessionStore.restore), дольше разрешение не нужно.
SESSION_GRANT_TTL_SECONDS = 24 * 3600


def _secrets_match(left: str, right: str) -> bool:
    """Сравнение секретов за постоянное время, переживающее русскую букву.

    `hmac.compare_digest` со строками работает, только пока обе стороны ASCII,
    а на любой другой букве бросает TypeError. В это сравнение приходят логин
    из формы входа, логин и пароль из окружения и подпись из cookie — то есть
    и то, что набирает человек, и то, что присылает кто угодно. Исключение
    отсюда становится ответом 500: неверный логин выглядел поломкой сервера,
    русский пароль в `.env` закрывал вход всем, а подделанная cookie давала
    постороннему способ получать пятисотку на каждый запрос.

    Сравниваем байты: свойство постоянного времени сохраняется, длина остаётся
    видимой ровно так же, как и в строковом варианте.
    """
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _sign_session_grant(session_id: str) -> str:
    """Разрешение на одну сессию. Ключ тот же, что у операторского токена."""
    return _sign_auth_payload(f"grant:{session_id}")


def _session_id_from_path(path: str) -> str | None:
    """`/api/session/<id>` и всё, что под ним."""
    parts = path.strip("/").split("/")
    return parts[2] if len(parts) >= 3 and parts[:2] == ["api", "session"] else None


def _session_grant_matches(request: Request, path: str) -> bool:
    """Пускает посетителя только в ту сессию, которую он сам начал.

    Аудит 12.08.2026: без этой проверки знание чужого session_id открывало
    анкету с именем, телефоном, городом и долгами — белый список публичных
    путей пускает весь /api/session*, а GET отдаёт facts целиком.
    """
    session_id = _session_id_from_path(path)
    if session_id is None:
        return False
    expected = _sign_session_grant(session_id)
    # Each surface retains its own signed owner grant when another chat is opened.
    return any(
        _secrets_match(granted, expected)
        for name in (SESSION_GRANT_COOKIE, DEBT_SESSION_GRANT_COOKIE)
        if (granted := request.cookies.get(name))
    )


def _public_chat_enabled() -> bool:
    return _env_flag("MBK_PUBLIC_CHAT")


def _visitor_address(request: Request) -> str:
    """Адрес посетителя, а не нашего прокси, и только из доверенного звена.

    За Caddy `request.client` — всегда один и тот же контейнер, поэтому лимит
    по нему закрыл бы чат сразу всем, и читать X-Forwarded-For приходится.

    Но читать его надо СПРАВА. Заголовок накапливается слева направо, и Caddy
    дописывает адрес пира в конец, не затирая присланное клиентом. Значит левые
    элементы пришли от самого клиента и подделываются свободно: первая версия
    брала первый элемент, и лимит обходился сменой строки в заголовке.

    `MBK_TRUSTED_PROXY_HOPS` — сколько прокси стоит перед приложением. Их два в
    обеих схемах: у нас Caddy → nginx фронтенда, у МБК их nginx → наш nginx.
    Здесь было написано «один (Caddy)», и вслед за докстрингом единица уехала в
    .env.example: по нему бот считает адресом посетителя адрес собственного
    nginx, то есть всех посетителей одним, и после десяти диалогов закрывает
    чат сразу всем. Появится CDN — станет три.
    """
    hops = max(1, int(_read_env_value("MBK_TRUSTED_PROXY_HOPS") or "1"))
    forwarded = request.headers.get("x-forwarded-for") or ""
    parts = [part.strip() for part in forwarded.split(",") if part.strip()]
    if len(parts) >= hops:
        candidate = parts[-hops]
        try:
            # Валидация не косметика: без неё в словарь лимита попадают
            # произвольные строки из заголовка, и его можно раздуть.
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            logger.warning("X-Forwarded-For содержит не адрес, беру адрес соединения")
    return request.client.host if request.client else "unknown"


def _session_start_allowed(address: str) -> bool:
    limit = max(1, int(_read_env_value("MBK_PUBLIC_CHAT_SESSIONS_PER_HOUR")
                       or str(DEFAULT_PUBLIC_SESSIONS_PER_HOUR)))
    now = time.time()
    with _session_starts_lock:
        # Чистим весь словарь, а не только свою запись: иначе каждый новый
        # адрес оставляет её навсегда, и память растёт с числом посетителей.
        for known in [key for key, at in _session_starts.items() if not at
                      or now - at[-1] >= 3600]:
            del _session_starts[known]
        recent = [at for at in _session_starts.get(address, []) if now - at < 3600]
        if len(recent) >= limit:
            _session_starts[address] = recent
            return False
        recent.append(now)
        _session_starts[address] = recent
        return True


def _consent_allowed_addresses() -> frozenset[str]:
    raw = _read_env_value("MBK_SITE_CONSENT_ALLOWED_IPS") or ""
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def _consent_caller_allowed(request: Request) -> bool:
    """Проверяет адрес отправителя согласия, если список адресов задан.

    Список необязателен и по умолчанию пуст — тогда согласие принимается
    откуда угодно. Так решил backend заказчика 24.08.2026: «allowlist убирай,
    внутри контейнеров работает нестабильно, не статично». И это правда:
    к нам приходит адрес шлюза docker-сети, а он меняется при пересоздании
    стека — наш же смоук показал 172.20.0.1 из сети, которой завтра не будет.

    Что осталось вместо него: согласие принимается только для сессии, которая
    действительно ждёт нажатия, и только один раз — повторный вызов ничего не
    отправляет. Номер сессии случайный и в чужие руки попадает лишь вместе с
    самой офер-ссылкой.

    Заполненный список по-прежнему работает: если однажды адрес станет
    постоянным, ограничение включается одной строкой.
    """
    allowed = _consent_allowed_addresses()
    if not allowed:
        return True
    caller = _visitor_address(request)
    if caller in allowed:
        return True
    logger.warning("Согласие с постороннего адреса %s отклонено", caller)
    return False


# --------------------------------------------------------------------------- #
# Статическая авторизация
# --------------------------------------------------------------------------- #
# Пути, которые открыты без нашего пароля. Согласие приносит бэкенд заказчика,
# и про наш логин он не знает — вместо пароля его защищает список адресов,
# см. _consent_caller_allowed.
PUBLIC_API_PATHS = frozenset({"/api/health", "/api/site-consent"})


@app.middleware("http")
async def require_api_auth(request: Request, call_next):
    path = request.url.path
    public_api = path in PUBLIC_API_PATHS or path.startswith("/api/auth/")
    if not path.startswith("/api/") or public_api:
        return await call_next(request)

    try:
        authorized = _verify_auth_token(request.cookies.get(AUTH_COOKIE_NAME))
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    if authorized:
        return await call_next(request)

    # Клиент приходит по кнопке с сайта МБК и пароля не знает. Открываем ему
    # только сам чат и только при явно включённом режиме: забытая переменная
    # означает «закрыто».
    if _public_chat_enabled() and path.startswith(_PUBLIC_CHAT_PREFIXES):
        if request.method == "POST" and path == "/api/session":
            visitor = _visitor_address(request)
            # Адрес в логе — единственный способ проверить MBK_TRUSTED_PROXY_HOPS
            # до того, как ошибка в нём проявится. А проявляется она так: бот
            # считает всех посетителей одним адресом и через десять диалогов
            # закрывает чат вообще всем. См. docs/INSTALL.md, шаг 5.
            logger.info("Новый разговор, адрес посетителя: %s", visitor)
            if not _session_start_allowed(visitor):
                logger.warning(
                    "Лимит разговоров в час исчерпан для адреса %s. Если он "
                    "один на всех посетителей — проверьте MBK_TRUSTED_PROXY_HOPS",
                    visitor,
                )
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Слишком много обращений, попробуйте позже"},
                )
            return await call_next(request)
        if not _session_grant_matches(request, path):
            return JSONResponse(
                status_code=403, content={"detail": "Нет доступа к этой сессии"}
            )
        return await call_next(request)

    return JSONResponse(status_code=401, content={"detail": "Требуется авторизация"})


# CORS подключается ПОСЛЕ авторизации и именно поэтому — в Starlette последний
# добавленный middleware оказывается снаружи. Пока порядок был обратным, до
# CORS не доходило ничего: предполётный запрос браузера (метод OPTIONS, без
# cookie) авторизация отбивала отказом, и настоящий запрос браузер уже не
# отправлял. Чат, встроенный на чужой домен, не открывался ни у кого, а в логах
# это выглядело обычным «нет доступа к этой сессии». Снаружи же CORS дописывает
# заголовки и к отказам — иначе фронтенд на чужом домене не отличит отказ от
# обрыва связи.
_cors_allowed = _cors_origins()
if _cors_allowed:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_allowed,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# --------------------------------------------------------------------------- #
# Эндпоинты
# --------------------------------------------------------------------------- #
@app.get("/api/health")
async def health() -> dict:
    cfg = load_config()
    return {
        "status": "ok",
        "conversation_model": cfg.model,
        "extractor_model": cfg.extractor_model or cfg.model,
    }


@app.post("/api/auth/login")
async def login(body: LoginBody) -> JSONResponse:
    expected_username, expected_password = _auth_credentials()
    username_ok = _secrets_match(body.username.strip(), expected_username)
    password_ok = _secrets_match(body.password, expected_password)
    if not (username_ok and password_ok):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    response = JSONResponse(content={"ok": True, "username": expected_username})
    response.set_cookie(
        AUTH_COOKIE_NAME,
        _make_auth_token(expected_username),
        max_age=AUTH_TTL_SECONDS,
        httponly=True,
        secure=_secure_auth_cookie(),
        samesite="lax",
        path="/",
    )
    return response


@app.get("/api/auth/me")
async def auth_me(request: Request) -> dict:
    username = _auth_username_from_token(request.cookies.get(AUTH_COOKIE_NAME))
    # public_chat отдаётся и неавторизованному: по нему интерфейс решает,
    # показывать посетителю форму входа или сразу открывать чат. Клиент приходит
    # по кнопке с сайта МБК и пароля не знает.
    public_chat = _public_chat_enabled()
    if not username:
        return {"ok": False, "username": None, "public_chat": public_chat}
    return {"ok": True, "username": username, "public_chat": public_chat}


@app.post("/api/auth/logout")
async def logout() -> JSONResponse:
    response = JSONResponse(content={"ok": True})
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    return response


@app.post("/api/session")
async def create_session(body: CreateSessionBody, response: Response) -> dict:
    if body.onloader_expected:
        if body.anketa is not None or body.context_expected or body.requested_scenario is not None:
            raise HTTPException(status_code=409, detail="onloader_context_conflict")
        if not _onloader_available():
            raise HTTPException(status_code=503, detail="onloader_unavailable")
    if body.requested_scenario == "debt":
        if body.anketa is not None or body.context_expected:
            raise HTTPException(status_code=409, detail="scenario_context_conflict")
        if not _env_flag("MBK_DEBT_POLICY_ENABLED"):
            raise HTTPException(status_code=503, detail="scenario_unavailable")
    if body.context_expected and not _current_context_available():
        raise HTTPException(status_code=503, detail="context_unavailable")
    if body.context_expected and body.anketa:
        raise HTTPException(status_code=409, detail="browser_form_conflict")
    session = await _store().create(anketa=body.anketa, utm=body.utm)
    if body.requested_scenario == "debt":
        # An explicit public questionnaire choice grants access to this new chat.
        session.state[POLICY_KEY] = DebtPolicyState(reason="explicit_request").as_state()
        session.state = initialize_debt_questionnaire(session.state)
        _store().persist(session)
    if body.context_expected:
        session.state[CONTEXT_STATE_KEY] = {"context_status": "pending"}
        _store().persist(session)
    if body.onloader_expected:
        # Reserve the same normal chat identity and its delivery hold before host/source I/O.
        session.state[onloader_policy.STATE_KEY] = {"status": "pending"}
        _store().persist(session)
    # Разрешение на эту и только эту сессию. Без него знание чужого
    # session_id открывало бы чужую анкету с телефоном и долгами: белый список
    # публичных путей пускает весь /api/session*, а разделить «свою» и «чужую»
    # сессию было нечем.
    response.set_cookie(
        DEBT_SESSION_GRANT_COOKIE if body.requested_scenario == "debt" else SESSION_GRANT_COOKIE,
        _sign_session_grant(session.id),
        max_age=SESSION_GRANT_TTL_SECONDS,
        httponly=True,
        secure=_secure_auth_cookie(),
        samesite="lax",
        path="/",
    )
    return {
        "session_id": session.id,
        "has_anketa": bool(session.anketa),
        "state": _public_state(session),
        "turn_in_progress": False,
    }


@app.post("/api/session/{session_id}/context")
async def attach_current_context(session_id: str, request: Request) -> dict:
    # Require the owning visitor's signed grant even when an operator bypassed middleware.
    if not _session_grant_matches(request, request.url.path):
        raise HTTPException(status_code=403, detail="context_forbidden")
    if not _current_context_available():
        raise HTTPException(status_code=503, detail="context_unavailable")
    # Parse a bounded body ourselves: automatic validation errors would echo capability.
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 8192:
            raise HTTPException(status_code=413, detail="context_request_too_large")
        raw.extend(chunk)
    try:
        body = AttachContextBody.model_validate_json(bytes(raw))
    except ValidationError:
        raise HTTPException(status_code=422, detail="invalid_context_request") from None
    session = await _store().get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    service = current_context_service
    assert service is not None
    coordinator = ContextAttachmentCoordinator(service, persist=_store().persist, lookup=current_context_lookup,
                                                debt_enabled=_env_flag("MBK_DEBT_POLICY_ENABLED"))
    try:
        await asyncio.wait_for(session.turn_lock.acquire(), timeout=1.0)
    except TimeoutError:
        raise HTTPException(status_code=409, detail="context_busy") from None
    release_lock = True
    try:
        _store().refresh_locked(session)
        loop = asyncio.get_running_loop()
        try:
            pending = loop.run_in_executor(
                _store().executor, coordinator.attach, session, body.capability.get_secret_value()
            )
            try:
                result = await asyncio.shield(pending)
            except asyncio.CancelledError:
                # A disconnected caller cannot release the lock while its worker still mutates state.
                release_lock = False
                def release_when_finished(completed: asyncio.Future) -> None:
                    try:
                        completed.exception()
                    except asyncio.CancelledError:
                        pass
                    finally:
                        session.turn_lock.release()
                pending.add_done_callback(release_when_finished)
                raise
        except ContextAttachmentError as error:
            raise HTTPException(status_code=error.status, detail=error.reason) from None
        return {"context": result.metadata, "idempotent": result.idempotent, "state": _public_state(session)}
    finally:
        if release_lock:
            session.turn_lock.release()


@app.post("/api/session/{session_id}/onloader")
async def bind_onloader(session_id: str, request: Request) -> dict:
    return await _onloader_request(session_id, request, "bind")


@app.post("/api/session/{session_id}/onloader/decision")
async def decide_onloader(session_id: str, request: Request) -> dict:
    return await _onloader_request(session_id, request, "decide")


@app.post("/api/session/{session_id}/onloader/reconcile")
async def reconcile_onloader(session_id: str, request: Request) -> dict:
    return await _onloader_request(session_id, request, "reconcile")


async def _onloader_request(session_id: str, request: Request, operation: str) -> dict:
    # A known SID, operator login or raw form-id grants no onloader authority.
    if not _session_grant_matches(request, request.url.path):
        raise HTTPException(status_code=403, detail="onloader_forbidden")
    if not _onloader_available():
        raise HTTPException(status_code=503, detail="onloader_unavailable")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 8192:
            raise HTTPException(status_code=413, detail="onloader_request_too_large")
        raw.extend(chunk)
    try:
        schema = {"bind": AttachContextBody, "decide": OnloaderDecision, "reconcile": ReconcileOnloaderBody}[operation]
        body = schema.model_validate_json(bytes(raw))
    except ValidationError:
        raise HTTPException(status_code=422, detail="invalid_onloader_request") from None
    session = await _store().get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    service = onloader_service
    assert service is not None

    def execute() -> None:
        if isinstance(body, AttachContextBody):
            service.bind(session, body.capability.get_secret_value(), _store().persist_durable)
        elif isinstance(body, OnloaderDecision):
            service.decide(session, body, _store().persist_durable)
        else:
            service.reconcile(session, _store().persist_durable)

    try:
        await asyncio.wait_for(session.turn_lock.acquire(), timeout=1.0)
    except TimeoutError:
        raise HTTPException(status_code=409, detail="onloader_busy") from None
    release_lock = True
    try:
        _store().refresh_locked(session)
        pending = asyncio.get_running_loop().run_in_executor(_store().executor, execute)
        try:
            await asyncio.shield(pending)
        except asyncio.CancelledError:
            # An HTTP disconnect cannot unlock a worker that is still committing its intent.
            release_lock = False
            def release_when_finished(completed: asyncio.Future) -> None:
                try:
                    completed.exception()
                except asyncio.CancelledError:
                    pass
                finally:
                    session.turn_lock.release()
            pending.add_done_callback(release_when_finished)
            raise
        except OnloaderError as error:
            raise HTTPException(status_code=error.status, detail=error.reason) from None
        return {"session_id": session.id, "state": _public_state(session), "turn_in_progress": False}
    finally:
        if release_lock:
            session.turn_lock.release()


@app.get("/api/session/{session_id}")
async def get_session(session_id: str) -> dict:
    session, turn_in_progress = await _store().read_committed(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    return {"session_id": session.id, "state": _public_state(session), "anketa": session.anketa,
            "turn_in_progress": turn_in_progress}


@app.get("/api/session/{session_id}/usage")
async def get_usage(session_id: str) -> dict:
    session = await _store().get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    return session.usage.summary()


@app.get("/api/usage/global")
async def get_global_usage() -> dict:
    summary = usage_tracker.aggregate(s.usage for s in _store().all_sessions())
    summary["active_sessions"] = len(_store().all_sessions())
    return summary


@app.post("/api/site-consent")
async def site_consent(body: SiteConsentBody, request: Request) -> dict:
    """Клиент подтвердил согласие кнопкой на офер-странице.

    Зовёт бэкенд заказчика, а не браузер: адрес страницы ведёт на их домен, а
    оттуда запрос уходит к нам. Поэтому здесь нет ни CORS, ни cookie — только
    список разрешённых адресов.

    Лид уходит ровно один раз: право отправки захватывается в базе, поэтому
    повторный клик, двойная отправка формы и гонка с таймаутом безопасны.
    """
    if not _consent_caller_allowed(request):
        raise HTTPException(status_code=403, detail="forbidden")

    workflow = crm_dialog_workflow
    session = await _store().get(body.session_id)
    if session is None or workflow is None:
        raise HTTPException(status_code=404, detail="session not found")
    if context_delivery_held(session.state) or debt_delivery_held(session.state):
        raise HTTPException(status_code=409, detail="context_delivery_held")

    loop = asyncio.get_running_loop()
    state = await loop.run_in_executor(
        _store().executor,
        lambda: workflow.confirm_site_consent(session),
    )
    # Адрес пишем всегда: список адресов необязателен, и это единственный след
    # того, кто именно подтвердил согласие. Пригодится, если однажды спросят.
    logger.info(
        "Согласие с сайта принято: сессия %s, статус %s, адрес %s",
        session.id,
        state.status,
        _visitor_address(request),
    )
    return {
        "success": True,
        "thanksUrl": (session.state.get("crm_workflow") or {}).get("thanks_url"),
    }


@app.post("/api/session/{session_id}/end")
async def end_session(session_id: str) -> dict:
    session = await _store().get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    try:
        log_summary(session.id, session.state)
    except Exception:  # noqa: BLE001
        pass
    _store().persist(session)
    return {"ok": True, "usage": session.usage.summary()}


# --------------------------------------------------------------------------- #
# SSE-стрим: opening или turn в зависимости от наличия message
# --------------------------------------------------------------------------- #
@app.get("/api/session/{session_id}/stream")
async def stream(session_id: str, request: Request, message: str | None = None) -> EventSourceResponse:
    session = await _store().get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")

    user_message = _clamp_message((message or "").strip())
    return EventSourceResponse(
        _run_turn_sse(session, user_message, request=request),
        media_type="text/event-stream",
    )


# --------------------------------------------------------------------------- #
# Главная корутина одного хода (opening или обычный turn)
# --------------------------------------------------------------------------- #
async def _run_turn_sse(
    session: Session,
    user_message: str,
    request: Request,
):
    """Сериализует ходы одной сессии, сохраняя порядок истории."""
    try:
        await asyncio.wait_for(
            session.turn_lock.acquire(), timeout=TURN_LOCK_TIMEOUT_SECONDS
        )
    except TimeoutError:
        logger.warning("ход не дождался очереди: session=%s", session.id)
        yield _sse_error(
            "Предыдущий ответ ещё готовится — секунду, и напишите снова."
        )
        yield {"event": "done", "data": "{}"}
        return
    release_lock = True
    try:
        _store().refresh_locked(session)
        effective_is_opening = (
            not user_message and not session.state.get("chat_history")
        )
        # Повторный opening-запрос не создаёт ещё одно приветствие. Возвращаем
        # актуальный state, чтобы второй клиентский подписчик синхронизировал UI.
        if not user_message and not effective_is_opening:
            yield {
                "event": "state",
                "data": json.dumps(
                    {
                        "session_id": session.id, "state": _public_state(session),
                        "delivery": session.state.get("delivery"),
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            }
            yield {"event": "done", "data": "{}"}
            return
        cfg = load_config()
        if cfg.policy_semantic_enabled and cfg.policy_voice_enabled and user_message:
            with session._lock:
                selected_state = deepcopy(session.state)
                debt_workflow.select_for_turn(selected_state, user_message, enabled=cfg.debt_policy_enabled)
                install_hold = (debt_delivery_held(selected_state)
                                and not debt_delivery_held(session.state)
                                and not context_delivery_held(session.state))
                if install_hold:
                    # Background submission owners use this lock, rather than turn_lock.
                    # Publish the conservative hold before model work can yield the thread.
                    session.state = deepcopy(selected_state)
            if onloader_policy.dialogue_active(selected_state) or debt_delivery_held(selected_state):
                # Policy speech is buffered already. Commit its conversation state before any chunk.
                pending = asyncio.get_running_loop().run_in_executor(
                    _store().executor,
                    lambda: _commit_grounded_policy_turn(
                        session, selected_state, user_message, cfg, checkpoint_hold=install_hold,
                    ),
                )
                try:
                    messages = await asyncio.shield(pending)
                except asyncio.CancelledError:
                    release_lock = False
                    def release_when_finished(completed: asyncio.Future) -> None:
                        try:
                            completed.exception()
                        except asyncio.CancelledError:
                            pass
                        finally:
                            session.turn_lock.release()
                    pending.add_done_callback(release_when_finished)
                    raise
                except SessionPersistenceError:
                    raise
                except Exception:
                    logger.warning("grounded policy turn failed before publication")
                    yield _sse_error(CLIENT_ERROR_MESSAGE)
                    yield {"event": "done", "data": "{}"}
                    return
                for text in messages:
                    yield {"event": "chunk", "data": json.dumps({"text": text}, ensure_ascii=False)}
                chips = quick_replies.quick_replies(
                    session.state, enabled=cfg.quick_replies_enabled and cfg.entry_routing_enabled,
                    debt_enabled=cfg.debt_policy_enabled,
                )
                async for event in _paced_bubbles(messages, chips):
                    yield event
                yield {"event": "state", "data": json.dumps(
                    {"session_id": session.id, "state": _public_state(session),
                     "delivery": session.state.get("delivery")}, ensure_ascii=False, default=str)}
                yield {"event": "done", "data": "{}"}
                return
        async for event in _run_turn_sse_unlocked(
            session,
            user_message,
            effective_is_opening,
            request,
        ):
            yield event
    except SessionPersistenceError:
        # A streamed answer cannot advertise an uncommitted state after storage failure.
        yield _sse_error("Не удалось сохранить диалог. Проверьте историю перед продолжением.")
        yield {"event": "done", "data": "{}"}
    finally:
        if release_lock:
            session.turn_lock.release()


def _commit_grounded_policy_turn(session: Session, state: dict[str, Any], user_message: str,
                                cfg: AppConfig, *, checkpoint_hold: bool = False) -> list[str]:
    """Execute a local policy turn on a detached state, with no business submission."""
    # History remains private until the full conversation checkpoint is acknowledged.
    if checkpoint_hold:
        # A new debt branch must have a durable delivery hold before any model call.
        _store().persist_durable(session)
    candidate = replace(session, state=state)
    _ensure_opening_history(candidate)
    session.touch()
    if conversation_is_over(state, user_message):
        state.setdefault("chat_history", []).append({"role": "user", "content": user_message})
        messages: list[str] = []
    else:
        prepared = prepare_turn(
            state, user_message, cfg, usage_collector=session.usage.collector(role="extraction"),
            shadow_usage_collector=session.usage.collector(role="interpretation_shadow"), submit_delivery=False,
        )
        # Disabled trials preserve the existing stream-adapter call shape.
        factual_usage = ({"factual_usage_collector": session.usage.collector(role="policy_factual_audit")}
                         if cfg.policy_factual_audit_enabled else {})
        full_reply = "".join(stream_reply(
            prepared, user_message, cfg, usage_collector=session.usage.collector(role="conversation"),
            critic_usage_collector=session.usage.collector(role="policy_voice_critic"),
            **factual_usage,
        )).strip()
        if not full_reply:
            raise SessionPersistenceError("unavailable")
        committed = commit_turn(prepared, user_message, full_reply)
        state, messages = committed["current_state"], committed["messages"]
    _store().commit_state_durable(session, state, opening=candidate.has_started_opening)
    return messages


async def _paced_bubbles(
    bubbles: list[str],
    chips: list[dict[str, str]] | None = None,
):
    """Фиксирует уже показанный stream одним атомарным событием.

    Клиент видит живой набор по событиям ``chunk``. Повторно разыгрывать те же
    пузыри с искусственными паузами после окончания stream нельзя: первый
    финальный пузырь очищал весь накопленный текст, остальные исчезали на
    несколько секунд и создавали ощущение тормозящего бота.

    Кнопки, если есть, едут тем же событием: виджет рисует их под пузырями
    (спека чипсов 14.09.2026). Пустой список поля не добавляет — старый виджет
    разницы не видит.
    """
    data: dict[str, Any] = {"bubbles": bubbles}
    if chips:
        data["quick_replies"] = chips
    yield {
        "event": "bubbles",
        "data": json.dumps(data, ensure_ascii=False),
    }


def _remember_unanswered(session: Session, user_message: str) -> None:
    """Кладёт реплику клиента в историю, оставляя её без ответа.

    Записывать надо: если человек следом скажет что-то по существу, модель
    должна видеть весь разговор, а не дыру в нём.
    """
    history = session.state.setdefault("chat_history", [])
    history.append({"role": "user", "content": user_message})
    _store().persist(session)


async def _run_turn_sse_unlocked(
    session: Session,
    user_message: str,
    is_opening: bool,
    request: Request,
):
    """Yields SSE-события: chunk → bubbles → state → done.

    Порядок фаз изменён относительно v3: сначала разбор реплики и решение движка, потом
    стрим ответа. Причина в core.py — вопрос выбирает движок, и планировать его по фактам
    без последней реплики нельзя, иначе бот переспрашивает только что сказанное.
    """
    if onloader_policy.STATE_KEY in session.state and should_close_dialog(session.state):
        # Consent and receipt reads stay available after the conversation's normal turn budget.
        yield {"event": "state", "data": json.dumps({"session_id": session.id, "state": _public_state(session)}, ensure_ascii=False)}
        yield {"event": "done", "data": "{}"}
        return
    cfg = load_config()
    # Publish a durable hold before background extraction can fail or a sweeper can inspect it.
    with session._lock:
        previous_policy = session.state.get("questionnaire_policy")
        policy = debt_workflow.select_for_turn(session.state, user_message, enabled=cfg.debt_policy_enabled)
    if policy is not None and previous_policy != session.state.get("questionnaire_policy"):
        _store().persist(session)
    if debt_delivery_held(session.state):
        cfg = replace(cfg, enable_pii_masking=True)
    if context_delivery_held(session.state):
        # Backend-originated contacts never inherit an operator's disabled PII switch.
        cfg = replace(cfg, enable_pii_masking=True)
        metadata = public_context_metadata(session.state) or {}
        if onloader_policy.STATE_KEY not in session.state and metadata.get("context_status") != "ready":
            # No ordinary questionnaire/LLM fallback while an owned context is unresolved.
            message = ("Проверяю данные вашей анкеты. Пожалуйста, дождитесь результата."
                       if metadata.get("context_status") == "pending" else
                       "Не удалось подтвердить данные анкеты. Попробуйте открыть обращение заново.")
            yield {"event": "bubbles", "data": json.dumps({"bubbles": [message]}, ensure_ascii=False)}
            yield {"event": "state", "data": json.dumps({"session_id": session.id, "state": _public_state(session)}, ensure_ascii=False)}
            yield {"event": "done", "data": "{}"}
            return
    loop = asyncio.get_event_loop()

    if not is_opening and not user_message:
        yield _sse_error("Напишите сообщение, пожалуйста.")
        return

    # Backend-контракт сильнее UI: реальная первая реплика никогда не может начать
    # историю раньше приветствия. Это страхует прямой API-вызов и любую frontend-гонку.
    if user_message and _ensure_opening_history(session):
        _store().persist(session)

    # Inactivity таймер сбрасывает только реальное сообщение клиента. Opening,
    # health-check и чтение state не должны откладывать брошенный лид.
    if user_message:
        if lead_intake_coordinator is not None:
            lead_intake_coordinator.note_user_activity(session)
        else:
            session.touch()

    # Разговор закончен, а клиент просто поддакивает — отвечать нечем и незачем.
    # Ролевой прогон 19.08.2026: после финала двенадцать «Ага» получили двенадцать
    # разных прощаний. Проверка стоит ДО разбора: иначе за каждое «спасибо» мы
    # платим ещё и вызовом извлекателя.
    if user_message and conversation_is_over(session.state, user_message):
        _remember_unanswered(session, user_message)
        # Пустой список, а не отсутствие события: клиент ждёт `bubbles` на каждый
        # ход. Виджет заказчика на этом месте держит индикатор набора, и без
        # события он крутился бы вечно.
        yield {
            "event": "bubbles",
            "data": json.dumps({"bubbles": []}, ensure_ascii=False),
        }
        yield {
            "event": "state",
            "data": json.dumps(
                {
                    "session_id": session.id, "state": _public_state(session),
                    "delivery": session.state.get("delivery"),
                },
                ensure_ascii=False,
                default=str,
            ),
        }
        yield {"event": "done", "data": "{}"}
        return

    # 1) Разбор реплики + решение движка. Синхронный вызов модели уводим в executor,
    #    чтобы не блокировать цикл событий.
    extract_cb = session.usage.collector(role="extraction")
    shadow_extract_cb = session.usage.collector(role="interpretation_shadow")
    try:
        prepared_state = await loop.run_in_executor(
            _store().executor,
            lambda: prepare_turn(
                session.state,
                user_message,
                cfg,
                usage_collector=extract_cb,
                shadow_usage_collector=shadow_extract_cb,
                submit_delivery=not (
                    crm_dialog_workflow is not None
                    and crm_dialog_workflow.enabled
                ),
            ),
        )
    except Exception:  # noqa: BLE001
        logger.exception("prepare_turn failed: session=%s", session.id)
        yield _sse_error(CLIENT_ERROR_MESSAGE)
        return

    # CRM application workflow получает уже обновлённые факты и локальный route.
    # Состояние сохраняется до внешнего POST, чтобы at-most-once пережил ошибку
    # стрима ответа. При выключенном флаге поведение локального mock не меняется.
    active_crm_workflow = crm_dialog_workflow
    if active_crm_workflow is not None and active_crm_workflow.enabled:
        session.state = prepared_state
        _store().persist(session)
        try:
            await loop.run_in_executor(
                _store().executor,
                lambda: active_crm_workflow.advance(session, user_message),
            )
        except Exception:  # noqa: BLE001
            logger.exception("CRM dialog workflow failed: session=%s", session.id)
            yield _sse_error(CLIENT_ERROR_MESSAGE)
            return
        prepared_state = session.state

    role = "opening" if is_opening else "conversation"
    usage_cb = session.usage.collector(role=role)

    # 2) Стрим ответа. Инструкция уже лежит в prepared_state.
    try:
        if is_opening:
            sync_iter = stream_opening(prepared_state, cfg, usage_collector=usage_cb)
        else:
            factual_usage = ({"factual_usage_collector": session.usage.collector(role="policy_factual_audit")}
                             if cfg.policy_factual_audit_enabled else {})
            sync_iter = stream_reply(prepared_state, user_message, cfg, usage_collector=usage_cb,
                                     critic_usage_collector=session.usage.collector(role="policy_voice_critic"),
                                     **factual_usage)
    except Exception:  # noqa: BLE001
        logger.exception("stream start failed: session=%s", session.id)
        yield _sse_error(CLIENT_ERROR_MESSAGE)
        return

    collected_parts: list[str] = []
    try:
        # Прокидываем sync-генератор в async через executor.
        while True:
            if await request.is_disconnected():
                logger.info("client disconnected mid-stream, session=%s", session.id)
                break
            chunk = await loop.run_in_executor(None, lambda: next(sync_iter, None))
            if chunk is None:
                break
            collected_parts.append(chunk)
            yield {"event": "chunk", "data": json.dumps({"text": chunk}, ensure_ascii=False)}
    except Exception:  # noqa: BLE001
        logger.exception("stream failed: session=%s", session.id)
        yield _sse_error(CLIENT_ERROR_MESSAGE)
        return

    full_reply = "".join(collected_parts).strip()
    if not full_reply:
        logger.error("модель вернула пустой ответ: session=%s", session.id)
        yield _sse_error(CLIENT_ERROR_MESSAGE)
        return

    # 3) Пузыри + детерминированный хвост (ссылка, если микросервис её вернул).
    committed = commit_turn(prepared_state, "" if is_opening else user_message, full_reply)

    session.state = committed["current_state"]
    session.has_started_opening = session.has_started_opening or is_opening
    _store().persist(session)

    try:
        facts = session.state.get("facts")
        safe_facts = facts if isinstance(facts, dict) else None
        safe_user_message = mask_text_with_facts(user_message or "(opening)", safe_facts)
        safe_reply = mask_text_with_facts("\n\n".join(committed["messages"]), safe_facts)
        log_dialog(session.id, safe_user_message, True, session.state, {})
        log_dialog(session.id, safe_reply, False, session.state, {})
    except Exception:  # noqa: BLE001
        pass

    chips = quick_replies.quick_replies(
        session.state,
        enabled=cfg.quick_replies_enabled and cfg.entry_routing_enabled,
        debt_enabled=cfg.debt_policy_enabled,
    )
    async for event in _paced_bubbles(committed["messages"], chips):
        yield event
    yield {
        "event": "state",
        "data": json.dumps(
            {"session_id": session.id, "state": _public_state(session), "delivery": session.state.get("delivery")},
            ensure_ascii=False,
            default=str,
        ),
    }
    yield {"event": "done", "data": "{}"}


# --------------------------------------------------------------------------- #
# Хелперы
# --------------------------------------------------------------------------- #
def _ensure_opening_history(session: Session) -> bool:
    """Гарантирует, что история начинается с единственного приветствия.

    Возвращает True, только если state был изменён.
    """
    history = session.state.setdefault("chat_history", [])
    if history:
        return False
    opening = (onloader_policy.reply(session.state) if onloader_policy.STATE_KEY in session.state else
               CONTEXT_OPENING_MESSAGE if context_delivery_held(session.state) else OPENING_MESSAGE)
    history.append({"role": "assistant", "content": opening})
    session.has_started_opening = True
    return True


def _public_state(session: Session) -> dict:
    """Слепок state для клиента: всё, что нужно фронту для рендера. PII не вырезаем —
    клиент видит то же, что ассистент собрал."""
    # A context is backend-originated data; expose only the existing PII-safe fact view.
    private_context = context_delivery_held(session.state) or debt_delivery_held(session.state)
    s = mask_session_snapshot(session.state, None)[0] if private_context else session.state
    return {
        "chat_history": s.get("chat_history", []),
        # Кнопки под последней репликой бота (спека чипсов 14.09.2026). Виджет
        # берёт их отсюда, когда восстанавливает разговор после перезагрузки
        # страницы. Пустой список — кнопок нет.
        "quick_replies": (
            []
            if should_close_dialog(s) or onloader_policy.STATE_KEY in session.state
            else quick_replies.quick_replies(s, enabled=quick_replies_active(),
                debt_enabled=_env_flag("MBK_DEBT_POLICY_ENABLED"))
        ),
        "facts": s.get("facts", {}),
        # Разговор ещё идёт или уже кончился — ровно тот вопрос, который задаёт
        # любой клиент чата. Раньше признак существовал только внутри, и
        # снаружи о завершении догадывались по доставке: клиент, которому
        # решения не нашлось, выглядел незакончившим анкету навсегда.
        "dialog_phase": s.get("dialog_phase"),
        # Сценарий развилки на входе: анкета, «уже оставлял заявку» или «не
        # наш профиль», и закончен ли разговор в ветке. Персональных данных
        # нет. Нужен разбору диалогов и ролевому прогону.
        "entry": s.get("entry"),
        # План — это ответ на вопрос «почему бот спросил именно это»: какой маршрут сейчас
        # в работе, каких полей не хватает и что уже отсеялось. Нужен для разбора диалогов.
        "plan": s.get("plan"),
        "delivery": s.get("delivery"),
        "crm_lead": s.get("crm_lead"),
        "crm_workflow": s.get("crm_workflow"),
        "customer_context": public_context_metadata(session.state),
        "onloader": onloader_policy.public_metadata(session.state),
        "questionnaire_policy": debt_workflow.public_policy_metadata(session.state),
        # Shadow-диагностика безопасна для UI: она не содержит второго набора facts
        # и не участвует ни в routing, ни в CRM. Evidence уже прошло PII masking.
        "interpretation": s.get("interpretation"),
        "runtime": {
            "crm_workflow_enabled": _env_flag("MBK_CRM_WORKFLOW_ENABLED"),
            "crm_lead_intake_enabled": _env_flag(
                "MBK_CRM_LEAD_INTAKE_ENABLED"
            ),
            "crm_submission_mode": (
                _read_env_value("MBK_CRM_SUBMISSION_MODE") or "blocked"
            ),
            "crm_test_full_name": TEST_FULL_NAME,
            "crm_test_phone_configured": bool(
                _read_env_value("MBK_CRM_TEST_PHONE_ALLOWLIST")
            ),
            # Панель разбора (итоговый JSON, lead_id, режим отправки, расход
            # токенов) существует для нас, а не для клиента. По умолчанию
            # выключена: 10.08.2026 на боевом стенде клиент увидел бы
            # «Тестовый лид создан в CRM: 3450947» и блок с JSON.
            "debug_panel": _env_flag("MBK_DEBUG_PANEL"),
        },
        "internal_summary": s.get("internal_summary", ""),
        "closed": should_close_dialog(s),
    }


def _sse_error(message: str) -> dict:
    return {"event": "error", "data": json.dumps({"message": message}, ensure_ascii=False)}


def _auth_credentials() -> tuple[str, str]:
    username = _read_env_value("MBK_AUTH_USERNAME")
    password = _read_env_value("MBK_AUTH_PASSWORD")
    if not username or not password:
        raise HTTPException(status_code=503, detail="Не настроены MBK_AUTH_USERNAME и MBK_AUTH_PASSWORD")
    return username, password


def _read_env_value(name: str) -> str | None:
    value = os.getenv(name) or os.getenv(f"$env:{name}")
    return value.strip() if value else None


def _env_flag(name: str) -> bool:
    return (_read_env_value(name) or "").lower() in {"1", "true", "yes"}


def _recover_interrupted_submissions(
    session_store: SessionStore,
    coordinator: LeadIntakeCoordinator,
) -> int:
    """Снимает с мели лиды, чей результат потерялся вместе с процессом.

    До появления таблицы сессий вызывать это было негде: рестарт стирал всё, и
    восстанавливать было нечего. Теперь сессия возвращается со статусом
    in_flight, отправка её больше не трогает (статус не NOT_SENT), и обращение
    зависает навсегда — молча. Перевод в outcome_unknown фиксирует, что исход
    неизвестен, и запрещает автоматический повтор: лид мог и уйти.

    Разбор вешается на подъём сессии из базы, а не только на старт. Сессия
    хранится 72 часа, а стартовый restore() поднимает сутки: клиент, чья
    отправка прервалась позавчера, приходит через ленивый store.get(), где
    восстановления не было вовсе. Проход по памяти остаётся для сессий,
    поднятых до включения крючка.
    """
    def recover_on_restore(session: Session) -> None:
        _recover_one_submission(session, coordinator)

    session_store.on_restore = recover_on_restore
    return sum(
        1
        for session in session_store.all_sessions()
        if _recover_one_submission(session, coordinator)
    )


def _recover_one_submission(
    session: Session,
    coordinator: LeadIntakeCoordinator,
) -> bool:
    if (session.state.get("crm_lead") or {}).get("status") != "in_flight":
        return False
    coordinator.recover_interrupted_submission(session)
    logger.warning(
        "Сессия %s поднята с незавершённой отправкой лида: исход неизвестен, "
        "требуется разбор",
        session.id,
    )
    return True


def _recover_interrupted_dialogs(
    session_store: SessionStore,
    workflow: CrmDialogWorkflow,
) -> int:
    """Возвращает право на ход диалогам, замёрзшим посреди обмена с CRM.

    Соседний проход по crm_lead спасал только лид: диалоговый автомат оставался
    в статусе обмена, и сессия, чей процесс умер между запросом и ответом,
    молчала до конца хранения. Проход локальный, в сеть не ходит — он лишь
    приводит состояние к тому, с чем следующий ход клиента сможет работать.
    """
    if not workflow.enabled:
        return 0
    recovered = 0
    for session in session_store.all_sessions():
        if workflow.recover_interrupted(session) is not None:
            recovered += 1
    if recovered:
        logger.warning(
            "Диалогов, прерванных посреди обмена с CRM: %s — восстановлены",
            recovered,
        )
    return recovered


async def _run_site_consent_scheduler(stop: asyncio.Event) -> None:
    """Досылает лиды, чьё согласие так и не пришло со страницы.

    Отдельная задача, а не ветка обхода брошенных анкет: у них разный смысл.
    Брошенная анкета — это клиент, ушедший на середине, и лид уходит неполным.
    Здесь анкета пройдена целиком, ждём только нажатия кнопки.
    """
    interval = max(10, int(_read_env_value("MBK_SITE_CONSENT_SCAN_SECONDS") or "30"))
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            continue
        except TimeoutError:
            pass

        workflow = crm_dialog_workflow
        session_store = store
        if workflow is None or session_store is None:
            continue
        sessions = session_store.all_sessions()
        loop = asyncio.get_running_loop()
        for session in sessions:
            # Scheduler и SSE не должны одновременно двигать один workflow.
            # Занятый живым диалогом сеанс просто попадёт в следующий проход.
            try:
                await asyncio.wait_for(session.turn_lock.acquire(), timeout=0.05)
            except TimeoutError:
                continue
            try:
                retried = await loop.run_in_executor(
                    session_store.executor,
                    workflow.retry_pending,
                    session,
                )
                state = retried
                if retried is None:
                    state = await loop.run_in_executor(
                        session_store.executor,
                        workflow.expire_site_consent,
                        session,
                    )
            except Exception:  # noqa: BLE001 — обход не должен умирать
                logger.warning(
                    "CRM scheduler: сессия %s не обработана",
                    session.id,
                    exc_info=True,
                )
                continue
            finally:
                session.turn_lock.release()
            if state is not None:
                logger.info(
                    "CRM scheduler: сессия %s обработана со статусом %s",
                    session.id,
                    state.status,
                )


async def _run_abandoned_lead_scheduler(stop: asyncio.Event) -> None:
    """Периодически проверяет inactivity без произвольного sleep в тестах.

    Timeout здесь является самой бизнес-семантикой scheduler: он задаёт точность,
    с которой обнаруживается достижение 15 минут. Реальная готовность анкеты всё
    равно повторно проверяется coordinator непосредственно перед POST.
    """
    interval = max(
        1,
        int(_read_env_value("MBK_CRM_ABANDONED_SCAN_SECONDS") or "30"),
    )
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            continue
        except TimeoutError:
            pass

        coordinator = lead_intake_coordinator
        session_store = store
        if coordinator is None or session_store is None:
            continue
        active_coordinator = coordinator
        sessions = session_store.all_sessions()
        loop = asyncio.get_running_loop()
        outcomes = await loop.run_in_executor(
            session_store.executor,
            lambda: active_coordinator.sweep_abandoned(sessions),
        )
        for outcome in outcomes:
            logger.info(
                "CRM abandoned lead outcome: status=%s lead_id=%s",
                outcome.status,
                outcome.lead_id,
            )


async def _retention_sweep(session_store: SessionStore, max_age_hours: int) -> None:
    """Один проход срока хранения — по всем следам разговора сразу.

    Следов два: состояние сессии (память и база) и журнал реплик на диске.
    Пока чистилось только состояние, копия того же разговора оставалась в
    `logs/dialog_<id>.jsonl` навсегда — телефон там замаскирован, а имя и город
    клиент называет обычными словами, и маскировать их нечем.

    Отдельная функция, а не тело цикла: срок хранения ПД должен проверяться
    тестом, а не ожиданием часа до следующего обхода.
    """
    dropped = await session_store.drop_stale(max_age_hours=max_age_hours)
    if dropped:
        logger.info("Сессии старше %s ч удалены: %s", max_age_hours, dropped)
    drop_stale_dialog_logs(max_age_hours)

    # Переписка в базе заказчика живёт по своему сроку: её читают глазами
    # спустя дни и недели, поэтому 72 часа сессии ей не подходят. По умолчанию
    # хранится бессрочно — это решение заказчика, а не наше.
    retention_days = analytics.transcript_retention_days()
    if retention_days:
        purged = await asyncio.to_thread(analytics.purge_transcripts, retention_days)
        if purged:
            logger.info(
                "Переписка старше %s дн. удалена: %s строк", retention_days, purged
            )


async def _run_session_retention(stop: asyncio.Event) -> None:
    """Удаляет всё, что старше срока хранения: сессии, базу и журналы реплик.

    Срок короткий намеренно: в состоянии лежат имя, телефон и адреса клиента.
    Держать их дольше, чем нужно для разбора инцидента, незачем — в CRM данные
    уже уехали, а здесь они только копятся.
    """
    max_age_hours = max(1, int(_read_env_value("MBK_SESSION_RETENTION_HOURS") or "72"))
    interval = max(60, int(_read_env_value("MBK_SESSION_RETENTION_SCAN_SECONDS") or "3600"))
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            continue
        except TimeoutError:
            pass

        session_store = store
        if session_store is None:
            continue
        await _retention_sweep(session_store, max_age_hours)


def _secure_auth_cookie() -> bool:
    value = (_read_env_value("MBK_AUTH_COOKIE_SECURE") or "0").lower()
    return value in {"1", "true", "yes"}


def _make_auth_token(username: str) -> str:
    expires_at = int(time.time()) + AUTH_TTL_SECONDS
    payload = {"u": username, "exp": expires_at, "n": secrets.token_urlsafe(16)}
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload_b64 = _base64url_encode(payload_json)
    signature = _sign_auth_payload(payload_b64)
    return f"{payload_b64}.{signature}"


def _verify_auth_token(token: str | None) -> bool:
    return _auth_username_from_token(token) is not None


def _auth_username_from_token(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    payload_b64, signature = token.split(".", 1)
    if not _secrets_match(signature, _sign_auth_payload(payload_b64)):
        return None
    try:
        payload = json.loads(_base64url_decode(payload_b64))
        username = str(payload.get("u") or "")
        expires_at = int(payload.get("exp") or 0)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    expected_username, _ = _auth_credentials()
    if time.time() >= expires_at:
        return None
    if not _secrets_match(username, expected_username):
        return None
    return username


def _sign_auth_payload(payload_b64: str) -> str:
    _, password = _auth_credentials()
    return hmac.new(password.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()


def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _base64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


# --------------------------------------------------------------------------- #
# Базовый JSON-обработчик для непойманных HTTPException — чтобы фронт получал детали
# --------------------------------------------------------------------------- #
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
