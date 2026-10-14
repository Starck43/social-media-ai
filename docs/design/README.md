# Development priorities and ownership

This is the only prioritized open-work queue. Product phases live in
[PRODUCT_PLAN](../PRODUCT_PLAN.md), stable acceptance gates in
[BUSINESS_PRODUCTION_READINESS](../BUSINESS_PRODUCTION_READINESS.md), and delivered
foundations in [IMPLEMENTATION_STATUS](../IMPLEMENTATION_STATUS.md).

## Current checkpoint

Planning baseline verified on `origin/dev`: `aac8bfabe66010b38858102c0746776315ec367f`.
Bounded home cleanup was integrated via [PR #185](https://github.com/Starck43/social-media-ai/pull/185),
merge `628b021`, by explicit owner approval with real-app ASGI/DB validation deferred.
The owner-selected next UI package is PRD-08.2 below; this checkpoint does not
claim that the shell or attention feed is implemented.
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

1. **Needs attention:** move this outcome into the shared notification area in
   PRD-08.2, not a separate permanent home card. The current template has only a
   non-rendered extension comment, not an implemented attention block. Reuse the
   same authorized projection for connection states and task failures; deduplicate,
   redact and hide when empty. No raw exception tooltip, inferred all-healthy
   state or live recovery action. PRD-08.2 owns this implementation; do not build
   a second attention feed under this task.
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

## PRD-08.2 — Assistant shell and unified notification area

Owner-selected UI task under priority 8 / PRD-08, linked to PRD-08.1.
Status: **PLANNED — design and implementation sequence recorded; no UI code changed**.
This is not a new readiness gate. Preserve the current FastAPI/Jinja/Alpine/HTMX
stack, managers and shared authorization predicates; no SPA/framework replacement,
new navigation service, LLM call or schema migration for the first shell slice.

### Verified starting point

Inspected dev `aac8bfabe66010b38858102c0746776315ec367f`:
`app/web/templates/web/base.html`, `dashboard.html`, `app/web/nav.py`, `deps.py`,
`middleware.py`, `perms.py`, `settings.py`, `notifications.py`,
`app/models/notifications/notification.py`, its manager, and `app/static/js/theme.js`.
The base currently duplicates workspace/account/theme controls around the header
and sidebar. The notification API lists unread workspace rows; `is_read` is one
workspace-shared flag, with no recipient User field. Connection alerts are personal
by User. Settings hides agent/channels tabs from non-managers but still eagerly
loads their data and other tabs' data: tab hiding is not sufficient read isolation.
Existing invite issuance is a guarded POST followed by a one-time code/link/QR
shown after redirect to the team tab. Preserve that security and revoke capability
when moving the UI. Do not infer access from role labels or the older prose in
TENANCY; current `WebPerms.can()` / `can_manage_workspace(target)` are authoritative.

Reference, not a visual clone: [Notion sidebar guidance](https://www.notion.com/help/navigate-with-the-sidebar)
illustrates a stable navigation surface and grouped workspace/account utilities.
The floating bell and page-specific header below are this owner's product choices,
not a claim that other services use exactly this layout.

### Agreed target shell

1. **Left navigation is stable across sections.** Desktop has an expanded
   sidebar (~240px) and a compact icon rail (~64px), controlled by an explicit
   collapse button. Persist the local UI preference, not rights, with safe storage
   fallback and no content flash. On narrow screens use a left navigation drawer
   opened from the page navbar; do not spend mobile reading width on a permanent
   desktop rail. Replace the limited bottom navigation only after the drawer
   exposes every permitted daily section and the account menu.
2. **Account at the top, no AI Monitor brand row.** Show the signed-in person's
   avatar (existing safe image if available, otherwise initials) and display name.
   The menu may open on desktop hover, but click/tap and keyboard are equally
   supported; keep it open while pointer/focus moves into it, close on Escape or
   outside click, return focus. In compact mode the avatar remains its trigger.
   Group current workspace and effective membership role, workspace selection /
   creation / join-by-code, Invite member, Settings, operator-only Admin, and
   POST+CSRF Logout. Role is explanatory, not a second permission source. Show the
   active workspace in the navbar subtitle too, so context is not hidden in a menu.
3. **One page-specific navbar contract in base.** Use small Jinja blocks such
   as page_heading, page_context, page_actions and an optional right_panel; do not
   duplicate the whole shell per page. Render exactly one semantic h1 in the navbar,
   remove its content duplicate, keep browser title and breadcrumbs where relevant.
   Home: heading Overview, workspace context and an icon chat link with accessible
   name / hover-focus tooltip (same can_open_chat and destination). Move existing
   section actions here without changing their guards or submit semantics. Filters
   and large form controls stay in a content toolbar; do not cram them all upstairs.
   Keep the operator's data-workspace filter distinct from active membership context.
4. **Theme icon at the right of every page navbar.** Open an anchored popover
   with three labelled icon choices: System / Light / Dark and a selected marker.
   Replace the web shell's native theme listboxes; reuse AppTheme and the existing
   data-theme-choice handler/storage key, pre-paint resolution, OS changes and
   cross-tab sync. Do not silently change the admin theme contract. Support keyboard,
   Escape/outside close, focus return, reduced motion and small-screen containment.
5. **Floating notifications, not a header badge or home attention card.** Bottom
   right; visible only when the authorized feed contains unread messages or active
   actionable signals. Counter and list use the identical scope/filter/dedup rules.
   Keep clear of mobile safe-area, drawers, chat composer and content/actions; an
   open modal owns focus and background scroll. Reuse the existing notification
   dialog first; do not add a second right sidebar for it. If the last item is read
   while open, keep an empty state and a valid close/focus target until dismissal.
   Fetch failure is unknown, never evidence of zero items / everything healthy;
   do not discard previously known items or silently accept failed read POSTs.
6. **Invitation entry belongs in the account menu.** It opens an on-demand shared
   dialog/partial using existing role/duration choices, single-use issuance,
   one-time code/link/QR output and revoke checks. Do not fetch invite data for
   unauthorized users or preload all settings into every shell request. Settings
   Team retains the active membership list and permitted role/member actions, not
   a duplicate issue form. Keep outstanding-invite status/revocation reachable in
   the invite dialog; preserve expiry, seats, last-owner rules, CSRF and scoping.
   Do not send email/messages or issue an invite merely by opening the menu.
7. **Optional right panel slot, closed by default.** Its toggle lives in the
   owning page navbar and renders only when that page actually supplies authorized
   panel content. No empty rail, Soon card or phantom toggle. Desktop can use a
   collapsible context column; mobile uses a dismissible drawer with focus/scroll
   handling. Initially no page is forced to move content into it; notifications
   remain independent. Local panel state is page-specific, not authorization.
8. **Icons are navigation data, not label matching.** Add an icon key to NavItem
   with a safe circle fallback and reuse the existing icon macro (extend narrowly).
   Suggested map: overview/home, sources/database, scenarios/layers, tasks/calendar,
   analytics/chart, digests/document, operator queue/list, chat/message. Keep
   readiness and permission filtering intact. Move Settings/Admin utilities to
   account menu, not the daily rail. Every compact link keeps its accessible name,
   focus/hover tooltip and active marker; no invented unavailable sections.

### Implementation sequence and bounded handoff

- **A — Shell, next implementation package.** Inventory all base-extending pages,
  their headings/actions, auth/onboarding/403 states, icon macro and current tests.
  Implement account menu, desktop collapse/mobile drawer, per-page navbar/title and
  home chat icon, theme popover, content-gated right-panel slot. Move the existing
  bell to floating placement and hide it for a genuinely empty existing authorized
  notification list; include minimal count/list consistency needed for that move,
  but do not claim the combined attention feed yet. Extract invitation UI into a
  permission-checked lazy partial/dialog and preserve its POST/result cycle. Verify
  changed flows before integrating. Do not redesign the remaining settings forms
  or change shared read-state semantics in A. Prefer small reusable partials and
  scoped CSS; rework the home-only header selectors without regressing PR #185.
- **B — Unified attention, after A.** Use a bounded service/read-time projection
  over existing notifications, the caller's connection statuses and authorized
  task failures. Typed items carry stable source-qualified key, kind, safe title,
  redacted summary, scope, destination, state/time and supported action. Deduplicate
  persisted events and derived signals by workspace/event/entity/state; do not
  write Notification rows on GET/poll or add a second table just to render a feed.
  Put actionable items first and bound/page results without an unbounded shell
  query. Never expose forbidden entity names, counts or links; recheck rights on
  list, count, read action and navigation, including revocation/workspace switch.
  Clear old feed state on workspace changes; personal signals remain caller-only.
  Reading an event is NOT resolving a connection/task problem: derived signals
  disappear only when their verified condition clears, not via mark-all-read.
  Current persisted event reads stay explicitly workspace-shared; never store a
  person's acknowledgement/preference in this global is_read flag. If per-person
  durable read/dismiss is required, stop for a separate storage/ACL decision rather
  than pretending it exists. Maintain CSRF and truthful failed-request handling.
- **C — Future notification preferences, separately bounded.** Personal settings
  for which supported kinds appear in the bell, reading/acknowledgement behaviour,
  optional popup policy and quiet hours. Distinguish event read, issue resolution
  and suppression. A display preference never grants permissions, removes audit
  evidence or mutes another member; define mandatory security/approval notices
  before allowing suppression. Use existing user preference storage only after
  verifying it; otherwise propose a minimal reviewed schema change separately.
- **D — Future settings scopes, separately bounded.** Produce and implement a
  field/action/query matrix, not an enum-name-only Owner/Member split:

  | Scope | Proposed availability and boundary |
  | --- | --- |
  | My account | Each authenticated user: their own implemented profile/security/preferences; no fake future forms. Theme stays local initially; notification preferences arrive in C. |
  | My connections | Each user manages only their own credentials/OAuth/session rows. Workspace role never turns another person's secret into theirs or auto-substitutes the owner's token. Using a connection for shared sources still requires the existing source/owner rights. |
  | Workspace | Name/timezone/budget and workspace-agent/delivery configuration: current can_manage_workspace(concrete target), owner or an actually granted manager capability. Other members get only explicitly authorized read-only fields, not hidden editable forms/data. |
  | Team | Invite/role/revoke/member actions follow the same server capability, seat and last-owner constraints. Define membership-list visibility explicitly; no blanket role-name inference. Invite issuance lives in the account menu; member administration remains in Settings. |
  | Platform | Admin, global providers/models, billing tier and infrastructure: existing operator/platform checks only. Workspace ownership does not confer these rights. |

  Shared workspace settings are not replaced by each member's personal settings.
  Present separate My account / Workspace groups and explicit scope/help. Reuse
  the shared permission predicate; no DENY enum or parallel permission table.
  Lazy-load only permitted selected-tab data: current hidden-tab eager queries
  are a concrete follow-up to resolve, not a claim of safe read gating already.
  Preserve direct URL compatibility and server-side mutation checks.

### Acceptance and release boundary

A is accepted only with focused evidence for all migrated page headings/actions,
expanded/compact rail, mobile drawer, account/invite menus, theme and floating
notification dialog. Cover populated/empty/denied/loading/failure/long-label states,
Viewer/Manager/owner/operator and no-membership/auth/403 pages; unauthorized links
and lazy queries absent, direct POSTs still refused, workspace switch cannot widen
scope. Verify hover, tap, keyboard, Escape/outside dismissal, focus return/trap,
44px targets, normal text contrast >=4.5:1, meaningful UI >=3:1 and reduced motion.
Render and individually inspect 375/390/768/1440px light/dark/system layouts: no
horizontal page overflow or overlap with floating controls, modal or chat composer.
Test the right-panel contract with authorized fixture content; production gets no
empty toggle. Preserve confirmation dialogs, form errors and autoescaping.

B additionally needs shared count/list fixtures, personal-vs-workspace separation,
forbidden/revoked entities, dedup, read failure, unresolved/cleared attention and
cross-workspace read/update regressions. Do not mark B/C/D complete from A.
Reuse relevant tests/unit_web and tests/web_home_layout.cjs fixture patterns, then
real ASGI/DB integration via Kilo R8/R10 on a safe existing test target. No automatic
schema bootstrap/reset, real provider call, invite delivery, send, deploy or new
dependency. Historical PR #185 sandbox permission was session-specific, not a new
pytest policy. First package has no migration; any later per-person state/schema
requires a separately reviewed decision. Also retain PRD-08.1's deferred real-app
checks; planning this task does not satisfy them or the PRD-08 gate.

## Active ownership

| Work | Owner / branch / PR | State | Next action |
| --- | --- | --- | --- |
| PRD-08.1 outcome-focused home overview | Unassigned for mixed results/upcoming schedules; cleanup integrated via [PR #185](https://github.com/Starck43/social-media-ai/pull/185), merge `628b021` | OPEN — cleanup MERGED; full layer and deferred real-app checks remain open | Preserve cleanup and run deferred ASGI/DB role/workspace checks on a safe existing target. Attention implementation moved to PRD-08.2; mixed results and actual upcoming schedules remain here. No migration or deployment accepted. |
| PRD-08.2 assistant shell / notification area | Remote Notion agent; `feat/assistant-shell`, [Draft PR #188](https://github.com/Starck43/social-media-ai/pull/188) (A only) | IN_PROGRESS — owner-requested shell polish | Move sidebar collapse/theme controls, group workspace switching, move join-by-code to Team, widen responsive home and remove navbar fill; finish with isolated no-DB regressions. Real acceptance and B/C/D remain pending. No merge, schema or live actions. |
| Task-add source selection initialization | Unassigned; no implementation branch or PR | OPEN — inherited Alpine runtime follow-up | Owner reproduced addTaskForm().task.source_ids missing while :checked calls includes; pre-fix control has the same error. Evidence: [PR178 runtime report](https://github.com/Starck43/social-media-ai/pull/178#issuecomment-6154504317). Next: initialize source_ids in the actual add-task model and verify selection/reset with real Alpine. Accept when opening the form has no includes error and selected IDs round-trip without changing submit semantics; no server/DB/live probes required. |
| Mobile web page-width overflow | Unassigned; no implementation branch or PR | OPEN — inherited layout follow-up | Owner observed app document width 432px under 375px mobile emulation; confirmation dialog remains contained. Evidence: [PR175 visual report](https://github.com/Starck43/social-media-ai/pull/175#issuecomment-6152533703). Next: PRD-08.2 A must cover the shared shell overflow while preserving this acceptance boundary; identify the overflowing base layout element independently of the Chromium emulation discrepancy. Accept when affected pages fit a verified 375px viewport without unintended horizontal scrolling and confirmation focus/keyboard behavior is retained. |
| PRD-02 digest send permission parity | Remote Notion agent; `fix/digest-send-permission-parity`, [Draft PR #176](https://github.com/Starck43/social-media-ai/pull/176); handoff requested via PR180 | DEFERRED — owner stopped coordinator infrastructure work; recipient acknowledgement pending | Delegation request: PR180 comment6166461970. Reuse existing collection exception; resolve reusable orchestration/fallback contract once, then delegate remaining PR176 acceptance at pinned verification HEAD0c18bab. Preserve all work/evidence; no duplicate runs, parallel shared-schema writers, repair or merge. Not DONE; no claim that recipient accepted ownership. |
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
