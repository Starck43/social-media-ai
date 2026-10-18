# Admin Panel Reference

The SQLAdmin console is a **global platform/operator interface**, not the
workspace web application and not an editor for every ORM table.

- Base path: `/admin` (local development: `http://localhost:8000/admin`).
- Registration: [setup.py](../app/admin/setup.py); views: [views.py](../app/admin/views.py).
- Enabled by `ADMIN_ENABLED` (default true) in [app.main](../app/main.py).
- Source baseline: `dev` `edbe0fc02e6ea2d78302129e51febfa0e87464ab`, reviewed
  on 2026-10-15; reservation commit `916d2e18eeafdd86d4857587638b73a24bcd0891`
  changes only the ownership board.

This is a source reference, not proof of deployment or whole-console acceptance.
The baseline above records the original documentation-only audit. The LLM model
section was subsequently revised with the shared short-check/admin UI change
and then combined with the personal-billing lane for code review only:
its evidence is isolated source/mocked-handler tests, real SQLAdmin form
scaffolding against canonical ORM metadata (no database), and offline template
browser fixtures. Evidence from either source lane is not acceptance of the combined candidate.
No real application startup, database inspection, migration,
provider call or messenger send was performed remotely. Other legacy page
mismatches remain listed below. See [MODELS](MODELS.md) for ORM definitions and
[IMPLEMENTATION_STATUS](IMPLEMENTATION_STATUS.md) for attributed prior acceptance.

## Authentication and platform authority

[Auth backend](../app/admin/auth.py),
[authorization backend](../app/admin/authorization.py),
[User](../app/models/identity/user.py),
[resource catalog](../app/core/access_catalog.py).

`GET /admin/login` displays the SQLAdmin login form. Login accepts username or
email and password, validates its CSRF token, and requires an active account
whose **platform** role is `operator` or `admin` (`User.has_admin_access()`).
Workspace owner/admin membership does not confer console access. SQLAdmin logout
clears the browser session.

Authenticated requests resolve the JWT subject to a fresh active User with
platform role/permissions. The backend does not trust a stale session `user_id`
as a second identity source. A platform admin receives wildcard console grants;
an operator receives explicit model/action grants. There is no current
`User.is_superuser` column.

Permissions come from `role_permission` → `permissions.model_type_id` /
`action_type`, with stable `ModelType.resource_key` registered in
`app.core.access_catalog`. `Permission.codename` is derived as
`resource_key.action`, not stored. `User.model_permissions()` resolves the resource
and the operator's grants; `User.has_perm_for()` additionally validates the
registered resource/action pair. Workspace surfaces use separate membership-role
presets and scope checks; do not reuse console grants as workspace membership.

### SQLAdmin action mapping

| Console operation | Required `ActionType` |
| --- | --- |
| list, details | VIEW |
| create, import (if offered by SQLAdmin) | CREATE |
| edit | UPDATE |
| delete | DELETE |
| export | EXPORT |
| custom action | Explicit `BaseAdmin.action_permissions` entry; otherwise UPDATE |

For operators, any granted non-VIEW action also implies list/details so its form
can be reached. No grants means no model access. View-level `can_*` flags still
disable operations even for platform admins. Custom action declarations are
checked against the action slugs; unknown declaration keys are logged when the
lazy view catalog is built. Menu visibility alone is not authorization.

Additional restrictions:

- `TenantAdmin.is_accessible()` requires a platform admin, not merely a tenant
  model grant or ownership of one workspace.
- Platform role and permission identities remain fixed: no create/delete or code,
  resource/action, or user-assignment editing. A platform admin may edit the
  permission selection of every role (`user`, `admin`, `operator`) through `RoleManager.set_permissions`,
  and may edit a permission's display name. Operators cannot edit either catalog,
  even with explicit grants. Writes recheck the active platform admin and CSRF.
  Stored grants do not change fixed role authority: `admin` retains wildcard
  console access, and `user` cannot enter the console. The role API/service keeps
  its separate operator-only update contract; the SQLAdmin editor is admin-only.
- User forms exclude `role` / `role_id`; new users receive the ordinary platform
  `user` role. Creating an operator/admin or changing operator grants is not an
  ordinary SQLAdmin user-form operation. `UserManager.set_platform_role()` also
  rejects every self-assignment (including selecting `admin` again) before any
  write; another active platform admin must change the account's role. Generic
  SQLAdmin updates reject role fields rather than bypassing this service guard.
