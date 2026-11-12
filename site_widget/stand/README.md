# Стенд виджета

Проверка правок `_ai-chat.js` и `_!ai-chat.scss` так, как их увидит клиент:
собранный `app.js` с сайта, настоящая разметка виджета, живой бот.

Зачем: тесты на jsdom ловят логику, но не ловят сборку и стили. Собранный
бандл — единственное, что реально уезжает на сайт, поэтому смотреть надо на
него, а не на исходник модуля.

## Как поднять

1. Клон сайта: `git clone --depth 1 --branch master <mbk-site> site-full`.
2. Зависимости сборки: в `www/resources/from_verstala` выполнить
   `npm ci --ignore-scripts` (без флага падает сборка `pngquant`, к делу не
   относящаяся) и создать пустышку `gulp/settings/ftp.json`
   (`{"host": "", "user": "", "pass": "", "remote": ""}`) — файл с доступами в
   репозиторий не коммитится, а `gulpfile` его требует.
3. Собрать **на node 20**: на node 24 минификатор стилей падает
   (`util.isRegExp is not a function`). Webpack 4 требует
   `NODE_OPTIONS=--openssl-legacy-provider`.

   ```
   NODE_OPTIONS=--openssl-legacy-provider node node_modules/gulp/bin/gulp.js js
   NODE_OPTIONS=--openssl-legacy-provider node node_modules/gulp/bin/gulp.js sass
   ```

   Проверено 25.09.2026: сборка нетронутых исходников master даёт `app.js` и
   `app.css` байт в байт как закоммиченные у заказчика.
4. Поднять бота: `MBK_PUBLIC_CHAT=1 python -m uvicorn main:app --port 8000`
   (в бою чат публичный, локально по умолчанию требует вход).
5. Поднять стенд:

   ```
   MBK_SITE_PUBLIC=<...>/site-full/www/public python serve.py
   ```

   Страница ждёт `index.html` рядом с `serve.py`: разметка из
   `www/resources/views/ai-chat-widget.blade.php` без blade-комментариев плюс
   вендорные скрипты из `header.blade.php` (jQuery, js.cookie и прочие — без
   `Cookies` бандл падает).

Стенд отдаёт статику сайта и проксирует `/chat-api/` в `/api/` бота, как это
делает nginx в бою. Системный прокси при этом обходится: с ним локальный вызов
получает закрытое соединение.

## Текущий чат и переход к долгам

Основная проверка доступна на `http://127.0.0.1:8879/widget/`.
Откройте чат, сообщите имя и телефон, затем попросите помочь с долгами.
Продолжение использует ту же сессию, историю и известные сведения.
Вспомогательная страница `/debt/` выбрана Codex для локальных проверок.
Стили собираются из текущего `_!ai-chat.scss`.
Команды выполняются из корня MBK_v4 в PowerShell.

```powershell
$stageAssets = Join-Path $env:TEMP 'mbk-debt-preview'
New-Item -ItemType Directory -Path $stageAssets -Force | Out-Null
$widgetSource = '$red: #ff0600;' + "`n" + '$white: #ffffff;' + "`n" + (Get-Content -Raw -LiteralPath 'site_widget/_!ai-chat.scss')
Set-Content -LiteralPath (Join-Path $stageAssets 'widget.scss') -Value $widgetSource -Encoding utf8
npm exec --yes --package=sass@1.93.2 -- sass --no-source-map --style=expanded "$stageAssets/widget.scss" "$stageAssets/widget.css"
.venv313/Scripts/python -m site_widget.stand.debt_preview --assets-dir $stageAssets
```

Sass запускается из временного npm-окружения. Стенд использует реальные
маршруты создания сессии, owner cookies, долговую policy и SSE. На границах
извлекателя и географии установлены локальные ответы. Состояние сессий хранится
в памяти процесса, отправка удерживается серверной policy. Environment и запуск
приложения изолированы в `debt_preview.py`.

Обычный виджет создаёт сессию при открытии чата. Запрос о долгах переключает
сценарий внутри текущего SID с исходной owner cookie. Уже известные сведения
повторно не спрашиваются. После обрыва читается сохранённая история,
неподтверждённый текст остаётся черновиком в текущей вкладке.
Вспомогательная `/debt/` открывает панель сразу и создаёт свою сессию по кнопке
либо первому сообщению, используя отдельный ключ хранения и owner cookie.

Проверки выполняются командами `npm --prefix site_widget test` и
`.venv313/Scripts/python -m pytest tests/test_same_chat_debt_transition.py tests/test_debt_landing_http.py tests/test_debt_preview.py`.

## Онлоадер в текущем виджете

Для локального подтверждённого контакта используется тот же `/widget/`.
Запустите отдельный экземпляр с уже собранными стилями.

```powershell
$stageState = Join-Path $env:LOCALAPPDATA 'MBK-local-preview-state'
.venv313/Scripts/python -m site_widget.stand.debt_preview --port 8882 --assets-dir $stageAssets --state-dir $stageState --onloader-fixture
```

Откройте `http://127.0.0.1:8882/widget/` и обычную кнопку чата.
Host fixture получает capability для фактического SID через принадлежащую
посетителю сессию. Виджет показывает предложение с кнопками разрешения и отказа.
Текстовая реплика «да» продолжает чат, решение принимается отдельной кнопкой.
Телефон остаётся у источника, нового контактного факта не создаётся.

Только этот opt-in preview содержит искусственный issuer. Основное приложение
получает сервис через composition root, реальный issuer остаётся за backend МБК.
SQLite sessions и журнал `onloader.sqlite` сохраняются в `$stageState` вне repo.
Приватные ключи создаются там один раз. Для перезапуска используйте прежний
каталог и тот же порт. Сохраняются исходные SID, owner cookie, история и решения.
Маскированная диагностика находится в подкаталоге `diagnostics`.
Незавершённое решение восстанавливается для ручной проверки результата.
Источник сохраняет прежний срок авторизации, обновление подтверждается явно.
Queued означает локальную очередь сигнала. Транспорт отправки в CRM/компанию
в этом режиме отсутствует. Каноническое состояние после сбоя читается тем же
GET, проверка неизвестного результата выполняется отдельной ручной кнопкой.

Приёмка реализована в `tests/test_onloader_local_http.py` и
`site_widget/test/onloader.test.mjs`. Контракты и зависимости указаны в
`docs/adr/0006-local-onloader-continuation.md`.

Надёжная запись и восстановление описаны в `docs/adr/0007-local-durable-sessions.md`.
Такой же `--state-dir` работает для обычной и долговой консультации без
`--onloader-fixture`. Для временного preview используются команды из раздела выше.
Перед боевым подключением нужна отдельная реализация строгого Postgres ACK/CAS.

### Речь в техническом preview

Preview выключает языковую модель и использует тестовые ответы для проверки
переходов, хранения и передачи. На `/widget/` это обозначено над виджетом.
По этим репликам нельзя оценивать тон рабочего бота.
Разговорный слой новых веток описан в `docs/adr/0008-policy-conversation-voice.md`.
