"""Детерминированная интерпретация результата first-qualification."""
from __future__ import annotations

import logging
import urllib.parse
from dataclasses import dataclass
from typing import Literal

from .crm_contracts import FirstQualificationResponse


logger = logging.getLogger(__name__)

NextStep = Literal["ask_site_consent", "create_lead"]
THANKS_BASE_URL = "https://www.mbk.ru"

# Куда ведёт сайт, когда у правила своей страницы нет. Андрей 10.08.2026: «у
# категории 2217 ссылки в матрице нет и не было, сайт ведёт на дефолтную
# страницу благодарности — используй её как фолбэк, тогда обещание бота
# „пришлю ссылку“ всегда выполнимо». Он же 17.08.2026: «перезванивать никто не
# должен по залогу».
#
# Мы прочли то письмо наоборот — как «у части правил ссылки нет, там звонит
# менеджер» — и построили на этом ветку финала. Клиент залога недвижимости
# уходил ждать звонка, которого никто не собирался делать.
# ?c=0 — та же ветка, на которую уводит анкета (targetUrl || '/thanks?c=0'):
# «Заявка в работе, менеджер перезвонит в ближайшее время». Без параметра
# страница обещает звонок «через 60 секунд» с таймером — для лида, который
# уходит в КЦ, это враньё, и анкета на эту ветку никогда не попадает.
DEFAULT_THANKS_URL = "https://www.mbk.ru/thanks?c=0"


@dataclass(frozen=True)
class QualificationDecision:
    next_step: NextStep
    should_create_lead: Literal[True]
    qualified: bool
    category: int | None
    thanks_url: str | None
    # Страница, которую действительно вернула CRM. `thanks_url` выше её
    # подменяет дефолтной благодарностью, когда своей нет, — а на дефолтной
    # нет кнопки согласия (проверено 21.08.2026). Держать лид на паузе в
    # расчёте на такую страницу нельзя: клиент не нажмёт того, чего не видит.
    offer_url: str | None = None


def decide_qualification(
    response: FirstQualificationResponse,
) -> QualificationDecision:
    """Определяет следующий шаг без LLM и без повторной бизнес-классификации.

    Backend подтвердил: lead-intake вызывается и при ``category = null``.
    Единственная промежуточная развилка бота — ``need_site_consent``.
    ``need_partner_consent`` здесь намеренно не используется: это флаг оператора КЦ.
    
    """
    result = response.result
    offer_url = _safe_thanks_url(result.thanks_url)
    return QualificationDecision(
        next_step=(
            "ask_site_consent"
            if result.need_site_consent
            else "create_lead"
        ),
        should_create_lead=True,
        qualified=result.category is not None,
        category=result.category,
        thanks_url=offer_url or DEFAULT_THANKS_URL,
        offer_url=offer_url,
    )


def _safe_thanks_url(path: str | None) -> str | None:
    """Нормализует HTTPS-ссылку, домен которой определяет CRM.

    Backend 24.08.2026 подтвердил: допустимы любые, в том числе чужие домены.
    Относительный путь сохраняет прежний контракт и привязывается к mbk.ru.
    Схему всё равно проверяем кодом: browser-ссылкой может быть только HTTPS,
    а ``javascript:``/``data:`` и значение без хоста клиенту не показываются.
    Отброшенное значение логируется без пути и query, где могут быть ПД.
    """
    if not path:
        return None
    raw_url = path.strip()
    if not raw_url:
        return None
    try:
        raw_parsed = urllib.parse.urlparse(raw_url)
        # Строка явно объявила себя абсолютной — не превращаем сломанный
        # ``https:///path`` в валидный относительный путь на mbk.ru.
        if raw_parsed.scheme and (
            raw_parsed.scheme != "https" or not raw_parsed.hostname
        ):
            logger.warning(
                "CRM вернула некорректный thanks_url: scheme=%s host=%s",
                raw_parsed.scheme,
                raw_parsed.hostname,
            )
            return None
        url = urllib.parse.urljoin(f"{THANKS_BASE_URL}/", raw_url)
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname
    except ValueError:
        parsed = urllib.parse.urlparse(THANKS_BASE_URL)
        hostname = None
    if parsed.scheme != "https" or not hostname:
        logger.warning(
            "CRM вернула некорректный thanks_url: scheme=%s host=%s",
            parsed.scheme,
            hostname,
        )
        return None
    return url