- External FastAPI dashboards use `require_admin_perm()` explicitly. Password
  administration has its additional self/cross-account checks below.

The main app's `PlatformScopeMiddleware` supplies explicit tenant bypass for the
operator console/infrastructure, while `/app` and `/api` have their own scope
middleware. Admin listings are not filtered by the web UI's selected workspace.
Actions that execute a task re-enter the selected task's owning workspace.

## Role permission picker

The role editor progressively enhances the existing `permissions` multiselect into
**Available / Chosen** lists, with independent search and counts. Double-click an
item to transfer it in either direction. On touch devices, tap to mark one or more
items, then use **Add / Remove** (44px minimum targets). Keyboard users can use
arrows/Home/End, Space to mark, Enter to transfer, Ctrl/Cmd+A to mark all filtered
items, and Escape to clear marks. Nothing is persisted until the form is saved.

The editor uses the viewport height: only the lists scroll, while transfer controls
and ordinary shared SQLAdmin buttons remain outside them and visible. Circular
transfer arrows sit between the panes and show the number of marked matches.
The field label and smaller keyboard hint sit below the lists; access rules remain
fully visible above them. Rows flow
from the top without per-item borders or gaps (32px desktop, at least 44px with a
coarse pointer). The two panes stay side by side on narrow screens; resource
prefixes appear in group headings rather than repeated narrow-screen rows.
The mobile sidebar remains a separate section before the viewport-height editor.

A shared group filter selects a domain (the first resource-key component), and
resource headings group related actions in each pane. Independent case-insensitive
search accepts words from both the code and display name; group and search filters
combine without clearing stored selections. Labels come from the canonical
Permission manager with model types prefetched. Keyboard select-all/transfer acts
only on visible matches. Group changes clear transient marks, never selected grants.

The native select remains the sole submitted value source, including filtered-out
choices, and remains usable if JavaScript is disabled. This needs no new dependency
or schema migration. Editing stored grants on `admin` does not remove its wildcard
access and is distinct from changing one's account role.

Offline validation: `tests/permission_transfer_fixture.py` renders actual SQLAdmin
forms with up to 206 synthetic permissions and explicit storage doubles;
`tests/permission_transfer_layout.py` exercises desktop/mobile widths, both themes,
empty/full/small selections, group/name filters, double-click, keyboard, touch,
native FormData/reset, legacy checkbox CSS collisions, scroll containment,
viewport/footer bounds and the no-JavaScript fallback. These are not real database
persistence or owner-account acceptance tests.

## Navigation and actor identity

The shared sidebar displays the verified request user and platform role, never a
username or privilege flag from session display values. Web account menus label
the platform role separately from workspace membership; platform authority does
not grant membership rights in `/app`.

Custom FastAPI console routes load the same request-bound SQLAdmin grants before
rendering the shared layout. This keeps model navigation present after model-test
POST responses without changing the authorization decision. The sidebar uses a
compact, scrollable menu and native mobile navigation disclosure.

Catalog editing is covered remotely by actual-source, real SQLAdmin form/route
checks with explicit identity and persistence doubles, plus offline Chromium
layouts. These checks are not PostgreSQL persistence or owner-account acceptance.
Real authenticated admin/operator/revoked-account checks and grant/name persistence
remain pending owner-local verification. The owner explicitly authorized integration
ahead of that verification and will report any findings. No migration is introduced.

## Registered model pages

Exactly **16 model views** are registered by `setup_admin()`. Paths shown are the
SQLAdmin list routes; available menu entries and operations depend on grants.
Unspecified CRUD/export flags inherit SQLAdmin defaults, not a promise of safe
business-workflow execution.

