# Последовательный synthetic write в локальном контуре

Дата: 06.11.2026. **evidence_level=protocol_simulation** для всех будущих результатов receiver-stub. Этот документ — план и контракт, не результат запуска. SQL/HTTP в рамках его подготовки не выполнялись. Готовой обезличенной Bitrix-БД/инструкции нет по ответу пользователя; продолжаем через отдельный синтетический контур.

## Что действительно выполняется

Настоящий сайт принимает синтетическую анкету с session/CSRF, сохраняет `leads_bfl`, ставит настоящий ProcessSendLeadBfl. Один выделенный site-worker вызывает настоящий Laravel `/mbk-form/lead`. До receiver работают реальные FormRequest/controller/ORM/AddressParser. После подключения stub только протокол сохранения/ответа моделируется отдельным сервисом. Не заменяем CCrmLead::Add и не называем локальную protocol-запись лидом Битрикса.

Существующие три pending и одно failed задание сохраняются. Runtime-агент создаёт ровно один новый site-lead с `utm_source=local_write_stage` и переносит только его job в отдельную очередь `local_synthetic_write_stage`. Постоянные workers и schedulers остановлены. Site/CRM queue.php для database задают queue=`default` буквально: **DB_QUEUE env не изолирует очередь**. Изоляция stage — проверенная точечная relocation/accounting конкретного job, отдельные schema и запуск site-worker только по named stage queue; никакого default-worker. У CRM своя БД/очередь; его workers остаются остановленными даже после удачного ответа stub: `created=true` запускает реальную постобработку/Direct dispatch. Возникшие CRM jobs сначала учитываются по ID/типу/счётчикам и при необходимости переносятся guard-операцией; фоново не исполняются.

## Прежде receiver: каноническая зависимость пользователей

Свежий `LeadController.php:523` безусловно вызывает `BUserGroup::getFirstUserId(BGroup::MBK_FORM_MANAGERS_GROUP)`; группа — 110. Метод делает `SELECT * FROM b_user_group WHERE GROUP_ID=110 LIMIT 1`, без join к b_user/b_group, проверки дат, активности или ORDER BY. Только для classifiedLead найденный USER_ID добавляется в payload (`:524–525`). Конструктор MbkFormPartners SQL не выполняет; его проверки/лимиты возникают позже при processPartners.

Для этого этапа уровень доказательств после выполнения — **evidence_level=native_apps_pre_receiver**, **data_source=synthetic_fixture**, scope=pre_receiver_orm_fixture: настоящий Laravel читает синтетические локальные строки. Метка protocol_simulation относится только к будущему отдельному receiver и результатам после него. Имена markers с `protocol` сами по себе не означают, что симулятор был запущен. Словарь evidence_level соответствует ADR0002: native_apps_reader / native_apps_pre_receiver / protocol_simulation / genuine_bitrix_runtime.

Файл `dev/mbk-local/synthetic-crm-write-fixture.sql` создаёт native membership/group и явно сокращённый набор канонических колонок b_user. Основание — свежий `bitrix/modules/main/install/mysql/install.sql:154–243,272–280`. У **native b_user_group нет ID**, хотя Eloquent-модель объявляет primaryKey=ID. Под текущий read-query фиктивный ID не добавляется.

Обязательные поля: membership USER_ID/GROUP_ID и UNIQUE pair; b_group NAME/ID плюс native defaults; b_user LOGIN/PASSWORD/DATE_REGISTER/ID плюс native defaults. Сам b_user до receiver здесь не читается: заблокированный локальный пользователь нужен только для однозначной связи fixture. Значение PASSWORD пустое, ACTIVE=N, BLOCKED=Y, PASSWORD_EXPIRED=Y; это не пригодная учётная запись.

Порядок применения: отдельно CREATE, затем проверка INFORMATION_SCHEMA (типы, nullability, defaults, индексы), затем guarded INSERT. **Нельзя импортировать весь файл вслепую:** CREATE IF NOT EXISTS старую схему не проверяет. Если у группы110 уже есть другой участник либо IDs/markers заняты чужими строками, остановиться без удаления/обновления. Итог — ровно один membership USER_ID969001 с markers `local_protocol_fixture_group110` / `local_protocol_fixture_user969001`, заблокированными флагами. Membership не фильтрует активность, поэтому пользователь остаётся отключённым.

`main/install/mysql/install.sql` и `crm/install/db/mysql/install.sql` доступны в Git и скачаны. Это исправляет предположение о недоступности базового SQL, но не даёт автоматически рабочую установку: нужны настройки, модули, autoload InformUnity и customer UF/status/user metadata. Laravel migrations поверх Bitrix не являются самостоятельным полным installer.

## Реальный receiver и следующая граница

