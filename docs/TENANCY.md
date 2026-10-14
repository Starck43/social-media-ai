# Multi-Tenancy

The platform uses a **shared bot** model: one Telegram/MAX bot serves every
client workspace. A chat is bound to exactly one tenant, and every data row
(analytics, schedules, jobs, agent history, digests) belongs to a tenant.

## How it works

1. **Bootstrap workspace** (`owner`): a single tenant that owns all pre-existing
   (legacy) rows. The platform owner's chat is onboarded here on first contact.
2. **Invite codes** bring client chats into their own workspaces (`/start
   <code>`). Codes are SHA-256 hashed in the DB; the plaintext is shown once.
3. **Membership** is `(tenant_id, channel, external_user_id)` — a chat
   participant with a platform role assigned via `role_id` (FK to `roles.id`).
   Roles are defined by `Role.codename` (enum `user_role_type`): `VIEWER`,
   `AI_BOT`, `MANAGER`, `ANALYST`, `MODERATOR`, `ADMIN`, `SUPERUSER`.
   Only an explicitly resolved `SUPERUSER` membership grants ownership. A legacy
   row with `role_id = NULL` has no workspace grants and is not an owner in the
   web UI, API or runtime; its role must be explicitly assigned before access
   is restored. Runtime owner authority also requires an active bound User.
   No migration, automatic role repair or identity linking is included.
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
| `/admin/*` (sqladmin) | admin backend | bypass — operator console | per-model rights via `User.has_perm_for()` (see `docs/ADMIN.md`) |
| CLI | none (developer tool) | bypass; `--tenant` opts into one workspace | developer surface, no rights check |
| worker / scheduler | — | from `job.tenant_id` / `task.tenant_id` | jobs run as the workspace they belong to |
| agent chat (Telegram/MAX/web) | inbound resolution | `resolve_inbound()` → tenant | tools check `required_permission` via `has_permission()` in `permission_scope()`; writes require confirmation |

Non-owner workspace-model rights come from the active membership role via
`member_decision()` in its tenant/user-bound scope. Global model/admin rights use
the platform role and `User.has_perm_for(model_name, action)`; both use structured
`role_permission` → `permissions.model_type_id` + `action_type`. Core owner
overrides cover source/agenttask/agentscenario only; web settings use their own
concrete-target management capability, not a platform privilege grant. The check
uses structured columns (`model_type_id` + `action_type`), not codename strings,
so changing a role's codename never breaks permission logic.

### Current bounded fail-closed permission foundation

The integrated identity/permission foundation enforces anonymous denial and a tenant-bound
owner override only for source/agenttask/agentscenario. Global fleet/role/queue
rights require platform permissions. Web settings use a separate active-User /
web-membership / concrete-target capability for the current workspace; this
does not grant tenant.update in core/model checks. API identity reaches manager
gates; exact service grants cover default task creation, one-shot disarming and collection
watermarks. Trusted CLI authority is explicit. Merged foundations and their original evidence are recorded in
[IMPLEMENTATION_STATUS](IMPLEMENTATION_STATUS.md); complete
[business readiness gates](BUSINESS_PRODUCTION_READINESS.md) remain open.
The later `fdb6c18` follow-up removes NULL-role ownership inference without data
repair; D separately adds fresh web-chat ownership checks. Do not treat confirmation as authorization.

## Roles and permissions

Platform roles are stored in the `roles` table (`settings.DB_SCHEMA.roles`).
Each role has a `codename` (enum `user_role_type`) and a set of permissions
(many-to-many via `role_permission` → `permissions`).

### Role codenames

| Codename | Purpose |
|---|---|
| `SUPERUSER` | Membership: explicit workspace owner; User platform role: operator. A membership does not grant global platform rights |
| `ADMIN` | Workspace administrator |
| `MODERATOR` | Content moderation |
| `ANALYST` | Read-only analytics access |
| `MANAGER` | Content management (sources, tasks, scenarios) |
| `AI_BOT` | Automated agent identity |
| `VIEWER` | Read-only workspace member |

### Structured permissions

Permissions are stored as pairs:
- `permissions.model_type_id` → `model_types.model_name` (e.g., `source`, `agenttask`, `tenant`)
- `permissions.action_type` → `ActionType` enum (`VIEW`, `CREATE`, `UPDATE`, `DELETE`, `EXPORT`, `CONFIGURE`)

`User.has_perm_for(model_name, action)` checks the platform role's pair;
`member_decision()` substitutes active membership grants for ordinary workspace
models. Entry/target capabilities and owner exceptions are enforced separately:

- `/api/*` — `app/api/deps.py`
- `/app/*` — `app/web/perms.py` (`WebPerms.can()`)
- `/admin/*` — `app/admin/authorization.py`
- Agent tools — `app/agent/runtime.py` (`has_permission_by_codename()` in `permission_scope()`)
- CLI/worker — `app/core/permissions.py` (`is_bypass()` skips checks)

### Legacy compatibility

Before migration 0080, `tenant_users.role` was a string column (`"owner"` / `"member"`).
Migration 0080 replaced it with `tenant_users.role_id` (FK to `roles.id`).
Historical NULL-role owner inference was removed in `fdb6c18`: unresolved rows
are not owners and receive no workspace grants. Explicit role assignment is
required to restore access; no automatic data repair or migration is performed.
The later `2dd3161` follow-up also makes `member_roles.key_of()` return no level
for a NULL role, removing the legacy owner-label inference. This aligns role
labels with fail-closed authorization without changing stored memberships.

Role string resolution helpers:
- `_resolve_role_codename("owner")` → `"SUPERUSER"`
- `_resolve_role_codename("member")` → `"VIEWER"`

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
database. Follow `.agent/rules.md`: run at most one database test owner at a time
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

The web shell labels each membership as an owned workspace or granted access,
with the active role visible beside the page context. Ownership follows the
existing membership owner contract, not the creator's identity. Workspace data
remains tenant-scoped: switching changes context; it does not merge the caller's
resources from another workspace. Ordinary member model rights come from the
role assigned to that membership, not from object authorship or a new allowlist.
Platform-operator overrides remain separate from ordinary member access.

Web chat is owner-only in the active workspace. Non-owner members, including
platform operators visiting as a non-owner, have no chat navigation or home
entry; direct transcript, send and delete routes refuse them. Runtime admission
reloads the existing RuntimeIdentity and refuses non-owner/revoked web identities
before starting the authorized turn. Messenger policy is unchanged. Web and
runtime ownership both require an explicitly resolved membership owner role.

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

Admin entry is open to active authenticated accounts, independently of workspace
membership. Per-model/action **platform** permissions remain authoritative there;
the console is not the tenant-scoped dashboard. See [ADMIN](ADMIN.md).
