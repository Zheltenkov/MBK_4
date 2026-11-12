# Независимая проверка контекста обращения для бота

Дата: 06.11.2026. Scope: этап1 трёх приоритетов Михаила — безопасное получение
данных текущего обращения, отдельно от CRM history. Критик не меняет app/runtime,
не вызывает production API и не создаёт/отправляет лиды. Документ содержит
контракт, проверку реализации и фактическую adversarial acceptance.

## Финальный независимый вердикт

**PASS для локального stage1 read-only.**
Данные текущей формы привязаны к owning session и подписанной server reference;
история CRM отделена, raw fields не попадают в LLM/public, любая context session
удерживает отправку. Найденные redirect/privacy дефекты исправлены и повторно
проверены. Никакие production API/поставки не включались. Это не production
readiness, не готовая долговая policy и не автоматическая доставка онлоадера.

Критик повторно выполнил116 связанных тестов:116 passed. Корневой оркестратор
завершил полный gate после Dockerfile/test исправлений: Ruff/Pyright0,
pytest2620 passed/1 skipped,113.57s, exit0. Mutation artifact независимо
проверен: baseline51 passed,27/27 killed, fixture/collection errors0;
99 скопированных live source/data файлов совпали по SHA256, изменений0.
Новых blocking findings в проверенной границе нет. Исторические pending/ошибки
и их закрытие ниже сохранены для воспроизводимости review.

| Проверяемая граница | Итог | Чего не доказывает |
|---|---|---|
| Session owner + signed application/subject/revision | PASS: actual HTTP, immutable repeat/conflict/invalid handling | Наличие issuer/ownership API в production МБК |
| Fresh snapshot + bounded authorized handle | PASS: active/reference/time/future/expiry/slow-source checks | Реальный backend resolver и его I/O deadline |
| Current form отдельно от history | PASS: current facts useful, old fields/empty/errors не создают новую заявку | Полноту CRM history и нового клиента по leads=[] |
| LLM/public/snapshot minimization | PASS: actual conversation/extraction spies при PIIflagFalse | Анонимизацию raw server storage |
| Lookup transport + budget | PASS: no redirect/retry, local HTTP negative control,4-process shared-file budget | Общий лимит иных клиентов Андрея и координацию разных host files |
| Полная/отложенная отправка | PASS: direct coordinator и реальные source paths held для всех context states | Consent/delivery ownership или право снять hold |
| Runtime deployment | Defaultoff, missing adapter fails closed, only intakeOFF/mock allowed | Production/bluegreen readiness без context CAS и интеграции виджета |

## Контракт, допустимый к реализации

Состояния далее описывают домен; названия Python enums определяет согласованный
typed package, тесты должны опираться на его публичные типы.

| Область | Закрытые состояния / правило |
|---|---|
| Вид обращения | standalone; current_site_application; partner_onloader; repeat_customer. Первые browser анкета/UTM не выбирают privileged source |
| Привязка | unbound → verifying → bound; rejected/expired для первой owned ошибки. Успешная bound привязка immutable |
| Текущий snapshot | available; missing; stale; unavailable; invalid; subject/application mismatch. В available есть provenance/revision/time, иначе значения unknown |
| CRM history | not_requested/disabled; found; outside_coverage; unavailable; quota_exhausted; invalid_response. История никогда не означает текущую форму |
| Отправка | На этом этапе новых отправок нет. Context-error/held не может вызвать fallback full/abandoned intake |

1. Браузер сначала создаёт обычную сессию. Existing HttpOnly
   mbk_session_grant уже подписан HMAC и привязан к session_id: новый ownersecret
   не нужен. Новый attach требует именно эту ownercookie **до store lookup и
   state mutation**, даже если middleware пустил operator auth.
2. Trusted backend выпускает короткоживущую HMAC capability с audience,
   session_id, application_id, subject_id, iat, exp (максимум300s), отдельным
   server-only signing key. Публичной issuance-by-phone/formID route нет.
