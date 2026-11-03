# P12-T12 — Inactive private payload format v1

Parent: [P12-T12](tasks/p12-t12.md), [draft PR #327](https://github.com/Starck43/social-media-ai/pull/327).
Lifecycle: [private storage proposal](FILE_WORKSPACE_PRIVATE_STORAGE.md).

## Scope and trust boundary

Pure closed-schema validation and canonical serialization only. No storage,
DB/session/migration, clock read, provider/model/network, API/tool/renderer or
activation. Reviewable format/limits, not approved policy. P8-T34 still supplies
no exact-byte version assertion; real durable admission remains forbidden.

`serialize_private_payload(data)` accepts an exact built-in dict of JSON-shaped
application-owned values (exclusively owned, not concurrently mutated during
the call) and returns canonical bytes or a sanitized
`PrivatePayloadUnavailable`. `private_payload_is_canonical(payload)` accepts only
exact bytes, bounded before decode, and returns a boolean. It validates both
schema and byte-for-byte canonical representation, not provenance or integrity.
It never releases a parsed mapping or invents a fresh read from retained data.

No dataclass/asdict/context serialization: construct the allowlist explicitly or
use the inactive same-read projection below. Schema validation
CANNOT establish that these values came from that verification. Synthetic callers
can invent IDs, hashes, versions, times and epochs that satisfy this format.
A future writer needs independent trusted source/version/consent/epoch binding,
fresh manager-owned authorization, timing/high-water checks, atomic quotas,
fences and storage integrity. Neither helper is that writer or an access grant.

## Exact schema (all fields required, no additional keys)

Root keys: `schema_version`, `serializer_revision`, `scope`, `source`, `binding`,
`lifecycle`, `line_count`, `excerpts`, `candidates`, `review_status`.

| Field | Exact shape / proposed bound |
| --- | --- |
| schema_version | int `1`; bool/float/subclass rejected |
| serializer_revision | str `private-insight-json-v1` |
| review_status | str `unverified`, never caller-selected promotion |
| scope | dict: tenant_id, owner_id, connection_id, object_id: int 1..2147483647; root_key, path_key: lowercase 64-hex catalog keys |
| source | dict: provider_version: nonblank str <=512 UTF-8 bytes; version_semantics: revision token <=64 ASCII bytes; provider_object_id: null or nonblank str <=512 UTF-8 bytes; raw_sha256, text_sha256: lowercase 64-hex; parser_version: `utf8-plain-v1` |
| binding | dict: read_attempt_id, generation_attempt_id, owner_epoch, connection_epoch, object_epoch: lowercase 32-hex; consent_revision, policy_revision: revision tokens <=64 ASCII bytes |
| lifecycle | dict: revision: `private-retention-v1`; captured_at, created_at, expires_at: fixed UTC `YYYY-MM-DDTHH:mm:ss.ffffffZ` (27 ASCII bytes) |
| line_count | int 1..1000, matching the existing nonempty plain-text evidence contract |
| excerpts | exact list of 1..8 exact dicts with start, end (1-based inclusive ints), text (str); sorted/disjoint ranges within line_count; <=32 selected lines, <=8192 total code points including internal LF; LF count must match each range |
| candidates | exact list of 0..5 dicts with text (nonblank str <=600 code points, total <=1800), evidence_indices (list of 1..4 sorted unique 1-based int indices into excerpts) |

Every nested dict/list/str/int must be an exact built-in, not an arbitrary mapping,
iterable or subclass; no coercion, floats, NaN, pickle, arbitrary objects or hooks.
Revision tokens match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`. Opaque provider fields
reject Unicode category-C characters; no normalization/stripping/truncation.
Quote/candidate text preserves canonical LF/TAB and rejects other category-C
characters (including CR/BOM/surrogates), as existing extraction/candidate code.
An empty quoted line is valid. Quotes need not be nonblank; candidates must be.

Time shape: capture <= creation <= capture +5 minutes, fixed expiry = capture
+24 hours. No sliding TTL. This only checks internal consistency of supplied
historical times; it cannot establish trusted capture, freshness at actual save/
read, current time or durable clock rollback protection. Current policy revision
and revocation epochs are not proved by syntactically valid strings.

`provider_object_id=null` honestly means unknown stable identity; version is
required and never replaced by a hash/mtime/catalog observation. `version_semantics`
is a revision label, NOT evidence of adapter review or exact-byte binding.
No credential/account fingerprint, display name/path, full source text, URL
field, prompt/response, chain-of-thought, raw bytes or arbitrary provider payload
is allowlisted. Selected text and opaque values can themselves contain secrets;
format validation is NOT redaction. Treat all content as untrusted DATA and never
render/execute/log it automatically. Structural references are not entailment.
No content-based global identity, ACL, shared KnowledgeSnippet or knowledge
promotion is derived here.

## Canonical bytes and accounting

Check fixed shapes/counts/field bounds BEFORE encoding. Fixed schema limits
allocation; unbounded input strings/containers are rejected before traversal or
UTF-8 conversion. Encode with sorted keys, separators `(',', ':')`,
`ensure_ascii=False`, `allow_nan=False`, strict UTF-8, no BOM/newline/padding.
String contents and list order are preserved exactly, without NFC/case folding.
The serializer revision pins this Python JSON representation, not a general
cross-language canonical JSON standard. Root and nested mapping insertion order
do not matter. All unsupported revisions fail closed; no migration/fallback.

Complete envelope <=65536 logical UTF-8 bytes. Charge **len(returned_bytes)**
outside the envelope: `charged_size` is not an allowed payload field, avoiding
self-referential accounting. Current per-field budgets may bind earlier than the
envelope cap. [Quota arithmetic](../app/integrations/file_workspace/retention_quota.py)
remains separate and is not atomic admission. Decoder rejects oversized/empty/
invalid UTF-8, malformed/deep JSON, duplicate keys, nonfinite numbers, unknown
fields and any noncanonical bytes (whitespace/order/escapes/precision included).

Failures expose only `private_payload_unavailable` or False, without automatic
logging/chained exceptions. As elsewhere, traceback-local capture/memory erasure
is not a guarantee; callers must not log inputs/locals/results. No public
DTO-to-storage route is introduced. Review, ORM reservations/idempotency/fences/
GC, real exact-version support and owner-local audit remain separate gates.


## Inactive same-read projection

`serialize_same_read_payload(source, batch, *, metadata)` in
`app/integrations/file_workspace/private_projection.py` returns canonical DTO v1
bytes or the same sanitized `PrivatePayloadUnavailable`. It is pure and unregistered:
no authority/model/provider call, clock, persistence, route or storage activation.

The helper verifies `batch.evidence` against `source` with the existing full-context
same-read verifier (request nonce, IDs, context, hashes, parser, version, exact line
ranges/text). It requires an exact unverified candidate batch, a bounded generation
nonce and exact bounded candidate/reference tuples. The existing DTO validator
checks all projected field types, budgets, references and lifecycle syntax.
Missing provider version fails closed, even when evidence also lacks a version.

Projection derives scope IDs/root from the read context, hashes/parser/version/
line count from extraction, read nonce from the request, and generation nonce,
excerpts and candidate references from the batch. `owner_id` is the read actor in
this personal-source convention; that mapping does not prove personal ownership.
It never dumps a context or accepts replacement scope/source/binding/candidate data.

`metadata` is an exact dict with only `path_key`, `version_semantics`,
`provider_object_id`, `owner_epoch`, `connection_epoch`, `object_epoch`,
`consent_revision`, `policy_revision`, and `lifecycle` (the existing DTO lifecycle
shape). These values are supplied assertions, NOT independently bound to a path,
provider, current authority/consent, epochs or trusted time. No policy fingerprint
is substituted for a policy revision. No hash or observation replaces a version.
Inputs must be exclusively owned trusted application objects, without concurrent
mutation; no source text, display path, credential key or arbitrary metadata is
included in the output except explicitly selected untrusted quote/candidate text.

This narrows accidental cross-read substitutions; it cannot authenticate an entire
forged graph, prove that a generator emitted the batch/generation ID, establish
entailment, verify exact-byte provider semantics or grant processing/storage rights.
A consistent forged raw hash still passes: the original bytes are absent. Changing
all linked objects consistently is outside this consistency guarantee. The helper
is not a writer, redactor, current-access check or trusted durable admission path.
Real source/version/identity/consent/epoch binding and P12-T13 remain prerequisites.
