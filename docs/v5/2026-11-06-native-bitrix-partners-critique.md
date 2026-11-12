# Независимая проверка native Bitrix и локальной партнёрки

Дата:06.11.2026. Этап начат по явному требованию получить настоящую запись
Битрикса и нативную обработку партнёрки. Критик не меняет runtime, не выполняет
HTTP/business writes; проверяет план, actual artifacts, read-only inspect и
финальные локальные SELECT/ORM reads.
Предшествующий simulator milestone описан [отдельно](2026-11-06-synthetic-write-critique.md)
и не считается созданием Bitrix-лида.

## Финальный независимый вердикт

**PASS для локальной native цепочки с явно выполненным восстановлением.**
Actual website7 создал настоящий Bitrix lead1. После post-Add ошибки локального
reader сайт связался с тем же ID1 через честный created=false replay; один
реальный source Sender восстановлен вручную. Дальше native CRM/partners jobs
доставили тот же ID локальному mock buyer и сохранили native delivery/accounting.
Production readiness и полностью автоматический исходный handoff не доказаны.

| Проверяемое утверждение | Независимо проверенный результат | Граница |
|---|---|---|
| Native creation/Get | Actual CCrmLead::Add, lead1/reserved ORIGIN, durable native UF/FM/address/birthday | Официальный DEMO core26.150.0 и local API harness, не customer receiver/OAuth |
| Website linkage | Website7→native1 через original created=false replay, старые site row hashes совпали | Две ошибки failed4/5 сохранены; stale500 исторический |
| Sender handoff | Один explicit recovery BusDispatcher→source SendLeadMicroservice1 | Automatic original handoff=false |
| Native partner processing | Partner-local lead1→queue1/campaign950, actual source controller/LeadSend | Fictional settings/schema fixtures; один положительный кейс |
| Mock receipt | UUID из sent совпал с buyer receipt;832 bytes и exact SHA тела совпали | HTTP200 подтверждает только local mock transport |
| Accounting | sent1/history1/usage1, charge10, balance100→90; новые queues/failed0 | Искусственный opening balance100 |
| Runtime/source | Current UID1000/RO/noports/internal, только HTTP server processes;13 archive hashes/8 reflected classes совпали | Сеть проверена на объявленных targets; не production parity |
| Release | **Не допущен как production ready** | Lost dispatch/sent/status semantics и бизнесматрица требуют отдельной реализации/проверок |

Ниже сохранён ход review, включая исторические pending/error checkpoints.
Они не заменяют этот финальный результат.

## Что считается результатом

genuine_bitrix_runtime допустим только при исполнении неизменённого native
CCrmLead::Add, появлении действительной записи lead в отдельной native БД и
чтении фактически сохранённых UF через native API/ORM/таблицы. Ни protocol
receipt700000003, ни заранее вставленная строка, ни ID из test handler этого
уровня не подтверждают. Core/install SQL доступны в Git; исследование bootstrap
продолжается, заранее объявлять его невозможным оснований нет.

Партнёрский этап: настоящий POST native controller, persisted local lead,
реальный selection/campaign/job, один bounded native sender worker, запрос
локальному buyer mock и native sent/history/accounting. Buyer mock — единственная
внешняя граница; его2xx не доказывает приём настоящей компанией/договор/оплату.

У каждого шага сохраняется один run marker и genuine Bitrix ID. Отдельно
обозначаются local partner lead ID, campaign/job/sent/buyer receipt IDs. Ручной
handoff из native UF-read допускает узкую проверку, но не объявляется целиком
автоматической customer цепочкой. Если Laravel продолжает читать прежнюю
reader schema crm, надо явно показать переход к новой native schema; старый
reader fixture не может выдаваться за UF-read нового genuine лида.

## Предстартовые условия native core

- Отдельные service/schema/runtime volumes, свежие локальные credentials;
  protocol runtime/ledger и прежние source/data не меняются. Новые DDL/данные
  применяются только к собственному namespace, без чужого cleanup/DROP.
- Native core и module source имеют pinned manifest. Production .settings.php,
  dbconn.php, license_key.php, customer cache/session/auth/workflow data не
  переносятся. Native config создаётся локально. Реальная коммерческая лицензия
  и её ключи не копируются из Git; допустимый test/development bootstrap
  определяется доступным кодом и правилами установки, без подмены license checks.
- Entry point ограничен lab API и локальной авторизацией; host ports/UI отсутствуют.
  Rootfs/source RO, RW только собственным cache/tmp/upload/data volumes, no Docker
  socket/host aliases/proxy/privileged. Для новых runtime — fresh positive host
  control плюс их собственный local/negative egress probe.
