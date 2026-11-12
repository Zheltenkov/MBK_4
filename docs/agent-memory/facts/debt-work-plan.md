---
name: debt-work-plan
description: План v2.26 от 09.11 — текущая анкета, долговая ветка, онлоадер и разговорный слой
metadata:
  type: reference
---

Текущий план: `docs/superpowers/plans/2026-11-06-sites-debt-onloader.md`, v2.26.
Рабочая копия: `ПЛАН_РАБОТ_МБК.md`; ссылки адаптированы к корню.
Основание/разбор: `РАЗБОР_ОТВЕТОВ_МИХАИЛА_2026-11-06.md`.
Текст переписки: `docs/v5/2026-11-06-mikhail-chat.md`; исходники голосовых
и относительные таймкоды указаны в разборе. Это не проверка production-настроек.
Версия 1.1 от 02.11 сохранена как историческая.
Read-context архитектура и границы первого этапа:
`docs/adr/0004-authorized-current-application-context.md`; текст решения не копировать.
Долговая policy и её границы:
`docs/adr/0005-local-debt-questionnaire-policy.md`; текст решения не копировать.
Переход в том же чате, сохранение истории/контактов и восстановление закреплены в ADR-0005.
Запуск локального стенда — `site_widget/stand/README.md`.
Онлоадер с известным контактом и локальным backend-owned сигналом —
`docs/adr/0006-local-onloader-continuation.md`. Живой источник, согласие и
поставка в mbk-site требуют согласования Андрея.
Надёжное локальное сохранение сессий/решений и границы Postgres —
`docs/adr/0007-local-durable-sessions.md`.
Разговорная речь новых веток и границы технического preview —
`docs/adr/0008-policy-conversation-voice.md`.
Готовые уточнения Андрею/Михаилу: `СЛЕДУЮЩИЕ_ШАГИ_МБК.md`, не отправлены.
Таблица короткой анкеты и восемь fixtures от 02.11 остаются исходной подготовкой,
а не контрактом нового site/onloader пути. 104 прежних теста его не доказывают.
Оперативные read-only проверки лидов — [[production-lead-checks]].

Связано: [[debt-branch-scope]], [[current-agenda]], [[crm-source-audit]].

После ответа Андрея 17:31–17:53 найден готовый БФЛ-путь, уточнены
lookup/ADDRESS_PROVINCE и пределы shipped/postback — [[andrey-contract-2026-11-06]].
