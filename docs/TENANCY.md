# Multi-Tenancy

The platform uses a **shared bot** model: one Telegram/MAX bot serves every
client workspace. A chat is bound to exactly one tenant, and every data row
(analytics, schedules, jobs, agent history, digests) belongs to a tenant.

## How it works

1. **Bootstrap workspace** (`owner`): a single tenant that owns all pre-existing
   (legacy) rows. The platform owner's chat is onboarded here on first contact.
2. **Invite codes** bring client chats into their own workspaces (`/start
   <code>`). Codes are SHA-256 hashed in the DB; the plaintext is shown once.
3. **Membership** binds a user or messenger identity to one workspace and an
   independent `WorkspaceRole` (`owner`, `admin`, `manager`, `analyst`, `viewer`).
   Platform authority belongs to the account's `PlatformRole` (`user`, `operator`,
   `admin`) and never widens workspace rights in web or client APIs. Unresolved
   memberships fail closed. AI chat is separately enabled per member; owners have
   effective access by default. Runtime authority requires an active bound User.
4. **Enforcement** lives in `BaseManager`/`QuerySet`, not in models:
   `TenantScopedMixin` just marks the model. Every SELECT is filtered to
   `current_tenant_id()`, every `create()` stamps `tenant_id`; `update_by_id`
   / `delete_by_id` re-fetch through the scoped queryset, so a cross-tenant id
   silently becomes "not found".

## Plans and quotas

`tenants.plan` is the workspace's billing tier. It is a `CHECK`-constrained
column with three values — `starter`, `pro`, `business` — and it is the one
place that decides what a workspace may do:

| | `starter` | `pro` (default) | `business` |
| --- | --- | --- | --- |
| Sources | 3 | 20 | ∞ |
| LLM budget / day | $2 | $20 | operator-set |
| Delivery channels | 1 | 5 | ∞ |
| Agent scenarios | 1 | 10 | ∞ |
| Team seats (`/app`) | 1 | 5 | ∞ |
| Scheduled tasks | 3 | 20 | ∞ |
| Data retention | 7 days | 30 days | 90 days |
| Auto-comments | dry-run only | auto | auto |
| Agent learning / reflection | — | ✅ | ✅ |
| LLM model types | text | text + image + embedding | text + image + embedding |

The numbers live in `Tenant.PLAN_LIMITS` (`app/models/tenant.py`) and are
enforced by `app/services/tenancy/limits.py`, which every write path that can
consume one calls. A quota check returns a **message**, not a boolean, so a
refusal names the tier and the count rather than saying "limit reached".

Three rules are worth knowing because they are not obvious from the table:

* **A tier change applies immediately.** There is no cached copy of the limits.
  Lowering a workspace to `starter` refuses the next source add.
* **A column may tighten a tier, never raise it.** `max_sources` and
  `daily_cost_limit` remain columns so a workspace can set a tighter budget of
  its own; `effective_limits()` takes whichever is *lower*. An unlimited tier
  stays unlimited — those columns are `NOT NULL` and defaulting to 20 would
  otherwise silently cap `business`.
* **Downgrading is allowed and reports the overage.** Refusing would trap a
  customer who must downgrade *because* they are over budget, so
  `limits.plan_overage()` returns what has to be deleted instead.

**Team seats count `/app` memberships, not chat participants.** `tenant_users`
holds both a web membership and a messenger identity; billing a plan for both
would charge the owner for talking to their own agent, which is the product.
The same applies to `tenant_channels`: binding a chat your workspace already
owns is not a second channel.

Only a platform superuser can change a tier (`POST /app/settings/plan`, or the
admin console) — it is a billing act, not a workspace preference.

## Tables

| Table | Scope | Notes |
| --- | --- | --- |
| `tenants` | global | Workspace metadata: slug, plan (tier), daily_cost_limit, etc. |
| `tenant_users` | global | Membership: which user belongs to which workspace. |
| `tenant_invites` | global | Invite codes (hashed); `redeem()` checks expiry/uses. |
| `tenant_channels` | global | Chat→tenant binding; unique per `(channel, chat_id)`. |
| `user_credentials` | global | Personal secrets (L2 user tokens / sessions), keyed by `users.id`, encrypted at rest with Fernet. |
| `sources`, `agent_tasks`, `jobs`, `digest_runs`, `ai_analytics`, `agent_scenarios`, `bot_actions`, `notifications`, `agent_sessions`, `agent_messages`, `agent_feedback`, `agent_memory` | tenant | Every `TenantScopedMixin` model. |

**Global tables** (never tenant-scoped): `Platform`, `LLMProvider`, `LLMModel`,
`ModelType`, `Permission`, `Role`, `User`, `UserCredential` — plus the tenancy
tables themselves (because they are resolved *before* a tenant context exists).

