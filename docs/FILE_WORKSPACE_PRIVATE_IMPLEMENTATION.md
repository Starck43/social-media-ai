# P12-T12 — Default-off durable private-insight implementation

[Task](tasks/p12-t12.md) · [PR #327](https://github.com/Starck43/social-media-ai/pull/327) ·
[Admission contract](FILE_WORKSPACE_ADMISSION.md) · [Lifecycle](FILE_WORKSPACE_PRIVATE_STORAGE.md).

## Delivered code, not activation

Migration 0128 adds owner-bound consent/control state, per-object fences, bounded
read-attempt metadata and encrypted compact results. No defaults grant consent;
there is no backfill, public route, automatic provider/model, GC scheduler or
runtime activation. `file_insights` is constructed disabled. Only trusted app
composition can explicitly construct `FileInsightManager(enabled=True, cipher=...)`.
This flag is not end-user consent or a production enablement approval.

The default-off implementation is subject to local PostgreSQL/migration/concurrency
validation before activation; remote fake sessions do not certify transactions.
No real exact-version adapter is registered for Yandex. Without an injected
reviewed version authority the workflow cannot save. No hash/mtime fallback.

## Internal flow

1. `set_consent`: current personal connection owner plus source-create/view grants;
   exact separate processing/retention booleans and the code-owned policy revision.
   Retention without processing is invalid. Every explicit choice rotates epochs,
   purges previous attempts/results and issues a new consent revision. Withdrawal
   works without a live connection or secret. A future UI must separately collect
   informed choices and enforce CSRF; there is no UI in this package.
2. `begin_read`: current active user/tenant/member/role, personal owner, connection,
   secret presence, saved LIST+READ/root and both consents. Allocate a server nonce
   and original database-clock capture time BEFORE provider work. Preserve only
   context hash, path identity, control epochs/revisions, not raw path or text.
3. `PrivateInsightWorkflow.read`: existing bounded read orchestration uses
   `OwnedContentAuthority` to reload the manager-owned attempt/current context
   before/after streaming. No transaction spans provider I/O. `complete_read`
   validates canonical extraction/same-read context and strict DTO bounds, binds
   hashes/parser and app-adapter version assertions once, and returns a receipt
   from the ORIGINAL captured time. It cannot redate old text or an absent ledger.
4. Generate candidates through the existing synthetic service; no real model in
   this package. `PrivateInsightWorkflow.save` runs the previous fresh authority /
   version / authority preparation and passes the checked assertion to the manager.
5. Final manager transaction rechecks rights/consents/context/epochs/age, matches
   the saved receipt and a <=5-second-old exact-version assertion, rebuilds the
   explicit canonical DTO using final database time, checks aggregate quotas and
   inserts encrypted result + consumes the read attempt atomically.

Receipt completion and version verification are trusted INTERNAL adapter boundaries.
They are not public data-to-storage endpoints. An app-supplied fake reader/version
implementation can lie; a dataclass or matching hash is not provider authentication.
No independent model-provenance or semantic entailment guarantee is invented.

## Transactions, quota and retries

All manager operations acquire PostgreSQL two-key tenant advisory transaction lock
(namespace 12120028) BEFORE existing shared authority locks, then the connection,
mount, catalog object, object fence and attempt locks. Own writers cannot both
spend the same tenant/owner/connection/object last slot. Usage is derived from rows,
including expired rows pending GC: no drift-prone counters or leaked reservations.
No network/model I/O occurs while these locks are held. Unique attempt-result
constraint is the final idempotency barrier. Encryption failure rolls back content.

A retry must use the same read/generation identity, still-current receipt/rights,
fresh version and unexpired capture window. It returns identical original bytes,
result ID and expiry without another insert/charge. Different data under that
identity is rejected. After deletion or GC, missing/consumed attempts never create
new content. Expiry is capture +24h, never refreshed by save/get/retry. Save/retry
capture age is <=5min; historical `get` remains possible until fixed expiry.

Expected logical denials roll back content work in a savepoint while committing
the mount's advanced DB-clock high-water. Rollback and expiry deny release. DB/
commit failures and cancellation return no success; a commit interrupted at the
client can have completed server-side. Retry the SAME attempt to resolve it,
never blindly allocate a new attempt. Uncommitted observations cannot survive a
DB crash; operational clock, restore and consistency validation remain gates.

Outstanding read ledgers have hard object/connection/owner/tenant limits
16/100/200/1000, including abandoned entries until purge. These are separate from
payload quotas. `purge_expired` deletes at most 100 expired attempt ledgers per
owner connection; result rows cascade, freeing logical quota exactly once.
The existing worker maintenance loop also purges at most 100 expired attempts
per pass under its trusted bypass, using the database clock and SKIP LOCKED.
Web-only maintenance performs the same bounded purge independently of job
cleanup failures. Full worker batches retry after one second; no content is
regenerated. Statement and lock timeouts bound the cleanup transaction. Operational SQL/index/WAL/
backup capacity is not covered by logical JSON quotas.

## Revocation and deletion

Migration triggers on connection changes, secret INSERT/UPDATE/DELETE, user/tenant
activation, web membership/role changes and workspace-role/permission mutations
invalidate epochs, clear both consents and purge attempts/results IN THE SAME
transaction as the change. They work even if old values are restored before any
subsequent read, and cover generic CRUD paths outside this manager. Invalidation
is intentionally conservative: even harmless saves affecting watched columns may
require new consent. Object identity/name/kind/observation changes fence and purge
that object's attempts/results. No inference of remote deletion from partial scans.

`forget_object` explicitly purges ALL compact results for an authorized object and
rotates its persistent fence before removal; old in-flight saves cannot recreate
content. Connection/object deletion cascades control/attempt/result rows; losing
an attempt always denies replay. Parent authorization locks plus mount/object locks
serialize saves with revocation; this must be exercised under real PostgreSQL.
No claim can recall bytes already released before a revocation committed.

## Encryption and historical release

`PrivateCipher` derives a purpose-separated Fernet key from an explicitly supplied
valid existing credentials key. No fallback, secret generation or settings export.
Only canonical <=64KiB DTO bytes are encrypted. Ciphertext is bounded to 90000
characters, and exact plaintext charge/hash is verified on load. Rotation makes
old ciphertext unavailable; re-encryption/backup recovery is NOT implemented here.
Selected quotes/candidates may themselves contain secrets; this is not redaction.

`get` rechecks current private access, consent, fences, original attempt, expiry,
charge/hash and decoded canonical scope/provenance/binding. Return is repr-hidden,
historical and unverified. It does NOT certify the provider's current content or
authorize sharing/model context/actions. No generic CRUD/search/global knowledge
projection, full original, retained prompt/response or public URL is introduced.

## Local verification and audit handoff

Use the focused [local handoff](tasks/p12-t12-local-handoff.md) for prerequisites,
commands, unrun checks and review gates. The PR body records remote evidence.

## Default-off file actions (remote completion package)

The standalone owner UI is gated by `FILE_CATALOG_UI_ENABLED` and
`FILE_WORKSPACE_ACTIONS_ENABLED` (both off by default). The latter requires 0128
and operational review. A separate `FILE_PRIVATE_RESULTS_UI_ENABLED` flag gates
historical result view/delete and requires the configured credentials key. This
is not a production-enable recommendation. Consent is explicit, scoped to the
personal connection/selected root, and changes purge previous results. Original
attachment does not require processing consent; selected-line preview does.

A POST requests an isolated attachment, not a native provider-open link. The
Yandex client accepts only bounded .txt bytes and fixed reviewed HTTPS hosts,
validates DNS answers and TLS, rejects redirects/compression and limits response
sizes and time; it reauthorizes before and after I/O. It compares metadata and
MD5 as error checks, **not** exact version, snapshot, identity or ABA proof.
Only the documented example download host is allowed; other legitimate hosts
fail closed until reviewed. Preview displays selected inert quotes, not model
output. No automatic request, model call, source persistence, or save action is
introduced. With no provider-backed exact-byte revision assertion, saving new
Yandex results remains unavailable. Historical results remain unverified.
Revocation cannot retract bytes already delivered.

Before enabling, follow the linked local handoff. Mocked HTTP and synthetic
tests are not provider, deployment or local DB evidence.

## Combined migration and key lifecycle

The unpublished catalog migration is `p12t12_catalog`, following
`p12t14_tg_retention`; search/scans/traversal/private state follow as 0125-0128.
This preserves the encrypted-refresh `0124` history. Both original PR heads and
the reconciled original plan are retained in integration ancestry.

The credential rotation utility recognizes the combined schema generations.
At 0128, dry-run output includes private mount/attempt/result counts. Apply
refuses while a private mount exists unless `--private-results-purge-approved`
is explicitly supplied. Approved rotation uses the normal invalidation fence
and clears private consent and results in the same transaction; it never
disables triggers. Any later failure rolls the credential changes and purge
back together. New consent and a new read are required afterward. This is not
private-result re-encryption or provider revocation. Working-key rotation was
not performed; tests use synthetic keys and a disposable database.

The metadata schema bootstrap installs the same 0128 helper, hook functions and
eight triggers as Alembic, gated on their required tables. The offline inventory
checks complete function bodies, including multiple DDL declarations in one
`op.execute`, without importing application/driver code.
