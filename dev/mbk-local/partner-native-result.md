# Native local delivery — 06.11.2026

**Passed with explicit post-Add recovery.** One synthetic website request created a true Bitrix lead using the officially installed native runtime; original CRM and partner jobs then delivered it over actual local HTTP to a mock buyer. No production requests or real-company delivery occurred. This is not a claim of clean automatic E2E or production parity.

| Identity / outcome | Observed |
|---|---|
| Reserved operation | `local-write-native20261106v1` |
| Website lead / genuine Bitrix lead / native partner-local lead |7 /1 /1 |
| Native origin / debt / region | reserved operation /400000 /Москва |
| Fixture partner / campaign / queue |991001 /950 /1 |
| Original CRM job / original native LeadSend |1 /1; each processing1, processed1, failed0 |
| Native sent / delivery history / campaign usage |1 /1 /1; sent HTTP200; queueDelivered2 |
| Artificial account |100 topup −10 native LEAD_CHARGE =90 |
| Terminal receipt | `local-e103fe7c3ef641c1b57d63e77e3f1360` |
| Receipt request digest | `3e33bc7c6bcf8ae01cf8ace8d7f486b412db5a1b1eda7ea82317848092ee553c` |
| Native shipped fields |true, timestamp present, native enum15/XML `microservice_bankruptcy` |
| Final CRM / partners jobs and failed rows |0 /0 in both new scopes |

## What actually ran

Original website session/CSRF and ProcessSendLeadBfl → original CRM LeadController → explicit local native adapter → actual CCrmLead::Add/Get. The adapter converts documented legacy enum IDs to freshly created native UF enum IDs and preserves nullable monthly/other debt. Website7 was linked to actual ID1 by replay created=false after the local postwrite UTM proof was repaired. No second Add, invented Bitrix ID or modified created response was used.

Because source CRM returns early on created=false, the native sender was recovered explicitly once through real Bus Dispatcher and original `SendLeadMicroservice(1, microservice_bankruptcy)`. Durable host marker, independent website ACK/native Get, exact origin and live empty queues/native-partner/buyer guards preceded dispatch and buyer arming. The actual sender POSTed into original native partner controller/selection and updated native shipped UFs through real CCrmLead::Update. Original native LeadSend posted to the local mock URL from fixture partner_settings; native code persisted sent/history/charge/usage.

Both named workers were original Laravel WorkCommand/Worker with original events/failed-job provider, --once/tries1/timeout75/outer95s. Full artisan discovery was skipped after unrelated Sheets command construction failed before reservation/attempt0. Business/vendor source was read-only; job.handle was never called directly. Framework event evidence and persisted outcomes, rather than CLI DONE/exit alone, establish delivery.

## Evidence index

Paths are relative to repository root. Run inputs were not rewritten after execution.

- `logs/local-stack-2026-11-06/website-native-v1/site-ack-observe.json`: website7→native1, original queues preserved, historical failures retained.
- `logs/local-stack-2026-11-06/genuine-crm/website-id1-auth-proof.json`: actual Laravel Http/BitrixRest/native ORM same identity and auth.
- `logs/local-stack-2026-11-06/genuine-crm/manual-post-add-recovery-id1.json`: exclusive marker and one explicit source-job recovery; automatic_original_handoff=false.
- `logs/local-stack-2026-11-06/genuine-crm/native-framework-worker.json` and `sender-observe.json`: original CRM worker events and strict empty pending/failed outcome.
- `logs/local-stack-2026-11-06/genuine-crm/post-delivery-native-uf-read.json`: actual read-only native Get/ORM, shipped fields, dynamic stream enum and empty CRM queue proof.
- `dev/mbk-local/partner-native-prepared/native20261106v1/manifest.json`, `schema-hash-provenance.json`, `post-import-schema-checks.json`, `import-result.json`: reviewed native structural provenance and prelead empty business tables.
- Same run directory: `pre-lead-runtime-verification.json`, `buyer-arm-proof.json`, `delivery-job-isolated.json`, `native-framework-worker.json`, `delivery-observe.json`, `post-delivery-native-app-snapshot.json`, `buyer-receipts.json` establish isolation, genuine-ID handoff, real selection/job/delivery/accounting and same receipt.
- `logs/local-stack-2026-11-06/root-native-partner-snapshot.json`: root's independent read-only native SQL terminal cross-check.

## Boundaries and remaining work

- Native creation/read/update evidence is `genuine_bitrix_runtime`; application/partner code is native, data/settings are `synthetic_fixture`/`user_fixture`; buyer is `local_mock_buyer`. The local native API adapter uses generated Basic and narrow native APIs, not Bitrix REST OAuth or the full legacy production receiver.
- First pre-Add nullable validation and post-Add UTM-proof failures are preserved. Original site failed rows4/5 and historical error text were not cleared; successful replay is verified separately. Automatic sender recovery after a lost postwrite response remains a product reliability issue, not solved by this test.
- Single active synthetic campaign: hands1/delay0/frozen1day/duplicate2days/price10/daily_limit2; these are test choices. The additive `leads.double_count` column is explicitly inferred from native code, not verified production DDL; no unknown phone_uniq/race behavior is claimed.
- Null other debt stays null in native/partner payload; only source tagger casts it to numeric0. Source native distribution has an age prerequisite even though CRM BFL qualification rules do not check age; the positive fixture is explicitly adult.
- Internal network/no host ports, RO source/vendor, no outbound buyer forwarding, idle services and stopped one-shot workers protect the boundary. Old protocol schema/queues/counter rows were not consumed or cleaned up.
- HTTP200 at the local mock is transport acknowledgment only. Real-company acceptance, contracts, production settings/eligibility, privacy/consent completeness, frozen/duplicate/concurrency/timeout retries and clean automatic website→sender recovery are not established.