## Routing

`app/services/tenancy/resolver.py::resolve_inbound()` decides which workspace a
message belongs to:

1. Is this chat already bound (`tenant_channels`)? → use that tenant.
2. Does the message look like an invite code (`/start ABCD-2345`)? → redeem it, bind the chat.
3. Is the sender a platform owner (env allowlist)? → bootstrap them into the `owner` workspace.
4. Otherwise → silently drop the message (the bot must not reveal its existence to strangers).

The agent loop wraps the entire turn in `tenant_scope(tenant_id)`. Tenant data
isolation is NOT user authorization. The merged PR #22 boundary additionally admits a bound
active RuntimeIdentity, eager-loads User rights, wraps the whole turn in
permission_scope and reloads authority before tool effects/confirmation. Missing
User bindings refuse runtime access rather than inheriting resolver owner flags.
Actor/session/role/argument-bound consent is in-process, not durable DB CAS.
See [business readiness](BUSINESS_PRODUCTION_READINESS.md) for the remaining authorization and approval acceptance gates.
PR #22 is merged; its bounded safeguards do not close the wider identity and
durable-approval gate. No personal multi-workspace router is added.

## Bypass

```python
from app.core.tenant_context import tenant_scope

# Superuser reads/writes (CLI, admin, job reaper)
with tenant_scope(bypass=True):
    ...
```