| View | Model | List path | Explicit restriction / purpose |
| --- | --- | --- | --- |
| `UserAdmin` | `User` | `/admin/user/list` | Account forms exclude platform-role assignment; password action is separate |
| `RoleAdmin` | `PlatformRole` | `/admin/platform-role/list` | Platform admin may edit every existing role's grants; catalog identities remain fixed |
| `PermissionAdmin` | `Permission` | `/admin/permission/list` | Fixed catalog; platform admin may edit display name only |
| `PlatformAdmin` | `Platform` | `/admin/platform/list` | Platform configuration; no sync action |
| `SourceAdmin` | `Source` | `/admin/source/list` | Sources and synchronous collection check |
| `CollectedItemAdmin` | `CollectedItem` | `/admin/collected-item/list` | Raw staged content |
| `AgentScenarioAdmin` | `AgentScenario` | `/admin/agent-scenario/list` | Analysis methodology, prompt preview and active flag |
| `AgentTaskAdmin` | `AgentTask` | `/admin/agent-task/list` | Scheduled tasks and inline run action |
| `BotActionAdmin` | `BotAction` | `/admin/bot-action/list` | Create disabled; edit form contains only `status` |
| `AIAnalyticsAdmin` | `AIAnalytics` | `/admin/ai-analytics/list` | Create disabled; stored analysis view |
| `JobAdmin` | `Job` | `/admin/job/list` | Read-only queue inspection: create/edit/delete/export disabled |
| `NotificationAdmin` | `Notification` | `/admin/notification/list` | Shared workspace read state; guarded hidden send action |
| `LLMProviderAdmin` | `LLMProvider` | `/admin/llm-provider/list` | Global provider/key configuration and connection test |
| `LLMModelAdmin` | `LLMModel` | `/admin/llm-model/list` | Global model configuration and short/mock/real checks |
| `TenantAdmin` | `Tenant` | `/admin/tenant/list` | Global workspace settings; platform-admin-only visibility |
| `UserCredentialAdmin` | `UserCredential` | `/admin/user-credential/list` | Personal credential vault rows, not workspace-owned credentials |

`Role` is a Python compatibility alias; the mapped class is `PlatformRole` and
its default SQLAdmin identity is `platform-role`, not `role`.

No model view is registered for `ModelType`, `WorkspaceRole`, membership/invite/
channel rows, `DigestRun`, agent sessions/messages/memory/feedback, or the five
LLM attempt/receipt/outcome/budget tables. Associations are not separate pages.
The existence of a model or permission resource never creates an admin view.

## Custom actions and side effects

These are the **10 declared actions** in `views.py`. Default UI placement and
required model right are shown separately from implementation behavior.

| View / action slug | Right | UI placement | Current behavior |
| --- | --- | --- | --- |
| `UserAdmin` / `change-password` | UPDATE | List + detail | Redirects to the selected account's password form; the destination applies stricter cross-account checks |
| `SourceAdmin` / `check-source` | UPDATE | List + detail | Calls the social client synchronously for the first selected source and renders paginated posts/stats; does not enqueue a collection job |
| `AgentScenarioAdmin` / `view-prompts` | VIEW | Detail only | Shared ScenarioBuilder/PromptBuilder preview for media and unified prompts, including scope; no LLM call |
| `AgentScenarioAdmin` / `toggle-active` | UPDATE | List + detail | Toggles selected scenarios' `is_active` |
| `AgentTaskAdmin` / `run-now` | UPDATE | List + detail | Admits/enqueues the task run, claims that exact Job and executes inline in the request process; not merely a queue submission |
| `AIAnalyticsAdmin` / `view-analysis` | VIEW | List + detail | Redirects to the stored analysis detail route |
| `NotificationAdmin` / `mark-read` | UPDATE | List + detail | Sets selected rows' shared `is_read`; not personal acknowledgement or issue resolution |
| `NotificationAdmin` / `send-to-messenger` | UPDATE | Hidden in list + detail | Endpoint remains registered; first selected notification requires explicit `recipient_id` for an active owned workspace binding |
| `LLMProviderAdmin` / `test-connection` | CONFIGURE | List + detail | Uses the first active model found for that provider; real `client.analyze()` call with a fixed short prompt and `max_tokens=10` |
| `LLMModelAdmin` / `test-model` | CONFIGURE | List + detail | Read-only GET opens the UI; POST checks fresh CONFIGURE/CSRF; explicit real POST is the paid action |

`PlatformAdmin` declares no custom action; there is no implemented or placeholder
sync button in the current class. `JobAdmin` has no run/cancel/retry button.
Changing a `BotAction.status` through its form is not evidence that full
actor-bound approval, replay safety or external execution is implemented.

### Run task now

[Dispatcher](../app/jobs/dispatcher.py), [enqueue helper](../app/jobs/enqueue.py).
`run_task_directly()` admits/enqueues inside `task.tenant_id`, claims the exact
due row and runs its handler inline; a competing claim raises rather than
starting a second handler. The Job remains the execution audit trail. The
shared trigger path updates task bookkeeping, including one-shot deactivation.
This action can invoke configured job behavior: do not use it as a harmless UI
probe. See [AGENT_TASKS](AGENT_TASKS.md) for queue/outcome boundaries.

