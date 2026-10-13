# Development priorities and ownership

This is the only prioritized open-work queue. Product phases live in
[PRODUCT_PLAN](../PRODUCT_PLAN.md), stable acceptance gates in
[BUSINESS_PRODUCTION_READINESS](../BUSINESS_PRODUCTION_READINESS.md), and delivered
foundations in [IMPLEMENTATION_STATUS](../IMPLEMENTATION_STATUS.md).

## Current checkpoint

Verified `origin/dev`: `00e878389a498a35f0e83e0949397f5c6b0f6903` on
2026-10-13. PR #137 is merged at accepted head `e8d335ef17cf4b851aaded5a30c3d743b454263e`;
receipt/verifier (#154), outcome storage (#168) and non-analyzer journal (#163) are integrated,
but admission/reservation/settlement and client activation remain open. Reuse the
[original PG evidence and deviations](https://github.com/Starck43/social-media-ai/pull/137#issuecomment-6130408067);
no repeat 0093 application or retained-receipt cleanup. Active lanes are listed
under Active ownership. Refresh `dev`, open PRs and this row before selecting work.

Nine readiness areas remain open. They are gates, not nine commits or an effort
estimate. Select one bounded change with an observable acceptance condition.

## Prioritized open work

| Priority | Gate | Next bounded outcome | Acceptance boundary |
| --- | --- | --- | --- |
| 1 | PRD-05 — truthful queue execution | Choose the next recovery or admission gap after the delivered claim renewal, preflight, heartbeat and atomic-trigger slices. | No handler starts after claim loss; recovery cannot duplicate an unresolved external effect; execution outcome remains truthful. |
| 2 | PRD-03 — complete usage and spend | Paid-attempt accounting separate from daily analytics — **DONE** ([PR #163](https://github.com/Starck43/social-media-ai/pull/163)): analyzer partial-collision, relevance-filtered and no-result runs keep journal evidence (PR #114), and every non-analyzer paid attempt is now journaled with identity, usage, tariff snapshot, cost state and outcome — each `chat_with_fallback` try (agent chat, learning, reflection, prompt suggestion), the model-test tool and the digest summary. Next: atomic concurrent budget admission on the receipt foundation (PRs #137, #154), so parallel attempts cannot bypass the cap; admin connection tests need an operator-payer decision first. Pricing truth is owner-decided (2026-10-12): FREE only on an explicit zero; structured provider USD price, never generated text, arbitrates cost; no resolvable price is UNDEFINED — never free, never zero, always tracked. | Admission: no request starts without committed admission under the current cap; parallel attempts cannot silently bypass it; undefined cost is never treated as free or zero; `ai_analytics`, `jobs.llm_cost` and `digest_runs.llm_cost` stay projections; no double counting. |
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
| Atomic LLM receipt and budget reservation | Remote Notion agent; `feat/llm-budget-reservation`; [Draft PR #180](https://github.com/Starck43/social-media-ai/pull/180) | BLOCKED — UNWIRED, no-DB and PG evidence pending | Implementation `9d24bae9718dbbff9fd605c23381e7b80fc3d32b`: one owned transaction locks payer/day, checks the current cap and stores receipt plus reservation. 31 isolated checks prepared, NOT RUN (remote project venv/Kilo unavailable); local assignment in PR. Preserve FREE/structured-provider-price/UNDEFINED policy. No settlement, clients, live activation, schema application or baseline provisioning; PG plan requires separate approval. |
| Stored XSS in web template rendering | Unassigned; no implementation branch or PR | OPEN — security priority | app/web/deps.py constructs Jinja Environment without autoescape; owner reproduced script execution from a stored task name on dev. Evidence: [PR175 visual report](https://github.com/Starck43/social-media-ai/pull/175#issuecomment-6152533703). Next: scope an escaping fix and audit intentional safe/HTML and Alpine/JSON contexts. Accept when hostile stored names render as inert text on affected pages and ordinary templates/bindings remain correct. PR175 fixes only its confirmation markup, not this gap; no live-data probes. |
| Mobile web page-width overflow | Unassigned; no implementation branch or PR | OPEN — inherited layout follow-up | Owner observed app document width 432px under 375px mobile emulation; confirmation dialog remains contained. Evidence: [PR175 visual report](https://github.com/Starck43/social-media-ai/pull/175#issuecomment-6152533703). Next: identify the overflowing base layout element independently of the Chromium emulation discrepancy. Accept when affected pages fit a verified 375px viewport without unintended horizontal scrolling and confirmation focus/keyboard behavior is retained. |
| PRD-02 digest send permission parity | Remote Notion agent; `fix/digest-send-permission-parity`, [Draft PR #176](https://github.com/Starck43/social-media-ai/pull/176) | BLOCKED — shared-writer exclusion and coordinator release; DB acceptance pending | Owner reports verification-only combined-tree bootstrap SIM_PASS; attribution and pins recorded in PR. Standalone bootstrap remains unsafe. Next: coordinator releases pause after all writer-owner acknowledgements, fresh read-only catalog/drift check, then sequential Kilo/pare-test DB tests and membership matrix with mocked effects. No schema repair/live sends or PR168 acceptance. |
| PRD-04 loopback-only database binding | Owner, local only; no remote branch or PR | DEFERRED — pre-production | Resume before the production release: restrict the checked-in PostgreSQL host publish to `127.0.0.1:5432:5432`, preserve container `db:5432` and local-owner tools, and prove network isolation with local/staging evidence. `docker/docker-compose.yml` on `dev` still publishes `5432:5432`. Do not start/restart Compose: API startup runs Alembic. |
| PRD-06 data-retention rollout | Owner; no branch or PR (code merged in `dev` via PR #139) | DEFERRED — deployment not yet approved | Owner decision (2026-10-12): do not deploy yet. Before deploying, back up `ai_analytics` if history matters and confirm `DATA_RETENTION_MONTHS` (default 1): the first daily prune after deploy irreversibly deletes analyses whose last write is older than the cutoff. Remove this row after the deployment is verified. |

For a new implementation lane, replace or add one row with its exact bounded
acceptance condition, owner, branch/PR and state. Remove the row after verified
integration; do not copy the PR narrative here.

## Selection rules

- Verify the gap in current code before treating a readiness statement as a bug.
- Read only the selected subsystem document and relevant call sites.
- Preserve delivered behavior listed in the implementation index.
- Record detailed design decisions and test evidence in the PR.
- Update this board only when priority, ownership or integration state changes.
