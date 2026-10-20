# CLI Reference

ИИ Ассистент administration CLI built with Typer.

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
| `analyze` | `--scenario <id>`, `--excluded <users>`, `--keep-raw` |
| `prune` | `--days <n>` (default `7`) |
| `learn` | `--min-messages <n>` (default `8`), `--window <n>` (default `200`) |
| `reflect` | `--dedup/--no-dedup` (default on) |

**Examples:**
```bash
# Analyze sources 739 and 740 with a scenario
python -m cli.main analyze --src 739,740 --scenario 3

# Preserve staged raw content after successful analysis
python -m cli.main analyze --src 739 --scenario 3 --keep-raw

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
| `--tracked` | — | Tracked authors (`platform:external_id`): analysis keeps only items they wrote |
| `--keywords` | — | Text scope: phrases an item must contain to be analysed (inflection-aware) |
| `--start-date` | — | Lower bound `DD-MM-YYYY`; enforced client-side (VK ignores `wall.get` dates) |
| `--end-date` | — | Upper bound `DD-MM-YYYY` |
| `--force-refresh` | `false` | Re-fetch the full date range from the API (default: only what is new since the last check) |
| `--dry-on` | `false` | Rehearsal: fetch and print a bounded preview; no raw rows, no model, cursor unchanged |
| `--verbose`, `-v` | `false` | Show detailed collection output |

**Examples:**
```bash
# Collect & analyze a single source with details
python -m cli.main collect --src 739 --verbose

# Collect from several sources by id
python -m cli.main collect --src 739,740 --verbose

# Collect all active sources on VK
python -m cli.main collect --src vk --verbose

# Collect by source url, keeping only tracked authors and matching phrases
python -m cli.main collect --src https://vk.com/russkikh_natalia --tracked vk:123456 --keywords "Киров"

# Rehearse a window: see the items and relevance without writing anything or
# calling a model
python -m cli.main collect --src 3160 --start-date 01-10-2026 --dry-on --verbose

# Re-fetch the full date range from the API (analysis still deduped, so tokens
# are only spent on genuinely new/changed content)
python -m cli.main collect --src 739 --force-refresh --start-date 01-05-2025
```

> `collect --force-refresh` re-fetches via the API only. Durable analysis
> receipts remain in force, so already-covered content in the same analysis
> context is not re-analyzed. Web task force controls are removed and legacy
> stored force flags are ignored; the task CLI options below are legacy and do
> not authorize web/API dedup bypass.

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
| `--tracked` | — | Authors whose items are analysed (`platform:external_id`), filtered over the task's own sources |
| `--keywords` | — | Text scope: phrases an item must contain (collect/analyze) |
| `--excluded` | — | Usernames to skip (collect/analyze) |
| `--scenario` | — | `AgentScenario` ID to apply when the task runs |
| `--start-date` | — | Content start date `DD-MM-YYYY` (collect/analyze) — stored on the task as `cli_dates.start_date` |
| `--end-date` | — | Content end date `DD-MM-YYYY` — stored on the task as `cli_dates.end_date` |
| `--force-refresh` | `false` | Legacy collection option; does not bypass analysis receipts |
| `--brands` | — | Comma/space separated brands to track (brand_mentions scenario) |
| `--competitors` | — | Comma/space separated competitors to analyze (competitor scenario) |
| `--hashtags` | — | Comma/space separated hashtags to track (hashtag_analysis scenario) |
| `--influencers` | — | Comma/space separated authors/influencers (influencer scenario) |
| `--topics` | — | Comma/space separated topics (topics scenario) |
| `--payload`, `-p` | `{}` | Extra JSON payload, e.g. `'{"period": "week"}'` (flat keys; sources/scenario are set separately) |

**Examples:**
```bash
# Hourly collection
python -m cli.main task add hourly-collect "0 * * * *" collect

# Daily digest at 9 AM
python -m cli.main task add daily-digest "0 9 * * *" digest

# Weekly digest on Mondays
python -m cli.main task add weekly-digest "0 9 * * 1" digest -p '{"period": "week"}'

# Collect from specific sources, keeping only tracked authors and matching phrases
python -m cli.main task add hourly-collect "0 * * * *" collect \
  --sources "1 2" --tracked "vk:123456" --keywords "Киров, дизайн" --excluded "spam"

