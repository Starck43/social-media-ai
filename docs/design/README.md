# Development priorities and ownership

This is the only prioritized open-work queue. Product phases live in
[PRODUCT_PLAN](../PRODUCT_PLAN.md), stable acceptance gates in
[BUSINESS_PRODUCTION_READINESS](../BUSINESS_PRODUCTION_READINESS.md), and delivered
foundations in [IMPLEMENTATION_STATUS](../IMPLEMENTATION_STATUS.md).

## Current checkpoint

Verified `origin/dev`: `7af4e0c7f01406c56de585dd77c45cbaf53a7eb3` (merge of [PR #184](https://github.com/Starck43/social-media-ai/pull/184)).
Test-bootstrap evidence protection and stored-text escaping are integrated;
prior delivered foundations remain in the implementation index. Active lanes
are listed under Active ownership. Refresh `dev`, open PRs and this row before selecting work.

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
| Task-add source selection initialization | Unassigned; no implementation branch or PR | OPEN — inherited Alpine runtime follow-up | Owner reproduced addTaskForm().task.source_ids missing while :checked calls includes; pre-fix control has the same error. Evidence: [PR178 runtime report](https://github.com/Starck43/social-media-ai/pull/178#issuecomment-6154504317). Next: initialize source_ids in the actual add-task model and verify selection/reset with real Alpine. Accept when opening the form has no includes error and selected IDs round-trip without changing submit semantics; no server/DB/live probes required. |
| Mobile web page-width overflow | Unassigned; no implementation branch or PR | OPEN — inherited layout follow-up | Owner observed app document width 432px under 375px mobile emulation; confirmation dialog remains contained. Evidence: [PR175 visual report](https://github.com/Starck43/social-media-ai/pull/175#issuecomment-6152533703). Next: identify the overflowing base layout element independently of the Chromium emulation discrepancy. Accept when affected pages fit a verified 375px viewport without unintended horizontal scrolling and confirmation focus/keyboard behavior is retained. |
| PRD-02 digest send permission parity | Remote Notion agent; `fix/digest-send-permission-parity`, [Draft PR #176](https://github.com/Starck43/social-media-ai/pull/176) | BLOCKED — test-runner environment/fixtures; DB membership acceptance incomplete | Owner reports completed sequential verification window with passing bounded checks and unchanged observed shared data. Remaining: web/registry collection setup, permissions suite and DB-backed membership matrix. Next: review bootstrap-free runner setup or obtain narrow approval for conftest side effects; Kilo/pare-test only. Do not repeat accepted outcome checks or infer full PR168 acceptance. |
| PRD-04 loopback-only database binding | Owner, local only; no remote branch or PR | DEFERRED — pre-production | Resume before the production release: restrict the checked-in PostgreSQL host publish to `127.0.0.1:5432:5432`, preserve container `db:5432` and local-owner tools, and prove network isolation with local/staging evidence. `docker/docker-compose.yml` on `dev` still publishes `5432:5432`. Do not start/restart Compose: API startup runs Alembic. |
| PRD-06 data-retention rollout | Owner; no branch or PR (code merged in `dev` via PR #139) | DEFERRED — deployment not yet approved | Owner decision (2026-10-12): do not deploy yet. Before deploying, back up `ai_analytics` if history matters and confirm `DATA_RETENTION_MONTHS` (default 1): the first daily prune after deploy irreversibly deletes analyses whose last write is older than the cutoff. Remove this row after the deployment is verified. |
| Atomic LLM receipt and budget reservation | Remote Notion agent; `feat/llm-budget-reservation`; [Draft PR #180](https://github.com/Starck43/social-media-ai/pull/180) | BLOCKED — UNWIRED; scoped style complete, separately approved PG evidence pending | Original owner-local no-DB evidence at `ff29f82615413533121c5265eec0477e02643f9a`: 31 passed / 0 failed / 0 skipped; 10 ASTs OK. Owner reports Black/isort and four-file AST equivalence PASS at style commit `9dd748cef465e09df74cc98c07ecc65748b41a8c` (parent `244fc29`); remote confirms exact four-path formatting diff and whitespace PASS, not an independent AST execution. Inherited root import style remains untouched. No pytest rerun or relabeling of tested SHA. Bootstrap-free PG runner and offline security review prepared in `docs/validation/llm-budget-pg-runner.md` (PG NOT RUN; initial owner-local offline FAIL at `ffc860f8f8556d80c0df4190564f7f29d83b007c` retained; formatter-only successor `efe8553fbadf1233e92f0ab7cdb140e491926e60` accepted from supplied one-file patch and owner-local Black/isort/AST-equivalence PASS; remote exact-byte/scope/whitespace checks only): 14 scenario IDs, 30 dispatch IDs, 18 fixture keys; proposed retained delta +11 receipts/+10 reservations/+18 days. Actual fixture tenant IDs/target/roles/window remain UNKNOWN; Gate A owner report has zero connections/tests/changes. Empirical report-write isolation remains unverified; review-worktree `.venv` confirmed absent (fail-closed guard retained). Next: separately agreed environment/report-isolation plan and owner-provided existing target/roles/window/actual fixture IDs, then distinct Gate B read-only and Gate C operation/fixture approval; no checks rerun or PG assignment yet. Historical runner/report-location and PR137 PG caveats retained in PR; latest dev docs and PR176 lane preserved. No settlement, HTTP/client wiring, schema application, baseline provisioning or live activation. |

For a new implementation lane, replace or add one row with its exact bounded
acceptance condition, owner, branch/PR and state. Remove the row after verified
integration; do not copy the PR narrative here.

## Selection rules

- Verify the gap in current code before treating a readiness statement as a bug.
- Read only the selected subsystem document and relevant call sites.
- Preserve delivered behavior listed in the implementation index.
- Record detailed design decisions and test evidence in the PR.
- Update this board only when priority, ownership or integration state changes.