3. Resolver проверяет подпись/тип/TTL/session binding до fetch. Fetch выполняет
   только configured trusted ApplicationSource, без browser URL. Snapshot
   обязан совпасть по app и subject с capability; один phone не заменяет identity.
4. После bound нельзя сменить app/subject/phone/revision молча. Тот же контекст
   повторно идемпотентен. Другой valid capability или invalid/stale token к
   уже bound session отклоняется без downgrade/fetch/lookup. Первая owned
   ошибка может перевести сценарий в held; foreign-session попытка ничего не меняет.
5. Raw anketa/phone/formID/UTM — клиентские данные для старого prefill, а не
   полномочие на lookup, authoritative product intent или reuse чужого телефона.
   Capability даёт доступ к server snapshot, но сама по себе не consent на
   отправку партнёрам. Site-consent endpoint не используется как identity proof.
6. Current application и CRM history хранятся отдельно, с provenance/time.
   Отсутствие поля сохраняется unknown; null/zero/false не отождествляются.
   Старый долг/успешная сделка/отсутствие history не переписывают current request.
7. В LLM идёт отдельная allowlisted projection: bounded numeric facts и закрытые
   semantic enums. Raw names/fields/comments/URLs/contacts/IDs/capabilities не
   передаются. Upstream «без ПД» не заменяет эту границу. Public state, GETanketa,
   snapshots, analytics и persisted state проверяются отдельно.
8. Lookup принимает только resolved authorized handle, не произвольный phone.
   Endpoint/токен заданы server config, redirects/proxies отключены, payload и
   response ограничены. Один HTTP attempt, timeout; нет retries429/5xx/timeout.
9. Общий upstream100/day включает иных клиентов Андрея. Наш coordinator может
   обеспечить лишь выделенный **наш** atomic budget across workers/replicas.
   Reserve до HTTP, расход не возвращать при неопределённом исходе. Durable
   shared store или lookup runtimeoff; per-process advisory counter не global
   guarantee. Cache/singleflight учитывает trusted subject/phone fingerprint;
   429/errors не кешируются как empty history.
10. Featureoff по умолчанию. Production enable требует backend issuer и реальный
    mapping/fresh snapshot adapter, shared budget и настроенные secrets. Локальный
    positive HTTP acceptance с injected trusted source доказывает нашу границу,
    но не существование недостающего метода у заказчика.

## Подтверждённые границы исходного кода

- main.py CreateSessionBody принимает raw anketa/utm; SessionStore.create176 и
  state.seed_facts переносят ограниченный prefill. Этот путь не авторизует CRM.
- main.py317–341: existing grant — HMAC('grant:'+sessionid), middleware
  защищает public /api/session/<id>. Новый attach проверяет ownercookie явно,
  поскольку operator middleware ветка иначе обходит её.
- Existing /api/site-consent использует optional caller allowlist. Пустой
  список допускает вызов; этот отдельный прежний контракт не может стать
  authority для нового context binding.
- Fresh CRM RouteServiceProvider70: Limit::perDay(100) без phone key;
  routes/api.php83–84: token auth + throttle. 429 — shared upstream limit.
- CRM config/crm_lookup.php разрешает safe typed dynamic fields и явные strings,
  включая offer/thanks URLs, UTM source/medium, car strings. CrmFieldPolicy
  пропускает новые safe_types кроме hidden; это шире допустимого LLM projection.
- CrmLookupTree поддерживает lead→contacts→deals и lead.deals без контакта.
  Отсутствующие/пустые поля не отдаются. record.bitrix_id числовой identity,
  fields[].bitrix_id — код поля; их нельзя смешивать. Номер с leads=[] находится
  вне покрытия результата, а не обязательно новый человек/новая заявка.

## Проверки после интеграции

- Defaultoff; missing adapter/key/budget fails closed; localpositive HTTP с
  actual session ownercookie и trusted signed binding.
