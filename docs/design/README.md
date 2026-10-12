# Development priorities and ownership

This is the only prioritized open-work queue. Product phases live in
[PRODUCT_PLAN](../PRODUCT_PLAN.md), stable acceptance gates in
[BUSINESS_PRODUCTION_READINESS](../BUSINESS_PRODUCTION_READINESS.md), and delivered
foundations in [IMPLEMENTATION_STATUS](../IMPLEMENTATION_STATUS.md).

## Current checkpoint

Verified local `dev`: `84ee16d284a6355006dd71c7157a0744ab203ddd` on
2026-10-12. Packages through PR #104 are integrated. Active lanes are listed
under Active ownership. Refresh `dev`, open PRs and this row before selecting work.

Nine readiness areas remain open. They are gates, not nine commits or an effort
estimate. Select one bounded change with an observable acceptance condition.

## Prioritized open work

| Priority | Gate | Next bounded outcome | Acceptance boundary |
| --- | --- | --- | --- |
| 1 | PRD-05 — truthful queue execution | Choose the next recovery or admission gap after the delivered claim renewal, preflight, heartbeat and atomic-trigger slices. | No handler starts after claim loss; recovery cannot duplicate an unresolved external effect; execution outcome remains truthful. |
| 2 | PRD-03 — complete usage and spend | Approve one every-call accounting and concurrent budget-admission contract covering fallback, errors and unknown tariffs. | Each paid attempt is durable; unknown cost is not zero; parallel work cannot silently bypass the cap. |
| 3 | PRD-02 — identity and approvals | Close one actor/workspace/tool/write gap, including revocation, expiry or replay where applicable. | Unknown, removed or revoked actors fail closed; approval is actor-bound, versioned and single-use. |
| 4 | PRD-01 — tenant-safe delivery | Legacy partial-delivery stop, zero-success terminal outcome and scheduled/forced publisher fences are integrated (PRs #106, #112, #113, #115). Next: lease-loss behavior and staging operator-recovery evidence. | Successful recipients are not resent; ambiguous sends stop for review; recipients and credentials belong to the workspace. |
| 5 | PRD-07 — safe AI boundaries | Close one typed-output, hostile-input, evidence-coverage or failed-output persistence gap. | Invalid output cannot gain authority; partial coverage is explicit; useful siblings remain available. |
| 6 | PRD-06 — lifecycle and privacy | Decide and implement one bounded retention, export, offboarding, credential-revocation or logging contract. | Tenant data is isolated and auditable; deletion preserves evidence required for unresolved effects and accounting. |
| 7 | PRD-04 — reproducible operation | Produce one staging-backed install, upgrade, rollback, restore or runtime-ownership proof. | One owner per migrator/scheduler/listener; private database and recoverable encrypted credentials. |
| 8 | PRD-08 — first value and support | Add one actionable redacted signal or a bounded read-only first-value journey using existing outcomes. | Operators can detect and own stale sources, backlog, stalled loops, delivery failures or spend anomalies. |
| 9 | PRD-09 — safe activation | Define one capability-specific activation contract for an external write. | Disabled by default; fresh authorization, idempotency, audit and kill switch; release claim matches verified capability. |

Dependencies and disabled-feature safeguards override this order. Product expansion
starts only when the applicable foundation gates are accepted.

## Active ownership

| Work | Owner / branch / PR | State | Next action |
| --- | --- | --- | --- |
| PRD-03 analyzer accounting certainty | Remote Notion agent; `fix/analyzer-accounting-certainty`, Draft PR to `dev` | IN_PROGRESS | Preserve missing/invalid provider token evidence as unknown in the journal without changing legacy USD projections; reject missing/non-finite/negative tariffs instead of substituting zero. Actual-source negative regressions and existing pipeline contract. No schema, chat/fallback, budget activation or live changes; whole PRD-03 stays open. |
| PRD-04 loopback-only database binding | Remote Notion agent; `fix/compose-db-loopback`, Draft PR to `dev` | IN_PROGRESS | Restrict the checked-in PostgreSQL host publish to `127.0.0.1:5432:5432`; preserve container `db:5432` and local-owner tools. Prepare configuration/negative regression checks; actual network isolation needs local/staging evidence. Do not start/restart Compose: API startup runs Alembic. No DDL/deploy/firewall/runtime-owner changes. |
| PRD-05 unknown-handler terminal outcome | Local session, `dev`; dispatcher + focused tests | VALIDATED | A claimed job with no registered handler records a terminal `failed` outcome even when the caller allows retries; no handler runs and no automatic retry is scheduled. Claim loss and outcome-write uncertainty stay explicit errors. 30 focused tests + 22 subtests passed. Remove this row after verified integration. |

For a new implementation lane, replace or add one row with its exact bounded
acceptance condition, owner, branch/PR and state. Remove the row after verified
integration; do not copy the PR narrative here.

## Selection rules

- Verify the gap in current code before treating a readiness statement as a bug.
- Read only the selected subsystem document and relevant call sites.
- Preserve delivered behavior listed in the implementation index.
- Record detailed design decisions and test evidence in the PR.
- Update this board only when priority, ownership or integration state changes.
