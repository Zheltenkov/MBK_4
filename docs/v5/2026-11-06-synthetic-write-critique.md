# Независимый review следующего synthetic-write этапа

Дата:06.11.2026. Роль: independent_critic. SQL/HTTP критиком не выполнялись;
runtime/app/compose/SESSION не изменялись. План и разрешённые уровни доказательств:
[ADR-0002](../adr/0002-local-synthetic-write-boundary.md). Детали протокола:
[контракт](2026-11-06-synthetic-write-contract.md). Предыдущий наблюдавшийся
результат — [отчёт локального контура](2026-11-06-local-stack-independent-critique.md).

## Предстартовый вывод

Пользователь подтвердил отсутствие готовой обезличенной Bitrix-БД. Допустим
последовательный native Laravel/ORM проход до локального stop-receiver.
Отсутствие полного Bitrix не запрещает этот тест. Отдельный simulator допустим
следующим этапом с protocol_simulation; до его старта нужны review нового
service/config и собственная проверка его runtime. Genuine Bitrix creation,
CCrmLead::Add и внешняя доставка этим не объявляются выполненными.

На первом шаге reviewed fixture и guard позволяют CREATE → проверку metadata →
guarded seed → один **новый** site job до capture, при выполнении условий ниже.
Это допуск теста; его окончательный outcome пока не подтверждён этим документом.

## Native ORM fixture

Прочитаны synthetic-crm-write-fixture.sql, свежий main/install/mysql/install.sql
и BUserGroup/read-query. b_user_group имеет только USER_ID/GROUP_ID и даты,
UNIQUE pair; фиктивный membership ID не добавляется. b_group и используемые
поля b_user взяты из native DDL. b_user явно сокращённый subset, не полная
установка. Fixture user969001 отключён/заблокирован, пароль непригоден для входа;
group110 содержит отдельный marker, permissions/Authorize/robots не включаются.

Артефакт write-orm-preflight-before-create.json сообщает0 существующих таблиц.
Критик самостоятельно SQL не запрашивал. Перед seed исполнитель обязан assert
INFORMATION_SCHEMA columns/types/nullability/defaults/indexes после отдельных
CREATE. CREATE IF NOT EXISTS существующую схему не валидирует и не исправляет.

Guarded INSERT не перезаписывают неизвестные строки. Из-за first() без ORDER BY,
join и active/date checks допустим ровно один member group110 —969001 с обоими
markers и disabled flags. Другой member/занятый чужой ID — остановка без cleanup.
Допустимы диагностические ошибки следующей native зависимости; их нельзя
скрывать утверждением полной работоспособности Bitrix.

## Очереди и неизменность прошлых кейсов

Reviewed stage-write-queue.php связывает один новый lead по utm_source и
run-specific utm_campaign с job class и scalar serialized leadId, без unserialize.
Транзакция lockForUpdate требует default queue, attempts0, unreserved, ровно одну
изменённую строку. Старые default IDs[2,3,4] и failed ID[1] сохраняются.
Проверять нужно идентичности этих заданий, а не только равные суммарные counts.

Site/CRM config/queue.php жёстко задают database.queue='default'. DB_QUEUE либо
QUEUE_NAME в env этого не меняют. Guarded перенос нового job в отдельную queue
обоснован. Для будущих CRM Direct jobs нужны аналогичный accounting/изоляция;
CRM workers остаются остановленными. Общий queue:work и retry старой failed
очереди в этот этап не входят. Один bounded worker берёт только новую queue.

В исходном observer найдены и переданы исполнителю два риска false pass:

- P1 для доказательства границы: failed job + is_crm_sent=true могут возникнуть
  **до** receiver, как уже было с missing b_user_group. Название
  receiver_boundary_reached допустимо только с независимым capture/endpoint
  evidence; без него — legacy_attempt_failed/boundary_unconfirmed.
- P2: MBK_EXPECT_WRITE_OUTCOME должен принимать только failed/completed.
  Неизвестное значение иначе обходит обе ветки проверки outcome. Phase также
  должен быть явным допустимым значением. Исправление проверяется перед финальным
  заключением; при корректном failed входе это не обнаруженная утечка.

## Receiver и реальные побочные эффекты

