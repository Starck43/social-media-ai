# Development priorities and ownership

This is the only prioritized open-work queue. Product phases live in
[PRODUCT_PLAN](../PRODUCT_PLAN.md), stable acceptance gates in
[BUSINESS_PRODUCTION_READINESS](../BUSINESS_PRODUCTION_READINESS.md), and delivered
foundations in [IMPLEMENTATION_STATUS](../IMPLEMENTATION_STATUS.md).

## Current checkpoint

Planning baseline verified on `origin/dev`: `aac8bfabe66010b38858102c0746776315ec367f`.
Bounded home cleanup was integrated via [PR #185](https://github.com/Starck43/social-media-ai/pull/185),
merge `628b021`, by explicit owner approval with real-app ASGI/DB validation deferred.
Historical planning baseline only. At audited dev `c62f948`, PRD-08.2 A/B and
the bounded C/D slices are merged (#188–191). Source-aware admission (#192/#193)
and budget reservation closeout (#180/#194) are also integrated. See the
[delivered index](../IMPLEMENTATION_STATUS.md) for separately attributed owner-local
home and A/B acceptance; merged does not mean whole-gate acceptance.
Test-bootstrap evidence protection and stored-text escaping are integrated;
prior delivered foundations remain in the implementation index. Active lanes
are listed under Active ownership. Refresh `dev`, open PRs and this row before selecting work.

Source reconciliation on 2026-10-15 uses `dev` `7ba5fe4ce0879a38d96ecb92770dad055c014624`.
Direct dev changes after the historical checkpoint are attributed in the
implementation index; new audit tasks are allocated separately, not implemented
or release-accepted by documentation synchronization.

Nine readiness areas remain open. They are gates, not nine commits or an effort
estimate. Select one bounded change with an observable acceptance condition.

## Prioritized open work

| Priority | Gate | Next bounded outcome | Acceptance boundary |
| --- | --- | --- | --- |
| 1 | PRD-05 — truthful queue execution | Choose the next recovery or admission gap after the delivered claim renewal, preflight, heartbeat and atomic-trigger slices. | No handler starts after claim loss; recovery cannot duplicate an unresolved external effect; execution outcome remains truthful. |
| 2 | PRD-03 — complete usage and spend | Paid-attempt accounting separate from daily analytics — **DONE** ([PR #163](https://github.com/Starck43/social-media-ai/pull/163)): analyzer partial-collision, relevance-filtered and no-result runs keep journal evidence (PR #114), and every non-analyzer paid attempt is now journaled with identity, usage, tariff snapshot, cost state and outcome — each `chat_with_fallback` try (agent chat, learning, reflection, prompt suggestion), the model-test tool and the digest summary. Atomic receipt/reservation storage and the owned-transaction admission API are **MERGED, UNWIRED** ([PR #180](https://github.com/Starck43/social-media-ai/pull/180), merge `df06b1e17d5d13d7f3e7171dff6d7a317c87cca2`); isolated PG acceptance and owner-local normal0094→0095 rehearsal on an approved legacy clone are recorded in the implementation index. Next: select/review one bounded activation prerequisite: durable UTC-day baseline provisioning, reviewed bound production, receipt/outcome-to-settlement contract or all-client/all-caller admission wiring. These remain OPEN, not assigned or implemented here; admin connection tests still need an operator-payer decision first. Pricing truth is owner-decided (2026-10-12): FREE only on an explicit zero; structured provider USD price, never generated text, arbitrates cost; no resolvable price is UNDEFINED — never free, never zero, always tracked. | Admission: no request starts without committed admission under the current cap; parallel attempts cannot silently bypass it; undefined cost is never treated as free or zero; `ai_analytics`, `jobs.llm_cost` and `digest_runs.llm_cost` stay projections; no double counting. |
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

### Bounded cleanup — MERGED; original and later acceptance

- Remove the six KPI cards, quick actions, analytical sidebar, detailed task
  table, inline run-now buttons, header slug/tariff/role and multi-step welcome.
- Keep one chat entry, up to five permitted analysis-result cards, up to three
  compact recent task summaries, section links and one conditional setup step.
- Gate data loading as well as rendering with the effective VIEW rights. Do not
  fetch analysis results without aianalytics.view; do not fetch source metadata or show separate source labels
  without source.view. Keep operator workspace selection and tenant isolation.
- Do not calculate the removed KPIs/aggregates on the home request. No new LLM
  summary and no health/spend claims inferred from missing data.

Historical sandbox follow-up for [merged PR #185](https://github.com/Starck43/social-media-ai/pull/185):
Merge `628b021` is verified in dev. The owner explicitly approved integration
with real-app checks pending. Explicit owner authorization permitted direct isolated checks in this session,
not a permanent R10 policy change. Eleven no-app/no-DB tests passed. Offline
Chromium fixtures passed 32 viewport/theme cases (375/390/768/1440px; four
states; light/dark) and a mocked interaction scenario. Six screenshots were
visually inspected. Fixed long-label horizontal overflow, mobile task wrapping,
header control wrapping, preview density and text/target sizes; CSS is home-only.
At that original revision real ASGI/DB acceptance was pending. Subsequent
owner-local home/identity acceptance (69 checks at `01f719d` plus reviewed working
diff) is recorded in IMPLEMENTATION_STATUS; it does not implement mixed results
or upcoming schedules. No deployment or migration. Original commands stay in the PR.

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
Status: **A/B MERGED; C BROWSER-LOCAL SLICE MERGED; D BOUNDED SLICE MERGED**.
Full PRD-08 acceptance and durable preference storage remain open. Current
contracts: [shell](assistant-shell.md), [notifications](../NOTIFICATIONS.md),
[settings/access](../TENANCY.md#web-settings-and-ownedgranted-workspace-context).
This is not a new readiness gate. Preserve the current FastAPI/Jinja/Alpine/HTMX
stack, managers and shared authorization predicates; no SPA/framework replacement,
new navigation service, LLM call or schema migration for the first shell slice.

### Historical verified starting point (before A–D)

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

### Owner-requested shell refinements

These owner choices supersede the original navbar theme and single-column home
placement above for package A; they do not implement B/C/D or the remaining home
feed/schedule functionality:
- Put a reversible double-chevron collapse control at the right of the desktop
  account/navigation block. Keep local collapse state and accessible control labels.
  Put AppTheme choices at the bottom left of the rail; on mobile use the navigation
  drawer footer. Remove the web navbar theme control, not the mobile drawer opener.
- Group workspace selection/creation separately from current-workspace tools and
  account/platform utilities. Show one clearly marked active workspace and explicit
  switch targets; use canonical workspace role captions only for explanation.
  Keep every mutation POST+CSRF and every server permission check unchanged.
- Move join-by-code to Settings Team for users with an active workspace, available
  independently of invitation-management rights. Preserve the no-workspace join
  entry so first-time users are not stranded; do not introduce a daily duplicate.
- Let home use the available content width. At 1200px viewport and wider, permitted
  latest-results/tasks cards share a row; below that breakpoint they stack. If tasks
  are forbidden, results retain the full width. Do not change their data/VIEW gates.
- Give every web navbar a transparent background and retain its separator; other
  page-specific form width limits and the admin shell remain unchanged.

### Approved implementation sequence (historical plan; merged boundaries above)

- **A — Shell, merged via #188.** Inventory all base-extending pages,
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
- **B — Unified attention, merged via #189.** Use a bounded service/read-time projection
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
- **C — Notification preferences, browser-local slice merged via #190.** Personal settings
  for which supported kinds appear in the bell, reading/acknowledgement behaviour,
  optional popup policy and quiet hours. Distinguish event read, issue resolution
  and suppression. A display preference never grants permissions, removes audit
  evidence or mutes another member; define mandatory security/approval notices
  before allowing suppression. Use existing user preference storage only after
  verifying it; otherwise propose a minimal reviewed schema change separately.
- **D — Settings scopes, bounded slice merged via #191.** Produce and implement a
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
| Models/admin source documentation reconciliation | Remote Notion agent; `docs/models-admin-source-sync`, [PR #199](https://github.com/Starck43/social-media-ai/pull/199) | READY — docs-only validation complete | Implementation `ab0d5a9`; 31 mapped + 3 association tables, 320 field-name checks, source head 0096, 16 admin views/10 actions/7 routes, 81 link targets and diff whitespace verified; both published docs byte-match. GitGuardian success; no commit-status contexts reported (empty pending is not a test). Merge through PR after fresh heads; runtime/view bugs and DB/provider acceptance not included. |
| Platform/workspace authorization and personal AI memory | Codex, local `dev`; accepted owner plan | DONE — bounded local implementation accepted | Separate role catalogs, domain permission keys, operator-only admin, membership-only web/API, transactional team controls and private AI memory. OpenCode isolated scope: 327/327; focused policy rerun: 62/62 (separate overlapping rerun). Real PostgreSQL/ASGI: 29 distinct cases in sequential batches (21 + 4 + 4), including concurrent last-owner protection, fresh AI/membership revocation, personal facts/transcripts/cursors and explicit operator/user entry gates. Migration 0096 applied locally on `social_manager/public`; authorized unchanged-0096 operations updated the unversioned test schema without stamping or replaying earlier revisions; checked counts and LLM guards preserved. Pytest startup validates the target read-only. Existing `alembic check` drift, full-suite/browser/production acceptance and a separate real-DB pending-confirmation end-to-end run remain outside this acceptance. Evidence: ROADMAP.md, `.agent/test-report.md`, `docs/ACCESS_MIGRATION.md`. |
| Source-aware explicit-run admission / incomplete-analysis retry | Remote Notion agent; `fix/source-analysis-admission`, [PR #193](https://github.com/Starck43/social-media-ai/pull/193); initial retry integrated via [PR #192](https://github.com/Starck43/social-media-ai/pull/192), merge `bed48ce` | MERGED — bounded slice; full gate open | Owner: “закрывай, все хорошо”. Implementation `b032b4739302d5ad536a491354bd9bdfcc2c40c6`, verified merge `c0c89a9`; compatibility with dev `f2e6bbd` checked without conflicts and isolated checks repeated. Real PostgreSQL race/rollback and ASGI workspace/permission acceptance remain unperformed, not PASS. Explicit manual/task/cron admission only; raw new-input wakeups retained. Preserve PR evidence; no schema/deploy or whole-gate acceptance. |
| PRD-08.1 outcome-focused home overview | Unassigned for mixed results/upcoming schedules; cleanup integrated via [PR #185](https://github.com/Starck43/social-media-ai/pull/185), merge `628b021` | OPEN — cleanup MERGED; owner-local bounded home acceptance recorded; full layer remains open | Preserve cleanup and owner-local 69-pass home/identity acceptance at `01f719d` plus reviewed working diff, recorded in IMPLEMENTATION_STATUS. This is not full home-layer or C/D acceptance. Attention implementation moved to PRD-08.2; mixed results and actual upcoming schedules remain here. No migration or deployment accepted. |
| PRD-08.2 assistant shell / notification area | Remote Notion agent; C `feat/notification-preferences`, [PR #190](https://github.com/Starck43/social-media-ai/pull/190); D `feat/settings-scopes`, [PR #191](https://github.com/Starck43/social-media-ai/pull/191) | A/B MERGED; C BROWSER-LOCAL SLICE MERGED (durable state decision open); D BOUNDED SLICE MERGED — real acceptance deferred by owner | C `8186cac`: user-keyed browser-local display/popup/quiet hours, explicit shared reads unchanged, empty-bell restoration reachable. Prior D `264bf2e`: scoped selected-tab queries; concrete capability gates shared config/team. Final sandbox: 166 no-DB pytest, 35 JS checks, Settings 80+8 cases, shell 336+38, home 72+2; fixtures are not real acceptance. Nine C paths / twelve D-only deltas byte-verified. Owner accepted C visually. D follow-up `43608e6` (code `4a7cfe7`): 226 no-DB pytest, 14 preference JS checks, final owner/guest 40+8 and Settings 80 cases PASS; shared shell 336+38 and home 72+2 also checked, with team CSS specifically rerun. 26 paths byte-verified; GitGuardian success. Active authenticated admin entry retains model/action guards; token-bound grants, label owned/shared workspaces, expose guarded membership disable, deny guest-workspace chat at every web endpoint, verify tenant-scoped reads; no individual object ACL or schema changes. Owners protected from disable; membership rows retained. Admin remains global/platform, not dashboard tenant scope. Owner authorized merge: “сливай в dev. буду смотреть”. C integrated first at `734e479ad29022c6597899029407135cc8ce6ffd`; D at `ae77f558fb4b518653c840e8348f8b8088db088e`. Fresh heads/checks and clean merge composition verified; both source heads are ancestors of dev, all 26 D follow-up paths preserved byte-for-byte. Implementation lane released; await owner feedback. Real SQLAdmin/ASGI/PG role, revocation, disable race/rollback acceptance and adjusted application-importing tests are still unexecuted; durable preferences need separate storage approval. Later owner-local home (69) and A/B (77) checks are recorded in IMPLEMENTATION_STATUS at their original revisions; they do not close the remaining C/D/legacy acceptance gaps. No schema/live changes. |
| Task-add source selection initialization | Unassigned; no implementation branch or PR | OPEN — inherited Alpine runtime follow-up | Owner reproduced addTaskForm().task.source_ids missing while :checked calls includes; pre-fix control has the same error. Evidence: [PR178 runtime report](https://github.com/Starck43/social-media-ai/pull/178#issuecomment-6154504317). Next: initialize source_ids in the actual add-task model and verify selection/reset with real Alpine. Accept when opening the form has no includes error and selected IDs round-trip without changing submit semantics; no server/DB/live probes required. |
| Merged web documentation reconciliation | Remote Notion agent; `docs/merged-web-contracts`, [PR #195](https://github.com/Starck43/social-media-ai/pull/195) | CLOSED — docs audit complete; integration tracked in PR | Audit baseline `c62f948`; reconcile ledger, A–D plan and current notification/settings/admin contracts. Review recent merged #180/#185–194, retaining historical evidence and original test attribution. PR bodies compressed separately; no runtime/schema/provider action. |
| Unresolved membership display label | Direct dev follow-up `2dd3161`; earlier finding verified at `c62f948` | DELIVERED — current source reconciled; this audit runs no acceptance tests | Earlier owner-label inference is removed: `member_roles.key_of()` returns None for NULL role. Preserve fail-closed grants, stored rows and original finding history; see TENANCY legacy compatibility. |
| Mobile web page-width overflow | Unassigned; no implementation branch or PR | OPEN — inherited layout follow-up | Owner observed app document width 432px under 375px mobile emulation; confirmation dialog remains contained. Evidence: [PR175 visual report](https://github.com/Starck43/social-media-ai/pull/175#issuecomment-6152533703). Next: PRD-08.2 A must cover the shared shell overflow while preserving this acceptance boundary; identify the overflowing base layout element independently of the Chromium emulation discrepancy. Accept when affected pages fit a verified 375px viewport without unintended horizontal scrolling and confirmation focus/keyboard behavior is retained. |
| PRD-02 digest send permission parity | Remote Notion agent; `fix/digest-send-permission-parity`, [Draft PR #176](https://github.com/Starck43/social-media-ai/pull/176); direct dev implementation `6d16436` | CODE IN DEV — PR still Draft/open; separate acceptance DEFERRED | Delegation request: PR180 comment6166461970. Reuse existing collection exception; resolve reusable orchestration/fallback contract once, then delegate remaining PR176 acceptance at pinned verification HEAD0c18bab. Preserve all work/evidence; no duplicate runs, parallel shared-schema writers, repair or merge. Not DONE; no claim that recipient accepted ownership. |
| PRD-04 loopback-only database binding | Owner, local only; no remote branch or PR | DEFERRED — pre-production | Resume before the production release: restrict the checked-in PostgreSQL host publish to `127.0.0.1:5432:5432`, preserve container `db:5432` and local-owner tools, and prove network isolation with local/staging evidence. `docker/docker-compose.yml` on `dev` still publishes `5432:5432`. Do not start/restart Compose: API startup runs Alembic. |
| PRD-06 data-retention rollout | Owner; no branch or PR (code merged in `dev` via PR #139) | DEFERRED — deployment not yet approved | Owner decision (2026-10-12): do not deploy yet. Before deploying, back up `ai_analytics` if history matters and confirm `DATA_RETENTION_MONTHS` (default 1): the first daily prune after deploy irreversibly deletes analyses whose last write is older than the cutoff. Remove this row after the deployment is verified. |

For a new implementation lane, add a row with its exact bounded acceptance
condition, owner, branch/PR and state. Update status after verified integration;
preserve rows/evidence under the append-only collaboration contract. Delete a
row only on explicit owner command; keep detailed narratives in the PR.

## 2026-10-15 source audit — new follow-ups

Audit baseline: `7ba5fe4ce0879a38d96ecb92770dad055c014624`.
[PR #196](https://github.com/Starck43/social-media-ai/pull/196) records the [source-pinned report and acceptance contracts](https://github.com/Starck43/social-media-ai/blob/cea26bc48f0b3f5a7e9b61f714f4bae6aa35790d/docs/audits/2026-10-15-dev.md),
probe observations and the audit ROADMAP execution ledger. Runtime fixes are
**not implemented** by that PR or the direct documentation commits. This is
risk-based source coverage, not a full security/production certification.
Existing ownership above is preserved. One bounded task per implementation;
serialize shared-file lanes and all DB acceptance under the project runner.

| Task / priority | Outcome | Owner / state | Acceptance / dependencies |
| --- | --- | --- | --- |
| AUD-01 / P0 | Replace unauthenticated password mutation with authorized recovery | Unassigned — TODO; [PR #196](https://github.com/Starck43/social-media-ai/pull/196) | Account-bound, expiring, one-use recovery or approved operator-only reset; no anonymous hash mutation or password logs. |
| AUD-02 / P1 | Remove password hashes from HTTP user responses | Unassigned — TODO; [PR #196](https://github.com/Starck43/social-media-ai/pull/196) | HTTP/OpenAPI profiles exclude password hashes; existing rights preserved. Serialize user.py/schema writes with AUD-03. |
| AUD-03 / P1 | Separate self-service profile updates from privilege updates | Unassigned — TODO; [PR #196](https://github.com/Starck43/social-media-ai/pull/196) | Profile updates cannot elevate authority; fresh privileged operator policy and endpoint/manager negative controls. Shared lane with AUD-02. |
| AUD-04 / P1 | Use shared, cookie-independent login protection | Unassigned — TODO; [PR #196](https://github.com/Starck43/social-media-ai/pull/196) | Normal failures counted; shared cookie-independent limits across surfaces/processes; valid dummy verifier and generic errors. |
| AUD-05 / P2 | Align legacy API DB probes with bounded redacted readiness | Unassigned — TODO; [PR #196](https://github.com/Starck43/social-media-ai/pull/196) | Bounded generic legacy probe failures; synthetic DB/DSN sentinels absent; root readiness unchanged. |
| AUD-06 / P1 | Centralize safe provider-error envelopes without dropping accounting | Unassigned — TODO; [PR #196](https://github.com/Starck43/social-media-ai/pull/196) | Allowlisted failure envelopes/logs; no provider-body/prompt/token sentinel disclosure; preserve attempt identity and unknown accounting. |
| AUD-07 / P1 | Make digest scope, date window and row set consistent | Unassigned — TODO; [PR #196](https://github.com/Starck43/social-media-ai/pull/196) | All sections use one source/scenario/window/base-row scope; boundary/rollup regressions and bounded reused reads; no delivery change. |
| AUD-08 / P1 | Gate home pipeline queries and links per data component | Unassigned — TODO; [PR #196](https://github.com/Starck43/social-media-ai/pull/196) | Denied data component issues no query and shows no forbidden link; actual middleware and revocation cases. |
| AUD-09 / P2 | Make pipeline counts and activity labels truthful | Unassigned — TODO; [PR #196](https://github.com/Starck43/social-media-ai/pull/196) | Preview vs totals explicit, type-correct activity, literal or eligibility-backed staged counts; depends on AUD-08, same files. |
| AUD-10 / P2 | Make distributable package include runtime code and encryption dependency | Unassigned — TODO; [PR #196](https://github.com/Starck43/social-media-ai/pull/196) | Wheel contains nested runtime packages/assets; encryption dependency in runtime; isolated no-dev import/asset smoke, no DB/provider. |

Existing NULL-role labels are DELIVERED (`2dd3161`), not a new TODO.
PR #176 acceptance remains deferred despite code integration `6d16436`.
Admission activation, operator recovery, C/D validation, DB binding and
retention rollout retain their earlier allocations; do not close a whole gate
from this audit. Exclusive file ownership is specified in each report task.

## Selection rules

- Verify the gap in current code before treating a readiness statement as a bug.
- Read only the selected subsystem document and relevant call sites.
- Preserve delivered behavior listed in the implementation index.
- Record detailed design decisions and test evidence in the PR.
- Update this board only when priority, ownership or integration state changes.
