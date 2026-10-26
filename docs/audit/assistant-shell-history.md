# Assistant shell implementation notes

Historical design/evidence; not current task ownership, migration authorization
or proof at a newer revision. Current contracts are routed by [DESIGN](../DESIGN.md).

Scope: original shell package A in [the active notebook](../../ROADMAP.md), followed by
merged B/C/D boundaries below. This is guidance, not another queue or whole-gate
acceptance. Mixed home results/upcoming schedules remain open. Owner-local bounded
home and A/B acceptance is recorded separately in the delivered index.
No migration, SPA, new dependency or permission model is introduced.

## Current integrated boundary

Audited dev `c62f948`: A #188 (merge `2c017f3`), B #189 (`42d0432`),
C #190 (`734e479`), D #191 (`ae77f55`). B replaces the original A-only unread
projection with one permitted event/connection/task area; C adds browser-local
optional display/hint policy, not durable state. D separates personal/workspace
settings, owned/granted context, guest-web-chat denial and global-admin entry.
See [notifications](../NOTIFICATIONS.md#current-web-bell-and-display-policy),
[settings/access](../TENANCY.md#web-settings-and-ownedgranted-workspace-context)
and [delivered evidence](../IMPLEMENTATION_STATUS.md). Subsequent owner-local
home/A/B checks do not certify all C/D, accessibility or inherited runtime gaps.
The A page inventory and source-pinned verification below are historical evidence;
later patches must not relabel those tests as combined-tree acceptance.

## Page inventory

Every direct `web/base.html` child has a `page_heading`; base owns the sole h1,
workspace `page_context`, `page_actions` target and optional `right_panel` block.
Browser titles are retained. Long headings wrap rather than widen the layout.

| Templates | Navbar heading / compact actions | Content retained |
| --- | --- | --- |
| dashboard | Overview / authorized chat icon | Operator data-workspace filter, bounded overview and setup step |
| sources, tasks | Section / guarded create dialog trigger | Workspace filters, source connection banners, concise setup guidance, tables, dialogs |
| source_detail | Source name or missing / no page actions | Breadcrumb, source access/readiness, source-wide aggregates, permitted related tasks with a source-prefilled creation link, and an inline collection-settings form for editors |
| task_detail | Task name or missing / edit, run now, toggle, delete under original guards | Task/source/platform scope, task-associated run statistics and progress, admin diagnostics, retry, editor dialog using the canonical edit URL |
| task_edit | Task name, synchronized with editing / back to task | One form reused directly or within the task-detail dialog, source selector, submitter overrides, confirmations; save returns to task detail |
| scenarios | Scenarios / guarded create link | Methodology description and list; task counts only for task viewers |
| scenario_detail, scenario_wizard | Scenario name or new / guarded save with original form ID | Methodology wizard; permitted task usage and prefilled task creation |
| analytics | Analytics | Period/workspace filters, metrics and analysis projections |
| analytics_detail | Analysis title / provenance; related task link | Content-first summary, known metrics, inline retry/resume and shared progress, collapsed provenance/chain history; no source or queue detour |
| analytics_group | Group value | Group breadcrumbs, filters and result list |
| analytics_chains | Topic chains / retrospective context | Breadcrumb, sort/filter controls and chain list |
| analytics_chain_detail | Chain name / guarded delete form | Breadcrumbs, period/count and evolution |
| digests | Digests | Description, workspace filter, current digest actions and results |
| jobs | Jobs / guarded clear form | Workspace/status filters, queue and per-job actions |
| chat | Chat / existing clear-history trigger | Access explanation, conversation, composer, confirmation flow |
| settings | Settings | Existing tab compatibility and member management; invite entry opens shared dialog |
| onboarding | Getting started | Existing permission-gated onboarding forms and states |
| section_denied | Unavailable section | Role explanation and permitted alternatives; HTTP 403 unchanged |
| not_found | Not found | Existing missing-record explanation and navigation |

Login, registration and join-by-code use `auth_base.html`, not the application
shell. Their authentication/CSRF/redirect contracts and the admin theme are not
changed. A shell rendered without membership offers account/create/join/logout,
not workspace data, notification fetch or invitation issuance.

Compact interactive actions use Alpine teleport into `#page-actions` to preserve
existing Alpine state, original Jinja guards, form ownership and delegated
confirmation handlers. Filters and multi-control toolbars remain in content.
This is node relocation, not copied forms or a new client router.

## State and authorization

- NavItem carries icon data; unknown icons render a circle. Settings/Admin are
  account utilities, not daily-rail entries. Desktop collapse uses local storage
  with a pre-paint read and unavailable-storage fallback; mobile gets the same
  permitted sections in a native modal drawer.
- Account popovers support mouse hover, click/touch, Tab/Enter/Space/ArrowDown,
  Escape, focus/outside dismissal and a persistent avatar-initials trigger.
- Theme choices use unchanged AppTheme handlers/key, OS changes and cross-tab
  synchronization. No preference implies a permission.
- The optional context dialog and launcher exist only when a page supplies
  non-whitespace authorized content. It starts closed; no production page is
  forced to populate it. Test the slot with a permitted synthetic fixture.
- The notification counter is the length of the same bounded unread list rendered
  by the dialog, not an independently queried total. A successful read refreshes
  that list to refill a truncated page. Failed GET/POST retains known unread rows
  and reports unknown/error, not a healthy empty state. Empty snapshots hide the
  floating trigger; an open last-item dialog keeps its empty/close state.
  POST reads now explicitly verify CSRF. Existing tenant-scoped managers and
  workspace-shared `is_read` remain authoritative. That was the A-only behavior.
  Merged B now includes caller-only connection and permitted failed-task signals;
  see the current notification contract above.
- GET `/app/settings/invites` checks `can_manage_workspace(active_target)` before
  any invite query or one-time code pop; it returns a no-store partial. No menu
  opening issues or sends an invitation. Existing POST issue/revoke guards,
  choices and manager constraints remain; redirects reopen the lazy dialog.
  Code/link/optional server-generated QR, expiry, role and outstanding revocation
  remain there. The one-time result is additionally bound to the issuing tenant.

## Verification and handoff

Remote no-DB checks are now authorized by the repository skill's explicit
owner-approved exception (policy commit `b65654393e11b014c046eb275ef0312905b5b83d`).
The initial Draft had syntax/readback evidence only. Subsequent sandbox execution
found and fixed a missing filter in the new fixture, a real account hover/click
race and a fixed-bell/content overlap missed by the initial geometry assertion.
The keyboard-outline assertion now enters keyboard modality before checking
`:focus-visible`; no assertion was removed to obtain PASS.

Sandbox environment: isolated `/data/smai-shell-venv`, source worktree
`/data/smai-shell-verify`; Python 3.13.14, pytest 9.1.1, pytest-asyncio 1.4.0,
Jinja2 3.1.6, Starlette 1.8.0, FastAPI 0.143.0, Playwright 1.63.0,
Chromium 153.0.8010.12. Dependencies installed from `pyproject.toml` dev/runtime
requirements with the existing Jinja template dependency; not the owner's .venv.
No `.env`, real secret, DB connection or application/runtime startup was used.

Executed sequentially with mocked boundaries, no root conftest/bootstrap:

```sh
/data/smai-shell-venv/bin/python -m pytest --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header tests/unit_web/test_assistant_shell.py tests/unit_web/test_dashboard_overview.py tests/unit_web/test_template_autoescape.py
node tests/web_shell_state.cjs
/data/smai-shell-venv/bin/python tests/home_overview_fixtures.py --out /data/shell-fixtures
CHROMIUM_BIN=$(command -v chromium) node tests/web_home_layout.cjs /data/shell-fixtures /data/shell-vendor
```

Result: 68 isolated pytest checks passed (0 failed/skipped); notification state
checks passed; 48 home-fixture cases (four states, 375/390/768/1440px,
light/dark/system) and two interaction scenarios passed, exit 0. The browser
harness serves static/vendor files locally and intercepts/aborts every HTTP
request; application endpoints are synthetic. It now checks content viewport
separation from the bell, last-read empty/close state and POST CSRF wiring,
mobile drawer/menu, desktop hover/click/ArrowDown/Escape, collapse, theme OS
following, keyboard focus outline and 44px chat target. Four representative
screenshots were visually reviewed after the footer fix; this is not an
individual visual review of all 48 screenshots.

The content region now scrolls inside the stable shell; a nonempty/open bell
reserves a real footer band, including mobile safe area, rather than overlaying
intermediate content on long documents. Revalidate every migrated page and chat
composer in the real app; home fixtures do not cover their individual layouts.
Source HEAD and final published follow-up SHA/readback are pinned in PR #188's
current handoff. Also executed static syntax/diff checks; formatted the new shell
unit-test file with Black/isort. Prepared integration tests have not been run.

Remaining blocker: real UI/ASGI/DB, full role/tenant-isolation acceptance, invite
issue/revoke/seat/last-owner flows, all migrated pages, chat composer, measured
contrast and PR185 deferred acceptance. No mock PASS closes these requirements.
Local coordinator must preserve the primary checkout's dirty files, inspect
worktrees and fresh ownership, and pin the PR implementation SHA. Use Kilo
`test-orchestrator` -> `test-runner` -> `pare-test`, serialized under R8/R10 for
real/shared-DB or environment-dependent checks. An unsafe target or collection
restriction remains BLOCKED: no shell fallback, schema repair or bootstrap.
No provider calls, real send, migration or deploy occurred.

Suggested isolated scope (no root conftest):
`tests/unit_web/test_assistant_shell.py`,
`tests/unit_web/test_dashboard_overview.py`,
`tests/unit_web/test_template_autoescape.py`.
Integration scopes to review/collect through the approved runner separately:
`tests/test_web_notifications.py`, `tests/test_workspace_invites.py`,
`tests/test_workspace_invite_list.py`, `tests/test_web_onboarding.py`,
`tests/test_web_permissions.py`, `tests/test_web_settings.py`,
`tests/test_web_sources.py`, `tests/test_web_scenario_wizard.py`,
`tests/test_web_task_edit.py`,
`tests/test_web_chat.py`, `tests/test_web_digests_jobs.py`, `tests/test_web_analytics.py`.
Tests touching the shared target must stay sequential; absence of a safe existing
target is a blocker, not permission to create/reset/migrate one.

Offline reproduction commands (sandbox-executed subset only):
- `node tests/web_shell_state.cjs` (mocked notification failures/CSRF/refill).
- Project `.venv/bin/python tests/home_overview_fixtures.py --out <fixture-dir>`
  then `node tests/web_home_layout.cjs <fixture-dir> <offline-vendor-dir>`.
  Use locally supplied vendor files matching base; all HTTP is intercepted.
  Harness covers home states, 375/390/768/1440px, light/dark/system, collapse,
  mobile menu, notification modal, Escape, theme and focus; inspect screenshots
  individually. This is not full-page/real-app authorization evidence.

Required real-app acceptance at the pinned SHA: all inventory rows, Viewer,
Manager, owner and operator; denied/no-membership/auth states; workspace switch,
revocation, forbidden direct POSTs and no unauthorized lazy invite queries;
code shown once, role/expiry/QR/revoke and unchanged seats/last-owner rules;
hover/tap/keyboard/Escape/outside, focus trap/return and scroll restoration;
confirmation submitter/CSRF semantics; long strings, loading/empty/failure states,
notification refill and read failure; all viewport/theme combinations, 44px
shell targets, contrast, reduced motion, no overflow or composer/control overlap;
optional right-panel authorized fixture. Preserve PR185's separately deferred
ASGI/DB checks and the inherited add-task source_ids follow-up.

### All-page/dialog follow-up (remote no-DB)

Continued A from PR HEAD `199cff9bb708c08de683267aae70599fdb045043`
in a fresh isolated worktree `/data/smai-shell-a-completion`, preserving existing
worktrees/dirty files. The owner's Mac checkout is inaccessible and untouched.
Fresh dev reservation was `7e26f552393ac02d91b5f9a5fc084d332fa7623a`.

- Shell dialog launchers expose controlled target/expanded state. Shell drawer,
  invitation and optional context dialogs explicitly wrap Tab/Shift+Tab.
- Closing invitations restores the visible account trigger or mobile drawer
  opener, not a hidden invite entry. Every reopening fetches the protected
  no-store partial again; a failed/revoked response replaces previous code/list
  with an error and retry. No invitation is issued by these GETs.
- The task-detail edit modal now remains inside the same Alpine state owner as
  its navbar trigger. A browser interaction caught the out-of-scope modal; moving
  one closing wrapper fixes the trigger without changing forms or permission gates.
- Added `tests/shell_page_fixtures.py` and `tests/web_shell_pages.cjs`: strict Jinja
  fixtures for all 21 direct base children, six read-only representative variants,
  and one authorized context slot. Real section values are used, including chat.
  Empty collections intentionally bound the fixture scope; this is not complete
  populated-page or role/tenant acceptance. No permissive Undefined fallback,
  app import/runtime, real HTTP, DB, secret, provider or send is used.

Executed on the exact working source subsequently published/byte-verified:
72 isolated pytest checks passed (0 failed/skipped), mocked notification state
checks passed, and the all-page offline browser run passed 336 viewport/theme
cases plus 11 interaction scenarios, exit 0. The matrix covers 375/390/768/1440px
and light/dark/system, document AND main-region horizontal overflow, single h1,
notification/content separation, runtime errors and chat composer geometry.
Interactions cover desktop/mobile lazy invites, issued/revoke form CSRF/choices,
403 reopening, focus trap/return, context panel, four relocated dialog actions
the editor's live navbar heading and native form ownership for two scenario Save buttons.
The final sequential run also repeated home regression: 48 viewport/theme cases
plus 2 interaction scenarios passed, exit 0. Static syntax/diff checks passed. Fixture expectations for CSRF
and the task-create label were corrected. The read-only source fixture also now
sets the server-derived can_delete predicate false, matching sources.py rather
than inheriting the editable fixture flag. Assertions were not removed.
Four representative 390px/light snapshots (chat, onboarding, source detail and
task editor) were visually inspected, not every matrix image or measured contrast.

Reproduce with the isolated project test environment and locally supplied vendor
files matching base plus `marked@4.3.0`; this is a test-only offline asset, not a
new application dependency:
- `python tests/shell_page_fixtures.py --out <fixtures>`
- `node tests/web_shell_pages.cjs <fixtures> <offline-vendor-dir>`
- `node tests/web_shell_pages.cjs <fixtures> <offline-vendor-dir> --contracts-only`
  repeats the six read-only variants/context panel and all interaction contracts,
  including native form ownership for relocated scenario Save buttons.
Set `CHROMIUM_BIN` to sandbox Chromium and resolve the preinstalled Playwright
module. Every browser request is fulfilled/aborted by the offline harness.

Still required: real Kilo R8/R10 ASGI/DB permission/revocation/workspace checks,
actual invite issuance/revocation/one-time QR/seat/last-owner flows, full populated
pages/roles, chat history/send/attachment flows, confirmation submissions,
individual full visual matrix, contrast/reduced-motion and PR185 deferred checks.
The inherited add-task source_ids defect remains open: the empty-source fixture
must not be reported as source selection acceptance. Read-only scenario-wizard
runtime is not covered by the six read-only variants. Packages B/C/D, remaining
PRD-08.1 and feature integration remain outside this package; keep PR Draft.

### Owner-requested layout/menu polish

Continued A from `06d0c56cb342e79db78e446e00084ce23f6dedaf` in isolated
`/data/smai-shell-polish`. The existing dirty source checkouts were preserved;
owner-local status is still inaccessible. The newly inspected dev baseline was
`4a8d84182b58f22ffac67c80a13ec1072546b4ef`; reservation is recorded on the board.

The double-chevron collapse button is separate from the account popover, at its
right edge, and reverses with the existing locally saved rail state. AppTheme is
now a reusable footer partial, at bottom left on desktop/in the mobile drawer;
the mobile navigation opener remains in the navbar. An anonymous shell keeps a
bottom-left theme control. The navbar is transparent with its existing separator.

The account popover now separates Spaces / Current workspace / Account, explains
switch targets, shows the active workspace once, and names the platform admin
new-tab destination. Role captions are derived from existing member_roles rather
than treating raw SUPERUSER as a platform identity. This adds a display field to
the already-loaded memberships, not a query or another permission predicate.
Selection/create/logout retain POST+CSRF; invite management keeps the original gate.
Join-by-code is in Settings Team for existing members, independent of invite rights.
The no-workspace join fallback is retained to preserve first-time entry.

Home uses its available width and a two-column results/tasks grid at viewport
1200px; smaller screens stack. Only permitted tasks produce the second column;
results use the whole row when tasks are forbidden. Existing query/data scopes,
result limits and recent-task semantics are unchanged. The canonical board records
these explicit owner overrides to the earlier navbar/single-column placement.

New isolated regressions cover grouped multi-workspace forms, Team join/invite
parity, canonical display-role captions, footer/control placement and unique
popover targets. The home harness also checks available width, transparent navbar,
main/document overflow, and both sides of 1199/1200px. The all-page harness adds
transparent navbar and mocked POST/CSRF workspace switching, never a real switch.
Real role/tenant/ASGI/DB acceptance and the previously listed limitations remain
open; no runtime/application startup, schema operation or provider/send is used.

Final sequential isolated run on the published source candidate passed:
- 80 pytest checks across unit_web/test_assistant_shell.py,
  unit_web/test_dashboard_overview.py and unit_web/test_template_autoescape.py;
  root conftest/cache disabled, 0 failures/skips, exit 0 (4.92s).
- Mocked notification state/error/CSRF contracts passed.
- 72 home viewport/state/theme cases plus 2 interactions passed, including
  1199/1200px breakpoint boundaries.
- 336 all-page viewport/theme cases plus 14 interaction contracts passed,
  including desktop/mobile workspace POST+CSRF and compact-rail persistence.
- Both changed browser scripts passed node --check; git diff --check passed.

Reproduction uses the existing commands above and
`pytest --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header`
with the three named unit files, followed sequentially by web_shell_state.cjs,
web_home_layout.cjs and web_shell_pages.cjs. Environment: Python 3.13.14,
pytest 9.1.1, Jinja 3.1.6, Playwright 1.63.0, Chromium 153.0.8010.12.
Browser assets were local and all requests were fulfilled/aborted by the harness.
Selector corrections scope account/footer controls after relocation; assertions
were not removed. This is isolated fixture coverage, not full-suite acceptance.

Representative final screenshots were reviewed at 1440/1200/1199px light,
390px dark with long text, desktop/mobile workspace menus, compact rail and the
footer theme popover. The mobile close button is right-aligned; the account menu
has bounded scrolling so navigation/theme remain reachable. The connection
settings glyph is now a gear, visually distinct from the relocated theme control.
This review is not an individual full visual matrix or measured contrast check.
All real-app/Kilo R8/R10 and previously recorded acceptance gaps remain open.

### Owner-requested workspace utility placement

Continued A from `22b55fd963210a0d34b983de02a390b244987b11` in the isolated
`/data/social-media-ai-shell-followup` checkout, using the owner's menu reference.
The Settings link and Invite member button are now a bordered paired row at the
start of the account popover, above workspace switching. Desktop keeps leading
icons; the narrow-screen drawer puts icons above labels so Russian words are not
split mid-word. The account popover is bounded to the viewport; a member without
invite capability gets Settings alone. Active workspace is still shown once.

This changes only shared template placement and scoped styles, not server rights,
queries, invite issuance, membership state or notification behavior. Existing
`can_manage_workspace(tenant.id)`, lazy invite authorization/refetch, CSRF and
POST semantics remain. No new dependency, migration, application startup, real
DB/provider/send or deployment. The owner's Mac checkouts remain inaccessible and
untouched; their git status/worktree state is unknown. No dev push or merge.

Isolated checks use `/data/shell-menu-venv`, separate from the owner's .venv:
Python 3.13.14, pytest 9.1.1, pytest-asyncio 1.4.0, Jinja 3.1.6, Starlette 1.8.0;
Playwright 1.63.0 and Chromium 153.0.8010.12. The selected imports/fixtures were
inspected first: standard library, Jinja, pytest and Starlette Request, plus AST
extraction/stdlib-only member_roles. Root conftest (which bootstraps shared DB)
and cache are disabled. No application module/runtime is imported or started.

Commands (sandbox only):

```sh
/data/shell-menu-venv/bin/python -m pytest --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header tests/unit_web/test_assistant_shell.py tests/unit_web/test_dashboard_overview.py tests/unit_web/test_template_autoescape.py
/data/shell-menu-venv/bin/python tests/shell_page_fixtures.py --out /data/shell-menu-fixtures
CHROMIUM_BIN=$(command -v chromium) node tests/web_shell_pages.cjs /data/shell-menu-fixtures /data/shell-menu-vendor
node tests/web_shell_state.cjs
node --check tests/web_shell_pages.cjs
git diff --check
```

Local vendor assets match the fixture URLs: Tailwind CDN, Alpine 3.14.1, HTMX
1.9.12 and marked 4.3.0. Every browser request is fulfilled/aborted locally. The
first browser attempt stopped because local marked.js was missing; it was a
harness setup failure, not acceptance evidence. After supplying that asset the
matrix passed. Screenshot review then found awkward mid-word mobile labels;
the final mobile styles and stronger no-mid-word assertions were checked again.

Final results (all exit 0): **83 passed, 0 failed/skipped** in the isolated
pytest subset; **336 all-page viewport/theme cases + 38 interactions**, with no
browser failures; mocked notification state/error/CSRF contracts passed.
Black/isort checks for the changed Python test, node syntax and diff checks passed.
Final desktop 1440px and mobile 375px dark screenshots were visually reviewed;
this is not an individual review of every viewport/theme image.
New tests cover top-row placement/order in both menus, no-workspace fallback,
permission-conditioned invite visibility, identical row geometry, 44px targets,
word-boundary label wrapping, keyboard order and absence of eager invite fetches
at 375/390/768/1440px in light/dark/system. Existing mocked invitation
open/refetch/403/focus return and workspace POST/CSRF contracts are retained.

Owner acceptance of the preceding implementation is attributed only to its
original SHA, not automatically to this follow-up. This is mocked/fixture evidence,
not real role/revocation/workspace/ASGI/DB or full visual/contrast/reduced-motion
acceptance. All previously deferred A/PR185, invite/QR/seats/last-owner, real chat,
source_ids and scenario-wizard limitations remain open. B/C/D are untouched;
PR #188 remains Draft/unmerged, with its existing integration conflict unresolved.


### Owner closeout of the final A implementation

After the final utility-row follow-up was delivered, the owner requested closing
A and proceeding to B. Record A implementation at
`2edabe22128020055d1e2ce765f756dfb6dbbf63` as owner-accepted and closed. This
closeout is not a report that deferred real-environment tests ran and does not
automatically waive required integration evidence. The 83 isolated pytest and
336 offline cases plus 38 interactions remain attributed to that implementation;
this closeout changes documentation only, with no new test run or code change.

Fresh dev remains `cfc669636843c991e6abc64a008daa3cc20ef34f`; shell code is not
integrated there. PR #188 is open/Draft. Its API metadata still reports the
preceding head and `dirty` mergeability, whereas direct branch/get_commit/fetch
checks verify the new implementation head and all six published files. Treat
PR-head metadata as unresolved/stale, not evidence of a lost write; do not
republish code or merge from that conflicting snapshot.

B is the next owner-selected bounded package, not C/D or remaining home work.
Its implementation must include the existing A shell without duplicating it.
Under the current branch-from-dev remote rule, choose the dependency path before
starting: satisfy A integration requirements, or obtain an explicit exception for
a separate stacked B branch from the accepted A implementation. Until then, B
implementation is blocked on its base, not complete or in progress. All prior
deferred checks and no-DB/no-runtime/no-live-action restrictions remain open.

## P8-T29 design constraint

The [universal task contract](../AGENT_TASKS.md#explicit-task-plans) preserves this shell and independent sources, scenarios, tasks/schedules and digests pages. Chat is supplementary. Add needed settings using existing controls/styles; do not replace navigation or introduce a new visual design.
