# Локальный контур МБК — 06.11.2026

## Текущий milestone: protocol_simulation

**Пройден ограниченный write-контур настоящего сайта и Laravel CRM с отдельным
receiver simulator. Это не создание лида в Битриксе и не доставка компании.**
Для resume/verify/down сейчас нужны три compose-файла и четыре runtime scopes:
site, crm, partners, protocol-receiver; команды — в README.

- Native prerequisites: исходно три ORM таблицы отсутствовали. Применены только
  canonical/reduced CREATE, сверены 27 колонок (тип/null/default) и 7 колонок
  индексов, затем guarded synthetic group110/user969001. Это не установленный Битрикс.
- Один новый site lead/job дошёл через настоящий legacy до capture: ровно один
  POST с hash пути реального METHOD_REQUEST. Capture не дал result.leadID;
  ошибка записана как native_apps_pre_receiver, без имитации Bitrix Add.
- Отдельный protocol receiver имеет свой SQLite volume и synthetic auth.
  Входной HTTP contract проверен 10/10 кейсами; root protocol suite — 39 passed.
- Реальный Laravel sanitization обнаружен первым отвергнутым app-прогоном:
  RETURN_RESPONSE приходит строкой "1", category строкой "4707". Контракт явно
  расширен только эквивалентными доказанными wire forms, бизнес-код не менялся.
  Payload hash сохраняет исходные типы: изменённый wire payload с тем же key
  по-прежнему даёт conflict. Output created — строгий bool.
- Новый site lead6 после bound retry получил **simulated ID700000003**;
  ID подтверждён committed ledger и реальной БД сайта. В ledger +1 lead/+1
  operation относительно самостоятельных HTTP probe fixtures.
- Реальный CRM response log подтверждает created sequence **true, false, false**:
  первый accept, повтор job и повтор после пересоздания только receiver.
  Ledger/ID не изменились; остальные три контейнера остались теми же.
- CRM создал ровно один Direct job2 с этим simulated ID. Guarded relocation
  перенёс его в local_protocol_direct_unexecuted: attempts0/reserved=NULL.
  CRM worker не запускался. Исходный CRM LogErrorJob1/default не изменён.
- Site pending IDs2,3,4 и все failed IDs1,2,3 сохранены. Failed3 — первый
  app-прогон до исправления wire-контракта; он не consumed/удалён. Успешные
  retry/repeat jobs — новые scoped jobs, а не запуск старой очереди.
- У lead6 старый crm_response со статусом500: success-ветка его не очищает.
  Это **historical stored error**, не latest HTTP status.

Главное доказательство: ignored protocol-write-milestone.json, плюс
protocol-app-receiver-response-metadata.json, protocol-app-final-site-state.json,
protocol-app-after-restart-crm-queue.json, protocol-runtime-after-restart.json.
Они не содержат телефонов, имён, raw payload, SQL или auth values.

Не доказаны CCrmLead::Add, workflows/robots, компания-получатель и exactly-once
постобработка. Commit receiver с потерянным ответом → retry created=false может
пропустить dispatch в текущем legacy; стабильный ID этот разрыв не исправляет.
Source/controller/vendor business patches не делались.

## Ранние этапы и их доказательства

Разделы ниже описывают предыдущие наблюдения до protocol-mode. Прежний
missing b_user_group устранён ORM fixture, а не полной Bitrix установкой.

## Фактический результат

Запущены три настоящих Laravel HTTP приложения — сайт, CRM API, партнёрка —
плюс MySQL и capture. Source/vendor RO, storage/bootstrap cache tmpfs.
Все сервисы только в mbk-local-lab_isolated/Internal=true, без host ports,
host overrides/socket/proxy. Runtime non-root. PHP 8.4.27 собран отдельно;
это лабораторный built-in HTTP server, не production FPM/образ заказчика.
Приложение MBK_v4 и production API не менялись.

Полный Bitrix receiver и production parity **не готовы**. Это ещё не успешная
сквозная запись сайта → Битрикс → компания.

