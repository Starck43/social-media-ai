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
| 2 | PRD-03 — complete usage and spend | Paid-attempt accounting separate from daily analytics — **DONE** ([PR #163](https://github.com/Starck43/social-media-ai/pull/163)): analyzer partial-collision, relevance-filtered and no-result runs keep journal evidence (PR #114), and every non-analyzer paid attempt is now journaled with identity, usage, tariff snapshot, cost state and outcome — each `chat_with_fallback` try (agent chat, learning, reflection, prompt suggestion), the model-test tool and the digest summary. Next: atomic concurrent budget admission on the receipt foundation (PRs #137, #154), so parallel attempts cannot bypass the cap; admin connection tests need an operator-payer decision first. Pricing truth is owner-decided (2026-10-12): FREE only on an explicit zero, the model's own answer arbitrates cost, no model price with an empty table is UNDEFINED — never free, never zero, always tracked. | Admission: no request starts without committed admission under the current cap; parallel attempts cannot silently bypass it; undefined cost is never treated as free or zero; `ai_analytics`, `jobs.llm_cost` and `digest_runs.llm_cost` stay projections; no double counting. |
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
| PRD-03 immutable outcome evidence | Remote Notion agent; `feat/llm-outcome-evidence`, [Draft PR #168](https://github.com/Starck43/social-media-ai/pull/168), head `5511a69` | BLOCKED — shared-schema read-only preflight pending | Remote 43 focused tests/compiler/scoped formatters PASS; owner-local larger 116-test standalone bundle and actual ORM registry PASS at the same head. After the competing runner ends, fresh idle/intake checks then read-only preflight only; return source/redacted evidence. Do not repeat accepted checks, interrupt another runner, bootstrap/register permissions or apply 0094. New-schema PG gate needs separate approval; storage stays unwired. |
| PRD-04 loopback-only database binding | Owner, local only; no remote branch or PR | DEFERRED — pre-production | Resume before the production release: restrict the checked-in PostgreSQL host publish to `127.0.0.1:5432:5432`, preserve container `db:5432` and local-owner tools, and prove network isolation with local/staging evidence. `docker/docker-compose.yml` on `dev` still publishes `5432:5432`. Do not start/restart Compose: API startup runs Alembic. |
| PRD-06 data-retention rollout | Owner; no branch or PR (code merged in `dev` via PR #139) | DEFERRED — deployment not yet approved | Owner decision (2026-10-12): do not deploy yet. Before deploying, back up `ai_analytics` if history matters and confirm `DATA_RETENTION_MONTHS` (default 1): the first daily prune after deploy irreversibly deletes analyses whose last write is older than the cutoff. Remove this row after the deployment is verified. |
| PRD-02 debug analytics endpoints and OAuth callback escaping | Remote Notion agent; branch `fix/debug-endpoints-callback-escape`, [Draft PR #170](https://github.com/Starck43/social-media-ai/pull/170), head `f4a34bf` | IN_PROGRESS — owner-local validation pending | `/api/v1/dashboard/debug/*` require `aianalytics.view` like the other analytics reads (a member without it gets 403); `/api/v1/social/callback` HTML-escapes the `error` query value and error messages. Regression tests added; owner-local validation required before Ready. |
| Web chat quick replies and thinking indicator | Remote Notion agent; branch `feat/chat-quick-replies`, Draft PR pending | IN_PROGRESS — owner review pending | `/app/chat`: the agent can offer one-tap answer buttons (trailing `[[options: …]]` marker; pending confirmations get «Да»/«Нет»), an in-log thinking indicator, refreshed `/help` and empty-state starters, smaller composer font, and a `source_enable` tool. Buttons send ordinary messages, so permissions and confirmation are unchanged; messengers get a plain-text choices hint. Owner-local DB/UI check before Ready. |

For a new implementation lane, replace or add one row with its exact bounded
acceptance condition, owner, branch/PR and state. Remove the row after verified
integration; do not copy the PR narrative here.

## Selection rules

- Verify the gap in current code before treating a readiness statement as a bug.
- Read only the selected subsystem document and relevant call sites.
- Preserve delivered behavior listed in the implementation index.
- Record detailed design decisions and test evidence in the PR.
- Update this board only when priority, ownership or integration state changes.
