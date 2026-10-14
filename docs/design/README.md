# Development priorities and ownership

This is the only prioritized open-work queue. Product phases live in
[PRODUCT_PLAN](../PRODUCT_PLAN.md), stable acceptance gates in
[BUSINESS_PRODUCTION_READINESS](../BUSINESS_PRODUCTION_READINESS.md), and delivered
foundations in [IMPLEMENTATION_STATUS](../IMPLEMENTATION_STATUS.md).

## Current checkpoint

Verified `origin/dev`: `628b021` (merge of [PR #185](https://github.com/Starck43/social-media-ai/pull/185)); bounded home cleanup integrated by explicit owner approval with real-app ASGI/DB validation deferred.
Test-bootstrap evidence protection and stored-text escaping are integrated;
prior delivered foundations remain in the implementation index. Active lanes
are listed under Active ownership. Refresh `dev`, open PRs and this row before selecting work.

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

## PRD-08.1 — Outcome-focused home overview

Owner-approved product task under priority 8 / PRD-08; not a new readiness gate.
The home page answers: what needs attention, what was produced, what happens next.
Target: one wide column on desktop and mobile, with no permanent quick-command
panel, KPI grid or duplicated analytical sidebar. Reuse current models/services;
no new workflow/result table, provider call or schema migration for this layer.

### Current bounded cleanup — MERGED; real-app validation deferred by owner

- Remove the six KPI cards, quick actions, analytical sidebar, detailed task
  table, inline run-now buttons, header slug/tariff/role and multi-step welcome.
- Keep one chat entry, up to five permitted analysis-result cards, up to three
  compact recent task summaries, section links and one conditional setup step.
- Gate data loading as well as rendering with the effective VIEW rights. Do not
  fetch analysis results without aianalytics.view; do not fetch source metadata or show separate source labels
  without source.view. Keep operator workspace selection and tenant isolation.
- Do not calculate the removed KPIs/aggregates on the home request. No new LLM
  summary and no health/spend claims inferred from missing data.

Sandbox follow-up for [merged PR #185](https://github.com/Starck43/social-media-ai/pull/185):
Merge `628b021` is verified in dev. The owner explicitly approved integration
with real-app checks pending. Explicit owner authorization permitted direct isolated checks in this session,
not a permanent R10 policy change. Eleven no-app/no-DB tests passed. Offline
Chromium fixtures passed 32 viewport/theme cases (375/390/768/1440px; four
states; light/dark) and a mocked interaction scenario. Six screenshots were
visually inspected. Fixed long-label horizontal overflow, mobile task wrapping,
header control wrapping, preview density and text/target sizes; CSS is home-only.
Real ASGI/DB permission and workspace acceptance remains pending; no deployment
or migration. Exact code revision and reproducible commands are in the PR.

### Deferred implementation placeholders / acceptance

1. **Needs attention:** at most three actionable, deduplicated, redacted items
   from existing connection states and authorized task failures; hide when empty.
   No raw exception tooltip, inferred all-healthy state or live recovery action.
2. **Latest results:** merge permitted analyses and digest runs into at most five
   cards using a bounded read-time projection. Label analysis creation time,
   digest period and actual delivery state separately. No fabricated success,
   duplicated delivery or additional LLM call. Digests remain a section link
   during the cleanup; they are not yet cards in this mixed feed.
3. **Scheduled next:** replace the recent-task preview with at most three actual
   upcoming, effectively active schedules, ordered by next_run_at. Handle missing
   next time, paused and completed one-shot tasks honestly. Do not call the current
   recent-by-creation list the nearest schedules or multi-step workflows.
4. **Empty/access states:** owner gets one permitted next setup step; invitees
   without creation rights get an explanation, not prohibited create links.
   Missing VIEW hides both section content and its query; future member-level
   allowlists must feed the existing shared permission predicate.
5. **Future extension points:** approvals, reminders and business outputs may
   enter attention/results only when their contracts exist. Template comments
   mark extension points; do not show fake rows, disabled commands or Soon cards.

Acceptance: role matrix for Viewer/Manager/owner/operator, cross-workspace and
no-rights cases, populated/empty data, stored-text escaping, desktop and verified
375px mobile layout. All pytest follows R8/R10 via Kilo; no schema bootstrap/reset,
provider calls, publication or deployment. Cleanup is not completion of this task
or acceptance of the whole PRD-08 gate.

## Active ownership

| Work | Owner / branch / PR | State | Next action |
| --- | --- | --- | --- |
| PRD-08.1 outcome-focused home overview | Unassigned for the future layer; cleanup integrated via [PR #185](https://github.com/Starck43/social-media-ai/pull/185), merge `628b021` | OPEN — cleanup MERGED; full layer and deferred real-app checks remain open | Run the deferred ASGI/DB role and workspace checks on a safe existing test target; then implement attention, mixed results and actual upcoming schedules. Cleanup reservation released after verified merge; no migration or deployment accepted. |
| Task-add source selection initialization | Unassigned; no implementation branch or PR | OPEN — inherited Alpine runtime follow-up | Owner reproduced addTaskForm().task.source_ids missing while :checked calls includes; pre-fix control has the same error. Evidence: [PR178 runtime report](https://github.com/Starck43/social-media-ai/pull/178#issuecomment-6154504317). Next: initialize source_ids in the actual add-task model and verify selection/reset with real Alpine. Accept when opening the form has no includes error and selected IDs round-trip without changing submit semantics; no server/DB/live probes required. |
| Mobile web page-width overflow | Unassigned; no implementation branch or PR | OPEN — inherited layout follow-up | Owner observed app document width 432px under 375px mobile emulation; confirmation dialog remains contained. Evidence: [PR175 visual report](https://github.com/Starck43/social-media-ai/pull/175#issuecomment-6152533703). Next: identify the overflowing base layout element independently of the Chromium emulation discrepancy. Accept when affected pages fit a verified 375px viewport without unintended horizontal scrolling and confirmation focus/keyboard behavior is retained. |
| PRD-02 digest send permission parity | Remote Notion agent; `fix/digest-send-permission-parity`, [Draft PR #176](https://github.com/Starck43/social-media-ai/pull/176) | BLOCKED — final DB acceptance evidence pending; session closed | Owner reports isolated smoke PASS through full Kilo/test-runner/pare-test chain; runner routing is demonstrated, not full DB acceptance. Prior direct-MCP suite evidence remains explicitly non-R10. Resume with verified local tree/plugin/config, outstanding DB suites and actor/membership confirmation matrix, then final evidence-preservation comparison. No automatic merge or repeated outcome suite. |
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
