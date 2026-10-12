# Development priorities and ownership

This is the only prioritized open-work queue. Product phases live in
[PRODUCT_PLAN](../PRODUCT_PLAN.md), stable acceptance gates in
[BUSINESS_PRODUCTION_READINESS](../BUSINESS_PRODUCTION_READINESS.md), and delivered
foundations in [IMPLEMENTATION_STATUS](../IMPLEMENTATION_STATUS.md).

## Current checkpoint

Verified `origin/dev`: `5941642c4caa052ef54e90e03ba1c00f3433c4c4` on
2026-10-12. Merged packages through PR #161, including PRs #137, #151, #153 and #158, are
integrated. Active lanes are listed
under Active ownership. Refresh `dev`, open PRs and this row before selecting work.

Nine readiness areas remain open. They are gates, not nine commits or an effort
estimate. Select one bounded change with an observable acceptance condition.

## Prioritized open work

| Priority | Gate | Next bounded outcome | Acceptance boundary |
| --- | --- | --- | --- |
| 1 | PRD-05 — truthful queue execution | Choose the next recovery or admission gap after the delivered claim renewal, preflight, heartbeat and atomic-trigger slices. | No handler starts after claim loss; recovery cannot duplicate an unresolved external effect; execution outcome remains truthful. |
| 2 | PRD-03 — complete usage and spend | Paid-attempt accounting separate from daily analytics: the partial capture that collides with an existing daily row is still skipped today, and a skipped, filtered or reaped daily row must not take the paid attempt's evidence with it. Record every paid attempt in the per-attempt journal (identity, usage, tariff snapshot, cost state, outcome), and extend the same journal/projection separation to the non-analyzer call paths: fallback, learning/reflection and agent chat. Pricing truth is owner-decided (2026-10-12): FREE only on an explicit zero (model-reported zero cost or an explicit 0 in the tariff table), the model's own answer arbitrates cost, and no model price with an empty table is UNDEFINED — never free, never zero, always tracked in statistics. | Each paid attempt whose daily aggregate row is skipped, filtered or reaped keeps its journal evidence; `ai_analytics`, `jobs.llm_cost` and `digest_runs.llm_cost` stay projections, never the ledger; no double counting; undefined cost is never treated as free or zero; free requires an explicit zero; parallel attempts cannot silently bypass the cap. |
| 3 | PRD-02 — identity and approvals | Close one actor/workspace/tool/write gap, including revocation, expiry or replay where applicable. | Unknown, removed or revoked actors fail closed; approval is actor-bound, versioned and single-use. |
| 4 | PRD-01 — tenant-safe delivery | Legacy partial-delivery stop, zero-success terminal outcome and scheduled/forced publisher fences and lease-loss evidence are integrated (PRs #106, #112, #113, #115, #120). Next: staging operator-recovery evidence. | Successful recipients are not resent; ambiguous sends stop for review; recipients and credentials belong to the workspace. |
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
| PRD-03 immutable preparation receipts | Remote Notion agent; merged [PR #137](https://github.com/Starck43/social-media-ai/pull/137) (`9f76c81`); `test/receipt-storage-pg-evidence`, [Draft PR #154](https://github.com/Starck43/social-media-ai/pull/154), corrected head `569c368` | #137 MERGED; #154 BLOCKED — corrected verifier local checks pending | Owner-local 16 tests/compiler/formatters PASS at `aa0a744`; PG stopped read-only at faulty Index parity, baseline 44 receipts/79 journal/guards O/public 0092 unchanged. Corrected separate UNIQUE/index reflection, 12 new regressions (28 total); new-head Python/style/PG NOT RUN remotely, no .venv. Re-intake #154, run no-DB then verbatim PG command from PR. No baseline tuning/reapply/bootstrap/cleanup, runtime change or whole-gate acceptance. |
| PRD-04 loopback-only database binding | Owner, local only; no remote branch or PR | DEFERRED — pre-production | Resume before the production release: restrict the checked-in PostgreSQL host publish to `127.0.0.1:5432:5432`, preserve container `db:5432` and local-owner tools, and prove network isolation with local/staging evidence. `docker/docker-compose.yml` on `dev` still publishes `5432:5432`. Do not start/restart Compose: API startup runs Alembic. |
| PRD-06 data-retention rollout | Owner; no branch or PR (code merged in `dev` via PR #139) | DEFERRED — deployment not yet approved | Owner decision (2026-10-12): do not deploy yet. Before deploying, back up `ai_analytics` if history matters and confirm `DATA_RETENTION_MONTHS` (default 1): the first daily prune after deploy irreversibly deletes analyses whose last write is older than the cutoff. Remove this row after the deployment is verified. |
| PRD-05 cancellation uncertainty evidence | Remote Notion agent; planned `fix/cancel-uncertainty-evidence` to `dev`; reservation branch `docs/reserve-cancel-uncertainty` | RESERVED — owner-approved scope | Classify new and legacy operator cancellations as unconfirmed external outcomes; show uncertainty and no ordinary replay offer; preserve records in scheduled prune; retain attempt/result/cost evidence and reject late completion of the cancelled claim. No schema, replay-path or rollout change. Implement after the reservation docs-PR is integrated; owner-local validation required. |

For a new implementation lane, replace or add one row with its exact bounded
acceptance condition, owner, branch/PR and state. Remove the row after verified
integration; do not copy the PR narrative here.

## Selection rules

- Verify the gap in current code before treating a readiness statement as a bug.
- Read only the selected subsystem document and relevant call sites.
- Preserve delivered behavior listed in the implementation index.
- Record detailed design decisions and test evidence in the PR.
- Update this board only when priority, ownership or integration state changes.
