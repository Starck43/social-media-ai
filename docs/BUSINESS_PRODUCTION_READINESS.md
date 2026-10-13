# Business production readiness

This document owns the stable engineering and release gates. It does not claim
that a deployment, database revision or security review has passed. Current
priority and ownership live in [the work board](design/README.md); delivered
foundations are indexed in [implementation status](IMPLEMENTATION_STATUS.md).

Recommended first release: a managed, invite-only B2B monitoring service with
explicitly supported collection paths, human-controlled external actions and
operator-owned provider configuration.

## Gate status

All nine areas remain open at dev checkpoint
`84ee16d284a6355006dd71c7157a0744ab203ddd` (2026-10-12). Merged slices reduce
risk but do not close a whole gate. Before implementing a gap, verify it in current
code and define one bounded acceptance condition in the board/PR.

## PRD-01 — Tenant-safe, retry-safe delivery

Delivery state is tracked per workspace, destination, content version and message
part. A retry sends only parts known not to have succeeded. Lost acknowledgement
becomes an ambiguous state requiring conservative review, not an automatic resend.

Acceptance requires:

- recipients and credentials belong to the active workspace and remain authorized;
- successful recipients are not resent after another destination fails;
- concurrent publishers and lease loss cannot both finalize the same part;
- split messages preserve ordered per-part progress and visible terminal outcome;
- no-channel, disabled-channel and partial delivery are not reported as success;
- operator recovery cannot clear receipts or enable live sending implicitly.

External transports do not provide a general exactly-once guarantee. See
[DIGEST](DIGEST.md), [CHANNELS](CHANNELS.md) and [NOTIFICATIONS](NOTIFICATIONS.md).

## PRD-02 — Fail-closed identity, permissions and approvals

Unknown identity, inactive/missing membership, missing permission and revoked
access fail closed at every interactive entry point. Platform administration,
workspace administration and personal credentials remain separate scopes.

Acceptance requires a Viewer/Manager/workspace-owner/platform-operator matrix
across chat, web, API and tools. Approval for an external write is bound to the
actor, workspace, normalized arguments, content/version and expiry; execution
rechecks rights and credentials. Replays and simultaneous confirmations cannot
execute the same action twice. Confirmation alone never grants authorization.

## PRD-03 — Complete, bounded economics

Each actual provider attempt receives an immutable identity linked to its tenant or
explicit operator payer, execution, purpose, provider/model, dispatch time, usage,
tariff snapshot, cost certainty and outcome. Fallbacks, internal tool calls,
retries, failed output and failed persistence remain separate attempts.

Authoritative accounting uses a reviewed Decimal USD contract. Preserve adapters
for legacy USD and USD-cent projections until an approved migration exists.
Pricing truth is owner-decided (2026-10-12) and three-state: FREE only on an
explicit zero in a structured provider USD report or an explicit zero tariff.
Structured provider price, never generated model text, arbitrates actual cost;
a non-zero tariff is a known-price basis. Missing/unresolved price is UNDEFINED,
never implicitly free or zero, separately recorded and admitted under the owner's
explicit undefined-price policy outside cap-bounded spend. Missing usage stays distinct from a reported zero, and
not-dispatched attempts are recorded as such.

Acceptance requires atomic budget admission under concurrency, explicit overshoot
policy, reservation release/settlement after every terminal outcome, and durable
attempt evidence that survives transcript/job/result cleanup. Aggregated analytics
and mutable model counters are projections, not the ledger.

## PRD-04 — Reproducible deployment and recovery

A supported profile defines one migration owner and one owner for each scheduler,
worker and channel listener. The database is private; the edge, cookies, secrets
and backups use the intended production settings. Optional integrations do not
block startup when absent.

Acceptance requires a staging-backed install, upgrade, rollback and restore
record; health plus scheduler/worker/listener freshness; drain/shutdown behavior;
encrypted database and vault-key backup; and proof that a restored tenant can use
its own credentials. `python -m app.runtime` already owns scheduler, worker and
listeners, so a deployment must not start duplicate owners. See [DEPLOYMENT](DEPLOYMENT.md).

## PRD-05 — Truthful, bounded execution

Queue claims, renewal and completion are fenced by owner/attempt identity. A worker
that loses ownership stops before a handler or externally visible effect. Manual
and scheduled triggers admit work atomically and expose duplicate/running results.

Acceptance requires recovery tests for healthy long jobs, stale claims, retries,
cancellation, shutdown, non-cooperative work and transaction rollback. An
unresolved external effect blocks replay until its idempotency or operator decision
is known. API/web status reflects actual terminal outcome rather than task return
shape or HTTP success alone. See [AGENT_TASKS](AGENT_TASKS.md).

## PRD-06 — Data lifecycle, privacy and offboarding

Define retention, export and deletion by data class: credentials; staged/raw
content; normalized content; analytics/evidence; conversations/memory; delivery
receipts; usage/accounting; action audit; and backups. Publication date is not an
ingestion/retention timestamp.

Acceptance requires workspace-scoped preview and execution, credential revocation,
offboarding/export, backup expiry and audit of partial failures. Cleanup preserves
records needed for unresolved external effects, billing disputes and legal holds.
Logs, traces and notifications exclude secrets, raw provider payloads and customer
content unless an explicitly approved destination requires it.

## PRD-07 — Structured, safe AI boundaries

External content and model output are untrusted data. Structured output is compiled
from the scenario contract, validated before persistence and never interpreted as
permission or a tool instruction. Deterministic code owns arithmetic, identifiers,
state transitions and access checks.

Acceptance requires hostile-input, malformed/empty output, cancellation, partial
media, fallback and failed-save cases. Saved hashes/evidence reflect actual content
coverage; missing media cannot become complete coverage; useful valid siblings may
remain available. Source citations, observation time and uncertainty are retained
where the source supports them. See [AI pipeline](AI_PIPELINE_AND_PROMPTS.md).

## PRD-08 — Operational visibility and support

Operators need actionable, deduplicated and redacted signals for source freshness,
credential expiry, queue backlog/age, quarantined work, scheduler/worker/listener
freshness, incomplete delivery, unknown price and spend exposure.

Acceptance requires named support/escalation ownership, useful first-value and
recovery journeys, and agreed latency/freshness/usefulness measures for the managed
pilot. External tracing requires an explicit data-transfer and retention decision.

## PRD-09 — Safe action activation and honest release claims

External publication and mutation stay disabled or preview-only until the exact
capability passes its applicable gates. Activation is separate from deploying code
or merging a helper.

Acceptance requires actor-bound versioned approval, fresh rights and credentials,
claim/idempotency handling, recipient/object scope, audit trail, visible outcome
and kill switch. Release notes list only verified source and channel capabilities;
for example, MAX delivery does not imply MAX content collection.

## Release evidence packet

A release candidate records:

- exact application and migration revisions plus configuration profile;
- accepted capabilities and intentionally disabled actions;
- focused and integration test commands at their tested revisions;
- staging install/upgrade/restore and failure-recovery evidence;
- cross-workspace authorization and recipient tests;
- cost/admission behavior for known, free, undefined and failed calls;
- owner, monitoring, escalation, rollback and kill-switch procedures.

Store detailed evidence in the release PR or deployment record. This document
changes only when a stable gate or acceptance rule changes.
