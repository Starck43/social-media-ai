# CLI Reference

Social Media AI Administration CLI built with Typer.

```bash
python -m cli.main <command> <subcommand> [options]
```

The CLI runs every command as the **platform owner** via `tenant_scope(bypass=True)`,
so it can administer the owner workspace and, with `--tenant`, any client workspace.
Tenant-scoped rows cannot be read or written without a scope.

---

## Top-Level Commands

| Command | Description |
|---|---|
| `schedule` | Manage cron schedules |
| `digest` | Digest operations |
| `credentials` | Manage platform credentials (tenant vault) |
| `roles` | Manage roles and permissions |
| `scenarios` | Manage bot scenarios |

---

## Schedule

Manage cron schedules that drive the scheduler runner.

### List Schedules

```bash
python -m cli.main schedule list
```

Displays all schedules in a table: `id`, `name`, `cron`, `job`, `active`,
`next_run_at`, `last_status`.

### Add Schedule

```bash
python -m cli.main schedule add <name> <cron_expr> <job_type> [options]
```

**Arguments:**
| Arg | Description |
|---|---|
| `name` | Unique schedule name |
| `cron` | Cron expression, e.g. `0 9 * * *` |
| `job_type` | One of: `collect`, `digest`, `prune` — the CLI validates against this list. `analyze`, `learn` and `reflect` are registered handlers (`app/jobs/handlers.py`) but are not accepted here yet, so their schedules have to be inserted into `schedules` directly |

**Options:**
| Option | Default | Description |
|---|---|---|
| `--payload`, `-p` | `{}` | JSON payload, e.g. `'{"source_ids": [1]}'` |

**Examples:**
```bash
# Hourly collection
python -m cli.main schedule add hourly-collect "0 * * * *" collect

# Daily digest at 9 AM
python -m cli.main schedule add daily-digest "0 9 * * *" digest

# Weekly digest on Mondays
python -m cli.main schedule add weekly-digest "0 9 * * 1" digest -p '{"period": "week"}'

# Analyze (writes to bot_actions ledger) — not accepted by the CLI yet;
# insert the row into `schedules` directly until the allowlist is widened.
```

### Remove Schedule

```bash
python -m cli.main schedule remove <name>
```

### Pause / Resume Schedule

```bash
python -m cli.main schedule pause <name>       # pause
python -m cli.main schedule pause <name> --resume  # resume
```

---

## Digest

Build and publish digests manually.

### Send Digest Now

```bash
python -m cli.main digest send-now <period>
```

**Arguments:**
| Arg | Description |
|---|---|
| `period` | `day` or `week` |

**Examples:**
```bash
# Send today's digest
python -m cli.main digest send-now day

# Send weekly digest
python -m cli.main digest send-now week
```

Manual runs are **not idempotent** — useful for testing channel setup without
waiting for a schedule.

---

## Credentials

Manage platform credentials in the per-tenant Fernet-encrypted vault.

### List Credentials

```bash
python -m cli.main credentials list [--tenant <slug_or_id>]
```

Lists all vault credentials. Secrets are **never printed**. Shows `id`, tenant,
platform, kind, label, expiry, and active status.

**Options:**
| Option | Default | Description |
|---|---|---|
| `--tenant` | all | Workspace slug or id (omit for all) |

### Set Credential

```bash
python -m cli.main credentials set <platform> <kind> [options]
```

Stores (or replaces) a credential. The value is encrypted before saving.

**Arguments:**
| Arg | Description |
|---|---|
| `platform` | `vk`, `telegram`, or `max` |
| `kind` | Platform-specific kind (see table below) |

**Options:**
| Option | Default | Description |
|---|---|---|
| `--tenant` | `owner` | Workspace slug or id |
| `--label`, `-l` | `""` | Free-form note |
| `--secret`, `-s` | prompted | Secret value; hidden prompt when omitted |

**Platform/Kind combinations:**

| Platform | Valid Kinds |
|---|---|
| `vk` | `user_token`, `service_token` |
| `telegram` | `bot_token`, `api_id`, `api_hash`, `session` |
| `max` | `bot_token` |

**Examples:**
```bash
# Set VK user token (prompts for secret)
python -m cli.main credentials set vk user_token --tenant owner

# Set VK service token with label
python -m cli.main credentials set vk service_token -l "production" -s "vk_secret_123"

# Set MAX bot token
python -m cli.main credentials set max bot_token -s "max_token_456"
```

