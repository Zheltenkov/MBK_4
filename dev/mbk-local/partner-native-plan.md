# Native Bitrix → native partners → local buyer

06.11.2026. **Native-partner perimeter started; awaiting a genuine Bitrix ID.** Root and independent critic approved the new empty-schema provision/import, own-volume initialization, standalone startup and GET/isolation checks. Those steps passed. No lead POST, buyer arming or worker execution has occurred in this scope. This is not a completed Bitrix→buyer delivery.

## Boundaries and evidence

The required result is a genuine locally created Bitrix lead, read by the unmodified CRM sender, accepted by the unmodified partners controller, selected by its native campaign engine and delivered by its native LeadSend job to a **local mock buyer**. The mock supplies a transport acknowledgment, not a real company/client acceptance. No simulated receiver ID qualifies.

- `crm-genuine` uses the real local Bitrix database. The existing reader/simulator CRM remains untouched.
- `partners-native` uses the new `partners_genuine_v1` schema and scoped user `mbk_partner_native_v1`. Existing counter fixtures and manufactured sent rows stay in the old partners schema and are excluded from evidence.
- No leads, sent, distribution queue, history or daily-usage rows are seeded. Those must appear through the native HTTP/job flow.
- No customer source changes. Local env, DDL fixtures and thin native-API adapters are allowed, labeled accurately.
- All services share only the isolated internal lab network; no published buyer port, no production URLs/credentials, no SMTP delivery. Preserve and account for old site/default/Direct jobs.

## Source-backed route

1. Create an artificial lead using real `CCrmLead::Add`; independently read its ID/UF/address/phone through native APIs and database. Record its genuine Bitrix ID separately from the future partners-local lead ID.
2. Real CRM `MbkFormPartners` calls `GET /api/day-limit-distribution/bankruptcy` for BFL capacity. `SendLeadMicroservice` with partner code `microservice_bankruptcy` reads the actual lead and performs `POST /api/lead-distribution/bankruptcy`. Its source is `lara/app/Jobs/SendLeadMicroservice.php:58–118`, payload builder `Services/PartnersMicroservice.php:58–104`.
3. Native `LeadDistributionController` validates the input, saves partners `leads`, tags the lead and invokes `LeadDistributor`; expected result is `status=queued` with queue/campaign/partner IDs. This is admission to dispatch, not final delivery.
4. Native distributor writes `distribution_queue`, `distribution_queue_tags`, `campaign_daily_usage` and dispatches native `App\Jobs\LeadSend`. No handmade sent rows and no replacement sender.
5. One bounded partners worker executes only the accounted new LeadSend job. It POSTs to **`partner_settings.callback_url`**, a local buyer service. This is the actual transmitter: `www/app/Jobs/LeadSend.php:129–170`.
6. Buyer returns HTTP200 with a local receipt. Native LeadSend saves `sent` and native DeliveryResultHandler changes queue status to delivered, writes history/charge and decrements the artificial partner balance.
7. CRM's queued acknowledgment updates `UF_SHIPPED_PARTNER`, `UF_SHIPPED_DATETIME`, `UF_PARTNER_NAME` via `BitrixRest::crm.lead.update`. A local narrow adapter may invoke real `CCrmLead::Update` and native UF read; label it **native API adapter, not Bitrix REST OAuth**. Do not route this through the old protocol simulator.

## Genuine lead fixture

All input is artificial, marked with a unique run ID. Suggested positive fixture:

