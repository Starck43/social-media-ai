# Business production readiness

## Release position

Baseline: dev `f11acefba8fbc2f47e3a17f670a97587208f1dd3`, reviewed 2026-10-08; revalidated before merge against dev `fa5fb1b224527865dd830e9024f37a67b1ea4748` ([delta recheck](design/business_readiness_recheck_2026_10_08.md)). The new individual-analysis UX work does not close the release gates below. **Readiness has not been demonstrated.** This document defines proposed gates, not a report of an actual deployment or security certification.

Recommended first product: a managed, invite-only B2B monitoring service with clearly supported collection paths, human-approved actions and operator-owned provider configuration. Self-service billing and high-scale SaaS are later releases, not excuses to postpone basic safety.

See the [proposal review/evidence map](design/proposal_review.md), [UX plan](LOCAL_EXPERIENCE_PLAN.md) and [roadmap](ROADMAP_INTEGRATED.md). Source observations are static unless explicitly stated otherwise. No live production database or credentials were used.

## Current bounded progress (2026-10-09)

Latest checked dev `1e908b2` preserves owner chat/notification UI changes.
PR #12 digest integration, PR #17 typed outputs, PR #18 declared-failure outcomes
and PR #19 bootstrap/API readiness are merged. The owner
reported all tests pass; commands/counts/tested SHA/logs were not provided. This
is not an independent rerun, release certification or evidence of live activation.

PR #19 merged the working-schema default guard, redacted check diagnostics
and API DB readiness/liveness. Its 24 mocked-source tests passed in the authoring
sandbox; 8 ASGI cases were prepared but unrun there. No fresh post-merge owner
results or deployment acceptance were supplied. See its
[handoff](design/bootstrap_readiness_handoff.md).

Dispatcher-owned log/failure-notification privacy is PREPARED in
`ai/dispatcher-log-privacy`, not merged (no PR created): 13 privacy tests plus
25 existing mocked outcome regressions passed; 2 PostgreSQL cases are unrun.
Audit columns and other process logs are unchanged. See the
[privacy handoff](design/dispatcher_log_privacy_handoff.md). Historical baseline
observations below are not statements of current implementation status.

## Critical gates and work packages

### PRD-01 — Tenant-safe, retry-safe delivery (blocker)

Status: **PARTIAL, OPEN**. Merged items and next-session continuation are in
[Implementation status](IMPLEMENTATION_STATUS.md). The
[retry design](design/digest_delivery_retry_plan.md) is prepared; storage/schema
option was approved and foundation PRs #6/#7/#9/#10/#11 are now merged.
PR #12 adds the default-off checkpoint publisher, atomic first snapshot/job
reference, conservative per-part recovery, pacing and claim-fenced finalization.
Prepared code/tests are not staged concurrency/recovery acceptance or live sender
activation. Verify target migration 0087 independently before updated ORM code;
merge is not deployment. Retention, distributed rate control and operator recovery
still need acceptance; see the job delivery handoff.

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
- [x] Bounded implementation merged: frozen per-destination/part progress, known-unsent retries and conservative ambiguity stops. Fresh recovery/transport acceptance remains open; no exactly-once guarantee.
- [ ] Accept concurrency/lease-loss behavior in staging for merged schedule locks/claim fencing; implement separately authorized force recovery. Do not reset receipts or enable a force UI automatically.

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

Observed: tracked [Compose](../docker/docker-compose.yml) starts db + API, not runtime. It publishes PostgreSQL 5432 and performs migration on each API startup. [Dockerfile](../docker/Dockerfile) is non-root, but defaults to API. baseline [HTTP health](../app/main.py) returned status ok/HTTP 200 on DB disconnection. Merged PR #19 registers /livez (no DB) and /readyz plus /health (200 ready, 503 failed/timed-out DB probe); deployment is unverified. Default session configuration does not explicitly require secure cookies.

Actions:
- Provide a tested production profile with one migration job, API, exactly one scheduler/listener owner, and worker execution. Runtime already includes a worker: avoid unintentionally multiplying pollers/schedulers when adding dedicated workers.
- Keep PostgreSQL private; bind API behind HTTPS proxy as appropriate. Configure secure cookies, trusted proxy/host/CORS rules, CSRF coverage, DEBUG=false, protected operator console and credential validation/redacted logs.
- Monitor scheduler/listener/worker freshness separately from HTTP liveness. Readiness must fail when required dependencies fail; runtime-only deployment is not an HTTP health server.
- Define graceful shutdown/drain, bounded request/job timeouts, restart policies and logs. Pin/build static UI dependencies rather than depending on live Tailwind/HTMX/Alpine CDNs for a business-critical UI.
- Automate encrypted off-host DB backups plus a protected recovery path for CREDENTIALS_KEY/SECRET_KEY. Restoring the DB without its vault key is not a usable restore.
- Rehearse install, upgrade, backward-compatible rollback and restore in staging. Never reset production to prepare tests.

