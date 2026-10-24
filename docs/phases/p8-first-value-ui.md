# P8 — First value and support UI (gate PRD-08)

Goal: operators can detect and own stale sources, backlog, stalled loops,
delivery failures or spend anomalies; home answers "what needs attention, what
was produced, what happens next".
Design contracts: [assistant-shell.md](../design/assistant-shell.md),
[NOTIFICATIONS.md](../NOTIFICATIONS.md),
[TENANCY.md](../TENANCY.md#web-settings-and-ownedgranted-workspace-context).
Delivered: home cleanup (PR #185, owner-local acceptance at `01f719d` in
IMPLEMENTATION_STATUS); shell A (#188), unified attention B (#189),
browser-local notification preferences C (#190), bounded settings scopes D
(#191). Stack stays FastAPI/Jinja/Alpine/HTMX with existing managers and shared
authorization predicates; no SPA replacement, no new navigation service.
Attention feed lives in the floating bell (B), not a home card.

## Tasks

- [x] P8-T26 — implement the accepted jobs lifecycle audit: built-in cleanup
  independent of a scheduled prune task, default 24-hour terminal history,
  administrator-only retention configuration through prefilled prune forms,
  safe linked replay, history-only clear, paginated jobs and useful safe error
  explanations; accept: scoped regression tests through R10, tenant and role
  isolation, unresolved effects protected, manual web execution without runtime;
  depends: none; owns: jobs lifecycle service/manager/dispatcher, web jobs and
  task forms, application lifespan, task bootstrap, focused tests and subsystem
  docs; owner: codex-jobs; status: DONE. Initial claim: `959a461`;
  isolated validation base: `37eeaf7` in `/private/tmp/social-jobs-lifecycle-check`;
  integrated onto `1f60dd1` after all 25 owned baseline hashes matched; parallel
  chat changes preserved. Primary OpenCode focused checks: A 79/79, B 52/52,
  C 67/67 (66 from the full group plus one corrected-node rerun), D 23/23;
  PostgreSQL E 4/4, including actual attention pagination and aggregates.
  All 225 cases passed without skips. PostgreSQL used the compatible existing
  `test_schema`; fixture residue and orphan checks were clean. No migration,
  schema preparation, runtime startup or live provider calls. Compile/Jinja
  syntax and diff checks passed. Final review fixed the web maintenance bypass
  context and verified cancellation with an isolated loop test. All owned Python
  files match the validated worktree byte for byte. Accepted: codex, 2026-10-20,
  implementation `a5ba8c1fc36f86bbeda692e38f5f9522c05f9f86`; exact parent,
  27-path commit scope and all 25 validated file hashes verified after commit.

- [x] P8-T01 — home mixed latest-results feed; accept: permitted analyses and
  digest runs merged into ≤5 cards via a bounded read-time projection;
  analysis creation time, digest period and actual delivery state labelled
  separately; no fabricated success, duplicated delivery or extra LLM call;
  data loading and rendering gated by effective VIEW rights (`aianalytics.view`,
  `source.view`), and digest-run reads/rendering additionally gated on
  independent `digestrun.view` rights; depends: none; owns: home
  template/route/tests; owner: home-agent; status: DONE — accepted: codex,
  2026-10-16, source `19ce6e1`; 20 focused unit tests, SQL-shape check, and
  15 PostgreSQL/ASGI acceptance cases passed.

- [x] P8-T02 — home upcoming schedules; accept: ≤3 actual upcoming, effectively
  active schedules ordered by `next_run_at`; missing next time, paused and
  completed one-shot tasks handled honestly; recent-by-creation list never
  presented as schedules; no multi-step workflow claims; depends: none;
  owns: home template/route/tests; owner: home-agent; status: DONE — accepted:
  codex, 2026-10-16, source `19ce6e1`; same focused and PostgreSQL/ASGI checks
  as P8-T01.

- [x] P8-T03 — server-side user notification preferences; accept: server is the
  source of truth, browser values import only on explicit user action, default
  flags and device-local quiet hours retained, real
  SQLAdmin/ASGI/PostgreSQL role, revocation and disable race/rollback
  acceptance for C/D executed via R10; depends: none; owns: shell/settings lane;
  owner: prefs-agent; status: DONE — accepted: codex, 2026-10-16, source
  `19ce6e1`. No-DB pytest 77/77, preference client 17/17, dashboard browser
  8/8; PostgreSQL/ASGI notification, SQLAdmin role/revocation and workspace
  mutation/race/rollback acceptance 10/10 PASS. Owner-authorized coordinator
  fallback ran the same acceptance after OpenCode/Kilo were unavailable.
  Alembic 0103 is current in both `public` and `test_schema`.

- [x] P8-T04 — initialize `source_ids` in the add-task form model;
  accept: opening the form has no `addTaskForm().task.source_ids.includes`
  error, selected IDs round-trip on selection/reset, submit semantics unchanged,
  verified with real Alpine; depends: none; owns: add-task form JS/tests;
  owner: codex; status: DONE — accepted: codex, 2026-10-16, `9a7c5cd`;
  real Alpine/Chromium selection/reactive/POST and negative-control PASS.

- [x] P8-T05 — verify reported mobile page-width overflow; accept: inspect
  authenticated ASGI pages at 375px and 320px with Chromium, confirm document
  width and dialog behavior; change CSS only if overflow reproduces. No current
  overflow reproduced; internal table scrolling is contained. Dialog focus,
  Escape and one accepted POST pass. No CSS change; accepted: codex,
  2026-10-16, source `9a7c5cd`; prior report:
  [PR #175 visual report](https://github.com/Starck43/social-media-ai/pull/175#issuecomment-6152533703)

- [x] P8-T06 — inventory operational signal and readiness coverage against
  PRD-08; accept: no duplicate P1/P2 work; depends: none;
  owns: inventory lane; owner: opencode; status: DONE — accepted: codex,
  2026-10-16, source `431059c`; coverage and follow-ups below.

Coverage evidence vs PRD-08 (state/refs → task):
- Stale sources MISSING (T07): `last_checked`/`get_sources_for_monitoring(hours_since_check=)` gate runtime; bell only `source_inactive`.
- Credential expiry PARTIAL (T08): `connections.py` reauth/missing + human expiry text; no near-expiry signal.
- Queue backlog/age/quarantine MISSING (T09): `job_manager.reap_stale(30)` quarantines + retries; no operator signal.
- Runtime freshness MISSING (T10): `health.py` `/livez`+`/readyz` = API+DB `Permission` only, not worker.
- Incomplete delivery MISSING (T11): receipts + quarantine + retry UI; bell coalesces `api_error`; no aggregate.
- Unknown price/spend DEPENDS (T12): `budget_admission` UNDEFINED + spend managers; P2 scope on P2-T06.
- Support/escalation + measures MISSING (T13): none named in P8 files.
- Delivered bell: `task_failures`, `statuses_needing_action`, `report_ready`/`api_error`/`connection_error`/`source_inactive`, read/read-window — windowed 100/50, dedup, permission-gated.

- [x] P8-T07 — stale-source observable; accept: active sources not checked within
  configured hours via `get_sources_for_monitoring`/`last_checked`, honest "never checked",
  gated `source.view`; depends: none; owns: source freshness lane; owner: opencode;
  acceptance: codex; status: DONE — accepted on 2026-10-24 at `f762088`.
  Evidence: the window is configured, not invented in a template —
  `SOURCE_STALE_HOURS` (`app/core/config.py`, default 24, `ge=1`; documented in
  `docs/CONFIGURATION.md` and `.env.example`). `/app/sources` computes one
  summary through `SourceManager.get_sources_for_monitoring` (the existing
  staleness predicate: active, and never checked or older than the window),
  intersected with the ordinary source rows the workspace page renders.
  Task-owned query lanes follow their task schedule and are excluded from this
  fixed-window source signal; their rows remain visible. The current `/app`
  route is workspace-pinned and makes no cross-workspace visibility claim. The banner
  names the count of the active total and the window, and counts sources that
  were **never** checked separately — "older than N hours" is never claimed for
  a source that has no check at all; each stale row is marked «устарел» or
  «не проверялся» with its last check in the title. Read-only: no write, no
  collection trigger, no provider call. Inactive sources are excluded (nothing
  is expected of them). Task-owned lanes remain visible but are excluded from
  this summary — a lane's freshness is its owning task's signal. Gated by
  `perms_can(request, "source", "view")`; without it the summary is `None` and
  neither the banner nor the marks render. Tests: `tests/collection/test_web_sources.py`
  11 passed, 4 new — stale/never/inactive counting with per-row marks, a
  fully fresh workspace hides the banner, the window follows
  `SOURCE_STALE_HOURS` (2 h makes a 3-hour-old check stale), and the signal is
  absent without `source.view`. Known unrelated failures in that run
  (`tests/web/test_web_ui.py`, `tests/web/test_web_list_layout.py`: settings and
  source-detail markup) are outside this lane and untouched here. Migration
  implications: none. Final source review also corrects the banner: never-checked
  sources require a check but have no elapsed check age. The banner states the
  configured freshness threshold separately from their count.
  Final code review passed on 2026-10-24 (`8c80d45`, fixture correction
  `af38f3e`); the focused no-DB acceptance package passed 132 tests and 33
  subtests. Final exact-SHA PostgreSQL/ASGI acceptance through OpenCode River
  at `f7620887f0fdcb4b32f9657cfb070fad89d96543`: 76 collected, 76 passed,
  0 failed, 0 skipped in 162.92 seconds, including the source freshness suite.
  The nine-file package and command are recorded under P5-T14. Root conftest
  redirected to `localhost:5432/social_manager/test_schema`; revision stayed
  `0109`. No migration, reset, seed, live provider call or runtime start was
  performed by this acceptance.
- [ ] P8-T08 — credential expiry/near-expiry signal; accept: reauth/missing plus near-expiry from
  vault `expires_at` metadata — reads only, never decrypts; `renewable` auto-renewal is not reauth/error;
  personal vs operator split honest; depends: none; owns: credential signal lane; owner: unassigned; status: TODO
- [ ] P8-T09 — queue backlog/age/quarantine signal; accept: operator view reusing
  `job_manager` claim/reap evidence (stale quarantine, retry receipts): pending count, oldest age, quarantined
  rows; no runtime write; depends: none; owns: queue signal lane; owner: unassigned; status: TODO
- [ ] P8-T10 — runtime freshness identity; accept: signal distinct from `/livez`-`/readyz` (API+DB only),
  honestly reporting single `app.runtime` component presence; depends: none;
  owns: runtime freshness lane; owner: unassigned; status: TODO
- [ ] P8-T11 — incomplete-delivery aggregate signal; accept: bell/operator view of undelivered/retried/
  quarantined deliveries from existing outcome receipts (job + digest) only, no new delivery semantics;
  depends: none; owns: delivery signal lane; owner: unassigned; status: TODO
- [x] P8-T12 — unknown-price/spend exposure signal; accept: operator view of provider models
  without known price plus spend totals via personal account managers (post P2-T06); depends: P2-T06;
  owns: financial visibility lane; owner: opencode; acceptance: codex;
  status: DONE — accepted by Codex on 2026-10-24 at `f762088`.
  The audit found the platform-role flag had been hardcoded false, denying
  every operator. Restore its identity check while retaining the workspace pin
  and disabled global workspace mode. The model review list already uses the
  manager's trusted personal-catalog scope; it is not a global catalog read.
  Final review passed on 2026-10-24 (`8c80d45`, fixture correction `af38f3e`).
  The focused no-DB package passed 132 tests and 33 subtests across the two
  revisions; application code is unchanged between them. Final exact-SHA
  PostgreSQL/ASGI acceptance through OpenCode River at
  `f7620887f0fdcb4b32f9657cfb070fad89d96543`: collection 76, execution 76
  passed, 0 failed, 0 skipped in 162.92 seconds. This includes actor-owned
  catalog fixtures, workspace financial reads and real ASGI identity,
  permission, workspace selection, unavailable-read and zero-write checks.
  The nine-file package and command are recorded under P5-T14. Schema revision
  remained `0109`; no migrations or schema repairs were part of the run.
  Implementation `7c23602`: `GET /app/billing/exposure` (platform-operator
  `guard_superuser`, active workspace pin, no writes) renders today's personal-ledger
  aggregates for the workspace (settled/reserved/provider-reported/tariff-reconstructed,
  open reservations, unknown-price attempts, `complete` false while either is non-zero),
  provider/model pairs with `cost_certainty=undefined` dispatched attempts over 7 days
  (distinct dispatch, reconciliations do not inflate), and active catalog models on the
  ambiguous 0/0 ordinary tariff. Failed reads render unavailable, never zeros; no payer
  identity, prompt, credential or provider payload is rendered. Evidence: no-DB route
  suite 4 passed + 6 subtests (`tests/unit_web/test_billing_exposure.py`), PostgreSQL
  manager suite 3 passed (`tests/billing/test_billing_exposure_db.py`) through the
  primary OpenCode runner on the shared test schema; `git diff --check` clean; no
  migration, live provider call, runtime start or deployment. These historical
  checks precede the final ASGI acceptance above. Limits: the zero-price list
  is a review aid, explicit free and an
  unconfigured default are indistinguishable; legacy unattributed attempts are outside
  the ledger projection; browser interaction was not part of this acceptance.
- [ ] P8-T13 — named support/escalation + pilot measures; accept: escalation owner and agreed
  latency/freshness/usefulness measures for the managed pilot, written and owner-signed; depends:
  P8-T10, P8-T11; owns: support contract lane; owner: unassigned; status: TODO

- [x] P8-T14 — integrate the shared local LLM check/admin UI with personal billing;
  accept: owner-reviewed combined candidate preserves CONFIGURE/CSRF/paid consent,
  trusted initiating-user payer, explicit workspace, fixed 8-token/20-second probe,
  unknown-usage semantics and no automatic paid retry; short/mock remain local;
  depends: P2 billing review and separately authorized local acceptance;
  owns: isolated integration branch only; source lanes remain unchanged;
  owner: remote Notion agent (`review/admin-llm-billing-integration`);
  status: DONE — accepted: coordinator review in OpenCode session under explicit
  owner instruction, 2026-10-17, at dev `c18ee2f`. Closeout review re-verified:
  PR #205 state MERGED at `b0bd202b`, tested HEAD `0b1fd71d` and merge trees are
  byte-identical (`d9e1a3c9a331e9d06d6ce22d91b39b39c6fbf48d` both); PR #206
  (`c27b9468`) is a docs-only two-file evidence sync (ROADMAP.md + this file);
  GitHub confirms both merges. All evidence caveats below are preserved as
  accepted limitations: warning details of the 41-case local run were not
  supplied, Mac-local audit files were not independently inspected remotely,
  a genuine VIEW-only DB login was not tested, and the 56-case offline browser
  QA belongs to `ace7c536`, not the final HEAD.
  [PR #205](https://github.com/Starck43/social-media-ai/pull/205) merged into dev
  with explicit owner approval; full tested/merge SHAs: HEAD
  `0b1fd71d08a332e668d25eeb585fc27bb01127af`, merge commit
  `b0bd202bccb04cdbcad009ddd6a8bd5664221a8c`.
  Evidence at the tested HEAD: 110/110 remote isolated tests; owner-relayed
  local `tests/test_admin_model_billing_acceptance.py`: 41 collected / 41 passed,
  0 failed/skipped/deselected/errors, collection/execution exits 0;
  execution reported 12 warnings in 62.08s (warning details not supplied).
  Mac-local audit files were not independently inspected remotely.
  `OBSERVED_OPERATOR_MODEL_GRANTS []`; actual DB-profile enforcement passed.
  A genuine VIEW-only DB login was not tested; VIEW/UPDATE do not imply
  CONFIGURE in separate controlled isolated tests. The owner explicitly
  accepted this evidence split. Historical 56-case offline browser QA belongs
  to `ace7c536300ae3ec1d5f9abaaa430bec4cdb74ad`, not a rerun at the final HEAD.
  See PR #205 and `docs/validation/admin-llm-billing-acceptance.md` for scope.
  Source PRs #200/#201 are snapshot-integrated, not ancestry-merged; closeout
  content review found no outstanding source changes requiring another merge.
  Their branches and owner-local artifacts remain preserved; closing these PRs
  requires separate owner authorization. The historical admin P8-T06 collision
  remains mapped to T14; accepted inventory T06 is unchanged.
  DONE marking and ROADMAP-row removal were reserved to the coordinating Codex
  after exact-SHA review; executed here by the owner's explicit in-session
  instruction with the same exact-SHA review recorded above. This is not broader
  P2/billing acceptance or P8 phase approval. No repeated tests, schema/grant/
  .env operations, fixture cleanup, runtime activation, deployment or live/paid
  calls are authorized by closeout.

Historical home placeholders (attention block, empty/access states, extension
points) are covered by merged B/D slices; future kinds enter attention only
when their contracts exist — no fake rows or Soon cards. NULL-role membership
labels are DELIVERED (`2dd3161`).

## Analysis progress delivery

PR #203 adds persisted source analysis progress to the source table and
"Выполнить сейчас" dialog, with role-gated diagnostics. Acceptance at
`dd36e7b` and Safari check at `ca282f7`: noDB 222 passed, DB/ASGI 33 passed;
authenticated Safari view and progress endpoint returned HTTP 200. No
migrations or dependency changes. This delivery is separate from inventory
P8-T06 and does not alter its task record.

- [ ] P8-T15 — distinct admin model checks; accept: short performs an authenticated
  metadata API request without generation; mock remains offline; real forwards
  the supplied prompt or a short default, with bounded output, existing consent,
  payer admission and no retry; mode-specific useful diagnostics; no migrations;
  owns: admin model action/endpoint/template/JS, probe helpers, focused tests/docs;
  owner: codex (`fix/admin-model-real-request`); status: IN_PROGRESS
  (implementation ready; required local ASGI/PostgreSQL acceptance unavailable).
  Remote evidence: 107 isolated no-DB tests passed; 56 offline Chromium
  viewport/theme/state cases plus form/consent/no-JS checks passed;
  Black/isort checks, py_compile and diff whitespace checks passed.
  No migration, real provider or owner-local application run. Updated local
  acceptance covers metadata vs offline web checks and blank/custom prompt
  settlement. PR #207 was merged at owner-authorized exception; its local
  R8/R10 acceptance remains deferred, not passed.

- [ ] P8-T16 — repair admin sidebar/menu continuity, display the verified actor and
  platform role, and expose platform-admin-only role-grant/permission-label
  editing without mutable catalog identities or role escalation; owner: codex
  (`fix/admin-role-picker-controls`); status: IN_PROGRESS
  (owner-requested self-role protection and responsive permission transfer picker;
  base dev `58a90c1` (includes PR #212 and fresh documentation); owner-local real PostgreSQL acceptance remains pending).
  Owns: shared admin shell/CSS/authorization, role and permission views, focused
  regression tests and documentation. P8-T15 model request behavior is unchanged.
  Evidence: 131 isolated tests passed, including real SQLAdmin routes/forms with
  explicit identity/storage doubles; 18 offline Chromium layout cases and one
  no-JavaScript case passed. This is not real DB/owner-account acceptance.
  The owner explicitly authorized merging PR #208 with real-environment
  verification deferred to their machine; this does not certify DB acceptance
  or mark the task DONE. Pending sequential local R8/R10 verification: admin/
  operator/revoked identities, menu continuity after model-test POST, persisted
  operator grants and permission names with protected catalog fields unchanged.
  Preserve existing grants/data; no reset, migration or live API calls.
  Historical PR #208 limitation: additional synthetic grant-selection cases were
  blocked by the execution safety gate and were not applied or run in that PR.
  Owner-requested follow-up from dev `b70911d`: removed the operator-only role-row
  edit restriction in both SQLAdmin and the canonical manager writer. An active
  platform admin may edit stored grants on user/admin/operator records; other
  actors remain denied and fixed role authority/catalog identities are unchanged.
  Follow-up evidence: 155 isolated tests passed, including all three edit links,
  GET/POST forms with explicit storage doubles, manager policy for every role,
  revocation/inactive/anonymous denial, CSRF and immutable-field checks.
  Real database persistence is not certified; the owner's local-verification
  agreement remains in force. No migration or runtime/provider operation.

  Follow-up from dev `a36ef4f`: self-role assignments now fail before any manager
  write (including `admin` to `admin`), and generic user CRUD rejects role fields.
  Another active platform admin can still assign roles to other accounts.
  Role grants use a dependency-free Available/Chosen picker with search, counts,
  double-click, touch buttons, keyboard support, native select submission/reset
  and bounded list-only scrolling. Native no-JS selection is preserved.
  Evidence on refreshed dev `3e57794`: 192 isolated tests passed (0 failed/skipped);
  18 offline Chromium
  width/theme/state cases plus desktop, touch and no-JS interaction suites passed
  with 30 synthetic permissions (Chromium 153.0.8010.12). Static formatting,
  compilation, JS syntax and whitespace checks passed. No app/runtime, DB,
  migration, live provider or deployment operation. Owner-local sequential
  verification still needs persisted role grant edits, forbidden self-role API
  writes, allowed other-account assignment and the actual owner browser form.
  The standing owner request to publish to dev and test locally remains in force;
  this is a delivery handoff, not final task/phase acceptance.

  Styling follow-up from dev `bd67996` (refreshed to docs-only `58a90c1`): neutralized the legacy checkbox-pill
  cascade with block-flow lists, compact borderless/gapless rows and top-aligned
  empty states. A viewport-height role editor keeps ordinary SQLAdmin footer
  buttons visible; no full-width custom button styling. Only permission lists
  scroll. The canonical Permission manager supplies code/display-name choices;
  domain filtering, resource headings and word search work without changing the
  hidden native select or server authorization/self-role protection.
  Evidence: 204 isolated tests passed (0 failed/skipped); 48 offline Chromium
  cases (390x667, 390x844, 768x700, 1440x900; both themes; six selection states)
  plus desktop, group/name-filter, touch and no-JS interaction suites passed with
  up to 206 synthetic permissions. Checks assert footer/viewport bounds, row CSS,
  legacy selector collision, search/group selection retention, native FormData,
  reset and contained scrolling. Black/isort, compilation, JS syntax and diff
  checks passed. Real owner-browser/DB acceptance remains deferred under the
  standing owner delivery/local-feedback instruction. No migration, runtime,
  provider call or deployment; task is not self-accepted as DONE.

  Presentation-only follow-up from fresh dev `9868b20`: field caption and a smaller
  hint below the panes, circular transfer arrows with marked-item counts between
  them, and non-collapsible access rules with readable spacing. Footer buttons,
  grouping/search, row styles and authorization/persistence behavior are unchanged.
  Evidence: 59 isolated catalog/role/self-role tests and 48 offline Chromium cases
  plus four interaction suites passed with up to 206 synthetic permissions.
  Checks include caption/button positions, 44px circular targets, badge counts,
  always-visible rules, small hint text, keyboard/touch, native submission/reset,
  filtering and viewport/footer bounds. Formatting, JS syntax and diff checks
  passed. Remote mocks only; real owner-browser/DB acceptance is still deferred.
  Shared docs synchronized with dev; foreign task records and evidence preserved.

- [ ] P8-T17 — remove redundant paid checkbox; explicit real POST is consent;
  readable answer plus sanitized provider JSON and transport/accounting statuses;
  visible pre-dispatch blockers with no bypass of CONFIGURE/CSRF/payer admission;
  owns: model-test action/endpoint/template/JS/CSS, opt-in client diagnostics,
  focused regression tests/docs; owner: codex
  (`fix/admin-model-diagnostics`); status: IN_PROGRESS (implementation ready; real
  PostgreSQL/owner-environment acceptance deferred under the owner's explicit
  implementation-and-dev instruction following their merge exception).
  Evidence: 167 isolated tests passed (actual client source over MockTransport,
  explicit storage/admission doubles; not real billing integration); 72 offline
  Chromium viewport/theme/state cases plus blank/custom submit, no extra consent,
  JSON copying/manual fallback, result focus, no-JS, keyboard and busy guard
  passed. Black/isort scoped checks, py_compile and diff whitespace check passed.
  Local acceptance was updated but not run: real payer/ledger/settlement, CSRF,
  revocation and workspace guards remain required R8/R10 follow-up. No migration,
  schema/config/runtime activation, real provider or owner-local app run.

- [ ] P8-T18 — real model test runs in existing single-owner mode without
  personal billing/workspace setup; enabled personal mode retains trusted actor
  payer, workspace and account/budget admission; remove disabled-billing warning;
  preserve answer/JSON/statuses and normal model usage; no activation/migrations;
  owns: model-test endpoint/actions/probe/client diagnostic label/template/JS,
  focused tests/docs; owner: codex (`fix/admin-model-owner-mode`);
  status: IN_PROGRESS only for deferred owner-local acceptance, not implementation.
  Owner explicitly clarified that disabled personal billing is normal single-owner
  operation and must not block real tests. Evidence: 178 isolated tests passed;
  80 offline Chromium viewport/theme/state cases plus owner blank/custom submit
  without workspace, personal workspace validation, no-JS, JSON copy/fallback,
  busy guard, keyboard and focus checks passed. Source uses the actual client
  flag-off admission branch over MockTransport; storage/metrics are explicit
  doubles, not a real DB/ledger acceptance claim. Scope formatting, compilation,
  JS syntax and diff checks passed. Prepared local acceptance covers one owner
  request, model counter/cost readback and zero personal dispatches; not executed
  remotely. Existing personal payer/admission/settlement tests remain. Owner's
  in-session dev delivery/local-validation deferral remains in force. No setting
  activation, schema change, runtime, live provider call or deployment.

- [x] P8-T19 — audit collect force-refresh semantics and task-run feedback; accept:
  correlate both Safari launch actions with persisted Jobs and source outcomes,
  document raw/analytics overwrite behavior and confirmed failure cause, preserve
  existing data, and record bounded follow-ups. Owner: codex-collect-repro;
  status: DONE. Identifier-only reconciliation: this audit formerly duplicated
  P8-T17 (the existing admin diagnostics task); it is now P8-T19. Ownership,
  acceptance and evidence are unchanged. Accepted by coordinating Codex on 2026-10-17 against the source
  SHA below and the report diff. Evidence: [collection launch audit](../audits/2026-10-17-collection-launch.md)
  reviewed at source SHA `b70911d6c1e0c6a71e5668a502df42f1e73fefa9`. Jobs 1737 and
  1738 were correlated to the two Safari actions; each ended after about five
  seconds with one VK source error and zero collected records. The reported
  missing modal was not reproduced. The screen-name lookup failed with
  `httpx.ConnectTimeout`; the precise network cause is unknown. Public raw and
  analytics data were unchanged. No code or tests were changed or run. The
  confirmed task-summary `ok` despite all-source errors is recorded as a bounded
  follow-up; no speculative launch fix was applied.

- [ ] P8-T20 — verify OpenRouter token/cost evidence (including explicit zero),
  model-test persistence and response-only truncation; replace JSON copy text
  control with accessible corner SVG feedback and semantic status text colors.
  Scope: model-test UI, focused cost extraction if needed, regression tests/docs.
  Owner: codex (`fix/admin-model-cost-feedback`); status: IN_PROGRESS (implementation ready;
  owner-local PostgreSQL/ASGI acceptance remains deferred under the explicit
  in-session dev delivery/merge exception).
  Acceptance: supplied 470/8/478, zero-cost response has faithful cost evidence;
  accessible copy success on icon only, manual fallback retained; dark/light and
  narrow layouts; no real provider/DB calls remotely, local persistence deferred.
  Identifier-only synchronization: P8-T19 was reserved by the merged collection
  audit before publication; this lane now uses P8-T20. Foreign owners, evidence
  and statuses are preserved. Synced base: `18a2cb6`.
  Evidence: 198 isolated tests passed (0 failed/skipped) on synchronized dev,
  including actual source over HTTP/storage doubles, canonical structured USD
  evidence and journal pricing. 144 offline Chromium viewport/theme/state cases
  (320/390/1440 px; explicit/system light/dark) plus clipboard success/reset,
  denial/manual selection, SVG position/44px target, keyboard, no-JS, submit and
  busy/bfcache safeguards passed. Semantic text contrast >=4.5 checked. Scoped
  Black/isort, Python compilation, JS syntax and diff checks passed. Prepared
  real DB readback case was updated, not remotely executed. API price is separate
  from the existing last-request tariff estimate; no token-history guarantee is
  added for unscoped owner mode. No schema/config/runtime/live provider/deploy.
  Remote tests are self-executed per the owner's explicit exception, now recorded
  in the remote skill; shared-doc sync follows the existing all-agent procedure.

- [x] P8-T21 — add/edit related FK/m2m records without losing the parent form.
  Accept: permission-aware controls on existing editable relationships; normal
  guarded CRUD in a separate window; successful save updates the correct select,
  preserves other m2m selections and unsaved parent input; cancellation, blocked
  popup, validation errors and hostile messages do not mutate the selection.
  No new catalog privileges, raw FK fields, ORM writes, schema or live actions.
  Depends: none; owns: app/admin/related.py, scoped setup/template integration,
  app/admin/base.py (form converter), app/static/{js,css}/admin-related.* and
  focused tests/docs/ADMIN.md; owner: remote
  Notion agent (`feat/admin-related-records`); status: DONE (owner accepted the bounded implementation and explicitly
  authorized Ready/merge after reporting that the updated local UI works).
  Synchronized base: `1e0e3a0c772c4a7bcc04862434c172d32288868e`. Active PRs #216
  (P10-T11) and #217 (P10-T12) inspected; their reservations, ownership and
  evidence remain on their own branches, with no code ownership conflict.
  Evidence at `6f1f695dec730396bda79043d19414fea13a2094`: 20 isolated tests passed
  (exit 0, no failures/skips), 32 offline Chromium assertions at 390/1440px
  (exit 0, all requests intercepted); actual target route/fragment behavior
  over synthetic ORM and explicit storage doubles, not real DB acceptance.
  [Contract, commands, versions and local handoff](../ADMIN.md#related-fkm2m-record-editors).
  Follow-up on owner feedback: inline plus/pencil SVG controls (not text buttons
  below the field); canonical Tenant Float `NoConverterFound` reproduced on
  SQLAlchemy 2.1/SQLAdmin 0.32 and fixed in BaseAdmin without converting precise
  Numeric/Decimal money. 81 isolated tests + 228 offline browser assertions
  passed (exit 0; 0 failed/skipped), including real Select2 and canonical Tenant
  create/edit/related GET scaffolding. Original evidence remains attributed above;
  local reported 500 is failure evidence only. Exact follow-up published SHA in
  PR #219 handoff. Owner subsequently reported that the updated local UI works
  and requested closeout/Ready/merge. This is owner-reported functional smoke,
  not evidence that the full PostgreSQL/revocation/rollback checklist ran.
  Accepted source: `aec048db192bfc9842883502065646d5650f43d9`; reviewed against
  fresh dev `1e0e3a0c772c4a7bcc04862434c172d32288868e`, no conflicting changes
  or review threads; GitGuardian check passed. Prior failures and sandbox evidence
  remain attributed above. Phase/gate approval, full-suite, deployment and schema
  acceptance are not implied by this task closure.

- [x] P8-T22 — audit and simplify the social analysis web journey; accept:
  one social analysis task choice collects then analyzes, standalone CLI/API/chat
  operations remain compatible, partial analysis retains staged input; scenario,
  source and task pages explain their distinct setup/actions/statistics using
  shared shell styles and permission-gated diagnostics; focused backend/render
  checks include the final task editor snapshot; visual testing excluded by
  the owner's later instruction; depends: none;
  owns: web task backend and scenario/source/task templates, shared web
  presentation helpers, final editor follow-up after the explicit P8-T23 handoff,
  focused tests and subsystem/audit docs; owner: codex-social-analysis;
  status: DROPPED (2026-10-21) — accepted web-journey implementation remains in
  the code, but the remaining blocked task-editor/scenario/source presentation
  scope is absorbed by P5-T14 stage E (task-owned collection UI and presets).
  The old test-schema blocker is also removed by migration 0108. Reopen only
  if a bounded P5-T14 E gap remains after that stage. Historical evidence:
  Base: local dev `c98dc9c`, origin/dev `1e0e3a0`
  verified 2026-10-18; source-picker changes preserved. Task editor changes
  are serialized after its active source-picker work finishes.
  Implementation and isolated verification recorded at `a11f1f9` plus reviewed
  working diff: 210 isolated pytest cases passed through primary OpenCode;
  Node cron checks and Safari 27.0 actual synthetic POST checks passed. See
  [audit and layout contract](../audits/2026-10-18-social-analysis-web.md).
  Persisted PostgreSQL acceptance remains blocked: the local
  test schema lacks `analyzed_content_receipts`; no migration/reset authorized.
  The live site returned 502. No paid collection/model run or full responsive
  visual acceptance is claimed. The final compact selector/button styling
  follow-up received syntax and diff checks only; it was not visually tested.
  Source-detail follow-up claimed 2026-10-19 at `401a636`, synchronized with
  the intervening owner commits through `346cd65`, by
  codex-social-analysis: retain source aggregates/access and related task links,
  move all mutable collection settings into an inline form, remove source-page
  run/history UI and header actions, and keep one source-prefilled task CTA.
  Existing unrelated working changes and exposed compatibility routes are
  preserved. Python/Jinja syntax and diff checks passed. Primary OpenCode
  collected 122 isolated cases: 118 passed; four editor cases exposed a missing
  constant in the AST test fixture. After that fixture-only correction, the
  four cases passed in a focused rerun (0 failed/skipped); application code
  remained unchanged. No visual or database-backed follow-up is claimed.
  Task-detail follow-up: moved latest task-associated run counters, per-source
  progress, raw-data coverage and admin-only diagnostics onto task detail; task
  navbar now exposes edit and run now, with retry in the run panel. Retry resets
  attempts only for that task's source/date scope and rolls back on busy
  admission. The canonical editor is reused in a modal; save returns to task
  detail, and task names link to detail. Current raw backlog uses authorized,
  windowed batch counts; model-call diagnostics match exact task and Job.
  Synchronized with the unrelated admin integration at `ddc9b99`, preserving
  its accepted P8-T21. Primary OpenCode collected 131 no-DB cases: 130 passed
  initially and one text assertion was corrected; targeted rerun passed 21/21
  cases after the bound-manager correction, leaving the other 110 unchanged
  passing cases valid. Python/Jinja/Node syntax and diff checks passed. Actual
  read-only ORM access for task 1582 passed; the local URI still returns 502.
  No browser, live execution or persisted worker acceptance is claimed.
  Closeout 2026-10-19: implementation reviewed for the authorized local commit;
  source/task implementation committed as
  `ca4b9b04cb42396765b0d5816abce0f67d8e21df`.
  Persisted acceptance remains BLOCKED. A fresh catalog query using the existing
  session factory in a read-only transaction confirmed that
  `test_schema.analyzed_content_receipts` is absent. No schema change was made;
  R8 requires explicit authorization for migration. No other active task's
  acceptance is implied. Visual checks are excluded by the owner's instruction.

- [x] P8-T23 — compact inline source picker on the task edit page; accept:
  selected sources render as inline chips with a remove control, adding uses
  a searchable list with shared styles, POST semantics of `source_ids` stay
  unchanged, and no-JS checkbox fallback is retained. Depends: none; owns:
  `app/web/templates/web/task_edit.html`, focused render test; owner: $local;
  status: DONE — accepted: codex, 2026-10-19, implementation commit
  `a11f1f9e880925b01bf30478dbde62fa6ad0b48c`; isolated no-DB pytest 53/53 passed
  (`tests/unit_web/test_task_source_picker.py`,
  `tests/unit_web/test_template_autoescape.py`); rendered script Node syntax
  check passed. Integration `tests/test_web_task_edit.py` blocked before setup
  by pre-existing test-schema drift (`analyzed_content_receipts` absent);
  schema unchanged. Identifier-only reconciliation after local merge
  `ddc9b99`: this source-picker row formerly duplicated P8-T21, now reserved
  by the accepted admin related-record editor. Ownership, status and evidence
  of both tasks are preserved.
  Coordinator closeout review found the picker acceptance met; the separately
  blocked PostgreSQL suite is retained as a documented integration limit and
  does not claim persisted worker acceptance. Removed its open registry row.

## Phase approval

- [ ] P8-T29 — the chat agent can turn an abstract "find and analyse" request
  into a reviewable plan instead of one concrete task. Scope: `search_plan`
  (free-form request → paraphrase families, platform search queries and a short
  candidate wording for target names, every value proposed and confirmed);
  `sources_search` (uses the P5-T14 discovery lanes and returns a capped,
  tenant-scoped candidate list — usernames, names, city, reason; returns to the
  model only a bounded list); `source_add` accepts `mode`, `token_owner` and
  `params` so a confirmed candidate becomes a usable source without the web UI;
  `task_plan` (one multi-step preview: collection content/queries/city, sources,
  scenario, window) executed as confirmed `task_add` calls.
  `scenario_suggest_prompt` and `scenario_create` warn when a custom prompt
  omits `{payload_instruction}` and a scenario asks for targets the task does
  not carry. Prompt procedure: never invent usernames, people or organisations;
  ask the owner to confirm every paraphrase and candidate; always state that
  discovery coverage is partial. Accept: mocked-runtime tests cover the plan
  preview, candidate cap, refusal without confirmation and the prompt warning;
  no live platform call in verification; depends: P5-T08 (DONE), P5-T14;
  owns: app/agent/toolset/, app/agent/prompts.py, focused tests; owner:
  unassigned; status: TODO

- [x] P8-T24 — keep incomplete-analysis recovery and its progress on analysis
  detail; accept: explicit retry launches only after committed admission even
  without the runtime worker, queue/running/unconfirmed states reflect stored
  evidence, shared progress UI renders inline, and content refreshes after
  completion. Task links replace source/queue detours, the detail layout keeps
  useful stored content and hides empty sections. Preserve tenant/permission,
  exact-window and transaction guards; no live model calls in verification.
  Depends: none; owns: analytics web retry/detail routes and templates, shared
  progress presentation, focused regression tests and analytics docs;
  owner: codex-analytics-inline-retry; status: DONE — accepted: codex,
  2026-10-19, implementation commit
  `78f7003dce35e15bbf4ee1be5a59873b7447bf73`.
  Claimed 2026-10-19 at local dev `ddc9b99`; preserve the current uncommitted
  source/task follow-up and accepted unrelated admin integration.
  Evidence 2026-10-19: reviewed the uncommitted diff on base `ddc9b99`.
  Explicit retries dispatch only after admission commits; compatible old pending
  retries require a guarded POST to resume. Inline polling has no write effects;
  queue and stale-heartbeat states do not claim provider activity. Fresh saved
  result IDs drive completion navigation, while related tasks replace source
  detours. Complete summaries with legacy metrics do not trigger recovery reads.
  Primary OpenCode `QwenPlan/deepseek-v4-flash-0731` collected and passed 232
  no-DB tests (0 failed, 0 skipped; 35 subtests passed) using
  `--noconftest -o addopts= -q --tb=short --no-header` across:
  `tests/unit_web/test_analysis_retry.py`,
  `tests/unit_web/test_analytics_inline_progress.py`,
  `tests/unit_web/test_analysis_result_ids.py`,
  `tests/unit_web/test_run_progress_component.py`,
  `tests/unit_web/test_analysis_content_layout.py`,
  `tests/unit_web/test_task_detail_progress.py`,
  `tests/unit_web/test_template_autoescape.py`,
  `tests/test_analysis_progress_endpoints.py`,
  `tests/test_inline_analysis_errors.py`, and
  `tests/test_analyzer_reported_errors.py`.
  Updated stale modality-model and durable-stage test doubles without weakening
  failure assertions. `git diff --check` passed. No migration, browser checks,
  runtime start, live Job dispatch, provider call or database write was performed.
  Coordinator reviewed implementation commits `ca4b9b0` and `78f7003` at
  closeout; the committed app/test contents match the verified working tree.
  Accepted this bounded no-DB contract and removed its open registry row;
  persisted worker/billing acceptance and phase approval are not implied.

- [x] P8-T25 — settings information layout and compact member access; accept:
  replace the two segment rows with one settings navigation, describe each
  section, align forms/buttons and put the model test beside its selector.
  Owner review: hide owner/self access controls; deny self-role changes in the
  web handler and locked manager, including with a second owner; keep tariff
  plans visible at the bottom; scope the admin entry to the existing active
  platform operator/admin predicate; use the shared picker for settings and
  invitation selects, including lazily loaded dialog content.
  Depends: none; owns: settings/invite templates and CSS, shared selector
  macro/initialization, bounded self-role guards, tenancy docs and focused
  regression expectations; owner: remote Notion agent;
  status: DONE; PR #222 and follow-up #223 are merged.
  Coordinator refresh 2026-10-21: live GitHub confirms #222 and its small
  follow-up #223 are merged; heads `75012fb` and `76d44a6` are historical
  integrated source, not open merge requests. Review current code and the
  owner-local role/access acceptance rather than reapplying old snapshots.
  Historical follow-up: submitted READY — admin-only Platforms navigation entry to the existing
  `/admin/platform/list` catalog using the active `is_platform_admin` predicate.
  Based on dev `6a040b0dd980c5c8a899e8b43a11b8f3cc94789c`; template/source diff
  reviewed only. No tests run at explicit owner request; no backend/schema
  changes. Owner authorized immediate merge into dev; task acceptance remains
  separate. PR #222 was already merged, so this uses a small follow-up PR.
  Initial implementation `500900ba86293f063d0bf0bce6f551be788dc023` passed
  109 isolated tests and 104 browser cases; its review follow-up supersedes
  the initial hidden tariff comparison and visible owner role editor.
  Reviewed implementation: `64e3eed0718640ba493535011da811b3e17bb256`.
  Synchronized dev: `0c757b274aaa6c46054e0bec6298dc30485d8c64`; active
  #216/#217/#220 heads unchanged and foreign owners/evidence preserved.
  Sandbox evidence at the reviewed SHA: 123 isolated checks passed across
  `tests/unit_web/test_settings_review.py`, `test_settings_layout.py`,
  `test_settings_scopes.py` and `test_template_autoescape.py`; 7 selected
  `test_workspace_access.py` checks passed with
  `-k 'team_change_locks_workspace or admin_entry_requires_active_platform_operator_or_admin'`.
  Both runs used `--noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header`:
  total 130 passed, 0 failed/skipped (59 intentionally deselected in the second run).
  `node tests/web_shell_state.cjs`: 38 mocked controller checks passed.
  `tests/settings_layout_fixtures.py` and `tests/web_settings_layout.cjs`:
  104 offline browser cases passed (13 states, 375/390/768/1440px, light/dark).
  Includes role/day picker keyboard selection, hidden form values, repeated
  lazy invite initialization, option-list bounds, owner/self exclusion, admin
  entry visibility, always-visible plans, existing model/form confirmations,
  and desktop account/theme popover regression. Screenshots visually checked
  on desktop/mobile and light/dark, including open option lists in dialogs.
  Black/isort, JS syntax and diff checks passed. Runtime/test bytes published
  through GitHub match the tested sandbox. No new production dependency.
  Environment: isolated venv, Python 3.13.14, pytest 9.1.1,
  Coordinator acceptance 2026-10-21 at tested source `f55abad` (current dev
  policy-only successor `421d369`): OpenCode sequential R8/R10 PostgreSQL
  acceptance passed `tests/web/test_settings_membership_acceptance.py` 4/4
  and `tests/web/test_settings_admin_entry_acceptance.py` 4/4. Focused current
  no-DB scopes passed `test_settings_review.py` 14/14, `test_settings_layout.py`
  23/23, `test_settings_scopes.py` 37/37, `test_workspace_access.py` 66/66,
  `test_template_autoescape.py` 49/49 and
  `tests/security/test_user_self_role_guard.py` 10/10. Two isolated test
  fixtures were corrected without changing application behavior or weakening
  assertions. Earlier browser/layout, shared-picker, tariff-visibility and
  responsive evidence above remains applicable. The coordinator reviewed
  implementation and exact-scope evidence; accepted DONE. Root health checks
  are unrelated to this task.
  pytest-asyncio 1.4.0, Jinja2 3.1.6, SQLAlchemy 2.1.5 (offline query checks),
  Playwright 1.63.0 and Chromium 153.0.8010.12.
  Three real-DB acceptance files had their self-demotion expectations updated
  and syntax checked, but were NOT executed remotely: `test_web_settings.py`,
  `test_settings_membership_acceptance.py`, `test_access_postgres_acceptance.py`.
  Synthetic checks are not full-suite/real persistence acceptance. No real DB,
  app/runtime startup, provider call, migration, reset or deployment occurred.
  The owner explicitly authorized PR closure by merge after these changes;
  coordinator task/phase acceptance remains separate from this merge authority.
  Historical source-refresh notes above were superseded by coordinator
  acceptance at `f55abad` (current `421d369` is policy-only): current role and
  revocation PostgreSQL/ASGI scopes passed 8/8; all six focused settings and
  self-role unit files passed 199/199. The two fixture-only corrections are
  recorded in the acceptance note above; no application code changed. Current
  aggregate evidence and remaining unrelated checks:
  [refresh audit](../audits/2026-10-21-ready-pr-audit.md).

- [x] P8-T27 — implement the Platforms settings tab for active platform admins,
  correct admin button contrast and bulk-delete confirmation wrapping, and
  audit app/models FK/ORM deletion rules; accept: active platform-admin and
  CSRF checks, catalog operation without workspace membership, preserved
  selection/delete semantics, bounded style regressions and accurate deletion
  inventory with owner-local PostgreSQL/ASGI acceptance. Depends: none; owns:
  bounded settings/admin styles, focused tests and deletion documentation;
  owner: codex; status: DONE. Canonical row restored from
  Draft [PR #224](https://github.com/Starck43/social-media-ai/pull/224), head
  `6fd66d163a11ae5a86ac6a9756e1d97214d53716`, verified live 2026-10-21;
  implementation `39fa3fc`, original synchronized base `213980a`.
  Historical isolated/source evidence remains in that PR; its handoff did not
  claim new PG/ASGI or deletion acceptance. Integration must preserve
  integrated settings controls, model pricing-unit labels and active-admin
  predicates. The old branch's `/1K` formatter is not a change in its three-dot
  diff; preserve current 1K/1M behavior rather than copying its whole file.
  No cascade/model/schema/migration changes are authorized by this review.
  Coordinator acceptance (2026-10-23): scoped implementation reconciled in an
  isolated tree at `dea6e4d985f0d5962ad959e86d19429d85688d95`; committed in
  `3226b405fec7988432175f608c9c79f8ab211ef9` on base `499962c5`. Every
  committed application and test blob matches the accepted candidate exactly.
  `git diff HEAD^ HEAD --check` passed. Active platform admins can
  create/edit/disable catalog rows without
  workspace membership; CSRF, immutable existing type/params/links and no-delete
  controls are preserved. Primary OpenCode: 94 isolated settings tests passed;
  real PostgreSQL/ASGI platform acceptance 15 passed, 0 failed. Final offline
  browser matrix passed 32/32, 0 failed, with default production animations,
  real SQLAdmin assets, exact 200-ID selection/order/URL and bounded scrolling.
  Before/after CSS SHA-256 was identical:
  `a642f2bfe34f231e36efd6dd5b040042d2992c959719d6348d046b4f8344d6f6`.
  Modern CSS colors and settled states are measured by the corrected harness:
  42 controls / 168 readings, no enabled contrast failures; disabled controls
  are exempt. There were no external browser requests. The candidate's recorded
  test evidence applies to the exact committed application/test blobs: 94
  isolated settings tests, 15 PostgreSQL/ASGI acceptance tests and 32 offline
  browser checks passed. Tests were not rerun after commit. No schema or
  migration change was included. Deletion inventory records the
  administrator/platform hard-delete bypass as separate P6-T05; this audit
  does not certify those destructive paths or deployment.

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —


- [x] P8-T28 — implement the owner-approved two-stage task run progress;
  accept: shared full/compact collection and analysis record counters with source
  segmentation, honest unknown/partial/dry-run states, no prototype toolbar,
  source names/links and last unsuccessful action only in the task sidebar,
  existing pending/admission and save/run navigation preserved; isolated render,
  projection, persistence-writer and offline browser regressions documented.
  Depends: none; owns: bounded task web presentation/routes, shared progress
  template additions, task progress projection, progress snapshot aggregates,
  focused tests and task documentation; owner: remote Notion agent;
  status: DONE (coordinator bounded presentation acceptance at `da464189`; PR #225). Claimed from fresh dev
  `adf1d6fc79ea147863c4c2d7560349140ab2a6cf` on branch
  `ui/task-run-segmented-progress`. Owner explicitly directed this bounded
  follow-up after design approval and review of the new dry-run/collection code.
  This is a narrow owner-authorized handoff of task presentation from P8-T22;
  its foreign owner, BLOCKED status and historical acceptance remain unchanged.
  No analyzer, receipts, collection algorithms, handler business logic, source
  editor or migrations are in scope. P8-T27 remains reserved by PR #224;
  the former duplicate agent row was renumbered P8-T29 on 2026-10-21; the
  settings task keeps P8-T25.
  Implementation: `05949aff7377573e540b47470cc3b7eb763af24d`; all 16 published
  implementation/test files matched the sandbox bytes. Remote self-run evidence:
  58 isolated unit checks and 12 offline Chromium checks (18 subtests) passed;
  desktop/mobile template screenshots inspected. Exact commands, counter units,
  dry-run semantics, current-run sidebar scope and validation limitations:
  [P8-T28 handoff](../validation/p8-t28-task-progress.md). No real DB/ASGI,
  app/runtime startup, migration/reset, provider or full-suite acceptance was run.
  Local coordinator audit corrected an idle failed-poll guard defect in
  `da464189`. OpenCode: 58 focused checks, full task-detail 15 checks and
  offline browser 13 checks (18 subtests) passed; actual HTMX/Alpine component
  swap/OOB/teleport and loss/recovery passed. Existing dispatcher and cancellation
  fencing checks passed 14 each. Two unchanged broader regression files retain
  15 fixture/obsolete-expectation failures; these are documented, not green.
  Coordinator accepts the stated isolated presentation/writer/browser contract;
  broader DB/runtime/manual application acceptance was not run or certified.
  P8-T22 was later dropped/absorbed by P5-T14 stage E; its historical
  blocked state and evidence remain unchanged.