- Agents/cron/SMTP/REST/Naumen/cloud callbacks не получают внешний маршрут.
  Пустые b_agent/workflow/event tables — необходимое evidence, но не достаточное
  доказательство: часть обработчиков регистрируется кодом, customer receiver
  явно вызывает workflows/Authorize/robots. Точные supported guard options и
  prolog flags подтверждаются source; их названия не угадываются.
- Первый pure native Add entry point может не вызывать customer receiver с
  явными robots. Это настоящий Add, но полное поведение того receiver отдельно
  не доказано. UF manager/CCrmLead не заменяются mock-классами, побочные записи
  не выдаются за внешнюю доставку. Проверяется before/after state автоматизаций.

## План локальной партнёрки: review до реализации

CRM reviewer предложил отдельные чистые schema/app без manufactured counter
rows; source DDL+BFL migrations, без customer seeders. Первый fixture: один
partner991001/campaign950, hands1/delay0/frozen1day/duplicate2days,price10,
daily limit2,balance100, без day/time ограничений. Все значения — **тестовые**,
не подтверждённые production settings.

Для подготовки план условно одобрен. До выполнения нужны конкретные native
transmitter class и source поле URL, направленные только локальному buyer.
Подменённый sender вместо native implementation не подтверждает этот этап.
APP_DEBUG=true как gate аналитики проверяется source; это lab configuration,
не production parity. Все самодиспетчеризуемые follow-up jobs учитываются.

Fixture genuine lead:Москва,debt400000,otherDebt0,consent23000,category3895,
IS_BFL_PRODUCT=true и синтетическая взрослая birthday. Здесь важна новая
граница: BFL classifier не проверяет возраст, но native legacy distribution
checkAge сбрасывает именно московский fitForBFLMicroservice при null/<20
(MIN_AGE=20; строки22,337–339); региональный флаг этим условием не сбрасывается.
Взрослая дата в happy path не доказывает, что
возраст не требуется downstream. Это расхождение следует учитывать в будущей
короткой анкете, не добавляя выдуманную DOB вместо неизвестной.

Native partner POST/selection/job/buyer receipt должны подтверждаться реальными
строками/событиями, а sent rows не создаются вручную. Проверить actual tag/geo/debt
filter match, campaign ID, один выбранный получатель, accounting change и их
связь с native Bitrix lead. SHIPPED update через настоящий local
CCrmLead::Update допустим при тех же guards и последующем UF-read; update harness
не выдаётся за выполнение полного Bitrix REST/auth API, если тот не исполнялся.

## Сохранение прежнего стенда

Старые site pending IDs[2,3,4],failed[1,2,3],CRM LogErrorJob1 и Direct job2 в
local_protocol_direct_unexecuted не consume/retry. Используются новые записи,
новые service/schema и отдельные named queues с identity guards. Только bounded
workers; общего default worker, cron/supervisor/schedulers нет.

Текущий статус этого отчёта — **план reviewed, новый native service ещё не
прошёл предстартовый review конфигурации**. Реальный Add/UF и native partner
delivery пока не объявляются подтверждёнными. После получения compose/config/
guards критик уточнит допуск, а по итогам — независимо сверит final chain.

## Offline review native partner schema/fixtures

Прочитаны partner-native-plan.md, partner-native-prepare.py и generated
dev/mbk-local/partner-native-prepared/{schema.sql,fixture.sql,manifest.json}.
Критик SQL/HTTP не выполнял. Самостоятельная проверка файлов:

- 37 CREATE, schema не содержит DML/DROP/TRUNCATE/USE/external clauses;
  выбранные native ALTER действительно ADD COLUMN/INDEX/CONSTRAINT.
- Schema/fixture SHA256 совпали с manifest. Source hashes перечислены;
  raw customer INSERT и snapshot AUTO_INCREMENT counters не перенесены.
  FK parents упорядочены до children, проверка FK не отключается.
- Fixture INSERT направлены только в dictionaries, partner/settings/campaign,
  campaign tags, opening balance ledger и working calendar. Ни leads/sent,
  ни jobs/distribution/history/daily_usage не seeded.
- Подтверждён единственный source-inferred элемент: leads.double_count INT
  default999. Root явно принял его как user_fixture/inferred_from_native_code;
  миграции этого поля в доступном snapshot нет. Не выдуман phone_uniq index,
  native UNIQUElead_id сохранён. Race/phone-dedup/DB parity этим не доказываются.
- Конкретный sender — неизменённый native App Jobs LeadSend, читающий
  partner_settings.callback_url. URL generator разрешает только internal
  partner-native-buyer:8000/receive, без credentials/query/fragment.

