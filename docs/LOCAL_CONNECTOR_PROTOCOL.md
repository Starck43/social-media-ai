# Local connector protocol — draft v1

[Task](tasks/p18-t01.md) · [Phase](phases/p18-local-file-connector.md) ·
[Product boundaries](PRODUCT_PLAN.md#extension-contracts) · [Business review](BUSINESS_MODULES.md#review-findings-corrections-to-the-tz)

## Status and non-goals

This is a proposed security contract, not implemented behavior, a wire-standard
or permission to enable a connector. P12-T12's delivered increment is integrated:
[PR #327](https://github.com/Starck43/social-media-ai/pull/327) merged as
`48caea61`, accepted source `6433ae6a` per [its audit](audit/p12-t12-increment.md),
with the plans of PR #307/#308/#311 in `dev`. This draft is reconciled against
that accepted catalog/admission revision; the broader P12-T12 roadmap is still
IN_PROGRESS, so anything outside the delivered increment stays unproven here.
D6 (desktop stack/signing) is open; nothing here selects an implementation stack.
P18-T02/T03/T05/T06 must implement and validate their respective portions; this
paper is not authorization to expose routes, migrate data or deploy.

The existing `LOCAL_GATEWAY` catalog method contains a certificate-fingerprint
field; it is a descriptor, not proof that enrollment, certificates or a gateway
exist. Connector-to-cloud traffic is outbound only. No public or inbound file
server is required; any local UI is separately protected and loopback-only.
There is no general shell, arbitrary URL fetching, source-file mutation or
provider tunneling in v1.

## Trust and privacy boundaries

The connector acts for one paired personal owner in one tenant, on explicitly
selected local roots and subject to current OS access. Tenant membership, an
administrator role, a fingerprint, a matching path, or the presence of a device
is not itself a grant to read files. The cloud must authenticate the human, verify
current tenant membership, personal ownership, connection, policy and operation
rights at issuance and again before release; the device independently enforces
its local selection, policy and OS access on execution. Missing or unavailable
authority fails closed. IDs are opaque, tenant/device scoped and non-authorizing.

`local_only` data, including paths, filenames, metadata, queries, snippets and
file bytes derived from it, MUST NOT transit the cloud or cloud chat/model, even
when explicitly requested; execute locally or report unavailable. Metadata and
snippets from other boundaries may leave only with explicit, current, scoped
cloud-processing consent and policy. An explicit request to fetch does not
change the data boundary or grant processing, retention, model egress, sharing,
external delivery or source-write permission. File contents are not uploaded
except by an individually authorized `file.fetch`. Snippets are content, not
anonymous metadata; searches must never return a whole file disguised as a
snippet. Surface the intended destination and kinds of data to the user before
consent. No silent boundary downgrade or permissive fallback.

## Enrollment and pairing

1. An authenticated owner selects a tenant and begins pairing in Settings. Server
   creates an opaque pairing ID and high-entropy, single-use secret with a short
   TTL (proposed 5 minutes), stored only as a keyed verifier. Code is shown once;
   never in URLs, logs, analytics or error text. Apply per-account, per-code and
   network rate limits and lock/expire on repeated failures. Pairing is cancelled
   on owner, membership, policy or session loss. No long-lived shared API key.
2. Connector generates a device private key locally using an OS-protected key
   store where available, creates a CSR with proof of possession, and sends the
   pairing ID, secret and CSR over server-authenticated HTTPS. Validate TLS host,
   certificate chain, CSR signature, allowed key algorithm and size. Reject CSR
   identity/extensions supplied by the device; the server assigns device identity.
3. Possession of a code alone is insufficient against code interception. Before
   issuing, show an independently obtained short fingerprint of the device public
   key on both the local UI and the authenticated Settings session; the owner
   must compare and confirm it within the TTL. Bind confirmation to the exact
   pairing ID, tenant, owner and public key. It is not device attestation; a
   compromised UI or owner session remains a risk. Re-pair after any mismatch.
4. Atomically consume the code and confirmation, recheck owner/tenant rights,
   register the device/public-key fingerprint and issue a short-lived client
   certificate bound to that exact device, tenant and owner. Concurrent redemption
   can issue at most one identity. Enrollment/issuance failure must not leave a
   usable unregistered credential. A lost response is resolved through an
   authenticated enrollment status; never issue a second identity by replay.
5. Connector validates the issued certificate and server identity. It stores no
   pairing code after enrollment. Renewal requires proof of the existing private
   key, an active non-revoked registration and current owner/tenant policy;
   key rotation creates a new binding and invalidates the old one atomically.
   Lost key, expiry or failed verification requires re-pairing. Define CA/key
   rotation, expiration, recovery and audit procedures before implementation.

## Channel and authenticated envelopes

Connector initiates TLS to an allowlisted cloud hostname with standard certificate
and hostname verification, then mutual TLS with its registered certificate. It
uses an outbound WebSocket; outbound HTTPS long-poll is a fallback with the same
identity, authorization, request/replay and result rules. No cloud callback to a
local listening port. Disable insecure redirects, mixed content and TLS fallback.
The server rechecks current registration/fingerprint on every channel admission
and operation, not merely certificate validity. A load balancer terminating mTLS
must pass authenticated identity over a mutually authenticated, integrity-protected
internal channel; never trust public client-supplied identity headers.

Every server request is a versioned, closed-schema signed envelope containing
protocol version, server key ID, device/tenant/owner and authenticated actor IDs,
operation and typed arguments, random request ID, issuance and expiry times,
root/object scope, policy/consent epochs, effective resource limits, and a hash of
the complete canonical payload. Sign all fields using an approved server key;
verify signature, key ID, audience, device binding, strict types, canonical bytes,
limits and freshness *before* interpreting arguments. Choose and document a
reviewed canonical encoding, signature suite, key distribution/rotation and
clock-skew bound before any implementation; TLS alone does not establish the
actor's current authorization. Unknown version, key, field, operation, malformed
encoding, duplicate keys, expiry or unverifiable time is rejected, never downgraded.
Do not trust actor IDs supplied by an end user or embedded in file content.

Responses bind the same request ID, device, version and request digest, with a
typed outcome and bounded payload; accept only on that authenticated device
channel and recheck current rights, consent, epochs and boundary before release.
Neither a signed request nor a stored response is a continuing authorization.
Device registration and request/replay state must survive process restarts.
Record request ID and digest in a bounded durable replay ledger scoped to device;
a duplicate with different digest is rejected. Same-ID retries return only a
previously committed, still-authorized result or a truthful `unknown` outcome;
never execute a destructive operation twice. Lost connection, timeout, restart,
revocation or missing ledger state must not turn an ambiguous outcome into success.
Do not silently issue a new request ID to retry `index.delete` or `file.fetch`.
Ledger TTL, capacity, clock rollback and crash consistency require review; if a
safe replay window cannot be established, deny execution, not replay optimistically.

## Operations and resource ceilings

Only the following operations are recognized. All require the envelope and current
scope checks above; policies must independently enable each capability. Values
below are conservative **proposed maximums**, not existing deployed limits or
owner-approved entitlements. Per-request policy may narrow, never expand them.

| Operation | Allowed result / required scope |
| --- | --- |
| `health` | Bounded device availability and protocol version; no paths or file content. |
| `index.status` | Bounded status for selected roots the actor may currently view; no index dump. |
| `search` | Bounded query over explicitly selected and authorized roots, filtering boundary and access before retrieval; metadata plus limited snippets only, never a full file or index. |
| `file.metadata` | Bounded metadata for one opaque object under a selected authorized root; no file bytes. |
| `file.fetch` | One object, only after a specific human request for that object and destination, freshly authorized against exact scope and boundary; no batch, background, model-triggered or implicit fetch. |
| `index.delete` | Explicit user-confirmed deletion of the selected local index scope, not original files. Separate delete permission; durable idempotency and deletion fence. |

Proposed ceilings: 64 KiB total control envelope, 4 KiB query, 50 search hits,
512 Unicode code points per snippet, at most 8 snippets per response, 64 KiB
serialized metadata/search response, 10 MiB total file bytes per explicit fetch,
and 30 seconds per operation including transfer. UTF-8 byte limits count the
whole serialized payload, not just visible characters. Reject oversized input
before allocation; bound decompression and parsing. No silent truncation that
misrepresents results; report partial or limit-exceeded distinctly. Fetch may
stream with byte/time limits but must not treat partial bytes as complete: cloud
stages privately, checks actual length and current authorization/boundary before
release, deletes staged bytes on failure, and does not claim rollback of bytes
already delivered. Concurrency, queue length, per-owner/device rate and idle
limits must be set before activation. No network I/O while holding DB locks.

Opaque object IDs resolve only through the selected root on the device. Resolve
canonical paths and enforce containment at use, not just discovery; reject `..`,
absolute path substitution, links/reparse points or mount changes escaping roots,
unsafe special files and OS ACL denial. Defend against symlink and rename races
with handle-relative/no-follow operations and revalidation as supported by the
platform; where safe enforcement cannot be established, deny. No arbitrary
path supplied by the server is authority. A source may change during transfer:
report stale/unknown rather than asserting an immutable exact version from mtime,
a path or a hash. Reuse the integrated P12-T12 admission, owner, consent and
exact-version contracts before any durable retention; no unknown version fallback
or implicit knowledge projection. The accepted P12-T12 increment delivers the
catalog and bounded text admission, not an `ExactVersionAssertion` for every
adapter ([admission contract](FILE_WORKSPACE_ADMISSION.md)). `file.fetch` therefore
requires a reviewed exact-version issuer for the target; where none exists the
object is ineligible, never served under a weaker version claim.

## Revocation, deletion and failure

Owner disables a device in Settings. Server atomically marks its binding revoked,
advances a durable device/authorization epoch and invalidates queued requests and
credentials. After commit, new handshakes and operations with the revoked key are
rejected even if its certificate remains cryptographically valid. Close existing
channels, cancel in-flight work and recheck epoch/rights before any release;
serialize final authorization with revocation where needed. Distributed gateways
must use a current authoritative revocation check, not a stale cache as authority;
if unavailable, deny. Revocation cannot recall bytes already delivered before
commit, guarantee physical erasure or stop a compromised offline device.

On receiving a verified revocation notice, a compliant connector disables its
channel, removes local credentials/tokens as its OS allows, cancels work and
requires new pairing. Offline notice is not guaranteed: server-side denial is the
security boundary, and wiping a hardware key or backups cannot be promised. Index
and source-file deletion are separate, explicitly described user actions. A local
index delete cancels active index operations, advances a durable generation fence,
removes index and derived cache for the selected scope and verifies completion;
source originals are never deleted. Do not claim completed erasure when offline,
interrupted, backed up or unsupported. Re-pair/re-consent cannot revive old
requests, results or epochs. Recovery and backups must preserve revocation and
deletion fences or fail closed until reconciled.

Use typed, sanitized errors (`denied`, `unavailable`, `expired`, `limit_exceeded`,
`stale`, `unknown`) without revealing unauthorized object existence. Logs/audit
may contain bounded operation/outcome and opaque correlation identifiers only;
never codes, credentials, private keys, raw paths, snippets, file contents or
query text. Telemetry retention and physical backup deletion need separate policy.

## Threat model and acceptance handoff

| Threat | Required defense / residual limitation |
| --- | --- |
| Stolen device/key | OS key protection, short cert, active revocation and owner alerts; unlocked compromised host may still read what the user can. |
| Stolen pairing code or hostile CSR | Time/rate limits, proof of possession, independently confirmed fingerprint, atomic redemption; compromised owner session/UI is not solved. |
| Malicious or compromised server message | mTLS plus signed closed envelopes, actor/scope/operation checks, replay ledger, local root/OS restrictions and limits; a compromised authorized signer remains constrained by local policy, not magically trustworthy. |
| Cross-tenant/actor substitution, revoked access | Bind identities and epochs; independently recheck at issuance, execution and release; fail closed on unavailable authority. |
| Traversal, symlink/TOCTOU, hostile parser input | Handle-relative containment, OS ACL and special-file checks, isolated bounded parsing; deny on unsupported safe semantics. |
| Prompt injection or secrets inside files | Treat content as inert untrusted data; it never authorizes tools or changes policy. Snippets can contain secrets; consent/limits do not provide redaction. |
| Replay, interrupted delete/fetch or lost acknowledgement | Persistent digest-bound idempotency, fencing and explicit unknown outcomes; no blind replay or false completion. |
| Cloud or offline device after revoke | Current server rejection and release recheck; no promise to recall already released bytes or erase an unreachable device. |

Before READY: independent protocol/security review; confirm current
owner/consent/catalog/version semantics still match the accepted P12-T12 revision
as its roadmap items land; resolve
wire encoding/algorithms, trust anchors, key lifecycle, ledger bounds, failure and
OS-path mechanics. P18 implementations must test pairing races, code expiry and
key substitution; cross-tenant/owner denial; all allowlist and size boundaries;
local-only non-egress; revoked live/offline channels and in-flight release races;
replay/crash recovery; traversal/symlink races; and hostile content/log redaction.
No document-only check demonstrates these runtime properties.
