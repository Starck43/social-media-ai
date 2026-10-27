# Action audit and support correlation contract (P11-T12)

**State:** contract proposal only. The durable store, hooks, support UI/export,
retention enforcement and migrations are not implemented by this document.
P11-T12's task card reserves migration/cleanup checks for a separately authorized
implementation task.

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
  duplicate money, provider payloads, delivery results or job state.
- Application logs are diagnostics, not durable business receipts or audit proof.

## Minimal event envelope

A future persisted event contains only these allowlisted fields:

| Field | Contract |
| --- | --- |
| `event_id` | Opaque unique event identity. |
| `tenant_id` | Owning workspace identifier; every read and export is scoped to it. |
| `occurred_at` | UTC time the event is recorded; a transactional `succeeded` event is authoritative only if its transaction commits. |
| `actor_kind`, `actor_id` | Authenticated user, service or scheduler identity captured from trusted execution context, never copied from request arguments. Preserve only the minimum identifier needed for support correlation. |
| `action` | Stable, bounded `domain.operation` code from an allowlist. |
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

## Outcome and transaction rules

- **Succeeded:** for a transactional state change, insert the event in the same
  transaction as the business change. Both commit or both roll back. A successful
  commit is the only basis for `succeeded`.
- **Denied:** record a minimal safe denial through a separate audit-write path;
  never place rejected raw arguments or authorization material in the event.
- **Rolled back:** the failed business transaction cannot contain its own durable
  rollback record. Record the safe outcome only after rollback, through a separate
  audit-write path, and do not claim that the business change committed.
- **Unknown:** use when commit/dispatch outcome cannot be established. Preserve
  existing idempotency fences and receipts; do not automatically retry an
  uncertain external effect. A correlation ID is not a replay key.

If durable audit writing fails, the system must expose that evidence gap through
an explicit safe operational signal; it must never silently report the event as
recorded. A later implementation must specify its failure policy for each
use-case before enabling the hook.

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
- Cross-workspace/global support access is not implied. If later required, it
  needs an explicit actor-bound permission, reason and separately reviewed scope.

## Immutability, redaction and retention

The application contract is append-only: no general update/delete route or ORM
mutation API for events. This is not a claim of cryptographic or database-level
tamper-proof storage; database-role enforcement is a separate design/verification
item.

Use an allowlist, not a denylist. Never persist credentials, access/session tokens,
raw tool arguments, prompts, customer content, raw provider payloads, SQL or
exception messages. Store only bounded safe action/resource identifiers and
reason codes. Existing business receipts remain the source of provider and billing
facts.

Classify these rows as the **action-audit** data class in the PRD-06 lifecycle
policy. Retention must be finite and owner-approved before enforcement; preserve
records needed for unresolved external effects, billing disputes and legal holds.
Offboarding/export and backup expiry must follow the workspace's approved
lifecycle rules. No numeric retention period is specified here because the
current source policy does not provide one. Retention deletion, legal-hold
handling and cleanup verification require separate authorization and tests.

## Required verification before implementation acceptance

A future implementation must prove, at minimum:

1. A committed mutation and its `succeeded` event are atomic.
2. Denied and rolled-back outcomes use the distinct safe path and never contain
   raw arguments or exception/provider text.
3. Unknown outcomes remain distinguishable and cannot trigger automatic replay.
4. Cross-tenant event IDs, filters and exports return no data; authorized
   same-tenant support reads are read-only and bounded.
5. Attempts to mutate/delete an event through application support interfaces are
   refused; the limits of database-level immutability are stated accurately.
6. Retention settings, legal holds, offboarding/export and backup expiry are
   covered by an owner-approved policy. No migration or cleanup acceptance is
   claimed by this contract-only slice.