- Wrongowner/session/audience/signature/TTL/futureiat, malformed/nested/oversized
  token; nonce/claim type confusion. До fetch и без чужой state mutation.
- Snapshot app/subject mismatch, stale/future timestamp, browser product injection.
- Same context repeat/concurrency/restart; conflicting rebind и invalid downgrade
  после successful binding, отсутствие повторного lookup.
- Invalid upstream schema/429/timeout/empty; quota reservation across clients,
  no retry и no lead/full-abandoned intake side effects.
- Raw labels/comments/IDs/phone/address не достигают LLM/public response/logs;
  current snapshot остаётся отделённым от old CRM facts.

На этом checkpoint implementation ещё не оценена; контракт не является
подтверждением прохождения перечисленных tests или готовности deployment.

## Первый adversarial code-review

### P1 — общий direct opener следует redirect с Authorization

Найдено в первом customer_context/crm_lookup.py HttpLookupTransport.post
(вызов direct_transport.direct_urlopen), sink transport.py338–343:
build_opener(ProxyHandler({})) сохраняет default HTTPRedirectHandler.
Фиксированный адрес и отключение proxies **не отключают redirects**.
При302/303 urllib меняет POST→GET и переносит Authorization на Location,
включая другой origin. Redirect upstream мог бы раскрыть server CRM bearer.

Критик независимо воспроизвёл через actual transport.direct_urlopen на двух
временных HTTPServer127.0.0.1, с исключительно synthetic-test-only bearer:
origin_posts1, target_gets1, target_bearer_seen=true. Внешних запросов не было,
listeners остановлены. Никакие реальные секреты не использовались/выводились.

Минимальный fix: отдельный lookup opener с ProxyHandler({}) и explicit
NoRedirect handler, redirect_request→None;3xx возвращается как unavailable,
без чтения error body. Не расширять изменение на посторонние transport clients.
Regression: actual lookup transport→локальный302→второйlocal server requests0,
upstream attempts1, safe unavailable. Исполнитель и root уведомлены до запуска
реального transport.

**Исправление независимо подтверждено.** Агент добавил отдельный lookup
opener с NoRedirect, общий transport.py не менял. Критик повторил локальный
тест через **actual HttpLookupTransport + CrmLookupClient**, настоящий typed
AuthorizedApplication после signed capability и shared SQLite budget. Только
исходный fixed Request URL в ephemeral test wrapper заменён на127.0.0.1;
actual opener/handlers и client parsing остались неизменными. Результат:
origin_posts1, target_requests0, target_bearer_seen=false,
status unavailable/upstream_response. Реальный секрет/production не использован.
Первый adapter-test shim переписывал URL также для redirect follow-up и тем
самым мог дать ложный green без NoRedirect. Это выявил независимый mutation
agent; прежняя проверка сама по себе была недостаточна. Критик исправил shim:
только exact initial CRM URL заменяется на local origin, follow-up разрешён
лишь к второму127 server, любой иной URL запрещён.

Повторный test с **отрицательным контролем** через actual client:

| Вариант opener | Origin POST | Redirect target | Synthetic bearer на target | Result |
|---|---:|---:|---|---|
| Исправленный NoRedirect |1|0|Нет|unavailable|
| Mutant с удалённым NoRedirect |1|1|Да|not_found_or_outside_coverage|

Этот test действительно различает защиту и её отсутствие. P1 статус:
**CLOSED**; corrected regression должен остаться в тестах исполнителя.

### Остальные замечания первого прохода

- Snapshot freshness отсутствовала в initial draft. Агент добавил timezone-aware
  observed_at, maxage<=300s, future/stale reject; это timestamp authenticated
  authoritative read, а не возраст создания анкеты. Повторно код просмотрен.
- Bound context identity digest безопаснее raw app/subject IDs в state. Он не
  заменяет atomic CAS между workers. Root выбрал stage1 read-only/local proof,
  featureoff/default, deliveryhold для всех context sessions и явную deployment
  dependency; crossworker production readiness не заявляется.
