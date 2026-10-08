# Business production readiness

## Release position

Baseline: dev `f11acefba8fbc2f47e3a17f670a97587208f1dd3`, reviewed 2026-10-08; revalidated before merge against dev `fa5fb1b224527865dd830e9024f37a67b1ea4748` ([delta recheck](design/business_readiness_recheck_2026_10_08.md)). The new individual-analysis UX work does not close the release gates below. **Readiness has not been demonstrated.** This document defines proposed gates, not a report of an actual deployment or security certification.

Recommended first product: a managed, invite-only B2B monitoring service with clearly supported collection paths, human-approved actions and operator-owned provider configuration. Self-service billing and high-scale SaaS are later releases, not excuses to postpone basic safety.

See the [proposal review/evidence map](design/proposal_review.md), [UX plan](LOCAL_EXPERIENCE_PLAN.md) and [roadmap](ROADMAP_INTEGRATED.md). Source observations are static unless explicitly stated otherwise. No live production database or credentials were used.

## Critical gates and work packages

### PRD-01 — Tenant-safe, retry-safe delivery (blocker)

Status: **PARTIAL, OPEN**. Merged items and next-session continuation are in
[Implementation status](IMPLEMENTATION_STATUS.md). The
[retry design](design/digest_delivery_retry_plan.md) is prepared; storage/schema
approval is pending and no receipt/retry code is implemented yet.

**Routing progress:** the [tenant-safe delivery change](design/tenant_safe_digest_delivery_review.md)
implements exclusively owned active DB recipients, ignored env destinations, within-call
identifier deduplication and non-bypass build scoping. No migration or live
production setup is included. Per-target retries, concurrent publication and
ambiguous transport acknowledgements remain open; this gate is not complete.

Baseline observation before that change: [broadcast_digest](../app/channels/registry.py) sends every digest to global env targets, then tenant targets. [build_and_publish](../app/services/digest/builder.py) stores one aggregate run outcome and rebroadcasts after a partial-channel failure.

Actions:
- [x] **Routing implemented:** digest delivery ignores env destinations in all workspaces, including bootstrap; no automatic binding creation.
- [x] **Notification boundary implemented:** workspace messages require explicit active owned bindings; fixed operator templates alone use `TELEGRAM_ADMIN_CHAT_ID`. Source-owned DB failure notifications do not forward raw exceptions. Recipient-picker UX, persisted outcomes/rate limits and process-wide logging audit remain open. See [notification contract](NOTIFICATIONS.md).
- [x] **Within-call routing implemented:** deduplicate normalized transport/chat identifiers and enforce active owned bindings. Numeric-ID/username equivalence still requires platform identity validation; keep operational alerts free of customer report content.
- [ ] Record outcomes per destination/part and retry only unfinished deliveries. Specify ambiguous transport-timeout behavior; external APIs may not provide exactly-once guarantees.
- [ ] Atomically prevent two concurrent sends for the same scheduled run/period. Make force-resend an explicit permission/confirmation decision.

Acceptance: two tenants + configured legacy destination cannot leak reports; one successful/one failing destination only retries the failure; duplicate destinations, concurrent run attempts, lost acknowledgements and message splitting are covered. A no-channel configuration is visible and not a successful delivery. Persisted per-target progress may require a separately reviewed migration or versioned result structure.

**Notification boundary progress:** the [notification contract](NOTIFICATIONS.md)
implements explicitly owned workspace recipients, source-owned DB failure
notifications and fixed-template operator alerts. It does not implement durable
notification delivery progress, rate limiting, process-wide log redaction or
digest retry/concurrency guarantees. Review together with the separate digest
routing branch; this does not close PRD-01 or establish business production readiness.

### PRD-02 — Fail-closed identity and authorization (blocker)

