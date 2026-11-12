# Настоящий Битрикс → локальная партнёрка

Проверено 06.11.2026. Создание лида, передача в партнёрку и доставка локальному
получателю независимо подтверждены. Вердикт критика: PASS с явным восстановлением
после Add. Боевые отправки не выполнялись.

| Участок | Фактический результат |
|---|---|
| Анкета сайта | Настоящие session/CSRF create → step → complete; заявка сайта №7 |
| Битрикс | Настоящий CCrmLead::Add создал лид №1; native API/ORM прочитали поля |
| Связь с сайтом | leads_bfl.crm_lead_id = 1, операция local-write-native20261106v1 |
| CRM → партнёрка | Штатный SendLeadMicroservice передал тот же Bitrix ID; лид партнёрки №1 |
| Распределение | Штатный движок выбрал тестовую кампанию 950, получателя 991001, очередь №1 |
| Доставка | Штатный LeadSend получил HTTP 200 от локального mock-получателя |
| Учёт | Один sent, одна delivery_history, одно списание 10; тестовый баланс 100 → 90 |
| Очереди | В новой CRM и партнёрке jobs = 0, failed_jobs = 0 |

Вход полностью искусственный: долг 400 000 ₽, Москва, дата рождения 01.01.1990,
согласие на передачу. Сайт преобразовал месячный платёж 0 в пустое значение;
в Битриксе оно и отсутствующее UF_TOTAL_DEBT_OTHER сохранены как NULL.
Настройки кампании, цена и баланс — локальные фикстуры,
а не выгрузка действующих настроек МБК. ID сайта, Битрикса и партнёрки относятся
к разным таблицам; совпадение двух единиц не означает общую нумерацию.

## Как прошёл прогон

Битрикс установлен штатным мастером из официального демо-дистрибутива:
main 26.150.0, 90 модулей. Регистрация у поставщика пропущена штатно.
Боевой ключ, настройки и база не переносились; лицензионные проверки не менялись.
Отдельный мастер оформления/CMS-контента портала не проходили.

Сайт, Laravel CRM и партнёрка используют закреплённый бизнес-код без правок.
На границе Битрикса работает лабораторный HTTP-адаптер с настоящими native API:
Add, чтение UF/ORM/FM/адреса/UTM и Update. Он явно сопоставляет legacy enum-коды
с ID новой установки. Это не исходный customer receiver и не REST OAuth.
Проверены 13 исходных хешей архива и Reflection восьми используемых классов.

Сохранены две ошибки адаптера: требование числового 0 вместо допустимого пустого
платежа и неверное чтение виртуальных UTM-полей. Исправлены nullable-контракт
и чтение через native UtmTable. Сохранённые данные лида вручную не исправлялись.
Повторного Add не было. Последний штатный повтор вернул created=false и тот же
ID 1, после чего сайт сохранил связь.

После ошибки ответа штатная CRM не повторяет postprocessing для existing ID.
Поэтому один SendLeadMicroservice поставлен вручную через настоящий Laravel
Dispatcher, с проверкой native ID, сайта, пустой очереди и постоянным маркером
единственной попытки восстановления. Это явное восстановление отправки;
автоматический handoff нового входа этим прогоном не подтверждён.

Общий Artisan discovery остановился до резервации CRM-задания из-за отсутствующей
настройки посторонней команды Google Sheets. Для обеих очередей использован
штатный Laravel WorkCommand/Worker через узкий CLI-вход: named queue, once,
tries=1, ограничение времени, настоящие события и failed_jobs. Исходный job handle
не вызывался вручную. У обоих workers processing=1, processed=1, failed=0.

Прежние site pending №2/3/4 и failed №1/2/3 сохранены по хешам. Новые failed №4/5
тоже сохранены. Поле crm_response сайта всё ещё содержит исторический 500:
успешный исходный код его не очищает; это не статус последнего запроса.

## Проверки и границы

- Python: 2504 теста прошли, 1 пропущен; Ruff и Pyright без ошибок.
- Native API: 8 проверок; nullable monthly: 7 проверок.
- Реальные UF, дата рождения, валюта, долг, адрес и телефон сверены через native API/ORM.
- Source/vendor read-only, internal Docker network, host ports/прокси/socket отсутствуют.
- Штатные агенты Битрикса сохранены, их исполнение ограничено; workflow и почтовых событий нет.
- Workers завершились; общих фоновых consumers/cron нет.
- Критик независимо сверил 13 runtime-исходников, 8 Reflection-классов и 22 артефакта.
- Восстановленный исходным кодом JSON отправки: 832 байта; его SHA256 и receipt ID
  совпали с сохранённой квитанцией локального получателя и sent.

Конечный получатель — локальный mock. HTTP 200 доказывает транспортный ответ,
не принятие клиента реальной компанией. Production-правила, custom robots,
исходный customer receiver, REST OAuth и готовность к релизу не подтверждены.
Страница /bankruptcy по-прежнему без CMS-контента; браузерный UI не проверялся.

Обнаруженные исходные ограничения: потеря postprocessing после успешного Add
и ошибки ответа, ранний sent-флаг сайта, исторический crm_response и игнорирование
HTTP-статуса в Bitrix24Api. Они не исправлялись в бизнес-коде МБК в этой задаче.

## Артефакты

В ignored logs/local-stack-2026-11-06/ на текущей машине:

- website-native-v1/: baseline, две ошибки, повторы, site-ack-observe.json (7 → 1);
- official-trial/runtime-v1/: установка, native Get/UF/Reflection, API/selftests, inventory;
- official-trial/ARCHIVE-CRITICAL-SOURCE.json: 13 хешей официального архива;
- genuine-crm/: actual Http/BitrixRest proof, восстановление и события worker;
- root-native-partner-snapshot.json: независимый read-only снимок учёта доставки.

Дополнительные delivery/receipt артефакты:
dev/mbk-local/partner-native-prepared/native20261106v1/.
Private env/HTML/JSON не предназначены для публичной передачи.

[Независимый отчёт](../../docs/v5/2026-11-06-native-bitrix-partners-critique.md).
[Архитектурные границы](../../docs/adr/0003-local-native-bitrix-chain.md).
[Подробные delivery-доказательства](partner-native-result.md).

## Возобновление на текущей машине

Запуск только подготовленных сервисов, без повторного installer/provision:

```powershell
docker compose -f dev/mbk-local/compose.yaml -f dev/mbk-local/apps.compose.yaml --profile apps up -d --no-deps database capture
docker compose -f dev/mbk-local/compose.yaml -f dev/mbk-local/apps.compose.yaml -f dev/mbk-local/bitrix/official.compose.yaml -f dev/mbk-local/genuine-site.compose.yaml --profile official --profile genuine-site up -d --no-deps bitrix-official site-genuine
docker compose -f dev/mbk-local/crm-genuine.compose.yaml up -d
docker compose -f dev/mbk-local/native-partners.compose.yaml up -d
```

Эти команды не отправляют новую заявку и не запускают workers.
Для остановки использовать stop только выбранных новых сервисов; volumes и
схемы сохранять. Старый protocol-контур и диагностический Git-native контур
с HTTP 302 оставлены отдельно, их данные не относятся к этому доказательству.
