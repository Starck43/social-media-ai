# CLI Reference

Social Media AI Administration CLI built with Typer.

```bash
python -m cli.main <command> <subcommand> [options]
```

The CLI is the **developer console**: it runs every command with
`tenant_scope(bypass=True)`, so it reads and writes across all workspaces and a
create without `--tenant` lands in the bootstrap workspace. End users never
reach this path — API and web requests are pinned to their own workspace.

Commands that must act *inside* one workspace take `--tenant <slug|id>` and
switch the scope on for that block (`task add`, one-off `task <job_type>`, `credentials`,
`scenarios`). A task's own workspace always wins over any flag: `task <job_type> --task
<id>` uses the workspace stored on the task row.

Output that spans workspaces names them: `task list` has a `workspace` column and
`task remove` reports every workspace it deleted from.

---

## Top-Level Commands

| Command | Description |
|---|---|
| `collect` | Run manual content collection & analysis (debug/analyst tool) |
| `analyze` | Run the analyze handler directly (triggers + bot actions) |
| `digest` | Digest operations |
| `prune` | Trim old finished jobs directly |
| `learn` | Extract durable facts from chat into memory directly |
| `reflect` | Weekly memory hygiene + prompt-evolution proposals directly |
| `task` | Manage agent tasks (cron) |
| `credentials` | Manage platform credentials (tenant vault) |
| `roles` | Manage roles and permissions |
| `scenarios` | Manage agent scenarios |

---

## Direct Job-Type Commands

`analyze`, `prune`, `learn` and `reflect` are **direct** commands: they resolve
sources via a unified `--src` flag and run the matching job handler *now*,
without creating a task or touching the queue. They share one shape:

```bash
python -m cli.main <job_type> [options]
```

**Common options:**
| Option | Default | Description |
|---|---|---|
| `--src`, `-s` | all active | Source ids, urls or platform keyword (`vk`/`telegram`/`max`), comma/space separated |
| `--tenant` | — | Workspace slug or id (empty = all active sources) |
| `--verbose`, `-v` | `false` | Show detailed output |

**Job-type specific options:**
| Command | Extra options |
|---|---|
| `analyze` | `--scenario <id>`, `--excluded <users>` |
| `prune` | `--days <n>` (default `7`) |
| `learn` | `--min-messages <n>` (default `8`), `--window <n>` (default `200`) |
| `reflect` | `--dedup/--no-dedup` (default on) |

**Examples:**
```bash
# Analyze sources 739 and 740 with a scenario
python -m cli.main analyze --src 739,740 --scenario 3

# Prune jobs older than 14 days
python -m cli.main prune --src 739 --days 14

# Learn from chat (min 5 new turns)
python -m cli.main learn --src 739 --min-messages 5
```

---

## Collect

Run manual content collection & AI analysis. Reuses the same
`ContentCollector.collect_from_source` pipeline the runtime jobs use, so the
analyzer behavior you test here is exactly what the cron/agent path runs.

```bash
python -m cli.main collect [options]
```

**Options:**
| Option | Default | Description |
|---|---|---|
| `--src`, `-s` | all active | Source ids, urls or platform keyword (`vk`/`telegram`/`max`), comma/space separated |
| `--tenant` | — | Workspace slug or id |
| `--monitored` | — | Usernames to collect for instead of source defaults |
| `--excluded` | — | Usernames to skip |
| `--verbose`, `-v` | `false` | Show detailed collection output |

**Examples:**
```bash
# Collect & analyze a single source with details
python -m cli.main collect --src 739 --verbose

# Collect from several sources by id
python -m cli.main collect --src 739,740 --verbose

# Collect all active sources on VK
python -m cli.main collect --src vk --verbose