The operator console runs with bypass: sqladmin via `PlatformScopeMiddleware`, the
CLI via `cli/main.py::_run_platform`, and the job claim/reaper paths (a worker
must be able to pick up any workspace's job).

It is **not** the client API. `/api/*` is a client surface: `ApiScopeMiddleware`
requires a bearer token, resolves the caller's workspace from their `tenant_users`
web memberships, and runs the whole request inside `tenant_scope(tenant_id)`.
`X-Tenant-Id` / `X-Tenant-Slug` only *select* among the caller's own workspaces —
a foreign one is refused with 403, never honoured.

| Surface | Auth | Workspace | Rights |
| --- | --- | --- | --- |
| `/api/*` | bearer JWT (required, 401 otherwise) | caller's membership; headers select, never widen | `member_decision()` for non-owner workspace models; platform predicate otherwise; declared per endpoint (see `docs/API.md`) |
| `/app/*` | web session | caller's membership (`TenantUIMiddleware`) | `WebPerms.can()` — effective membership/platform rights and bounded owner exceptions; `guard_web()` enforces routes |
| `/admin/*` (sqladmin) | admin backend | bypass — operator console | active operator/admin entry, then platform model/action rights (see `docs/ADMIN.md`) |
| CLI | none (developer tool) | bypass; `--tenant` opts into one workspace | developer surface, no rights check |
| worker / scheduler | — | from `job.tenant_id` / `task.tenant_id` | jobs run as the workspace they belong to |
| agent chat (Telegram/MAX/web) | inbound resolution | `resolve_inbound()` → tenant | tools check `required_permission` via `has_permission()` in `permission_scope()`; writes require confirmation |

## Roles and permissions

Current source implements independent fixed catalogs. Migration **0096 is
prepared, not automatically applied**; see [cutover instructions](ACCESS_MIGRATION.md).
Earlier migration/commit evidence below describes historical foundations.

| Context | Roles | Authority |
|---|---|---|
| Account/platform | `user`, `operator`, `admin` | Ordinary account; configured operator grants; full platform administration |
| Each workspace membership | `owner`, `admin`, `manager`, `analyst`, `viewer` | Explicit workspace presets, independent of the account role |

There is no role ordering, `UserRoleType`, account `is_superuser` field or
editable role-name synonym in the target schema. Codenames are checked strings;
labels come from one presentation catalog. Role descriptions are plain text.
Only platform admins assign platform roles or configure operator grants.
Workspace role creation/editing is not exposed.

Workspace owners/admins manage settings and the team. An admin may manage only
manager/analyst/viewer members and invitations; only owners assign admins/owners.
Team changes lock the workspace row and reload the active actor/membership.
The final active bound owner cannot be demoted. Owner disabling is refused.
Unknown roles have no access and require explicit assignment by an owner.

Permissions use registered `domain.resource.action` keys, such as
`analysis.result.view`, `collection.source.update`, `llm.provider.configure`.
`ModelType.resource_key` plus `Permission.action_type` is the unique authority
identity; `Permission.codename` is derived for display/API. ORM class names and
UI pages do not define domains. Exact legacy three-part aliases are accepted for
one release; wildcards and guessed domains are rejected. A model does not
implicitly receive every action when registered.

The shared core policy checks active workspace grants with a user/tenant-bound
context. Web and client API never inherit platform privileges, including for a
platform admin. Admin and explicit operator API paths require active operator
entry and platform action grants; a global data scope alone grants no action.
Personal credential endpoints additionally constrain `user_id` to their caller.
The web queue is scoped to the current membership and requires job permissions;
platform status does not open it. Global queue inspection is a read-only admin
view with explicit `scheduling.job.view` rights and no payload/error projection.
Trusted CLI/worker service contexts remain explicit and are cleared by nested
interactive permission scopes.

AI chat requires effective membership AI access. Every tool retains the member's
ordinary rights and confirmation controls. Fresh identity binds the workspace
role and AI grant to pending consent; revocation invalidates it. Conversations,
facts, preferences, reflection feedback and learning watermarks are personal to
user plus workspace. Only the current workspace's owners may read the NULL-user
legacy memory bucket. Jobs learn from the user's own web or explicitly bound
private messenger sessions; group transcripts are excluded. Existing cost and
plan limits remain workspace-wide.

## Config

| Variable | Default | Purpose |
| --- | --- | --- |
| `DEFAULT_TENANT_SLUG` | `owner` | Bootstrap workspace slug in `r`untime |
| `CREDENTIALS_KEY` | — | Fernet key for encrypting personal (`user_credentials`) secrets |

Generate `CREDENTIALS_KEY`:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Migration

`migrations/versions/0044_add_tenancy_core.py`:
- Creates tenancy core tables (`tenants`, `tenant_users`, `tenant_invites`, `tenant_channels`, `tenant_credentials`).
- Adds `tenant_id` to every business table (10 models).
- Backfills with the bootstrap tenant id — all existing rows belong to `owner`.
- Rewrites global unique constraints to per-tenant: `uq_source_platform_external` → `uq_source_tenant_platform_external`, `schedules_name_key` → `uq_schedule_tenant_name`, `uq_agent_memory_scope_key` → `uq_agent_memory_tenant_scope_key`.

Downgrade drops everything and restores the global constraints — only safe on a
single-workspace (owner-only) database.

`migrations/versions/0065_add_user_credentials_personal_l2_vault.py` adds the
personal vault (`user_credentials`, keyed by `users.id`, not `tenants.id`);
`0066_drop_tenant_credentials.py` drops the old workspace vault: personal L2
secrets now live in `user_credentials` and app/bot config (VK app/token, bot
tokens) moves to the environment. See [COLLECTION.md](./COLLECTION.md).

Migration `0080_replace_tenant_role_string_with_role_id.py` replaced the legacy
`tenant_users.role` string column with `tenant_users.role_id` (nullable FK to
`roles.id`). The migration:
- Backfilled `role_id` from string values (`"owner"` → SUPERUSER, `"member"` → VIEWER)
- Made `tenant_users.role_id` nullable (NULL = unresolved/legacy)
- Updated all managers (`add_member`, `add_web_member`, `issue`, `redeem`, `redeem_web`) to use `role_id`
- Historically updated owner helpers for SUPERUSER/NULL compatibility; the later
  `fdb6c18` follow-up supersedes NULL ownership. Current authorization requires
  an explicitly resolved SUPERUSER membership.

Migration `0081_add_ai_analytics_chain_label.py` adds a nullable `chain_label`
column to `ai_analytics` — a human-readable label for topic chains so grouping
can be done at query time without deriving it from `summary_data`.

Migration `0082_collected_items_nonpartial_unique_index.py` replaces the partial
unique index on `collected_items` (`WHERE external_id IS NOT NULL`) with a regular
unique index. This makes the index usable by `ON CONFLICT DO NOTHING` in
`CollectedItemManager.store_items` without changing runtime behaviour.

Migration `0083_drop_analyze_type_from_agent_scenarios.py` drops the old
`analyze_type` enum column from `agent_scenarios`; grouping axes
(`group_by` + `time_breakdown`) are now query-time parameters, not scenario
properties.

## Validation boundary

At audit baseline `c62f948`, migration definitions extend through `0095`
(`0095_add_llm_budget_reservations.py`); those budget tables remain UNWIRED. A repository head does not establish
the revision of any running database. Check a target explicitly before upgrade or
rollback, and use the schema resolved from `settings.DB_SCHEMA`.

Tests default to the shared `DB_TEST_SCHEMA=test_schema` on the configured test
database. Follow `docs/RULES.md`: run at most one database test owner at a time
and never create, reset, drop, stamp or migrate a schema without explicit owner
authorization. A historical test count is not current evidence.

For a tenancy change, verify at least unknown identity, missing membership,
revoked/disabled membership, cross-workspace read/write, manager update/delete,
channel rebind and transaction rollback behavior. Broaden to the relevant API,
web, agent and job entry points when the shared authorization contract changes.

### Web settings and owned/granted workspace context

Merged [D #191](https://github.com/Starck43/social-media-ai/pull/191) resolves the
permitted selected tab before querying. Hidden tabs do not preload their data;
unknown or forbidden bookmarks fall back to a permitted tab. GET is no-store.

| Tab/scope | Current read/action boundary |
| --- | --- |
| My account | Active authenticated caller's read-only profile and browser notification display preferences; theme remains in the shell. The exact GET/HEAD Settings page remains reachable without membership, showing this tab only. |
| My connections | Caller-only credential metadata/status, with existing guarded connect/OAuth writes unchanged. No other member's credentials or secrets; existing connection routes still require onboarding. |
| Workspace | Current active membership may read name/timezone only. Full configuration, budget and quota projections require `can_manage_workspace(concrete target)`; individual counts also retain model VIEW gates. |
| Team | Same concrete-target management capability; only selected-tab member queries. Roles and disable controls retain server checks. Invitations use the shared guarded lazy dialog, not eager team-page queries. |
| Agent / delivery channels | Concrete-target management capability before queries/mutations. Existing platform model catalogue is read only for the permitted Agent tab; this grants no global provider/model edit privilege. |
| Admin | Global platform model/action rights, separate from all workspace tabs. |

Notification display preferences remain browser-local (see
[NOTIFICATIONS](NOTIFICATIONS.md#current-web-bell-and-display-policy)). No fake
profile-write flow, durable preference field or permission matrix was added.

### Web owner/guest context and membership disable

Settings role changes apply only to the target membership in the active workspace,
never to the account's platform role. A caller cannot change their own workspace
role, even if another active owner exists: both the web handler and the locked
`TenantUserManager` mutation reject it. The Team UI exposes access controls only
for other non-owner members. Existing checks on managing other members and the
last-owner invariant remain in place.

The settings admin-console entry uses the same `has_admin_access()` predicate as
the shell and console: an active platform `operator` or `admin` may see it.
Workspace ownership or workspace-admin status alone does not grant entry.


The web shell labels each membership as an owned workspace or granted access,
with the active role visible beside the page context. Ownership follows the
existing membership owner contract, not the creator's identity. Workspace data
remains tenant-scoped: switching changes context; it does not merge the caller's
resources from another workspace. Ordinary member model rights come from the
role assigned to that membership, not from object authorship or a new allowlist.
Platform-operator overrides remain separate from ordinary member access.

Web chat is available to active owners and members with `ai_chat_enabled`.
Navigation, transcript/send/delete routes and runtime admission enforce the same
boundary. Platform operator status alone never opens a workspace conversation.
History and message deletion are confined to the actor's own session.

Settings Team exposes `POST /app/settings/members/{id}/disable` only with the
existing concrete workspace-management capability. CSRF is required. The manager
conditionally updates an active, bound web membership in that workspace to
`is_active=False`; it retains the row and personal credentials. Explicit owners
are protected from this action even when more than one owner exists: transfer
ownership and change the role first. Unresolved NULL-role rows are also
conservatively refused by this endpoint until their role is assigned; that
protection grants them no workspace access. The role predicate
is part of the UPDATE, not a separate stale read. Subsequent web/API membership
resolution excludes the disabled row. Existing sessions and unrelated workspace
memberships are not deleted. Invite/session races, in-flight requests and real
role/revocation/tenant acceptance remain environment-dependent verification.

Admin entry requires an active platform operator/admin account, independently of
workspace membership. Model/action grants remain authoritative in the global
console. See [ADMIN](ADMIN.md).

## Personal LLM catalog (0109 source contract; deployment pending)

Providers/models are user-owned, not global. `owner_user_id` and a composite
model/provider owner FK constrain ownership. Personal CRUD/defaults require an
active caller and are shared across that caller's workspaces. UI lives at
Settings → Models; social Platform remains a separate global directory.
`Tenant.agent_model_id` is a numeric FK, not a model-name lookup. An explicit
unavailable chat model fails closed; automatic fallback cannot use another
user's keys for any capability — text, image, video and audio resolution and
the analyzer's failover queue all stay inside one personal catalog. Scheduled execution uses the stored task payer's personal catalog;
missing consent never guesses a workspace owner. Platform inspection permission
does not grant another user's key use.

Historical global-fleet descriptions above describe pre-0109 behavior. Migration
definition and required cutover checks: [personal ownership evidence](validation/personal-llm-ownership.md).