GO на передачу prepared SQL runtime-агенту и применение только после assert
новой пустой partners_native schema и проверки полученной metadata. Новые
service/buyer/worker этим не запущены и не подтверждены. Общий OUTPUT preparer
перезаписывается, поэтому actual run manifests сохраняются отдельно до следующей
подготовки; нельзя потерять provenance уже выполненного run.

Schema SHA256:334a328a7b59e422a56ba414d7608386acadaa589552a35bca8b0d9c2847d512.
Review fixture SHA256:5b2aca291bf6d24377bdea29d8e94d9a0e2968382f762a411afa6055b81a47fc.

## Native Bitrix draft: конфигурация и guards

Прочитаны bitrix/BOOT_PLAN.md,compose.yaml,settings.php,dbconn.php,router.php,
kernel.php,start-native.sh,php.ini,make_native_env.py. План использует чистый
webroot и source-модульный volume, не весь customer project2. Сгенерированная
DB учётная запись ограничена новой bitrix_genuine_v1 schema; старые схемы не
выдаются ей. PHP image добавляет mysqli/exif/pcntl без customer entrypoint.

Draft service nonroot/RO/internal, без ports/host aliases/socket; RW только
own upload/cache/tmp. Router разрешает health без kernel и один authenticated
kernel endpoint, остальные пути/инсталляторы не обслуживает. Mail transport
через /bin/false; config/crypto/Basic credentials создаются локально.
Это позволило условный GO на idle HTTP service/health/его egress-проверку,
но не преждевременный вывод о native Add.

Source main/include.php:132–142 действительно загружает customer init hooks.
В clean webroot они отсутствуют/заменены явным пустым lab init. Пустая таблица
регистраций сама бы этих hooks не отключила. main check_agents/check_events
должны быть N. Допускается source-backed allowlist только штатных core
OnUserTypeBuildList handlers, нужных настоящему UF manager; сам менеджер не mock.

Остался предстартовый вопрос native kernel lifecycle: main/include.php:38–44
добавляет WWall/VendorNotifier background jobs, а Application::terminate():387
вызывает runBackgroundJobs. До kernel/Add оператор проверяет реальное выполнение
этой lifecycle и поддержанные guards. Одной пустой b_agent недостаточно.
Если настоящий Add создаст native agent/event/timeline rows, их следует
сохранить в evidence, не удалять ради нулевого count; критично отсутствие
неразрешённого исполнения/внешних эффектов и закрытый egress.

В первоначальном kernel helper найден premature label genuine_bitrix_runtime
при main_kernel_only/lead_created=false. Исполнитель его удалил: теперь kernel
boot остаётся диагностикой; genuine label выдаётся combined Add+native UF proof.
Полный native boot/start/Add этим разделом ещё не подтверждён.

## Независимая сверка загруженного backend source

Критик пересчитал Git blob SHA1 всех записей native-source-tree.json:
8830 файлов/34,970,747 bytes,0missing/0mismatch, один pinned ref240fd2ae… .
Разбивка: main2225,crm6558,currency47. Production license_key.php/dbconn.php
среди этих выбранных module paths отсутствуют. Это достаточное основание для
сверки ReflectionClass/метода Add с исходным файлом, но **не полный clone**.
Код не менялся ради bootstrap. Явные runtime/config fixtures проверяются отдельно.

Дополнительно source проверены безопасные early-return условия security jobs:
WWall::refreshRules использует main_sec/WWALL_ACTUALIZE_RULES и TTL10800;
VendorNotifier::refreshNotifications — SEC_ACTUALIZE_VENDOR_NOTIFICATIONS,
TTL86400. Они могут служить явно искусственными offline freshness guards,
без license bypass/patch. Это не означает, что настоящие security updates
были выполнены. HttpApplication::finish пуст; фоновые jobs выполняет явный
Application::terminate. Native helper lifecycle и guard freshness проверяются
до вызова kernel; одних названий options без состояния недостаточно.

## Unified prestart review и выполненная подготовка

Native apply_prepared.py/verify_native.py прочитаны до применения. GO охватил
только новую schema/user, prepared373DDL+минимальные local metadata, own modules/
upload volumes, idle service/health/probe и guarded main-kernel diagnostic.
Указано убрать MySQL password из argv в пользу MYSQL_PWD; нового согласования
для этой точечной поправки не требовалось. Add/UF proof в этот допуск заранее
не включался. Kernel helper уже не выдаёт genuine label до фактического Add.

Отдельно reviewed native-partners.compose.yaml,partner-native-runtime.py и
partner-native-buyer.py. Standalone project не пересоздаёт старые сервисы;
external network name ссылается на проверенную существующую Internal=true сеть,
а не даёт внешний маршрут. Оба runtime nonroot/RO/noports/extra_hosts/socket;
RW только own buyer receipt volume. Buyer unarmed до подтверждённого genuine ID,
POST пока503; собственно mock не пересылает данные и пишет hash receipts с fsync.

