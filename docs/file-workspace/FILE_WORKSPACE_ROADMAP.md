# File-workspace roadmap and acceptance boundaries

This document preserves the parent-product requirements from the original
P12-T12 planning card and maps them
to the current task owners. It is a plan and acceptance checklist, not evidence
that every listed capability has shipped.

## Product goal

Let a person find an allowed file, understand whether its content is available
and current, inspect the original, and use bounded agent help without creating
an unnecessary mirror. Show what the system retained and what an operation did.
Measure time to a useful answer or original, repeat work, retained storage and
resource use, rather than indexed bytes alone.

Representative journeys:

- Select a connected account or paired device, choose one folder and see its
  effective read scope, processing boundary, limits and retention before use.
- Find a file by its visible source/name, see analysis freshness and evidence,
  and open its original when a safe supported route exists.
- Compare observations only when the referenced versions and required bytes are
  actually available; explain when an older version cannot be reconstructed.
- Ask about selected files, preview an answer with source/version/line evidence,
  then choose whether to retain it. Repeated unchanged reads should not create
  redundant bodies or analyses without a policy reason.
- Review, preview and execute any permitted operation under separate rights and
  explicit confirmation. Unknown outcomes must remain unknown until reconciled.
- Inspect file-access history, saved insights, version observations, cache and
  storage use as distinct records with distinct deletion and retention behavior.

## Ownership and current implementation boundary

