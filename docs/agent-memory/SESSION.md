# Состояние сессии

Обновлён: 2026-11-12 · Агент: Codex · Ветка: dev · Рабочее дерево: грязно (304 файла).

## Задача
По запросу пользователя создать приватный GitHub MBK_4 и загрузить текущий проект.
Отправка ожидает согласования состава и сообщения коммита по прежнему прямому правилу пользователя.

## Сделано
- Создан https://github.com/Zheltenkov/MBK_4, private, без auto_init; аккаунт Zheltenkov проверен через connector и REST.
- Исходные ветка dev, origin https://github.com/Zheltenkov/MBK_v4 и HEAD9e0db4a сохранены; коммитов/push в них нет.
- Отдельная копия: C:/Users/zvp/AppData/Local/MBK-github-export-MBK_4-20261112/project/, новый Git dev, origin MBK_4. Только staged, коммитов0/push0.
- 671 исходный файл +GITHUB_SNAPSHOT.md =672 staged. Дополнен только экспортный .gitignore защитой локальных секретов/БД/логов/outputs.
- Исключены outputs2generated (матрица.xlsx/inspect), .env/доступы, базы/диалоги, логи, окружения, кэши и пакеты; исходная Markdown-матрица включена.
- История старого Git остаётся в исходном репозитории. Экспорт — новый текущий срез всех tracked/untracked разрешённых файлов, включая прежние незакоммиченные изменения.
- Форматные token/password-подобные совпадения проверены: тестовые/пример/переменная. Совпадений с5действующими локальными ключами нет; проверки содержимого staged blobs также без секретов и лишних runtime-путей.
- В экспорте нет .github/workflows; отправка не запускает деплой. Python/app исходники скопированы побайтно; тесты повторно не запускались, новых изменений кода нет.
- Подробности, source hashes и review: C:/Users/zvp/AppData/Local/MBK-github-export-MBK_4-20261112/{UPLOAD_REVIEW.md,upload-manifest.json,final-preflight.json}.
- Предложенный коммит: «Сохранить текущую версию МБК и локальные проверки».

## Сохранённый технический статус
- Сравнение4Cмоделей/40запросов: ни одна не прошла заранее фиксированный порог; C остаётсяPro, GQwen/E/FPro, private.env не менялся. Поиск моделей остановлен.
- Исправлены semantic/deterministic fronted ownNo и сохранение приCtimeout; No/phone/consent независимы. 477целевых/14файлов PASS, Ruff/Pyright0; source/No защиты сохранены.
- Полный13Polza local: median8.020/max23.917s,0providerfailures/0business;5/8debtfallback, стиль/задержкиPARTIAL. All469sourceequal. Не объявлять таймауты устранёнными навсегда.
- Currentmanual http://127.0.0.1:8891/:7sessions/revisions/keyexact afterrestart;8893closed. Продакшен не затронут.
- Отчёт docs/v5/2026-11-12-validator-model-comparison.md, ADR0009дополнение12.11; private evidence MBK-validator-model-compare-20261112/MBK-validator-extra-20261112/MBK-validator-live-20261112 вLocalAppData.

## Следующий шаг
- Получить согласование именно подготовленного состава и сообщения коммита. До него gitcommit/push не выполнять.
- После согласования: проверить staged/source drift и remoteprivate/empty; gitcommit в отдельной export-копии, gitpush origin dev; проверить SHA/файлы/visibility/defaultbranch GitHub. Старый origin не заменять.
- Поддерживать основной проект по согласованному dev→MR Андрею; личная GitHub-копия не разрешает боевую выкатку.

## Открытые вопросы
- Подтверждение отправки и текста коммита → пользователь.
- Задержки/5из8fallback остаются технической задачей; следующий разбор причин code/F-rejection и лишнихG2 без ослабления защиты.

## Не трогать
- Боевой сервер/SSH/удалённую БД даже read-only. Не выполнять деплой.
- Приватные ключи/пароли/локальные базы и значения .env не переносить и не печатать.
- Исходный Git/301старых dirtydiff, прежние raw evidence и failed proofs; глобальную Codex memory не обновлять.
- ПередPython/app/test: LANGFUSE_DISABLED=true,LANGFUSE_TRACING_ENABLED=false,OTEL_SDK_DISABLED=true.
