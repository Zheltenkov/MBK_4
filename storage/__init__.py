"""Долговременное хранение состояния диалогов.

Отделено от `analytics`: там события без персональных данных и роль только на
чтение для дата-инженера заказчика, здесь — анкетные ПД, которые наружу не
отдаются вообще.
"""
from storage.sessions import (
    PostgresSessionRepository,
    SessionRecord,
    SessionRepository,
    build_session_repository,
)

__all__ = [
    "PostgresSessionRepository",
    "SessionRecord",
    "SessionRepository",
    "build_session_repository",
]
