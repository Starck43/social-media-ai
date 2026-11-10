# Business modules P13–P19: adapted specification and review

[Product direction](PRODUCT_PLAN.md#business-modules) · [Phases](phases/README.md) ·
[ROADMAP](../ROADMAP.md#p13) · planning task [P13-T01 accepted](audit/p13-t01.md) · owner decisions [Q-18](audit/q-18-owner-decisions.md)

The owner's technical specification (TZ, 2026-11-03) adds six business modules
with an owner estimate of 26–33 weeks. This document is the canonical,
repository-adapted version of that TZ: it keeps the owner's goals, functional
scope and acceptance intent, and corrects details that conflict with the current
architecture, security contracts or provider facts. Module-level requirements,
data, API and acceptance live in the linked phase plans; task cards carry the
step-by-step plans. Nothing here is a claim of shipped behaviour, activation or
production readiness.

| TZ module | Phase | Owner estimate | Plan |
| --- | --- | --- | --- |
| Shared requirements | P13 | (spread across modules) | [Business-module foundation](phases/p13-business-foundation.md) |
| 1. Bitrix24 Integration | P14 | 3–4 weeks | [Bitrix24 CRM](phases/p14-bitrix24-crm.md) |
| 2. Unified Communication Hub | P15 | 5–6 weeks | [Unified communication hub](phases/p15-communication-hub.md) |
| 3. Task & Calendar Management | P16 | 4–5 weeks | [Planner and calendars](phases/p16-planner-calendar.md) |
| 4. 1C:Enterprise Integration | P17 | 4–5 weeks | [1C:Enterprise](phases/p17-onec-erp.md) |
| 5. Local File Connector | P18 | 6–8 weeks | [Local file connector](phases/p18-local-file-connector.md) |
| 6. Document Management | P19 | 4–5 weeks | [Document management](phases/p19-document-management.md) |

Week estimates are the owner's TZ figures, not repository promises; queue
complexity (`C1`–`C5`) in ROADMAP remains the engineering estimate.

## Sequencing

TZ order is kept: 1 → 2 → 3 → 4 → 5 → 6, with 4 (1C) and 5 (local files) allowed
in parallel. Two corrections make the order executable:

1. **Shared foundation first (P13).** Protected personal-data fields, the contact
   core, rate limiting, the business write-approval workflow, audit codes, API
   conventions and the coverage gate are prerequisites of every module. Building
   them inside Bitrix24 would make modules 2–6 depend on CRM internals.
2. **Bitrix24 ↔ communications cycle removed.** The TZ makes module 1 depend on
   module 2 for contact linking and delivery while module 2 is scheduled later.
   The contact core moves to P13; Bitrix24 delivers connection, read-only sync,
   contact cards, upcoming events and greeting *drafts* first (P14-T01..T04).
   Greeting sending and batch messaging (P14-T05/T06) wait for the persistent
   outbox and channel senders of P15.

```
P13 foundation ──► P14 Bitrix24 read/drafts ──► P15 hub (ingest, identities, inbox, outbox, senders)
                                                   │          └──► P14-T05/T06 sending, batch
                                                   ├──► P16 planner + calendars
                                                   └──► P19 documents (delivery via outbox)
P13 ──► P17 1C (parallel)          P13 + P12-T12 ──► P18 local connector (parallel)
```

Agent lanes, waves and merge rules: [P13 execution waves](phases/p13-business-foundation.md#execution-waves-and-agent-assignment-p13p19).
Do not start all modules at once. Test each module in isolation (mocked
providers) before integrating it with another module.

## Mapping TZ concepts to the repository

| TZ concept | Repository convention / existing owner |
| --- | --- |
| `tenants`, `tenant_id UUID` | Workspace = `tenants`; models use `TenantScopedMixin`, integer primary keys and `settings.DB_SCHEMA`; enforcement lives in managers ([TENANCY](TENANCY.md#how-it-works)). UUID primary keys are **not** introduced. Use opaque public IDs only where a URL must not be enumerable. |
| `*_connections` tables with `access_token`/`refresh_token` columns | One `workspace_connections` row per account plus a separate Fernet-encrypted `workspace_connection_secrets` row ([connector contract](CONNECTOR_CONFIGURATION.md#storage-boundaries)); provider/method/field contracts in `app/integrations/catalog/`. No module-specific token columns. OAuth state/refresh build on P12-T14 connector authorizations once [PR #308](https://github.com/Starck43/social-media-ai/pull/308) is integrated. |
| "encrypted vault" | Same as above; deployment OAuth client secrets stay in deployment configuration. |
| Periodic sync (15 min, 5 min) | Typed jobs owned by the single `python -m app.runtime` scheduler/worker ([queue](AGENT_TASKS_QUEUE.md)); Celery/Redis stay frozen. |
| `audit log` tables per module (`onec_audit_log`) | Tenant-scoped append-only `action_audit_events` with allowlisted `domain.operation` codes ([ACTION_AUDIT](ACTION_AUDIT.md#minimal-event-envelope)); business state stays in its own ledger (approval/outbox/command rows), the audit stores references only. |
| "approval workflow for write operations" | Preview → approve → separately admitted execute, bound to actor, tenant, exact payload hash and TTL, rechecked at execution, off by default per capability ([ACTION_ACTIVATION](ACTION_ACTIVATION.md#user-and-authorization-contract), [outbound lifecycle](PRODUCT_PLAN.md#extension-contracts)). Generalized in P13-T04. |
| `tasks`, `/api/v1/tasks` | **Name collision**: `agent_tasks` and `/api/v1/tasks` already mean scheduled agent work ([AGENT_TASKS_QUEUE](AGENT_TASKS_QUEUE.md)). User to-dos use the `planner` namespace: `planner_tasks`, `/api/v1/planner/tasks`, `/app/planner` (P16). |
| `documents`, `document_versions` | Extend existing `knowledge_documents` / `document_revisions` (migration 0122, P12-T03/T11) instead of a parallel table set (P19). |
| `unified_contacts`, `bitrix_contacts` | Shared contact core: `contacts` + `contact_identities` ([CONTACTS](CONTACTS.md); [P13-T07 audit](audit/p13-t07.md)). Bitrix24 entities are a source of identities, not a separate contact system. |
| Outbox, follow-ups | P12-T04 delivered pure state-machine/quiet-hours services; P15 persists them and adds per-target delivery. |
| Knowledge/evidence boundary | `data_boundary` = `local_only` / `company` / `cloud` (P12-T03/T11); local-only content never transits cloud chat or fallback models. |
| REST API | Existing FastAPI app under `/api/v1` (`app/api/v1/`) with OpenAPI generation; web pages under `/app`. Route tables are generated in [docs/generated](generated/README.md). |

## Shared requirements

| TZ requirement | Adapted contract | Task |
| --- | --- | --- |
| Encrypt all personal data with Fernet | Field-level Fernet (`MultiFernet` for rotation) for personal identifiers and free-text personal fields; a keyed HMAC **blind index** for exact-match lookup/dedupe of normalized email/phone/usernames (Fernet ciphertext is randomized and cannot be compared or indexed). Separate PII key(s) from `CREDENTIALS_KEY`, rotation script, redaction in logs and errors. Content that must be full-text searchable is governed by owner decision D3. | [P13-T02 accepted](audit/p13-t02.md) |
| Tenant isolation | Every new model is `TenantScopedMixin`, uniqueness constraints include `tenant_id` (and the connection where external IDs are per account); managers only; cross-tenant fixtures for every route and job. **One owner-authorized exception (2026-11-04):** a platform-wide row may omit `tenant_id` only when it holds no customer data and is global *by nature* — quoting one provider account's remaining budget, one connector's health, one cache's expiry. Such a row names the reason in its docstring, holds no personal or customer content, and a task that needs it says so at planning time instead of requesting an exception at review. A row a subscriber can see through any surface is never covered by this exception. | all; fixtures in [P13-T06](audit/p13-t06.md) |
| Audit logging of all operations | Allowlisted audit codes per module for writes, approvals, executions, sync runs and denials; reads that expose bulk personal data (exports, 1C queries) are audited too. No payloads or personal data in audit rows. | [P13-T05 audit](audit/p13-t05.md) |
| Rate limiting for external APIs | Per-provider-account budgets coordinated across API and runtime processes (DB-backed token bucket or runtime-owned queue), honouring `Retry-After`/provider error codes; reads may wait/defer, writes are never blindly retried. Extends the collection policy in `app/services/social/rate_limit.py` rather than replacing it. | [P13-T03 accepted](audit/p13-t03.md) |
| Approval workflow for write operations | Generic business-command ledger with capability registry, preview, approval, execute, idempotency and `unknown` outcomes. | [P13-T04 accepted](audit/p13-t04-p14-t04-business-crm.md) |
| Test coverage ≥ 80% | Enforced **per new package** (`--cov=<package> --cov-fail-under=80`) via a deterministic script; the existing whole-app coverage stays opt-in, and full suites still require an explicit owner request. | [P13-T06](audit/p13-t06.md) |
| OpenAPI documentation | Every new `/api/v1` route has typed request/response schemas, tags, error model and examples without personal data; generated route tables regenerated and checked; production exposure of `/docs` follows configuration. | [P13-T05 audit](audit/p13-t05.md) |
| Do not log personal data | Existing rule; redaction helpers from P13-T02 are mandatory in new adapters; tests assert absence of identifiers in logs. | all |
| Follow existing architecture | This document; deviations need a task-card decision. | all |
| Surface parity and placement (owner request 2026-11-05) | One permission-checked service per capability behind web, `/api/v1`, CLI for operator/bulk/recovery operations and a chat tool where the agent can usefully read, preview or draft ([surface parity](WORKSPACE_UX.md#surface-parity)). A module adds a sidebar item only for a daily list of its own objects; it contributes to «Сегодня»/decisions through read-time projections, never copies. Applies to tasks claimed after 2026-11-05; in-flight tasks adopt it at their next revision without reopening acceptance. | all; [P20](phases/p20-proactive-assistant.md) |

## Review findings: corrections to the TZ

Each item records what changes and why; phase plans already apply them.

1. **R-01 Primary keys and tenancy.** UUID PKs and `REFERENCES tenants(id)` are
   replaced by the repository's integer PKs, `TenantScopedMixin` and scoped
   managers. Mixing UUID and integer keys would break existing joins and managers.
2. **R-02 Dependency cycle M1↔M2.** Resolved by the P13 contact core and by
   splitting Bitrix24 sending into P14-T05/T06 after P15 (see Sequencing).
3. **R-03 `tasks` name collision.** Planner namespace (see mapping).
4. **R-04 Duplicate document store.** P19 extends 0122 tables; approval already
   binds an exact revision hash and edits invalidate approval (P12-T03).
5. **R-05 Local connector networking.** The TZ has the cloud server call a
   connector HTTP server on the user's `localhost:8080`. A cloud server cannot
   reach a user's loopback interface, and exposing a file server to inbound
   connections contradicts the [local connector contract](PRODUCT_PLAN.md#extension-contracts)
   (optional, paired, outbound-authenticated, no public file server). P18 uses an
   outbound, mutually authenticated connector→cloud channel; any local HTTP API
   binds to `127.0.0.1` for the local UI only. The existing `local_gateway`
   catalog descriptor (certificate fingerprint) is the pairing anchor.
6. **R-06 Yandex Calendar API.** Yandex Calendar exposes CalDAV
   (`caldav.yandex.ru`), not a Google-style REST API. P16-T04 implements CalDAV
   (RFC 4791, `sync-collection`/ETag) with OAuth or an application password.
7. **R-07 1C reachability and query safety.** Most 1C:Enterprise servers are
   on-premises; the cloud may not reach them (owner decision D5: published HTTPS
   endpoint vs. tunnel through the paired P18 connector). The existing catalog
   descriptor is the standard OData interface; published HTTP services are an
   optional adapter. Natural-language queries produce a **typed, allowlisted
   query plan** validated against `$metadata`; the model never emits raw 1C query
   language or free OData text that is executed directly.
8. **R-08 Encryption vs. lookup and search.** See shared requirements: blind
   index for matching; full-text search over encrypted content is impossible, so
   message/document content search needs decision D3.
9. **R-09 Message uniqueness.** `UNIQUE(channel, original_id)` collides across
   workspaces and accounts; use `(tenant_id, connection_id, channel, original_id)`.
10. **R-10 Bulk messaging limits and consent.** Telegram user accounts (MTProto)
    must not be used for bulk or unsolicited messages (provider anti-spam rules,
    account bans); a bot can only message users who started it; MAX has similar
    bot rules. Batch messaging requires a recorded consent/opt-in basis and a
    working unsubscribe (Russian Federal Laws 152-FZ on personal data and 38-FZ
    on advertising may apply — legal review, decision D7). Email batch requires
    SMTP sending, which is not implemented (`send_enabled` is false).
11. **R-11 Rate limits are per provider account.** Bitrix24 limits apply per
    portal (2 req/s sustained with a burst allowance; prefer `batch` calls);
    the limiter must be shared by API and runtime processes.
12. **R-12 Scheduling.** All sync/reminder jobs run in `app.runtime`; no Celery.
13. **R-13 Coverage scope.** 80% applies to each new package, not the legacy app
    (see shared requirements).
14. **R-14 Real-time webhooks need a public endpoint.** Bitrix24/Google push
    notifications require a reachable HTTPS callback with token validation and
    replay protection; they are optional accelerators, polling remains the
    source of truth.
15. **R-15 Calendar/CRM write semantics.** Two-way sync writes into the user's
    external systems. Each write type is a separately enabled capability; the
    approval mode for routine calendar sync (per-connection consent vs.
    per-event approval) is decision D4.
16. **R-16 "Merge duplicates" in two places.** Bitrix24 "manual merge" and hub
    identity reconciliation are one feature (P15-T03) over the contact core;
    P14-T03 only surfaces candidates from CRM data.
17. **R-17 Desktop stack.** TZ allows Python or Electron; one stack is chosen
    (decision D6, recommended Python with a minimal tray/local UI, SQLite FTS5
    instead of the unmaintained Whoosh, `pypdf` instead of deprecated PyPDF2).
18. **R-18 Document storage.** No object storage exists today; text/structured
    content stays in PostgreSQL and binary originals reuse the P12-T12 private
    storage contracts (decision D9 for S3-compatible storage later). S3-compatible
    storage is nonetheless a planned connector in the project (owner clarification
    2026-11-10, D9), because users may already keep files there; it is not a
    default byte store until a measured need. An attachment or document reference
    therefore names where its bytes live and is resolved by a reader, so adding
    an S3 source later needs no change to the approval or outbox contracts.
19. **R-19 Signatures.** A product approval click is not a legally recognized
    electronic signature; qualified signature providers need separate legal and
    security review (already a product boundary).
20. **R-20 Migration numbering.** [#308](https://github.com/Starck43/social-media-ai/pull/308) is now merged; the last inspected [#327](https://github.com/Starck43/social-media-ai/pull/327) head still contained a competing `0124` from `0123`. Verify its current migration chain and re-parent as necessary before integration. New phases do **not** pre-reserve revision numbers: take the next free revision after rebasing on current `dev`.

## Interaction with related pull requests

#308 has been integrated; #327 remains open. Dependent tasks use integrated contracts and must not assume unmerged functionality is available.

| PR | Task | What it brings | Effect on P13–P19 |
| --- | --- | --- | --- |
| [#308](https://github.com/Starck43/social-media-ai/pull/308) (+ stacked docs [#311](https://github.com/Starck43/social-media-ai/pull/311)) | [P12-T14](audit/p12-t14.md) | Protected personal credentials, connector account authorizations (`app/models/integrations/authorization.py`), VK encrypted refresh, Telegram login/runtime safeguards, revisions `0124` + `p12t14_*` | P13-T02 key handling and P13-T08 OAuth framework build on it; Bitrix24/Google/Yandex OAuth (P14-T01, P16-T03/T04) build on the integrated contracts; coordinate shared-path changes with their current owners. |
| [#327](https://github.com/Starck43/social-media-ai/pull/327) (+ docs [#307](https://github.com/Starck43/social-media-ai/pull/307)) | [P12-T12](tasks/p12-t12.md), [P12-T13 audit](audit/p12-t13.md), [P8-T34](tasks/p8-t34.md) | Private file catalog, scans/traversal, bounded plain-text read, line evidence, private insights storage, admission contract, revisions `0124`–`0128` | P18 cloud side reuses its catalog, admission, evidence and retention contracts; P19 attachments/originals reuse private storage. Folder rights come from [P12-T15](tasks/p12-t15.md). |

If a PR is closed without merge, the dependent task card records the gap and
the executor re-plans the seam before implementation.

## Owner decisions

Decisions are recorded in [Q-18](audit/q-18-owner-decisions.md); each task card names
its decision by ID and date. All are answered, and the *Recommended default* column
is kept for reference only.

| ID | Decision | Recommended default | Needed by |
| --- | --- | --- | --- |
| D1 | Priority band of the new phases — **answered 2026-11-04**: P13 + P14 in `P2`, P15–P19 in `P3` | P13 + P14 in `P2`, P15–P19 in `P3` (row order = TZ order) | ROADMAP |
| D2 | Bitrix24 authorization — **answered 2026-11-04**: OAuth 2.0 local application per portal; webhook is an admin-only single-portal alternative (not a second equal path) | OAuth 2.0 local application per portal (TZ); incoming webhook kept as an admin-only alternative for a single portal | P14-T01 |
| D3 | Searchable message/document content vs. Fernet — **answered 2026-11-04**: metadata only; no content cache, no full-text index. Content search is a new measured decision later, not a pre-approved option | Encrypt identifiers; keep an optional per-workspace content cache with retention TTL and PostgreSQL full-text index, disabled by default; otherwise search metadata only. Answer this together with the FTS dependency: no `tsvector`/`pg_trgm`/`unaccent` usage or Russian configuration exists in the current code or migrations, so the index type and language configuration must be decided and measured first (audit F-4 of [P13-T01 accepted](audit/p13-t01.md)) | P15-T04, P19-T06 |
| D4 | Calendar write approval mode — **answered 2026-11-04**: per-connection consent + per-event audit; conflicting or deleting changes need per-item approval | Explicit per-connection write consent + per-event audit; conflicting or deleting changes require per-item approval | P16-T03/T04 |
| D5 | 1C network path — **answered 2026-11-04**: published HTTPS with allowlisted host first; P18 connector as documented fallback | Published HTTPS endpoint with allowlisted host first; tunnel via P18 connector later | P17-T01 |
| D6 | Desktop connector stack and signing — **answered 2026-11-04**: Python + PyInstaller; pilot and early builds unsigned (Windows SmartScreen and macOS Gatekeeper warnings accepted), signing and notarization deferred to a follow-up after the pilot | Python + PyInstaller, signed builds for Windows/macOS, AppImage/deb for Linux | P18-T03/T07 |
| D7 | Batch messaging channels and consent basis — **answered 2026-11-04**: email + bot channels, explicit opt-in, unsubscribe; no MTProto bulk | Email + bot channels only, explicit opt-in record, unsubscribe link/command; no MTProto bulk | P14-T06 |
| D8 | PII key management — **answered 2026-11-03**: separate `PII_ENCRYPTION_KEYS` (`MultiFernet`, first key active) and `PII_INDEX_KEY`, kept stable; never reuse `CREDENTIALS_KEY`. Rotating the index is a separate approved task | P13-T02 |
| D9 | Binary document storage — **answered 2026-11-04**: PostgreSQL + P12-T12 private storage; S3 only after a measured need | PostgreSQL + P12-T12 private storage; S3-compatible storage only with a measured need | P19-T01 |

## Original TZ acceptance criteria

The owner's per-module acceptance criteria are preserved, adapted, in the
"Acceptance" sections of each phase. Where a criterion conflicts with a review
finding above, the phase states the adapted form and the finding ID.