Task create/edit exposes source multi-selection (`agent_task_sources`),
`agent_scenario`, schedule/job/payload and trigger/action controls. Scenario =
methodology; task = execution. Empty source selection and payload semantics are
runtime contracts, not permission grants or a new business-workflow model.

### LLM testing and prices

[Action implementation](../app/admin/actions.py),
[capped probe](../app/services/ai/model_probe.py).

- Opening `test-model` is read-only, even if a legacy URL carries `prompt` or
  `test_type=real`. Select exactly one model. No GET performs a model probe.
- The page has three explicit POST modes at `/admin/llm-model/test`. The route
  checks fresh platform `llmmodel.CONFIGURE` rights and a signed CSRF token
  before lookup/dispatch. The explicit real-mode POST is the administrator's
  paid action; no additional `confirm_real` checkbox or hidden consent field.
  Legacy posted checkbox values are ignored. GET remains read-only.
- **Short check** (`test_type=short`) needs no question. In the admin console it
  makes one authenticated `GET /models` through the configured OpenAI-compatible
  provider URL/headers, with a 20-second timeout and redirects disabled. No chat,
  generated answer, billing dispatch or paid fallback. The result shows HTTP status,
  elapsed time, model count and whether the selected model appears. A valid list
  confirms metadata API/key access, not generation permissions or answer quality.
  Unsupported metadata endpoints fail explicitly; never retry via chat. Non-OpenAI
  formats are explicitly unsupported. `/app/settings` and scenario checks retain
  their existing offline `probe_short()` behavior; this change is admin-only.
- **Mock** (`test_type=mock`) requires a question (up to 8000 characters); it returns
  a canned local response and simulated usage, not actual paid tokens. The page
  does not accept image uploads or claim multimodal validation.
- **Real request / «Реальный запрос»** (`test_type=real`) is available to an active
  platform administrator with CONFIGURE and valid CSRF in both accounting modes.
  The billing feature selects accounting; it is never a prerequisite to testing.
  In single-owner mode the existing client runs with normal model usage/health
  telemetry and cost estimate. No personal account, execution workspace, cutover
  or model-bound policy is required; posted payer/workspace identities are ignored.
  No personal reservation or settlement is fabricated, and the UI has no billing
  activation warning or workspace selector in that mode.
- When personal accounting is enabled, the administrator who initiated the test
  remains the trusted personal payer (never a submitted payer or workspace owner).
  An active execution workspace is required and the existing personal admission
  service enforces account, cutover, daily cap and reviewed model bound before
  transport. Missing/invalid workspace and admin refusals retain HTML 422/403
  results with no dispatch. This path is not bypassed or replaced with direct HTTP.
- Real results show a readable escaped text card, delivery state, actual provider
  HTTP status (or explicitly unconfirmed), stage, finish_reason, accounting/journal
  status and a safe error code. Ordinary mode is labelled normal model accounting,
  not a confirmed personal settlement. Response JSON is formatted Unicode text,
  expanded by default and copyable. Known secrets are redacted; bounded display
  copies are marked when truncated. Provider JSON/text survives HTTP errors,
  empty replies and post-response failures; no automatic retries.
- JSON copying uses an accessible top-right SVG button. A temporary checkmark
  indicates success, with a screen-reader announcement but no visible success
  paragraph. Clipboard denial keeps manual JSON selection and a visible fallback;
  without JS the inert copy button is hidden. Text status colors distinguish
  success (green), incomplete/uncertain (amber), errors (red) and neutral facts;
  labels remain readable without color. HTTP 200 does not imply usable content:
  `finish_reason=length` with empty `content` remains an incomplete text result.
- Provider-reported USD cost is shown separately from the configured-tariff
  estimate, using the same pure structured-cost parser as the attempt journal.
  Explicit `usage.cost=0` is a reported zero, not missing data; missing cost is
  unknown, and non-USD/unrepresentable claims remain unresolved. Supplier
  `usage.cost_details.upstream_inference_*` is not the customer's retail cost.
  Output counts include reasoning tokens; detail counts are not added again.