## Подтверждённые проверки

- Партнёрка: 4 HTTP проверки distinct sent counter, неизвестного lead,
  найденного/отсутствующего искусственного телефона прошли. Четыре sent rows
  внесены вручную как fixture; это не исполненные доставки.
- CRM mapper/engine/router: 8 HTTP проверок 189999/190000/299999/300000 ×
  Москва/Тверская область прошли с искусственными категориями 990191/990300/990301.
  Это шесть пользовательских fixture rules, не действующие настройки production.
- CRM без локального bearer: 401. Production lookup token не использовался.
- Сайт POST create без XSRF: 419 при APP_ENV=local, без test-mode bypass.
- Сайт с настоящими session/XSRF cookies: 4 сессии × create/step/complete,
  все 12 POST дали 200/success=true без патчей контроллера/CSRF middleware.
- Фактическая БД: 190000 и 299999 → 4707; 300000 Москва → 3895;
  190000 + 110000 monthly → 3895. Подтверждена legacy формула debt + monthly.
  Нулевой monthly сохраняется NULL, filled=1; is_classified complete не меняет.
- Общий diagnostic HTTP smoke **15/16 passed**, один fail /bankruptcy → 404
  из-за отсутствующего CMS content. Скрипт возвращает exit 1, не ложный pass.

## Один настоящий worker

После свежего сетевого контроля выполнен ровно один ProcessSendLeadBfl job:
GNU timeout 15s, --once --tries=1. До job был устранён local console blocker:
пустой ELASTICSEARCH_HOSTS давал invalid URI. Локальный capture URL позволяет
сконструировать клиент, без имитации Elasticsearch API.

Job отправил запрос в локальный Laravel CRM и получил HTTP 500. Первичная
ошибка CRM: отсутствует crm.b_user_group при поиске группы 110. Fake leadID
не создавался; упрощённый insert вместо Bitrix API не выполнялся.

После того первого job было **3 pending, 1 failed**. Первый lead имел is_crm_sent=1 и crm_lead_id=NULL;
остальные три — is_crm_sent=0. Это доказанная попытка отправки, не доставка.
Worker пишет FAIL, затем DONE: exit 0 команды не доказывает создание CRM lead.
Непрерывных workers/cron нет. Строгий post-verifier проверил actual categories,
суммы/флаги, job class/count, локальный sender target и stored HTTP 500.

Первый standalone pre-verifier имел ошибку request binding: предсостояние
4 pending/0 failed/all crm_sent=0 подтверждено ручным SQL наблюдением и честно
помечено observed, не автоматическим pass. Verifier исправлен: request binding
и Throwable catch с nonzero exit; post-state assert выполнен успешно.

## Источники и данные

| Репозиторий | Git SHA |
|---|---|
| mbk-site/master | 04aaa4aa28c0fe25cede8ed291a1b86c2eaa8741 |
| crm-mbk/main | 240fd2ae748e1b383b7de0018824c0c01987f183 |
| mbk-partners/master | 21552834cf76a1d35400781aaec632c5655b4d8f |

Выборочные GitLab tree/raw text snapshots, не полный clone. Archive API дал
406; private registry HTTPS v2 probe не ответил. Docker trust/proxy не менялись.
Binary assets и некоторые большие location migrations не загружались.
database/csv скрыт пустым tmpfs. Customer env/storage/cached config не импортированы.

Site: 70 DDL tables + 92 validated migration-history строки; partners: 17 tables
+ 11 migration-history строк. Остальные INSERT из дампов не копировались.
Partners dump содержит email-like values, failed jobs, notifications/auth/settings;
его анонимность не подтверждена, raw dump не исполнялся. CRM имеет 9 reader fixture
tables, это не установленная Bitrix БД. Дополнительно созданы только queue schema
и явно искусственные site/counter fixtures. Customer seeders не выполнялись.

## Dependency provenance

