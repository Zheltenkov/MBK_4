"""Срок хранения должен доходить до диска, а не только до памяти и базы.

Аудит 19.08.2026. `drop_stale` чистит память и Postgres, а снимки диалогов в
`logs/sessions/{id}.json` не удаляет никто: во всём репозитории не было ни
одного `unlink`. Комментарий в коде при этом утверждал, что «здесь истекает срок
хранения ПД».

Заказчику мы обещали иначе: «Сессия вместе с персональными данными удаляется
через 72 часа» (docs/INSTALL.md).

Цена промаха уже известна: снимки копились в контейнере месяцами, и когда
разработчик заказчика распаковал образ, одиннадцать файлов уехали в чужой git —
вместе с телефоном живого клиента.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import pytest

import session_store
from session_store import SessionStore


@pytest.fixture
def sessions_dir(tmp_path, monkeypatch):
    directory = tmp_path / "sessions"
    directory.mkdir()
    monkeypatch.setattr(session_store, "SESSIONS_DIR", directory)
    return directory


def _snapshot(directory, name: str, *, age_hours: float) -> "os.PathLike":
    path = directory / name
    path.write_text(json.dumps({"id": name[:-5]}), encoding="utf-8")
    stamp = time.time() - age_hours * 3600
    os.utime(path, (stamp, stamp))
    return path


def test_expired_snapshot_is_removed_from_disk(sessions_dir):
    old = _snapshot(sessions_dir, "aaaaaaaaaaaa.json", age_hours=100)

    asyncio.run(SessionStore().drop_stale(max_age_hours=72))

    assert not old.exists(), "снимок с ПД пережил срок хранения"


def test_fresh_snapshot_survives(sessions_dir):
    fresh = _snapshot(sessions_dir, "bbbbbbbbbbbb.json", age_hours=1)

    asyncio.run(SessionStore().drop_stale(max_age_hours=72))

    assert fresh.exists(), "живой диалог удалять нельзя"


def test_leftover_temporary_files_are_cleaned_too(sessions_dir):
    """Запись идёт через .tmp; оборванная запись оставляет его навсегда."""
    stale_tmp = _snapshot(sessions_dir, "cccccccccccc.json.tmp", age_hours=100)

    asyncio.run(SessionStore().drop_stale(max_age_hours=72))

    assert not stale_tmp.exists()


def test_broken_disk_does_not_break_the_sweep(sessions_dir, monkeypatch):
    """Чистка диска не должна ронять чистку памяти и базы."""
    _snapshot(sessions_dir, "dddddddddddd.json", age_hours=100)

    def _boom(*args, **kwargs):
        raise OSError("диск недоступен")

    monkeypatch.setattr(os, "unlink", _boom)

    dropped = asyncio.run(SessionStore().drop_stale(max_age_hours=72))

    assert dropped == 0