Observed: [permissions](../app/core/permissions.py) treats a None user as bypass and lets a workspace owner pass model rights. [runtime](../app/agent/runtime.py) can resolve messenger members to None. [TenantUser.is_owner](../app/models/tenant.py) treats missing role_id as owner. LLM fleet tables are global, while [LLM tools](../app/agent/toolset/llm.py) rely on these rights. [action tools](../app/agent/toolset/actions.py) lack explicit permission requirements.

Actions:
- Distinguish trusted worker/operator execution from an unresolved interactive identity. Map messenger membership roles deliberately; missing user/role is not automatically full access.
- Separate platform administration from tenant administration. Tenant owners may configure only their workspace, not add/delete shared models/providers or select another user's secrets.
- Inventory all 33 registered tool declarations at baseline, plus API/web/admin writes. Apply explicit rights, bounded arguments and confirmation where appropriate; collect_now/actions are review targets.
- Migrate legacy memberships with an explicit operator-approved mapping before removing compatibility behavior; do not silently lock out owners or elevate unknown users.
- Bind approvals to initiating actor, tenant, exact normalized arguments and TTL; recheck membership/role and operational guards at execution time. Serialize simultaneous chat turns or test their state consistency.

Acceptance: Viewer/Manager/tenant-owner/platform-operator matrix across chat/web/API; foreign tenant/credential/global-model attempts denied before reads/writes; unknown identities fail closed; revoked membership/permission, approval replay and concurrent confirmations tested. Confirmation alone is not sufficient authorization.

### PRD-03 — Complete, bounded economics (blocker)

Observed: [daily_cost_today](../app/services/tenancy/resolver.py) counts chat/digest/job usage, not [analysis cost](../app/services/ai/analyzer.py). Analysis uses USD cents and overwrites daily-row costs on update; rollups are not independent paid calls. Business limits also have inconsistent semantics between effective_limits, tenant_daily_cost_limit and check_daily_cost.

Actions:
- Define one authoritative spend policy across analysis, multimodal/unified stages, chat, digest, learn/reflect, prompt suggestion/model tests, retries/fallbacks and future probes/embeddings. Decide explicitly how operator test spend is attributed.
- Record every priced attempt durably, including calls whose output is invalid/filtered or whose persistence fails. Do not infer historical spend by summing mutable analysis snapshots or both daily and period rollups.
- Standardize units to USD in enforcement; convert legacy cents explicitly. Distinguish missing tariff/unknown usage from truly free calls.
- Add atomic reservations or another proven concurrent admission policy with bounded max tokens and request limits; settle/refund estimated reservations. Define allowed overshoot, UTC reset and cap behavior for Business.
- Preserve aggregate-only digest delivery when narrative budget is exhausted; never hide omitted analysis coverage.

Acceptance: concurrent chat/analysis/digest cannot bypass the agreed cap; repeated analysis, invalid output, retry/fallback, failed save and unknown prices are accounted once; non-overlapping UTC boundaries tested; tier limits match UI/contract. A proper usage/reservation ledger likely needs schema work: design and approval first. Interim strict concurrency/volume bounds must be labeled approximate, not billing-grade.

### PRD-04 — Repeatable, hardened deployment and recovery (blocker)

Observed: tracked [Compose](../docker/docker-compose.yml) starts db + API, not runtime. It publishes PostgreSQL 5432 and performs migration on each API startup. [Dockerfile](../docker/Dockerfile) is non-root, but defaults to API. [HTTP health](../app/main.py) returns status ok/HTTP 200 on DB disconnection and default session configuration does not explicitly require secure cookies.

Actions:
- Provide a tested production profile with one migration job, API, exactly one scheduler/listener owner, and worker execution. Runtime already includes a worker: avoid unintentionally multiplying pollers/schedulers when adding dedicated workers.
- Keep PostgreSQL private; bind API behind HTTPS proxy as appropriate. Configure secure cookies, trusted proxy/host/CORS rules, CSRF coverage, DEBUG=false, protected operator console and credential validation/redacted logs.
- Monitor scheduler/listener/worker freshness separately from HTTP liveness. Readiness must fail when required dependencies fail; runtime-only deployment is not an HTTP health server.
- Define graceful shutdown/drain, bounded request/job timeouts, restart policies and logs. Pin/build static UI dependencies rather than depending on live Tailwind/HTMX/Alpine CDNs for a business-critical UI.
- Automate encrypted off-host DB backups plus a protected recovery path for CREDENTIALS_KEY/SECRET_KEY. Restoring the DB without its vault key is not a usable restore.
- Rehearse install, upgrade, backward-compatible rollback and restore in staging. Never reset production to prepare tests.

