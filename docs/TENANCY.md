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
   participant with a `role` (`owner` | `member`).
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
| `/api/*` | bearer JWT (required, 401 otherwise) | caller's membership; headers select, never widen | `app/api/deps.py` — model rights / role ladder, declared per endpoint (see `docs/API.md`) |
| `/app/*` | web session | caller's membership (`TenantUIMiddleware`) | the membership is the boundary; no per-model right in the client UI |
| `/admin/*` (sqladmin) | admin backend | bypass — operator console | per-model rights, Django-style (`docs/ADMIN.md`) |
| CLI | none (developer tool) | bypass; `--tenant` opts into one workspace | developer surface, no rights check |
| worker / scheduler | — | from `job.tenant_id` / `task.tenant_id` | jobs run as the workspace they belong to |

Rights come from the platform role (`role_permission` → `permissions`) and are
read through `User.model_permissions()`; every surface that needs them uses that
one predicate, so they cannot drift apart.

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

## Validation status

- **Migration**: `0052` is applied; the database is on revision `0052` (head) and
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