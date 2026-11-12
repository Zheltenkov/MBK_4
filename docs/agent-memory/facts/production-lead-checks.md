---
name: production-lead-checks
description: Восстановлены и проверены 02.11.2026 прямые read-only проверки лидов Claude; команда и доступы
metadata:
  type: reference
---

Инструкция и смысл показателей: `ПРОВЕРКА_ЛИДОВ_МБК.md`.
Запуск из корня: `.venv313/Scripts/python -m analytics.production_leads`.
Код и фиксированные SELECT: `analytics/production_leads.py`,
`analytics/lead_report_queries.py`; тесты — `tests/test_production_leads_report.py`.

Проверен прямой PostgreSQL `111.88.127.189:54234`, database `mbk`, role `mbk_app`.
GitLab/SSH не нужны. Пароль читается из локального `доступ.txt`, ключ
`mbk_app_password`; альтернатива — MBK_REPORT_PASSWORD в окружении процесса.
Значения секретов не писать в память, параметры команд, отчёт и сообщения.
Это прежний способ Claude: role приложения, но транзакция принудительно
read-only до первого SELECT, затем проверка server transaction_read_only=on.
Права самой роли не объявляются ограниченными SELECT.

Исходные db.py/leads_now.py/crm_check.py/loss_funnel.py сохранены с SHA-256:
`C:\Users\zvp\.agent-memory\archives\MBK\2026-10-31-094031-827335\restored-lead-checks-2026-11-02\`.
Оперативные результаты сверены с исходным leads_now.py на живой базе.
Срез 02.11 16:35:53 МСК: `ОТЧЁТ_ЛИДЫ_2026-11-02.md`;
машинный артефакт — `logs/production-leads-2026-11-02.json` (gitignored).
За свежими числами запускать команду, не повторять этот срез как текущий.

Категория CRM доступна только у сохранившихся sessions, не из локального
qualification_completed. Телефон без лида — кандидат на разбор, не доказанная
потеря; landing исторический. Подробности ограничений — в инструкции.
Автоматический 30-дневный funnel этим CLI не реализован.

Связано: [[funnel-counting-trap]], [[project-identity]], [[debt-work-plan]].
