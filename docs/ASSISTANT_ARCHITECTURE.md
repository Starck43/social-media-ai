# Universal assistant: target architecture and extension contracts

## Status

Proposed architecture, 2026-10-09. No new tables, endpoints, local agent, connectors or workflow engine are implemented by this document. Read the [product plan](PRODUCT_PLAN.md) for outcomes and [business readiness](BUSINESS_PRODUCTION_READINESS.md) for existing release gates. The current PostgreSQL runtime is the starting point, not a system to rewrite wholesale.

## Boundaries

1. **Interaction:** messenger/web chat and future unified inbox. Converts intent into a preview and explains results. Does not grant permissions.
2. **Workflow orchestration:** persisted, versioned definition with typed steps, checkpoints and bounded execution. Reuses current schedules/jobs; LLM proposals are validated against allowed capabilities. Scheduling and admission remain deterministic.
3. **Connectors:** authorized reads/writes to external systems and a separate optional local connector. A read capability never implies a send/delete capability.
4. **Knowledge/evidence:** source references, versioned extracts, freshness and retrieval ACLs; original systems remain authoritative.
5. **Analysis:** structured extraction, summarization and recommendations; deterministic services perform calculations, joins, unit conversion and state transitions.
6. **Business context:** verified contacts, channel threads, events and configurable cases/documents. Domain templates add vocabulary and rules without changing the core.
7. **Policy and execution:** tenant and actor resolution, permission checks, budget admission, approval/outbox, retention, audit and kill switches. Enforced outside prompts on every execution path.

### Proposed concepts (not existing SQL models)

| Concept | Contract |
| --- | --- |
| Connection | Credential reference, owner, scopes, capabilities, health, expiry and revocation |
| Evidence reference | Source/object ID, URL or file locator, version/hash, observed time, access scope and extraction location |
| Workflow definition/run | Versioned inputs, steps, schedule, policy, execution state and step results |
| Contact/channel identity | Verified external identifiers; ambiguous merges require review |
| Conversation/message | Native thread/message IDs, participants, ordering, edits/deletes and attachment references |
| Case | Configurable process stages, participants, tasks, documents, decisions and external CRM/ERP mappings |
| Document version | Immutable content hash, author, input evidence, review/approval/signature state |
| Approval/outbox item | Exact versioned payload/recipient, initiating actor, approver, validity, scheduled time and delivery evidence |
| Usage/audit event | Every billed attempt or controlled action with tenant, actor, units, correlation and outcome |

These concepts require separate schema proposals with data minimization, migration/rollback and compatibility review. Existing `agent_memory` must not become an unstructured substitute for contacts, contract versions or a spend ledger.

## Connector contract

Each adapter declares supported object types and operations: search, incremental read, attachment read, send, archive/delete, event subscription or signature submission. Unsupported operations fail explicitly; MAX digest transport, for example, does not establish MAX history collection.

Required fields/behavior:
- tenant + actor context and credential owner; permission/consent checks before access;
- typed input/output, native IDs, source URL/location and observed/version time;
- cursor/watermark, paging, bounded history, rate limits and Retry-After handling;
- read/write capability separation and connector-specific validation;
- health states: missing, connected, renewable, reauth, throttled, unavailable, unsupported;
- normalized outcomes: success, partial, skipped, failed, unknown-external-outcome;
- revocation/deletion behavior, retention and redacted diagnostics;
- fixture tests for cross-tenant reads, revoked credentials, partial results, stale data and retry behavior.

Prefer official APIs and explicitly authorized sessions. Browser automation is a reviewed last resort, not a promise to bypass access controls or platform policy. MCP or an authorization broker is an optional adapter transport when it solves a demonstrated need; neither is mandatory nor permanently forbidden. Untrusted plugins/customer code cannot run unrestricted in the shared runtime.

## Local computer boundary

Ship a local connector only when required by a pilot. Pair it with an authenticated account/device, expose user-selected folders/file types and show whether extracted content leaves the device. Use outbound authenticated communication; do not expose an unrestricted shell or public file server.

Requirements: path canonicalization, traversal/symlink controls, per-folder permissions, file-size/parser/time limits, sandboxed extraction, malware/attachment handling, device revocation and cache/index deletion. Avoid secrets/system folders by default. Offline results must be labeled stale or unavailable. A local filename and authorized file reference are evidence, not permission to execute a file.

## Retrieval and memory

Start with bounded keyword/metadata search and measured extraction quality. Adopt embeddings/pgvector only when a representative relevance benchmark justifies it; count indexing and reindexing costs. Every retrieval filters tenant, actor and source ACL before content reaches an LLM. Revoked access must invalidate retrieval and cached results.

Store provenance and uncertainty. Distinguish user preferences, verified business records, external claims and model inferences. Content from websites, mail, files and previous model output cannot change permissions or become trusted tool instructions. Memory must be inspectable/correctable/deletable; do not claim perfect historical recall.

Current monitoring avoids a permanent raw archive. Future mail/case/document retention is a separate purpose-bound policy, not an accidental exception: define storage, evidence durability, expiry, export, legal hold, backup deletion and whether the system keeps a reference or a copy.

## Controlled outbound actions

Proposed lifecycle: draft → validated → awaiting approval → approved/scheduled → claimed → sent/confirmed, failed or unknown. Support cancellation and expiry. Product approval is not a legal signature.

Approval binds tenant, initiating actor, approving actor, exact recipient/channel, normalized payload/document version, scope and TTL. Revalidate permissions, consent, document version, source freshness and channel guards when execution begins; edits invalidate approval.

Use per-target idempotency and persisted outcomes. External APIs may not guarantee exactly once: after an ambiguous timeout, reconcile native message IDs/status where possible; otherwise show an unknown outcome rather than blindly resend. A completed successful target is not resent during another target's retry. Bulk invitations need deduplication, suppression lists, explicit opt-in and supported APIs. Give operators and users appropriate pause/kill switches.

## Calculations and document integrity

Extract candidate values with evidence; validate required units/ranges and ask about ambiguities. Use decimal arithmetic, explicit currency/tax conversion, versioned formulas and domain-specific rounding. Distinguish estimate, quotation, order and invoice. Preserve assumptions, calculation inputs and supplier validity dates.

A signed/approved document is immutable. New terms produce a new version and approval. Signature integrations verify callbacks, participant identities and document hashes; retain evidence per policy. Legal review decides which remote approval/signature method meets the intended use and jurisdiction.

## Scaling path

Keep a modular application and PostgreSQL queue first. Separate API and bounded workers when measured load requires it; preserve single scheduler/listener ownership or introduce tested coordination. Apply fair per-tenant work admission, DB connection budgets and source/provider concurrency limits.

Scale by measured queue age, source freshness, parser load, storage growth, p95 latency, spend and recovery objectives, not user count alone. Add managed/replicated storage, object storage, indexes, caches or a broker only with a concrete bottleneck and an architecture decision. Local connectors and sensitive parsers may require isolated execution earlier for security rather than throughput.

## Implementation and test policy

Each connector/workflow is a thematic PR from fresh dev, with explicit non-goals. Validate permissions, correctness, malformed/untrusted content, external outages, concurrent admission, delivery ambiguity, data lifecycle and upgrade compatibility. Do not use production credentials/data for tests. Historical test counts are not evidence for a new change. This plan authorizes no feature activation or schema migration.
