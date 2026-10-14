# Assistant shell implementation notes

Scope: shell package A in [the canonical board](README.md). This is implementation
and validation guidance, not another queue or an acceptance claim. Packages B/C/D,
mixed home results/upcoming schedules and PR185 real-app acceptance remain open.
No migration, SPA, new dependency or permission model is introduced.

## Page inventory

Every direct `web/base.html` child has a `page_heading`; base owns the sole h1,
workspace `page_context`, `page_actions` target and optional `right_panel` block.
Browser titles are retained. Long headings wrap rather than widen the layout.

| Templates | Navbar heading / compact actions | Content retained |
| --- | --- | --- |
| dashboard | Overview / authorized chat icon | Operator data-workspace filter, bounded overview and setup step |
| sources, tasks | Section / guarded create dialog trigger | Workspace filters, source connection banners, tables, dialogs |
| source_detail | Source name or missing / analyze, collect, edit, toggle, delete under original guards | Breadcrumb, source metadata, forms, activity; explicitly closed header wrapper |
| task_detail | Task name or missing / edit, toggle, delete under original guards | Queue-aware breadcrumb, task/workspace metadata, editor, outcomes |
| task_edit | Task name, synchronized with editing / back link | Existing form, source selector, submitter overrides, confirmations |
| scenarios | Scenarios / guarded create link | Description and list |
| scenario_detail, scenario_wizard | Scenario name or new / guarded save with original form ID | Breadcrumbs, description, wizard, form validation |
| analytics | Analytics | Period/workspace filters, metrics and analysis projections |
| analytics_detail | Analysis title / source context | Analysis breadcrumbs, source/chain links, saved result |
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
  workspace-shared `is_read` remain authoritative. No personal/task signal feed
  is implemented. The existing caller-only connection indicator stays separate.
- GET `/app/settings/invites` checks `can_manage_workspace(active_target)` before
  any invite query or one-time code pop; it returns a no-store partial. No menu
  opening issues or sends an invitation. Existing POST issue/revoke guards,
  choices and manager constraints remain; redirects reopen the lazy dialog.
  Code/link/optional server-generated QR, expiry, role and outstanding revocation
  remain there. The one-time result is additionally bound to the issuing tenant.

## Verification and handoff

Executed remotely: Git status/HEAD inspection on the isolated checkout, targeted
source/diff review, `git diff --check`, Python AST syntax only (no application
imports), `node --check` for changed JS/CJS. These do not prove template rendering,
Alpine behavior, permissions, layout, contrast or DB integration.

Not executed: pytest, Jinja rendering, browser fixtures, real UI/ASGI/DB tests.
The remote checkout has no project `.venv`, Kilo CLI or owner-local filesystem.
No direct sandbox pytest authorization is assumed and no substitute environment
is created. No provider call, real send, schema bootstrap/reset/migration or deploy.

Local owner/coordinator must preserve the primary checkout's dirty files, inspect
worktrees and fresh ownership, and pin the PR implementation SHA before validation.
Use Kilo `test-orchestrator` -> `test-runner` -> `pare-test`, serialized under R8/R10.
If the configured runner cannot collect an integration scope without conftest,
report BLOCKED and resolve the existing collection contract; never shell-fallback,
weaken restrictions, repair schemas or bootstrap another target.

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

Prepared offline checks, not executed here:
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