- Active model/provider, configured key and `api_format=openai` are required.
  Historical failures are shown but do not permanently block an explicit recovery
  test. The endpoint forwards the entered question (at most 8000 characters) to
  the selected billed client with temperature 0, a 20-second transport timeout and
  at most `min(model.max_tokens, 512)` output tokens. A blank/whitespace question
  sends `Ответь одним словом: да` with at most `min(model.max_tokens, 8)` output
  tokens. The request and answer, limit, elapsed time, actual/unknown usage and
  known/unknown cost estimate are displayed separately. Mock usage is labelled
  simulated and actual spent tokens stay zero. Timeout is not a provider JSON
  field. Anthropic/custom and multimodal validation remain out of scope. The retired `/admin/llm-model/test-real` route never dispatches;
  an authenticated administrator with valid CSRF receives 410, not a replay.
- A received real response with valid, reported input/output tokens allows an
  estimate from configured USD-per-1K rates and an update of `last_request_cost`
  / `last_request_cost_at`. Missing/invalid raw provider usage stays **unknown**, not zero, even if the
  compatibility chat response synthesizes numeric zeros.
  If saving fails, the answer remains visible with a warning; never automatically
  repeat a paid call. Model tariffs are never rewritten from generated text.
  This estimate is not journal completeness, settled spend or budget admission.
  `last_request_cost` remains an estimate (the existing column contract), not the
  API-reported price. `_record_llm_usage` updates model health/use counters, not
  per-call token rows. The existing `llm_attempts` journal records input/output
  tokens and exact provider USD evidence when a trusted tenant scope is present;
  its best-effort writer can skip/fail. In single-owner mode without that scope,
  no durable per-attempt token/price row is guaranteed. No implicit workspace or
  personal billing account is created by a test.
- The list and detail views preserve tiny tariff values instead of rounding
  them to `$0.0000`. The create/edit field allowlist excludes usage/cost/history
  telemetry; those fields remain visible read-only in details. Capability choices,
  provider selection and default-model rules keep their existing contracts.
- The responsive testing page uses `/app` theme tokens with light/dark/system
  themes, explicit labels, keyboard focus, 44px action targets and a common
  escaped result panel. Offline fixture coverage does not replace owner-local
  route, session, permission/revocation or PostgreSQL acceptance.
- Provider `test-connection` is a separate real analyze path, not the
  model page's metadata/mock/billed-request modes. Provider probes still need explicit
  review of their distinct payer/accounting path; do not infer that this model
  probe candidate accepts provider connection testing or the broader P2 cutover.
  See [accounting scope](IMPLEMENTATION_STATUS.md).

Only `LLMModel` has `is_default`; providers do not. Model capability/default
selection and encrypted key access are described in [MODELS](MODELS.md).
Provider keys are encrypted after save if not already recognized as encrypted;
this does not authorize exposing plaintext or another user's personal secrets.

### Notification delivery

The messenger action is hidden pending a recipient picker/delivery-feedback UI.
Its endpoint passes `recipient_id` and the notification's workspace to the
messenger service. Missing/foreign/inactive recipients or an ownerless row cannot
fall back to an environment admin chat. The action redirects after attempting
delivery; it is not a per-recipient delivery report. See [NOTIFICATIONS](NOTIFICATIONS.md).

## Password routes outside SQLAdmin

[Endpoints](../app/admin/endpoints.py).

| Method / route | Current contract |
| --- | --- |
| `GET /admin/reset-password` | Legacy public form still renders and issues a CSRF token; it is not a verified recovery flow |
| `POST /admin/reset-password` | Handler raises **410 Gone** without account lookup, password mutation or temporary-password logging; limiter can reject earlier |
| `GET /admin/user/change-password/{user_id}` | Active platform operator/admin required; self-change or platform-admin-only cross-account reset |
| `POST /admin/user/change-password/{user_id}` | Same authority plus CSRF, confirmation and password validation; self-change verifies current password; cross-account reset also checks `user.update` |
| `POST /admin/verify-current-password` | Uses the authenticated token subject and platform-entry gate; checks only that account's supplied `current_password`; returns `valid` Boolean |

The password action's UPDATE grant alone does **not** authorize an operator to
reset someone else's password. An operator may change their own through the
external form without a user UPDATE grant. A platform admin resetting another
account does not need that account's current password. No delivered expiring,
one-use, account-bound self-service recovery mechanism is implied by the inert
legacy form. Historical anonymous password replacement is no longer the current
POST implementation; this documentation change did not implement that fix.

