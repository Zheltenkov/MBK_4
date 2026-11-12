"""Reproducible, read-only equivalent of Claude's production lead checks."""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

import psycopg
from pydantic import BaseModel

from analytics.lead_report_queries import QUERIES

Cell = str | int | float | bool | None


class ReportConfigurationError(ValueError):
    """Missing or invalid local reporting credentials/configuration."""


@dataclass(frozen=True)
class ConnectionSettings:
    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)


class ReportSection(BaseModel):
    key: str
    title: str
    columns: list[str]
    rows: list[list[Cell]]


class LeadReport(BaseModel):
    snapshot_utc: str
    snapshot_msk: str
    database: str
    role: str
    transaction_read_only: Literal["on"]
    days: int
    recent_hours: int
    grace_minutes: int
    sections: list[ReportSection]


def load_password(path: Path) -> str:
    """Use the same credential label as the old db.py, without logging values."""
    try:
        content = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        raise ReportConfigurationError("Не удалось прочитать файл доступа.") from None
    match = re.search(r"(?m)^[ \t]*mbk_app_password:[ \t]*(\S+)", content)
    if match is None:
        raise ReportConfigurationError("В файле доступа нет mbk_app_password.")
    return match.group(1)


def _cell(value: object) -> Cell:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Неподдерживаемый тип ячейки: {type(value).__name__}")


def collect_report(
    settings: ConnectionSettings, *, days: int = 8,
    recent_hours: int = 60, grace_minutes: int = 60,
) -> LeadReport:
    # The server enforces read-only before the first query; all queries share a snapshot.
    with psycopg.connect(
        host=settings.host, port=settings.port, dbname=settings.database,
        user=settings.user, password=settings.password, connect_timeout=10,
        options="-c default_transaction_read_only=on -c statement_timeout=20000 -c lock_timeout=3000",
    ) as conn:
        conn.read_only = True
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        with conn.cursor() as cur:
            cur.execute("""SELECT now(),
              to_char(now() AT TIME ZONE 'Europe/Moscow', 'YYYY-MM-DD HH24:MI:SS'),
              current_database(), current_user, current_setting('transaction_read_only')""")
            clock = cur.fetchone()
            if clock is None or not isinstance(clock[0], datetime) or clock[4] != "on":
                raise RuntimeError("Не подтверждена read-only транзакция.")
            as_of = clock[0]
            params = {"as_of": as_of, "days": days, "recent_hours": recent_hours,
                      "grace_minutes": grace_minutes}
            sections: list[ReportSection] = []
            for query in QUERIES:
                cur.execute(query.sql, params)
                columns = [column.name for column in cur.description or ()]
                sections.append(ReportSection(
                    key=query.key, title=query.title, columns=columns,
                    rows=[[_cell(value) for value in row] for row in cur.fetchall()],
                ))
            return LeadReport(
                snapshot_utc=as_of.isoformat(), snapshot_msk=str(clock[1]),
                database=str(clock[2]), role=str(clock[3]), transaction_read_only="on",
                days=days, recent_hours=recent_hours, grace_minutes=grace_minutes,
                sections=sections,
            )


def render_markdown(report: LeadReport) -> str:
    lines = ["# Проверка лидов МБК", "",
             f"Срез: {report.snapshot_msk} МСК. Транзакция: read-only, repeatable read.",
             f"Дни: {report.days}; недавняя когорта: {report.recent_hours} ч; "
             f"ожидание до разбора: {report.grace_minutes} мин.", "",
             "Анкеты — локальное завершение, не категория CRM. Категория доступна только",
             "в сохранившихся сессиях; отсутствие лида у телефона — кандидат, не доказанная потеря.",
             "landing_group основана на историческом landing, не гарантирует текущую страницу."]
    for section in report.sections:
        lines.extend(["", f"## {section.title}", ""])
        if not section.rows:
            lines.append("Нет строк.")
            continue
        lines.append("| " + " | ".join(section.columns) + " |")
        lines.append("| " + " | ".join("---" for _ in section.columns) + " |")
        for row in section.rows:
            values = [str(value).replace("|", "\\|").replace("\n", " ")
                      if value is not None else "—" for value in row]
            lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Только чтение: проверка лидов боевой БД МБК.")
    parser.add_argument("--host", default=os.getenv("MBK_REPORT_HOST", "111.88.127.189"))
    parser.add_argument("--port", type=int, default=54234)
    parser.add_argument("--database", default="mbk")
    parser.add_argument("--user", default="mbk_app")
    parser.add_argument("--access-file", type=Path,
                        default=Path(__file__).resolve().parents[1] / "доступ.txt")
    parser.add_argument("--days", type=int, default=8)
    parser.add_argument("--recent-hours", type=int, default=60)
    parser.add_argument("--grace-minutes", type=int, default=60)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not (1 <= args.days <= 90 and 1 <= args.recent_hours <= 168
            and 1 <= args.grace_minutes < args.recent_hours * 60 and 1 <= args.port <= 65535):
        parser.error("Некорректные интервалы отчёта или порт.")
    try:
        # No secret is accepted through command-line flags or included in the report.
        password = os.getenv("MBK_REPORT_PASSWORD") or load_password(args.access_file)
        settings = ConnectionSettings(args.host, args.port, args.database, args.user, password)
        report = collect_report(settings, days=args.days, recent_hours=args.recent_hours,
                                grace_minutes=args.grace_minutes)
        output = report.model_dump_json(indent=2) + "\n" if args.format == "json" else render_markdown(report)
        if args.output:
            args.output.write_text(output, encoding="utf-8")
            print(f"Сохранено: {args.output}. Срез {report.snapshot_msk} МСК, read-only.")
        else:
            print(output, end="")
        return 0
    except ReportConfigurationError as exc:
        print(f"Конфигурация: {exc}")
    except psycopg.Error as exc:
        # Database error text may contain connection details or client data.
        print(f"Ошибка БД: {type(exc).__name__}; SQLSTATE={exc.sqlstate or 'unknown'}.")
    except (OSError, TypeError, RuntimeError) as exc:
        print(f"Отчёт не сформирован: {type(exc).__name__}.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
