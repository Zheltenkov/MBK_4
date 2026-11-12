---
name: gitlab-access-map
description: Подтверждён вход и чтение GitLab 02.11.2026 — новый хост, 17 проектов и новые project ID
metadata:
  type: reference
---

Проверенная карта группы и commit SHA: `КАРТА_GITLAB_МБК.md`.
Хост `https://gitlab.mbk-credit.ru`, группа mbk ID 3, аккаунт v.zheltenkov.
Browser login успешен; same-origin GET API прочитал полный список 17 проектов
и файлы четырёх основных репозиториев. Содержимое остальных не изучалось.
mbk_ai ID 4/main, crm-mbk ID 2/main, mbk-site ID 1/master, mbk-partners ID 3/master.
Код бота: custom/vendor-src. Старые numeric ID и MR нельзя переносить вслепую.
Права на push и deployed version этим чтением не подтверждены.

Работает browser-session access, не старый PAT: тот ранее возвращал 401.
Новый токен не создавался. Пароль/cookies/одноразовые ссылки не сохранены;
при окончании browser session восстанавливать обычный вход в целевой GitLab.
Статическая сверка контрактов и сравнение с локальным кодом выполнены:
[[crm-source-audit]]. Значения правил/кампаний БД и runtime не проверены.

Связано: [[mbk-production-setup]], [[mbk-partners]], [[debt-work-plan]].