### Disable Credential

```bash
python -m cli.main credentials disable <platform> <kind> [--tenant <slug_or_id>]
```

Deactivates the newest matching credential without deleting it.

### Test Credentials

```bash
python -m cli.main credentials test [--tenant <slug_or_id>]
```

Shows where each platform's secret comes from (`vault` | `env` | `missing`)
and whether it works by pinging the platform. Also tests L2 MTProto session
for Telegram.

**Example output:**
```
vk: vault -> ok
telegram: vault -> ok (bot @my_bot)
max: missing
telegram L2 (MTProto): ok
```

### Login (MTProto L2)

```bash
python -m cli.main credentials login telegram [--tenant <slug_or_id>]
```

Interactive login for Telegram MTProto user session (L2 collection layer).
Walks the code + optional 2FA flow and stores `api_id`/`api_hash`/`session`
in the vault. The session string is **never echoed**.

Re-running performs a fresh login and replaces the stored session parts.

---

## Roles

Manage roles and permissions (RBAC).

### List Roles

```bash
python -m cli.main roles list
```

Displays all roles with their permissions in a table.

### Show Role

```bash
python -m cli.main roles show <role_id>
```

Shows detailed information about a specific role: name, code, description,
and full permission list.

### Assign Default Permissions

```bash
python -m cli.main roles preset
```

Assigns default permissions to all roles based on hierarchy.

### Update Role Permissions

```bash
python -m cli.main roles update <role> <pattern...> [options]
```

Updates permissions for a role using patterns and specified strategy.

**Arguments:**
| Arg | Description |
|---|---|
| `role` | Role name (case-insensitive) |
| `pattern...` | Permission patterns with wildcards and exclusions |

**Pattern Examples:**
| Pattern | Meaning |
|---|---|
| `social.post.view` | Specific permission |
| `posts.*` | All permissions for posts |
| `*.view` | All view permissions |
| `!posts.delete` | Exclude delete permission |
| `posts.* !posts.delete` | All post permissions except delete |

**Options:**
| Option | Default | Description |
|---|---|---|
| `--strategy`, `-s` | `replace` | `replace` \| `merge` \| `synchronize` \| `update_actions` |
| `--dry-run` | `false` | Show what would be changed without making changes |

**Examples:**
```bash
# Replace all permissions for moderator role
python -m cli.main roles update moderator posts.* comments.*

# Merge: add new permissions to existing
python -m cli.main roles update moderator analytics.view -s merge

# Dry run to preview changes
python -m cli.main roles update moderator posts.* !posts.delete -s replace --dry-run
```

---

## Scenarios

Manage bot scenarios (analysis templates for sources).

### List Scenarios

```bash
python -m cli.main scenarios list [--tenant <slug_or_id>]
```

Displays all scenarios for a workspace in a table: `id`, `name`, `is_default`, `is_active`, `analysis_types`, `content_types`.

### Seed Default Scenario

```bash
python -m cli.main scenarios seed-default [--tenant <slug_or_id>] [--force]
```

Creates a default "Базовый мониторинг" scenario for the workspace if none exists.
The scenario includes sentiment + keywords analysis, TIME_BASED trigger, and uses the
workspace's default LLM model.

Use `--force` to replace an existing default scenario.

**Example:**
```bash
# Seed default scenario for the owner workspace
python -m cli.main scenarios seed-default --tenant owner

# Replace existing default
python -m cli.main scenarios seed-default --tenant owner --force
```

---

## Architecture Notes

### How CLI Commands Run

Every CLI command goes through `_run_platform()`, which wraps execution in
`tenant_scope(bypass=True)`. This means:

1. The CLI always has superuser-level access to all tenant data.
2. Tenant-scoped operations (credentials, schedules, etc.) work without
   explicitly setting a tenant context.
3. The `--tenant` option is used to scope operations to a specific workspace
   when needed.

### Async Execution

CLI commands that interact with the database run async code via:

```python
def _run(coro):
    from app.core.tenant_context import tenant_scope
    with tenant_scope(bypass=True):
        return asyncio.run(coro)
```

### Rich Output

The CLI uses the `rich` library for formatted tables and colored output.
No JSON output — the CLI is operator-facing, not machine-facing.