# Collect by source url with monitored/excluded users
python -m cli.main collect --src https://vk.com/russkikh_natalia --monitored user_a --excluded spam
```

---

## Task

Manage the cron tasks that drive the task runner. Tasks are rows in the
`agent_tasks` table.

### List Tasks

```bash
python -m cli.main task list
```

Displays all tasks in a table: `id`, `name`, `cron`, `job`, `active`,
`next_run_at`, `last_status`.

### Add Task

```bash
python -m cli.main task add <name> <cron_expr> <job_type> [options]
```

**Arguments:**
| Arg | Description |
|---|---|
| `name` | Unique task name |
| `cron` | Cron expression, e.g. `0 9 * * *` |
| `job_type` | One of the registered handlers in `app/jobs/handlers.py::HANDLERS`: `collect`, `digest`, `prune`, `analyze`, `learn`, `reflect` — the CLI validates against this list |

**Options:**
| Option | Default | Description |
|---|---|---|
| `--sources`, `-s` | — | Comma/space separated source IDs to link via the `agent_task_sources` m2m table (empty = all active) |
| `--monitored` | — | Usernames to collect for (collect only) |
| `--excluded` | — | Usernames to skip (collect/analyze) |
| `--scenario` | — | `AgentScenario` ID to apply when the task runs |
| `--payload`, `-p` | `{}` | Extra JSON payload, e.g. `'{"period": "week"}'` (flat keys; sources/scenario are set separately) |

**Examples:**
```bash
# Hourly collection
python -m cli.main task add hourly-collect "0 * * * *" collect

# Daily digest at 9 AM
python -m cli.main task add daily-digest "0 9 * * *" digest

# Weekly digest on Mondays
python -m cli.main task add weekly-digest "0 9 * * 1" digest -p '{"period": "week"}'

# Collect from specific sources with monitored/excluded users
python -m cli.main task add hourly-collect "0 * * * *" collect \
  --sources "1 2" --monitored "user_a" --excluded "spam"

# Analyze with a scenario
python -m cli.main task add daily-analyze "0 2 * * *" analyze --sources "1" --scenario 5
```

### Remove Task

```bash
python -m cli.main task remove <name>
```

### Run Task

Run an existing task now (or create a one-off run). Each job type is a
subcommand, so the type is implicit — there is no `--job-type` flag:

```bash
python -m cli.main task <job_type> --task <name|id> [options]
```

where `<job_type>` is `collect`, `digest`, `analyze`, `prune`, `learn` or
`reflect`.

**Options:**
| Option | Default | Description |
|---|---|---|
| `--task`, `-t` | — | Existing task by name or id to run (source already lives on the task) |
| `--sources`, `-s` | — | Source IDs to link (one-off run) |
| `--monitored` | — | Usernames to collect (one-off run) |
| `--excluded` | — | Usernames to skip (one-off run) |
| `--scenario` | — | `AgentScenario` ID (one-off run) |
| `--period` | — | Period for digest/collect: `day`, `week`, `last month` etc. |
| `--tenant` | — | Workspace slug or id for a one-off run |

A task's own workspace always wins over `--tenant`. Running a task executes
*its own* job — it never drains an unrelated pending job.

**Examples:**
```bash
# Run an existing collect task
python -m cli.main task collect --task 524

# Run an existing digest task with a period
python -m cli.main task digest --task 524 --period week

# One-off analyze run
python -m cli.main task analyze --sources 739 --scenario 3
```

### Pause / Resume Task

```bash
python -m cli.main task pause <name>       # pause
python -m cli.main task pause <name> --resume  # resume
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
waiting for a task.

### Run Digest Direct

Same as `send-now`, but resolves sources via the unified `--src` flag:

```bash
python -m cli.main digest run [options]
```

**Options:**
| Option | Default | Description |
|---|---|---|
| `--src`, `-s` | all active | Source ids, urls or platform keyword |
| `--tenant` | — | Workspace slug or id |
| `--period` | `day` | `day` or `week` |
| `--verbose`, `-v` | `false` | Show detailed output |

```bash
python -m cli.main digest run --src 739 --period week
```

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
2. Tenant-scoped operations (credentials, agent tasks, etc.) work without
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
