# Development priorities and ownership

This is the only prioritized open-work queue. Product phases live in
[PRODUCT_PLAN](../PRODUCT_PLAN.md), stable acceptance gates in
[BUSINESS_PRODUCTION_READINESS](../BUSINESS_PRODUCTION_READINESS.md), and delivered
foundations in [IMPLEMENTATION_STATUS](../IMPLEMENTATION_STATUS.md).

## Current checkpoint

Verified local `dev`: `84ee16d284a6355006dd71c7157a0744ab203ddd` on
2026-10-12. Packages through PR #104 are integrated. No implementation lane is
reserved. Refresh `dev`, open PRs and this row before selecting work.

Nine readiness areas remain open. They are gates, not nine commits or an effort
estimate. Select one bounded change with an observable acceptance condition.

## Prioritized open work

| Priority | Gate | Next bounded outcome | Acceptance boundary |
| --- | --- | --- | --- |
| 1 | PRD-05 — truthful queue execution | Choose the next recovery or admission gap after the delivered claim renewal, preflight, heartbeat and atomic-trigger slices. | No handler starts after claim loss; recovery cannot duplicate an unresolved external effect; execution outcome remains truthful. |
| 2 | PRD-03 — complete usage and spend | Approve one every-call accounting and concurrent budget-admission contract covering fallback, errors and unknown tariffs. | Each paid attempt is durable; unknown cost is not zero; parallel work cannot silently bypass the cap. |
| 3 | PRD-02 — identity and approvals | Close one actor/workspace/tool/write gap, including revocation, expiry or replay where applicable. | Unknown, removed or revoked actors fail closed; approval is actor-bound, versioned and single-use. |
| 4 | PRD-01 — tenant-safe delivery | Validate per-recipient/per-part retry behavior for partial failure, lost acknowledgement and lease loss. | Successful recipients are not resent; ambiguous sends stop for review; recipients and credentials belong to the workspace. |
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
| PRD-02 exact intent consumption | Remote Notion agent, `fix/atomic-confirmation-consumption`; PR to `dev` | IN_PROGRESS | Only one concurrent confirmation may dispatch the same stored intent; stale cleanup preserves replacements. Draft pending project-venv unit/runtime checks and local PostgreSQL commit/serialization evidence. No schema or live-action changes. |
| PRD-03 attempt journal (analyzer attempts) | Local session, `dev`; `llm_attempts` ledger + analyzer wiring | VALIDATED | One journal row per analyzer provider attempt: usage, tariff snapshot, cost certainty and outcome survive a skipped daily row, a filtered day and a failed batch. Remaining: fallback, learning and agent-chat attempts; per-dispatch identity; concurrent budget admission. |
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