Password rules come from `validate_password()` and `PASSWORD_*` settings, including
minimum length and configured uppercase/lowercase/digit/special requirements.
Rate-limit defaults: reset POST `3/hour`, change-password POST `5/minute`;
`DEBUG` disables this endpoint limiter. These limits are not authorization.

## Dashboard routes

`GET /dashboard` and `GET /dashboard/topic-chains` are FastAPI template pages,
not `/admin/<identity>` model views or the tenant home `/app/`. Both redirect a
missing token to admin login and explicitly require platform entry plus
`aianalytics` VIEW through `require_admin_perm()` before rendering. They provide
analytics/trend/cost and topic-chain presentation; do not infer workspace scope
from the web UI selector or full data-endpoint acceptance from a page GET.

## Sessions, CSRF and known verification limits

[Main setup](../app/main.py), [CSRF manager](../app/admin/csrf.py),
[legacy middleware](../app/admin/middleware.py).

- The outer Starlette `session` cookie is signed, not encrypted/server-side
  storage. It carries an access JWT; max age is 24 hours. Access JWT expiry follows
  the environment-specific token settings. HTTPS-only is false only for the
  explicit development profile.
- `LOGIN_TIMEOUT_MINUTES` is the legacy lockout interval, not session lifetime.
  Login counters are cookie-local; ordinary wrong-password returns do not call
  `track_login_attempt()`. A shared cross-surface throttle is still open work.
- Login, password-change and the model-test POST handlers explicitly verify `csrf_token`. The current
  `CSRFTokenManager` constructor defaults to **30 minutes** and a signed timed
  payload. Setup passes only `secret_key`; the similarly named
  `SCRF_TOKEN_EXPIRY_MINUTES` / `SCRF_TOKEN_LENGTH` settings are not wired there.
- Do not claim blanket CSRF coverage for all console CRUD/custom actions from
  token availability in templates. The main app's legacy `csrf_middleware`
  registration is commented out; that helper also skips `/admin`. Several custom
  action handlers take state-changing arguments from GET query parameters.
  Full SQLAdmin CRUD/action CSRF coverage was not verified by this source audit.
- Console model rights do not replace tenant, recipient, provider-accounting,
  external-write approval or runtime safety contracts. No live action is needed
  to verify these local UI contracts.

### Known view/ORM configuration drift

The registered source still contains legacy configuration mismatches; this
reference must not describe them as working schema fields:

- `SourceAdmin.column_list` names `date_from` / `date_to`, while current `Source`
  declares neither column nor property (the columns were removed in migration
  0079). Its create/edit form no longer includes those fields.

These are source observations, not a claim that startup or a particular rendered
page was reproduced. These unrelated runtime/view fixes are outside the LLM model-test change. Real SQLAdmin/ASGI/PG role, revocation and page-render acceptance remains
outside the checks performed here; prior C/D limits stay in the delivered index.

## Templates and module map

SQLAdmin uses project `app/templates/` plus its own template loader; the custom
FastAPI pages use both project and installed SQLAdmin template directories.
Template globals registered by setup: `csrf_token()`, `settings`, `debug`,
`render_analysis`. Static assets are mounted at `/static`.

Representative overrides: `sqladmin/source_{create,edit,details}.html`,
`sqladmin/bot_scenario_{create,edit}.html`, `sqladmin/scenario_prompts.html`,
`sqladmin/ai_analytics_detail.html`, `llm_provider/{create,edit}.html`,
`llm_model/{test_results,test_error}.html` and `sqladmin/user/` password forms.
The separate `/app` Jinja autoescaping contract does not certify SQLAdmin's
independent template environment or URL safety.

| Module | Responsibility |
| --- | --- |
| `app/admin/setup.py` | Console/auth/authorization setup, globals, static mount and 16 registered views |
| `app/admin/views.py` | Forms, lists, flags, queries and 10 action declarations |
| `app/admin/auth.py` | Login/logout and fresh platform entry |
| `app/admin/authorization.py` | Per-request grants, lazy identity/action catalog and external-route guard |
| `app/admin/base.py`, `formatters.py`, `widgets.py` | Shared view configuration, display formatting and form widgets |
| `app/admin/actions.py` | LLM metadata/mock/billed-request action rendering |
| `app/admin/endpoints.py` | Password, dashboard and model-test POST routes |
| `app/admin/csrf.py`, `middleware.py` | Token manager and legacy middleware helpers; registration/coverage limits above |