Stage A: CRM BITRIX24_URL=http://capture:8000, Basic credentials пустые; сайт
BITRIX24_URL по-прежнему адресует локальную Laravel CRM. Capture не возвращает
leadID. Ожидаем диагностическую ошибку после настоящего native прохода.
Сумма189999 и IS_BFL_PRODUCT штатной формы дают NO_CATEGORY4707.

Stage B: новый отдельный simulator с собственным persistent store может вернуть
синтетический ID **только после ledger commit**. Новый ID запускает реальную
Laravel postprocessing: notify/partner/Direct ветви зависят от входа. Даже
NO_CATEGORY+IS_BFL_PRODUCT пропускает обычный processPartners, но оставляет
Direct dispatch. Нельзя считать отсутствие работающего CRM worker отсутствием
созданного задания или принимать его за подтверждённую доставку.

Свежий Bitrix24Api::send: raw JSON object, Basic auth, local base + METHOD_REQUEST;
curl_error и json_decode, **без проверки HTTP status**. Поэтому500 с ошибочно
успешным result.leadID в теле — отдельный contradictory-response testcase.
Ошибки нормального simulator не содержат leadID, но тест обязан показать реальное
поведение Laravel при несовместимом status/body, а не спрятать этот риск.

Сайт ProcessSendLeadBfl после HTTP response ставит is_crm_sent=true до проверки
ошибки/наличия ID. Уже наблюдалось true при HTTP500/CRMIDnull. Флаг, worker DONE
и exit0 не являются доказательствами приёма CRM. Фиксировать HTTP/body outcome,
ledger state, CRM/site IDs и dispatch state раздельно.

## Контрпроверки protocol simulation

1. Успешный created=true:1durable ledger record, стабильный synthetic ID;
   реальные consumer/site сохраняют тот же ID. Postprocessing/jobs учитываются
   отдельно. created=false проверяется отдельным кейсом и не подменяет этот путь.
2. Повтор: stable operation_id отдельно от canonical payload hash. Same key +
   same payload → тот же ID; same key + изменённый payload →409 без leadID.
   Если hash включить в сам ключ, конфликт изменённого payload станет недостижим.
   Текущий transport не добавляет idempotency header, поэтому источник ключа
   явно fixture-only, например opaque UTM_CAMPAIGN/mapping, без parity claims.
3. Одновременные одинаковые операции/рестарт: транзакция/UNIQUE и persistent
   store предотвращают второй ID. Scope и900s окно — выбранные тестовые условия,
   не реальные frozen/duplicate/hands/delay и не полная native дедупликация.
4. Storage failure до commit: нет ID и ledger row. Commit → потеря ответа:
   row может существовать, а consumer outcome остаётся unknown. При повторе
   created=false legacy пропускает postprocessing; если первая попытка не успела
   до него дойти, dispatch может не произойти вообще. Stable ID сам по себе не
   доказывает exactly-once effects. Не обходить это возвратом created=true:
   он создаёт риск повторного Direct dispatch. Этот разрыв нужно измерить явно.
5. Malformed/missing response,401/409/422/500,timeout: безопасные error codes,
   отсутствие leadID, ограниченный timeout/retry. Переполненный/недоступный store
   не даёт «успех» и произвольное число вместо подтверждённой записи.

## Согласие и lookup

UF_SITE_AGREE_PARTNERS не является универсальным отказом от любых передач.
Общая BFL ветка, IS_BFL_PRODUCT, CPA-исключение и общий отказ клиента фиксируются
в отдельных сценариях. Stub валидирует согласованный протокол/fixture namespace,
но не пересчитывает категории, не подставляет согласие и не внедряет новую
бизнес-политику. Штатная site форма сама проставляет YES; успешный её POST не
доказывает согласие в будущем AI-чате/другом UI.

Производственный crm-lookup имеет общий лимит100/сутки. Этот этап не использует
его токен/endpoint; local lookup либо simulator не расходует и не доказывает
этот production budget. Для локальной проверки429 нужен явно искусственный
лимит, отдельные URL/keys/store и evidence label. Локальные найденные IDs/профили
не становятся справкой о реальном клиенте; сырой ответ не отправляется LLM/UI.

## Изоляция и provenance следующего service

Новый overlay должен попасть в **effective** compose и в runtime verifier.
Текущий verify_runtime.py знает два compose-файла и хеширует только compose.yaml,
apps.compose.yaml и runtime-probe.php. Старый aggregate hash не покрывает будущий
overlay, receiver code, startup/config и изменённый env. Нужны актуальные service
IDs/StartedAt/image, состав active overrides и безопасные hashes relevant files;
секретные env значения в отчёт не выводятся.