Root secret provisioning helper получает из env существующего MySQL контейнера,
не в argv; credential-bearing SQL/stdin/error не выводит. Own schema/user
partners_genuine_v1/mbk_partner_native_v1 создаются только при отсутствии, затем
empty-target и checksum guard. В seed отсутствуют evidence rows. GO был дан
единым пакетом на provision/import/own volume init/start/GET health/egress,
**без** lead POST/arming/worker. Пакет завершился в разрешённых границах.

Артефакты native20261106v1: import-result.json показывает0leads/sent/queue/history/
daily_usage/jobs. Capacity GET200/more=true; buyer health200/unarmed/receipt0.
Fresh host-positive control и оба runtime probes прошли. Критик independently
Docker-inspect подтвердил running/internal/noports/nonroot/RO/source/vendor
mounts для partners-native и partner-native-buyer. Лидов не отправляли, worker0.

Windows provenance замечание устранено sidecar: schema-hash-provenance.json
явно различает старый normalized_utf8_text_lf hash и actual disk-byte SHA256;
prepared inputs не переписывались. Новый generator пишет LF bytes. Это не
изменение смыслового SQL. Подготовка стала immutable по run-specific output path.

## Наблюдаемый предел native Bitrix kernel

Первый диагностический500 объяснён source acquisition filter: отсутствовали
два настоящих main/lib/data/cache класса. Root исправил область исключения
runtime cache vs source cache classes и получил exact Git blobs. Core не
патчился; новый own module volume v2 содержит8837 byte-verified files.
source-volume-proof.json сверяет runtime source с manifest через network-none
helper. v1 ошибка сохранена как diagnostic evidence, schema/upload/protocol
из-за этой source-only замены не очищались.

После исправления main prolog вернул **HTTP302 на local coupon_activation.php**.
kernel-smoke.json: redirects не следовались, native Add не вызывался; до/после
0native leads/agents/mail events/handlers. genuine_lead_proven=false. Это
наблюдаемая activation boundary после получения source, не основание называть
сам bootstrap технически невозможным. Обфускация не декодировалась, license
checks/key/core не подменялись и production license не копировалась.

