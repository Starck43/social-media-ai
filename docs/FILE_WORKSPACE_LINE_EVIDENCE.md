# P12-T12 — Source-bound plain-text line evidence

Parent: [P12-T12](tasks/p12-t12.md), [PR #327](https://github.com/Starck43/social-media-ai/pull/327).
Consumes the inactive [bounded-read seam](FILE_WORKSPACE_CONTENT_READ.md) and
[pure UTF-8 extraction](FILE_WORKSPACE_PLAIN_TEXT.md). No production wiring.

## Pure quoting contract

`build_line_evidence(source: ReadPlainText, *, ranges: tuple[LineRange, ...])`
returns immutable `LineEvidence` with exact `LineExcerpt` quotes. This pure
internal helper performs no read, authorization, model call, logging, persistence,
cache or KnowledgeSnippet projection. Existing generic knowledge anchors are
not reused as rights or stable provider identities. Source must be an application-
controlled completed read, NEVER a browser-supplied object or permission grant.

Ranges are exact typed, 1-based inclusive over canonical LF text. Require a
nonempty tuple, ascending disjoint ranges, exact integer endpoints (not bool),
start <= end and bounds within source.line_count. Adjacent ranges remain separate;
no merging, sorting, overlap removal, whitespace trimming or silent truncation.
Empty text has zero lines and no valid range. A trailing LF creates a final
empty line, which can be quoted explicitly. U+2028/U+2029 do not become LF lines.
Quotes join only the selected lines with internal LF separators; they do not
include an LF outside a range or invent one between disjoint excerpts.

Provisional synthetic budgets pending P12-T13 review: **8 ranges**, **32 selected
lines total**, **8192 Unicode code points total**, including internal LF but no
extra separator across distinct excerpt objects. These are not model-token,
provider, retention or production capability approvals. Reject overflow rather
than return partial evidence. Content—including instructions/HTML/URLs/formulas—
remains inert untrusted text. A future renderer must escape it; no renderer,
LLM prompt, hyperlink or executable interpretation is provided here.

## Source binding and verification

Evidence carries the entire frozen read context (tenant/actor/connection/object,
request nonce, path/name, root/policy/credential/processing fingerprints and
metadata observation), raw/text SHA-256, parser version, optional provider-version
assertion, canonical source line count and excerpts. All dataclasses are frozen,
slotted and hide all fields from default repr. Fingerprints must not be secrets.
No original URL or public citation token is generated.

The helper checks typed source shape, cloud boundary assertion and canonical
text consistency: strict helper round-trip, text hash, parser, counts and bounded
input size; raw hash must have lowercase SHA-256 shape. Raw bytes are absent,
so **it cannot recompute/authenticate raw_sha256** or prove actual provider read,
version identity, current access, consent or provenance. Optional provider version
is preserved only as the reader's assertion; None stays None. A metadata
observation/hash is not a provider revision or stable file ID.

`verify_line_evidence(evidence, source)` rebuilds exact quotes against the SAME
completed read and compares all evidence/binding fields. Mismatched quote text
or ranges, request nonce, actor/tenant/object/connection, context fingerprint,
raw/text hash, parser or version assertion invalidate reuse. Consistently selecting
a different valid range can produce other valid evidence; this is not a signature
over a previously approved selection. Equal canonical text in CRLF/LF
inputs does not erase the raw-hash distinction. Equal bytes/hashes in another
request do not merge source identities. Verification returns False for malformed
inputs; building raises only sanitized `line_evidence_unavailable`, without
chained parser/source exceptions in ordinary tracebacks. No automatic logs.

This is **consistency checking, not cryptographic authentication**: trusted code
can construct/replace both source and evidence. Replacing both consistently is
not detectable and does not grant anything. The typed source is an internal
trust boundary, not an external validation or anti-forgery scheme. Quotes are
not conclusions or proof a cited statement is true/relevant. No chain-of-thought.

## Integration gates and remaining work

No new access grant, share, workspace-wide retrieval, analysis tool, retained
insight or source refresh is enabled. Fresh actual authority and processing
permission are still required before future asynchronous/external release or
storage; verification does NOT reauthorize or revoke already delivered data.
No TTL/GC, snapshot/backup/latest-content promise or secure memory erasure.

P8-T34 owns real authorized exact-file reads/version integrity/network and
original-open. Its dev card records NOT ACCEPTED audit on `681cf525`; nothing is
copied or activated from that draft. P12-T13 owns budgets/capability/processing/
retention review; those gates remain open. P12-T15 owns sharing/approved writes.
Next analysis/insight generation and private storage need separate contracts,
mocked checks and later owner-local real-condition validation/audit.

Focused no-DB synthetic tests cover normalization/Unicode/empty-final-line and
boundary cases, range/aggregate budgets, tampered quotes/provenance/identities,
unknown versions, unsigned-source limitation, repr/errors, and the full injected
read → extraction → quote → same-source verification chain. See the
[task checkpoint](tasks/p12-t12.md) for exact tested SHA/command and deferred work.
