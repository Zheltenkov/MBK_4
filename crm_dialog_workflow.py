"""Application orchestration CRM после завершения локальной квалификации.

Модуль не выбирает продукт и не формулирует ответы клиенту. Он связывает уже
готовые компоненты в явный автомат:

``route_found|local no_fit → test gate → first-qualification → consent? → lead-intake``.

Qualification можно безопасно повторять по transport policy. Lead-intake всегда
остаётся под at-most-once coordinator и никогда не повторяется автоматически.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from collections.abc import Callable
from copy import deepcopy
from enum import StrEnum
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict

import analytics
import delivery
from delivery.contracts import LeadRoutingRequest
from delivery.crm import CrmApiError
from delivery.crm_contracts import (
    FirstQualificationRequest,
    FirstQualificationResponse,
    RealtyQualificationRequest,
    RealtyQualificationResponse,
)
from delivery.crm_mapping import map_first_qualification, map_realty_qualification
from delivery.crm_submission_gate import SubmissionGate
from delivery.crm_workflow import decide_qualification
from delivery.offer_link import build_offer_link
from entry_routing import lead_blocked
from logger import log_crm_incident
from lead_intake_coordinator import (
    CRM_LEAD_STATE_KEY,
    LeadIntakeCoordinator,
    LeadSubmissionState,
    LeadSubmissionStatus,
)
from routing import get_fact
from session_store import Session
from utils import client_display_name, set_by_path


logger = logging.getLogger(__name__)

CRM_WORKFLOW_STATE_KEY = "crm_workflow"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)

# Сколько раз звучит вопрос о согласии, прежде чем мы перестаём его задавать.
# Совпадает с потолком полей анкеты (routing.planner.MAX_FIELD_ATTEMPTS): три —
# это вопрос и два переспроса. Дальше человек либо не понимает вопрос, либо
# отвечает так, что разобрать ответ мы не умеем, и повтор только злит.
MAX_SITE_CONSENT_ATTEMPTS = 3

# Сколько ждём нажатия кнопки на офер-странице. Анкета сайта отправляет
# неподтверждённый лид кроном через 5 минут, и её же JS на офер-странице
# ровно через 5 минут уводит клиента на «спасибо» — после этого нажимать
# уже нечего, ждать дольше бессмысленно (первоначальные «15 мин» заказчика
# от 21.08.2026 заменены его же правилом от 28.08.2026: «как в анкете»).
DEFAULT_SITE_CONSENT_TIMEOUT_SECONDS = 300

# Адрес на стороне заказчика, который зовёт офер-страница после нажатия. Он же
# потом стучится к нам. Значение продиктовано заказчиком дословно.
DEFAULT_CONSENT_CALLBACK_URL = "https://www.mbk.ru/api/chat-consent"


def site_consent_timeout_seconds() -> int:
    raw = (os.getenv("MBK_SITE_CONSENT_TIMEOUT_SECONDS") or "").strip()
    try:
        return max(60, int(raw)) if raw else DEFAULT_SITE_CONSENT_TIMEOUT_SECONDS
    except ValueError:
        logger.warning(
            "MBK_SITE_CONSENT_TIMEOUT_SECONDS=%r не число — беру %s",
            raw,
            DEFAULT_SITE_CONSENT_TIMEOUT_SECONDS,
        )
        return DEFAULT_SITE_CONSENT_TIMEOUT_SECONDS


def site_consent_pause_enabled() -> bool:
    """Держать ли лид до нажатия кнопки на офер-странице.

    По умолчанию выключено, и это осознанно. Пауза полезна ровно тогда, когда
    на офер-странице есть кнопка согласия. Пока её нет, ожидание делает хуже:
    сейчас такой лид уходит сразу с «шаг не показывался», а с паузой уйдёт на
    пятнадцать минут позже и с отметкой «отказался» — при том, что клиенту
    нечего было нажимать.

    Включается, когда сайт научится читать `d_lead_id` и звать consent-адрес.
    """
    raw = (os.getenv("MBK_SITE_CONSENT_PAUSE_ENABLED") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def consent_callback_url() -> str:
    return (
        os.getenv("MBK_SITE_CONSENT_CALLBACK_URL") or ""
    ).strip() or DEFAULT_CONSENT_CALLBACK_URL


def site_consent_default_url() -> str | None:
    """Офер-страница на случай, когда CRM своей не вернула.

    Заказчик 25.08.2026: у маршрутов БФЛ своей ссылки в матрице нет, а кнопка
    согласия живёт на постоянной странице — её и подставляем. Пусто по
    умолчанию: без явно названного адреса лид уходит сразу, как раньше.
    """
    return (os.getenv("MBK_SITE_CONSENT_DEFAULT_URL") or "").strip() or None


# Боевые адреса страниц подтверждения — прямо в коде, чтобы рабочая схема не
# требовала env (заказчик 27.08.2026: «дефолт задай, чтоб я не лез в env»).
# Категории — enum CRM «Категория заявки»: 3895 БФЛ Мск/МО, 21300 БФЛ Регионы,
# 4705/4706 залог авто. Страницы — те же, куда ведёт анкета сайта (заказчик
# 28.08.2026: «поведение бота должно быть как в анкете»): у обеих стоит кнопка
# подтверждения и chat-guard, клиента узнаёт кука виджета.
DEFAULT_SITE_CONSENT_OFFER_PAGES: dict[int, str] = {
    3895: "https://www.mbk.ru/bfl-finish",
    21300: "https://www.mbk.ru/bfl-finish",
    4705: "https://www.mbk.ru/auto-finish",
    4706: "https://www.mbk.ru/auto-finish",
}


def site_consent_offer_pages() -> dict[int, str]:
    """Страница подтверждения по категории.

    Дефолт — {@link DEFAULT_SITE_CONSENT_OFFER_PAGES} в коде. Переменная
    ``MBK_SITE_CONSENT_OFFER_PAGES`` (пары «категория=адрес» через запятую)
    полностью ЗАМЕНЯЕТ дефолтную карту; значение ``off`` выключает её совсем.

    Матрица хранит только «URL спасибо» — страницу ПОСЛЕ согласия (для залога
    авто это витрина партнёров без кнопки подтверждения). Адрес самой страницы
    подтверждения в системах заказчика у каждого потока свой: у анкеты он в
    коде сайта, у партнёрского сервиса — в его базе (one_hands_setting).
    Боту ни один из этих источников не доступен, поэтому карта живёт здесь.
    """
    raw = (os.getenv("MBK_SITE_CONSENT_OFFER_PAGES") or "").strip()
    if not raw:
        return dict(DEFAULT_SITE_CONSENT_OFFER_PAGES)
    if raw.lower() == "off":
        return {}
    pages: dict[int, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        key, sep, url = item.partition("=")
        try:
            category = int(key.strip())
        except ValueError:
            category = None
        if not sep or category is None or not url.strip():
            logger.warning(
                "MBK_SITE_CONSENT_OFFER_PAGES: пара %r не в формате "
                "«категория=адрес» — пропущена",
                item,
            )
            continue
        pages[category] = url.strip()
    return pages


def site_consent_button_pages() -> list[str]:
    """Страницы, на которых кнопка согласия действительно есть.

    Backend 25.08.2026 описал механику: виджет ставит куку
    `mbk_chat_consent = session_id` на домене `www.mbk.ru`, страница
    `/bfl-finish` по нажатию зовёт их guard, а тот читает куку и приносит
    согласие нам. Отсюда два ограничения: кука видна только своему домену, а
    guard стоит на конкретной странице.

    Значит, офер на другом хосте (`info.mbk.ru` у залоговых маршрутов) кнопки
    не покажет. Пауза там закончилась бы отметкой «отказался» у клиента,
    которому нечего было нажимать, — ровно тот дефект, что мы уже разбирали с
    общей страницей благодарности. Поэтому ждём только на перечисленных
    страницах; пусто — не ограничиваем.
    """
    raw = (os.getenv("MBK_SITE_CONSENT_BUTTON_PAGES") or "").strip()
    return [item.strip() for item in raw.split(",") if item.strip()]


def _own_page_link(page: str, facts: dict[str, object] | None) -> str:
    """Ссылка на страницу своего домена: чистый адрес плюс только ``fio``.

    Клиента такая страница узнаёт по куке виджета, поэтому ``d_lead_id`` и
    прочие параметры согласия ей не нужны (а на /bfl-finish чужой ``d_lead_id``
    и вовсе роняет её с 500). Но ``fio`` анкета сайта пробрасывает всегда
    (finishAuto уводит на /auto-finish?fio=...): кнопка страницы несёт его
    дальше — на витрину и партнёрские «спасибо», где он заменяет плейсхолдер
    «Маргарита Ивановна». Без ``fio`` клиент видел чужое имя по всей цепочке
    (QA 28.08.2026). Как и в анкете, уходит полное ФИО как его назвал клиент.
    """
    name = get_fact(facts or {}, "client.name")
    if not isinstance(name, str) or not name.strip():
        name = client_display_name(facts if isinstance(facts, dict) else None)
    if not name:
        return page
    parts = urlsplit(page)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["fio"] = name.strip()
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def page_has_consent_button(url: str | None) -> bool:
    """Есть ли на этой странице кнопка согласия.

    Сравниваем хост и начало пути, игнорируя query и якорь: CRM возвращает
    ссылки со своими метками, а кнопку это не отменяет.
    """
    if not url:
        return False
    allowed = site_consent_button_pages()
    if not allowed:
        return True
    return any(_same_page(url, pattern) for pattern in allowed)


def _same_page(url: str, pattern: str) -> bool:
    left, right = urlsplit(url), urlsplit(pattern)
    if (left.hostname or "").lower() != (right.hostname or "").lower():
        return False
    path = left.path.rstrip("/") or "/"
    prefix = right.path.rstrip("/") or "/"
    return prefix == "/" or path == prefix or path.startswith(prefix + "/")


def ask_site_consent_in_chat() -> bool:
    """Спрашивать ли согласие репликой в чате. По умолчанию — нет.

    Заказчик 20.08.2026, дословно: «согласие он даёт после того, как получает
    ссылку, переходит по ней и даёт согласие на сайте, это тебя уже не
    касается». То есть `need_site_consent` в ответе квалификации — пометка
    маршрута, а не поручение боту спросить.

    Пока бот спрашивал сам, ответ «да» в чате уезжал в CRM как
    `UF_SITE_AGREE_PARTNERS = 23000`: партнёр получал лид с согласием, которого
    человек не давал, — и не мог дать, потому что офер-страницы не видел.
    Теперь бот не спрашивает и не утверждает: в CRM уходит 23002 «шаг не
    показывался», а согласие фиксирует сайт.

    Выключатель оставлен на случай, если у заказчика маршрутизация к партнёру
    ждёт именно 23000 от нас: тогда `MBK_ASK_SITE_CONSENT_IN_CHAT=1` возвращает
    прежнее поведение без выкатки новой сборки.
    """
    raw = (os.getenv("MBK_ASK_SITE_CONSENT_IN_CHAT") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


class CrmQualificationClient(Protocol):
    """Узкий read-only интерфейс qualification transport."""

    def first_qualification(
        self,
        payload: FirstQualificationRequest,
    ) -> FirstQualificationResponse: ...

    def realty_qualification(
        self,
        payload: RealtyQualificationRequest,
    ) -> RealtyQualificationResponse: ...


class CrmDialogStatus(StrEnum):
    """Состояния CRM-ветки, которые сохраняются вместе с сессией."""

    NOT_STARTED = "not_started"
    GATED_LOCAL = "gated_local"
    QUALIFYING = "qualifying"
    QUALIFICATION_RETRY_PENDING = "qualification_retry_pending"
    AWAITING_SITE_CONSENT = "awaiting_site_consent"
    SUBMITTING = "submitting"
    SUBMISSION_RETRY_PENDING = "submission_retry_pending"
    SUBMITTED = "submitted"
    QUALIFICATION_ERROR = "qualification_error"
    SUBMISSION_ERROR = "submission_error"
    OUTCOME_UNKNOWN = "outcome_unknown"


# Исход записан: возвращаться в такую сессию автомату незачем.
_SETTLED_STATUSES = frozenset(
    {
        CrmDialogStatus.GATED_LOCAL,
        CrmDialogStatus.SUBMITTED,
        CrmDialogStatus.QUALIFICATION_ERROR,
        CrmDialogStatus.SUBMISSION_ERROR,
        CrmDialogStatus.OUTCOME_UNKNOWN,
    }
)
# Ход за клиентом: автомат ждёт его реплику, а не ответ CRM.
_WAITING_STATUSES = frozenset(
    {
        CrmDialogStatus.NOT_STARTED,
        # В обоих состояниях внешний необратимый POST гарантированно не
        # выполнялся. Их можно безопасно продолжить после рестарта.
        CrmDialogStatus.QUALIFICATION_RETRY_PENDING,
        CrmDialogStatus.SUBMISSION_RETRY_PENDING,
        CrmDialogStatus.AWAITING_SITE_CONSENT,
    }
)
# Всё остальное означает «запрос ушёл, ответ ещё не записан». Перечислять такие
# статусы поимённо не станем: новый статус обмена появится без напоминания и
# молча выпадет из восстановления — ровно так qualifying и submitting и
# провалились мимо всех веток advance.
_INTERRUPTED_STATUSES = (
    frozenset(CrmDialogStatus) - _SETTLED_STATUSES - _WAITING_STATUSES
)


class CrmDialogState(BaseModel):
    """Сериализуемый workflow state без анкетных ПД."""

    model_config = ConfigDict(extra="forbid")

    status: CrmDialogStatus = CrmDialogStatus.NOT_STARTED
    category: int | None = None
    qualified: bool | None = None
    need_site_consent: bool | None = None
    # Точный факт показа шага хранится отдельно от режима offer-page. Иначе
    # safe retry legacy chat-consent менял YES/NO на NOT_REQUIRED.
    site_consent_shown: bool | None = None
    site_consent_answer: bool | None = None
    site_consent_attempts: int = 0
    thanks_url: str | None = None
    # Ссылка на офер с параметрами согласия — та, что ушла клиенту. Хранится
    # ради разбора инцидентов: по ней видно, что именно человек видел.
    offer_url: str | None = None
    # Когда началось ожидание нажатия. По нему считается таймаут, и он же
    # переживает перезапуск контейнера — состояние лежит в базе.
    consent_requested_at: str | None = None
    # Отметка core.py «ссылка на офер уже выдана пузырём» (_site_consent_followup
    # пишет её в сырой dict мимо модели). Без поля в схеме extra="forbid" валил
    # восстановление такой сессии — и с ней весь старт процесса (прод,
    # 27.08.2026: единственная сессия в awaiting_site_consent, backend в
    # crash-loop до ручной чистки state).
    offer_link_sent: bool | None = None
    last_error: str | None = None


class CrmDialogWorkflow:
    """Оркестрирует один CRM qualification/lead lifecycle на сессию."""

    def __init__(
        self,
        qualification_client: CrmQualificationClient,
        lead_coordinator: LeadIntakeCoordinator,
        submission_gate: SubmissionGate,
        *,
        persist: Callable[[Session], None],
        enabled: bool,
        second_realty_enabled: bool = False,
        replan: Callable[[dict], None] | None = None,
    ) -> None:
        self._qualification_client = qualification_client
        self._lead_coordinator = lead_coordinator
        self._submission_gate = submission_gate
        self._persist = persist
        self._second_realty_enabled = second_realty_enabled
        # Ответ ветвителя приходит после того, как вопрос хода уже выбран.
        # Без пересчёта открытая ветка прозвучит следующим ходом — посреди
        # просьбы оставить телефон.
        self._replan = replan
        self.enabled = enabled


    # Поля одного квалифицирующего объекта. Комплект должен быть полным: по
    # половине ответа CRM решение принять не сможет.
    # Регион здесь намеренно не проверяется классом: `region_class` — наш
    # внутренний артефакт маршрутизации, а запросу нужен текст названия, и он
    # бывает у нас тогда, когда DaData место не разобрала. 12.08.2026 живой
    # прогон на этом и встал: «деревня под Тверью» в лид уехала, а ветвитель не
    # запустился. Полноту запроса проверяет map_realty_qualification.
    _FIRST_OBJECT_FIELDS = ("kind", "ownership_form", "owners")

    def check_second_realty(self, session: Session) -> None:
        """Решает, спрашивать ли клиента про второй объект недвижимости.

        Андрей 12.08.2026: realty-qualification — не часть квалификации, а
        ветвитель диалога. На категорию из first-qualification он не влияет
        вообще; он отвечает на один вопрос — стоит ли задать ещё три.

        Смысл конверсионный. У клиента дом в деревне, под залог не годится, но
        может быть ещё квартира в Москве: тогда лид уходит первой категорией,
        самой дорогой, а не проваливается в потреб или БФЛ.

        Отказ CRM трактуется как «не спрашивать». Потерять редкого залогового
        клиента дешевле, чем удлинить анкету всем из-за нашей аварии.
        """
        if not self._second_realty_enabled:
            return
        with session._lock:
            facts = deepcopy(session.state.get("facts") or {})
        if get_fact(facts, "assets.has_realty") is not True:
            return
        if get_fact(facts, "assets.realty.needs_second") is not None:
            return  # уже спрашивали CRM: по одному объекту ответ не меняется
        values = {
            name: get_fact(facts, f"assets.realty.{name}")
            for name in self._FIRST_OBJECT_FIELDS
        }
        if any(value in (None, "") for value in values.values()):
            return

        mapping = map_realty_qualification(facts)
        if mapping.request is None:
            # Не записываем ответ: он пишется навсегда, а объект просто ещё
            # неполон — например, место клиент назовёт следующим ходом.
            # Записать сейчас значит закрыть ветку из-за порядка вопросов.
            return
        try:
            response = self._qualification_client.realty_qualification(mapping.request)
            needs_second = not response.result.is_first_category
        except Exception as exc:  # noqa: BLE001 — ветвитель не должен ронять разговор
            logger.warning(
                "realty-qualification не ответила, второй объект не спрашиваем",
                exc_info=True,
            )
            needs_second = False
            # Ветвитель глотает исход сам (иначе он удлинял бы анкету всем из-за
            # одной нашей аварии), но глотать факт отказа молча — та же дыра,
            # что была у основной квалификации до этой доработки: сбой был
            # виден только в общем логе.
            _record_crm_call_failed(
                session.id,
                stage="realty_qualification",
                reason=getattr(exc, "reason", "http_error"),
                retryable=getattr(exc, "retryable", False),
                http_status=getattr(exc, "http_status", None),
                error_class=type(exc).__name__,
            )
        self._write_needs_second(session, needs_second)

    def _write_needs_second(self, session: Session, value: bool) -> None:
        with session._lock:
            set_by_path(
                session.state.setdefault("facts", {}),
                "assets.realty.needs_second",
                value,
            )
            # Пересчёт под тем же замком: ветка открылась прямо сейчас, и
            # вопрос этого хода должен стать её вопросом, а не следующего.
            if value and self._replan is not None:
                self._replan(session.state)
        self._persist(session)

    def advance(self, session: Session, user_message: str) -> CrmDialogState:
        """Продвигает автомат после ``prepare_turn`` и до ответа модели."""
        if not self.enabled:
            return _read_workflow_state(session)

        # Ветка «уже оставлял заявку»: второй лид не создаётся ни при каком
        # развитии разговора (спека, компонент 2). Проверка стоит до всего,
        # что ходит в CRM, включая вызов по второму объекту.
        with session._lock:
            blocked = lead_blocked(session.state)
        if blocked:
            return _read_workflow_state(session)

        self.check_second_realty(session)

        with session._lock:
            plan = session.state.get("plan")
            plan_action = plan.get("action") if isinstance(plan, dict) else None
            current = _read_workflow_state(session)
            facts = deepcopy(session.state.get("facts") or {})

        # Незавершённый обмен на входе в ход бывает ровно от одного: прошлый ход
        # не дожил до записи исхода. Ход идёт под turn_lock, второго живого
        # advance у сессии нет, значит процесс умер между запросом и ответом.
        # Проверка стоит выше всех остальных: замёрзшую сессию надо снимать с
        # мели независимо от того, куда клиент увёл план следующей репликой.
        recovered = self.recover_interrupted(session)
        if recovered is not None:
            current = recovered

        # Локальный no_fit означает только, что наша матрица исчерпала варианты.
        # Backend остаётся финальным классификатором, а category=null по его
        # контракту всё равно обязан завершиться созданием заполненного лида.
        if plan_action not in {"route_found", "no_fit"}:
            return current
        # Клиент так и не оставил номер, а лимит переспросов исчерпан: план
        # доходит до route_found без телефона. Лид без него всё равно не
        # соберётся — маппинг откажет, — но пойти и упереться в этот отказ
        # значит показать клиенту извинение за сбой, которого не было.
        # 13.08.2026 живой прогон: «приношу извинения, повторить отправку не
        # получится» в ответ на его же «не дам номер».
        if not get_fact(facts, "client.phone"):
            return current
        if current.status in _SETTLED_STATUSES:
            return current

        gate = self._submission_gate.evaluate(facts)
        if not gate.allowed:
            current.status = CrmDialogStatus.GATED_LOCAL
            current.last_error = gate.reason
            self._store_state(session, current)
            _set_local_diagnostic_delivery(session)
            self._persist(session)
            return current

        if current.status == CrmDialogStatus.QUALIFICATION_RETRY_PENDING:
            # Qualification только читает решение CRM, поэтому временный сбой
            # можно безопасно повторить — в отличие от создания лида.
            current.status = CrmDialogStatus.NOT_STARTED
            self._store_state(session, current)

        if current.status == CrmDialogStatus.SUBMISSION_RETRY_PENDING:
            # Этот статус ставится только когда coordinator подтвердил, что
            # lead-intake не начинался (слот БД не занят либо transport off).
            return self._submit_completed(
                session,
                current,
                site_consent_shown=_site_consent_was_shown(current),
                site_consent_answer=current.site_consent_answer,
            )

        if current.status == CrmDialogStatus.AWAITING_SITE_CONSENT:
            if not ask_site_consent_in_chat():
                # Согласие фиксирует сайт по нажатию кнопки. Слова в чате его
                # не заменяют — иначе мы снова начнём утверждать за клиента то,
                # чего он не подтверждал.
                return current
            consent_answer = parse_site_consent_answer(user_message)
            if consent_answer is None:
                # Пустое сообщение — не ответ клиента, а наш собственный ход
                # (например, первый после квалификации): попытку он не тратит.
                if user_message.strip():
                    current.site_consent_attempts += 1
                if current.site_consent_attempts < MAX_SITE_CONSENT_ATTEMPTS:
                    self._store_state(session, current)
                    with session._lock:
                        session.state["dialog_phase"] = "awaiting_site_consent"
                    self._persist(session)
                    return current
                # Потолок исчерпан. Невнятица в ответ на трижды заданный вопрос
                # — не согласие, поэтому в CRM уходит честное «нет». Но лид
                # уходит: пока ветка висела без потолка, дошедший до конца
                # клиент с телефоном не уезжал вообще, пока не замолчит.
                consent_answer = False
            current.site_consent_answer = consent_answer
            self._store_state(session, current)
            return self._submit_completed(
                session,
                current,
                site_consent_shown=True,
                site_consent_answer=consent_answer,
            )

        if current.status == CrmDialogStatus.NOT_STARTED:
            current.status = CrmDialogStatus.QUALIFYING
            current.last_error = None
            self._store_state(session, current)
            self._persist(session)
            mapping = map_first_qualification(facts)
            if not mapping.ready or mapping.request is None:
                current.status = CrmDialogStatus.QUALIFICATION_ERROR
                current.last_error = (
                    mapping.issues[0].message
                    if mapping.issues
                    else "qualification_mapping_not_ready"
                )
                self._store_state(session, current)
                self._set_error_phase(session)
                _record_crm_call_failed(
                    session.id,
                    stage="qualification",
                    reason="mapping_rejected",
                    retryable=False,
                )
                return current

            if mapping.request.registration_address is None:
                # Заказчик 12.08.2026: регион обязателен во всех вертикалях.
                # Отправку не останавливаем — решение о матчинге принимает CRM,
                # и лид без региона лучше потерянного клиента. Но без записи
                # это выглядит как «клиент не подошёл», хотя на деле мы сами
                # прислали пустое поле и получили category=null.
                log_crm_incident(
                    session.id,
                    "region_missing",
                    "регион клиента не определён, категория придёт пустой",
                )

            try:
                response = self._qualification_client.first_qualification(
                    mapping.request
                )
            except CrmApiError as exc:
                current.status = (
                    CrmDialogStatus.QUALIFICATION_RETRY_PENDING
                    if exc.retryable
                    else CrmDialogStatus.QUALIFICATION_ERROR
                )
                current.last_error = str(exc)[:500]
                self._store_state(session, current)
                if exc.retryable:
                    # Попытка ещё не исчерпана — это не отказ, а ожидание
                    # повторного вызова. Если и он не удастся, событие запишет
                    # уже он, своим отдельным проходом через эти же ветки.
                    self._set_pending_phase(session)
                else:
                    self._set_error_phase(session)
                    _record_crm_call_failed(
                        session.id,
                        stage="qualification",
                        reason=exc.reason,
                        retryable=exc.retryable,
                        http_status=exc.http_status,
                        error_class=type(exc).__name__,
                    )
                return current
            except Exception as exc:  # noqa: BLE001 — нормализуем внешний сбой
                current.status = CrmDialogStatus.QUALIFICATION_ERROR
                current.last_error = f"{type(exc).__name__}: {exc}"[:500]
                self._store_state(session, current)
                self._set_error_phase(session)
                _record_crm_call_failed(
                    session.id,
                    stage="qualification",
                    reason="http_error",
                    retryable=False,
                    error_class=type(exc).__name__,
                )
                return current

            decision = decide_qualification(response)
            current.category = decision.category
            current.qualified = decision.qualified
            current.thanks_url = decision.thanks_url
            needs_consent = decision.next_step == "ask_site_consent"

            # Пауза до нажатия кнопки на офер-странице. Она имеет смысл
            # только там, где кнопка есть: на общей странице благодарности её
            # нет (проверено 21.08.2026). Адрес страницы подтверждения ищем по
            # старшинству: карта «категория=адрес» → ссылка из матрицы →
            # общий дефолт. Матричная ссылка в этой роли исторична: у части
            # категорий (залог авто) там витрина ПОСЛЕ согласия, без кнопки, —
            # для них адрес подтверждения задаёт карта.
            #
            # Два алгоритма передачи клиента — по домену страницы (заказчик
            # 27.08.2026: «внутри домена один алгоритм, на сторонние —
            # другой»). Свой домен (тот же, что у callback): страница узнаёт
            # клиента по куке виджета, параметры не дописываем — d_lead_id на
            # «своих» страницах занят под другой формат и, например, на
            # /bfl-finish роняет её с 500. Сторонний домен куки не видит:
            # параметры дописываем, страница читает их из адреса.
            mapped_page = (
                site_consent_offer_pages().get(decision.category)
                if decision.category is not None
                else None
            )
            offer_page = (
                mapped_page or decision.offer_url or site_consent_default_url()
            )
            own_host = (urlsplit(consent_callback_url()).hostname or "").lower()
            external_page = bool(offer_page) and (
                (urlsplit(offer_page).hostname or "").lower() != own_host
            )
            offer_link = None
            if needs_consent and site_consent_pause_enabled():
                # The predicate rejects None; make that invariant explicit for its URL consumers.
                if offer_page is not None and page_has_consent_button(offer_page):
                    # thanks_url в параметрах — страница ПОСЛЕ согласия («URL
                    # спасибо» из матрицы, например витрина партнёров). Когда
                    # он совпадает с самим офером (у ссылок из матрицы
                    # decide_qualification подставляет одну страницу в оба
                    # поля) — не передаём: кнопка вернула бы клиента на тот же
                    # офер уже без параметров, с алертом «d_lead_id не
                    # найден». Без параметра страница уводит на свою
                    # брендированную «спасибо» (свой дефолт есть у всех
                    # офер-страниц заказчика, например /spasibo-nssd).
                    thanks_param = (
                        decision.thanks_url
                        if decision.thanks_url != offer_page
                        else None
                    )
                    offer_link = (
                        build_offer_link(
                            offer_page,
                            session_id=session.id,
                            callback_url=consent_callback_url(),
                            thanks_url=thanks_param,
                            name=client_display_name(session.state.get("facts")),
                        )
                        if external_page
                        else _own_page_link(
                            offer_page,
                            session.state.get("facts"),
                        )
                    )
                if offer_link is None:
                    logger.warning(
                        "CRM просит согласие, но кнопки на офер-странице нет: "
                        "сессия %s уходит сразу, поле согласия — «шаг не "
                        "показывался», страница %s",
                        session.id,
                        offer_page or "не задана",
                    )

            current.need_site_consent = bool(offer_link)
            current.offer_url = offer_link
            if offer_link:
                current.consent_requested_at = _now_iso()
                current.status = CrmDialogStatus.AWAITING_SITE_CONSENT
                self._store_state(session, current)
                with session._lock:
                    session.state["dialog_phase"] = "awaiting_site_consent"
                    session.state["delivery"] = None
                self._persist(session)
                return current

            # Прежний путь: вопрос репликой в чате. Остался под выключателем на
            # случай, если у заказчика маршрутизация ждёт 23000 именно от нас.
            if needs_consent and ask_site_consent_in_chat():
                current.status = CrmDialogStatus.AWAITING_SITE_CONSENT
                self._store_state(session, current)
                with session._lock:
                    session.state["dialog_phase"] = "awaiting_site_consent"
                    session.state["delivery"] = None
                self._persist(session)
                return current

            return self._submit_completed(
                session,
                current,
                site_consent_shown=False,
                site_consent_answer=None,
            )

        # Сюда попадает только статус, который выше уже прошёл восстановление.
        return current

    def retry_pending(self, session: Session) -> CrmDialogState | None:
        """Повторяет только операции, про которые известно: POST лида не шёл.

        Метод вызывается scheduler'ом и следующим пользовательским ходом через
        ``advance``. Для timeout после настоящего lead-intake статус остаётся
        ``outcome_unknown`` и сюда никогда не попадает: дубли по-прежнему
        запрещены.
        """
        current = _read_workflow_state(session)
        if current.status not in {
            CrmDialogStatus.QUALIFICATION_RETRY_PENDING,
            CrmDialogStatus.SUBMISSION_RETRY_PENDING,
        }:
            return None
        return self.advance(session, "")

    def recover_interrupted(self, session: Session) -> CrmDialogState | None:
        """Снимает с мели автомат, чей процесс умер посреди обмена с CRM.

        Раньше восстановление было только у лида (`crm_lead`), а `crm_workflow`
        не читал никто: статусы обмена проваливались мимо всех веток `advance`,
        и сессия стояла молча — ни лида, ни ошибки, ни следа. Клиент мог писать
        сколько угодно: каждый ход возвращал прежний статус.

        Об исходе POST здесь не гадаем — единственный источник правды о лиде
        остаётся coordinator, и at-most-once по-прежнему держит он:

        * лид ещё не уходил (`not_sent` пишется до сети) — повтор безопасен,
          автомат возвращается в начало: first-qualification read-only;
        * лид уже в сети или в CRM — исход отражаем как есть, повторного POST
          coordinator не сделает, а разговор получает свою концовку вместо
          вечного молчания.

        В сеть не ходит: годится и для старта процесса. Возвращает None, если
        восстанавливать нечего.
        """
        current = _read_workflow_state(session)
        if current.status not in _INTERRUPTED_STATUSES:
            return None
        # Тот же запрет автоповтора, что и при старте: лид, чей ответ потерялся
        # вместе с процессом, обязан стать outcome_unknown до любых решений.
        self._lead_coordinator.recover_interrupted_submission(session)
        if _read_lead_state(session).status == LeadSubmissionStatus.NOT_SENT:
            current.status = CrmDialogStatus.NOT_STARTED
            current.last_error = None
            self._store_state(session, current)
            self._persist(session)
            logger.warning(
                "CRM-обмен прерван рестартом, сессия %s продолжит с начала",
                session.id,
            )
            return current
        # Отправка уже случилась: пересобирать ответ клиента не из чего, кроме
        # сохранённого состояния — оно и решало, что уходило в лид.
        return self._submit_completed(
            session,
            current,
            site_consent_shown=_site_consent_was_shown(current),
            site_consent_answer=current.site_consent_answer,
        )

    def confirm_site_consent(self, session: Session) -> CrmDialogState:
        """Клиент нажал кнопку согласия на офер-странице.

        Вызывается эндпоинтом, а не диалогом: согласие приходит с сайта. Лид
        уходит ровно один раз — право отправки захватывается в базе, поэтому
        повторный клик, двойная отправка формы и гонка с таймаутом безопасны.

        Сессия не на паузе — не делаем ничего: это либо уже отправленный лид
        (поздний клик после таймаута), либо чужой номер сессии. Заказчик
        против пересоздания и дообновления уже отправленного лида.
        """
        current = _read_workflow_state(session)
        if current.status != CrmDialogStatus.AWAITING_SITE_CONSENT:
            return current
        current.site_consent_answer = True
        self._store_state(session, current)
        return self._submit_completed(
            session,
            current,
            site_consent_shown=True,
            site_consent_answer=True,
        )

    def expire_site_consent(
        self,
        session: Session,
        *,
        now: datetime | None = None,
        timeout_seconds: int | None = None,
    ) -> CrmDialogState | None:
        """Пятнадцать минут прошли, кнопку никто не нажал.

        Лид уходит без согласия — роутер заказчика уводит такой в колл-центр.
        Возвращает None, если ждать ещё рано или ждать уже нечего: так вызов
        из обхода по расписанию сам решает, было ли что делать.
        """
        current = _read_workflow_state(session)
        if current.status != CrmDialogStatus.AWAITING_SITE_CONSENT:
            return None
        started = _parse_iso(current.consent_requested_at)
        if started is None:
            # Отметки нет — считаем, что ждать уже поздно: сессия попала сюда
            # из старого состояния, и держать её вечно хуже, чем отправить.
            started = datetime.min.replace(tzinfo=timezone.utc)
        deadline = timeout_seconds or site_consent_timeout_seconds()
        moment = now or datetime.now(timezone.utc)
        if (moment - started).total_seconds() < deadline:
            return None
        current.site_consent_answer = False
        self._store_state(session, current)
        logger.info(
            "Согласие не получено за %s с, сессия %s уходит без него",
            deadline,
            session.id,
        )
        return self._submit_completed(
            session,
            current,
            site_consent_shown=True,
            site_consent_answer=False,
        )

    def _submit_completed(
        self,
        session: Session,
        current: CrmDialogState,
        *,
        site_consent_shown: bool,
        site_consent_answer: bool | None,
    ) -> CrmDialogState:
        current.site_consent_shown = site_consent_shown
        current.site_consent_answer = site_consent_answer
        current.status = CrmDialogStatus.SUBMITTING
        self._store_state(session, current)
        self._persist(session)

        outcome = self._lead_coordinator.submit_completed(
            session,
            site_consent_shown=site_consent_shown,
            site_consent_answer=site_consent_answer,
        )
        if outcome.status == LeadSubmissionStatus.SUBMITTED:
            if not outcome.attempted:
                # SUBMITTED без попытки означает чужую, более раннюю отправку:
                # sweeper уехал с брошенной анкетой (filled_out=0), пока клиент
                # молчал, а клиент потом вернулся и дошёл до конца. Единственная
                # разрешённая сессии отправка уже потрачена, и повторять запрос
                # нельзя: idempotency key у CRM нет, дедуп в окне 15 минут вернёт
                # старый leadID и выбросит наше тело, за окном создаст дубль.
                # Финал клиенту оставляем прежний — лид в CRM есть, извиняться
                # не за что, — но полные сведения остались только у нас, а без
                # записи это неотличимо от обычного успешного лида.
                log_crm_incident(
                    session.id,
                    "full_form_not_delivered",
                    "полная анкета не уехала, в CRM остался более ранний лид: "
                    f"{outcome.reason or 'без описания'}",
                )
            current.status = CrmDialogStatus.SUBMITTED
            current.last_error = None
            self._store_state(session, current)
            _set_local_diagnostic_delivery(session)
            self._persist(session)
            return current
        if outcome.status == LeadSubmissionStatus.OUTCOME_UNKNOWN:
            current.status = CrmDialogStatus.OUTCOME_UNKNOWN
        elif (
            outcome.status == LeadSubmissionStatus.NOT_SENT
            and not outcome.attempted
            and outcome.reason
            in {"submission_slot_unavailable", "lead_intake_disabled"}
        ):
            current.status = CrmDialogStatus.SUBMISSION_RETRY_PENDING
            current.last_error = outcome.reason
            self._store_state(session, current)
            self._set_pending_phase(session)
            return current
        else:
            current.status = CrmDialogStatus.SUBMISSION_ERROR
        current.last_error = outcome.reason
        self._store_state(session, current)
        self._set_error_phase(session)
        return current

    def _set_pending_phase(self, session: Session) -> None:
        """Показывает обработку без ложного финала и без технической ошибки."""
        with session._lock:
            session.state["dialog_phase"] = "delivery_pending"
            session.state["delivery"] = {
                "status": "pending",
                "message": "CRM request pending safe retry",
                "mocked": False,
                "attempts": 0,
                "request": {},
            }
        self._persist(session)

    def _store_state(
        self,
        session: Session,
        state: CrmDialogState,
    ) -> None:
        with session._lock:
            session.state[CRM_WORKFLOW_STATE_KEY] = state.model_dump(mode="json")

    def _set_error_phase(self, session: Session) -> None:
        # Единственный проход для всех трёх видов сбоя, поэтому журнал висит
        # здесь: новая ветка ошибки попадёт в него сама, без напоминания.
        state = _read_workflow_state(session)
        log_crm_incident(
            session.id,
            state.status.value,
            state.last_error or "без описания",
        )
        with session._lock:
            session.state["dialog_phase"] = "delivery_error"
            session.state["delivery"] = {
                "status": "error",
                "message": "CRM integration error",
                "mocked": False,
                "attempts": 0,
                "request": {},
            }
        self._persist(session)


# Порядок разбора здесь — часть защиты, а не оформление: отказ проверяется
# первым, поэтому «конечно нет» остаётся отказом. По той же причине сюда нельзя
# позвать routing.normalize.parse_bool: согласие он ищет подстрокой, и «иногда»
# у него уже согласие. Ложное «да» отдаёт контакты клиента партнёру без его
# согласия — это дороже, чем лишний переспрос.
_CONSENT_NEGATIVE = (
    r"\bнет\b",
    r"\bне\s+(?:хочу|согласен|согласна|нужно|нужна|надо|интересно|интересует)\b",
    r"\bотказываюсь\b",
)

# Отложенный ответ — это ещё не ответ, и согласием он не становится даже когда
# начинается со слова из списка ниже: «ладно, давайте потом» — не «давайте».
_CONSENT_DEFERRED = (
    r"\bподума\w*",
    r"\bпотом\b",
    r"\bпозже\b",
    r"\bне\s+знаю\b",
    r"\bсомнева\w*",
)

# Живая речь, а не бланк: на «хотите консультацию?» отвечают «конечно», «ок»,
# «ага», «не откажусь». Аудит 19.08.2026: ни одно из этих слов не проходило, и
# вопрос задавался снова, хотя человек уже согласился.
_CONSENT_POSITIVE = (
    r"\bда\b",
    r"\bага\b",
    r"\bугу\b",
    r"\bконечно\b",
    r"\bок\b",
    r"\bокей\b",
    r"\bok(?:ay)?\b",
    r"\byes\b",
    r"\bсоглас(?:ен|на|ны)\b",
    r"\bхочу\b",
    r"\bдавай(?:те)?\b",
    r"\bможно\b",
    r"\bладно\b",
    r"\bхорошо\b",
    r"\bне\s+откажусь\b",
    r"\bне\s+против\b",
    r"\bполучить консультацию\b",
)


def parse_site_consent_answer(message: str) -> bool | None:
    """Распознаёт явный ответ на текущий yes/no-вопрос, включая живую речь."""
    normalized = " ".join(message.casefold().split())
    if not normalized:
        return None
    if any(re.search(pattern, normalized) for pattern in _CONSENT_NEGATIVE):
        return False
    if any(re.search(pattern, normalized) for pattern in _CONSENT_DEFERRED):
        return None
    # Сообщение из одних плюсов — согласие жестом, а не словом. Правилом, а не
    # перечислением «+», «++», «+1»: вариантов у этого жеста ровно столько,
    # сколько раз человек нажмёт клавишу.
    if re.fullmatch(r"\++\d*", normalized.replace(" ", "")):
        return True
    if any(re.search(pattern, normalized) for pattern in _CONSENT_POSITIVE):
        return True
    return None


def _read_workflow_state(session: Session) -> CrmDialogState:
    raw = session.state.get(CRM_WORKFLOW_STATE_KEY)
    if isinstance(raw, dict):
        return CrmDialogState.model_validate(raw)
    return CrmDialogState()


def _site_consent_was_shown(state: CrmDialogState) -> bool:
    """Совместимость старых сессий и точное значение для новых retry."""
    if state.site_consent_shown is not None:
        return state.site_consent_shown
    return bool(state.need_site_consent)


def _read_lead_state(session: Session) -> LeadSubmissionState:
    """Читает состояние лида так же, как его пишет coordinator."""
    raw = session.state.get(CRM_LEAD_STATE_KEY)
    if isinstance(raw, dict):
        return LeadSubmissionState.model_validate(raw)
    return LeadSubmissionState()


def _set_local_diagnostic_delivery(session: Session) -> None:
    """Показывает привычный полный JSON, не вызывая legacy router endpoint."""
    with session._lock:
        plan_object = session.state.get("_plan_object")
        matched = getattr(plan_object, "matched", None)
        facts = deepcopy(session.state.get("facts") or {})
        payload = LeadRoutingRequest.from_facts(facts, matched).as_json_dict()
        session.state["delivery"] = delivery.DeliveryResult(
            status="completed",
            message="Опрос завершён",
            request=payload,
            mocked=True,
            attempts=0,
        ).as_dict()
        session.state["dialog_phase"] = "survey_completed"


def _record_crm_call_failed(
    session_id: str,
    *,
    stage: str,
    reason: str,
    retryable: bool,
    http_status: int | None = None,
    error_class: str | None = None,
) -> None:
    """Кладёт факт неудавшегося вызова CRM в аналитику.

    Ровно один ряд на один остановившийся на ошибке вызов, без dedup_key: та
    же сессия может упасть сперва на qualification, а после восстановления —
    ещё раз на lead_intake, и это два разных факта, а не дубль одного. Пишется
    только для qualification/realty_qualification — lead_intake свой источник
    правды держит `lead_intake_coordinator._record_crm_call_failed`, чтобы не
    задвоить событие через relay в `_submit_completed`.
    """
    payload: dict[str, object] = {
        "stage": stage,
        "reason": reason,
        "retryable": retryable,
    }
    if http_status is not None:
        payload["http_status"] = http_status
    if error_class is not None:
        payload["error_class"] = error_class
    analytics.get_recorder().emit(analytics.EVENTS.CRM_CALL_FAILED, session_id, payload)


__all__ = [
    "CRM_WORKFLOW_STATE_KEY",
    "MAX_SITE_CONSENT_ATTEMPTS",
    "CrmDialogState",
    "CrmDialogStatus",
    "CrmDialogWorkflow",
    "CrmQualificationClient",
    "parse_site_consent_answer",
]
