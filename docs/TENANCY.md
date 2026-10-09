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
   `SUPERUSER` is the canonical "owner" role; legacy rows with `role_id = NULL`
   are treated as owners for backward compatibility.
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

The agent loop wraps the entire turn in `tenant_scope(tenant_id)`. Tools,
managers, and the job dispatcher then see only one workspace's data.

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
| `/api/*` | bearer JWT (required, 401 otherwise) | caller's membership; headers select, never widen | `User.has_perm_for(model_name, action)` — structured `model_type_id` + `action_type`, declared per endpoint (see `docs/API.md`) |
| `/app/*` | web session | caller's membership (`TenantUIMiddleware`) | `WebPerms.can()` — owner bypass + `User.has_perm_for()`; gated by `guard_web()` |
| `/admin/*` (sqladmin) | admin backend | bypass — operator console | per-model rights via `User.has_perm_for()` (see `docs/ADMIN.md`) |
| CLI | none (developer tool) | bypass; `--tenant` opts into one workspace | developer surface, no rights check |
| worker / scheduler | — | from `job.tenant_id` / `task.tenant_id` | jobs run as the workspace they belong to |
| agent chat (Telegram/MAX/web) | inbound resolution | `resolve_inbound()` → tenant | tools check `required_permission` via `has_permission()` in `permission_scope()`; writes require confirmation |

Rights come from the platform role (`role_permission` → `permissions.model_type_id` + `action_type`, never the stored codename) and are
read through `User.has_perm_for(model_name, action)` — one predicate shared
across all surfaces (`/api`, `/app`, `/admin`, agent tools, CLI). The check
uses structured columns (`model_type_id` + `action_type`), not codename strings,
so changing a role's codename never breaks permission logic.

### Prepared fail-closed permission follow-up

`ai/identity-permissions-boundary` prepares anonymous denial and a tenant-bound
owner override only for source/agenttask/agentscenario. Global fleet/role/queue
rights require platform permissions. API identity reaches manager gates; exact
service grants cover default task creation, one-shot disarming and collection
watermarks. Trusted CLI authority is explicit. Not merged or accepted; see the
[matrix, bypass map and compatibility gates](design/identity_permissions_handoff.md).
The existing legacy membership-owner inference and runtime identity loading are
not migrated by this bounded package. Do not treat confirmation as authorization.

## Roles and permissions

Platform roles are stored in the `roles` table (`settings.DB_SCHEMA.roles`).
Each role has a `codename` (enum `user_role_type`) and a set of permissions
(many-to-many via `role_permission` → `permissions`).

### Role codenames

| Codename | Purpose |
|---|---|
| `SUPERUSER` | Workspace owner / platform operator — full access, all actions |
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

The single predicate `User.has_perm_for(model_name, action)` checks whether the
user's role grants the required pair. This is the same check used by:
- `/api/*` — `app/api/deps.py`
- `/app/*` — `app/web/perms.py` (`WebPerms.can()`)
- `/admin/*` — `app/admin/authorization.py`
- Agent tools — `app/agent/runtime.py` (`has_permission_by_codename()` in `permission_scope()`)
- CLI/worker — `app/core/permissions.py` (`is_bypass()` skips checks)

### Legacy compatibility

Before migration 0080, `tenant_users.role` was a string column (`"owner"` / `"member"`).
Migration 0080 replaced it with `tenant_users.role_id` (FK to `roles.id`).
Legacy rows with `role_id = NULL` are treated as owners (`is_owner = True`) for
backward compatibility.

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
- Updated `Resolution.is_owner` and `TenantUser.is_owner` to check `role.codename == "SUPERUSER"` or `role_id = NULL`

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

## Validation status

- **Migration**: `0083` is applied; the database is on revision `0083` (head) and
  `alembic check` reports **no new upgrade operations** when run against the
  working (production) schema.
- **`alembic check` and the test schema**: models read `settings.DB_SCHEMA`,
  which the pytest suite sets to `DB_TEST_SCHEMA` (default `test_schema`). If you
  run `alembic check` while the env has `DB_TEST_SCHEMA` set, the model metadata
  points at `test_schema` while the live tables live in `public` — so alembic
  reports spurious index/table drops. That diff is a schema mismatch, not real
  schema drift. Run `alembic check`/`alembic revision --autogenerate` with
  `DB_SCHEMA=<target schema>` (default `public`) explicitly set to avoid it.
- **Historical migrations**: the older files in `migrations/versions/` hardcode
  the `social_manager` schema name. They are intentionally left untouched: they only
  apply during `alembic upgrade`, and the database is already on `.head`. New
  migrations and the models read `settings.DB_SCHEMA`; only future migrations
  need to keep using it. Rewriting the historical files would add regression risk
  with no benefit.
- **Isolation**: proven by both code review **and** integration tests
  (`tests/test_tenancy.py`, 8 tests, real PostgreSQL, `@pytest.mark.tenancy`):
  isolated rows across tenants, cross-tenant `update_by_id`/`delete_by_id` → noop,
  missing tenant context → `TenantContextError`, cross-tenant `create` rejected,
  channel rebind rejected, CASCADE cleanup isolated per tenant.
- **Full pytest is a green gate**: 255 tests pass. `tests/test_vk_collection.py`
  performs real network calls to VK and is the slow one (~40 s of the run).
  The suite runs against its own database (`TEST_POSTGRES_URL`, created and
  seeded by `scripts/setup_test_db.py`), never the working one, so a run cannot
  write into real data.
- **Formatting**: `app/models/source.py` is legacy tab-indented and is
  intentionally **not** reformatted (black would rewrite a whole legacy file);
  excluded as a documented baseline decision.