Simulator: только internal network, без host ports/overrides/socket/proxy,
production keys/automations и host browser. Code/rootfs RO, отдельный writable
ledger volume с synthetic provenance; инициализация без сети. Собственный
service egress probe и положительный host-control выполняются до cases.
Проверять побочные endpoint URLs/queues надо и при created=true, а не только
при тестовой ошибке capture. Прямые REST/SDK вызовы не должны неожиданно уйти
наружу после разблокировки успешного ответа.

## Что ещё не проверено

Применение DDL/metadata assert/seed, новый isolated job outcome, новый service
и protocol cases этим review ещё не подтверждены. По завершении исполнитель
передаёт actual artifacts: schema/invariant, baseline/new job identity, fresh
runtime proof, capture/ledger evidence, HTTP/flags/jobs и точный evidence_level.
Неизвестность бизнес-исхода не блокирует тест; конкретная утечка либо ложная
граница доказательства требует исправления/правильного обозначения.

## Повторный предстартовый review после правок

Прочитаны исправленные fixture/spec и stage guard. Нормативный контракт теперь
разделяет opaque operation_id из UTM_CAMPAIGN и payload hash; UTM_CAMPAIGN
действительно передаётся LeadsBfl::mapBitrix24:83. Отдельно описаны500+ID,
commit-response-loss и обычный повтор после завершённой postprocessing.
Header SQL теперь intended_evidence_level=native_apps_pre_receiver и явно
synthetic data source. SQL/contract принципиальных расхождений с ADR не имеют.

Артефакты исполнителя после отдельных CREATE: write-orm-schema-assert.json
passed,27columns/7index-columns, group110 пуст перед seed; membership artifact
показывает единственного969001 с обоими markers, disabled/blocked/expired и
непригодным password. Metadata просмотрена критиком, запросы им не выполнялись.
Это достаточная проверка узкой ORM fixture, не доказательство полноты Bitrix DB.

Stage guard теперь валидирует допустимые phase/outcome enum. После failed job
без capture evidence выдаёт legacy_attempt_failed_boundary_unconfirmed вместо
неподтверждённого receiver_boundary_reached. Указанные false-pass границы
исправлены; для узкого stage A препятствий к тесту больше нет.

Дополнительные требования к следующим simulator cases:

- При phone-dedup нового operation нужно персистить mapping operation→existingID
  и его payload hash. Иначе retry этой операции после900s мог бы создать новый
  ID, нарушив заявленную идемпотентность старого operation_id.
- Legacy проверяет bool(created??true): missing→true, string"false"→true,
  JSONfalse→early return. Нормативный simulator возвращает только strict boolean;
  повреждённые/missing поля — отдельные fault cases реального клиента, а не
  исправление его семантики внутри stub.

## Проверенный результат native stage A

Прочитан combined artifact write-stage-native-boundary.json и отдельные before/
after capture snapshots. Критик независимо сравнил их:28→29 events, весь старый
prefix сохранён, ровно1новый POST; его path SHA256 совпал с самостоятельно
пересчитанным SHA256 штатного METHOD_REQUEST. Размер тела1042bytes, raw payload
не сохранялся в артефакте и критиком не выводился.

Это подтверждает настоящий путь site→new job→Laravel legacy→локальная граница
receiver. Новый lead5/job5 изолирован отдельно; old default IDs[2,3,4] и failed[1]
остались. Новый failed ID[2], pending в isolated queue нет. Site category4707,
filled=true,is_crm_sent=true,CRMIDnull,stored HTTP500. Receiver не принял
протокол, genuine receiver и Bitrix creation не выполнялись.

Fresh runtime-isolation от19:10:53+03:00 passed; container IDs/control IDs всех
трёх runtime независимо совпали с текущим Docker inspect. Для этого этапа
evidence_level=native_apps_pre_receiver подтверждён. Сам queue observer сохраняет
boundary_unconfirmed; повышение уровня в combined artifact обосновано отдельным
capture event, а не одним failed job. Нового service этим результатом не проверено.

## Предстартовый review отдельного protocol receiver

