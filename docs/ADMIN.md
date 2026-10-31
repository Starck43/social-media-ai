# Admin Panel Reference

The SQLAdmin console is a **global platform/operator interface**, not the
workspace web application and not an editor for every ORM table.

- Base path: `/admin` (local development: `http://localhost:8000/admin`).
- Registration: [setup.py](../app/admin/setup.py); views: [views.py](../app/admin/views.py).
- Enabled by `ADMIN_ENABLED` (default true) in [app.main](../app/main.py).

## Admin actor scope

Analytics routes `/dashboard` and `/dashboard/*` use the same selected-workspace
scope and membership action limits as SQLAdmin.

`AdminScopeMiddleware` binds each `/admin/*` request to its fresh signed-in
actor ([scope.py](../app/admin/scope.py)). Console entry itself is the login
gate: only an active account whose **platform** role is `operator`, `admin` or
`superuser` may sign in, so an ordinary invited client never reaches `/admin`
at all ([authentication below](#authentication-and-platform-authority)). What
the console then *shows* is decided by one scope rule:

| Actor | Personal rows (`owner_user_id`) | Workspace rows (`tenant_id`) | Platform catalogs |
| --- | --- | --- | --- |
| `superuser` (the owner) | **own only** | all (global inspection) | all |
| `admin` / `operator` | **own only** | only the selected active workspace, with that membership's role | per platform grants |

Personal provider/model rows stay owner-scoped for every platform role,
including the owner, as the personal-catalog contract requires. "Own" is the
signed-in actor, never a value from the form, the path or the query string; a
row outside the scope does not exist for that request. A foreign id is a
404/403 on its direct details/edit URL, and a provider reached that way cannot
decrypt its key.

For workspace screens the platform console grant is only the outer ceiling:
`app_tenant_id` selects one active membership, the selection is validated on
every request and falls back to the oldest active membership if it is stale,
unknown or foreign. Users switch workspaces through `/app`. The selected
membership's role then caps the action — a stronger role in another membership
does not grant actions in the selected workspace. Rows and direct
detail/edit/delete URLs outside the selected workspace are unavailable.

Failures close: an actor with no workspace membership gets `403` on a workspace
page (never an empty list, never a 500), and an unknown view identity is treated
as workspace-bound. The classification is derived from the model
(`__tenant_scoped__`) and cached per view identity (`_workspace_view_by_identity`),
not from a hand-kept list. Active workspace grants are resolved from preloaded
`TenantUser.role` relationships (or a single batched role query when unloaded)
without per-membership N+1 queries.

The platform owner retains the existing global workspace inspection exception.
It does **not** widen the personal catalog, credentials or `owner_user_id`
filtering of providers/models, and it does not let an ordinary workspace member
into `/admin`. SQLAdmin's own ORM statements (counts, details, relationships and
raw-FK writes) run on the dedicated `AdminAsyncSession`/`AdminSession` in
[session.py](../app/admin/session.py) so they obey the same actor/workspace
scope, caching computed per-model row criteria across `SELECT`s and `before_flush`
checks within each request (`row_criteria_scope`); that session is not a
general-purpose raw-query guard and `BaseManager` does not implement one either.

Regression evidence: [middleware](../tests/admin/test_admin_scope_middleware.py),
[manager scope](../tests/admin/test_admin_scope_database.py),
[console session](../tests/admin/test_admin_scope_session.py),
[coverage](../tests/admin/test_admin_scope_coverage.py).

## Personal catalog source contract (0109; PR #229 + #230 integrated)

Providers and models are personal to a user and may be shared across that user's
workspaces. Console action grants do not widen row ownership or permit another
user's key use. Settings → Models remains the atomic provider/model/default flow;
SQLAdmin uses the same managers. See [TENANCY](TENANCY.md#personal-llm-catalog-0109-source-contract-code-integrated-rollout-pending),
[MODELS](MODELS.md#personal-catalog-0109-defined-operational-rollout-separate) and the
[ownership audit](audit/personal-llm-ownership.md) for the canonical ownership,
rollout and source-pinned evidence.

### Who sees which rows, and who writes the owner

All catalog reads remain owner-scoped, including for the platform owner. In the
provider form, only the platform owner can choose an owner at creation; other
actors are stamped as owner, and the database prevents reassignment. Models
inherit their provider's owner and cannot be attached to a foreign provider.
The source-only regression is
[`test_admin_llm_catalog_scope.py`](../tests/admin/test_admin_llm_catalog_scope.py).
Provider UID is derived from its name when omitted; the UI previews the same slug.


## Read-only related FK lists (P8-T30 / P8-T31 / P8-T32)

Persisted details and edit pages append **Связанные записи** below the main
content, including custom Source, analytics and LLM/scenario templates. Creates,
list pages and unsaved objects do not render this footer. This is a read-only
projection, not a new CRUD route: no add/edit/delete buttons, inline mutation,
exports or foreign-record popup controls are introduced by these lists. Each
section is a native `details` disclosure, collapsed by default; click its heading
or decorative SVG chevron (or use the keyboard) to expand/collapse. Open state is
not persisted; page navigation reloads sections collapsed. Collapse is visual,
not lazy loading: the existing scoped count/page queries remain unchanged.

ID and name/title/user-label cells link to the existing edit route when
SQLAdmin's per-row `check_can_edit` allows it; otherwise to details when
`check_can_view_details` allows it. If neither allows navigation, cells remain
plain text. Both checks use a detached shell of the already scoped scalar row,
never a relationship read. Destination routes recheck authority themselves;
links do not grant access or perform writes. Pagination stays aligned right;
the visible-row count remains on the left.

- Discover inbound FK constraints among **registered admin model views** with a
  registered access resource. Compound FKs stay conjunctive; multiple independent
  FKs to the parent get separate sections. Missing ORM backrefs are supported.
  Unregistered models and association-table/many-to-many editors are not exposed.
- Require a fresh active console actor and parent/target list/details grants and
  view accessibility. Parent visibility is checked before loading any child list.
- The lists add scope constraints beyond the historical global console queries.
  Non-owner platform operators/admins see workspace rows only through their
  active web membership in an active workspace, and only when that membership's
  fixed role grants VIEW on the target resource. Owning and granted workspaces
  are both supported; authorship does not replace workspace rights. Disabled,
  foreign, unknown-role and unresolved memberships do not grant visibility.
- The platform owner's existing global workspace inspection exception remains.
  It does **not** widen personal provider/model catalogs or credentials: those
  are restricted to the signed-in user's owner/user id for every platform role.
  Global reference catalogs still require console model rights. These new filters
  do not change the legacy global scope of the main console pages or grant an
  ordinary workspace member access to `/admin`.
- Each FK section reuses SQLAdmin `Pagination`: independent previous/next,
  numbered pages, visible-row count and page-size selection (10/25/50/100 by
  default, 25 initial). Counts and pages share the same scoped DISTINCT
  projection and existing view predicates; no global count is exposed. Page/size
  query parameters are namespaced by target and FK constraint. Invalid pages
  clamp before OFFSET and unsupported sizes fall back to configured defaults;
  links preserve other sections/parent query state and jump to their section.
- Tables scroll within a bounded container, headers remain visible, and native
  SQLAdmin/Tabler pagination classes are used in both themes. On edit pages a
  notice asks the operator to save first: page navigation performs a GET reload
  and does not save/preserve unsaved form input implicitly.
- Only configured scalar list columns also allowed in details are projected;
  sensitive names, JSON/binary values, relationship labels and arbitrary/custom
  row formatters remain excluded. Cells are escaped length-bounded text; enum
  labels are used where the mapped enum provides them. Empty means **Нет
  доступных записей**, not that no rows exist globally.
- `CollectedItemAdmin.inline_exclude = True` retains P10-T21's high-volume
  raw-content exclusion for every parent/console role. Raw rows remain on their
  dedicated collection list; no central table deny-list is needed.

### Configuring inline lists in views

A target view configures its presentation wherever it appears as a related FK
list; configuration never grants access or overrides projection safety rules:

```python
class SourceAdmin(BaseAdmin, model=Source):
    inline_columns = ["id", "name", "source_type", "is_active", "last_checked"]
    inline_page_size = 25
    inline_page_size_options = (10, 25, 50, 100)
    inline_exclude = False
```

`inline_columns = None` reuses the safe scalar subset of `column_list`;
`[]` omits the inline. `inline_link_columns` defaults to ID, name, title, username and label cells;
it only selects which displayed cells may receive a permission-gated link.
`column_labels` supplies Russian column/relation headings,
using shared BaseAdmin labels for common identifiers. Target-level exclusion
skips even count/page queries. Page-size options are bounded to 1–200.

Current view choices retain 3–6 useful columns: IDs and names, type/status or
active flag, and task dates/cost where useful. Provider URLs, repeated owner/FK
IDs, scenario descriptions and the wide model-price matrix are omitted from
compact inlines, without changing the full model lists. Models keep their API
identifier/type/default/active state; jobs keep type/status/run time/attempts.

Implementation: [inlines.py](../app/admin/inlines.py). Synthetic role/scope,
ASGI and canonical metadata checks: [scope tests](../tests/admin/test_p8_t30_related_inlines.py) and
[pagination tests](../tests/admin/test_p8_t31_inline_pagination.py).
Real PostgreSQL/session and deployed UI audit remain owner-local follow-up;
mocked evidence does not certify the deployed console.

## Authentication and platform authority

[Auth backend](../app/admin/auth.py),
[authorization backend](../app/admin/authorization.py),
[identity resolver](../app/admin/identity.py),
[User](../app/models/identity/user.py),
[resource catalog](../app/core/access_catalog.py).

`GET /admin/login` displays the SQLAdmin login form. Login accepts username or
email and password, validates its CSRF token, and requires an active account
whose **platform** role is `operator` or `admin` (`User.has_admin_access()`).
Workspace owner/admin membership does not confer console access. SQLAdmin logout
clears the browser session.

Authenticated requests resolve the JWT subject to a fresh active User with
platform role/permissions through the shared `resolve_operator_identity()` helper
in [identity.py](../app/admin/identity.py) (used by auth, authorization, scope
middleware and standalone admin endpoints). The backend does not trust a stale
session `user_id` as a second identity source. **Only the platform owner (`superuser`) receives
wildcard console grants.** An `admin` and an `operator` both receive explicit
model/action grants from `role_permission`; the difference is what the stored
grants say, not a code-level bypass. There is no current `User.is_superuser`
column.

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

For operators and admins, any granted non-VIEW action also implies list/details
so its form can be reached. No grants means no model access. The owner skips the
lookup entirely. View-level `can_*` flags still disable operations even for the
owner. Custom action declarations are
checked against the action slugs; unknown declaration keys are logged when the
lazy view catalog is built. Menu visibility alone is not authorization.

Additional restrictions:

- Workspace deletion is reserved for the platform `superuser`. Even explicit
  platform admin/operator grants and workspace owner/admin membership cannot
  delegate `tenancy.workspace.delete`. Other workspace grants are unchanged.

- `TenantAdmin.is_accessible()` requires the `tenancy.workspace` VIEW right, not
  the platform badge and not ownership of one workspace. The owner holds it
  through the wildcard; an `admin` needs it granted like any other role.
- Platform role and permission identities remain fixed: no create/delete or code,
  resource/action, or user-assignment editing. Only the platform owner
  (`superuser`) may edit the permission selection of the operator role
  (`RoleManager.set_permissions`) and a permission's display name; a platform
  `admin` is refused. Operators cannot
  edit either catalog, even with explicit grants. Writes recheck the active owner
  and CSRF. Stored grants decide role authority: widening an `admin` means
  granting the right in `role_permission`, never restoring a bypass, and `user`
  cannot enter the console. The API
  `PUT /api/v1/users/roles/{role}/permissions` uses the same owner gate.
- The console names the signed-in level truthfully: the owner's identity block
  says «Собственник: полный доступ ко всем данным», an admin sees «Доступ по
  назначенным правам», an operator the same.
- Реестр таблиц (`/admin/registry`, [registry.py](../app/admin/registry.py),
  [table_registry.py](../app/admin/table_registry.py)) is the owner's read-only map
  of every mapped table, including association tables and tables without a
  console page: search by table, model or resource key, pagination, and a detail
  page with columns, types, keys, foreign keys and sensitive-column markers.
  It is gated on `is_accessible` (`User.is_superuser`) rather than model grants,
  so an admin's grants do not open it; it has no create/edit/delete/export
  route and reads no rows.
- User forms: the edit page carries a **role picker**
  ([role_picker.py](../app/admin/role_picker.py)) listing only the roles the
  signed-in actor may assign, highest rank first. The rule is the rank, not the
  badge: an actor assigns a role strictly **below their own** — the owner
  (`superuser`) offers `admin`/`operator`/`user`, an `admin` offers
  `operator`/`user`, an `operator` offers `user`, and a rank with nothing below it
  sees a disabled empty select. Two rows show the role **read-only** instead of a
  choice list — the actor's own account and the platform owner's account: the
  select holds the current role only and is disabled, with the reason in its
  hint, because `set_platform_role()` refuses both writes. The `identity.user`
  UPDATE right still decides who
  opens the form at all; the rank decides what the form may submit. The write goes
  through `UserManager.set_platform_role()`, never the generic `role_id` column, so
  a forged payload is refused too.
- `superuser` is never assignable at runtime: not by an admin, not by the owner,
  not from the console, the API or any CLI. Only revision `0110`/the bootstrap
  command (`scripts/setup/bootstrap_owner.py`) put an account on it, and a second
  owner cannot be created. An `admin` also cannot rewrite an account that already
  sits at or above their rank, so peers stay peers.

  The owner's account is protected at every seam, not only in the form:
  `UserManager.delete_user()` refuses it (so `DELETE /api/v1/users/{id}` cannot
  drop it — the old protection was an incidental foreign key from the catalog),
  `UserManager.update_user()` and `UserAdmin.update_model()` refuse another
  account writing it, and both password routes (`GET`/`POST
  /admin/user/change-password/{id}`) answer 403. Nobody changes their own role.
  The owner keeps the routine rights: their own name, email and password.
- New users always receive the ordinary platform `user` role: the create form has
  no role field.
- `UserManager.set_platform_role()` rejects every self-assignment (including
  selecting the same role again) before any write; another account above the
  target's rank must change it. Generic SQLAdmin updates reject role fields rather
  than bypassing this service guard.
- External FastAPI dashboards use `require_admin_perm()` explicitly. Password
  administration has its additional self/cross-account checks below.

The main app's `PlatformScopeMiddleware` leaves infrastructure paths outside
data scoping, while `/app` and `/api` have their own scope middleware. `/admin`
and `/dashboard` are excluded from its operator bypass: `AdminScopeMiddleware`
binds their requests to the actor ([actor scope](#admin-actor-scope)). Admin
listings use the validated selected membership for workspace rows and the actor's
own personal catalogue. Users select the shared workspace through `/app`;
console middleware rechecks membership before using it. Manual task actions
look up the task inside that scope before admission into its owning workspace.

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
or schema migration. Editing stored grants on `admin` is how its console reach is narrowed or
widened — there is no wildcard to remove — and is distinct from changing one's
account role.

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

`setup_admin()` registers **17 menu entries** — the owner-only `TableRegistryAdmin`
base view plus **16 model views** — grouped into 5 SQLAdmin domain categories
(`Система`, `Пользователи и доступ`, `Источники и сбор`, `ИИ и аналитика`,
`Задачи и операции`). `RelatedAdmin._build_menu` wraps each category in
`_ScopedCategoryMenu`, hiding any category whose child views are all invisible or
inaccessible to the current operator. Paths shown below are the SQLAdmin list
routes; available menu entries and operations depend on grants. Unspecified
CRUD/export flags inherit SQLAdmin defaults, not a promise of safe
business-workflow execution.

| View | Category | Model | List path | Explicit restriction / purpose |
| --- | --- | --- | --- | --- |
| `TableRegistryAdmin` | `Система` | *(BaseView)* | `/admin/registry` | Owner-only read-only schema registry cached by metadata signature |
| `UserAdmin` | `Пользователи и доступ` | `User` | `/admin/user/list` | Account forms use rank-limited role picker on edit; password action is separate |
| `RoleAdmin` | `Пользователи и доступ` | `PlatformRole` | `/admin/platform-role/list` | Owner-only permission grant editor; catalog identities remain fixed |
| `PermissionAdmin` | `Пользователи и доступ` | `Permission` | `/admin/permission/list` | Fixed catalog; owner may edit display name only |
| `TenantAdmin` | `Пользователи и доступ` | `Tenant` | `/admin/tenant/list` | Global workspace settings; gated on `tenancy.workspace` VIEW |
| `UserCredentialAdmin` | `Пользователи и доступ` | `UserCredential` | `/admin/user-credential/list` | Personal credential vault rows; secret sealed before write and blanked on edit |
| `PlatformAdmin` | `Источники и сбор` | `Platform` | `/admin/platform/list` | Platform configuration; no sync action |
| `SourceAdmin` | `Источники и сбор` | `Source` | `/admin/source/list` | Sources and synchronous collection check |
| `CollectedItemAdmin` | `Источники и сбор` | `CollectedItem` | `/admin/collected-item/list` | Raw staged content |
| `AgentScenarioAdmin` | `ИИ и аналитика` | `AgentScenario` | `/admin/agent-scenario/list` | Analysis methodology, prompt preview and two-step active toggle |
| `AIAnalyticsAdmin` | `ИИ и аналитика` | `AIAnalytics` | `/admin/ai-analytics/list` | Create disabled; stored analysis view |
| `LLMProviderAdmin` | `ИИ и аналитика` | `LLMProvider` | `/admin/llm-provider/list` | Personal catalog: API key sealed before write and blanked on edit; two-step connection test |
| `LLMModelAdmin` | `ИИ и аналитика` | `LLMModel` | `/admin/llm-model/list` | Personal models: create/edit for roles holding `llm.model` CREATE/UPDATE, short/mock/real checks |
| `AgentTaskAdmin` | `Задачи и операции` | `AgentTask` | `/admin/agent-task/list` | Scheduled tasks and two-step inline run action |
| `BotActionAdmin` | `Задачи и операции` | `BotAction` | `/admin/bot-action/list` | Read-only general proposal ledger; no arbitrary status edits or deletion |
| `JobAdmin` | `Задачи и операции` | `Job` | `/admin/job/list` | Read-only queue inspection: create/edit/delete/export disabled |
| `NotificationAdmin` | `Задачи и операции` | `Notification` | `/admin/notification/list` | Read-only event archive; hidden two-step messenger delivery action |

`Role` is a Python compatibility alias; the mapped class is `PlatformRole` and
its default SQLAdmin identity is `platform-role`, not `role`.

No model view is registered for `ModelType`, `WorkspaceRole`, membership/invite/
channel rows, `DigestRun`, agent sessions/messages/memory/feedback, or the five
LLM attempt/receipt/outcome/budget tables. Associations are not separate pages.
The existence of a model or permission resource never creates an admin view.

## Custom actions and side effects

These are the **9 declared actions** across `app/admin/view_modules/`. Mutating and
paid actions (`toggle-active`, `run-now`, `send-to-messenger`, `test-connection`)
use a **two-step confirmation contract** (`render_action_confirmation` and
`verified_action_form` in `app/admin/actions.py`, template
`sqladmin/action_confirm.html`): the `@action` GET route never mutates state or
spends provider tokens, and the paired `@expose(..., methods=["POST"])` handler
on the same path verifies the session-bound CSRF token and reads `pks` (plus
`recipient_id` for `send-to-messenger`) from the POST form body.

| View / action slug | Right | UI placement | Current behavior |
| --- | --- | --- | --- |
| `UserAdmin` / `change-password` | UPDATE | List + detail | Redirects to the selected account's password form; the destination applies stricter cross-account checks |
| `SourceAdmin` / `check-source` | UPDATE | List + detail | Calls the social client synchronously for the first selected source and renders paginated posts/stats; does not enqueue a collection job |
| `AgentScenarioAdmin` / `view-prompts` | VIEW | Detail only | Shared ScenarioBuilder/PromptBuilder preview for media and unified prompts, including scope; no LLM call |
| `AgentScenarioAdmin` / `toggle-active` | UPDATE | List + detail | Two-step: GET renders `sqladmin/action_confirm.html`; CSRF-verified POST toggles selected scenarios' `is_active` |
| `AgentTaskAdmin` / `run-now` | UPDATE | List + detail | Two-step: GET renders confirmation; CSRF-verified POST admits/enqueues the task run, claims that exact Job and executes inline |
| `AIAnalyticsAdmin` / `view-analysis` | VIEW | List + detail | Redirects to the stored analysis detail route |
| `NotificationAdmin` / `send-to-messenger` | UPDATE | Hidden in list + detail | Two-step: GET renders confirmation with `recipient_id` field; CSRF-verified POST reads `pks` and `recipient_id` from form body only (query-string `recipient_id` is ignored) |
| `LLMProviderAdmin` / `test-connection` | CONFIGURE | List + detail | Two-step: GET renders confirmation; CSRF-verified POST runs a real `client.analyze()` probe (`max_tokens=10`) on the first active model |
| `LLMModelAdmin` / `test-model` | CONFIGURE | List + detail | Read-only GET opens the UI; POST checks fresh CONFIGURE/CSRF; explicit real POST is the paid action |

`PlatformAdmin` declares no custom action; there is no implemented or placeholder
sync button in the current class. `JobAdmin` has no run/cancel/retry button.
BotAction/AgentAction status is no longer editable through an admin form. Use the
CSRF-protected workspace review page `/app/actions/{id}` for approve/reject/cancel;
it rechecks the actor, membership, grant and reviewed payload hash. This does not
enable external execution. Notification read state is personal and can only be
acknowledged in the user's inbox, not by the old shared admin mark-read action.

### Run task now

[Dispatcher](../app/jobs/dispatcher.py), [enqueue helper](../app/jobs/enqueue.py).
`run_task_directly()` admits/enqueues inside `task.tenant_id`, claims the exact
due row and runs its handler inline; a competing claim raises rather than
starting a second handler. The Job remains the execution audit trail. The
shared trigger path updates task bookkeeping, including one-shot deactivation.
This action can invoke configured job behavior: do not use it as a harmless UI
probe. See [AGENT_TASKS_QUEUE](AGENT_TASKS_QUEUE.md) for queue/outcome boundaries.

Task create/edit exposes source multi-selection (`agent_task_sources`),
`agent_scenario`, schedule/job/payload and trigger/action controls. Scenario =
methodology; task = execution. Empty source selection and payload semantics are
runtime contracts, not permission grants or a new business-workflow model.

**P5-T14 task-owned collection.** The task is the collection owner; a scenario
answers only how to analyse. `AgentTaskAdmin` shows and edits the `collection`
block inside the payload (content types, engagement types, limits, phrases,
city/discovery, comment/candidate caps) and explains the keys in the payload
help; the `dry_run` column/form flag is visible as a rehearsal switch. A
task-scoped query source is labelled as a task lane through
`sources.owner_task_id` (details show the owner task), and the collected-item
list exposes `content_type`, `selection_status` and `media_type` while the
collector-written lane/selection fields stay out of the edit form. See
[collection plan](audit/collection-plan-history.md).

**Deleting a source.** The web sources page and the source page carry a
delete action (`source`/`delete` permission, workspace-scoped, with the
cascade named before it happens). A task-owned lane is refused with the owning
task named — that lane exists for the task's collection and is removed there,
not from the source list.

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
- A received real response with complete usage permits an estimate using the
  model's explicit ordinary-price unit and cache prices in USD per million tokens.
  Missing applicable cache counters or prices leave that estimate unknown.
  A resolved structured provider USD total takes precedence. This permits updating `last_request_cost`
  / `last_request_cost_at`. Missing/invalid raw provider usage stays **unknown**, not zero, even if the
  compatibility chat response synthesizes numeric zeros.
  If saving fails, the answer remains visible with a warning; never automatically
  repeat a paid call. Model tariffs are never rewritten from generated text.
  This estimate is not journal completeness, settled spend or budget admission.
  `last_request_cost` stores the estimate, preferring a resolved provider USD
  total (including explicit zero) over a tariff fallback. It does not certify
  settled spend. `_record_llm_usage` updates model health/use counters, not
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
Provider API keys (`LLMProviderAdmin.encrypted_api_key`) and personal credentials
(`UserCredentialAdmin.secret_encrypted`) are encrypted **before write** in
`on_model_change` via `app/admin/secrets.py` (`seal_form_secret`): plaintext
never reaches an `INSERT` or `UPDATE`, a blank edit field preserves the stored
ciphertext, an already-sealed Fernet token is never encrypted twice, and
`get_form_data_for_edit` (`hide_form_secret`) blanks the field so ciphertext
never renders into a browser form.

### Notification delivery

The messenger action is hidden pending a recipient picker/delivery-feedback UI.
Its GET endpoint renders `sqladmin/action_confirm.html` with a `recipient_id` input;
only the CSRF-verified POST reads `pks` and `recipient_id` from the form body
(query-string `recipient_id` is ignored) and passes `recipient_id` and the
notification's workspace to the messenger service. Missing/foreign/inactive recipients or an ownerless row cannot
fall back to an environment admin chat. The action redirects after attempting
delivery; it is not a per-recipient delivery report. See [NOTIFICATIONS](NOTIFICATIONS.md).

## Password routes outside SQLAdmin

[Endpoints](../app/admin/endpoints.py).

| Method / route | Current contract |
| --- | --- |
| `GET /admin/user/change-password/{user_id}` | Active platform operator/admin required; self-change or platform-admin-only cross-account reset |
| `POST /admin/user/change-password/{user_id}` | Same authority plus CSRF, confirmation and password validation; self-change verifies current password; cross-account reset also checks `user.update` |
| `POST /admin/verify-current-password` | Uses the authenticated token subject and platform-entry gate; checks only that account's supplied `current_password`; returns `valid` Boolean |

The password action's UPDATE grant alone does **not** authorize an operator to
reset someone else's password. An operator may change their own through the
external form without a user UPDATE grant. A platform admin resetting another
account does not need that account's current password.

**Accepted recovery mechanism (P10-T01).** Recovery is the operator-issued reset
above, not self-service. A platform admin resets a member account after identity
confirmation; the owner changes their own password with their current one. The
legacy public `/admin/reset-password` form (and its "Забыли пароль?" login-page
link) has been removed from both routes and templates: there is no anonymous
password-mutation route and no handler that looks an account up by a submitted
email address. Account-bound, expiring, one-use email recovery is **not**
implemented; it is deferred to a separate task that must also bring a verified
delivery channel. Nothing in the current console flow mutates a hash without an
authenticated operator, and no route logs a password or a temporary credential.

Password rules come from `validate_password()` and `PASSWORD_*` settings, including
minimum length and configured uppercase/lowercase/digit/special requirements.
Rate limiting: the change-password POST keeps `CHANGE_PASSWORD_RATE_LIMIT`
(`5/minute`) and `POST /admin/verify-current-password` enforces
`VERIFY_PASSWORD_RATE_LIMIT` (`10/minute`) so it cannot be used as an unbounded
online password oracle; `DEBUG` disables the endpoint limiter. The change-password
form renders its heading via template context (`title`) without mutating the
shared `admin.title`. These limits are not authorization.

### Owner recovery (out-of-band)

The console refuses to reset the owner's password for *everyone*, including other
platform admins and the owner's own reset link — the owner changes it only with
the current password. If the owner loses that password, recovery happens on the
deployment host with write access to the database, never through a web route:

1. Generate the hash with the application's own hasher, entering the new secret
   through a prompt so it never reaches the shell history:
   `.venv/bin/python -c "import getpass; from app.core.hashing import get_password_hash; print(get_password_hash(getpass.getpass()))"`.
2. Update only that account, qualified by `DB_SCHEMA` (`public` by default), and
   check the returned row:
   `psql "$POSTGRES_URL" -c "UPDATE public.users SET hashed_password = '<hash>' WHERE id = <owner-id> RETURNING id, username"`.
3. Record actor, timestamp, reason and the host in the operator log. Do not paste
   the password or the hash into chat, tickets or the log.
4. Treat every access token issued before the reset as still valid: tokens are
   stateless JWTs with no server-side revocation list, so a reset does not by
   itself sign live sessions out. Rotate `SECRET_KEY` (invalidating all tokens for
   all users) when the compromise of a session is suspected, and confirm the
   recovery with the owner over an already trusted channel.

This path is an accepted residual risk for the single-owner pilot, not a
self-service product feature.

## Dashboard routes

`GET /dashboard` and `GET /dashboard/topic-chains` are FastAPI template pages,
not `/admin/<identity>` model views or the tenant home `/app/`. Both redirect a
missing token to admin login and explicitly require platform entry plus
`aianalytics` VIEW through `require_admin_perm()` before rendering. They provide
analytics/trend/cost and topic-chain presentation; do not infer workspace scope
from the web UI selector or full data-endpoint acceptance from a page GET.

## Sessions, CSRF and verification contract

[Main setup](../app/main.py), [CSRF manager & middleware](../app/admin/csrf.py).

- The outer Starlette `session` cookie is signed, not encrypted/server-side
  storage, with explicit `same_site="lax"`. It carries an access JWT and a
  per-session `csrf_session_id` (rotated on login, ensured on authenticate, with
  stale `return_url_*` and `_messages` session keys cleared at login); max age is
  24 hours. Access JWT expiry follows the environment-specific token settings.
  HTTPS-only is false only for the explicit development profile.
- `LOGIN_TIMEOUT_MINUTES` is the counting window and lockout duration, not
  session lifetime. Failed logins are counted in the global `login_attempts`
  table keyed by the casefolded account identifier and by the client address, so
  the console, `/app/login` and `POST /api/v1/auth/login` share one budget across
  cookies and processes. The account lockout is `MAX_LOGIN_ATTEMPTS`; the
  per-address budget is `LOGIN_IP_MAX_ATTEMPTS`. A successful login clears the
  account key, the address key decays with the window, and every refusal answers
  the same generic credentials error. Unknown accounts verify a real placeholder
  bcrypt hash, so the failure path does not reveal whether the account exists.
  Losing or not migrating the table breaks login — apply the revision with the
  deploy.
- One `CSRFTokenManager` instance (`30 minutes` default expiry) is constructed in
  `app/main.py` and published on both `app.state.csrf_manager` and the mounted
  SQLAdmin sub-application (`admin.admin.state.csrf_manager`). Tokens minted for
  the console are bound to `request.session["csrf_session_id"]`.
- `AdminCSRFMiddleware` (`app/admin/csrf.py`, registered in `app/main.py`) is the
  single verification gate for every mutating `/admin/*` request (`POST`, `PUT`,
  `PATCH`, `DELETE`), including SQLAdmin CRUD (`create`, `edit`, `delete`),
  catalog edits, password routes, LLM probes and custom action POSTs. It caches
  the raw request body before parsing `csrf_token` from the form so downstream
  handlers can still read the form stream; `/admin/login` POST is verified inside
  `AdminAuthBackend.login` so a failed token renders the login form message.
  `create.html`, `edit.html`, `base_form.html`, `llm_create.html` and
  `action_confirm.html` render hidden `csrf_token` inputs; `layout.html` publishes
  `<meta name="csrf-token">` and attaches `X-CSRF-Token` to same-origin jQuery
  AJAX requests (including the SQLAdmin delete modal).
- Console model rights do not replace tenant, recipient, provider-accounting,
  external-write approval or runtime safety contracts. No live action is needed
  to verify these local UI contracts.

### Known view/ORM configuration drift

The dated source observation is retained in
[admin implementation history](audit/admin-history.md#known-vieworm-configuration-drift); verify against current source before relying on it.

## Templates and module map

SQLAdmin uses project `app/templates/` plus its own template loader; the custom
FastAPI pages use both project and installed SQLAdmin template directories.
Template globals registered by setup: `csrf_token()`, `settings`, `debug`,
`render_analysis`, `related_controls`, `related_inlines`, `related_token`.
Static assets are mounted at `/static`.

Representative overrides: `sqladmin/source_{create,edit,details}.html`,
`sqladmin/bot_scenario_form.html`, `sqladmin/scenario_prompts.html`,
`sqladmin/ai_analytics_detail.html`, `sqladmin/action_confirm.html`,
`sqladmin/llm_{create,edit}.html`, `llm_model/{test_results,test_error}.html`
and `sqladmin/user/` password forms. The separate `/app` Jinja autoescaping
contract does not certify SQLAdmin's independent template environment or URL
safety.

| Module | Responsibility |
| --- | --- |
| `app/admin/setup.py` | Console/auth/authorization setup, shared CSRF manager publication, globals, static mount and 17 categorized views |
| `app/admin/views.py`, `app/admin/view_modules/` | Domain view modules (`users`, `sources`, `collection`, `scenarios`, `analytics`, `llm_catalog`, `operations`, `notifications`), forms, queries and 9 custom actions |
| `app/admin/registry.py`, `table_registry.py` | Owner-only read-only table registry view and metadata-cached schema descriptor |
| `app/admin/related.py`, `inlines.py` | Related-record popup flow, `_ScopedCategoryMenu` category visibility gating, and read-only reverse-FK inline lists |
| `app/admin/role_picker.py`, `catalog.py` | Rank-limited platform-role select on user edit and owner-only role/permission catalog editors |
| `app/admin/identity.py` | Shared token-to-fresh-user operator identity resolver with prefetched role permissions |
| `app/admin/auth.py` | Login/logout, session hygiene, `csrf_session_id` rotation and fresh platform entry |
| `app/admin/authorization.py` | Per-request grants, lazy identity/action catalog and external-route guard |
| `app/admin/scope.py` | Actor/selected-workspace scope, cached view classification and N+1-free membership grant resolution |
| `app/admin/session.py` | Dedicated console session enforcing scope on SQLAdmin ORM statements with per-request `row_criteria_scope` caching |
| `app/admin/secrets.py` | Encrypt-before-write secret sealing (`seal_form_secret`) and edit-form blanking (`hide_form_secret`) |
| `app/admin/base.py`, `formatters.py`, `widgets.py`, `price_fields.py`, `filtered_select.py` | Shared view configuration, display formatting, price fields and transfer widgets |
| `app/admin/actions.py` | Shared two-step action confirmation helpers (`render_action_confirmation`, `verified_action_form`) and LLM probe rendering |
| `app/admin/endpoints.py` | Rate-limited password routes, dashboard and model-test POST routes |
| `app/admin/csrf.py` | Session-bound `CSRFTokenManager`, `AdminCSRFMiddleware`, `issue_token` and `verify_request_token` |

## Access schema and rollout

Platform/workspace access migration procedure: [ACCESS_MIGRATION](ACCESS_MIGRATION.md).
Canonical entity fields and migration state: [MODELS](MODELS.md). Source-pinned
acceptance is indexed in [IMPLEMENTATION_STATUS](IMPLEMENTATION_STATUS.md); it does
not establish a deployed database revision.

## Reusable horizontal relationship picker (P8-T36)

Use the existing SQLAdmin field pipeline, not a custom form or endpoint:

```python
from app.admin.filtered_select import FilteredSelectMultipleField

class ExampleAdmin(BaseAdmin, model=Example):
    form_overrides = {"sources": FilteredSelectMultipleField}
```

`FilteredSelectMultipleField` subclasses `QuerySelectMultipleField`: offered
choices, bound PKs, empty selection and server validation stay with SQLAdmin.
Its `media` uses SQLAdmin's deduplicated `collect_form_media` support on create,
edit and validation-error renders. No template-specific asset tags are needed.
The historical `permission-transfer.js/css` filenames now contain the shared
`FilteredSelect` JS class/UI and scoped role-shell styling, not a second widget.
Dynamic forms may call `FilteredSelect.init(container)`; repeated calls are safe.

- Default `plain` mode displays complete record labels (including punctuation),
  with two-line visual clamping, full title/ARIA labels and full-text search.
  `AgentTaskAdmin.sources` uses it on create/edit; the existing empty-selection
  meaning (all active sources) and task save validation remain unchanged.
- `form_args = {"permissions": {"transfer_mode": "permissions"}}` retains the
  platform role editor's code/name presentation and group filter. The same field
  and JS class drive both views. Owner-only catalog writes/CSRF are unchanged.
- Both panels search independently; double-click or Enter transfers records,
  Space marks them, arrows/Home/End navigate, Ctrl/Cmd+A marks visible enabled
  records. Search never changes submitted values. Reset restores the native
  defaults. Disabled controls fall back to their disabled native select.
- The original multiple select is the only named value source. With JS disabled
  it stays usable. External `change` events and option mutations synchronize
  the enhanced UI, including existing related-record popup updates. Existing
  authorized source create/edit buttons remain below the full-width component.
- Source lists use local scrolling; on narrow mobile forms their panels stack
  with downward/upward transfer icons. The role editor's existing horizontal,
  viewport-height layout remains scoped to its own route.

This is presentation only: it performs no option queries or authorization.
It preserves whatever the existing scoped converter supplies; it does not
replace actor/workspace filtering or grant access to unoffered IDs. P9-T07 owns
pending console-wide actor-scoping work.

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

Historical source pins, sandbox commands, test outcomes and owner handoff are in
[admin implementation history](audit/admin-history.md#related-editor-validation-and-local-handoff).

### Related-editor follow-up: inline icons and Float forms

Follow-up failure diagnosis, source pins, validation counts and owner-reported
closeout are in [admin implementation history](audit/admin-history.md#related-editor-follow-up-inline-icons-and-float-forms).

### Bulk-delete confirmation readability

Project overrides live in `app/static/css/custom.css`, loaded after SQLAdmin's
Tabler CSS through `app/templates/sqladmin/layout.html`; never edit site-packages.
Admin-scoped rules use theme text for neutral/light controls and readable
per-variant foregrounds in normal, hover and active states. Footer-first controls
and submit-input variants retain their button-state tokens under the existing
cascade. Focus styles and theme tokens are preserved. The delete and
custom-confirmation modals retain the theme
surface/text tokens for readable contrast, including muted text.
SQLAdmin inserts the full comma-separated selected IDs via `.text()` into
`#modal-delete-text`. The project sets `overflow-wrap: anywhere`, `white-space:
normal`, a viewport-bounded height and vertical scrolling there; selection and
DELETE URL are not truncated or rewritten. Buttons remain outside that scrolling
region. Custom confirmation bodies use the same wrapping boundary.
See [deletion audit](MODEL_DELETION.md) before changing cascade policies or treating
multi-select deletion as one atomic transaction.

## Universal social task parity (P8-T29)

Task plans follow the shared [task contract](AGENT_TASKS_PLAN.md#explicit-task-plans).
Console forms do not edit collector/runtime evidence or secrets; Run now uses the
standard admission path. Implementation evidence belongs to the task record.

Version-2 `payload.task_plan` uses the same bounded composed research parser:
select `analyze`, matching sources/scenario and trigger, then supply the separate
`pipeline` and `digest.window` fields from the [shared contract](AGENT_TASKS_PLAN.md#composed-research-and-digest-plans-p8-t40).
Run now and schedules generate a digest only. External delivery is a separate
confirmed task action with fresh rights and frozen per-part receipts.
