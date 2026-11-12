import glob
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Dict, Optional


logger = logging.getLogger("mbk.logger")

LOG_DIR = "logs"
CRM_INCIDENT_LOG = f"{LOG_DIR}/crm_incidents.jsonl"

# Что заведено под конкретного клиента и живёт по сроку хранения его данных.
# Общий журнал сбоев CRM сюда не входит намеренно: он не привязан к клиенту,
# номера в нём скрыты, а вопрос «почему лид не доехал» приходит через неделю.
_PER_SESSION_LOGS = ("dialog_*.jsonl", "summary_*.json")

# Телефон, номер карты, длинный номер счёта — всё, что выглядит как непрерывная
# цепочка из шести и более цифр. В тексте ошибки CRM возвращает кусок того, что
# мы ей отправили, а туда входит телефон клиента.
_LONG_DIGIT_RUN = re.compile(r"\d{6,}")
_EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b", re.IGNORECASE)
_PII_FIELD_VALUE = re.compile(
    r"(?P<prefix>[\"']?(?:phone|telephone|email|name|full_name|fio|address|"
    r"registration_address)[\"']?\s*[:=]\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^;}\r\n]+)",
    re.IGNORECASE,
)


def _sanitize_incident_detail(detail: object) -> str:
    """Оставляет код/тип сбоя, но вырезает значения известных PII-полей."""
    text = str(detail)
    text = _PII_FIELD_VALUE.sub(
        lambda match: f"{match.group('prefix')}[данные скрыты]",
        text,
    )
    text = _EMAIL.sub("[email скрыт]", text)
    return _LONG_DIGIT_RUN.sub("[номер скрыт]", text)[:500]


def ensure_log_dir():
    """Создаёт папку logs, если её нет"""
    os.makedirs(LOG_DIR, exist_ok=True)


def drop_stale_dialog_logs(max_age_hours: int) -> int:
    """Удаляет журналы разговоров старше срока хранения.

    Из состояния сессии персональные данные уходят по таймеру — и из памяти, и
    из базы. А рядом лежит построчная копия того же разговора: телефон в ней
    замаскирован, но имя и город клиент называет обычными словами («Ирина»,
    «живу в Москве»), маскировать их нечем. Файлы лежат в примонтированном
    томе и не удалялись ничем, то есть копия разговора переживала сам разговор
    на неопределённый срок.

    Возраст берётся по времени последней записи: пока в файл дописывают, он
    считается свежим.

    Сбой уборки не имеет права стоить клиенту разговора — как и везде в этом
    модуле, ошибка проглатывается.
    """
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for pattern in _PER_SESSION_LOGS:
        for path in glob.glob(os.path.join(LOG_DIR, pattern)):
            try:
                if os.path.getmtime(path) >= cutoff:
                    continue
                os.remove(path)
                removed += 1
            except OSError:
                logger.warning("Не удалось удалить устаревший журнал %s", path)
    if removed:
        logger.info("Журналы разговоров старше %s ч удалены: %s", max_age_hours, removed)
    return removed


def log_crm_incident(session_id: str, status: str, detail: str) -> None:
    """Запись о том, что лид не уехал в CRM.

    Уведомлений у нас нет: канала со стороны МБК не назвали, и 12.08.2026
    заказчик решил «пока пиши внутренний лог». Отдельный файл, а не
    `logger.warning`, потому что вопрос «почему лид не доехал» приходит не в
    момент сбоя, а через неделю — к этому времени поток `docker logs` уже
    провернулся.

    Сбой диагностики не имеет права стоить клиенту разговора, поэтому любая
    ошибка записи проглатывается.
    """
    try:
        ensure_log_dir()
        record = {
            "occurred_at": datetime.now().isoformat(),
            "session_id": session_id,
            "status": status,
            "detail": _sanitize_incident_detail(detail),
        }
        with open(CRM_INCIDENT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — журнал не важнее диалога
        logger.warning("Не удалось записать сбой CRM для сессии %s", session_id)


def log_dialog(
    session_id: str,
    message: str,
    is_user: bool,
    state: Dict,
    analysis: Optional[Dict] = None,
    anketa_id: Optional[str] = None
):
    """Логирует каждое сообщение"""
    ensure_log_dir()

    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "session_id": session_id,
        "anketa_id": anketa_id or "unknown",
        "is_user": is_user,
        "message": message,
        "selected_case": state.get("selected_case"),
        "dialog_stage": state.get("dialog_stage"),
        "message_count": state.get("message_count"),
        "analysis": analysis or {}
    }

    filename = f"{LOG_DIR}/dialog_{session_id}.jsonl"

    with open(filename, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")


def log_summary(session_id: str, final_state: Dict):
    """Итоговый summary по завершении диалога"""
    ensure_log_dir()
    summary = {
        "session_id": session_id,
        "finished_at": datetime.now().isoformat(),
        "selected_case": final_state.get("selected_case"),
        "dialog_stage": final_state.get("dialog_stage"),
        "total_messages": final_state.get("message_count", 0),
        "success": final_state.get("dialog_stage") in ["offer", "closed"]
    }

    with open(f"{LOG_DIR}/summary_{session_id}.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"✅ Диалог {session_id} сохранён в logs/")
