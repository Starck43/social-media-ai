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

Each list has its own bounded, contextual height and contained scrolling; its
panel is not another scroll container. Narrow editors stack the panes. The native
select remains the sole submitted value source, including filtered-out choices,
and remains usable if JavaScript is disabled. This needs no new dependency or
schema migration. Editing stored grants on `admin` does not remove its wildcard
access and is distinct from changing one's account role.

Offline validation: `tests/permission_transfer_fixture.py` renders actual SQLAdmin
forms with 30 synthetic permissions and explicit storage doubles;
`tests/permission_transfer_layout.py` exercises desktop/mobile widths, both themes,
empty/full/small selections, double-click, keyboard, touch, native FormData/reset,
scroll containment and the no-JavaScript fallback. These are not real database
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
- **Real request / «Реальный запрос»** (`test_type=real`) also requires enabled personal billing,
  an active platform administrator and an explicitly selected active execution
  workspace. The initiating administrator is the personal payer; a submitted
  payer ID is never trusted. The personal admission service must authorize the
  account, daily baseline, cap and model bound before transport. Disabling
  billing refuses paid probes, never falls back to unaccounted HTTP.
- Disabled billing, missing platform-admin authority and missing/inactive workspace
  produce a visible, pre-dispatch result on the same page, with the original
  HTTP 409/403/422 status. The real button is not silently disabled; a reason is
  shown next to it. A client-side missing-workspace notice is scrolled into view.
  The billing flag, account/cutover/cap/model-bound gates are **not bypassed**.
- Real results show a readable escaped text card, delivery state, actual provider
  HTTP status (or explicitly unconfirmed), stage, finish_reason, accounting/journal
  status and a safe error code. Response JSON is formatted Unicode text, expanded
  by default and copyable. It is an actual provider response, not a reconstructed
  usage envelope. Known credential fields and the configured key are redacted;
  depth/size limits mark truncated display copies. Non-JSON bodies are shown as
  escaped, redacted excerpts. No request headers or arbitrary exception strings.
  Provider JSON survives HTTP errors, empty content/length/tool/reasoning replies,
  and failures after the response (including accounting). These are not conflated
  with an unsent request. No diagnostics enter provider payloads or alter the
  durable billing evidence; ordinary non-probe chat keeps its existing shape.
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