Acceptance: clean deploy starts all required loops; DB is not public; process/DB failures are detected; only one migrator/poller owner; restart during work has bounded side effects; restored tenant can decrypt credentials and deliver a sample. Operator signs off recorded recovery evidence. Existing DEPLOYMENT examples must be replaced by tested commands, not copied as-is.

### PRD-05 — Honest outcomes and bounded task execution (blocker)

Observed: [dispatcher](../app/jobs/dispatcher.py) marks a returned dictionary done even when a handler reports status failed. Collection records partial source errors; analysis can catch failures and continue. [reaper](../app/models/managers/job_manager.py) requeues by locked_at age with no heartbeat in that path.

Actions:
- Define success/partial/failed/skipped semantics per handler and mirror them in notifications/tasks/UI. Handler return status is not automatically success; all-source failure must be visible.
- Add proven job leases/heartbeats or a timeout policy that prevents a healthy long job from being concurrently retried. Bound per-source work and poison-item attempts.
- Test scheduler enqueue+next_run advancement atomically under concurrent ticks, not merely assume SKIP LOCKED job claiming solves scheduler duplication.
- Apply provider/platform-specific backoff, Retry-After and concurrency controls; separate quota/auth/config errors from transient failures.
- Preserve staged items after failed analysis, retire only stored hashes, and expose quarantined/max-attempt items before retention removes them.

Acceptance: worker killed before/after external side effect; stale lease; concurrent workers/ticks; partial/all-source failures; invalid LLM output and rate limits; data retained/retired correctly. External side effects require their own idempotency, not just a DB claim lock.

### PRD-06 — Data lifecycle and customer contract (blocker)

Observed: tier retention_days is advertised, but [prune](../app/jobs/handlers.py) deletes jobs and staged rows using payload/default days, not a complete tier policy. Raw text is not archived by design; DEBUG has different trace behavior.

Actions:
- Define retention by data class: staged raw items, analytics/rollups, chat, memory/evidence, actions/audit, notifications, credentials and backups. State when deleted data ages out of backups and how a restore reapplies deletions.
- Implement tier policy consistently, with bounded batches, tenant scopes and observability. Protect spend/audit records required for the agreed business/legal term.
- Support offboarding/export/deletion and immediate credential revocation. Do not silently clear another workspace's shared personal credential.
- Document third-party LLM processing, subprocessors/data location, social-platform access limitations and acceptable automation. Obtain jurisdiction-specific legal review before commercial claims.
- Keep DEBUG off in production. Redact tokens, sessions and sensitive content from logs/audit/external tracing. Opt-in raw retention requires a purpose and permissions, not simply a paid plan.

Acceptance: time-bound fixtures for each class and tier; tenant deletion cannot affect others; vault/revocation and evidence FK behavior tested; privacy/support terms match deployed behavior. Legal/commercial policy is a decision gate, not something this code review certifies.

### PRD-07 — Structured, safe AI boundaries (blocker for write-enabled features)

Retain existing strict analysis validation and text framing; do not repeat completed CA-01/02. Add typed digest/learn/reflect contracts, bounded fields/operations and safe failure paths. Validate supported scenario schemas at save time; an unsupported schema must not silently remove enforcement.

Treat social content, derived summaries, learned facts and error advice as untrusted data, not authority. Test Russian/English injection, boundary escaping, malformed nested outputs and external metadata. Never promise regex blocks all injection. Safety comes from server-side permissions, tool argument validation, scoped data, confirmation and publication restrictions.

