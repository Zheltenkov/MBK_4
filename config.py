from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

FactInterpretationMode = Literal["legacy", "shadow", "semantic"]

# Извлекатель занимает три четверти времени хода, поэтому его модель выбрана
# замером. Корпус из 50 одиночных реплик, 31.07.2026:
#
#   deepseek-v4-pro    48/50, атрибуты 0.988, медиана 6.9s, максимум 58.6s
#   deepseek-v4-flash  48/50, атрибуты 1.000, медиана 6.8s, максимум 23.8s
#   qwen3.7-max        46/50, атрибуты 0.964, медиана 5.0s, но втрое дороже
#   qwen3.7-flash      25/50 — теряет половину фактов, не годится
#
# По этой таблице flash выглядел строго лучше, и его попробовали. Живой прогон
# показал обратное: один провал маршрута (клиент с квартирой в Москве уехал в
# потреб) и связность 100 -> 70..85 в шести сценариях, везде из-за повторного
# вопроса о том, что клиент уже сказал.
#
# Вывод, который стоит помнить: корпус разбирает каждую реплику изолированно, а
# в разговоре важнее удержать уже собранное. Одиночные кейсы этого не измеряют,
# и выбирать по ним модель нельзя — только полным диалоговым прогоном.
#
# 25.09.2026 вышла deepseek-v4.1-flash, и проверка прошла по этому правилу —
# сначала корпус, потом весь ролевой набор:
#
#   deepseek-v4-pro      43/50, F1 0.966, атрибуты 0.952, медиана 5.4s, p95 11.6s
#   deepseek-v4.1-flash  47/50, F1 0.983, атрибуты 0.976, медиана 0.9s, p95 3.4s
#   gemini-2.5-flash-lite 37/50 и вдвое больше денежных реплик мимо анкеты
#
# Намерение первой реплики на 194 боевых: 164 против 166 верных, мимо анкеты
# 3 против 4 денежных (разница — пограничные «как долго рассмотрение»).
# Ролевой прогон 16/16 без замечаний, связность 100 во всех ролях — провал
# июльской flash не повторился. Первая содержательная реплика ждёт ~2.5s
# вместо 6-7s. Отчёт — logs/roleplay/roleplay_20260925_233118.json.
DEFAULT_FACT_INTERPRETATION_MODEL = "deepseek/deepseek-v4.1-flash"


@dataclass(frozen=True)
class AppConfig:
    """Runtime configuration for the local assistant app."""

    # Credential of the selected service. Keep the field for existing callers.
    openrouter_api_key: str
    # Разговорная модель (видит клиент). Лучше быструю чат-модель, НЕ reasoning.
    model: str = "qwen/qwen3.7-max"
    # Молчаливый извлекатель JSON. Если пусто — берём ту же модель.
    extractor_model: str | None = None
    temperature: float = 0.7
    max_tokens: int = 5000
    # Явное кэширование статического префикса (персона+few-shots+знания).
    # Qwen 3.7-max и Anthropic-модели поддерживают cache_control. Если провайдер
    # не поддерживает — параметр игнорируется, ничего не ломает.
    enable_prompt_cache: bool = True
    # Маскировка ПД перед отправкой в зарубежные LLM (ФИО→только имя, телефон скрыт,
    # ДР→только год, адрес→только город). На нашем сервере хранится оригинал.
    enable_pii_masking: bool = True
    # Explicit direct AppConfig instances support deterministic offline fixtures.
    # The normal runtime enables the existing conversational model for policy branches.
    policy_voice_enabled: bool = False
    # Базовый URL OpenRouter (или совместимого шлюза). Меняется для прокси-шлюзов.
    base_url: str = "https://openrouter.ai/api/v1"
    # Как OpenRouter выбирает провайдера модели; None — не вмешиваемся.
    provider_sort: str | None = "latency"
    # HTTP-прокси к модели по порядку предпочтения: первый упал — запрос идёт
    # через следующий (transport.ConnectionPool). Пусто — напрямую.
    proxy_urls: tuple[str, ...] = ()
    # Таймаут запроса к модели, сек. Через прокси/иностранный VPS латентность выше.
    request_timeout: float = 120
    # Новый semantic extractor сначала работает только как shadow: его факты не
    # меняют ответ, routing или CRM до доказанного паритета с legacy.
    fact_interpretation_mode: FactInterpretationMode = "legacy"
    fact_interpretation_model: str | None = None
    # Жёсткие ограничения бюджета и времени для дополнительного shadow-вызова.
    fact_shadow_max_calls_per_session: int = 5
    # Сложная реплика с 8–10 атомарными фактами и boundary-сигналами занимает
    # около 700 output tokens. Запас предотвращает обрезанный JSON; число
    # вызовов по-прежнему ограничено session cap.
    fact_shadow_max_tokens: int = 1000
    fact_shadow_timeout: int = 30
    # Ветка второго объекта недвижимости. Здесь она нужна не ради CRM, а ради
    # анкеты: пока ветка включена, вопросы об объекте не срезаются досрочно —
    # исход решает realty-qualification, а не наша матрица. Без этого ветка
    # включалась «на бумаге» и не запускалась ни разу.
    second_realty_enabled: bool = False
    # Развилка на входе (docs/superpowers/specs/2026-09-09-dialog-entry-and-
    # order-design.md): первая реплика клиента делится на «деньги», «уже
    # оставлял заявку» и «не наш профиль». Включено по умолчанию (решение
    # 12.09.2026); выключение — MBK_ENTRY_ROUTING=0 — возвращает поведение без
    # развилки целиком, вместе с промптом извлекателя.
    entry_routing_enabled: bool = True
    # Controls new debt-policy entry; persisted debt sessions retain their delivery hold.
    debt_policy_enabled: bool = False
    # Чипсы в приветствии (docs/superpowers/specs/2026-09-14-quick-replies-
    # greeting-design.md): кнопки «Получить деньги» и «Другой вопрос» под
    # первой репликой бота. Включено по умолчанию, как развилка, и работает
    # только вместе с ней. Выключение — MBK_QUICK_REPLIES=0.
    quick_replies_enabled: bool = True
    # Local semantic-contract trial. Appended to preserve positional callers.
    policy_semantic_enabled: bool = False
    # Independent factual-audit trial. Existing sessions and call budgets opt in explicitly.
    policy_factual_audit_enabled: bool = False
    llm_provider: Literal["openrouter", "polza"] = "openrouter"
    # Appended for positional compatibility; only the C validator uses this override.
    policy_semantic_model: str | None = None

    @property
    def effective_policy_semantic_model(self) -> str | None:
        """Resolve C without changing extraction or source-audit models."""
        return (self.policy_semantic_model or "").strip() or (self.extractor_model or "").strip() or None