# Analyze with a scenario
python -m cli.main task add daily-analyze "0 2 * * *" analyze --sources "1" --scenario 5
```

### Remove Task

```bash
python -m cli.main task remove <name>
```

### Update Task

```bash
python -m cli.main task update <name> [options]
```

Only provided options are changed; all others keep their current values.

**Options:**

| Option | Description |
|---|---|
| `--job-type` | New job type: `collect`, `digest`, `prune`, `analyze`, `learn`, `reflect` |
| `--cron` | New cron expression (5 fields) or `@once` |
| `--sources` | New comma/space separated source IDs (empty = all active) |
| `--tracked` | New tracked authors (`platform:external_id`) |
| `--excluded` | New comma/space separated excluded usernames (collect/analyze) |
| `--scenario` | New `AgentScenario` ID |
| `--period` | Period for digest: `day`, `week` |
| `--start-date` | New start date `YYYY-MM-DD` (collect/analyze) |
| `--end-date` | New end date `YYYY-MM-DD` |
| `--force-refresh` | Legacy option; web task execution ignores stored force flags |
| `--force-reanalyze` | Legacy option; web task execution ignores stored force flags |
| `--brands` | New comma/space separated brands (brand_mentions scenario) |
| `--competitors` | New comma/space separated competitors (competitor scenario) |
| `--hashtags` | New comma/space separated hashtags (hashtag_analysis) |
| `--influencers` | New comma/space separated authors/influencers (influencer scenario) |
| `--topics` | New comma/space separated topics (topics scenario) |

**Example:**

```bash
# Change schedule and add brands for an existing task
python -m cli.main task update nightly-analyze --cron "0 3 * * *" --brands "Coca-Cola, Sprite"

# Switch scenario and add competitors
python -m cli.main task update brand-watch --scenario 7 --competitors "Pepsi, Fanta"
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
| `--tracked` | — | Tracked authors for a one-off run (`platform:external_id`) |
| `--keywords` | — | Text scope for a one-off run |
| `--excluded` | — | Usernames to skip (one-off run) |
| `--scenario` | — | `AgentScenario` ID (one-off run) |
| `--period` | — | Period for digest/collect: `day`, `week`, `month` etc. |
| `--tenant` | — | Workspace slug or id for a one-off run |
| `--force-refresh` | `false` | Direct CLI collection may re-fetch a window; it does not bypass analysis receipts |
| `--start-date` | — | Content start date `DD-MM-YYYY` (one-off only) |
| `--end-date` | — | Content end date `DD-MM-YYYY` (one-off only) |

A task's own workspace always wins over `--tenant`. Running a task executes
*its own* job — it never drains an unrelated pending job.

For direct collection, `--force-refresh` can re-fetch the requested window but
does not bypass durable analysis receipts. Web task force controls were removed;
legacy stored force values are ignored by web task admission. Analysis re-runs
when its semantic context changes (for example, scenario or targets), not
through a force flag. The task-run `--force-refresh` still forces digest
resending, while it does not bypass analysis receipts for collect/analyze.
Prune, learn and reflect retain their normal behavior. Direct
`analyze --keep-raw` changes retention only: staged rows are preserved after a
successful save, while normal receipt and provider rules still apply.

**Examples:**
```bash
# Run an existing collect task
python -m cli.main task collect --task 524

# Run an existing digest task with a period
python -m cli.main task digest --task 524 --period week

# One-off analyze run
python -m cli.main task analyze --sources 739 --scenario 3

# Direct collect command: re-fetch a collection window; receipts still apply
python -m cli.main collect --src 739 --force-refresh --start-date 01-05-2025
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

An unscoped operator run selects `DEFAULT_TENANT_SLUG` (bootstrap), and the
complete build runs with tenant bypass disabled. For another workspace use an
explicitly scoped task/direct run. Env recipients alone do not authorize sends:
they must match an active, digest-enabled binding of bootstrap. Other workspaces
use only their own active bindings. See [delivery setup](DIGEST.md#configuration).

### Run Digest Direct

Same as `send-now`, but resolves sources via the unified `--src` flag:
Without `--tenant`, publication still uses bootstrap scope, not an all-client
digest. With `--tenant`, aggregation and delivery are restricted to that active
workspace; a source filter cannot widen the selected workspace.

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
The scenario includes sentiment + keywords analysis and uses the workspace's
default LLM model.

Use `--force` to replace an existing default scenario.

**Example:**
```bash
# Seed default scenario for the owner workspace
python -m cli.main scenarios seed-default --tenant owner

# Replace existing default
python -m cli.main scenarios seed-default --tenant owner --force
```

---

## LLM catalog and local model tests

`llm provider-list` displays provider ID, name, API format, base URL, active
flag and model count. Defaults belong to models; providers have no default
flag after migration 0091. Model lifecycle commands continue using the existing
model manager; this package does not change capability filtering or API/tool
schemas. `model-list --type` remains an exact stored-value filter.

```bash
# Trusted operator catalog reads (require the configured database)
python -m cli.main llm provider-list
python -m cli.main llm model-list

