"""Событие, приёмники и запись с изоляцией отказов."""
from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence, Set as AbcSet
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol


logger = logging.getLogger(__name__)


# Ключи, наличие которых в payload означает утечку ПД во вторую систему
# хранения. Набор шире, чем `observability._REDACT_KEYS`: там значения
# редактируются, здесь запись вообще не должна была их получить.
PERSONAL_DATA_KEYS = frozenset(
    {
        "phone",
        "name",
        "full_name",
        "first_name",
        "last_name",
        "middle_name",
        "patronymic",
        "birth_date",
        "passport",
        "inn",
        "snils",
        "email",
        "address",
        "registration_address",
        "living_address",
    }
)


class EVENTS:
    """Каталог имён. Строки уезжают в чужие отчёты — менять их нельзя молча.

    Разделение зон, зафиксированное заказчиком 30.07.2026:

        старт чата                          — мы    -> CHAT_STARTED
        количество квалификаций             — мы    -> QUALIFICATION_COMPLETED
        собранные номера всего              — CRM
        подошедшие (по категории)           — CRM
        отправленные (по микросервису)      — CRM

    Всё, что считает CRM, мы не пишем вовсе. Технически могли бы: маршрут
    выбирает наш движок, ссылку запрашиваем мы. Но два источника для одного
    числа не сходятся, а расхождение в отчёте потом ищут долго.

    Поле `matched` в QUALIFICATION_COMPLETED остаётся — без него неизвестно,
    сработала ли маршрутизация вообще, и разбирать инциденты нечем. Это НЕ
    «количество подошедших»: его считает CRM по своей категории.

    `LEAD_SUBMITTED` добавлено 04.09.2026 по просьбе дата-инженера заказчика:
    ему нужно связать разговор бота с карточкой в Битриксе. Номер лида CRM
    возвращает в ответе на lead-intake, и он уже хранится в состоянии сессии —
    но `sessions` содержит ПД, и роль `analytics_ro` доступа туда не имеет (см.
    `storage/migrations/001_sessions.sql`). Событие выносит в аналитику ровно
    один ключ связи и ничего больше.

    Это НЕ счётчик лидов: их по-прежнему считает CRM, и два источника для
    одного числа не сходятся. Здесь `count` даст только «сколько разговоров
    получили номер лида» — величину для сверки, а не для отчёта.

    Брошенных анкет отдельным событием нет намеренно — они выводятся запросом:
        SELECT count(*) FROM (
            SELECT session_id FROM analytics_events GROUP BY session_id
            HAVING bool_or(event = 'chat_started')
               AND NOT bool_or(event = 'qualification_completed')
        ) t;
    Событие «клиент ушёл» пришлось бы порождать по таймеру, и оно врало бы для
    тех, кто вернулся позже.
    """

    CHAT_STARTED = "chat_started"
    TURN_COMPLETED = "turn_completed"
    QUALIFICATION_COMPLETED = "qualification_completed"
    LEAD_SUBMITTED = "lead_submitted"
    # turn_completed в списке заказчика нет: это наш счётчик отвала по шагам,
    # и критерий «80% доходят до конца» считается только по нему.

    # Развилка на входе (спека 09.09.2026). `entry_routed` — какой сценарий
    # ведёт разговор: пишется на первой реплике и при каждой смене сценария.
    # По нему считается разрез «сколько диалогов ушло в каждую ветку», и по
    # нему же разговоры веток исключаются из воронки анкеты.
    ENTRY_ROUTED = "entry_routed"
    # Local debt questionnaire progress is distinct from CRM qualification/delivery.
    DEBT_POLICY_TURN = "debt_policy_turn"
    # Local backend signal progress is separate from commercial lead delivery.
    ONLOADER_STATE_CHANGED = "onloader_state_changed"
    POLICY_VOICE_RENDERED = "policy_voice_rendered"
    # Обращение человека, который уже оставлял заявку: выгрузка «кто просил
    # связаться». Лид по такому обращению не создаётся намеренно.
    EXISTING_CLIENT_REQUEST = "existing_client_request"

    # Чипсы в приветствии (спека 14.09.2026): клиент первой репликой нажал
    # кнопку. `payload->>'choice'` — `money` («Получить деньги») или `other`
    # («Другой вопрос»). По нему считается, сколько раньше молчавших
    # посетителей начали разговор кнопкой.
    QUICK_REPLY_USED = "quick_reply_used"

    # Отказ вызова CRM (дизайн согласован 07.09.2026, сделано по команде
    # 19.09.2026 — docs/agent-memory/facts/crm-failure-visibility.md).
    # Событие про НАШ неудавшийся вызов, а не про количество лидов: правило
    # «CRM считает своё» не нарушено, потому что число неудачных попыток
    # никто, кроме нас, не знает — самой CRM неизвестно о звонках, которые до
    # неё не дошли или получили ответ, не прошедший наш контракт. Повод:
    # дефект `widget_denis` (CRM добавила поле в ответ, наш строгий контракт
    # падал) три дня ронял каждую квалификацию — девятнадцать клиентов без
    # лида, и заметили это только по расхождению отчётов, потому что ноль
    # строк в аналитике означал одновременно «никто не дошёл» и «всё
    # сломано», а различить было нечем.
    CRM_CALL_FAILED = "crm_call_failed"

    # Клиент просит отменить заявку, не звонить или удалить номер и данные
    # (бой 28.09.2026). Бот этого не умеет и честно так отвечает; событие —
    # чтобы такие обращения можно было выгрузить и разобрать, пока у
    # заказчика нет процесса («нужен процесс создания таких обращений»,
    # 30.09.2026). `payload`: `phone_given`, `lead_sent`, `turn` — без ПД.
    CANCEL_REQUESTED = "cancel_requested"