def _read_env_value(name: str) -> str | None:
    value = os.getenv(name) or os.getenv(f"$env:{name}")
    return value.strip() if value else None


def _flag_on_by_default(name: str) -> bool:
    """Флаг, который выключает только явное значение: забытая переменная — «включено»."""
    return (_read_env_value(name) or "1").lower() not in {"0", "false", "no", "off"}


def quick_replies_active() -> bool:
    """Показывать ли чипсы: нужны оба флага — чипсов и развилки на входе.

    Без развилки нажатие «Другой вопрос» некому обработать. Функция нужна
    `main._public_state`, где полного конфига нет; `load_config` разбирает те
    же переменные тем же `_flag_on_by_default`.
    """
    return _flag_on_by_default("MBK_QUICK_REPLIES") and _flag_on_by_default(
        "MBK_ENTRY_ROUTING"
    )


_PROVIDER_SORTS = frozenset({"latency", "throughput", "price"})


def _read_proxy_urls() -> tuple[str, ...]:
    """Прокси к модели: `OPENROUTER_PROXY` списком через запятую.

    Прод 25.09.2026: единственный прокси упал, и бот двое суток отвечал только
    приветствием. Второй адрес в списке — запасной, переключение делает
    транспорт. Системные HTTPS_PROXY/HTTP_PROXY остаются запасным источником
    настройки, как было.
    """
    raw = (
        _read_env_value("OPENROUTER_PROXY")
        or _read_env_value("HTTPS_PROXY")
        or _read_env_value("HTTP_PROXY")
        or ""
    )
    urls = tuple(part.strip() for part in raw.split(",") if part.strip())
    for url in urls:
        # Транспорт ходит через HTTP-туннель (CONNECT). Адрес socks5:// он принял
        # бы за HTTP-прокси и молча не подключился бы — лучше не стартовать.
        if not url.lower().startswith(("http://", "https://")):
            # В сообщение идёт только схема: в адресе лежат логин и пароль.
            what = f"схема {url.split('://', 1)[0]}://" if "://" in url else "адрес без схемы"
            raise ValueError(
                f"OPENROUTER_PROXY: {what} не поддерживается, "
                "укажите адрес вида http://логин:пароль@хост:порт"
            )
    return urls


def _read_provider_sort() -> str | None:
    """Порядок выбора провайдера у OpenRouter: по умолчанию — по задержке.

    Одна и та же модель у разных провайдеров отвечает то за 3, то за 12 секунд
    (замер 25.09.2026), и клиент всё это время ждёт. ``off`` выключает поле
    совсем — на случай шлюза, который понимает только стандартный формат.
    """
    raw = (_read_env_value("OPENROUTER_PROVIDER_SORT") or "latency").lower()
    if raw in {"0", "off", "false", "no", "none"}:
        return None
    if raw not in _PROVIDER_SORTS:
        raise ValueError(
            f"OPENROUTER_PROVIDER_SORT={raw!r}: допустимо latency, throughput, price или off"
        )
    return raw