Acceptance: clean deploy starts all required loops; DB is not public; process/DB failures are detected; only one migrator/poller owner; restart during work has bounded side effects; restored tenant can decrypt credentials and deliver a sample. Operator signs off recorded recovery evidence. Existing DEPLOYMENT examples must be replaced by tested commands, not copied as-is.

### PRD-05 — Honest outcomes and bounded task execution (blocker)

Merged progress: PR #18 records explicit non-checkpoint returned status=failed as terminal Job/task failure, retaining known cost and suppressing success notifications/replay. Collection partial errors and unknown status shapes retain legacy behavior. General claim CAS, Job/task atomicity, scheduler correctness and ordinary-job leases remain open; checkpoint-specific fencing is not a queue-wide guarantee.

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

#### Proposed data-class policy — decision record, not implementation

Source inventory: dev `c8e58971443b254b0d447541e2aa21a362cc5e56` on
2026-10-10. This bounded review covers the paths linked below, not every deletion
entry point or the deployed environment. Numeric policy periods, offboarding
completion deadlines and backup expiry are **UNDECIDED** until product/operator
and applicable legal review. No cleanup, export, revocation, migration, backup or
restore was executed. PRD-06 remains OPEN.

**Observed implementation, not approved retention promises:**

- [Tenant plan metadata](../app/models/tenancy/tenant.py) contains
  `retention_days` 7/30/90 for Starter/Pro/Business. The reviewed deletion paths
  below do not read it; these values do not establish a data-class policy.
- [JobManager.cleanup_done](../app/models/managers/job_manager.py) deletes done
  Jobs by `finished_at`, default 24 hours. [handle_prune](../app/jobs/handlers.py)
  instead uses `created_at`, default 7 days, for done/failed Jobs and preserves
  attempt-budget-stop errors. These different clocks/windows can remove outcome
  evidence before the advertised tier window; this review changes neither path.
- Staged raw items retire by saved hashes; exhausted attempts remain stored.
  [delete_older_than](../app/models/managers/collected_item_manager.py) uses
  `created_at` and deletes even unanalysed/exhausted items. Its raw SQL has **no
  tenant_id predicate**; `handle_prune` passes only session/days. This is a static
  tenant-boundary risk, not an executed cross-tenant deletion test. A future fix
  must explicitly define tenant scope or separately authorized global maintenance.
- Model deletion dependencies are not a complete lifecycle: deleting a chat
  session cascades messages; memory evidence becomes NULL while the learned value
  survives. Source deletion cascades analytics/actions; deleting analytics can
  leave an action with a NULL analytics reference. These require an offboarding
  dependency map, not a blanket delete/cascade assumption.

Every row below proposes a purpose and eligibility rule. **Period = UNDECIDED
for every class**; the origin clock is proposed, not installed behavior. No
indefinite retention or automatic destructive cleanup is approved by this table.