Прочитаны protocol_receiver.py, protocol.compose.yaml, make_protocol_env.py и
effective merge всех трёх compose. Новый service ещё не запускался в момент
этой проверки; новый ledger volume отсутствовал. Генератор создаёт локальные
Basic credentials, не читает production secrets, значения не выводятся.

Проверено: CRM сохраняет base env и DB_HOST=database; receiver base только
protocol-receiver:8000, REST только capture:8000. Новый service — isolated only,
без ports/extra_hosts/privileged/socket/proxy, UID/GID1000, RO code/rootfs,
cap_drop ALL/no-new-privileges; RW только собственный `/data` volume. У volume
заданы synthetic/evidence labels. Путь receiver берётся из реального METHOD_REQUEST.

**GO на инициализацию только этого нового ledger volume и boot сервиса.**
Ограниченный helper: network none, rootfs RO, только CHOWN capability для
chown1000:1000/data; без app/env/source и чужих volumes. Затем helper удаляется,
а runtime остаётся nonroot. Contract cases — после runtime-specific egress proof.

Код не содержит внешнего HTTP/SDK клиента. BEGIN IMMEDIATE сериализует запись;
ID возвращается только после commit. Operation aliases phone-dedup сохраняются
отдельно и переживают окончание phone window. Auth проверяется до body parsing;
строгая HTTP framing/ограничение64KiB/socket timeout; error responses без result;
health не отражает данные, stats требует auth и выдаёт только IDs/counts/hashes.
Сырые name/phone/payload/Basic не записываются ни в ledger, ни в logs.

Согласованы два уточнения spec: используется фактически проходящий через сайт
local-write-alnum operation marker; extension policy допускает любые string keys
со scalar/finiteNumber/bool/null значениями, без вложенных коллекций. Это не
allowlist всех бизнес-полей и не эквивалент native receiver validation. Canonical
hash включает весь payload; нормализация не доказывает production phone parity.

Verifier дополнен protocol service, Python runtime probe, третьим compose и
hashes receiver/probe при выборе protocol-receiver. До cases команда должна
охватывать явно site/CRM/partners/protocol-receiver. У прежнего crm-only режима
overlay не попадал бы в provenance, даже если CRM создан с ним; нужен active
compose label либо принудительный protocol scope. Env changes дополнительно
связываются actual container identity/безопасными configuration hashes, не
публикацией секретов. Полный policy checker из этого runner не следует.

Root сообщил19passed независимых ledger/client/HTTP handler tests, включая
concurrency, rollback, alias binding, strict errors. Критик проверил реализацию
read-only; этот результат тестов относится к сообщению root, не собственному
повторному HTTP-запуску критика. Deployment/runtime proof пока отдельный этап.

## Deployment и выявленная wire-граница

После boot критик read-only inspect подтвердил protocol service: only internal,
UID1000, RO rootfs/code/probe, drop ALL/no-new-privileges, без ports; RW только
свой ledger volume с synthetic labels. Runtime proof охватил все4сервиса и
активный overlay. Runner теперь требует protocol scope при соответствующем
active compose label и добавляет actual_env_sha256 без раскрытия значений.
Ранее отмеченный overlay/env provenance gap исправлен для этого пути.

HTTP driver root:10/10 protocol cases passed — auth, JSON/envelope, unsupported
fixture, durable receipt/replay/conflict/phone alias,6 concurrent calls и safe
stats. Эти тесты создали2ledger leads/3operations; поэтому дальнейший app-case
сравнивается с baseline, а не с искусственно обнулённым store.

Первый настоящий app-case не прошёл: ledger не изменился, site job6 failed,
strict observer сообщил failed. Причина422 — реальный Laravel FormRequest
передал RETURN_RESPONSE="1" и category="4707"; IS_BFL_PRODUCT остался bool true.
Это подтверждено source MbkFormLeadRequest и подготовленным wire metadata,
а не произвольная подгонка результата.

Согласованная delta меняет только input validation simulator:
RETURN_RESPONSE принимает true либо string"1"; category — nonbool int либо
canonical decimal string. Payload не переписывается перед hash, смена wire-типа
для уже существующей операции по-прежнему конфликт409. Output created всегда
strict boolean. Spec синхронизирован; root сообщил39passed tests (19прежних,
4положительные комбинации,16отказов). Нового outgoing поведения/endpoint нет.

