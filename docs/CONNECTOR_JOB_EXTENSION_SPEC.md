# Connector and job extension seams

**Status:** proposal for P11-T18; not implemented. No new connector, job type,
permission, provider operation, or capability is enabled by this document.

## Decision summary

No concrete new capability has been selected. This slice therefore documents
the narrow extension seam and the decision required before implementation; it
does not reopen deferred P8-T34 or implement P8-T33's additional VK categories.

The current collection path already has a typed provider boundary. Extend that
bounded contract only when a named pilot requires an operation the existing
`CollectionProvider` cannot express. Keep job registration explicit and
server-owned. Do not add a dynamic plugin loader, a universal connector schema,
or a generic workflow/event bus.

## Current contracts and gaps

| Existing surface | Contract today | Design implication |
| --- | --- | --- |
| `app/services/collection/adapters/base.py` | `CollectionProvider` protocol returns bounded `Page[T]` values. | Reuse it for compatible read-only collection work; do not create a parallel connector API. |
| `app/services/collection/pages.py` | `ProviderCapabilities` and `Page` distinguish supported, empty, bounded, and unsupported results. | Preserve explicit unsupported/partial outcomes; never convert unsupported into an empty result. |
| `app/services/collection/context.py` | Platform capability map and adapter construction are explicit for VK and Telegram. | A future provider enters through an explicit allowlisted factory, not import-time discovery. |
| `app/services/social/factory.py` | Constructs the existing authenticated platform client. | Credential resolution/ownership remains separate from normalized collection operations. |
| `app/types/enums/job_types.py` | `JobType` is a finite set of six persisted string values. | Reuse its values; do not change the persisted job-type column or add a schema migration for registration. |
| `app/jobs/handlers.py` / `app/jobs/dispatcher.py` | `HANDLERS` maps job strings to facades; the dispatcher claims, scopes, executes, and commits outcomes. | Add a typed registration wrapper at this boundary before moving handler packages. Preserve claim fencing and retry policy. |

## Proposed architecture

```mermaid
flowchart LR
  Surface[HTTP / web / CLI / agent] --> Policy[Existing use case, permission and admission]
  Policy --> Queue[(PostgreSQL jobs)]
  Scheduler[Existing scheduler] --> Queue
  Queue --> Claim[Dispatcher + fenced JobClaim]
  Claim --> Registry[Explicit JobType registry]
  Registry --> Facade[Typed compatibility facade]
  Facade --> Handler[Existing job handler / domain service]
  Handler --> Pipeline[Platform-neutral planner, bounds and persistence]
  Pipeline --> Context[CollectionContext]
  Context --> Factory[Explicit platform factory]
  Factory --> Protocol[CollectionProvider + Page[T]]
  Protocol --> VK[VK adapter / existing VK client]
  Protocol --> TG[Telegram adapter / existing Telegram client]
```

The connector seam ends at normalized provider operations. Planning, task
selection, quotas, deduplication, persistence, and model calls remain owned by
the application pipeline. A connector receives only the already-resolved
credential/client and operation arguments; it cannot grant permissions, choose
the payer, change the task scope, or persist results.

### Connector contract for a later selected capability

1. Name the pilot and exact read or write operation before changing an interface.
   If it is not the existing social collection domain, do not force it into
   `Source`, `Platform`, or `CollectionProvider`.
2. Prefer an operation-specific protocol or a small optional protocol when not
   every provider supports the operation. Keep read, send, and delete capabilities
   separate. Write operations require their own consent, recipient/object scope,
   idempotency, cancellation, and ambiguous-outcome contract.
3. For paged reads, retain explicit bounds, continuation/watermark semantics,
   rate-limit/retry classification, and separate `empty`, `unsupported`,
   `partial`, and `failed` outcomes. Provider payloads and credentials never
   become the normalized result contract.
4. Resolve credential owner and scopes through the existing connection/credential
   services. Recheck actor, tenant, capability, and payer policy at execution.
5. Add one explicit factory registration and contract tests for the chosen
   capability. Leave unsupported platforms disabled and fail closed.

### Typed job registration target

The first implementation slice should wrap—not relocate—the existing handlers:

```python
JobHandlerSpec[PayloadT, ResultT](
    job_type=JobType.COLLECT,
    parse_payload=validate_payload,
    execute=run_handler,
    present_result=bounded_result,
)
```

`PayloadT` is a validated, job-specific input; `ResultT` is an internal result
adapted into the existing bounded JSON receipt. The execution context is built
from the acquired `JobClaim` and trusted tenant scope, not copied from payload
fields. The registry is a static mapping keyed by `JobType`; duplicate or
unregistered values fail closed. Persisted unknown job types retain the current
terminal-failure behavior rather than being retried or dynamically loaded.

The facade adapts current handler signatures until each handler is explicitly
migrated. It does not alter transaction ownership, attempt budgets, backoff,
claim fencing, notification projection, or the single `python -m app.runtime`
owner. Typed payload/result migration proceeds one handler at a time.

## Adoption and verification gate

Before implementation, the owner selects one concrete capability. The
implementation task then names its exact protocol, owner/scopes, bounds,
outcomes, compatibility path, rollback, and any schema/API changes.

For that one capability, acceptance should cover:

- registry completeness and rejection of duplicate/unknown identifiers;
- typed payload validation and the existing public result shape;
- enabled and disabled capabilities, with unsupported distinct from empty;
- paging bounds, cursor/watermark behavior, rate limits, retryability, and
  partial/unknown outcomes;
- fresh tenant/actor/credential checks and cross-tenant isolation;
- no duplicate external effect on retry, and no claim-loss outcome presented as
  success.

Use mocked providers for automated tests. Live provider access, capability
activation, schema changes, and cross-surface pilots remain separately scoped.
Rollback is removal of the selected registration/adapter while retaining the
existing handler and compatibility facade; no move-only package split is part
of this proposal.

## Documents and source contracts

- [P11-T18 task card](tasks/p11-t18.md)
- [P11-T04 accepted task orchestration](audit/p11-t04.md)
- [P11-T06 accepted typed launch/error contract](audit/p11-t06.md)
- [Product extension contracts](PRODUCT_PLAN.md#extension-contracts)
- [Agent task-plan model](AGENT_TASKS_PLAN.md#model)
- [Queue and claim lifecycle](AGENT_TASKS_QUEUE.md#how-a-run-happens)