Bitrix24Api::send (`lara/app/Services/Bitrix24Api.php:47–61`) отправляет **сырой JSON object без wrapper** в base `BITRIX24_URL` + `METHOD_REQUEST`, с Content-Type application/json и Basic credentials `BITRIX24_LOGIN`/`BITRIX24_PASSWORD`. Значение receiver path и credentials не публикуются. На сайте одноимённый BITRIX24_URL обозначает base Laravel CRM; у CRM он должен обозначать отдельный локальный receiver. `BITRIX_REST_URL` — отдельный base для REST-побочных эффектов; lookup не использует боевой токен/лимит.

Свежий настоящий request.php требует main prolog, crm module и notifier (:69–71). Новый лид вызывает CCrmLead::Add, AutoStartWorkflows, Authorize и Automation Factory (:211–232). У него нет независимого простого DB-insert режима. Минимальный честный следующий прогон после membership fixture — один выделенный worker до ошибки подключения/недоступности реального receiver. Сохранить конкретную границу; не обходить её fake-реализацией CCrmLead.

Для дальнейшей проверки Laravel протокола допускается **отдельный protocol receiver**, только после независимой проверки конфигурации и разрешённых postprocessing-веток. Он не импортирует настоящий request.php, не загружает prolog, не вызывает Bitrix/robots/Authorize и не выдаётся за реализацию Битрикса.

## Контракт отдельного protocol receiver

Сервис размещается только во внутренней сети, без опубликованных портов и исходящих соединений. Отдельные env с синтетическими credentials; отдельный persistent store/volume, не b_crm_lead и не таблицы production. Каждый log/report/state содержит `evidence_level=protocol_simulation`; raw phone/имя, Basic header и полный payload в логи не попадают. Никаких боевых lookup token или production запросов.

Вход реализован в `dev/mbk-local/protocol_receiver.py`: POST по локальному маршруту, согласованному с METHOD_REQUEST; JSON object со строковыми ключами и только скалярными значениями (str/int/finite float/bool/null). Именной allowlist остальных ключей нет; вложенные объекты/списки запрещены. Требуются PHONE/PHONE_DEAL: после удаления нецифровых символов одинаковые11цифр по `[78]000\d{7}` (7/8 здесь не превращаются друг в друга); RETURN_RESPONSE — boolean true **или строка`"1"`**; IS_BFL_PRODUCT — строго boolean true; UF_CRM_1491296976 — **int, но не bool, либо каноническая десятичная строка по`0|[1-9]\d{0,9}`** (например`"4707"`, без знака/пробелов/ведущих нулей). SUMM —0 или строка`"0"` (0.0 не проходит). Это ограничение специального БФЛ fixture, не произвольный кредитный ввод. Остальные скалярные поля участвуют в canonical payload hash; полное содержимое в store/логи не сохраняется. Stub не вычисляет категорию, не заменяет согласие, не добавляет данные и не применяет правила371. Категория не ограничена4707 самим stub: узкую ветку189999/4707 обеспечивает manifest/runtime-runner. Требования этого stub отличаются от настоящего receiver и не доказывают паритет валидации.

Основание wire-форм: настоящий MbkFormLeadRequest содержит RETURN_RESPONSE rule=`nullable|string` (`:91`), category rule=`nullable|int` (`:62`), а оба значения проходят FilterTrim/FilterSanitize/FilterStripTags (`:145,173`). В full-app проходе runtime-агент подтвердил подготовленный payload с RETURN_RESPONSE string`"1"`, category string`"4707"`, IS_BFL_PRODUCT boolean true и SUMM int0. Это слой Laravel FormRequest между сайтом и receiver, а не ошибка телефона. Расширена только **валидация входа simulator**; исходный payload и типы не переписываются перед canonical hash. Поэтому смена true→`"1"` или int4707→`"4707"` при уже использованном operation_id меняет hash и должна дать409; обе формы принимаются как новый ввод, но не сливаются произвольным coercion. Ответ result.created остаётся строгим boolean. Уровень этого обмена — protocol_simulation; production source не менялся.

Успех: HTTP200, JSON `{"result":{"leadID":"700000001","created":true},"evidence_level":"protocol_simulation"}`. leadID — непустая десятичная строка из **зафиксированной записи persistent local store**, не произвольно возвращённое число; namespace700000000+ только для симулятора. Формат result/created взят из request.php:313–317 и проверок Laravel:544–561. Настоящий receiver не гарантирует одним этим исходником постоянный JSON-тип ID: строка выбрана как совместимая форма stub. Laravel legacy возвращает клиенту только result.leadID, дополнительный label может отбросить; поэтому локальный evidence-label обязателен также в manifest/report и UI описании прогона.