def _read_fact_interpretation_mode() -> FactInterpretationMode:
    raw = (_read_env_value("MBK_FACT_INTERPRETATION_MODE") or "legacy").lower()
    if raw not in {"legacy", "shadow", "semantic"}:
        raise ValueError(
            "MBK_FACT_INTERPRETATION_MODE должен быть legacy, shadow или semantic"
        )
    return cast(FactInterpretationMode, raw)


def load_config() -> AppConfig:
    """Load the selected model service without sharing its credentials or proxies."""
    provider = (_read_env_value("MBK_LLM_PROVIDER") or "openrouter").lower()
    if provider not in {"openrouter", "polza"}:
        raise ValueError("MBK_LLM_PROVIDER должен быть openrouter или polza")
    # Select the credential before applying legacy aliases. An old key must
    # never be sent to a different service when its new key is missing.
    polza = provider == "polza"
    key = (_read_env_value("POLZA_API_KEY") if polza else
           _read_env_value("OPEN_ROUTER_API_KEY") or _read_env_value("OPENROUTER_API_KEY"))
    if not key:
        raise ValueError("POLZA_API_KEY не найден в .env" if polza else "OPEN_ROUTER_API_KEY не найден в .env")
    if not polza and not key.startswith("sk-or-"):
        raise ValueError("OPEN_ROUTER_API_KEY не похож на ключ OpenRouter")

    return AppConfig(
        openrouter_api_key=key,
        llm_provider=cast(Literal["openrouter", "polza"], provider),
        model=((_read_env_value("POLZA_MODEL") if polza else None)
               or _read_env_value("OPENROUTER_MODEL") or "qwen/qwen3.7-max"),
        extractor_model=((_read_env_value("POLZA_EXTRACTOR_MODEL") if polza else None)
                         or _read_env_value("OPENROUTER_EXTRACTOR_MODEL")
                         or ("deepseek/deepseek-v4-pro" if polza else None)),
        policy_semantic_model=((_read_env_value("POLZA_POLICY_SEMANTIC_MODEL") if polza else None)
                               or _read_env_value("MBK_POLICY_SEMANTIC_MODEL") or None),
        temperature=float(_read_env_value("OPENROUTER_TEMPERATURE") or 0.7),
        max_tokens=int(_read_env_value("OPENROUTER_MAX_TOKENS") or 5000),
        policy_voice_enabled=_flag_on_by_default("MBK_POLICY_VOICE_ENABLED"),
        policy_semantic_enabled=(_read_env_value("MBK_POLICY_SEMANTIC_ENABLED") or "0").lower() in ("1", "true", "yes"),
        policy_factual_audit_enabled=(_read_env_value("MBK_POLICY_FACTUAL_AUDIT_ENABLED") or "0").lower()
        in ("1", "true", "yes"),
        enable_prompt_cache=(_read_env_value("OPENROUTER_PROMPT_CACHE") or "1") not in ("0", "false", "False", "no"),
        enable_pii_masking=(_read_env_value("ENABLE_PII_MASKING") or "1") not in ("0", "false", "False", "no"),
        base_url=("https://polza.ai/api/v1" if polza else
                  (_read_env_value("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")),
        provider_sort=_read_provider_sort(),
        # Polza is reached directly even if deployment retains old proxy env.
        proxy_urls=() if polza else _read_proxy_urls(),
        request_timeout=int(_read_env_value("OPENROUTER_TIMEOUT") or 120),
        fact_interpretation_mode=_read_fact_interpretation_mode(),
        fact_interpretation_model=(
            _read_env_value("MBK_FACT_INTERPRETATION_MODEL")
            or DEFAULT_FACT_INTERPRETATION_MODEL
        ),
        fact_shadow_max_calls_per_session=max(
            0, int(_read_env_value("MBK_FACT_SHADOW_MAX_CALLS_PER_SESSION") or 5)
        ),
        fact_shadow_max_tokens=max(
            100, int(_read_env_value("MBK_FACT_SHADOW_MAX_TOKENS") or 1000)
        ),
        fact_shadow_timeout=max(
            1, int(_read_env_value("MBK_FACT_SHADOW_TIMEOUT") or 30)
        ),
        second_realty_enabled=(_read_env_value("MBK_CRM_SECOND_REALTY_ENABLED") or "")
        .lower()
        in {"1", "true", "yes"},
        # Выключается только явным значением: забытая переменная означает
        # «развилка работает», как решено 12.09.2026.
        entry_routing_enabled=_flag_on_by_default("MBK_ENTRY_ROUTING"),
        debt_policy_enabled=(_read_env_value("MBK_DEBT_POLICY_ENABLED") or "").lower()
        in {"1", "true", "yes"},
        quick_replies_enabled=_flag_on_by_default("MBK_QUICK_REPLIES"),
    )