Acceptance: invalid output cannot write arbitrary memory/scenario/task/action data; original staged content remains retryable; usage still counted; poisoned digest/memory cannot gain tools or permissions. Record factual quality, relevance false negatives and evidence coverage on a labeled fixture set.

### PRD-08 — Operational visibility and support (pilot gate)

Reuse health counters, job history, notifications and request snapshots. Add redacted correlation IDs/timings and dashboards for source freshness, staged backlog/quarantine, oldest pending job, stale loops, analysis failure rate, delivery outcomes, spend and missing prices. Existing notification service is DB-first; writing a notification does not prove messenger delivery.

Define operator alerts and runbooks for provider/auth/DB/delivery outages. Bounded probes should alert on repeated degradation; a globally shared model must not be auto-disabled by one tenant's incidental error. Support has a named owner, severity/escalation rules and auditable access; optional external tracing follows a data-transfer review.

Acceptance: injected failures trigger actionable, deduplicated alerts to the right audience; health can distinguish idle from stalled; an operator can trace one report without seeing customer raw secrets. No Langfuse requirement for the first pilot.

### PRD-09 — Safe publication and feature promises (release gate)

Fix UX-02 tool binding/permissions first. Keep automatic external writes disabled for the initial pilot. Existing action path supports live sends in principle but registry invocation is broken at baseline; dry-run approval currently changes state, so preview/approve/send semantics need explicit tests/design.

Before enabling live actions: validated non-empty payload/target, actor-bound approval, fresh guards/credential ownership, per-action claim/idempotency, retries, audit and immediate kill switch. Dry-run must not consume the only sendable state unless the product explicitly supports that transition. Tier “allow_auto_actions” is not proof of an unattended scheduler publisher.

Publish a capability matrix: VK public/user-access collection; Telegram push vs MTProto history; MAX chat/digest transport versus collection (factory has no MAX client). Only capabilities with acceptance evidence may be sold. Postpone MAX collection if not required by the first customers; do not promise parity.

## Release gate sheet

All statuses below are **open / evidence required**, not completed by this documentation PR.

| Stage | Required evidence | Go/no-go owner |
|---|---|---|
| Staging candidate | PRD-01–07 fixes/design limits verified; fresh isolated full suite, focused security tests, schema drift check against staging, reproducible build/deploy. | Engineering owner |
| Managed pilot | PRD-08/09 restrictions, first-value/recovery journeys, restore drill, bounded workload, named support owner and approved data terms. | Operator + product owner |
| Paid repeatable B2B | Pilot outcomes acceptable, spend margin measured, retention/deletion verified, upgrade/rollback rehearsed, agreed commercial process and incident handling. | Business + engineering |
| Self-service SaaS | Subscription lifecycle/payment correctness, abuse controls, access boundary audit, capacity/isolation evidence and customer support readiness. | Separate launch decision |

Proposed initial objectives for discussion, **not measured guarantees**: core workflow success >=99% on the agreed pilot window excluding explicitly unsupported sources; p95 UI aggregate response <=2 s for the agreed dataset; queue-to-start p95 <=60 s at the agreed arrival rate; backup RPO <=24 h and restore RTO <=4 h. Decide whether the business needs stricter values, then measure/price them. Provider collection delays and API limits need separate freshness objectives.

## Required evidence packet for a release

- exact release SHA, migration head and deployed revision; changes/configuration and capability matrix;
- complete test results from an isolated environment (do not reuse historical 255/891/961 counts as current);
- cross-tenant/least-privilege, concurrent budget and delivery-failure results;
- production profile validation, restore/upgrade/rollback logs with secrets redacted;
- capacity assumptions and measured latency, backlog, spend/unknown-price rates;
- unresolved risks, explicit disabled features, owner/runbook and sign-off.

No production migration, table change, billing integration or application fix is authorized by this documentation plan alone. Each implementation package requires its own agreed scope and PR.