Официальное руководство описывает coupon_activation.php как путь регистрации/
разблокировки demo и получение владельцем легитимного ключа при регистрации
пробной установки; это направление для штатного test/trial recipe, а не
skip-check. См. [регистрацию DEMO](https://dev.1c-bitrix.ru/learning/course/index.php?COURSE_ID=32&LESSON_ID=1946).
Отдельная [документация Битрикс24](https://helpdesk.bitrix24.ru/open/23155430/)
подтверждает наличие30-дневной демо-установки коробочной версии. Совместимость
конкретной trial activation с текущим source ещё не доказана. Критик никакую
регистрацию/отправку email/активацию не делал и runtime egress не открывал.

**Текущий результат независимой проверки:** genuine Bitrix Add/UF и финальная
цепочка до native partners/buyer **ещё не достигнуты**. Native partner stand
подготовлен и ждёт genuine ID, buyer остаётся unarmed. Нельзя компенсировать
activation boundary ID симулятора, ручным INSERT лида или фиктивным partner
receipt. Следующий шаг — поддержанный легитимный native activation/bootstrap
путь; после него actual Add/read и разрешённые native sender/worker проверяются
по ранее установленным критериям с сохранением старых очередей.

## Продолжение: новая официальная trial установка

Root выбрал fresh official portal DEMO, не reset/patch старой Git-копии.
DOWNLOAD-MANIFEST указывает официальный bitrix24_encode.tar.gz,786212629bytes,
SHA256e379b86869d4904d919ba1e2daffbaac8be3e15995a67f8b87ada85c7b8692d0.
Root проверил140202archive members/около1.46GB и наличие CRM/core26.150.
Это другой источник native core, а не доказательство равенства версии production.
Старый Git runtime и его activation diagnostic сохраняются отдельно.

Предстартово прочитаны official.compose/router/start/ini,prepare_official.py,
apply_official.py и bounded extractor. GO охватил fresh bitrix_official_v1/user,
новый own webroot volume, network-none CHOWN helper и nonroot extraction с
datafilter/path bounds/reject links/devices и8GB cap. Runtime rootfs RO,
webroot RW только для штатной установки, internal/noports/no proxy, SMTPfalse;
ключи/админ credentials новые локальные. Старые БД/volumes не меняются.

Router с Basic auth разрешает только / и /index.php; health не грузит kernel.
CGI PHP_SELF/SCRIPT_FILENAME и short_open_tag/opcache параметры настроены
в собственном wrapper/ini по требованиям дистрибутива, не vendor patch.
После fresh egress proof protected первый GET дал HTTP200/welcome; DB была empty.

Дан продолженный GO на штатную wizard navigation без JS/внешних redirects/
персональных данных. Registration checkbox оставлен off, имя/email для
регистрации не отправлялись; шаги license/requirements прошли нормальным путём.
Конкретный DB mapping reviewed: host/user/password/database только generated
MBK_NATIVE_DB_*,create_user=N/create_database=N для existing empty own schema,
root fields пустые,file0644/folder0755. Далее допускается штатный CreateDB/
CreateModules. Это обычный installer scope, отдельного approval на каждый Next
не требуется; неожиданный внешний registration/payment/key request не обходится.

После официального InstallDB штатные agents/handlers могут быть ненулевыми:
их source/inventory и ограничения исполнения проверяются перед Add. Не следует
удалять штатные записи ради фиктивного нуля. До combined native Add+UF read
эта trial-установка остаётся bootstrap/installation evidence, не genuine lead.

## Расширение website input в genuine branch: prestart review

Root подготовил отдельный site-genuine process с теми же pinned RO site/vendor,
новыми APP_KEY/sessioncookie/env и адресом только crm-genuine:8000. Повторно
используется существующая **искусственная** site schema без дополнительных
миграций; это не изолированная копия её записей. Старый container не пересоздаётся.

Merged active-profile config проверен: only internal/noports/nonroot/RO,
QUEUE_CONNECTION=database,APP_ENVlocal (реальный CSRF),Mail array. Env maker
проверяет provenance старого generated lab env и не читает production env.
Fixture runner допускает только site:8000/site-genuine:8000 и суммы189999/400000;
новый run использует явно искусственный недозваниваемый000-prefix phone.

genuine-site-stage.php reviewed: baseline hashes **всех** jobs/failed rows;
ровно один новый marked400k filled lead; scalar serialized leadId без unserialize;
transaction/lock/attempts0/unreserved/default guard и ровно1перенос в named queue.
Observe требует тот же ожидаемый native ID ниже simulation namespace, отсутствие
новых pending/failed и побайтовую неизменность старых queue rows.

GO на idleup/probe после готовности crm-genuine. Сам website POST/worker ждёт
настоящий native receiver и automation guards; это ожидаемый следующий шаг,
не дополнительное разрешение на уже авторизованный scope. Thin adapter
source enums3895/23000→fresh native returned enum IDs маркируется явно; он
переводит идентификаторы, не пересчитывает debt/согласие и не подменяет реальный Add.
Customer receiver/OAuth и production metadata parity этим не заявляются.

## CRM genuine: отдельный idle process и условия handoff

Повторно прочитаны crm-genuine.compose.yaml, crm-genuine-prepare.py,
crm-genuine-auth.php, crm-genuine-stage.php, partner-native-stage.php и
partner-native-single-id-workflow.md. Для provision/start/GET/egress/baseline
блокирующих замечаний не найдено: отдельный compose не пересоздаёт старую CRM,
source/vendor RO, UID1000, без ports/proxy/socket, только существующая internal
сеть. Новый пользователь имеет SELECT на native business schema и DML только
на собственные jobs/failed_jobs; provision требует отсутствия пользователя и
обеих queue tables. Читается только созданный локально native.env.

GO дан на этот idle этап без Add/worker. Дальнейший handoff требует отдельно
доказанного website native ID, actual Laravel Http/BitrixRest auth, того же
ORIGIN_ID/debt/адреса через native API и ORM, единственного настоящего
SendLeadMicroservice и затем LeadSend. Queue helpers проверяют старые row hashes,
новые failures и exact job class/payload; перемещают только новый unreserved
attempts0/default job. Сам supplied ID или завершение worker не является proof.

Draft native-api.php пока не исполнен. До Add требуется исправить отмеченные
root несоответствия фактическому wire phone/date, валидировать входные debt,
monthly/other/address/consent вместо подстановки ожидаемых значений и проверять
полные postconditions также при повторе после частичного Add. До этого draft
не является доказательством genuine lead. Родитель и local_stack уведомлены.

### Проверенный checkpoint официального ядра

native-kernel.json показывает HTTP200, main26.150.0 и настоящий CAllCrmLead::Add.
Reflection SHA256 crm_lead.php совпал с независимо извлечённым официальным
ARCHIVE-CRITICAL-SOURCE.json: 60063a37481147f8ba0cae3fcb8c706c650503f16df48263b04c6787ac4a35ed.
Это проверка загруженного исходника и prolog, пока **не вызов Add**.
На момент проверки native-metadata.json фиксировал HTTP500/RuntimeException
без создания лида; зелёным завершение metadata не объявляется.

guarded-before-kernel-inventory.json: 1624 tables, lead0/mail0, agents241,
handlers1727, workflow template/instance/state0; check_agents/events=N.
Штатные агенты и handlers сохранены, security updates и license modifications
не выполнялись. Прочитанный официальный crm_lead.php112–123,1440–1442,
2120–2122 направляет factory Add/Update в compatibility adapter с
runAutomation=false/runBizProc=false. Следует зафиксировать фактическое
factory-enabled состояние перед настоящим Add, без изменения его ради теста.

Замечание о длине DB lock снято по native source: mysqlcommonconnection.php
297–334 сам формирует 64-символьный ключ md5(serveruniq)+md5(name).

Read-only Docker inspect после CRM idleup подтвердил official/crm-genuine/
partners-native/buyer: только mbk-local-lab_isolated (Internal=true, IPv6=false),
UID1000, RO root, cap_drop ALL, no-new-privileges, без published ports или
privileged. У buyer единственный writable persistent mount /data; у новых CRM
и official источники readonly, runtime пишет в tmpfs.

Отдельно прочитан original intranet/lib/ustat/crmeventhandler.php из official
critical-inspect: OnAfterCrmLeadAdd вызывает только UStat::incrementCounter('CRM').
Это targeted source inspection, не объявление аудита всех 1727 штатных callbacks.
Website-native-v1/site-baseline.json сохранил row hashes прежних site pending
IDs2/3/4 и failed IDs1/2/3. Эти строки должны сохраниться после genuine цепочки.

Следующий checkpoint: native-metadata.json теперь HTTP200 и 14 native UF;
fresh enum IDs15–21 созданы настоящими metadata API. Предыдущий записанный
HTTP500 был ошибкой локального helper, не ошибкой Add (лидов ещё не было).
Обновлённый native-api проверяет actual input и stored BIRTHDATE ISO, monthly,
SUMM→OPPORTUNITY/RUB, optional other debt, address, PHONE/FM и fresh enum IDs.
Единый native_assert_saved вызывается и после Add, и при replay; прежняя
возможность подтвердить частичный Add только по payload hash закрыта.

Дополнительно прочитан native-ustat-counter-source.json: incrementCounter
сначала проверяет доступность companypulse/notification, затем при отсутствии
авторизованного global USER возвращает false; остальной body обновляет local
hour/day/department counters. Это достаточный targeted review в сочетании с
internal egress/no executor, не основание менять vendor hooks.

## Допуск одного настоящего website lead

Финальный official-router/native-api/official.compose пакет просмотрен до
исполнения. Basic проверяется до bootstrap, JSON ограничен64KB, доступен только
derived local Add path и два конкретных Get/Update path. WRITE_ENABLED
включается явно, источники RO, customer receiver не исполняется. Native Add
возвращает только фактический ID после native-read postconditions, Update
ограничен тремя shipped полями и собственными fixture leads.

Дан единый GO на WRITE1→RO recreate→fresh egress/kernel factory state→один
actual website400k fixture→owned named site worker→native Add/Get/Update и
настоящий CRM sender по single-ID workflow. Отдельного smoke lead не требуется.
Покупатель вооружается только после фактического website native ID+proof.
Это допуск технически безопасного опыта, **не утверждение его успеха**.
Повторные проверки каждого Next не нужны; неожиданный внешний target,
непринадлежащая очередь или неоднозначный результат Add останавливают опыт.

Независимо выполнен read-only runtime sha256sum всех12 файлов
ARCHIVE-CRITICAL-SOURCE.json непосредственно в official container:
runtime/archive mismatches0. Vendor source не менялся.

## Первый actual website запуск: ошибка сохранена

native-kernel-final.json подтверждает factory_enabled=true и семь настоящих
Reflection classes с archive-matching hashes; runtime-isolation.json свежий
control25a8f5c146380aa840483126/containerbc21bbc1… прошёл после recreate.
Это выполняет предварительные условия Add, не заменяет сам Add результат.

Website-native-v1/site-isolate.json: actual filled site lead7, собственный
ProcessSendLeadBfl job10 перемещён в local_native_site_20261106_v1, прежние
queue rows сохранены. Site-worker.json: CLI exit0, но site-identity.json
после него показывает crmID=null, is_crm_sent=1, сохранённый HTTP500,
failed_jobs4 (прежние3 плюс новый failed). Это **не успешная native цепочка**.
Outcome проверка поймала ошибку вместо ложного green по CLI/sent flag.

Перед recovery необходимо read-only сверить b_crm_lead по ORIGIN_ID и payload
hash: ошибка могла возникнуть до или после Add. Нельзя повторять операцию
вслепую, удалять failed evidence или armed mock ради завершения опыта.
Причина и строго ограниченное продолжение разбираются исполнителями.

Уточнение причины по first-create-failure-types.json и read-only reviewer:
CRM prepared monthly=null; native adapter отверг его как ненумерическое до Add.
Native website lead0 и CRM queues0, buyer unarmed. Нулевые/пустые group110 и
settings здесь не SQL failure: выбранная ветка не использует manager.
Исходный MbkFormLeadRequest46 разрешает monthly nullable|string. Следующий
adapter contract должен честно поддержать nullable и сверять null native UF,
сохранив первый failed4; подстановка0 исказила бы фактический pipeline.

Bounded retry просмотрен отдельно: тот же ProcessSendLeadBfl/site7, один
retained failed4, original row hashes и новая baseline, empty named queue,
transaction+lead lock. Nullable adapter fix не менял source apps. После
непосредственной проверки native operation rows0 запущен новый job11.
Site-retry-identity сообщает реальный native ID1 в БД, но site.crmID по-прежнему
null, failed5, CRM jobs0: произошёл **частичный Add до ошибки ответа**.
Повторные слепые retries остановлены, native lead1/failed4/failed5 сохранены.

После полного native UF/Get доказательства допустим явно обозначенный manual
recovery dispatch настоящего SendLeadMicroservice(ID1,XML) в свою очередь.
Он докажет native sender/partners, но не original automatic website→sender
handoff. Existing lead нельзя выдавать за created=true ради обхода source
created=false early-return; это скрывало бы уже известное окно lost dispatch.

### Native creation доказано, automatic handoff ещё не доказан

Причина второй ошибки — локальный reader воспринимал virtual UTM reference
LeadTable как scalar. Исправлено только чтение через оригинальный UtmTable ORM:
никаких repair writes, удаления lead1 или нового Add. В b_crm_utm данные уже
сохранялись корректно.

Независимо проверен native-existing-lead-private.json без вывода phone/секретов:
HTTP200/ID1, reserved ORIGIN, debt400000, monthlyNULL/otherNULL, native birthday
1990-01-01, native address Moscow, ровно один WORK phone с digest равным UFphone,
fresh consent17/category18 и factory_enabled=true. Значения читаются из native
ORM/UF/FM/address, а не исходного JSON. Прежние семь Reflection SHA совпадают
с official archive; новый восьмой UtmTable требует дополнительного archive
entry при итоговой provenance синхронизации (не новый leakage blocker).

Это genuine native lead creation/storage evidence. Далее согласован honest
replay created=false для website linkage1 и явно marked manual recovery sender.
Original automatic postprocessing и доставка покупателю пока не подтверждены.

Site-ack-observe.json независимо просмотрен: site7→Bitrix1, new pending0/new
failed0 относительно recovery baseline; старые queue rows и retained failed4/5
сохранены. Historical stored500 явно обозначен как прежняя ошибка, не latest
HTTP. Created=false replay не создаёт original Sender — это важное ограничение
фактически наблюдавшейся цепочки, не только теоретический failure mode.

UtmTable provenance gap закрыт: critical manifest теперь13 entries; все8
Reflection hashes из actual nativeGet имеют exact archive match. Дополнительно
runtime sha256sum crm/lib/utm.php совпал с archive a509901830307d36aa18d2a8c7940c00ecdb8f7f589c9acbebb398229b347907.

crm-genuine-recover.php просмотрен: actual authenticated Get, dynamic enum join,
ORIGIN/actor/date/debt/FM/address/SUMM/RUB/hash/shipped-empty proof, пустые CRM
queues. Настоящий BusDispatcher создаёт один source SendLeadMicroservice в
своей named queue; после него проверяется class/scalar ID/XML/attempts0.
Host marker фиксирует invocation до dispatch и запрещает повтор после unknown
outcome. Manual-post-add-recovery-id1.json: state dispatched_once/job1/Bitrix1,
до него все7 partner evidence tables0 и buyer unarmed. Buyer-arm-proof.json
связывает native1/website7 с теми же nativeGet и siteACK SHA, которые просмотрены
критиком. GO на два bounded source workers дан; pending terminal proof.

Полный artisan queue:work остановился до job из-за конструктора посторонней
GoogleSheets command без SPREAD_SHEET_ID. Job1 оставался attempt0/unreserved,
поэтому redispatch не нужен. Отдельно просмотрен partner-native-work-once.php:
создаёт original Illuminate Queue WorkCommand с actual app queue.worker/cache,
выполняет --once/свою queue/tries1/timeout75 под внешним95s bound. Сохраняются
оригинальные события и failed provider, никакого прямого job.handle нет.
Before: ровно1accountedjob/attempt0/unreserved/class+genuineID. After: ровно
processing1/processed1/failed0 и queues0/failed0, иначе nonzero. Framework
source SHA и хеш console output сохраняются. GO на такой runner дан; обход
full Artisan command discovery должен остаться явной особенностью стенда.

## Финальная независимая сверка persistent evidence

Прочитаны root-native-partner-snapshot.json, native-final-shipment-proof.json,
оба native-framework-worker.json, sender/delivery observe, buyer-receipts,
manual recovery/arm evidence и native automation inventory. Дополнительно
критик сам выполнил **только read-only локальные SELECT/ORM reads**, без HTTP,
dispatch или business writes. Это расширение проверки сверх первого prestart
прохода; production API не вызывался.

Raw b_crm_lead/b_uts_crm_lead: actual ID1/reserved ORIGIN/birthday1990, debt400k,
monthlyNULL/otherNULL, consent17/category18, shipped1 и непустой datetime.
Multi enum лежит в b_utm_crm_lead.VALUE_INT:15→microservice_bankruptcy
(generic VALUE для enumeration NULL; он не использован как ложный признак
потери значения). API/Get и raw storage совпали.

Из фактического native partner lead data и campaign950.label восстановлен
тот же source LeadSend JSON через original Guzzle Utils::jsonEncode, **без
повторного запроса**. Получено832 bytes и
3e33bc7c6bcf8ae01cf8ace8d7f486b412db5a1b1eda7ea82317848092ee553c —
точное совпадение с durable buyer receipt. Его UUID
local-e103fe7c3ef641c1b57d63e77e3f1360 совпадает с actual native sent response.
Root snapshot и native app snapshot согласуются: local lead1/Bitrix1,
queue1/campaign950/partner991001, Delivered2, sent1/history1/usage1,
charge type4 amount10 и balance90. Оба framework workers: processing1,
processed1, failed0, pending0/failed0.

Критик повторно снял actual site baseline read-only helper и сравнил полные
row SHA с original и recovery baseline: pending2/3/4 и failed1/2/3/4/5,
**mismatches0**, лишних строк0. Старые CRM LogErrorJob1/default и
SendLeadSodaTechDirect2/local_protocol_direct_unexecuted остаются attempt0,
unreserved; они не исполнялись. Для старой CRM здесь проверены row identity/
class/queue/attempts, а не заявлены отсутствующие исторические full-row hashes.

Повторный Docker inspect пяти новых services: UID1000, RO root, nonprivileged,
cap_dropALL, published ports0, только mbk-local-lab_isolated/Internal=true,
IPv6=false. Read-only process inspection показал только php -S HTTP servers
и python -u /app/partner-native-buyer.py; one-shot workers/cron/supervisor нет.
Первый Docker top без PID не сработал; повтор с PID,args успешно подтвердил
процессы, ошибочный пустой вывод не считался доказательством.

Все13 official critical-source файлов повторно SHA-checked непосредственно
в actual runtime: mismatch0;8 Reflection классов покрыты archive manifest.
Текущие official container ID, env SHA и aggregate config/helper SHA совпали
с финальным egress proof control4a20c90ac81da4e29e9f7cf6. Все22 byte hashes
final-delivery-manifest.json пересчитаны: mismatch0. Private env/private Get
артефакты покрыты gitignore.

Automation final:241 существующих agents не поменяли LAST_EXEC; новый280
LAST_EXEC пустой, mail events0,1727 штатных handlers сохранены, workflow
template/instance/state0, check_agents/events=N. Число agents242 означает
новую штатную schedule запись, **не исполнение агента**.

### Оставшиеся findings и ограничения

- **P1, release:** receipt-loss после durable Add + created=false replay
  пропускает original postprocessing. На этом прогоне потребовался явный
  recovery dispatch; exactly-once/automatic recovery не реализованы.
- **P2, observability/correctness:** site sent flag выставляется до проверки
  ID/HTTP, прежний crm_response500 не очищается; source create client игнорирует
  HTTP status. Нужны явные состояния/HTTP-result contract, а не один shipped flag.
- **P2, scope:** fresh official core и local API adapter, искусственные partner
  settings/double_count inference и один adult400k Moscow case не подтверждают
  production eligibility/consent/dedup/timeouts/concurrency, полноценный clone
  или CMS/browser QA. Global full-artisan discovery также пока неработоспособен.

Новых blocking leakage findings по фактическому изолированному прогону нет.
Релизные findings не отменяют достигнутую native storage→source sender→local
partner→mock receipt/accounting проверку в явно указанной границе.