Критик подтвердил GO на recreate симулятора → fresh4 runtime probe → новый
guarded job для того же site lead6, с сохранением failed3; затем repeat/restart.
Старый CRM baseline job1 уточнён как LogErrorJob, не Direct; он сохраняется.
Bind-mounted Python source требует restart/recreate для загрузки новой версии,
одного изменения файла и его hash недостаточно. Дальнейшие промежуточные
согласования для этих уже разрешённых шагов не требуются; итог проверяется
по actual ledger/site/queue artifacts. Успех после delta пока этим разделом
не объявляется.

## Финальный review protocol milestone

Прочитан protocol-write-milestone.json и его отдельные ledger/site/CRM/runtime
артефакты. Критик независимо пересчитал следующие инварианты, не выполняя SQL,
HTTP или workers:

- Ledger до app-case:2leads/3operations. После первого успешного app-case:
  3leads/4operations, то есть ровно+1/+1. Новый durable synthetic ID700000003
  совпадает с записанным site lead6 и Direct job2.
- Повтор и повтор после receiver-only recreate сохранили ledger целиком
  неизменным относительно первого успешного accept. ID тот же; наблюдавшаяся
  в реальных Laravel response-log metadata последовательность created:
  [true,false,false], все три значения действительно boolean.
- Receiver container ID изменился при recreate; site/CRM/partners IDs не менялись.
  Финальный runtime proof охватывает все4сервиса. Независимо сравнили actual
  container IDs, actual_env_sha256, текущий aggregate configuration hash и
  control IDs: все совпали. Service остаётся internal/nonroot/RO с собственным
  persistent volume. Старый Python process не выдаётся за загрузивший новый код.
- CRM Direct job2 ровно один, class SendLeadSodaTechDirect, отдельная очередь
  local_protocol_direct_unexecuted, attempts0/unreserved. CRM worker и
  партнёрская доставка не запускались. Старый CRM default job1 — LogErrorJob;
  сохранён, не обрабатывался.
- Старые site pending IDs[2,3,4] и failed[1,2,3] сохранены. Новые bounded
  retry/replay jobs закончились без новых failed/pending. Первый диагностический
  отказ до wire-delta остаётся failed3; история не очищалась ради pass.

### Исторический crm_response после успеха

У site lead6 теперь crm_lead_id=700000003, но поле crm_response сохранило прежний
ошибочный HTTP500. Успешный ProcessSendLeadBfl его не очищает. Поэтому старое
stored_crm_http_status нельзя интерпретировать как последний ответ. Финальный
артефакт исправлен: stored_error_http_status=500 и stored_error_is_historical=true.
Это ещё одна подтверждённая граница метрики outcome, не новый отказ успешного
protocol replay. Значения created и durable ID подтверждаются отдельными
response/ledger свидетельствами.

### Итог

**Protocol milestone подтверждён:** настоящий сайт → отдельная bounded очередь
→ настоящий Laravel legacy/ORM → отдельный persistent protocol receiver → тот
же ID на сайте; обычный повтор и повтор после перезапуска не добавили второй
ledger lead либо повторный Direct dispatch в проверенном сценарии.

Уровень исключительно protocol_simulation. Genuine CCrmLead::Add/Bitrix,
реальные business rules/consent parity, downstream delivery и exactly-once
effects при commit-response-loss не доказаны. Нормальный replay после успешного
первого dispatch не закрывает ранее описанное окно потери ответа. Известные
transport/status и stale-flag проблемы этим этапом production-кода не исправлены.

Root сообщил финальные quality gates: Ruff/Pyright0,2486passed/1skipped.
Это regression/contract suite, не дополнительные выполненные production
интеграции. Новых конкретных блокирующих путей утечки в reviewed deployment
не выявлено; дальнейшие уже авторизованные synthetic-прогоны могут продолжаться
с теми же ограниченными runtime/очередями и явными evidence levels.

Финальные README.md/STATUS.md прочитаны после синхронизации. Текущий режим
protocol_simulation вынесен наверх; resume/ps/down используют3compose и
protocol profile; verify охватывает4runtime. Native ошибки обозначены ранней
историей, незатронутые очереди и Direct/unattempted сохранены, stored500 явно
исторический. Документы не объявляют ID симулятора лидом Битрикса, выполненной
доставкой либо решением exactly-once. Дополнительных блокирующих замечаний нет.