| Data class / source | Proposed purpose and retention clock | Proposed deletion or revocation rule | Evidence/exception to resolve before implementation |
| --- | --- | --- | --- |
| [Staged raw text, metadata and hashes](../app/models/collection/collected_item.py) | Temporary analysis input; age from ingestion `created_at`, not historical publication date | Retire only confirmed saved hashes; expire eligible backlog separately after scoped preview | Failed/exhausted items need visible coverage loss; no raw archive merely because a plan is paid |
| [Analytics, prompts and rollups](../app/models/analysis/ai_analytics.py) | Customer results; choose whether clock follows creation or reporting window, and whether updates extend it | Delete/redact derived personal content and rebuild/invalidate dependent views under explicit scope | Prompts/response_payload may contain source text; mutable estimated_cost is not a durable every-call spend ledger |
| [Chat messages/tool results](../app/models/agent/agent_message.py) and sessions | Conversation continuity; proposed message creation clock, separate session/state lifetime | Approved conversation/account deletion must include tool outputs and pending approval state | Session CASCADE removes messages; deleting cost-bearing messages may erase economics evidence; define minimal retained accounting separately |
| [Memory and provenance](../app/models/agent/agent_memory.py) | Purpose-bound preferences/facts; review on purpose change/offboarding, not just last write | Explicitly remove/review learned values when their evidence or subject is deleted | evidence_message_id SET NULL does not erase the fact; decide required provenance and dependent feedback handling |
| Jobs, [actions](../app/models/scheduling/bot_action.py), [digest content/receipts](../app/models/scheduling/digest_run.py) | Operational trace and reconciliation; proposed terminal-completion clock; approval/receipt lifetime separately | Separate eligible customer content deletion from minimal scoped outcome/cost/decision evidence | Preserve unresolved external effects, in_flight/uncertain receipts, reservations and approved incident/legal holds until an explicit resolution; minimal holds need owner/review/expiry decisions |
| [Notifications](../app/models/notifications/notification.py) | User attention/history; proposed creation clock | Scoped expiry independent of read/unread state; remove sensitive report copies where required | is_read is not deletion eligibility or evidence of messenger delivery |
| [Personal credentials](../app/models/identity/user_credential.py) and deployment keys | Authorized connector access while needed; expiry/revocation event, not tier period | Stop workspace access promptly on revocation/offboarding; purge an eligible personal secret only with its owner's scope | Personal vault is shared across the user's workspaces; tenant departure must not delete another workspace's usable secret. Provider-side revocation and cached/session invalidation need separate evidence |
| Process logs, traces, support exports and third-party LLM copies | Minimized operational purpose; separate approved log/provider periods and data-location agreement | Bound collection/access and delete eligible copies; redact at source rather than relying on a grep filter | Bounded #70 logs do not sanitize all sinks; provider/subprocessor deletion and legal terms require verified capabilities, not a DB-delete promise |
| [Backups, snapshots/WAL and protected keys](DEPLOYMENT.md#backup-strategy) | Recoverability; choose rolling generations, expiry, key custody, RPO/RTO and location together | Deleted primary data ages out on the approved backup lifecycle; restrict restores and replay approved deletion/revocation records before reopening | Backup presence is not restore acceptance; DB without CREDENTIALS_KEY is unusable. Do not promise immediate deletion from immutable/off-host/provider copies |

**Offboarding and restore proposal (not available commands):** verify actor,
workspace and data subject; stop future schedules/access and reconcile in-flight
work; preview scoped export/deletion/dependencies and holds; record an approved,
minimal deletion/revocation marker outside the expired restore generation;
perform only the separately authorized operations; verify remaining references
and report what remains/why and the approved backup expiry. For an isolated
restore, apply newer deletion/revocation markers and recheck access before
activation; never resume customer sends as a restore test by default. Marker
storage, integrity, retention and access control need their own reviewed design;
no tombstone table or legal-hold mechanism is claimed to exist.

**Decisions required to approve policy:** product owner chooses each class's
period, clock, tier coverage and downgrade/offboarding treatment; operator chooses
backup/log generations, key recovery, restore/deletion replay and support access;
legal/privacy review confirms required exceptions, disclosure, provider geography
and subprocessors. Until those decisions and implementation evidence exist, do
not advertise the plan metadata as enforced deletion or a commercial guarantee.

**Future acceptance, not performed here:** UTC boundary/NULL timestamp and
class/tier fixtures; two-tenant deletion and scope-change races; FK/provenance and
uncertain-effect preservation; deletion of derived copies; credential revocation
without collateral shared-vault loss; isolated restore with deletion replay and
key recovery. Each implementation gets its own bounded checks, not a repeat of
already completed UI/log tests. Publishing this proposal closes only the inventory
and proposal-writing task, not PRD-06 or staging/business acceptance.

### PRD-07 — Structured, safe AI boundaries (blocker for write-enabled features)

Retain existing strict analysis validation and text framing; do not repeat completed CA-01/02. PR #17 merged typed digest/learn/reflect contracts, bounded fields/operations, owned evidence validation and safe validation-failure paths. Atomic memory batches, watermark concurrency, poisoned-input/factual-quality acceptance and complete billing are still open. Validate supported scenario schemas at save time; an unsupported schema must not silently remove enforcement.

Treat social content, derived summaries, learned facts and error advice as untrusted data, not authority. Test Russian/English injection, boundary escaping, malformed nested outputs and external metadata. Never promise regex blocks all injection. Safety comes from server-side permissions, tool argument validation, scoped data, confirmation and publication restrictions.

Acceptance: invalid output cannot write arbitrary memory/scenario/task/action data; original staged content remains retryable; usage still counted; poisoned digest/memory cannot gain tools or permissions. Record factual quality, relevance false negatives and evidence coverage on a labeled fixture set.

### PRD-08 — Operational visibility and support (pilot gate)

Reuse health counters, job history, notifications and request snapshots. Add redacted correlation IDs/timings and dashboards for source freshness, staged backlog/quarantine, oldest pending job, stale loops, analysis failure rate, delivery outcomes, spend and missing prices. Existing notification service is DB-first; writing a notification does not prove messenger delivery.

Define operator alerts and runbooks for provider/auth/DB/delivery outages. Bounded probes should alert on repeated degradation; a globally shared model must not be auto-disabled by one tenant's incidental error. Support has a named owner, severity/escalation rules and auditable access; optional external tracing follows a data-transfer review.

Acceptance: injected failures trigger actionable, deduplicated alerts to the right audience; health can distinguish idle from stalled; an operator can trace one report without seeing customer raw secrets. No Langfuse requirement for the first pilot.

### PRD-09 — Safe publication and feature promises (release gate)

Draft PR22 reconciles UX-02 registry binding to the preview-only contract: `botaction.view`, actor-bound confirmation, `dry_run=False` refusal and no PENDING transition or publication. Owner105 checks at8af65bf passed; isolated fixture and newly included ok/no_data/partial/skipped UI verification are pending at the integrated head. Keep automatic external writes disabled. Live publication and durable approval/claim semantics require a separate contract and release evidence; preview is not approval to send.

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