- debt `UF_DEBT_SUMM=400000`; the actual website does **not** supply UF_TOTAL_DEBT_OTHER. Preserve absence/native null; do not inject zero. The native sender passes null and LeadTagger's integer cast contributes0 to debt tags. Monthly payment is0/nullable according to the actual form wire;
- BFL Moscow category and YES consent registered through genuine UF APIs; for the **first direct sender-job path** use returned local enum IDs and record their XML/value mapping. Fixed3895/23000 belong to the legacy customer's rules, not a requirement to copy those IDs into the new native instance;
- registration Moscow, actual primary `ADDRESS_PROVINCE=Москва`; `IS_BFL_PRODUCT=true` is a legacy form/control parameter, not a new UF to register;
- explicit synthetic adult birthdate `1990-01-01`, history9257, no real-estate/car ownership assertion;
- synthetic phone registered in native phone multi-fields with TYPE_ID=PHONE/VALUE_TYPE=WORK; title contains run ID.

**Important mismatch:** classification BFL rules described by Andrey do not check age, but the existing legacy `MbkFormPartners::checkAge():337–339` rejects Moscow BFL if age is absent or <20. The adult birthdate is a local positive-test prerequisite, not a new real-client question/policy. Registration/classification and native primary address are separate: sender reads `lead.address.PROVINCE`, not the registration string.

CRM must have genuine UF metadata and values required by its Eloquent model/sender, including product/debt/address/phone/partner markers. `UserFieldsHelper` resolves `UF_PARTNER_NAME` by field name and enum XML_ID `microservice_bankruptcy`; native local enum IDs need not be production IDs. Fresh phone/lead avoids the separate 14-day historical BFL→SodaTech path. If legacy prechecks run, local settings MIN_AGE/MAX_AGE/MIN_OPPORTUNITY/MIN_OPPORTUNITY_SECOND must exist with explicit fixture values; do not infer production values from the test.

Minimum for the first **direct native SendLeadMicroservice job**: genuine UFs `UF_CRM_1452152621` string (phone WHERE in the14-day checker), `UF_SHIPPED_DATETIME` datetime (also queried), `UF_SHIPPED_PARTNER` boolean, `UF_PARTNER_NAME` enumeration/MULTIPLE=Y with enum XML_ID `microservice_bankruptcy`, `UF_DEBT_SUMM` double400000 and numeric metadata for nullable `UF_TOTAL_DEBT_OTHER`. Explicit semantic/payload fields recommended: product `UF_CRM_1452154548` enumeration/valueLocalBFL/XML_IDlocal_bfl; `UF_SITE_AGREE_PARTNERS` enumeration/LocalYES; category `UF_CRM_1491296976` enumeration/LocalMoscowBFL; registration `UF_CRM_1452153725` and actual residence `UF_CRM_1452153749` strings; numeric `UF_GENERAL_MONTHLY_PAYMENTS`. Create through CUserTypeEntity/CUserFieldEnum and pass their returned IDs into CCrmLead::Add; do not hand-insert UF rows/lead storage. Unanswered numeric fields stay null; the positive website flow is not a request to fabricate them.

This first direct job does not execute MbkFormPartners/classification's fixed category/consent/age routing. Category is not checked by the sender; its consent read in getUtmTerm is attribution, not a consent decision. Native API creation+sender+partners evidence is sufficient for this first path but does not demonstrate the legacy consent/classifier contract. Later legacy intake must solve those enum mappings and prechecks explicitly; no business source patch is proposed.

## Offline schema preparation

Run only the preparer, which has **no database/network client**:

```powershell
.venv313/Scripts/python dev/mbk-local/partner-native-prepare.py --run-id <run-id> --day 2026-11-06
```

It writes `partner-native-prepared/<run-id>/schema.sql`, `fixture.sql`, `manifest.json`. A prepared run directory is immutable: a repeated run ID fails rather than overwriting evidence. The per-run review artifact uses run ID `review20261106`; prepare a **new** run ID for an actual run after review. Earlier common-directory review outputs are not actual-run evidence and are not runtime inputs. The buyer service is fixed to an internal local hostname and receives no credentials.

Schema is 37 tables:17 native CREATE statements from the old test dump, native BFL/tag/campaign/financial DDL, working-days/adjacency/send-plan DDL and Laravel's jobs structure. Only CREATE definitions and reviewed additive ALTER statements are extracted, not INSERT rows/migration history/customer campaigns/passwords. Snapshot AUTO_INCREMENT table counters are removed. Foreign-key parents are ordered before children; checks are not disabled. Source hashes and output hashes are recorded.

