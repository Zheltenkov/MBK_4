"""Fixed SELECT queries for the recovered production lead report."""

from dataclasses import dataclass
from typing import LiteralString


@dataclass(frozen=True)
class ReportQuery:
    key: str
    title: str
    sql: LiteralString


# All time bounds use the same database snapshot and explicit Moscow dates.
QUERIES = (
    ReportQuery(
        "daily", "По дням, МСК",
        """
        WITH days AS (
          SELECT (%(as_of)s AT TIME ZONE 'Europe/Moscow')::date - n AS day
          FROM generate_series(0, %(days)s - 1) AS n
        )
        SELECT to_char(d.day, 'YYYY-MM-DD') AS day,
          count(DISTINCT a.session_id) FILTER (WHERE event='lead_submitted') AS leads,
          count(DISTINCT a.session_id) FILTER (
            WHERE event='lead_submitted' AND nullif(payload->>'lead_id','') IS NOT NULL
          ) AS leads_with_crm_id,
          count(DISTINCT a.session_id) FILTER (
            WHERE event='qualification_completed'
          ) AS local_questionnaires_completed,
          count(*) FILTER (WHERE event='crm_call_failed') AS crm_failures
        FROM days d LEFT JOIN analytics_events a
          ON a.occurred_at >= d.day::timestamp AT TIME ZONE 'Europe/Moscow'
          AND a.occurred_at < (d.day + 1)::timestamp AT TIME ZONE 'Europe/Moscow'
          AND a.occurred_at <= %(as_of)s
          AND a.event IN ('lead_submitted','qualification_completed','crm_call_failed')
        GROUP BY d.day ORDER BY d.day
        """,
    ),
    ReportQuery(
        "month", "Текущий календарный месяц, МСК",
        """
        SELECT count(DISTINCT session_id) AS leads,
          count(DISTINCT session_id) FILTER (
            WHERE nullif(payload->>'lead_id','') IS NOT NULL
          ) AS leads_with_crm_id
        FROM analytics_events
        WHERE event='lead_submitted' AND occurred_at <= %(as_of)s
          AND occurred_at >= date_trunc('month', %(as_of)s AT TIME ZONE 'Europe/Moscow')
            AT TIME ZONE 'Europe/Moscow'
        """,
    ),
    ReportQuery(
        "latest", "Последние подтверждённые отправки",
        """
        SELECT to_char(occurred_at AT TIME ZONE 'Europe/Moscow',
                       'YYYY-MM-DD HH24:MI:SS') AS submitted_msk,
          payload->>'lead_id' AS crm_lead_id
        FROM analytics_events
        WHERE event='lead_submitted' AND occurred_at <= %(as_of)s
        ORDER BY occurred_at DESC, id DESC LIMIT 6
        """,
    ),
    ReportQuery(
        "hourly", "Сегодня по часам, МСК",
        """
        SELECT to_char(occurred_at AT TIME ZONE 'Europe/Moscow', 'HH24') AS hour,
          count(DISTINCT session_id) FILTER (WHERE event='lead_submitted') AS leads,
          count(*) FILTER (WHERE event='turn_completed') AS turns
        FROM analytics_events
        WHERE occurred_at >= date_trunc('day', %(as_of)s AT TIME ZONE 'Europe/Moscow')
            AT TIME ZONE 'Europe/Moscow'
          AND occurred_at <= %(as_of)s AND event IN ('lead_submitted','turn_completed')
        GROUP BY 1 ORDER BY 1
        """,
    ),
    ReportQuery(
        "recent_cohort", "Недавние сессии: когорта по дате создания",
        """
        WITH cohort AS (
          SELECT s.*,
            EXISTS (SELECT 1 FROM dialog_messages m WHERE m.session_id=s.id
              AND m.role='client' AND m.occurred_at <= %(as_of)s) AS wrote,
            EXISTS (SELECT 1 FROM analytics_events a WHERE a.session_id=s.id
              AND a.event='lead_submitted' AND a.occurred_at <= %(as_of)s) AS sent,
            nullif(btrim(s.state #>> '{facts,client,phone}'), '') IS NOT NULL AS phone
          FROM sessions s
          WHERE s.created_at >= %(as_of)s - %(recent_hours)s * interval '1 hour'
            AND s.created_at <= %(as_of)s
        )
        SELECT to_char(created_at AT TIME ZONE 'Europe/Moscow', 'YYYY-MM-DD') AS day,
          CASE WHEN state #>> '{attribution,landing}' LIKE '%%bfl-finish%%'
            THEN 'bfl-finish' ELSE 'other_or_unknown' END AS landing_group,
          count(*) AS sessions, count(*) FILTER (WHERE wrote) AS wrote,
          count(*) FILTER (WHERE sent) AS leads,
          count(*) FILTER (WHERE sent AND state #>> '{crm_workflow,status}'='submitted'
            AND nullif(state #>> '{crm_workflow,category}', '') IS NOT NULL
          ) AS submitted_with_crm_category,
          count(*) FILTER (WHERE phone AND NOT sent AND
            created_at < %(as_of)s - %(grace_minutes)s * interval '1 minute'
          ) AS phone_without_lead_all_scenarios
        FROM cohort GROUP BY 1,2 ORDER BY 1,2
        """,
    ),
    ReportQuery(
        "crm_failures", "Ошибки CRM за последние 72 часа",
        """
        SELECT payload->>'stage' AS stage, payload->>'reason' AS reason,
          count(*) AS failures,
          to_char(max(occurred_at) AT TIME ZONE 'Europe/Moscow',
                  'YYYY-MM-DD HH24:MI:SS') AS latest_msk
        FROM analytics_events
        WHERE event='crm_call_failed'
          AND occurred_at >= %(as_of)s - interval '72 hours' AND occurred_at <= %(as_of)s
        GROUP BY 1,2 ORDER BY 1,2
        """,
    ),
    ReportQuery(
        "phone_without_lead", "Телефон есть, лида нет: кандидаты для разбора",
        """
        SELECT s.id AS session_id,
          to_char(s.created_at AT TIME ZONE 'Europe/Moscow',
                  'YYYY-MM-DD HH24:MI:SS') AS created_msk,
          coalesce(s.state #>> '{entry,scenario}', 'questionnaire') AS scenario,
          s.state->>'dialog_phase' AS dialog_phase,
          s.state #>> '{crm_workflow,status}' AS crm_status,
          s.state #>> '{crm_lead,status}' AS lead_status,
          EXISTS (SELECT 1 FROM analytics_events a WHERE a.session_id=s.id
            AND a.event='cancel_requested' AND a.occurred_at <= %(as_of)s) AS cancel_seen,
          count(*) OVER () AS total_candidates
        FROM sessions s
        WHERE s.created_at >= %(as_of)s - %(recent_hours)s * interval '1 hour'
          AND s.created_at < %(as_of)s - %(grace_minutes)s * interval '1 minute'
          AND nullif(btrim(s.state #>> '{facts,client,phone}'), '') IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM analytics_events a WHERE a.session_id=s.id
            AND a.event='lead_submitted' AND a.occurred_at <= %(as_of)s)
        ORDER BY s.created_at LIMIT 50
        """,
    ),
    ReportQuery(
        "coverage", "Границы доступных данных",
        """
        SELECT 'analytics_events' AS source,
          to_char(min(occurred_at) AT TIME ZONE 'Europe/Moscow',
                  'YYYY-MM-DD HH24:MI:SS') AS oldest_msk,
          to_char(max(occurred_at) AT TIME ZONE 'Europe/Moscow',
                  'YYYY-MM-DD HH24:MI:SS') AS newest_msk
        FROM analytics_events WHERE occurred_at <= %(as_of)s
        UNION ALL
        SELECT 'sessions',
          to_char(min(created_at) AT TIME ZONE 'Europe/Moscow', 'YYYY-MM-DD HH24:MI:SS'),
          to_char(max(created_at) AT TIME ZONE 'Europe/Moscow', 'YYYY-MM-DD HH24:MI:SS')
        FROM sessions WHERE created_at <= %(as_of)s
        """,
    ),
)
