# Action audit and support correlation contract (P11-T12)

**State:** implementation in progress in [PR #272](https://github.com/Starck43/social-media-ai/pull/272).
The implementation adds tenant-scoped persistence, task-admission hooks,
read-only API views/export, and plan-aligned retention. Alembic revision `0118`
is authorized only against `test_schema`; no production/public-schema apply or
backfill is authorized. Database-backed acceptance and cleanup verification
remain pending.

## Purpose and boundaries

The action audit is a tenant-scoped, append-only record of **who attempted which
business use-case, against which resource, and with what known outcome**. It is
for support and security review, not a second source of truth for business state.

Keep existing records authoritative:

- `bot_actions.history` is the transition evidence for the action-proposal ledger;
  it is not the cross-surface audit and is not tamper-proof. See
  [models](MODELS.md#tenant-scoped-conversation-memory-and-action-audit) and
  [action activation](ACTION_ACTIVATION.md#idempotency-audit-and-uncertain-effects).
- Jobs, entitlement usage, delivery receipts and billing/LLM records keep their
  existing identities and facts. The audit stores references only; it does not
  duplicate money, provider payloads, delivery results or job state. In
  particular, an audit or diagnostic log is never a payment/delivery receipt.
- Application logs are diagnostics, not durable business receipts or audit proof.

## Minimal event envelope

The persisted `action_audit_events` row contains only these allowlisted fields:

| Field | Contract |
| --- | --- |
| `event_id` | Opaque unique event identity. |
| `tenant_id` | Owning workspace identifier; every read and export is scoped to it. |
| `occurred_at` | UTC time the event is recorded; a transactional `succeeded` event is authoritative only if its transaction commits. |
| `actor_kind`, `actor_id` | Authenticated user, service or scheduler identity captured from trusted execution context, never copied from request arguments. Preserve only the minimum identifier needed for support correlation. |
| `action` | Stable, bounded `domain.operation` code from an allowlist. The current hook uses `scheduling.task_admit`. |
| `resource_kind`, `resource_id` | Optional type and opaque identifier; never a name, URL with credentials, message body or raw resource content. |
| `outcome` | Exactly one of `succeeded`, `denied`, `rolled_back`, or `unknown`. Do not collapse an uncertain external/commit result into success or failure. |
| `reason_code` | Optional bounded safe code from an allowlist; no exception text or provider response. |
| `request_id` | Optional validated correlation token, distinct from an idempotency key and never an authorization credential. |
| `job_id` | Optional existing job identity for scheduled/asynchronous work; do not copy job payload/status. |
| `receipt_ref` | Optional reference to an existing action, delivery, entitlement or billing receipt when one exists; do not create a parallel ledger. |

Actor, resource, job and receipt references are historical correlation values, not
authority grants. They must not be trusted to authorize an operation or bypass a
fresh tenant/membership check. If referenced rows are pruned, the event must retain
only the safe identifiers needed by the approved retention policy.

## Separate outcome paths

The owner approved three distinct paths. Keep them semantically separate; this
does not authorize treating every path as a durable audit row or inventing a
second billing/delivery ledger.

- **Succeeded:** for a transactional state change, insert the event in the same
  transaction as the business change. Both commit or both roll back. This is the
  transactional audit-evidence path; a successful commit is the only basis for
  `succeeded`. The current hook records task admission in the same transaction
  that saves the job/task admission result, using the existing job ID.
- **Denied and rolled back:** use a distinct, safe structured logging path after
  the refusal or rollback. Do not claim a failed/denied attempt was written as a
  successful durable audit event, and never include rejected arguments,
  authorization material, secrets, or exception/provider text. These outcomes
  are diagnostic logs, not rows in the durable event table.
- **Unknown:** use a separate outcome category/path when commit or external
  dispatch cannot be established. Keep it distinguishable from both success and
  failure; preserve existing idempotency fences and billing/delivery identities,
  and do not automatically retry an uncertain external effect. A correlation ID
  is not a replay key. Unknown task-admission observations are persisted as
  `unknown` events and sent to a separate logger; if that write fails, the logger
  emits an explicit `unknown_persistence_gap` signal without exception text.

Do not collapse the three paths into one undifferentiated stream or outcome.
Shared validation/helpers may be reused only if distinct routing, storage
semantics, redaction and outcome guarantees remain explicit. Logging alone is
diagnostic evidence, not a payment receipt, delivery receipt, or proof that a
business transaction committed.

If durable audit writing fails, the system must expose that evidence gap through
an explicit safe operational signal; it must never silently report the event as
recorded. Successful task admission fails atomically if its event cannot be
inserted. For unknown task admission, failure to persist the event is reported
through the separate safe persistence-gap log.

## Access, views and export

- Every query starts with the authorized workspace boundary; never find a row by
  event ID first and apply tenant filtering afterward.
- Use the existing tenant-scoped manager and current actor/workspace permission
  checks. Support access is read-only; no support route may approve, replay,
  change, or delete a business action.
- Views and exports expose only the event envelope above, have bounded time
  windows and page sizes, and apply the same tenant and permission checks. Export
  must not include raw payloads, prompts, message contents, provider responses,
  secrets or exception text.
- Implemented JSON endpoints are `GET /api/v1/audit/events` (`VIEW`) and
  `GET /api/v1/audit/events/export` (`EXPORT`). Both require an authenticated
  active workspace member, fence every query to the selected workspace, default
  to the last 30 days, reject windows over 90 days, and cap results at 100 and
  1,000 rows respectively. `before_id` provides bounded descending pagination.
  The migration grants these permissions to workspace owners and admins only.
- Cross-workspace/global support access is not implied. If later required, it
  needs an explicit actor-bound permission, reason and separately reviewed scope.

## Immutability, redaction and retention

The application contract is append-only: no general update/delete route or ORM
mutation API for events. This is not a claim of cryptographic or database-level
tamper-proof storage; database-role enforcement is a separate design/verification
item.

Use an allowlist, not a denylist. Never persist or log credentials,
access/session tokens, raw tool arguments, prompts, customer content, raw
provider payloads, SQL or exception messages. Store only bounded safe
action/resource identifiers and reason codes. Reuse existing billing, delivery
and job identities as references; existing business receipts remain the source
of provider, payment and delivery facts.

Classify these rows as the **action-audit** data class in the PRD-06 lifecycle
policy. Retention uses the existing workspace-plan `retention_days` values
(Starter 7, Pro 30, Business 90) in `Tenant.PLAN_LIMITS`; no duplicate setting
was introduced in `config.py`. The regular prune handler applies this limit per
workspace. This only deletes audit rows: it does not delete or replace task,
billing, entitlement or delivery records, which retain their own identities and
facts. Offboarding/export and backup expiry still follow the workspace's
approved lifecycle rules.

The database trigger blocks ordinary row updates/deletes and truncation, while
the explicit retention path uses a transaction-local marker. This is an
application-level guard, not cryptographic/database-role tamper-proof storage:
a database role able to issue arbitrary SQL can set the marker. External log
sink retention is not controlled by this application. No legal-hold workflow is
implemented here; production lifecycle acceptance must verify the applicable
hold/offboarding policy before enabling destructive cleanup.

## Required verification before implementation acceptance

A complete acceptance run must prove, at minimum:

1. A committed mutation and its `succeeded` event are atomic.
2. Denied and rolled-back outcomes use the distinct safe path and never contain
   raw arguments or exception/provider text.
3. Unknown outcomes remain distinguishable and cannot trigger automatic replay.
4. Cross-tenant event IDs, filters and exports return no data; authorized
   same-tenant support reads are read-only and bounded.
5. Attempts to mutate/delete an event through application support interfaces are
   refused; the limits of database-level immutability are stated accurately.
6. Retention settings, legal holds, offboarding/export and backup expiry are
   covered by an owner-approved policy. Current source-only checks and offline
   migration generation do not substitute for test-schema migration, database
   tests, cleanup verification or production lifecycle approval.
