---
name: andrey-contract-2026-11-06
description: Ответ Андрея — crm-lookup, БФЛ-правила, маркеры отправки, готовая анкета и состояние локального стенда
metadata:
  type: reference
---

Контракт, ограничения и подтверждения свежим кодом:
`ОТВЕТЫ_АНДРЕЯ_2026-11-06.md`. Токен CRM lookup получен в чате, значение
в файлы/контейнеры не переносилось и production API не вызывался.
Переменная клиента — MBK_CRM_LOOKUP_TOKEN, это не GitLab-токен.
План актуализирован до v2.7 — [[debt-work-plan]].
Изоляция и запуск локального контура: `dev/mbk-local/README.md`;
прежнее покрытие protocol-приложений/fixtures — `dev/mbk-local/STATUS.md`;
прогон с настоящим Битриксом — `dev/mbk-local/NATIVE-RESULT.md`.
Наличие контейнера не означает сквозную доставку. Свежие source snapshots — gitignored
`logs/local-stack-2026-11-06/sources/`.
Пользователь 06.11.2026 подтвердил: готовой обезличенной базы Битрикса и
инструкции её запуска нет. Локальная проверка строится на явно
искусственных fixtures; вывод о совпадении с production из этого не следует.
Границы synthetic write-stage и уровни доказательства —
`docs/adr/0002-local-synthetic-write-boundary.md` (решение не дублировать в памяти).
Границы официального native-контура и восстановления отправки —
`docs/adr/0003-local-native-bitrix-chain.md`; результат хранить в отчёте, не копировать сюда.
Получателей из партнёрки Андрей пока не выдаёт; не блокировать первый этап
поиском полного buyer receipt и не выдавать CRM shipped за принятого клиента.

Связано: [[crm-source-audit]], [[debt-branch-scope]], [[gitlab-access-map]].