- Existing diagnostic snapshot делает deepcopy(state) и sanitizes известные
  поля. Новые private context/history keys не маскируются автоматически; raw
  fields/phone/URLs/capability в них нельзя помещать. Root уведомлён.

### P2 — raw current-form address text переживает обычный PII masker

Конкретный путь: CurrentApplicationFields.living_address/registration_address
→ AuthorizedApplication.to_anketa → context_attachment.attach/seed_facts
→ core.build_runtime_payload909 facts → mask_payload_for_llm →
llm_agent._context_block169 JSON facts в system context.

Pure-code adversarial probe использовал исключительно искусственный sentinel
UNTRUSTED_CONTEXT_INSTRUCTION_REVEAL_TOKENS в address fields без «ул/дом».
Обычный masker оставил его в client.registration_address/residence_address/
region, хотя phone замаскировал. Это конкретное нарушение ожидаемой
минимизации и канал model instructions из user free text; способность модели
исполнить инструкцию/утечь реальными секретами не заявляется. Deliveryheld
снижает последствия, но не исправляет data contract.

Минимальный fix для нового сценария: отдельный context-aware LLM projection
по закрытым fact paths и допустимым enum values/bounded numeric values. Raw
contact fields нужны лишь серверу, backend-origin не делает их инструкциями.
Regression должен перехватить actual model payload с таким sentinel; проверки
одного history exclusion недостаточно. Root и acceptance agent уведомлены.
Исправление: context_projection.py оставляет только matrix money/int/bool/enum
после нормализации/enum membership, raw text исключает. Core строит model facts
и turn_instruction из projection, phone остаётся только presence marker.
Diagnostic/public snapshot использует ту же projection, но сохраняет original
Masker для history. Первоначально snapshot затем перезаписывал projected facts
original masked facts; критик поймал это отдельным probe и root исправил.

Финальный повтор pure-code actual paths core.build_runtime_payload,
mask_payload_for_llm(enable=False), mask_session_snapshot: rawaddress sentinel
не входит ни в model payload, ни в diagnostic snapshot; rawphone и binding
digest также отсутствуют. Original server debt400000 сохранён. Никакого live
model вызова не было. Статус P2: **CLOSED по этим путям**, HTTP acceptance и
прямой model-message spy дополняют, а не заменяют данную проверку.

## Независимые инструментальные проверки package

- Исходный package checkpoint:36 tests/test_customer_context.py passed.
- Отдельные4 Python processes, один temp SQLite ledger,80 reserve attempts,
  limit7: granted7. Подтверждена atomicity на одном shared file; разные host
  copies/чужие upstream clients этим не координируются.
- Два actual localHTTP redirect experiences описаны выше: уязвимый общий
  opener передал synthetic bearer, исправленный lookup client target не вызвал.

Нативный lab, production lookup и отправки не трогались. Полный application
integration на этом checkpoint ещё ожидает финального review.

## Реализованная граница stage1: read context без отправки

Root ограничил этап: **каждая** session с ключом customer_context (pending,
ready, error или malformed value) остаётся deliveryheld. Успешное чтение формы
не выдаёт разрешения отправить лид. Legacy sessions без этого ключа сохраняют
прежнее поведение. Это конкретный executable first stage, а не обещание полной
готовности трёх задач Михаила.

CurrentContextService и optional lookup инжектируются composition root, HTTP
issuer отсутствует. Один MBK_CURRENT_CONTEXT_ENABLED не создаёт backend adapter.
Main availability требует feature flag + service + CRM intakeOFF + mock delivery.
Create context_expected использует StrictBool и ставит pending; attach требует
ownercookie даже оператору до lookup/mutation, ограничивает тело8KB и скрывает
validation input. Никакие capability/subject/app IDs не возвращаются в public
state; binding digest остаётся private server metadata.