# Shared local mock: catalog lookup, no provider request or key decryption
python -m cli.main llm model-test 7 --mock --prompt "Local check"
```

`model-test` accepts a positive PostgreSQL INTEGER ID (1–2147483647) and a
nonblank prompt of at most 8000 characters. `--mock` explicitly selects the
shared `model_probe.probe_mock` service. It reads the model/provider from the
catalog but never constructs a provider client, decrypts a key, reserves
billing, journals a provider attempt or sends a request. Inactive models and
providers can be mocked: success is **not** a readiness, authorization or
provider-reachability verdict. The prompt is not echoed in the report.

Mock success prints one JSON report with `ok`, `mode`, `is_real_response`,
`content`, `tokens_spent`, `simulated_usage` and `estimated_cost_usd`. Simulated
usage and tariff estimates are not observed usage or charges; `tokens_spent`
is zero because nothing was dispatched. The shared pricing helper's zero
estimate does not establish a known-free provider tariff. Rich markup is
literal and control characters are JSON-escaped.

Without `--mock`, the command exits **1 before any catalog access or dispatch**.
The trusted CLI tenant bypass does not establish an authenticated payer. Use
the authenticated admin model-test surface for an explicitly authorized paid
probe; its current admission, permission, CSRF, capping and accounting rules
remain unchanged. No automatic fallback or CLI impersonation flag is added.
Do not grant global catalog tools to workspace chat as a workaround.

Exit codes: **0** for a successful mock; **1** for semantic validation,
missing model/provider, failed/malformed probe, lookup/service error or the
blocked real path; **2** for Typer parsing errors. Failed probes print a fixed
generic message, never raw exceptions/provider diagnostics. No retries.

### LLM CLI validation handoff

P10-T13 is based on dev `be07e515c4fa86e856af04faf2233c0680a57acd`.
Remote execution used Python 3.13.14 in a separate sandbox venv, Typer 0.27.3
and Rich 15.0.0 from `requirements.txt`; Black 26.10.1 and isort 9.0.2 were
also pinned there. Direct unittest commands, without pytest/conftest:

```bash
python tests/test_llm_cli_contracts.py -v
python tests/test_llm_cli_parser.py -v
python -m isort --check-only cli/commands/llm.py tests/test_llm_cli_contracts.py tests/test_llm_cli_parser.py
python -m black --check cli/commands/llm.py tests/test_llm_cli_contracts.py tests/test_llm_cli_parser.py
git diff --check
```

Results: 17 whole-source handler/shared-mock tests and 8 real Typer/Rich parser
checks passed (exit 0, no skips/expected failures). On a separate clean archive
of the same dev SHA, the new tests fail as expected: handler suite 17 tests,
5 failures/5 errors; parser suite 8 tests, 5 failures (both exit 1). All model
queries, tenant context and provider boundaries were explicit doubles; no app,
settings, real DB, secrets or providers were loaded. Actual shared mock source
ran with an isolated pricing double; this is not full pricing acceptance.

Draft/BLOCKED until the coordinating local agent reviews the exact published
PR SHA and validates existing-catalog ORM behavior, missing/providerless rows,
mock zero writes/dispatch/key access, and exit codes in the owner's approved
environment. The examples above are deferred acceptance guidance, not remote
execution evidence or permission to start a runtime/use a real DB. No schema,
paid probe or deployment is required or authorized by this package.

Active audit PR #216 (P10-T11) is historical at `e79583e`: reconcile its
provider-list/model-test expected-failure probes and outdated CLI warnings
before integrating both PRs; retain its pinned audit evidence. PR #217
(P10-T12) owns chat task-tool fixes; no task-tool/API/billing files are changed
here. All existing dirty worktree copies remain untouched.

---

## Architecture Notes

### How CLI Commands Run

CLI entrypoints use `_run_platform()`, command-local `_run()`/`_platform()`
wrappers or the direct-job runner; they are not all calls to one wrapper. The
trusted operator lane starts with `tenant_scope(bypass=True)` and selected
operations re-enter a concrete workspace scope. This means:

1. Unscoped developer operations can access data across workspaces.
2. Workspace-specific commands resolve a tenant and retain their service,
   validation and accounting boundaries; bypass is not end-user authorization.
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
Most commands are operator-facing tables/messages. `llm model-test --mock`
prints the explicit JSON report described above, with Rich markup disabled.


### Digest grouping values

Digest command help lists `themes`, `sources`, `entities`, `intent` and
`topic_chains`. `days` is web-only chronology; use `time_breakdown` for digest
chronology. Sentiment and media are metrics/cross-filters, not grouping axes.
Saved legacy task payloads with `sentiment` or `content_type` normalize to themes.