Source migrations used for structure:2025_04_12 working_days,2025_04_27 jobs,2025_10_20 sent step columns,2026_07_17 BFL distribution,2026_08_19 adjacencies,2026_09_02 campaign label,2026_09_14 send-plan/logo,2026_09_25 partner campaign name. Tag dictionary values are supplied as local fixtures; the time dictionary uses the same native constants as2026_08_17 time_tags.

**Explicit schema inference accepted by root:** the old test dump has no `leads.double_count`; fresh native validator/controller requires999 for an active row, but no saved migration defines the column. The preparer adds `double_count INT NOT NULL DEFAULT999` with **source=inferred_from_native_code + user_fixture**. It invents no unknown `phone_uniq` index. The existing native UNIQUE lead_id is retained. This supports the single positive native flow; database phone-race/parity claims are excluded. If a current canonical leads DDL becomes available, review it before replacing this inference.

Applying SQL is a separate runtime step: assert the target database/schema is newly created and empty, inspect full DDL/types/FKs, import schema, inspect resulting schema, then import fixture data. A collision/unsupported DDL stops the run; no DROP/cleanup/ALTER-on-existing customer schema workaround. Do not run broad artisan migrate after this prepared schema, because migration-history rows were deliberately not manufactured.

## Explicit user_fixture values

| Item | Local value |
|---|---|
| Program namespace | native IDs3/5; program tag950/type1/valueБФЛ |
| Distribution settings | hands_count1; delay_minutes0; frozen_days1; duplicate_days2 |
| Buyer/channel | partner991001; settings110; active native BFL program3; callback_url internal local buyer; emailNULL |
| Campaign |950, active status2, priority1, price10, daily_limit2, label `local-native-<run-id>` |
| Target tags | program950 + geo92 + debt94 |
| Artificial money | opening balance100 and native manual-topup ledger100; successful charge10 → balance90 |
| Calendar | requested explicit day is_work=1; campaign has no day/time targeting for the initial positive case |

Every value is a test choice, not an exported production setting. There is exactly one active partner/campaign and no adjacencies. Objective tags still record day, possible daytime tag100, Moscow3→geo92 and debt94; absence of day/time campaign targets keeps the initial test independent of runtime hour. Later day/time/freeze/duplicate tests are separate scope.

## Worker/network preflight

- Assert internal networks for every added service, effective local receiver/REST/partner/buyer bases and no external aliases/host ports. Do not print credential-bearing endpoint values.
- Assert `APP_DEBUG=true` in crm-genuine: source `SendLeadMicroservice.php:99–111` and `PartnersMicroservice.php:209–217` gate GA/Yandex follow-ups. This is a local operational setting, not production parity.
- Confirm database queues. Queue names may be hardcoded default: an env DB_QUEUE claim is insufficient. Account and guard-relocate only the new CRM sender job and later the new partners LeadSend job into separate named queues; never run a broad default worker.
- Record baseline old queues/failed jobs. Preserve the two previous Direct jobs and all reader/protocol/site artifacts. No native flow uses old manufactured counter sent rows.
- Buyer records only run/campaign label, genuine leadId, request hash/length, receipt ID and timestamp, without raw phone/name or full payload logging. HTTP200 is only a local transport acknowledgment.

## Assertions for acceptance

Before input: no leads/sent/distribution_queue/delivery_history/daily_usage/jobs in the new partners schema; one active fixture campaign/channel; artificial balance and opening-ledger100.

After real partner POST, before buyer worker: exactly one native partners lead whose `lead_id` equals the genuine Bitrix ID; data debt400000 and actual regionMoscow; raw totalDebtOther remains null when omitted by the website, with numeric contribution0 only in the tagger; tags include950/3/92/94 and expected day tag; exactly one queue row campaign950/partner991001/order1/status1; one native LeadSend job; native daily_usage1; no sent/history/charge yet. A `queued` HTTP result alone does not satisfy delivery.