Idempotency: отдельный configured `run_id` + стабильный **operation_id**, с UNIQUE constraint; отдельно хранится canonical payload hash. Операция берётся из явно синтетического UTM_CAMPAIGN формата **`local-write-<alnum>`**, regex`local-write-[A-Za-z0-9]+`, которое fixture-runner записывает при создании site-lead. Формат совпадает с фактическим site-stage: готовую операцию/запись для соответствия старому черновику не меняем. Поле проходит в штатном payload; отдельного production API не добавляем. Run/operation неизменны при retry; body hash **не входит в ключ**. Повтор того же ключа/эквивалентного payload возвращает тот же ID и created=false. Изменённый payload при том же ключе даёт409 без leadID. Дополнительная **синтетическая** телефонная дедупликация внутри run: новый operation_id получает существующий ID/created=false, если возраст записи **0≤age<900секунд**; окно не продлевается повтором. Ровно900секунд — новая запись для нового operation_id; старый operation_id всегда остаётся идемпотентным. Одновременные запросы сериализуются SQLite BEGIN IMMEDIATE/unique key. Это упрощённая модель: реальный receiver имеет исключение смены статуса Incoming (:180–182) и особенности поиска; stub их не воспроизводит.

POST не имеет дополнительного idempotency header: Bitrix24Api его не шлёт. UTM operation marker используется только будущим локальным stub, не объявляется production idempotency API. Run без operation marker должен быть отвергнут stub, а не получать случайный новый ключ. Для900-секундных границ нужен управляемый fixture clock и manifest времени/operation, без долгих sleep. Телефон нельзя логировать открыто; hash без ограниченного synthetic namespace не обеспечивает анонимность.

Если **новый operation_id** склеен телефонной дедупликацией с прежним leadID, store атомарно сохраняет также mapping этого operation_id → existing leadID + payload hash. Тогда повтор этой операции после900секунд остаётся тем же ID/created=false; отсутствие такого mapping нарушило бы идемпотентность после истечения телефонного окна.

Нормативные ответы stub для независимых тестов:

| HTTP | error code / success fields | Условие |
|---|---|---|
| 200 | result.leadID=непустая десятичная строка, result.created=true | Новая durable запись |
| 200 | тот же result.leadID, result.created=false | Идемпотентный повтор / синтетический phone-dedup |
| 400 | error=invalid_json | Тело не парсится как JSON |
| 400 | error=invalid_envelope | JSON не объект (включая массив/null/скаляр) |
| 400 | error=invalid_framing | Нет ровно одного корректного Content-Length, непустой Transfer-Encoding, размер вне1..65536, неполное тело/ошибка чтения при socket timeout5сек либо Content-Type не application/json |
| 401 | error=unauthorized | Неверные/отсутствующие локальные Basic credentials |
| 422 | error=unsupported_fixture_input | Поле/тип/operation marker/ограниченный fixture scope не удовлетворяет контракту |
| 409 | error=idempotency_conflict | Уже существующие run_id+operation_id и иной canonical payload hash |
| 500 | error=storage_error | Durable commit не выполнен / store недоступен |
| 404 | error=not_found | Неизвестный GET/POST path; значение настроенного receiver path в журнал не выводится |

В каждом ответе top-level `evidence_level="protocol_simulation"`. В каждой нормативной ошибке **полностью отсутствует result**, нет leadID и отражения входных значений; тело только `{"error":"<code>","evidence_level":"protocol_simulation"}`. created имеет строгий JSON boolean, не0/1/строку. ID создаётся только после успешного commit;500 storage_error не используется для ошибки отправки ответа **после** commit — такой сбой отдельный неопределённый исход. Laravel Bitrix24Api проверяет curl_error/распарсенный ID, не HTTP status; ошибка status с ошибочно оставленным leadID может считаться успехом. Ошибки должны проверяться на обоих слоях. Вечные зависания запрещены: timeout внешнего single-worker и watchdog задаются runtime-агентом; production transport здесь не правится.

Служебные GET: `/health` возвращает200/forwarding=false без auth; `/lab/ledger-stats` требует локальный Basic и возвращает только count/IDs/hashes, без исходных данных. Это интерфейсы simulator, не Битрикса. Тесты Ledger могут передавать управляемый clock; HTTP runtime использует wall clock и выбранный env dedup_seconds (default900). Этот документ сверяет реализацию статически, не сообщает о её запуске или прохождении runtime-тестов.

## Порядок прогонов и проверяемые инварианты

