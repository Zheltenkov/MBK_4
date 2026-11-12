"""Переписка целиком в базе заказчика — читать диалоги, не читая номер клиента.

Заказчик попросил складывать все диалоги в его Postgres: «в нашу БД нужно
писать все диалоги, чтобы потом просто было их смотреть». Единственное, что он
сам назвал персональными данными, — номер телефона: «его хэшируй, и всё».

Отсюда две вещи, за которые отвечает модуль.

Первая: номер не попадает в таблицу ни в каком виде. Он заменяется на отпечаток
`[телефон a1b2c3d4e5f6]` прямо в тексте реплики. Отпечаток считается от
нормализованного номера, поэтому «8 999…» и «+7 999…» одного человека дают
одинаковую строку, и лид в CRM с диалогом связать можно.

Вторая: отпечаток солёный. Номеров в России порядка миллиарда — голый SHA-256
перебирается на ноутбуке за минуты, то есть «хэш» без соли равен открытому
номеру. Соль задаётся `MBK_DIALOG_PHONE_SALT` и хранится там же, где остальные
секреты. Без неё модуль берёт случайную на процесс: номер по-прежнему не
восстановить, но отпечатки одного клиента разойдутся после перезапуска — про
это пишется предупреждение в лог, чтобы настройку не забыли.

Остальной текст сохраняется как есть: это и есть предмет просьбы. Имя и город
из переписки не вырезаются — иначе читать её незачем.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from routing.normalize import parse_phone


logger = logging.getLogger(__name__)

# Тот же шаблон, что у маскирования для модели: российский номер в любой
# записи, какой его набирает человек в чате.
_PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:"
    r"(?:\+7|8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}"
    r"|7[\s\-()]*9\d{2}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}"
    r"|9\d{2}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}"
    r")(?!\d)"
)

# Половины SHA-256 хватает: коллизия на миллиарде номеров невероятна, а строка
# остаётся читаемой глазом в выборке.
_FINGERPRINT_LENGTH = 12

ROLE_CLIENT = "client"
ROLE_ASSISTANT = "assistant"

_salt: bytes | None = None


def _phone_salt() -> bytes:
    global _salt
    if _salt is None:
        configured = (os.getenv("MBK_DIALOG_PHONE_SALT") or "").strip()
        if configured:
            _salt = configured.encode("utf-8")
        else:
            _salt = secrets.token_bytes(32)
            logger.warning(
                "transcripts: MBK_DIALOG_PHONE_SALT не задан — отпечатки "
                "телефонов будут разными после каждого перезапуска"
            )
    return _salt


def reset_salt_cache() -> None:
    """Сбрасывает разобранную соль. Нужно тестам, меняющим окружение."""
    global _salt
    _salt = None


def phone_fingerprint(phone: str) -> str:
    """Устойчивый отпечаток номера: одинаковый для всех его написаний."""
    normalized = parse_phone(phone) or re.sub(r"\D", "", phone)
    digest = hmac.new(
        _phone_salt(), str(normalized).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return digest[:_FINGERPRINT_LENGTH]


def hash_phones(text: str) -> str:
    """Заменяет каждый номер в тексте его отпечатком."""
    return _PHONE_PATTERN.sub(
        lambda match: f"[телефон {phone_fingerprint(match.group(0))}]", text
    )


@dataclass(frozen=True)
class TranscriptMessage:
    """Одна реплика диалога — строка таблицы `dialog_messages`."""

    session_id: str
    turn: int
    position: int
    role: str
    message: str
    occurred_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class TranscriptSink(Protocol):
    def write(self, message: TranscriptMessage) -> None: ...


class NullTranscriptSink:
    """Хранение переписки не настроено: бот работает как раньше."""

    enabled = False

    def write(self, message: TranscriptMessage) -> None:
        return None


class MemoryTranscriptSink:
    """Приёмник для тестов."""

    enabled = True

    def __init__(self) -> None:
        self.messages: list[TranscriptMessage] = []

    def write(self, message: TranscriptMessage) -> None:
        self.messages.append(message)


class TranscriptRecorder:
    """Единственная точка записи переписки.

    Номера вычищаются здесь, а не в приёмнике: правило должно работать
    одинаково и на Postgres, и на MemorySink в тестах.
    """

    def __init__(self, sink: TranscriptSink) -> None:
        self.sink = sink

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.sink, "enabled", True))

    def record_turn(
        self,
        session_id: str,
        turn: int,
        client_message: str,
        assistant_bubbles: list[str],
        *,
        occurred_at: datetime | None = None,
    ) -> None:
        """Пишет ход целиком: реплику клиента и все пузыри ответа.

        Отказ хранилища проглатывается по тому же правилу, что и в аналитике:
        потерянная строка переписки дешевле оборванного разговора.
        """
        if not self.enabled or not session_id:
            return
        stamp = occurred_at or datetime.now(timezone.utc)
        position = 0
        pending: list[TranscriptMessage] = []
        if client_message and client_message.strip():
            pending.append(
                TranscriptMessage(
                    session_id=session_id,
                    turn=turn,
                    position=position,
                    role=ROLE_CLIENT,
                    message=hash_phones(client_message.strip()),
                    occurred_at=stamp,
                )
            )
            position += 1
        for bubble in assistant_bubbles:
            if not bubble or not bubble.strip():
                continue
            pending.append(
                TranscriptMessage(
                    session_id=session_id,
                    turn=turn,
                    position=position,
                    role=ROLE_ASSISTANT,
                    message=hash_phones(bubble.strip()),
                    occurred_at=stamp,
                )
            )
            position += 1
        for message in pending:
            try:
                self.sink.write(message)
            except Exception:  # noqa: BLE001 — переписка не роняет диалог
                logger.warning(
                    "transcripts: реплика %s/%s потеряна",
                    session_id,
                    message.position,
                    exc_info=True,
                )


def transcripts_enabled() -> bool:
    raw = (os.getenv("MBK_DIALOG_TRANSCRIPTS_ENABLED") or "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


__all__ = [
    "MemoryTranscriptSink",
    "NullTranscriptSink",
    "ROLE_ASSISTANT",
    "ROLE_CLIENT",
    "TranscriptMessage",
    "TranscriptRecorder",
    "TranscriptSink",
    "hash_phones",
    "phone_fingerprint",
    "reset_salt_cache",
    "transcripts_enabled",
]