| Scope | Owner and contract | Acceptance boundary |
| --- | --- | --- |
| Pilot capability/security contract | [P12-T13](../audit/p12-t13.md) | Existing admission contract defines separate processing, retention, model-egress and action grants. Contract review does not grant user consent or turn on storage. |
| Read-only Yandex Disk pilot | [P8-T34](../tasks/p8-t34.md) — **owner-deferred 2026-11-04**; resume via draft [PR #313](https://github.com/Starck43/social-media-ai/pull/313) | Metadata browsing and bounded mock-tested transport do not prove live account connectivity, exact immutable byte-version identity or safe native browser opening. The owner will test live connections after merge; that check remains explicitly unrun until then. |
| Catalog, bounded text read/preview, private-result foundations | [P12-T12](../tasks/p12-t12.md), [PR #327](https://github.com/Starck43/social-media-ai/pull/327) — increment accepted 2026-11-03; remaining roadmap [re-scoped 2026-11-11](../tasks/p12-t12.md#re-scoped-remaining-roadmap-2026-11-11) | Delivered portions are default-off. Current text attachment is not a provider-native open and does not certify an immutable provider version. Durable Yandex result creation remains unavailable until exact-version evidence is established. |
| Folder policy editor, sharing and approved file actions | Per [D21](../audit/q-20-notification-surface.md) the file-workspace management (including folder grants and mutations) is owned by [P12-T12](../tasks/p12-t12.md); [P12-T15](../tasks/p12-t15.md) is a future extension of it, never a second management | Separate future scope. Read permission, membership and a provider token do not imply sharing or write/delete permission. |
| Local-only execution | [P18](../phases/p18-local-file-connector.md) — protocol accepted, gateway/scaffold/extraction delivered; T05–T07 TODO; [Q-19](../tasks/q-19.md) open | P18 reuses the P12-T12 catalog/admission/evidence/retention contracts; P12-T12 remains their contract owner. No cloud egress may be claimed before Q-19 and real-device evidence. |

Integrating the delivered #327 increment does not complete the entire originals-first
parent journey. The task remains open while its remaining file-history, performance,
recovery and user-flow scope remains; local-only execution is delivered by P18 on top
of these contracts. Do not duplicate accepted P12-T11 evidence, the existing job
queue, old immutable evidence revisions or credential authorities.

## Re-scope status (2026-11-11)

Where the six delivery-sequence steps stand after the accepted increment and the
subsequent owner decisions. Owners, gates and the residual slices S1–S4:
[P12-T12 card](../tasks/p12-t12.md#re-scoped-remaining-roadmap-2026-11-11).

| Step | Status |
| --- | --- |
| 1. Capability contract | Reviewed and accepted ([P12-T13](../audit/p12-t13.md)). The exact-version capability for Yandex remains unestablished, not fabricated ([P8-T34](../tasks/p8-t34.md), owner-deferred). |
| 2. Read-only catalog and original access | Catalog, scans/traversal and the bounded attachment are delivered default-off. Vanished or moved originals are not reconciled: the admitted-page manager never infers deletion (residual S1). Original-open is gated on P8-T34 (cloud route) and P18/[Q-19](../tasks/q-19.md) (device route). |
| 3. Bounded analysis and compact insights | Delivered: bounded `.txt` extraction, selected-line preview, mock candidates, encrypted retention lifecycle with bounded expiry. Durable admission and version-aware freshness stay gated on exact-version authority (residual S3). |
| 4. Typed actions | Not started (residual S2). Writes remain off until preview, fresh approval, conditional revision checks and reconciliation pass. |
| 5. Local-only execution and later adapters | Owned by [P18](../phases/p18-local-file-connector.md). P12-T12 keeps contract ownership of the reused catalog/admission/retention records; additive contract changes are reviewed under P12-T12 when a consumer needs them. |
| 6. Parent evaluation | Pending activation; measurement harness is residual S4. |

## Data, identity and version model

Keep originals at their current provider/device. Treat these as separate concepts:

- **Catalog observation:** stable identity only where the adapter can establish
  one; parent/root, display name/path, type/size, access, freshness and observed
  availability. A path or hash is not a stable provider identity.
- **Provider revision:** provider-issued version and documented semantics, if
  available. It must bind to the representation whose bytes were read. A hash,
  mtime, observation number or matching LIST response is not a substitute.
- **Extracted artifact:** bounded output tied to the exact input revision, parser
  version, data boundary, size and retention policy. Keep one justified canonical
  representation; do not store a complete duplicate plus redundant chunks by
  default.
- **Insight:** a concise finding and uncertainty with references to its input
  revision and line/page/cell anchors. Keep machine output distinct from user
  corrections. Mark stale when its source is unavailable or changes.
- **Operation and access history:** actor, task/request identity, target revision,
  approval, result and outcome. The journal records metadata, not file bodies or
  full prompts. Personal access history is not automatically visible to other
  workspace members.
- **Cache, retained result and backup:** each has separate purpose, expiry,
  quota, restore and deletion semantics. A digest or journal is not a backup.

Personal results stay scoped to tenant, personal owner, connection, selected
root and current consent/epochs. Tenant membership alone does not share a
personal disk. Never infer owner or provider identity from a client-supplied
path, generic source reference or credential possession. Preserve the existing
legacy evidence and knowledge rows; link new catalog identity through additive,
compatible schema changes.

Local-only applies to file names/paths, content, previews, hashes, derived text,
search queries, insights, logs and telemetry. Until an end-to-end paired local
route and its no-egress evidence exist, keep cloud processing denied for that
boundary. A UI selector is not a local executor or model route.

## User experience

Keep one connection entry point and integrate a file catalog into existing
knowledge/document flows. Avoid a separate menu or disconnected copy for every
provider. Use paged server queries; do not load every file body to render a list.

For each available item, show its name, source/folder, type, observed freshness
and supported state. Keep hashes and access-policy details in technical
information. Separate original, agent insights, history and access/storage.
Distinguish “access checked”, “listed”, “content read”, “analysis available” and
“result retained”; one successful login must not imply a completed sync.

Cover empty, unsupported, encrypted/password-protected, partial scan, offline,
reauthorization, moved/deleted, stale, quota and unavailable-original states.
Responses and counts must not reveal another person's file existence. Existing
evidence without a catalog identity remains accessible with an honest
“original not linked” explanation. Never convert an arbitrary source reference
into a browser URL.

Opening the original accepts an opaque catalog ID. Recheck actor, tenant,
personal owner/share, connection, selected root, current policy and object
identity at use. Reject arbitrary schemes, URLs, redirects and token leakage.
Use a reviewed authenticated provider route or bounded proxy with safe download
headers and byte/time limits. A local path cannot be opened on a user's device
from the server: this requires a separately paired, authenticated, revocable
device operation. If the provider cannot safely open the requested revision,
state that limitation.

## Commands, authorization and failure behavior

Keep capabilities narrow: list/read/open-original do not grant create/modify/
delete. File bytes are untrusted data, never instructions that can change roots,
permissions, secrets, confirmation or shell access.

Before enabling any mutation, require a typed preview bound to actor, tenant,
target object and exact expected revision, plus a fresh explicit confirmation
with expiry. Recheck current rights and version at execution; use provider
conditional writes; verify the resulting representation and issue a receipt.
If the target changes, require a new preview and approval. If the adapter cannot
prove a conditional replace, refuse overwrite. After an uncertain timeout,
reconcile by operation identity instead of blindly retrying. Cancellation after
commit is not undo; undo is a separate operation with its own evidence.

Keep provider credentials encrypted and owner-scoped. Bound pagination, response
bytes, parsing, time, redirects, retries and rate-limit waits. Mock external
writes and paid/model calls in tests. Revocation immediately denies new reads;
physical deletion follows the verified retention process and backup lifetime.
Do not promise instant erasure from backups or recall data already delivered.

## Storage and lifecycle

Do not mirror every original or retain every read. Perform metadata scans
separately from content reads, and reuse an unchanged authorized representation
when safe. Begin with metadata/text search and measure quality before adding
embeddings. Keep the provenance needed to explain an answer without retaining
full prompts or conversation reasoning.

Before enabling storage, settle explicit byte/count quotas, capture-age limit,
TTL, retention and clock-rollback behavior in the owning contract. Count expired
rows waiting for purge and concurrent reservations. Reject a save atomically if
any limit is exceeded; never truncate quotes, silently evict a saved result or
extend TTL on retry. Deletion must free quota once under concurrency.

Cache, index, history, insights, pinned outputs and originals have distinct
controls. “Clear cache” must not delete an original or a retained report.
“Forget insight”, “remove index”, “disconnect folder” and “delete original” are
different actions with clear consequences. Original deletion remains separately
approved and off in the first read-only pilot.

Expiry must deny access even when cleanup is unavailable. Bounded, resumable
garbage collection must fence in-flight save/read work, avoid resurrecting
revoked data, and avoid returning content or granting provider access. Expose
minimal health counts, not paths or quotes. Before enabling retention, test
backup/restore, stale-row suppression, deletion fences and key loss. Encrypted
backups of derived data must not be described as backups of the original disk.

## Delivery sequence and exit criteria

1. **Capability contract.** Review one in-demand provider and one folder. List
   verified, planned and unsupported operations; identity/version semantics;
   privacy boundaries; explicit grants; numeric limits; migrations and rollback.
   Keep provider choice and rollout decisions with the owner. Do not activate a
   connector from a planning document.
2. **Read-only catalog and original access.** Additive migrations, owner-safe
   paged scan and recoverable cursor, folder selection, object detail and
   supported secure-original flow. Reconcile incomplete scans before tombstoning;
   absence from a partial page is not deletion. Exit when a supported user flow
   can list, find and securely open an original with no permanent body mirror.
3. **Bounded analysis and compact insights.** Support only named formats with
   parser/time/size limits and line/page/cell anchors. Add version-aware
   freshness, provenance, reuse, storage accounting and expiry. Exit when
   unchanged authorized input avoids redundant work and changed input makes
   dependent findings stale.
4. **Typed actions.** Connect approved search/read/analyze/propose/output tools
   to the existing job queue with visible progress, cancellation and budgets.
   Keep writes off until preview, fresh approval, conditional revision checks and
   reconciliation pass. Exit only when conflicts, revocation and uncertain
   outcomes cannot cause a silent overwrite or duplicate output.
5. **Local-only execution and later adapters.** Pair/revoke a device, scope
   canonical roots, provide local listing/reading/search and prove no cloud egress
   for local-only metadata and content. If no local model is available, refuse
   analysis without cloud fallback. Add other adapters/formats only with their
   own capability evidence; do not claim arbitrary-file analysis.
6. **Parent evaluation.** Run named user journeys, migration recovery, privacy
   and storage checks; report measured latency, memory, bytes and cost for a
   fixed corpus before choosing numeric performance targets. Close the parent
   only after the agreed cloud and local scope is complete or the owner records
   an explicit scope change.

## Acceptance evidence for future implementation

Use the normal assembled application, authorization middleware, ORM and DB for
integration paths. Mocks cover external provider/model behavior; they do not
prove live connectivity. The owner will test real connections after the changes
merge. Report those checks as pending/unrun until performed.

- Empty selection to supported folder, listing, search, detail and safe original
  access; no demo fallback or fabricated provider status.
- Two tenants and two personal users in one tenant; owner/share/revoke checks
  before search, preview, download, saved-result reads and queued work. Forged
  object/revision/mount/job IDs reveal no content or existence.
- Local-only network-denial instrumentation for content, metadata, queries,
  derived artifacts, model calls and telemetry. Without the local executor, deny.
- URL/redirect/token, stale-link, traversal/symlink and revoke tests for original
  access; offline device does not report success.
- Cursor/paging retries, root changes, rename, deletion, partial scans, 429 and
  outage recovery. Incomplete scans do not hide known files.
- A fixed corpus records repeated scan downloads, repeated extraction, stored
  body count, journal growth, logical bytes, physical storage, and measured query
  latency/memory. State the environment and corpus; do not claim universal
  savings or invented numerical targets.
- Version changes stale old findings and require new approval for actions.
  Missing historical bytes do not appear restorable.
- Malformed/large/encrypted/unsupported files, compressed bombs, active content,
  prompt injection and parser timeout fail within documented limits.
- Writes require least privilege and actor/version/payload-bound approval.
  Version conflict, timeout, retry and cancellation cannot silently overwrite,
  claim false success or create duplicate output.
- Cache clear, result deletion, connection revoke, expiry, GC and concurrent
  saves preserve retained records correctly; purge is bounded, idempotent and
  leaves unrelated evidence/receipts intact.
- Keyboard/mobile and empty/offline/stale/unsupported states make source,
  freshness, privacy boundary and next action understandable.
- Test-schema migration upgrade/rollback/restore preserves earlier evidence and
  leaves no synthetic residue. Never use public test records. Any public
  migration requires test-schema validation and separate apply authorization.

## Provenance

The original planning PR #307 is merged into the current implementation history;
this roadmap preserves its product and acceptance requirements in English.
Current implementation and verification detail stays in the active
[P12-T12 card](../tasks/p12-t12.md), its [local handoff](../archive/tasks/p12-t12-local-handoff.md),
and [PR #327](https://github.com/Starck43/social-media-ai/pull/327). Contract,
Yandex and folder-action work remains owned by the linked task cards. This
document records requirements and their owners; it does not change task status
or assert unfinished behavior as accepted.
