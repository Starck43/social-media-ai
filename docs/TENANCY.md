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

## Tables

| Table | Scope | Notes |
| --- | --- | --- |
| `tenants` | global | Workspace metadata: slug, plan, daily_cost_limit, etc. |
| `tenant_users` | global | Membership: which user belongs to which workspace. |
| `tenant_invites` | global | Invite codes (hashed); `redeem()` checks expiry/uses. |
| `tenant_channels` | global | Chat→tenant binding; unique per `(channel, chat_id)`. |
| `tenant_credentials` | global | Per-tenant secrets, encrypted at rest with Fernet. |
| `sources`, `schedules`, `jobs`, `digest_runs`, `ai_analytics`, `bot_scenarios`, `notifications`, `agent_sessions`, `agent_messages`, `agent_memory` | tenant | Every `TenantScopedMixin` model. |

**Global tables** (never tenant-scoped): `Platform`, `LLMProvider`, `LLMModel`,
`ModelType`, `Permission`, `Role`, `User` — plus the tenancy tables themselves
(because they are resolved *before* a tenant context exists).

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

The HTTP surface (`/api/v1`, sqladmin) runs with bypass via
`PlatformScopeMiddleware` — it is the operator's console, not a client API.

## Config

| Variable | Default | Purpose |
| --- | --- | --- |
| `DEFAULT_TENANT_SLUG` | `owner` | Bootstrap workspace slug in `r`untime |
| `CREDENTIALS_KEY` | — | Fernet key for encrypting `tenant_credentials` secrets |

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

## Validation status

- **Migration**: `0044` is applied; the database is on revision `0044` (head) and
  `alembic check` reports **no new upgrade operations**.
- **Isolation**: proven by both code review **and** integration tests
  (`tests/test_tenancy.py`, 8 tests, real PostgreSQL, `@pytest.mark.tenancy`):
  isolated rows across tenants, cross-tenant `update_by_id`/`delete_by_id` → noop,
  missing tenant context → `TenantContextError`, cross-tenant `create` rejected,
  channel rebind rejected, CASCADE cleanup isolated per tenant.
- **Full pytest is not a green gate** because of baseline issues unrelated to
  tenancy: `tests/test_base_manager.py` errors on a missing `aiosqlite`
  (test-only dependency), a stale enum tuple breaks
  `test_get_source_analytics_returns_new_fields`, and
  `tests/test_vk_collection.py` performs real network calls to VK and must be
  excluded from regular CI runs. None of these are covered by tenancy changes.
- **Formatting**: `app/models/source.py` is legacy tab-indented and is
  intentionally **not** reformatted (black would rewrite a whole legacy file);
  excluded as a documented baseline decision.