Coordinator проверяет successful bound digest повторно через verify без
source/lookup и не downgrade после invalid/rebind. Разговор с user turns,
начавшейся delivery/workflow или browser anketa не перетирается:409.
Первый owned resolve persist-ит hold перед backend I/O. Успешные facts происходят
только из normalized current snapshot; история не merge-ится в них. Optional
history exception сохраняет current form и history_status unavailable.

Независимо просмотрены реальные sinks: entry_routing.lead_blocked с presence
context проверяется в workflow.advance, abandoned eligibility и core delivery;
LeadIntakeCoordinator._submit проверяет context **до** mapping/IN_FLIGHT/gate/
HTTP, включая прямой full submit; site-consent endpoint409 до confirm.

Session turn_lock обеспечивает лишь concurrency в одном process. Existing PG
ordinary save защищает crm_lead subtree, но не atomic immutable context binding.
Cross-worker/bluegreen contextCAS не реализован и не заявляется. Это явная
deployment dependency вместе с trusted backend ownership/fresh snapshot API;
read-only локальный proof допустим, включение production capability/отправок
без этих условий — нет.

## Проверки application integration

Критик самостоятельно выполнил текущие четыре набора:
test_context_attachment_http, test_customer_context, test_site_context_acceptance,
test_site_context_llm_acceptance —101 passed. После добавления cancellation и
двух overwrite guards последние3 HTTP tests отдельно дали3 passed.

Actual FastAPI/session path проверяет signed valid attach и ownercookie,
отказ чужому owner/оператору без cookie, конфликт/expired/invalid/oversized
envelope, недоступный adapter/feature/real delivery, timeout, optional history
failure, directfull/abandoned holds, pending SSE и persisted binding roundtrip.
Повтор не делает source/lookup I/O; invalid token не downgrade existingready.

Actual conversation/extraction model-message spies с выключенным global PIIflag
подтвердили отсутствие raw name/phone/address/subject/app/digest при сохранённом
полезном debt400000. Cancellation test реально отменяет coroutine после начала
executor fetch и проверяет lock до завершения worker. Код shield/deferredrelease
просмотрен независимо; начавшийся user dialogue/browser form не перетираются.

Дополнительные deployment limits: lazy TTL cache не означает физическое
удаление idle memory ровно в deadline; настоящий source adapter обязан иметь
свой bounded I/O timeout (generic Python callable не может быть безопасно убит).
Они не объявляются реализованным production orchestration.

## Финальная сверка mutation corpus и quality gate

Критик прочитал runner,27 anchors и фактически упавшие test names. Runner
требует зелёный baseline, ровно1source anchor и для killed — pytest exit1,
непустые test-body failures, errors0. Collection/import failures с exit2 и
fixture errors не считаются killed. XML failure type runner не классифицирует:
общую гарантию «любой killed обязательно AssertionError» давать нельзя.
В текущих27 случаях anchors и failure semantics соответствуют ожидаемым
проверкам прав/срока/исходов/счётчиков/privacy; признаков false killed не найдено.

Final artifact guard-mutations-final.json:51 baseline tests, exit0/errors0;
имена27 mutants точно совпадают с текущим MUTATIONS, у всех exit1 + failures
и errors0. Независимо пересчитаны99 live copied file SHA256: mismatch0.
Два дополнительных manifest entries — generated sitecustomize/conftest, не
файлы checkout. Копируются explicit Python/package data, без .env/logs/DB;
subprocess network guard разрешает только собственные fixture loopback ports.
Исходный20/21 negative run сохранён; он не подменяет финальные27/27.

Последний independent targeted run:
test_context_attachment_http + customer_context + site_context_acceptance +
site_context_llm_acceptance + site_context_submission_acceptance —
**116 passed,4.91s**. Полные2620 tests повторно не запускались критиком:
их зелёный run завершил root после исправления обнаруженного missing runtime
COPY в Dockerfile. Образ здесь не пересобирался и production не проверялся.