1. **Схема/группа:** подтвердить типы/индексы и единственный membership; сохранить counts без персональных данных. Никакие ALTER/cleanup или широкие migrate не выполняются.
2. **Новый site-lead:** отдельные cookies/session и настоящий CSRF, worker ещё выключен. Выбрать сумму189999 и IS_BFL_PRODUCT штатной анкеты: сайт даст NO_CATEGORY4707. Это ограничивает первую ветку после receiver: обычный processPartners не вызывается, но Direct job всё равно будет поставлен.
3. **До receiver:** выделенный один site-job/worker, локальные bases, bounded timeout. Ожидаем реальный Laravel проход и конкретную следующую границу. Ошибка не превращается в доказательство записи.
4. **До старта stub:** критик проверяет compose/env/internal network/persistent volume, отдельные site/CRM queues, отсутствие prod destinations, отсутствие других jobs в новой очереди. Прямые REST/notifier/партнёрские endpoints не запускаются; CRM workers off. Широкие BFL300000-пути требуют отдельного audit MbkFormPartners/settings и сюда не входят.
5. **Успех симуляции:** stub имеет1durable запись; Laravel вернул тот же synthetic ID; сайт сохранил его; создан ровно ожидаемый Direct job в изолированной CRM queue, который **не исполнялся**. Это site→Laravel→protocol_simulation, не site→Bitrix.
6. **Повтор:** тот же synthetic request/run-key возвращает тот же ID и created=false; Laravel пропускает постобработку; число CRM Direct jobs не растёт. Ошибочные payload/storefailure не дают leadID.
7. **Состояние после теста:** старые3pending/1failed неизменны, новыйstage accounted; workers stopped. `is_crm_sent=1` без реального Bitrix ID остаётся возможным; simulated ID отмечается явно и не используется как справка по настоящему клиенту.

Обязательные отдельные fault-кейсы перед выводами о повторах:

| Сбой | Что фиксируем |
|---|---|
| HTTP500 + ошибочно присутствующий result.leadID | Негативный fault-injection с явной меткой, отдельно от нормального error-контракта stub. Текущий Bitrix24Api может принять такой ID: статус не проверяется. Это дефект границы transport/response, а не успешная доставка |
| Ledger commit выполнен, ответ потерян до Laravel postprocessing | Retry возвращает created=false; legacy делает ранний return. Direct/уведомление могут остаться **не поставленными**. Запись ledger, факт отправленного ответа и факт CRM dispatch учитываются раздельно |
| Первый ответ и postprocessing завершены, затем повтор | created=false должен предотвратить повторный dispatch; это другой случай, чем потерянный первый ответ |

Стабильный ID и dedup не доказывают exactly-once. Не заставлять stub возвращать created=true после неопределённого ответа: это меняет риск пропуска на риск дубля. В этом этапе у production-кода нет общей транзакции receiver ledger + Laravel dispatch; simulator не должен скрывать этот разрыв.

## Статический аудит created=true для189999/NO_CATEGORY

Свежий legacy controller: category4707 + IS_BFL_PRODUCT=true оставляет все четыре routing flags=false (`LeadController.php:432–485`). До receiver есть group110 ORM-read и AddressParser; это не REST/уведомления. После created=true не выполняются ни classified-notify (`:564–572`), ни processPartners/limit HTTP (`:573–582`); выполняется только `SendLeadSodaTechDirect::dispatch(leadId)` (`:585–586`), затем return ID (`:593`). Ветка заполненной анкеты после dispatch содержит только закомментированный DenisCheckDataByPhone (`:589–590`). Зарегистрированный BUtsCrmLead observer здесь не вызывается: настоящий BUtsCrmLead объект не создаётся/не сохраняется этим маршрутом. Queue hooks с дополнительными HTTP-вызовами в сохранённом app не найдены. Вывод ограничен этим payload и реальным database queue driver: при sync driver dispatch исполнил бы job немедленно.

Ассерты после единственного успешного protocol-вызова: ledger+operation durable; ответ Laravel и site.crm_lead_id равны synthetic receipt ID; ровно один новый CRM job с displayName `App\Jobs\SendLeadSodaTechDirect`, command привязан к тому же int leadId, attempts=0, reserved_at=NULL, accounted queue/default→stage relocation; никаких дополнительных jobs/failed и никаких исполняющихся CRM workers. Native b_crm_lead/UF_SHIPPED_* не должны появляться как следствие stub: он туда не пишет; отсутствие таблицы допустимо, baseline counts не меняются. Старые site jobs/failed неизменны. При повторе created=false новых CRM jobs нет.

У сайта `ProcessSendLeadBfl` при успехе сохраняет crm_lead_id и is_crm_sent=true, но **не очищает crm_response**. После прежнего receiver404 старый error text может остаться; проверять baseline/hash, не требовать NULL без знания исходного состояния. Сам is_crm_sent устанавливается также до проверки ошибочного HTTP-ответа и не доказывает запись Битрикса.

## Что этот этап не доказывает

Реальную запись/дедупликацию Битрикса, UF mapping внутри CCrmLead, workflows/robots, статус конечной компании, телефонные frozen/duplicate windows, campaign limits, общий смысл согласия либо конверсию. Для consent-кейсов отдельно обозначать согласие БФЛ, исключение CPA и общий отказ; stub не должен молча ужесточать или расширять их семантику.