## Access schema and rollout

Source head 0096 separates platform/workspace catalogs, adds resource keys and
personal-memory ownership, and changes membership AI access. See
[ACCESS_MIGRATION](ACCESS_MIGRATION.md) and [MODELS](MODELS.md). Existing owner-local
application/validation is recorded in [IMPLEMENTATION_STATUS](IMPLEMENTATION_STATUS.md);
it is not proof of production state, universal schema parity or full admin
acceptance. This patch applies no migrations and activates no provider or live action.

## Related FK/m2m record editors

On existing create/edit form selects backed by a registered FK or many-to-many
relationship, permitted operators see inline **plus** (add) and **pencil**
(edit selected) SVG icon buttons, with Russian tooltips and accessible names.
The latter needs exactly one selected record (for m2m, temporarily select only
that record to edit it). This is a related-record window, not nested inline CRUD.

- A separate window uses the normal target SQLAdmin create/edit route. Parent
  input stays in place; successful creation selects the new record, preserving
  other m2m selections. Editing updates the option label without changing the
  current selection. Save the parent form to persist its relationship changes.
- The related record is saved independently. Cancelling the parent does not
  undo creation/editing of the related record. Closing the child without saving
  does not change the parent's selection. Popup blockers get an inline notice.
- Buttons follow the target's access, create/edit flags and grants; target routes
  still enforce authentication, current grants and object-specific edit checks.
  No new grants, raw FK fields, CRUD endpoints or persistence/session code.
  Excluded relationships, view-only and one-to-many relationships, unregistered
  models, disabled/read-only selects and the specialized permission-transfer
  widget are not expanded. Existing catalog restrictions remain unchanged.
- The return receipt is emitted only after successful normal CRUD and its hooks;
  validation/storage failures and custom responses retain normal behavior. A
  form-body token survives validation errors even when the action loses its
  query string. The parent validates same origin, exact child window, random
  token, target identity and (for edits) original record ID. Labels are JSON
  escaped and inserted as text, never HTML. A receipt is not write authority.
- Without JavaScript, ordinary selectors and standard CRUD remain available;
  automatic return from a separate window requires JavaScript and popups.

### Related-editor validation and local handoff

Task P8-T21. Implementation/test SHA: `6f1f695dec730396bda79043d19414fea13a2094`;
synchronized dev: `1e0e3a0c772c4a7bcc04862434c172d32288868e`. Other active
lanes #216/P10-T11 and #217/P10-T12 were inspected; their owners, reservations,
statuses and evidence were not changed or imported as accepted work.

Initial implementation sandbox evidence (owner remote exception; follow-up below):

```sh
/data/admin-relations-venv/bin/python -m pytest tests/test_admin_related.py \
  --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
NODE_PATH=/data/admin-relations-browser/node_modules node tests/admin_related_browser.cjs
```

- Pytest: exit 0, **20 passed, 0 failed/skipped**. Actual SQLAdmin routes and
  Jinja fragments over synthetic ORM metadata and explicit storage doubles;
  create/edit, ordinary redirects, validation/storage failures, grant and
  object-check denials, excluded/unregistered targets, token preservation,
  JSON escaping and custom-response preservation. No bound database engine.
- Chromium: exit 0, **32 assertions** at 390/1440px. Actual child-completion
  script, FK and m2m selection/rename, unsaved parent preservation, origin,
  source-window, identity/ID rejection, cancellation and popup blocker. Every
  request intercepted; no real application server or external requests.
- Separate venv: Python 3.13.14; SQLAdmin 0.32.0, SQLAlchemy 2.1.5,
  Starlette 1.9.0, WTForms 3.2.2, pytest 9.1.1, pytest-asyncio 1.4.0,
  httpx 0.28.1, greenlet 3.5.6 (manifest-compatible versions). Browser fixture
  uses a separate Playwright install and local Chromium; set `CHROMIUM_PATH`
  if Chromium is elsewhere. No owner `.venv`, `.env`, secrets or providers used.
- Earlier fixture attempts failed on an unbound synthetic read and a missing
  request `query_string`; corrected explicit doubles/request scope, not weakened
  assertions. These were not successful runs. Final counts above are complete.

Original owner-local acceptance checklist (historical plan; final bounded owner
acceptance is recorded below; the full checklist is not claimed as executed).
At the exact implementation SHA, run sequentially under R8/R10 in the owner's
prepared environment, without reset/migration/seed or real provider dispatch:

1. Exercise actual `LLMModel.provider` and `Source.platform` FK forms, then
   `AgentTask.sources` m2m with records in an existing approved test workspace.
   Verify create/edit persistence, unchanged other selections/unsaved fields,
   cancellation and independent child/parent transaction behavior.
2. Verify platform admin/operator create-only/edit-only/denied roles, revoked
   identity/grants between opening and saving, direct forged popup POSTs and
   object-level denials. Catalog permissions and hidden user-role assignment
   must remain restricted; workspace roles must not gain console access.
3. Check actual Select2, application CSP/browser headers, popup opener policy,
   specialized forms, nested windows, validation retry, back/refresh and no-JS.
   A restrictive opener policy may require falling back to ordinary CRUD.
4. Confirm existing workspace/source/scenario validators still reject invalid
   cross-workspace links and actual DB write/rollback failures do not report a
   success receipt. No new atomicity guarantee is made across two forms.

No migration, schema change, real DB/ASGI integration acceptance, full-suite,
whole-console security or deployment certification is claimed by this subset.


### Related-editor follow-up: inline icons and Float forms

The owner-local attempt reported HTTP 500 while opening `/admin/tenant/edit/1`
from a related field. This is failure evidence, not successful local acceptance.
The same failure was reproduced offline on the canonical Tenant/TenantAdmin:
SQLAdmin 0.32.0 could not convert `daily_cost_limit` (`sqlalchemy.sql.sqltypes.Float`)
with SQLAlchemy 2.1.5, where Float no longer inherits Numeric.

`BaseAdmin` now uses a converter for Float/FLOAT/REAL/Double/DOUBLE families;
exact Numeric/Decimal monetary fields retain SQLAdmin's DecimalField path.
This does not alter ORM types, stored values, validation boundaries or grants.
The regression loads the actual model and view declaration with isolated
configuration and a storage function that raises on use; both ordinary and
related create/edit form scaffolding therefore need no database.

Plus/pencil SVG actions now share a flex row with the native input or actual
Select2 container. Icon-only controls have 44px targets, accessible names,
tooltips and visible keyboard focus; the status remains below the field.
Select2 is moved with its original select and handlers, not recreated; scoped
height/padding prevents label clipping and chips follow the existing theme.

Follow-up evidence: **81 isolated pytest cases passed**, 0 failed/skipped, exit 0;
**228 offline Chromium assertions passed**, exit 0, across native + real Select2,
light/dark and 390/1440px. Checks include canonical Tenant related GETs, required
and nullable float parsing, unchanged precise Decimal, existing catalog grants,
parent preservation, popup protocol, inline geometry, icon-only labels, dropdown
operation, label bounds and no page overflow. Eight fixture screens were
individually inspected; this is not owner-local browser or DB acceptance.
Published source SHA is pinned in PR #219's current handoff. Initial evidence
above stays attributed to its original SHA and does not certify this follow-up.

```sh
/data/admin-relations-venv/bin/python -m pytest \
  tests/test_admin_form_compatibility.py tests/test_admin_related.py \
  tests/test_admin_shell_catalog.py tests/test_admin_all_role_records.py \
  --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
SQLADMIN_STATIC_DIR=/data/admin-relations-venv/lib/python3.13/site-packages/sqladmin/statics \
NODE_PATH=/data/admin-relations-browser/node_modules node tests/admin_related_browser.cjs
```

Same Python/SQLAdmin/SQLAlchemy/Starlette/WTForms versions as the initial run;
FastAPI 0.146.0 and itsdangerous 2.2.0 added to the separate sandbox environment
for the existing isolated catalog tests. Python 3.14 was not available/tested
remotely. No application/runtime, real DB, environment secrets, providers,
schema operation, dependency manifest change or delegated test execution.
Owner subsequently reported that the updated local UI works and explicitly
requested task closure and Ready/merge into dev. Accepted implementation:
`aec048db192bfc9842883502065646d5650f43d9`. This is owner-reported functional
smoke, not a remotely verified PostgreSQL/revocation/rollback/full-suite result.
The coordinator reviewed the exact implementation and reused the completed
81-test/228-assertion evidence; no runtime source changed for closeout. P8-T21
is accepted as a bounded feature; no whole-phase, security-gate, schema or
deployment acceptance is implied. Detailed original test limitations remain above.