Точные Composer версии/commit refs, включая dev dependencies (CRM безусловно
регистрирует dev provider). Packages: site 125, CRM 151, partners 124.
Composer scripts/plugins отключены. Builder видел только manifest/lock/private
package source, без app/env/DB; runtime не подключался к обычной сети.

Изменены только ignored lock copies: GitHub API dist → codeload того же commit
для site/CRM; mbk/fraud-monitoring → локальная tracked vendor копия для partners.
Исходные lock не менялись. Vendor Linux volumes заполнены через отдельный
stopped helper с network none и docker cp; недостающий Google package затем
установлен отдельным builder в volume. Windows unzip timeout не объявлялся pass.

Полные original/local lock hashes записаны в dependencies/project-*/DEPENDENCY-MANIFEST.json;
installed.json hashes/counts и autoload-present — vendor-volumes.json.
Локальные lock SHA256:

- site: 76e37405bb65aa175636962927a2554114dbb9ba53327283f77573e588fb1504;
- CRM: 3ca55c92757d27e90f44ddb95f5d1c0a325a5dca97ad8628bc32e5202faab5f9;
- partners: 43557a057aff48a91f89aa0e8cf6f2f6098675a71587672f7d348b131814c6c4.

## Что осталось для полной цепочки

1. Проверенный local recipe Битрикса: core/crm module, InformUnity notifier/autoload,
   пользователь и UF metadata, обезличенные business fixtures и локальные автоматизации.
   Base install SQL найден в Git и сохранён как source, НЕ исполнен. Наличие
   install SQL само по себе не восстанавливает пользовательские UF/правила/robots.
2. Реальный receiver включает Bitrix prolog/crm module и InformUnity notifier,
   вызывает CCrmLead Add, workflows/Authorize/robots. Нельзя заменить его insert
   или выполнять без проверки локального bootstrap/автоматизаций.
3. Для genuine_bitrix_runtime нужен отдельный реальный receiver webroot.
   Protocol-mode проверяет только интерфейс отдельного simulator; capture
   по-прежнему не возвращает leadID. Роли BITRIX24_URL сайта/CRM разделены.
4. Campaign/delivery fixtures и совместимые конечные mocks, включая hardcoded
   SDK paths; затем bounded distribution/consent/dedup tests.
5. CMS/compiled frontend assets и browser request allowlist либо endpoint localization
   перед UI smoke: internal Docker network не ограничивает Windows browser JS.

## Артефакты

Ignored logs/local-stack-2026-11-06/:

- runtime-isolation.json: fresh host-positive control, все 3 runtime, IDs/start/image/
  config SHA, local POST и blocked external/host probes.
- http-smoke.json: 15/16 diagnostic statuses и selected JSON.
- site-bfl-smoke.json: реальные session/CSRF create/step/complete.
- site-bfl-db-verification.json: pre-worker SQL observation, не automatic pass.
- site-bfl-after-worker.json: строгий post-state assert, stored HTTP 500.
- worker-failure-metadata.json: missing-table metadata без raw SQL/body/contacts.
- vendor-volumes.json, dependencies/project-*/DEPENDENCY-MANIFEST.json: provenance.
- source-fetch-manifest.jsonl, bootstrap-source-manifest.json, endpoint-hosts.json,
  schema manifests: source inventory, receiver/core source и отделение DDL от данных.

Главный агент выполнил корневые проверки: Ruff check . — успешно,
Pyright — 0 ошибок; финальный pytest — 2486 passed, 1 skipped, 71.33s.
Из них 39 проверок protocol store/HTTP/input wire contracts.
Из Pyright исключён только Gitignored generated logs с PHP vendor-документацией;
target Python 3.12 и проверка dev-скриптов сохранены.
Прежний root inspect подтверждал три приложения. Актуальный protocol-runtime-
after-restart.json охватывает четыре runtime, их IDs/start/image/config/env SHA
после реального пересоздания simulator. Эти проверки не доказывают production
runtime или успешную доставку клиенту.
