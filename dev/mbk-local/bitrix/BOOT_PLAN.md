# Настоящий локальный Битрикс: выполненный bootstrap и границы

Текущее состояние и команды — [STATUS.md](STATUS.md). Архив официальной trial
закреплён SHA256 e379b86869d4904d919ba1e2daffbaac8be3e15995a67f8b87ada85c7b8692d0.
Customer Laravel CRM и партнёрка используют исходники своего Git snapshot;
ядро trial main26.150.0/CRM26.100.200 имеет отдельное происхождение.

## Критерий настоящего результата

Выполнены original CCrmLead::Add, native ORM/UserFieldManager/FM/address/UTM read
лида1 с ORIGIN_ID реального website fixture. ID не выдаётся SQLite simulator,
класс не заменялся, прямого INSERT лида нет. Отдельный native_api_harness
не считается original customer request.php или полноценным Bitrix REST OAuth.

Этот же native ID подтверждён исходной site job. Передача партнёру выполняется
исходной SendLeadMicroservice job и отдельно сверяется с native partners/receiver
артефактами. После первого post-write сбоя использовано явное восстановление;
новое создание или ложный created=true на повторе запрещены.

## Штатная установка отдельного scope

1. Публичный официальный portal demo архив скачивается без registration/PII/key.
   Hash проверяется до распаковки. Не смешивать его CRM core с Git core.
2. Отдельные новые schema/user/volume. Extraction — networknone/nonroot,
   readonly archive, tar datafilter, запрет traversal/links/devices и лимиты.
3. Только во время native wizard свой webroot RW через official-install overlay.
   HTTP-клиент сохраняет cookies/forms локально; не исполняет JS/redirects.
   Registration checkbox off, внешние updates — штатный skip.
4. Existing generated scoped DB user/database, root credentials не передаются.
   Administrator/email синтетические, сохраняются только локально.
5. После main+90modules+admin design/demo-content wizard не требуется.
   Исходники RO, runtime caches/upload/session на отдельных tmpfs.
6. Native UF создаются настоящими CUserTypeEntity/CUserFieldEnum. Native статус
   RESTORED создаётся CCrmStatus с корректным SORT15. Реальные enumIDs
   сопоставляются legacy3895/23000/9257 явно; sequence не подгоняется.

## Ограничения исполнения

- Internal Docker network/no published ports, no proxies/host-gateway/socket,
  nonroot1000, rootfsRO, capsALLdrop, SMTP /bin/false.
- Cron/общие queue workers/CAgent scheduler не запускаются. Только конкретные
  новые synthetic jobs с identity/queue guard и ограниченным выполнением.
  Старые pending/failed/Direct queues не потребляются.
- Main options check_agents/check_events=N; NO_AGENT_CHECK/NO_KEEP_STATISTIC
  определены до original prolog. Нет вызова Application::end/terminate/epilog.
  Source-backed finite offline security freshness keys не считаются updates.
- Stock agents/handlers сохраняются. Перед/после фиксируются LAST_EXEC,
  mail/workflow states; новые native pending agents не удаляются ради нулей.
- Factory path действительно enabled. Исходный compatibility adapter
  setRunAutomation(false)/setRunBizProc(false) применяется к Add и Update.
  Relevant UF/lead hooks проверены; customer init/workflow/robots не импортируются.

## Контракт адаптера

Basic auth с generated local credentials до bootstrap. Add route соответствует
реальному Bitrix24Api::METHOD_REQUEST, значение хранится только в local env.
REST-like get/update endpoints — /__lab/rest/crm.lead.get и crm.lead.update.
JSON ограничен64KB; только отмеченный synthetic native400k/Moscow fixture.

Учитывается фактический wire: строковые enumIDs/RETURN_RESPONSE, d.m.Y birthday,
UTMSOURCE, nullable monthly. Неизвестный платёж остаётся NULL, не0.
Moscow/debt/SUMM/PHONE_DEAL проверяются по входу, не подставляются для прохождения.

Idempotency: native ORIGINATOR_ID+ORIGIN_ID и сохранённый native UF payload hash,
original connection advisorylock. После Add и на повторе одинаково проверяются
immutable UF, date/currency/amount, WORK phone и native address/UTM. Native lock
хэширует имя до64символов. Повтор возвращает прежний ID с created=false.
Shipment UF могут меняться после настоящей передачи и не считаются конфликтом.

## Доказательства и пределы

Сохраняются archive hashes, Reflection source provenance восьми core classes,
actual container/image/env/config hashes, native API/read data и изменившиеся
queue/automation states. Secret values/phones/rawpayload не публикуются.

HTTP200/доставка локальному receiver не доказывают принятие клиента компанией,
договор или оплату. Этот trial/fixture не подтверждает runtime production rules,
customer license, весь Bitrix UI или автоматическую production end-to-end работу.

Git-only373DDL/bootstrap с activation redirect сохранён отдельно как история;
его лицензионные проверки/дату/ключ не меняли. Существующий protocol-mode также
сохранён, genuine proof с ним не смешивается.
