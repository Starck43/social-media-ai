# P12-T12 — Inactive bounded plain-text read seam

Parent: [P12-T12](tasks/p12-t12.md); [PR #327](https://github.com/Starck43/social-media-ai/pull/327).
Builds on the [pure UTF-8 helper](FILE_WORKSPACE_PLAIN_TEXT.md).

## Scope and trust boundary

Internal mock-only orchestration, not a production connector or authorization
implementation. There is no default authority/reader, route, UI action, scheduler,
model, persistence, cache or KnowledgeSnippet projection. All inputs/outputs stay
in process memory; memory lifetime is not secure erasure. Limits are defensive
synthetic proposals pending P12-T13 review, not accepted provider/model budgets.

`read_plain_text` accepts positive integer tenant/actor/connection/catalog-object
IDs and application-injected `ContentReadAuthority` and `ContentReader`. It accepts
no caller URL, path, filename, bytes, version or permission assertion. Every call
creates a fresh nonce. Protocol objects must never come from browser/request data.

A future authority MUST independently check active actor, membership, source.view,
personal connection ownership, LIST **and** READ, active secret/account, current
root/restrictions and explicit cloud processing permission on EVERY call. READ
alone is insufficient. Unknown/local_only/company boundaries are denied here;
this is not a paired local-execution route. Use existing ORM/managers for future
wiring, without DB locks held across stream work. No such authority is delivered
or certified by synthetic tests. Original-open permission is not processing consent.

The authority returns a frozen request-bound context including display name,
path, root/policy/credential/processing fingerprints and metadata observation.
Fingerprints must not contain raw secrets. Metadata observation is not a content
revision. Before opening a reader the service validates context shape, cloud
boundary and a supported safe `.txt` display name using the existing helper.

## Bounded streaming and release

1. Authorize the newly created request.
2. Open the trusted reader with a 262144-byte cap inside an async context manager.
3. Require the exact context, `complete is True` and an exact integer decoded full
   representation length from 0 through 262144; reject range/partial/unknown-size
   representations. An optional provider version is nonempty, category-C-free
   text up to 512 characters, never inferred from hashes, name or metadata.
4. Consume exact `bytes` chunks, at most 1024 including empty chunks. Before
   extending the bounded buffer, reject cumulative bytes beyond the declared
   full size. Require exact equality at EOF. No silent truncation, retries or
   incomplete success. A reviewed adapter must independently bound network,
   decompression and individual chunk allocation; post-receipt validation cannot
   prevent an injected adapter from allocating oversized data first.
5. Close the reader on success/failure/cancellation BEFORE parsing or rechecking.
6. Run strict UTF-8 extraction, then freshly authorize the SAME request again.
   Deny malformed/changed context, including credential/policy/processing/root/
   name/path/metadata changes. Release only immutable repr-hidden text/provenance.

One **5-second cooperative** budget covers both authority calls, opening,
streaming, closing and parsing. Monotonic checks after awaited phases and parsing
reject synchronous overruns or swallowed timeout cancellation before release.
Pending task cancellation also propagates if an adapter swallows CancelledError
without clearing cancellation. This is not hard CPU/network preemption; malicious
adapters that erase cancellation state or refuse to finish cleanup are outside
the trusted protocol. Reader context managers own resource cleanup even on errors.

All ordinary failures map to `plain_text_unavailable`, without chained provider,
policy or decoder exceptions in ordinary formatted tracebacks. No automatic logs.
Do not log frames with locals, serialize results automatically or treat returned
text as executable instructions. Explicit external cancellation propagates; no
partial result is returned. Errors do not prove absence of a file or permission.

## Provenance and limits of the assertion

`ReadPlainText` contains context, the pure helper's raw/canonical hash result and
optional `provider_version`. All lineage/result dataclasses are frozen, slotted
and omit all fields from default repr. The raw byte buffer is not returned.

A future P8-T34 reader MUST live-revalidate the exact owner-bound file and bind
any version to the exact complete bytes read. Stream shape/equality cannot prove
a synthetic or dishonest adapter performed that work. No production reader or
version mapping is registered. A supplied version remains an adapter assertion,
not a verified provider guarantee, latest-content claim or stable file identity.
Missing version remains None even when a hash/metadata observation exists.

Equal hashes do not merge owners/requests or authorize cross-tenant reuse. No
backup, retained insight, shared knowledge, snapshot, post-release revocation or
safe original-open guarantee follows. Future persistence must independently
recheck authority and have reviewed tenant/owner/version lineage, retention,
quota and GC contracts; this seam authorizes none of it.

## Ownership and checks

P8-T34 owns reviewed network/credentials/full exact-file read/version integrity
and original-open. P12-T13 owns capability/processing/retention/limit review;
P12-T15 owns policy editing/sharing/approved writes. The shared engineering
interface and conservative inactive-preparation limits are now settled in the
[admission contract](FILE_WORKSPACE_ADMISSION.md); real authority/version/consent
implementations and activation checks remain undelivered.

Focused no-DB tests use only synthetic authority and async context-managed
streams. Cover stage order, IDs/context/nonce binding, unsupported boundaries,
changed/revoked authority, empty/partial/oversized/chunk-limited responses,
parser rejection, version absence/assertion, errors/timeout/cancellation, cleanup,
repr/immutability and no DB/network/model imports. These tests do not validate
real ACL SQL, provider integrity, transport cleanup or real-condition acceptance.
See [the task checkpoint](tasks/p12-t12.md) for exact tested SHA/evidence.
