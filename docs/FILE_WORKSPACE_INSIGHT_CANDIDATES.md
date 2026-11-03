# P12-T12 — Mock compact insight candidates

Parent: [P12-T12](tasks/p12-t12.md), [PR #327](https://github.com/Starck43/social-media-ai/pull/327).
Inputs: [bounded read](FILE_WORKSPACE_CONTENT_READ.md) and
[source-bound line evidence](FILE_WORKSPACE_LINE_EVIDENCE.md).

## Inactive delivery boundary

`generate_insight_candidates(source, evidence, *, authority, generator)` is an
internal in-memory seam with **no default generator or authority**. This checkpoint
uses application-controlled synthetic/in-process doubles only. No real model,
provider, network, API route, tool, autonomous action, persistence, cache or
KnowledgeSnippet projection is registered. Protocol injection is not an egress
sandbox; trusted code could violate its contract. No real generator is delivered.

A future model adapter must reuse approved AI routing/admission/usage evidence and
reviewed prompt/output boundaries, not invent a parallel analyzer/billing path.
Ordinary READ/cloud-processing authority does NOT authorize external model egress,
paid calls, tools or spend. That requires separate model/processing consent,
provider/token/cost budgets and P12-T13 review. Character caps below are not token
or billing limits and do not approve any production capability.

## Sequence and authority

1. Verify evidence consistency against the same complete ReadPlainText.
2. Freshly invoke the injected existing ContentReadAuthority protocol for the
   original read request and require the identical context via public evidence
   verification. Actual actor/membership/source.view/personal owner/LIST+READ/
   root/secret/cloud-processing checks are a future authority implementation,
   NOT proved by mocks. Unknown/company/local_only assertions are rejected.
3. Build a fresh generation nonce and minimal typed CandidateGenerationInput.
   The generator receives only `(index, start, end, text)` for selected excerpts
   plus the nonce and a fixed `untrusted_data=True` marker. No full source text,
   paths/names, tenant/actor/object/connection IDs, hashes, secret fingerprints,
   read nonce or provider-version metadata. Selected text itself may contain
   sensitive content: minimization is NOT redaction or external-egress consent.
4. Invoke the injected synthetic generator once, with no retries/fallbacks.
5. Validate the exact response type, matching generation nonce and all candidate/
   reference/character budgets. Reject the entire batch on any failure.
6. Freshly authorize the same read request again and require unchanged evidence
   context before returning a frozen repr-hidden InsightCandidateBatch.

No DB locks or concrete managers are introduced here. Real authority wiring must
reuse current managers and reviewed processing consent; stale read evidence alone
never grants access. Context changes (including root/policy/credential/processing/
name/path/metadata/actor identities) deny release. This does not prove the remote
file is still at the same content version or recall data already released.

One **5s cooperative** proposal covers validation, both checks, generation and
final release checks. Explicit monotonic/pending-cancellation checks reject late
or cancellation-swallowing returns; no CPU/network preemption or protection
against malicious cancellation-state erasure is claimed. No resources/network
are owned by the synthetic generator. Future real adapter cleanup is separate.

## Typed output and proposed budgets

- Response: GeneratedInsightCandidates(generation_id, candidates), exact tuple.
- 0–5 InsightCandidate objects; zero is unverified abstention, not proof the file
  contains nothing useful or that an actual model analyzed it.
- Each candidate: nonblank exact str up to **600 Unicode code points**, canonical
  LF/TAB allowed, other category-C/CR/BOM rejected. No stripping, normalization or
  silent truncation. **1800 code points total** across the batch.
- Each candidate has **1–4 exact integer** evidence indices, sorted/unique and
  within the selected excerpt list; bool/float/string/URL/foreign references fail.
  No arbitrary source URL, substituted quote or provider identity is accepted.
- Results preserve the original evidence binding and optional provider-version
  assertion; unknown version stays None. No version is derived from a hash/name.
- Batch review_status is fixed **unverified**, not caller/generator-selectable.
  Input/excerpt/candidate/response/result dataclasses are frozen, slotted and hide
  fields from default repr. This is not cryptographic signing or secure memory
  erasure; trusted code can construct/replace objects. Do not log/serialize them
  automatically. No chain-of-thought is requested or retained.

Input excerpts and output candidates—including prompts, HTML, URLs, formulas and
instructions—are DATA, not system instructions, tools or grants. No prompt adapter
or renderer is provided; a future adapter must maintain structured untrusted-data
framing and a renderer must escape output. The marker alone does not prove a real
model resists prompt injection. No executable interpretation occurs here.

**Citations are structural references, not entailment/truth/relevance checks.**
A candidate can be wrong or unrelated while referencing an existing quote.
Tests deliberately preserve that counterexample as unverified. No automatic fact
promotion, accepted insight, public knowledge, sharing or downstream action follows.

Ordinary failures yield only `insight_candidates_unavailable`, with no chained
source/authority/generator details in ordinary tracebacks and no automatic logs.
External cancellation propagates; no partial batch or successful empty fallback.

## Integration and handoff

P12-T13 owns model/processing/capability/budget/private-retention review. Private
storage, quotas, owner/version lineage, TTL/GC, pin/backup and reauthorization on
future access remain unimplemented, not implied by an in-memory result. The
[draft private storage lifecycle](FILE_WORKSPACE_PRIVATE_STORAGE.md) proposes the
review/acceptance boundary only; it does not enable retention. P8-T34
owns real authorized exact-file reads/network/version/native-original. Its draft
transport is not copied/activated. P12-T15 owns sharing/approved mutations.

Synthetic tests include read → quote → candidate orchestration, minimization,
context/revoke checks, nonce replay, typed references, exact/plus-one budgets,
abstention/unsupported claims, errors/timeout/cancellation and repr/immutability.
No DB/provider/model, real ACL, ASGI/browser or full-suite acceptance. Exact SHA,
command, warnings and local handoff are in [the task checkpoint](tasks/p12-t12.md).
