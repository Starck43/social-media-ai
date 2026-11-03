# P12-T12 — Draft private insight storage lifecycle

Parent: [P12-T12](tasks/p12-t12.md), [draft PR #327](https://github.com/Starck43/social-media-ai/pull/327).
Inputs: [bounded read](FILE_WORKSPACE_CONTENT_READ.md),
[line evidence](FILE_WORKSPACE_LINE_EVIDENCE.md),
[unverified candidates](FILE_WORKSPACE_INSIGHT_CANDIDATES.md).
Shared engineering interface: [admission contract](FILE_WORKSPACE_ADMISSION.md).
Review owner: [P12-T13](audit/p12-t13.md); real read/version adapter: [P8-T34](https://github.com/Starck43/social-media-ai/pull/313).

## Status and deliberately small scope

The later [default-off implementation](FILE_WORKSPACE_PRIVATE_IMPLEMENTATION.md)
now delivers manager-owned control/attempt state and encrypted retention code in
PR #327. Sections below remain lifecycle requirements/proposal history, not a
claim that earlier pure-helper tests verify the new PostgreSQL implementation.

**Proposal for review, NOT an approved policy or an implementation.** Storage
stays OFF. This document adds no ORM model/manager, DDL, migration, job, route,
cache, model/provider call or permission. Existing 0124–0127 catalog migrations
store metadata only; they do not persist these candidates. No test result for
in-memory helpers certifies this lifecycle, quotas, DB concurrency or erasure.

Propose retaining only selected quotes and compact **unverified** candidates for
a consenting personal owner, briefly and within hard bounds. No original bytes,
full canonical text, mirror, file backup, vector index or workspace knowledge
projection. Selected quotes are still private content, not anonymized metadata.
A hash is neither anonymous nor a permission nor a provider content version.

The coordinated P12-T13 review selects these conservative bounds for inactive
preparation in the [admission contract](FILE_WORKSPACE_ADMISSION.md). Durable
implementation, deployment-policy approval and local verification are still
required before activation; actual processing/retention consent is independent.
Missing policy, consent, version semantics, storage guarantees or authority means
no save/release, not a permissive fallback. Retention consent is distinct from
READ, cloud processing, external-model egress/spend and sharing permissions.

## Implemented pure timing subset

[retention_time.py](../app/integrations/file_workspace/retention_time.py) implements
`make_retention_deadline` and `retention_time_eligible` for the proposed 5-minute
capture age / fixed 24-hour TTL. Exact stdlib `timezone.utc` datetimes only,
including microseconds; naive/non-UTC/subclass inputs fail closed. Expiry is
capture-relative, not sliding; exact expiry and supplied clock rollback deny use.
The fixed format rejects changed deadlines; policy shortening needs a separately
reviewed policy-revision input, not caller edits. These defaults remain proposals.

The caller must supply and atomically preserve a trusted clock high-water mark,
including on expiry denial. The pure helper does NOT persist/prove that mark,
authenticate provenance, check owner/version/consent, reserve quota, run GC or
activate storage. Frozen repr-hidden data is not an access grant. Focused tests
are [here](../tests/unit_services/test_p12_t12_retention_time.py); DB atomicity,
durable anti-rollback and the rest of this lifecycle remain unimplemented.

## Implemented pure quota subset

[retention_quota.py](../app/integrations/file_workspace/retention_quota.py) adds
`retention_quota_allows(payload, snapshot)` for ONE new unreserved record. It
checks nonempty exact bytes/strict UTF-8/64KiB, object count and all connection/
owner-within-tenant/tenant count+byte ceilings, including outstanding reserves.
Exact nonnegative counters, count/byte consistency and child <= parent usage
are required. Expired/invalidated rows still count until purge. Limits above
remain proposals, not approved entitlements or deployed storage controls.

The caller supplies the COMPLETE canonical serialized envelope and a trusted,
coherent current snapshot; the helper cannot prove either. This is not DTO/schema
validation, a scope/owner proof, atomic reservation, eviction, idempotent retry
settlement or DB concurrency safety. It never mutates usage. Future persistence
must recheck/reserve all dimensions atomically. [Synthetic tests](../tests/unit_services/test_p12_t12_retention_quota.py)
include the stale-snapshot/non-reservation trust boundary. Storage stays OFF.

## Implemented pure payload-format subset

[Format v1](FILE_WORKSPACE_PRIVATE_PAYLOAD.md) and
[private_payload.py](../app/integrations/file_workspace/private_payload.py) add
closed-schema validation/canonical UTF-8 serialization and canonical-byte checks.
Bounds precede encoding; exact JSON types, fixed revisions, UTC microseconds,
quote/candidate caps and existing 1-based reference semantics are preserved.
No context/dataclass dump, unknown fields or invented missing provider version.
Charge the complete returned bytes outside the envelope. These are syntactic
checks ONLY: supplied versions/IDs/epochs/times can be forged and old data can
be format-valid. No authentication, source consistency, current authorization,
clock high-water proof, storage integrity, reservation or persistence is delivered.
Reviewable format/limits, not approval. Storage stays OFF.

## Personal ownership and immutable source binding

Use the [connection's personal owner](../app/models/integrations/connection.py)
and the [private catalog manager](../app/models/managers/file_catalog_manager.py),
not generic CRUD or a tenant-wide knowledge ACL. Membership, tenant ownership,
operator roles and possession of an insight ID do not grant private-file access.
Never automatically copy a retained result when owner, tenant, account or
connection changes. A connection with a different personal owner is a different
namespace, even for the same path/content.

A future immutable retained envelope needs an allowlisted, versioned storage DTO:

- Tenant, personal owner, connection and catalog-object identity; selected-root
  namespace and the catalog's path-identity key. Resolve display paths/names via
  current authorized catalog access instead of duplicating them into the record.
  [Catalog identity](../app/models/file_workspace/__init__.py) is currently
  root/path-based; a rename is NOT proof of the same provider object.
- Opaque provider version **asserted for these exact bytes** by a reviewed adapter,
  with the adapter/version-semantics revision and provider object ID only if that
  adapter actually supplies a stable identity. Reject unbounded/unknown metadata.
  Raw/text SHA-256 and parser version remain separate extraction fingerprints.
- Selected canonical line ranges, exact quoted text, line count, candidates and
  their excerpt references, with `review_status=unverified`. Preserve current
  quote/candidate caps and validation; no additional inferred facts or free-form
  provider/model payload. Citation consistency is NOT truth or entailment.
- Server-issued read/generation attempt binding, trusted capture time, lifecycle
  format revision, consent/policy revision and opaque invalidation epochs for the
  owner/connection/object scope; immutable creation/expiry times and charged size.
  Epochs are control-plane revision IDs, NOT OAuth tokens or secret fingerprints.

Do not serialize current dataclasses wholesale: their context includes paths and
credential fingerprints and lacks a trusted capture time/durable epoch. A frozen
Python object or user-supplied JSON is not authenticated provenance. A future
writer must consume an application-owned read/generation result with independently
validated, bounded metadata; no direct public DTO-to-storage path. DTO/schema,
opaque-field byte caps and serializer revision must be specified and tested before
code admission. No pickle, arbitrary classes, raw credentials, download/share URLs,
full prompt/response, chain-of-thought or personal content in logs/idempotency keys.

**Conservative first-pilot proposal:** a missing provider version, a version not
bound to the exact completed read, or unreviewed adapter semantics denies durable
save. `None` remains unknown in existing in-memory results. Never substitute a
hash, mtime, observation counter, name or successful LIST for a provider version.
Whether to support separately labelled historical observations without a provider
version is an explicit later review question, not a fallback in this proposal.

## Admission, access and invalidation

Future save sequence, without holding a DB transaction over provider/model I/O:

1. Complete the bounded authorized read and candidate generation; verify the
   same-read evidence and preserve unverified status. Capture time/attempt/epochs
   come from the trusted orchestration, not a caller or a model.
2. Obtain explicit scoped retention consent and a reviewed fresh exact-version
   assertion. The adapter's freshness/conditional-read contract must be agreed
   with P8-T34; no freshness duration or transport implementation is invented here.
3. In the existing ORM/manager transaction pattern, recheck active actor/membership,
   `collection.source.view`, exact personal owner, active connection, current root,
   LIST+READ restrictions, current credential/account and cloud-processing policy,
   plus retention consent. Reject company/local_only/unknown boundaries. Match
   immutable source/version/consent epochs to current control-plane values.
4. Atomically enforce expiry, validate payload, reserve all quota dimensions,
   insert once and settle quota with the commit. A failed check, cancellation,
   timeout or rollback exposes no saved payload and leaves no charged reservation.
   Reuse reviewed transaction managers; no parallel ad-hoc session/authorization.

Retrieval, list/count/search and any future export must freshly check the same
personal scope and current policy. Fetch only within that authorized partition,
then recheck epochs/expiry before release; no cross-owner/global search, content
cache, embedding, generic knowledge projection or existence/count side channel.
A stored ACL/consent snapshot is evidence, not current authority. Any future use
as current-file context also requires a reviewed fresh matching version assertion;
no version revalidation means unavailable, not "still current". There is no
automatic fetch, regeneration, model call or external export to repair staleness.
Authorized display must label the capture time, asserted historical version and
unverified status; a stored version is not proof of the provider's current state.
Validate the stored schema/ranges/references on load, but do not fabricate a new
ReadPlainText/read nonce to make historical evidence look freshly read. Without
full original bytes/text, load-time checks cannot recompute their hashes or rerun
the existing same-read verifier; storage integrity is a separate implementation
gate. A new export/sharing/action surface needs separate approved policy and tests.

Proposed invalidations: permission/membership loss, retention-consent withdrawal,
connection disable/delete, owner/account/credential rotation, root or processing/
access-policy change, proven object deletion or different exact content version.
Advance a durable scope epoch under the same authorization/connection locks used
by admission; make existing records immediately ineligible and enqueue physical
purge. Re-enabling the old policy or restoring consent MUST NOT resurrect old
rows or old in-flight attempts; a new read/generation under the new epoch is needed.
An unavailable authority/version check denies access but alone does not assert
remote deletion. Partial catalog scans, absent pages and path renames must not
invent a provider deletion/stable identity. Never inspect another owner's data
or call a provider just to decide whether GC can delete our retained copy.

Delete/withdrawal and admission must share a serialization boundary. Deletion
advances the applicable durable generation fence before/with purge; a late save
with the old epoch fails even if the old payload row is gone. Keep the fence
outside purgeable content (not a tombstone containing text/path/hash). Manual
result deletion also fences that object attempt scope; it is not an invitation
to regenerate. Future implementation must prove lock order, epoch ownership and
crash recovery; a check-then-insert or process-local lock is insufficient.
Once bytes were released, a later revocation cannot recall them; do not claim
linearizable provider state or physical memory erasure from application checks.

## Proposed pilot quotas and admission accounting

These are conservative engineering ceilings, **not existing plan entitlements**. Do not borrow
[tenant plan retention](TENANCY.md#plans-and-quotas) or catalog limits as approval.
Count every retained row, including expired/invalidated rows awaiting purge, and
all outstanding reservations. No silent eviction, truncation or auto-upgrade.

| Dimension | Proposed hard ceiling |
| --- | --- |
| One retained envelope | 65,536 logical UTF-8 bytes, including metadata/quotes/candidates |
| Per catalog object in its owner/connection namespace | 3 records |
| Per connection | 100 records AND 4 MiB logical payload |
| Per personal owner within one tenant | 200 records AND 8 MiB logical payload |
| Per tenant, across its personal partitions | 1,000 records AND 32 MiB logical payload |
| Capture-to-save delay | At most 5 minutes; also reject future-dated capture |

1 MiB = 1,048,576 bytes. Charge the exact UTF-8 length of the canonical versioned
allowlist JSON envelope, not code points, tokens, compressed bytes or a Python
repr. Proposed serialization: fixed schema, no floats/NaN, deterministic sorted
keys, no insignificant spaces, `ensure_ascii=False`; stored timestamps have one
specified UTC precision. The quota charge field itself lives outside this
serialized envelope. Bound/type-check fields before encoding; no unbounded
provider metadata can allocate memory before a later size check. SQL/index/WAL,
control-plane fences and physical backups are NOT covered by logical quotas;
operational capacity/bounded metadata growth remain separate enablement gates.

All dimensions must admit together in one transaction, with deterministic lock
order and rollback-safe reservations. At an exact limit permit the last fitting
record; at plus one byte/count reject the entire save without dropping quotes.
Concurrent saves cannot both spend the last slot. Deletion frees charge exactly
once in the same transaction as row removal; quota repair is reconciliation of
private metadata only, not source reads/model calls or invented negative usage.

Idempotency is scoped to tenant/owner/connection/object + server-issued generation
attempt + lifecycle epoch, never a global content hash. A matching retry returns
the same authorized result/expiry without extra charge or extension; a different
payload under that identity fails. A retry still rechecks current authority,
epoch and expiry. Do not recreate a missing expired/deleted row from a replay.
Trusted capture age, a nonextendable deadline and durable invalidation fences
must remain sufficient after payload GC; their crash/replay/metadata-growth
implementation is a required local review, not delivered by this document.

Storage caps do not authorize a model call or account for model tokens/costs.
A later real model must use separately approved routing/admission and durable
usage evidence. Existing [AI/billing receipts](MODELS.md) have their own retention
and FK rules; private-insight GC must not delete or rewrite them.

## Absolute TTL, bounded GC and failure behavior

Proposed TTL: **24 hours from trusted read capture**, not sliding access time or
save time. `expires_at = captured_at + 24h`, fixed at admission; current policy may
shorten but never extend it in place. No save if `now >= expires_at`, capture is
future-dated or capture-to-save exceeds 5 minutes. Reads, duplicate saves, login,
retries and re-consent cannot renew it; new retention requires a new authorized
read/generation under current policy, not copying the old payload.

`now >= expires_at` denies every read/list/reuse immediately, even if GC is down.
Use server UTC and transaction-consistent time; test exact boundaries and clock
rollback. Implementations must not reopen an expired record after a backwards
clock jump (e.g. a durable expiry fence/high-water rule); clock health/rollback
handling must be selected before enablement, not assumed solved by UTC.

Proposed GC target: purge eligible payloads within **one hour** of expiry or
invalidation during healthy operation, in resumable batches of at most **100**.
This is a target for implementation/local acceptance, NOT an observed SLA.
Use a narrowly scoped cleanup capability that can identify/delete eligible rows
and adjust quota atomically without returning content or granting source access.
A service owner losing file access must not prevent removal of our expired data.
No provider calls, model calls, source writes or broad operator content bypass.

Lease/CAS or equivalent manager-owned serialization must prevent double release
and resurrection across concurrent GC/delete/save and crash/retry. Process only
bounded eligible partitions/batches; no unbounded table/content dump into memory.
Emit minimal reason/count/age health evidence without paths/quotes/hashes or
credentials. Log/audit/fence retention and aggregate health visibility need a
separate reviewed bound; do not create an unlimited shadow history.

If GC/storage is unhealthy, keep access denial at expiry/revocation and stop new
admissions when quotas/capacity or required authority/fences cannot be proven.
Report delayed physical purge honestly; no successful-erasure claim on timeout.
The implementation must distinguish a committed save with a lost response from
rollback via authorized idempotency lookup; never regenerate on an ambiguous save.

## Pin, backup and sharing boundaries

No pin or archive exemption in the first proposal. A "favorite"/UI label cannot
extend TTL, bypass invalidation or consume unlimited storage. Durable pinning,
longer-lived compact history or a separately labelled unknown-version historical
record requires an explicit owner/P12-T13 decision, new quotas/consent/deletion
semantics and tests; do not add it as a hidden cache behavior.

This service is NOT an original-file backup and does not promise recovery of
quotes/candidates after TTL/delete. Opening the current provider original does
not recover the analyzed historical version. Existing infrastructure snapshots,
WAL, replicas and backups may outlive a logical deletion: no claim of physical
24h erasure or cryptographic wipe without a reviewed operational policy. Before
activation, specify backup retention/access/encryption, restore-time suppression
of expired/revoked data, and deletion-fence restoration; otherwise storage stays
off. Never restore an old payload without current time/authority/epoch checks.

Do not write personal file results into [workspace-owned knowledge models](../app/models/knowledge/__init__.py)
or hand them to another member/agent by membership alone. Sharing, approved facts,
actions and conditional source writes remain separate P12-T15 gates. Candidates
remain untrusted/unverified data; no executable interpretation or truth promotion.

## Review decisions and future acceptance matrix

P12-T13/owner review must explicitly resolve:

1. Approve/change the quoted-data allowlist, personal boundary, consent wording,
   model/processing separation and whether any unknown-version history is wanted.
2. Approve/change numeric byte/count/capture-age/TTL/GC defaults; specify serialized
   field caps, control-plane growth limits, UTC precision and clock rollback rule.
3. Agree with P8-T34 on exact-byte version identity/freshness and rename/account/
   credential revocation; the currently unaccepted draft is not an adapter grant.
4. Select manager/ORM transaction/fence/quota and cleanup ownership, physical
   backup/restore/deletion policy, and local verification evidence before activation.

Posting a review request is NOT agreement. No approval or accepted upstream
revision is recorded by this draft. Keep parent IN_PROGRESS and PR draft.

| Future focused synthetic/local check | Required outcome |
| --- | --- |
| Owner/tenant/connection/object substitution; member/operator guesses ID/count | Deny without private content/existence leak; no shared knowledge row |
| No retention consent; company/local_only/unknown boundary; auth changes mid-save/read | Deny; no payload or reservation leak |
| Missing/foreign/stale version, path rename, metadata-only observation | No invented exact version/currentness; deny durable admission when unproven |
| Epoch changes, revoke then re-enable, delete versus late save | Old result/attempt never resurrects; new authorized read required |
| UTF-8 multibyte payload, envelope exact limit and plus one, every aggregate cap | Exact logical charge; whole-record rejection; no truncation |
| Parallel last-slot saves, duplicate retry/different payload, rollback/cancel/crash | Single accounting; no over-admission or invisible charged reservation |
| Capture age, future capture, exact TTL, duplicate/read renewal, clock rollback | Fail closed at boundaries; nonextendable TTL; no reopening |
| GC down/restart/parallel/delete, lease expiry, repeated purge | Expired data unavailable; bounded idempotent purge; quota freed once |
| Backup restore with expired/revoked rows and stale epochs | No restored access/resurrection; physical retention limitation disclosed |
| Logs, cache/index/knowledge/export/model boundaries | No content/credentials or implicit sharing/egress; receipts untouched |

Pure lifecycle/accounting policy tests can be implemented on synthetic inputs
separately; they cannot certify DB locks/FKs, operational erasure or real ACLs.
Future DB concurrency/integration and owner-local real-condition audit require
explicitly isolated test-schema work and exact source evidence. No public
migration, deployment, merge or real provider/model operation is authorized here.
