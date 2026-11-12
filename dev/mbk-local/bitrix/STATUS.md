# Настоящий локальный Битрикс: состояние

Проверено 06.11.2026. В настоящем Битриксе создан **лид 1** с
ORIGIN_ID `local-write-native20261106v1`. Источник — реальная короткая анкета
локального сайта → исходный CRM Laravel → `native_api_harness` → оригинальный
`CCrmLead::Add`. Существующий лид прочитан через native ORM и UserFieldManager.

Этот же ID1 передан исходной локальной партнёрке и доставлен локальному
receiver: 1 lead, 1 sent с HTTP200, 1 history/usage, jobs0/failed0.
Синтетический баланс покупателя100→90, списание10. Native Read после передачи
подтвердил UF_SHIPPED_PARTNER=1, непустое UF_SHIPPED_DATETIME и native stream enum15.
Это подтверждённая локальная доставка; договор/принятие клиента компанией не проверялись.

## Что запущено

- Официальная новая демоверсия портала: main 26.150.0, CRM 26.100.200.
- Исходный архив: 786212629 байт, SHA256
  `e379b86869d4904d919ba1e2daffbaac8be3e15995a67f8b87ada85c7b8692d0`.
- Отдельные schema `bitrix_official_v1`, generated DB/admin/HTTP credentials,
  volume `mbk-bitrix-official-webroot-20261106-v1`, service `bitrix-official`.
- Штатный мастер установил ядро, 90 модулей и локального администратора.
  Регистрация продукта отключена предусмотренным checkbox; внешние обновления
  пропущены штатной кнопкой. Дизайн и демонстрационный контент портала не
  устанавливались: они не требуются этому серверному сценарию.
- Source volume после установки RO; cache/upload/session — отдельные tmpfs.
  Только internal network, без host ports/proxy/socket/cron/workers,
  nonroot1000, cap-dropALL, SMTP `/bin/false`.

## Доказательство настоящего создания

`native-created-read-proof.json` содержит ID/ORIGIN, исходный Add, native UF read,
типы полей, сумму400000, дату1990-01-01, регионМосква, WORK phone hash и
реальные enumIDs. Метаданные созданы CUserTypeEntity/CUserFieldEnum, статусы —
CCrmStatus. Production enumIDs не навязывались локальной последовательности:
legacy3895/23000/9257 явно сопоставляются native18/17/21.

Восемь Reflection classes сверены с точными файлами официального архива:
CCrmLead::Add, LeadTable, UserTypeEntity, UserFieldEnum, UserTypeManager,
AddressTable, FieldMultiTable, UtmTable. Подмен классов или прямого INSERT лида нет.
Factory path фактически включён; исходный compatibility adapter отключает
automation/bizproc и для Add, и для Update. Customer request.php с robots не запускался.

Проверка автоматики: 241 исходный agent сохранён, LAST_EXEC не изменился.
Новый native agent280 оставлен pending с пустым LAST_EXEC. Mail events и
workflow template/instance/state —0. Все1727 stock handlers сохранены;
customer init hooks не импортировались. Это подтверждение конкретного прогона,
не универсальный аудит Битрикса.

## Исправленные ошибки адаптера и восстановление

Первый запрос был отклонён до Add: настоящий сайт превращает нулевой платёж
в NULL. Адаптер исправлен: неизвестное значение сохраняется NULL, не заменяется0.
Следующий запрос создал native lead1; подтверждение сорвалось после записи:
UTM хранится отдельной native сущностью, а helper неверно читал ORM reference
как строку. Исправлено только чтение через UtmTable; бизнес-данные не чинились.

Повтор настоящей site job подтвердил существующий ID1 с `created=false`.
Первоначальные failed jobs сохранены. Исходный CRM при `created=false`
пропускает дальнейшую отправку, поэтому дальнейшая передача использует
отдельно зафиксированное восстановление исходной SendLeadMicroservice job
с тем же website ID. Автоматическая передача с первого запроса не заявляется.
Восстановленная исходная job завершила передачу без нового Add и без подмены
created=true. Связка website7 → native1 → partner bitrixID1 сохранена.

## Проверки и артефакты

В ignored `logs/local-stack-2026-11-06/official-trial/runtime-v1/`:

- `native-kernel-final.json`: исходный kernel/CRM, factory enabled.
- `native-metadata.json`: 14 полей и реальные native enumIDs15–21.
- `native-source-proof.json`: 8/8 native classes совпали с архивом.
- `native-created-read-proof.json`: фактический lead/UF/FM/address read.
- `native-final-shipment-proof.json`: native shipment UF после реальной передачи.
- `native-automation-proof.json`: stock agent LAST_EXEC и pending agent.
- `native-api-selftest.json`: 8/8 HTTP contract checks, без новых valid Add/Update.
- `runtime-isolation.json`: свежий host-positive/runtime-negative proof,
  actual container/image/config/env hashes. Значения секретов не публикуются.

Независимая конечная сверка партнёрки —
`logs/local-stack-2026-11-06/root-native-partner-snapshot.json`.
Общие проверки репозитория: Ruff/Pyright без ошибок; pytest2504 passed/1 skipped.

Контур принимает только ограниченный синтетический fixture; это не полноценный
customer receiver, Bitrix REST OAuth или подтверждение production правил/лицензии.
Локальная демоверсия имеет обычные ограничения официального trial.

## Возобновление и остановка

Установка уже выполнена. Не повторять provision, extraction, мастер или seed.
Generated env и volume должны сохраниться. Возобновление только этого сервиса:

```powershell
docker compose -f dev/mbk-local/compose.yaml -f dev/mbk-local/apps.compose.yaml -f dev/mbk-local/protocol.compose.yaml -f dev/mbk-local/bitrix/official.compose.yaml --profile apps --profile protocol --profile official up -d --no-deps bitrix-official
.venv313/Scripts/python dev/mbk-local/bitrix/verify_native.py --official
```

Остановка только этого сервиса сохраняет данные и остальные контуры:

```powershell
docker compose -f dev/mbk-local/compose.yaml -f dev/mbk-local/apps.compose.yaml -f dev/mbk-local/protocol.compose.yaml -f dev/mbk-local/bitrix/official.compose.yaml --profile official stop bitrix-official
```

Не применять `--remove-orphans` и `down -v`: старый protocol-mode, его очереди,
диагностический Git-runtime и новые настоящие сервисы принадлежат разным этапам.

Первичная установка нового отдельного scope использует дополнительно
`official-install.compose.yaml` для RW только собственного webroot. После
штатной установки этот overlay убирается. `prepare_official.py` и
`apply_official.py` намеренно запрещают повторное наложение на существующий scope.

## История первого source-only bootstrap

Git-pinned CRM core240fd2ae748e1b383b7de0018824c0c01987f183 был подготовлен в
отдельной bitrix_genuine_v1 с373DDL. Ошибка отсутствующего CacheEntry была
ошибкой нашего acquisition filter и устранена загрузкой оригинальных классов.
Этот исходный prolog затем перенаправил на coupon_activation.php. Лицензионные
проверки/дату/ключ не меняли; вместо этого штатно установили отдельный официальный
trial. Исходные schema/volumes/артефакты сохранены как диагностическая история.
