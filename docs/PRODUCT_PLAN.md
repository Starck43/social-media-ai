# Product direction and extension contracts

The product is **AI Assistant**, repository `social-media-ai`. The monitoring
foundation is implemented in bounded slices; the expansion below is proposed,
not a claim of shipped features or production/legal certification.

Goal → authorized sources → collect/search → verify → analyze/calculate →
propose → approve when required → deliver/execute → follow up.

## Universal social tasks

The owner-approved first vertical and cross-surface contract are in
[universal tasks](AGENT_TASKS.md#explicit-task-plans). The AI chat is the primary
workspace for the full task lifecycle: request, clarification, preview,
execution, progress, results and follow-up. Dedicated source, scenario, task,
schedule and digest pages remain first-class, directly accessible management
surfaces. Preserve the existing design. P8-T29 delivered a shared task foundation
and VK collection slice; its remaining evidence gates are open. Follow-up work is
P8-T38–P8-T41, additional VK categories are P8-T33, and non-social connectors
P8-T34 are deferred beyond the social milestone.

The general capability-entitlement foundation is integrated through
[P2-T15](tasks/p2-t15.md), but its validation and independent acceptance remain
owner-deferred to project end; see [ENTITLEMENTS](ENTITLEMENTS.md). Catalog v1
grants all known capabilities on the existing plans, not paid-plan packaging.
Registered-user service-plan purchase and commercial connector packaging are
not shipped or authorized by that foundation. The P2-T16 configuration
foundation (YooKassa provider selection and env field contract) was accepted
2026-10-27 ([audit](audit/p2-t16.md)); the broader checkout and lifecycle scope
closed with that decision and is not tracked as an open task. Existing personal
LLM payer consent, balances and spend caps do not constitute self-service
subscription purchase or connector entitlements.

## Foundation directions

Choose one task from [ROADMAP](../ROADMAP.md), then its linked card. Phase plans in `docs/phases/` describe direction, active task links and short
accepted summaries; detailed historical evidence lives in `docs/audit/`. Release
criteria live once in [BUSINESS_PRODUCTION_READINESS](BUSINESS_PRODUCTION_READINESS.md).

| Direction | Parent | Release gate |
| --- | --- | --- |
| Execution truth and recovery | [P1 archive](phases/p1-queue-execution.md) | [Execution](BUSINESS_PRODUCTION_READINESS.md#prd-05--truthful-bounded-execution) |
| Usage, pricing and payer attribution | [P2](phases/p2-usage-spend.md) | [Economics](BUSINESS_PRODUCTION_READINESS.md#prd-03--complete-bounded-economics) |
| Identity, permissions and approvals | [P3](phases/p3-identity-access.md) | [Identity](BUSINESS_PRODUCTION_READINESS.md#prd-02--fail-closed-identity-permissions-and-approvals) |
| Tenant-safe delivery | [P4](phases/p4-delivery.md) | [Delivery](BUSINESS_PRODUCTION_READINESS.md#prd-01--tenant-safe-retry-safe-delivery) |
| Typed output and hostile content | [P5](phases/p5-ai-boundaries.md) | [AI boundaries](BUSINESS_PRODUCTION_READINESS.md#prd-07--structured-safe-ai-boundaries) |
| Retention, privacy and offboarding | [P6](phases/p6-lifecycle-privacy.md) | [Lifecycle](BUSINESS_PRODUCTION_READINESS.md#prd-06--data-lifecycle-privacy-and-offboarding) |
| Installation, operations and recovery | [P7](phases/p7-operations.md) | [Operations](BUSINESS_PRODUCTION_READINESS.md#prd-04--reproducible-deployment-and-recovery) |
| First value, diagnostics and support | [P8](phases/p8-first-value-ui.md) | [Visibility](BUSINESS_PRODUCTION_READINESS.md#prd-08--operational-visibility-and-support) |
| Controlled activation | [P9](phases/p9-safe-activation.md) | [Activation](BUSINESS_PRODUCTION_READINESS.md#prd-09--safe-action-activation-and-honest-release-claims) |
| Verified security/correctness findings | [P10](phases/p10-audit-hardening.md) | Task-selected gate |

## Expansion sequence

Each direction needs a pilot, bounded supported scope and its own task acceptance.
Existing source/channel capability never implies a new read/write capability.

| Direction | Outcome | Boundary before implementation |
| --- | --- | --- |
| Research | One additional web/video/mail source and an evidence-linked report | Authorized access, incremental sync, source coverage, dates and limitations; transcript analysis is not full audiovisual analysis |
| Knowledge | Search one approved folder/mailbox with page/cell/message references | Paired local connector, ACL-aware retrieval, extraction/OCR limits, freshness, revocation and deletion |
| Communication | Verified contacts and channel threads, draft reply, approved scheduled outbox | Ambiguous identities/routes require review; per-channel capabilities, consent, expiry, timezone, cancellation and no bulk invitations without supported API/opt-in |
| Follow-up | Evidence-backed unanswered-request, commitment, birthday and expiry reminders | Confirm uncertain dates; quiet hours, acknowledgements, snoozes and duplicate suppression; archive/delete only through approved retention preview |
| Cases | One configurable case from request through supplier comparison and documents | CRM/ERP are optional adapters; validate currency, tax, pack units, delivery, freshness and explicit missing prices; deterministic Decimal arithmetic, editable assumptions and domain rounding |
| Documents/approval | Versioned correspondence and remote decision on one document | Unsupported terms stay visible; edits invalidate approval; a product click is not a legally recognized signature; signature provider/callback/hash/identity checks need legal/security review |
| Scale | Repeatable managed service, then demand-led self-service | Measured queue age, p95 latency, parser/storage load, spend and recovery; fair tenant admission and DB/provider budgets before new infrastructure |

Suggested first vertical: authorized monitoring → analytical digest. Next comes
one demand-backed pilot, not all integrations. Track usefulness, evidence/citation
coverage, retrieval quality, successful completion, delivery duplication, cost per
outcome and time saved against representative fixtures. Preserve inspect/correct,
pause/revoke/cancel and eligible export/delete controls.

## Extension contracts

These are design constraints for new work, not existing SQL model declarations.
Keep the modular application/PostgreSQL queue; do not replace foundations without
a measured requirement. `AgentScenario` is methodology; `AgentTask` selects
execution/sources/targets. A multi-step workflow is a separate versioned concept.

- Interaction proposes plans; deterministic server policy owns authorization,
  scheduling, validation, budget and approved execution. Typed steps/checkpoints
  reuse jobs. Prompts and retrieved content never grant permissions.
- New connection/evidence/workflow/contact/thread/case/document/approval/audit
  entities need explicit schemas, ownership, version/native IDs, source/hash/time,
  retention and migration/rollback contracts; KV memory is not a business ledger.
- Connectors declare typed object/operation capabilities, credential owner and
  scopes; separate reads from sends/deletes. Require bounded paging/watermarks,
  rate/Retry-After handling, health/reauth/revocation, redacted partial/failed/
  unknown outcomes and cross-tenant/outage/retry fixtures. Prefer official APIs;
  browser automation is a reviewed fallback; MCP is optional. Untrusted customer
  code must not run unrestricted in shared runtime.
- Local connectors are optional, paired and outbound-authenticated. Expose only
  selected folders/types; show cloud transfer. Enforce canonical paths, symlink/
  traversal checks, ACLs, parser/size/time limits, sandboxing, device revocation
  and cache deletion; no unrestricted shell or public file server.
- Start retrieval with measured keyword/metadata quality; embeddings require a
  relevance/cost case. Filter tenant/actor/source ACLs before retrieval/model
  input; revoke cached access. Keep provenance/uncertainty and editable memory;
  mail/case/document storage needs a purpose-bound retention/backup/legal-hold
  policy distinct from staged monitoring raw data.
- Outbound lifecycle: draft → validated → approved/scheduled → claimed →
  confirmed/failed/unknown. Approval binds actor/tenant, exact recipient/payload/
  document version and TTL; recheck rights/consent/freshness at execution. Use
  per-target idempotency/outcomes; reconcile ambiguous timeouts, never blindly
  resend successful targets. Provide cancellation, expiry and kill switches.
- Deterministic services own arithmetic, joins, unit conversion and state.
  Preserve evidence, currency/tax/formula versions and assumptions; distinguish
  estimate/quotation/order/invoice. Approved/signed content is immutable.
- Keep sensitive content within its declared local/company/cloud boundary;
  local-only prompts/results must not transit cloud chat or fallback models.
  Separate API/workers/connectors only for measured load or security isolation;
  preserve coordinated scheduler/listener ownership.

Before a package: name the pilot, scope, data geography/access/retention,
role/consent matrix, spend limits, schema/API changes, fixtures and recovery.
Record the selected contract sections in the task card; no activation is granted
by a product proposal. Current code locations: [DESIGN](DESIGN.md).
