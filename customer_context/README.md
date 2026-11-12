# Server-authorized current application context

This package implements the isolated backend boundary for stage1. It does not by
itself integrate the HTTP session route, delivery policy, frontend or production
MBK context provider. `main.py` owns those integrations and their HTTP tests.

## Trust and flow

1. An authenticated MBK server establishes the caller's right to an application
   and subject. Only that server may call `ContextCapabilityCodec.issue` with a
   separately provisioned random key (at least32 bytes). There is no public
   issuance route and no browser phone/formID/URL→grant operation.
2. Capability claims bind audience, session, application, subject and immutable
   source revision, with a maximum lifetime300s. The token contains no phone or
   form answers. `CurrentContextService.resolve` checks it before source access.
3. `ApplicationSource.fetch` must independently read the current authoritative
   snapshot through an authenticated backend connection. Its `observed_at` is a
   timezone-aware timestamp of that verified read, not the old form creation
   time. Snapshots must be active, match all reference fields, not be future-dated
   and be strictly younger than300s. A delayed source cannot redeem an expired
   capability; the handle also expires at the source freshness deadline, so a
   newly issued capability cannot extend an already old snapshot.
4. The resulting `AuthorizedApplication` is an internal typed handle, not a
   browser request model. `to_anketa()` returns the allowlisted **current** form
   and verified contact. It is not an LLM-safe projection: free text/contact
   still requires existing fact normalization and a separate closed projection.
5. `CrmLookupClient.lookup(handle)` may read history for its verified phone.
   History remains in `LookupResult.history`, never merged into current facts.
   Browser/public metadata contains only `context_status/source/history_status`.

Persist `handle.safe_context_state()` if needed: status, source and a
session-bound HMAC binding digest. Do not persist capability, raw reference,
snapshot or CRM history. `service.binding_digest(token, session_id=...)` verifies
a repeated capability without source access; root integration can use it for
idempotence. `resolve(existing_binding=digest)` rejects context swaps before
source or CRM calls. A valid attachment must not be downgraded by a later bad
token; root session integration owns this transition.

## Missing backend dependency

`ServerApplicationAdapter` is a typed bridge to an injected, already-authenticated
resolver. It does not magically authorize a browser dict, and it invents no
production formID→phone endpoint. Before deployment MBK must provide actual
ownership/subject resolution, fresh snapshot/revision semantics and trusted
capability issuance bound to the chat session. Until then source and lookup
remain disabled by default. These dependencies are not supplied by crm-lookup.

## CRM lookup contract

The only production transport target is confirmed `POST /lara/api/crm-lookup` on
the CRM server, JSON `{phone: string}`, backend-only bearer supplied by the
server configuration `MBK_CRM_LOOKUP_TOKEN`. Phones canonicalize Russian7/8
prefixes to8XXXXXXXXXX. No phone discovery or lead creation method exists here.

Transport uses its own no-proxy **no-redirect** opener: default urllib redirect
handling could otherwise forward Authorization to a different host. Requests
have one attempt, timeout≤10s and response bound≤1MiB; defaults3s/256KiB. No
automatic retries for429,5xx or timeout; error bodies/exceptions are not logged or
returned. The independent redirect regression used two localhost servers and
synthetic-only credentials; production was never called during implementation.

| Outcome | Meaning |
|---|---|
| found | At least one accessible lead; fields retain their codes and JSON types |
| not_found_or_outside_coverage | Empty accessible result, never “new customer” |
| unauthorized | Upstream401/403, not a user-identity decision |
| rate_limited | Upstream429 or our exhausted request ledger |
| unavailable | Network/error/invalid/oversized response or unavailable budget |
| disabled | Backend feature is disabled |

The tree preserves direct lead deals and contact deals, record integer IDs,
field string codes, false/zero/list/money values and unknown field codes. Missing
fields remain absent. Strict validation rejects malformed trees/nonfinite values.
No entire raw tree, field label or history free text belongs in the LLM prompt.

## Budget and caching

`SqliteLookupBudget(path, limit=100)` uses `BEGIN IMMEDIATE` before count+insert
for at most100 reservations in a sliding24h window. All bot workers/replicas
must share this same persistent ledger. Independent local files do not
coordinate replicas. No reservation is refunded after timeout/5xx; a failed
ledger closes lookup without HTTP. The ledger stores timestamps only.

This is the bot's own budget; other upstream clients can still consume the
shared100/day quota. Upstream429 is therefore always respected without retry.
Successful history/empty-coverage responses have a short in-process cache keyed
by authorized subject/application/revision and keyed phone fingerprint, with
single-flight inside one worker. Errors are not cached as “no history”. Cross-
worker duplicate reads can consume reservations, but cannot bypass the shared
ledger. Cache freshness is bounded by capability expiry, snapshot freshness
deadline and a maximum300s TTL.

## Verification boundary

`tests/test_customer_context.py` covers signing/session binding, immutable digest,
source identity/active/freshness, prefetch rejection, expiry, typed tree parsing,
no raw-number access, separate public projection, response bounds, disabled/error
states, no retry and atomic shared-ledger accounting. Separate independent
acceptance/mutation and actual HTTP/session tests are required for integrated
claims. None of these checks exercises production API or changes native-lab data.
