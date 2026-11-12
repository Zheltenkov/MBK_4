"""Срок хранения распространяется на журнал реплик, а не только на сессию.

Из состояния сессии персональные данные уходят по таймеру: и из памяти, и из
базы. А рядом лежит `logs/dialog_<id>.jsonl` — тот же разговор построчно, и он
не удаляется ничем. Телефон там замаскирован, но имя и город клиент называет
обычными словами («Ирина», «живу в Москве»), маскировать их нечем, и файлы
копятся в примонтированном томе бессрочно.

Хранить копию разговора дольше, чем сам разговор, мы не договаривались:
удалять надо по тому же сроку и тем же обходом.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

import logger
import main
from session_store import SessionStore


RETENTION_HOURS = 72


def _write_dialog(session_id: str) -> Path:
    logger.log_dialog(
        session_id,
        "Ирина, живу в Москве",
        True,
        {"dialog_stage": "greeting", "message_count": 1},
    )
    return Path(logger.LOG_DIR) / f"dialog_{session_id}.jsonl"


def _age(path: Path, hours: float) -> None:
    old = time.time() - hours * 3600
    os.utime(path, (old, old))


@pytest.fixture()
def logs_in_tmp(tmp_path, monkeypatch):
    """Журнал пишется относительно рабочего каталога — туда и уводим."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_old_dialog_log_is_deleted_by_retention(logs_in_tmp):
    stale = _write_dialog("aaaaaaaaaaaa")
    _age(stale, RETENTION_HOURS + 1)

    logger.drop_stale_dialog_logs(RETENTION_HOURS)

    assert not stale.exists()


def test_todays_dialog_log_stays(logs_in_tmp):
    """Разговор идёт прямо сейчас — журнал нужен для разбора инцидента."""
    fresh = _write_dialog("bbbbbbbbbbbb")

    logger.drop_stale_dialog_logs(RETENTION_HOURS)

    assert fresh.exists()


def test_summary_of_an_old_dialog_goes_too(logs_in_tmp):
    """Итог разговора лежит рядом и относится к тому же клиенту."""
    logger.log_summary("cccccccccccc", {"dialog_stage": "closed", "message_count": 9})
    summary = Path(logger.LOG_DIR) / "summary_cccccccccccc.json"
    _age(summary, RETENTION_HOURS + 1)

    logger.drop_stale_dialog_logs(RETENTION_HOURS)

    assert not summary.exists()


def test_crm_incident_journal_is_not_touched(logs_in_tmp):
    """Общий журнал сбоев не привязан к клиенту и нужен для долгого разбора.

    Номера в нём уже скрыты, а вопрос «почему лид не доехал» приходит через
    неделю — см. logger.log_crm_incident.
    """
    logger.log_crm_incident("dddddddddddd", "500", "CRM вернула ошибку")
    incidents = Path(logger.CRM_INCIDENT_LOG)
    _age(incidents, RETENTION_HOURS * 10)

    logger.drop_stale_dialog_logs(RETENTION_HOURS)

    assert incidents.exists()


def test_retention_sweep_clears_both_the_session_and_its_log(logs_in_tmp):
    """Один обход — один срок хранения: иначе половина следов остаётся."""
    store = SessionStore(max_extract_workers=1)
    session = asyncio.run(store.create(anketa=None))
    stale = _write_dialog(session.id)
    _age(stale, RETENTION_HOURS + 1)

    asyncio.run(main._retention_sweep(store, RETENTION_HOURS))

    assert not stale.exists()


def test_a_dialog_log_keeps_its_shape(logs_in_tmp):
    """Страховка от «почистили заодно и содержимое»."""
    path = _write_dialog("eeeeeeeeeeee")

    logger.drop_stale_dialog_logs(RETENTION_HOURS)

    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert record["session_id"] == "eeeeeeeeeeee"
