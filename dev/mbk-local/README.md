# Локальная среда МБК

Есть два отдельных контура. Прежний **protocol_simulation** сохранён с его
очередями и ID симулятора. Новый контур использует отдельные процессы сайта,
CRM, официального демо-Битрикса и партнёрки с локальным тестовым получателем.
В новом Битриксе создан настоящий лид №1 и доставлен локальному получателю;
независимая сверка пройдена с явно отмеченным восстановлением отправки.
Результат, ограничения и команды — [NATIVE-RESULT.md](NATIVE-RESULT.md).

[STATUS.md](STATUS.md) описывает прежние проверки protocol-режима.
Архитектурные границы: [ADR-0002](../../docs/adr/0002-local-synthetic-write-boundary.md)
и [ADR-0003](../../docs/adr/0003-local-native-bitrix-chain.md).

Приложения доступны только внутри Docker: `http://site:8000`,
`http://crm:8000`, `http://partners:8000`. Host ports отсутствуют.
PHP built-in server предназначен для этой лаборатории.
Браузер Windows не открывать: Docker internal не ограничивает его JS-запросы.

## Возобновление прежней protocol-среды

Из корня MBK_v4, с уже созданными ignored sources/env/dependency volumes:

```powershell
docker compose -f dev/mbk-local/compose.yaml -f dev/mbk-local/apps.compose.yaml -f dev/mbk-local/protocol.compose.yaml --profile apps --profile protocol up -d
.venv313/Scripts/python dev/mbk-local/verify_runtime.py site crm partners protocol-receiver
docker compose -f dev/mbk-local/compose.yaml -f dev/mbk-local/apps.compose.yaml -f dev/mbk-local/protocol.compose.yaml --profile apps --profile protocol ps
```

В текущем режиме использовать все три compose-файла. Env уже созданы; resume
не запускает генераторы, INSERT fixtures или новые workers.

Runner сначала проверяет публичный IP с хоста, затем тот же адрес из всех
контейнеров и локальный capture. Проверяет действительные container IDs,
сети, host ports/overrides/socket/proxy и RO rootfs, сохраняет actual env SHA256
без значений. Если активен protocol overlay, неполный scope из трёх приложений
отклоняется. Без свежего положительного
host-control проверки нет. Старый артефакт сначала заменяется статусом checking.
Прямой запуск probe без control ID намеренно завершается ошибкой.

```powershell
Get-Content -Raw dev/mbk-local/http_smoke.py | docker compose -f dev/mbk-local/compose.yaml exec -T capture python -
```

Сейчас diagnostic smoke даёт **15 из 16**: `/bankruptcy` возвращает 404,
поскольку CMS-контент не импортирован. Поэтому exit code 1 и status failed
здесь ожидаемо показывают неполную готовность. Восемь CRM проверок используют
искусственные правила, не выгрузку действующих настроек.

## Подготовка с нуля

Нужны проверенные текстовые снимки в `logs/local-stack-2026-11-06/sources/`;
Git SHA/manifest перечислены в STATUS. Это выборочный snapshot, не полный clone.
Production env/storage/cached config и дампы с данными не переносить в runtime.

```powershell
docker build -f dev/mbk-local/Dockerfile.php -t mbk-local/php:8.4 dev/mbk-local
.venv313/Scripts/python dev/mbk-local/make_local_env.py
.venv313/Scripts/python dev/mbk-local/prepare_dependencies.py 1
.venv313/Scripts/python dev/mbk-local/prepare_dependencies.py 2
.venv313/Scripts/python dev/mbk-local/prepare_dependencies.py 3
```

Env-генератор создаёт четыре отдельных файла со свежими локальными ключами/
паролями и не читает customer env. Существующие credentials не перезаписывает:
они связаны с текущим DB volume. Значения в stdout не выводятся.
Dependency builder видит только composer.json/lock/private package, без app/env/DB:

```powershell
$projectId = 1 # затем 2 и 3
$dependencyRoot = (Resolve-Path "logs/local-stack-2026-11-06/dependencies/project-$projectId").Path
docker volume create --label mbk.local.data-origin=composer-locked-dependencies --label "mbk.local.project=$projectId" "mbk-local-lab-vendor-$projectId-20261106"
docker run --rm --network bridge --user 0:0 --cap-drop ALL --security-opt no-new-privileges:true -e COMPOSER_ALLOW_SUPERUSER=1 -e COMPOSER_MIRROR_PATH_REPOS=1 -e COMPOSER_CACHE_DIR=/tmp/composer -e COMPOSER_PROCESS_TIMEOUT=120 -v "${dependencyRoot}:/inputs" -v "mbk-local-lab-vendor-${projectId}-20261106:/inputs/vendor" -w /inputs --entrypoint composer mbk-local/php:8.4 install --no-scripts --no-plugins --no-interaction --prefer-dist --no-progress
```

Runtime остаётся non-root и подключён только к internal сети. Vendor монтируется RO
из Linux volumes: распаковка Google package через Windows bind превышала 300 секунд.
Версии/commit refs lock сохранены; изменены только dist URL на тот же codeload
commit и private dist на локальную копию. Хеши — DEPENDENCY-MANIFEST.json.

Для нового protocol-mode дополнительно нужен make_protocol_env.py: два ignored
env с новыми локальными credentials/namespace, без production secrets и без
перезаписи существующих env. Новый volume mbk-local-protocol-ledger-20261106-v1
создан отдельно; только его пустой root передан user1000 через network-none/
read-only helper с единственной capability CHOWN. Для повторной подготовки
проверять происхождение volume; текущий стенд восстанавливается resume выше.

БД инициализируется пустыми схемами. `prepare_schema.py` извлекает только DDL
и проверенные имена миграций. Сырой partners SQL содержит данные и не исполнялся.
Отдельно импортированы queue schema, synthetic CRM reader fixture, leads_bfl schema,
две synthetic landing routes и manufactured counter fixture. Customer seeders,
штатные compose/crontab/supervisor не запускались.

## Бизнес-проверки и остановка

`site_bfl_smoke.py` создаёт четыре новых искусственных лида через настоящие
session/XSRF cookies: create → step → complete. Повтор меняет БД и добавляет
четыре job; это не read-only health check. `verify-site-bfl.php` отдельно проверяет
фактические строки/категории, очередь и локальный sender target.

Исходные три site jobs всё ещё pending. В failed сохранены три наблюдавшиеся
попытки: исходная неполная CRM БД, capture без receipt и первый несовместимый
wire-контракт. Новые jobs выполнялись только по одному в отдельной очереди
local_synthetic_write_stage с GNU timeout15s/--once/--tries=1.
Единственный Direct job находится в local_protocol_direct_unexecuted, attempts0,
reserved=NULL; CRM workers и schedulers не работают. Production Битрикс не запускался.
Capture не выдаёт leadID. Отдельный protocol receiver выдаёт ID только после
commit своего SQLite ledger с evidence_level=protocol_simulation. Он не заменяет
CCrmLead::Add и не доказывает exactly-once для downstream effects.
BITRIX24_URL у сайта адресует Laravel CRM, у CRM сейчас — отдельный simulator.
Сохранённый crm_response=500 у успешного тестового lead исторический: success
код его не очищает; это не последний HTTP status.

```powershell
docker compose -f dev/mbk-local/compose.yaml -f dev/mbk-local/apps.compose.yaml -f dev/mbk-local/protocol.compose.yaml --profile apps --profile protocol down
```

Без -v: тестовая БД и dependency volumes сохраняются. Чужие volumes/сети
и глобальные настройки Docker не менялись; приложение MBK_v4 не затрагивается.