@dataclass(frozen=True)
class AnalyticsEvent:
    """Одна строка таблицы `analytics_events`."""

    event: str
    session_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    dedup_key: str | None = None
    occurred_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class AnalyticsSink(Protocol):
    """Приёмник событий. Реализация вправе бросать — вызов защищён Recorder'ом."""

    def write(self, event: AnalyticsEvent) -> None: ...


class NullSink:
    """Аналитика не настроена: приложение работает ровно как раньше."""

    enabled = False

    def write(self, event: AnalyticsEvent) -> None:
        return None


class MemorySink:
    """Приёмник для тестов и локальной отладки."""

    enabled = True

    def __init__(self) -> None:
        self.events: list[AnalyticsEvent] = []

    def write(self, event: AnalyticsEvent) -> None:
        self.events.append(event)


class AnalyticsRecorder:
    """Единственная точка записи событий.

    Проверку ПД делает здесь, а не в приёмнике: она должна срабатывать
    одинаково и на Postgres, и на MemorySink в тестах.
    """

    def __init__(self, sink: AnalyticsSink) -> None:
        self.sink = sink

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.sink, "enabled", True))

    def emit(
        self,
        event: str,
        session_id: str,
        payload: dict[str, Any] | None = None,
        *,
        dedup_key: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        """Записывает событие. Не бросает ничего, кроме нарушения контракта ПД.

        Отказ хранилища намеренно проглатывается: потерянное событие дешевле
        оборванного разговора. Нарушение контракта ПД, наоборот, поднимается —
        это ошибка разработчика, и она должна падать на тестах, а не тихо
        уезжать в базу, которую читает посторонний.
        """
        body = payload or {}
        _reject_personal_data(body)

        record = AnalyticsEvent(
            event=event,
            session_id=session_id,
            payload=body,
            dedup_key=dedup_key,
            occurred_at=occurred_at or datetime.now(timezone.utc),
        )
        try:
            self.sink.write(record)
        except Exception:  # noqa: BLE001 — аналитика не роняет диалог
            logger.warning("analytics: событие %s потеряно", event, exc_info=True)


def _reject_personal_data(value: Any, path: str = "") -> None:
    """Ищет запрещённые ключи всюду, куда заглянет json.dumps при записи.

    Правило, а не перечисление форм: спускаемся в любой контейнер. Обход,
    знавший только dict, молча пропускал `{"clients": [{"phone": ...}]}` —
    список сериализуется целиком, и телефон уезжал в таблицу, которую читает
    посторонний. Перечислять «dict и ещё list» бессмысленно: завтра появится
    кортеж или множество, и дыра откроется снова.

    Строки и байты исключены намеренно: они тоже последовательности, и наивный
    обход разобрал бы их посимвольно. Проверка ключевая, а не по значениям —
    `{"tags": ["a", "b"]}` обязан оставаться разрешённым.
    """
    if isinstance(value, Mapping):
        for key, nested in value.items():
            where = f"{path}.{key}" if path else str(key)
            if str(key).lower() in PERSONAL_DATA_KEYS:
                raise ValueError(
                    f"персональные данные в payload аналитики: {where}. "
                    "Таблицу читает посторонний — связь с клиентом только через "
                    "session_id."
                )
            _reject_personal_data(nested, where)
        return

    if isinstance(value, (str, bytes, bytearray)):
        return

    # Sequence/Set, а не Iterable: генератор от обхода опустел бы, и в базу
    # уехал бы пустой payload вместо данных.
    if isinstance(value, (Sequence, AbcSet)):
        for index, item in enumerate(value):
            _reject_personal_data(item, f"{path}[{index}]")
