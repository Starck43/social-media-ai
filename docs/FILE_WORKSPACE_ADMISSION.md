# P12 file-workspace admission contract

Parent: [P12-T12](tasks/p12-t12.md); capability owner: [P8-T34](tasks/p8-t34.md);
policy review: [P12-T13](tasks/p12-t13.md); implementation: [PR #327](https://github.com/Starck43/social-media-ai/pull/327).

## Decision and boundaries

The owner assigned the three-task integration to one executor and requested grouped
implementation with focused tests at batch end. This document settles the shared
engineering interface; it is not end-user consent, independent audit, production
policy approval, a provider capability claim or permission to enable storage.

Use existing conservative bounds for inactive preparation: plain UTF-8 `.txt` only,
262144 input bytes, 32768 canonical code points, 1000 lines, 8 excerpts/32 selected
lines/8192 excerpt code points, 5 candidates/600 each/1800 total and 4 references
each. Admission capture age <=5 minutes, fixed capture-relative 24-hour expiry,
65536 canonical envelope bytes; object/connection/owner/tenant quotas remain those
in [private storage](FILE_WORKSPACE_PRIVATE_STORAGE.md#proposed-pilot-quotas-and-admission-accounting).
No eviction, silent truncation, TTL renewal or implicit budget upgrade. These
engineering ceilings may be implemented/tested while activation remains OFF.
Deployment retention/backup/capacity approval is still required before persistence.

### Independent grants

| Operation | Required authority; no implication for another operation |
| --- | --- |
| Metadata LIST / original-open | Current personal owner, tenant, active connection and folder policy; original-open transport remains separately gated |
| Cloud extraction | Explicit current scoped cloud-processing consent plus LIST+READ and live access |
| External model generation | Separate model egress/spend/usage consent and approved adapter; no real model in this batch |
| Private retention | Separate explicit scoped retention consent, current policy revision and epochs, exact-byte version eligibility and atomic durable admission |
| Sharing, export, writes, local execution | Separate approved flow under P12-T15/local execution; never granted by the above |

False, missing, malformed, unknown, company/local-only or withdrawn cloud consent
means unavailable. A tenant administrator or same-workspace member is not the
personal owner. Project-owner coordination does not consent on behalf of users.
Restoring consent/policy after withdrawal needs new epochs and a new read; old
in-flight attempts must not resurrect old content.

## Provider capability review

Reviewed official Yandex documentation:

- [API access/OAuth](https://yandex.com/dev/disk-api/doc/en/concepts/quickstart.md):
  `cloud_api:disk.read` reads the whole Disk, distinct from app-folder scope. A
  user-selected root is an application restriction, not one-folder provider scope.
- [Resource response](https://yandex.com/dev/disk-api/doc/en/reference/response-objects.md):
  path, modified time, MD5 and size are metadata. The reviewed contract does not
  establish an immutable revision bound to a conditionally fetched representation.
- [Download](https://yandex.com/dev/disk-api/doc/en/reference/content.md): request
  a temporary URL by path, then GET it with the same OAuth token. A sample download
  host is not a complete allowlist or a redirect/revision guarantee.

Therefore the delivered metadata transport remains metadata-only; the current
Yandex adapter cannot produce `ExactVersionAssertion` for durable admission.
This is an unestablished capability, not a claim that the provider can never
support one. Do not turn MD5/SHA-256, mtime, observation, URL, equal pre/post LIST
or successful GET into a provider revision. No production version authority is
registered. Temporary download URLs/tokens must not reach a browser or DTO.
A future conditional/snapshot adapter needs documented semantics, full bounded
representation verification, current file identity, pinned reviewed transport,
cancellation/redirect/resource cleanup and owner-local validation before use.
Metadata browsing can continue independently; durable insights cannot.

## Capture and current-authority bindings

`PrivateScopeBinding` is trusted application data for one original read: full
request/context (including read nonce and credential/root/processing fingerprints),
private catalog path key, exact personal owner and durable owner/connection/object
epochs, consent and policy revisions. Context is in-memory only, never dumped into
DTO/logs. Paths/names/credential keys remain outside the envelope.

`PrivateReadReceipt` adds the original trusted capture instant, extraction hashes/
parser, adapter-asserted provider version/semantics and optional stable provider
object ID. It must be issued by the future reviewed orchestration at the original
read, with source-bound control-plane values. It is NOT issued by a save request,
model, public JSON, retained DTO or a constructor alone. Never redate a receipt.
Unknown version forbids retention; unknown stable object ID remains null.

`PrivateAdmissionAuthority.authorize(request)` supplies a new `PrivateAdmissionState`
on every call. A future manager implementation must independently reload current
actor/membership/source.view, exact owner/tenant, connection/account/credential,
LIST+READ/root, cloud-processing AND separate retention consent, revisions and
all epochs. Return trusted UTC now, durable high-water and coherent quota usage;
advance clock high-water atomically even when denying. Release transaction/locks
before provider I/O. A stale coherent quota snapshot is not detectably fresh here.

`ExactVersionAuthority.verify(source, receipt)` revalidates the exact file after
initial authority and returns `ExactVersionAssertion`: same strict request/nonce,
raw hash, provider version/semantics/object identity and fresh trusted check time.
Those fields are assertions by trusted code, not proof from string comparison.
No default/fallback exists. The source adapter must already have bound the version
to the complete original representation; this step also requires a fresh matching
provider version. Initial authority does not grant a model or an external write.

## Implemented inactive preparation

`prepare_private_payload` in `app/services/file_workspace/private_admission.py`:

1. Verify source/evidence/batch lineage and original receipt identity; reject
   wrong types, source hash/parser/version or personal scope substitutions.
2. Freshly authorize and match the complete capture binding, independent explicit
   processing/retention booleans, UTC high-water and capture age. Construct/validate
   an explicit canonical DTO before asking the version boundary to do any work.
3. Ask the injected exact-version boundary, then freshly authorize again. Reject
   access/consent/epoch/path/root/account changes, rollback or aged-out capture.
   Version assertion must match the same request/read/hash/version/semantics/object
   and have a check time between the two fresh authority observations.
4. Derive creation time from final authority, preserve capture-relative expiry,
   project again and check actual canonical bytes against the FINAL quota snapshot.
5. Return preview bytes only. A shared cooperative 5-second deadline covers the
   entire call; cancellation propagates and ordinary failures are sanitized. No
   retry, partial output, clock read, DB, provider/model default, cache or logging.

Only synthetic injected implementations are supported in this checkpoint. The
receipt/authority/version graph can be consistently fabricated by trusted code.
Tests prove composition and rejection behavior, not real authority, version truth,
redaction, semantic entailment, secure memory erasure or storage admission.

## Durable writer implementation checkpoint

A later [default-off implementation](FILE_WORKSPACE_PRIVATE_IMPLEMENTATION.md)
adds the manager/receipt/control-plane/writer code under this interface. It has
no registered immutable-version provider or public activation. Local PostgreSQL
validation is recorded in the PR integration evidence. The requirements below remain acceptance gates, not test results.

## Durable writer transaction requirements

The returned bytes are NOT a reservation, save command, bearer grant or durable
access decision. Another request can consume quota or revoke access immediately.
A future manager must serialize final authorization/epochs/high-water, quota
reservation, idempotent insert/settlement and deletion/revocation fences in one
reviewed transaction, without holding locks over external I/O. Reuse existing
manager patterns; no ad-hoc sessions or process-local locks as a substitute.

Before activation: approved durable consent/control-plane schema and receipt
issuer; reviewed real exact-version capability; atomic quota/idempotency/fences;
expiry/purge/backup/restore and storage integrity; owner-local DB/integration/
security validation. No success here changes ROADMAP to READY automatically.