After the one native LeadSend: buyer capture contains the same genuine `leadId` and native campaign_label; exactly one native sent row with matching partners-local lead ID and queue ID, captured HTTP200 receipt; queue status2; one delivery_history and one LEAD_CHARGE type4 amount10; balance90. Follow-up jobs are accounted, not consumed blindly. Native Bitrix UF shipped markers read back through real API belong to the same genuine lead, and only mean admitted-to-dispatch.

Final manifest distinguishes `genuine_bitrix_runtime` for native creation/read/update, native partner HTTP/selection/worker/database evidence, and `local_mock_buyer` for the terminal receiver. No real-company acceptance, contract, production eligibility or throughput result is claimed. This plan is preparation, not evidence these assertions passed.

## Preparation checkpoint (before genuine input)

- Actual run `native20261106v1`: shared-MySQL system DDL was serialized with local_stack; only the new schema/user and its schema-scoped grant were created.37 tables and explicit fixtures imported without customer rows or business evidence rows.
- `native-partners.compose.yaml` started only its two new services; source/vendor mounts are read-only, processes nonroot/read-only-root, no published ports. Existing app services were not recreated.
- GET native `/api/day-limit-distribution/bankruptcy` returned200/more=true. Buyer GET health returned200/armed=false/receipt_count0. Buyer was not sent any lead.
- A fresh positive host TCP control and probes inside both new containers confirmed internal capture reachability plus blocked public/host egress. Docker network is Internal=true.
- Before genuine input, leads/sent/distribution_queue/delivery_history/campaign_daily_usage/jobs are all0. No workers started. Receipt volume belongs only to the terminal local mock and remains unarmed.
- Evidence: `partner-native-prepared/native20261106v1/import-result.json`, `pre-lead-runtime-verification.json`, `schema-hash-provenance.json`. The last file records exact disk hashes and the LF-normalized hashes used by the initial Windows preparation; prepared inputs were not rewritten. New preparer outputs use exact LF bytes.

## Executed result — 06.11.2026

The genuine website operation is now completed: website lead7 → actual native Bitrix lead1 → native CRM sender → native partners local lead1 → campaign950 → original LeadSend → one local mock buyer receipt. Native sent/queue/history/usage/charge rows were created by original runtime code, not seeded. Final queue status2, sentHTTP200, history1, usage1, charge10, balance90; CRM and native partners pending/failed jobs0. Native Get read back shipped=true, a shipped timestamp and actual local enum15/XML `microservice_bankruptcy` for the same ID1.

The handoff required **explicit manual post-Add recovery**, after local adapter errors were diagnosed and preserved: first nullable-monthly validation failed before Add; the next attempt persisted the true native ID but its UTM readback failed; a corrected readback and `created=false` replay linked website7 to ID1. Original CRM skips postprocessing on replay. Exactly one original `SendLeadMicroservice` was dispatched manually with identity/empty-queue/receipt guards and a durable exclusive marker. No new Add or altered created flag was used. Therefore this is genuine creation/delivery evidence with a documented recovery, **not clean automatic end-to-end success**.

Full artisan command discovery hit an unrelated Google-Sheets command constructor before job reservation. The approved harness executed original framework WorkCommand/Worker with original queue events and failed-job provider, one named job per worker, tries1/timeout75 and outer95s. It did not call job.handle directly or patch customer/vendor code. Both event sets were processing1/processed1/failed0. No remaining worker is running.

Read the complete evidence index and limits in `partner-native-result.md`; immutable run artifacts are in `partner-native-prepared/native20261106v1/`. The terminal receiver is only a local mock transport acknowledgment. Production eligibility/settings, real company acceptance and phone-race/freeze/retry parity remain outside this result.
