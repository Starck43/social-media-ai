# Agent Tasks and Job Queue

Cron scheduling and background work are backed by **PostgreSQL**, not by Celery
or Redis. The runtime, worker and web app share the same tables:

| Process | Entrypoint | Responsibility |
| --- | --- | --- |
| runtime | `python -m app.runtime` | `tasks/runner.py` tick loop, `jobs` worker, and `channels/listener.py` agent chat loop |
| worker | `python -m app.worker` | claim and execute jobs |
| web app | ASGI lifespan | independently reap stale claims and apply Job history retention every five minutes |

The process split matters: heavy collection runs in `worker`, so a chat reply
is never blocked behind a long collection.

## Tables

- `agent_tasks` — one cron definition **and the reaction it produces**:
  `cron_expr`, `job_type`
  (`collect` | `digest` | `prune` | `analyze` | `learn` | `reflect`),
  `payload` (JSON): **selection keys (collect/analyze):** `cli_dates` (`start_date`/`end_date`), `tracked_users` (author filter, `platform:external_id`), `keywords_list` + `keywords_match` (`any`/`all`), `topics_list`, `brands`, `competitors`, `hashtags`, `influencer_names`; **operational keys (collect/analyze):** `force_refresh`, `force_reanalyze`, `analyze_inline`, `collect_before_analyze`; **read-path keys (digest):** `group_by` (`themes` | `sources` | `entities` | `intent` | `topic_chains`, default `themes`), `time_breakdown` (boolean, default `false` — enables per-date entries within each group), `period` (`day` | `week` | `month`), `scenario_id` (alias for `agent_scenario_id` override). The **`dry_run` column** (not payload) makes the task a rehearsal: collect fetches without staging or calling a model, analyze reports the selection only. **Task-owned collection (P5-T14):** `payload["collection"]` answers what/where/how much — `content_types` (`posts` | `comments`), `engagement_types` (`likes` | `reposts`), `limit`, `date_window`, mention `queries` with `queries_match` (`any` | `all`), `discovery` (the population: `queries` + `queries_match`, `people`/`communities`, `city` = the city the search is scoped to) and the comment/candidate caps. It is read on `collect` and `analyze` and dropped for `digest`/`prune`/`learn`/`reflect`; a task without the block keeps the legacy posts-only default. The scenario owns methodology and supported media only. See [collection plan](design/collection-plan.md).
- `agent_task_sources` — many-to-many between tasks and `sources`. A task's
  sources are linked here (not in `payload`); an empty set means all active
  sources. The `sources` relationship is loaded via `task.sources` (a list).
  **Task-owned query lanes (P5-T14):** a query source (`sources.owner_task_id`)
  is a `Source` owned by one task, hidden from the global source list and from
  the task's “all active” fallback; only an explicit link activates it.
  `python -m cli.main collection lanes <task_id>` moves an existing task onto
  its own lanes (dry run unless `--apply`).
- `jobs` — the queue: `job_type`, `payload`, `status`
  (`pending` | `running` | `done` | `failed`), `run_at`, `locked_at`,
  `attempts`, `max_attempts`, `result`, `error`, `llm_cost` (USD spent by the
  job's LLM call — `learn`/`reflect`, NULL when none), plus `agent_task_id` when
  the job came from a task.

### Source-aware run admission

Explicit `collect`/`analyze` starts use `JobManager.admit_source_job`, not a
page-local check followed by low-level enqueue. Task starts (web, API, CLI/admin
and cron), one-off inline source collection, source analysis and detail retries
share this boundary. Active jobs are `pending` or `running` in the same workspace;
occupancy includes both job types, regardless of scenario/date-window differences.

Membership matches worker precedence: a present task's linked sources win over
payload source IDs; a task without links means all currently active workspace
sources. For taskless/deleted-task jobs use payload source IDs, or all active
sources when absent. Job/task/source queries are tenant-filtered and task links
are loaded in a batch, never once per active job. The source page's busy hint uses
the same manager projection; hiding a button is not the enforcement boundary.

Admission locks the existing owning Tenant row `FOR NO KEY UPDATE` and rechecks
occupancy before inserting in that same transaction. No table/column/migration,
Redis or process-local production mutex is introduced. The lock is short-lived,
not held through handler/LLM execution, and does not serialize work in different
workspaces. Lock order is workspace admission gate before task/staged-row locks;
task admission takes the gate before its existing Task lock, and manual retries
before resetting attempts. Reset/enqueue rollback together on a busy conflict.
Admission explicitly requires PostgreSQL READ COMMITTED isolation and fails
closed for transaction-wide snapshots that could miss a winner's inserted Job.
Real concurrent-connection validation on the owner's database remains required.

A collision raises `SourceJobBusy` with the existing Job, never a newly admitted
run. The web task modal can show that job with an informational message; API
explicit task runs return HTTP 409 (`source_busy`, `job_id`); direct/inline callers
do not claim or execute another task's job. A busy task does not change
`last_run_at`, `last_status`, `last_error`, `next_run_at` or `@once` activity. Cron
keeps its occurrence due and does not count a collision as an enqueue, so a
different scenario/task is not silently discarded or marked executed.

A task keeps at most one queued or claimed occurrence. Workspace-level job types
(`digest`, `prune`, `learn`, `reflect`) answer a scheduled tick that arrives while
their own earlier occurrence is still pending/running with the same busy receipt:
no duplicate row is inserted and `next_run_at` is not advanced. Source job types
already enforce this through the source conflict check.

A queued occurrence is a snapshot of the definition that existed at admission, so
it stops being valid when that definition changes. Saving a changed value for
`job_type`, `cron_expr`, `payload`, `agent_scenario_id`, `next_run_at`,
`trigger_type`, `trigger_config`, `action_type`, `rate_limit_per_hour`,
`cooldown_seconds` or `dry_run` removes its `pending` rows in the same transaction.
Replacing or adding linked sources does the same when the membership actually
changes. Saving the same value or the same source set preserves queued work; a
task is deactivated only when it transitions from active to inactive. Deleting a
task also removes its replaceable pending rows because the Job FK is
`ON DELETE SET NULL` — otherwise the occurrence would execute the removed task's
payload. The task's `running` and terminal rows stay as history. A pending row
whose previous attempt left an unconfirmed external outcome (`outcome_unconfirmed`)
is retained for review rather than dropped.

`AgentTask.last_status="queued"` is a historical trigger receipt, not proof that
a Job remains pending. The task list and detail page show a live queue/running
state only from a current pending/running Job; when that Job was invalidated,
the receipt remains visible as historical context.

Task definition and source-link mutations take the workspace admission gate
before locking task rows, in stable task order for multi-task operations. This
keeps a concurrent admission on one side of the edit and commits the task/source
change with stale-pending cleanup as one unit. Caller-owned sessions retain
commit and rollback ownership.

This is explicit-run deduplication, not a universal execution lock or a content
idempotency ledger. Low-level `JobManager.enqueue` remains available for durable
new-input wakeups (e.g. Telegram staging): coalescing new content into a running
batch that already selected its inputs could strand it. Already committed wakeup
jobs block an explicit repeat, but autonomous new-input admission is not serialized
by this explicit-run gate. No terminal history is cancelled/removed, no claim,
recovery/cancellation policy is weakened, and a running job is not stopped by a
source/task configuration edit. Existing ordinary result/hash dedup still applies.
Use the guarded method for new explicit source-run surfaces; never use a raw
insert or a page-level busy check as their only protection.

Remote checks exercise actual manager code with isolated dependencies, in-memory
transaction/lock doubles and PostgreSQL SQL compilation. They do not prove real
PostgreSQL locking, ASGI authorization, worker execution or database rollback.

### Clearing the queue (`/app/jobs`)

The page shows 50 rows per page and can delete one row
(`POST /app/jobs/{id}/delete`) or clear safe terminal history
(`POST /app/jobs/clear`).
Both are gated on `agenttask.delete` — the same right as the task delete on
`/app/tasks`, since the rows are the tasks' run history — plus CSRF and a
shared in-app confirmation dialog (no native browser `confirm()`).
The same component confirms queue cancellation and source/task deletion.
`web/base.html` includes `web/_confirm.html` once; `js/ui-confirm.js` intercepts
forms or submit buttons marked `data-confirm`, with optional
`data-confirm-title` and `data-confirm-label`. It resumes via `requestSubmit`
only after acceptance, preserving CSRF, workspace fields, validation and the
original submit button (including `formaction`/`formnovalidate`). Cancel, Escape
and backdrop dismissal do not submit; the dialog restores focus and scroll.

Bulk clear only removes ordinary terminal rows. Pending/running rows and
unresolved or review-required outcomes stay for explicit operator action. A
review-required row can be individually deleted after a stronger warning.
The clear flash says how many rows were kept.
- **One workspace at a time.** A superuser without a workspace selected is
  refused, rather than offered a "delete every job of every workspace" button.

Both handlers filter on `tenant_id` explicitly instead of relying on the
manager's tenant guard alone — the guard is what keeps the *page* honest, and a
mutation that trusted it alone would be one refactor away from deleting another
workspace's row.

### Queue health signals (`/app/jobs`)

Above the history the page shows an amber banner when the queue, in the page's
current scope and across all its rows (not only the last 50), has:

- **overdue pending** jobs — due (`run_at`) more than 15 minutes ago and still
  unclaimed, with the age of the oldest: the worker loop is stopped or busy with
  one long job;
- **stale running** jobs — `locked_at` older than 30 minutes, the
  `JobManager.reap_stale` default cutoff. Worker and web maintenance loops
  quarantine stale claims, so a row still `running` means maintenance is not
  reaching it; check the worker and web process health;
- **unconfirmed failures** — `failed` rows whose external outcome is unknown
  (`app/jobs/recovery_policy.py`); inspect them before any retry.

The signals are counts and whole minutes only; no payload, error text or
recipient is shown. They are read-only: nothing is requeued, reaped or alerted,
and there is no external notification yet.

## The reaction: when to look and what to do

A task answers "when to look" (`cron_expr`) and "what to do about a match"
(`trigger_type` + `trigger_config` + `action_type` + the guards). A **scenario**
answers only "how to analyse" — its interest, prompts, analysis mode and models.
They are separate because a scenario is reused across tasks: a rule stored on it
would change meaning every time a differently-scheduled task picked it up.

`app/core/triggers.py` is the single source of truth — each condition's name,
its config keys, its defaults, and a one-sentence `describe()`. The web task
editor, the sqladmin form, the agent's `task_list` tool and the analysis prompt's
`{trigger_condition}` all render from it, so a hint cannot describe a rule the
evaluator does not apply.

| Condition | Phase | Config | Notes |
| --- | --- | --- | --- |
| `KEYWORD_MATCH` | text | `{"keywords": [...], "match": "any"\|"all"}` | Whole-word, case-insensitive. Empty list = no filtering |
| `USER_MENTION` | text | `{"usernames": [...]}` | Names are matched literally, with or without `@` |
| `SENTIMENT_THRESHOLD` | analysis | `{"threshold": 0.0–1.0, "direction": "below"\|"above"}` | Reads `sentiment_analysis.sentiment_score` |

`TIME_BASED` and `MANUAL` were removed. The first was a no-op equal to NULL
(the schedule already lives on the task); the second made `should_act` always
return `False`, so actions never executed.

**"Phase" is honest about cost.** The `text` conditions decide from the raw post
without a model call — but in the current pipeline the analysis has already run
by the time `should_analyze` is called, so they do not save tokens today. They
decide whether to *act*, not whether to analyse. The UI says so rather than
implying a saving that does not happen.

A task with no `action_type` analyses and reports and never writes to a
platform; the guards (`app/services/social/guards.py`) are checked against the
**task**, so two tasks sharing one scenario get independent hourly budgets.

## How a run happens

1. `tasks/runner.py::tick()` selects active agent_tasks with
   `next_run_at <= now`.
2. For each, it inserts a `pending` job and advances `next_run_at` to the next
   cron occurrence. Deduplication is a side effect: advancing the task
   means the same occurrence cannot be enqueued twice.
3. `jobs/dispatcher.py::claim_next()` claims the oldest due job with
   `SELECT ... FOR UPDATE SKIP LOCKED` and flips it to `running`. Any number of
   workers can run this concurrently without double-processing.
4. The handler from `jobs/handlers.py` runs, then the job is marked `done` with
   its result, or retried: `attempts++` happened at claim time, and
   `JOB_RETRY_BACKOFF_SECONDS * 2**(attempts-1)` sets the next `run_at`.
5. `reap_stale()` quarantines stale `running` jobs as failed with an unconfirmed-
   outcome marker; it never replays them automatically. The worker emits one
   redacted warning with the quarantined count so operators can investigate.
   In the same transaction, each quarantined or attempt-budget-stopped job
   writes its task's `last_status=failed` and `last_error` (the marker), so the
   task list, task page and `/api/v1/tasks` stop showing `queued` or an older
   result for a run that died with its worker. A task whose last trigger or
   outcome is later than that attempt's start keeps the newer evidence.

Operator cancellation records a fenced `failed` transition, not proof that an
in-flight handler or external request stopped. New and existing rows with the
exact `Cancelled by operator` error are classified as unconfirmed outcomes: the
queue shows the review warning and does not offer its ordinary retry button.
Built-in automatic retention and bulk clear preserve these rows, including
attempt, result and cost evidence; late completion by the cancelled claim cannot
overwrite them. An operator can still delete a specific row after confirming the
strong warning; that manual deletion removes its evidence.

Job columns are merged into the handler payload by `execute_job()`, so handlers
receive `agent_task_id` and `job_id` alongside their own `payload` keys. Digest
idempotency depends on this.

If a claimed job has no registered handler, the dispatcher records a terminal
`failed` outcome for that claim even when the caller allows retries. No handler
runs and no automatic retry is scheduled: an operator must inspect the job type
and worker configuration before deciding on a new run. Claim loss and outcome
write uncertainty remain explicit errors, not proof of a committed failure.
This does not reject enqueue requests or prevent later cron occurrences.

## Cron expressions

`app/tasks/cron.py` validates expressions with `croniter` and computes the
next occurrence **in the task's own timezone**, returning UTC. `0 9 * * *`
in `Europe/Moscow` therefore means 06:00 UTC.

## Adding a task

```bash
python -m cli.main task add weekly-digest "0 9 * * 1" digest -p '{"period": "week"}'
python -m cli.main task add hourly-collect "0 * * * *" collect \
  --sources "1 2 3" --tracked "vk:123456, vk:7890" --keywords "Киров, дизайн" --dry-on
python -m cli.main task add nightly-analyze "0 2 * * *" analyze \
  --sources "1" --scenario 5 --brands "Coca-Cola, Sprite"
python -m cli.main task list
python -m cli.main task pause weekly-digest
python -m cli.main task remove weekly-digest
python -m cli.main task update hourly-collect --brands "Coca-Cola, Fanta" --cron "0 */2 * * *"
```

Task sources are linked via the `agent_task_sources` m2m table
(`--sources`). Selection belongs to the task, not to a source or a scenario
([task selection](design/task-selection.md)): `--tracked` → `payload.tracked_users`
(authors whose items are analysed, `platform:external_id`),
`--keywords`/`--topics` → the text scope (`keywords_list` + `keywords_match`,
`topics_list`). `--dry-on` sets the task's `dry_run` column. `--scenario` sets
`agent_scenario_id`. Specific analysis targets (`--brands`, `--competitors`,
`--hashtags`, `--influencers`) are run variables in the task `payload`, not the
shared scenario. Date window: `--start-date`/`--end-date` →
`cli_dates.start_date`/`end_date`.
Flags: `--force-refresh` → `force_refresh`, `--force-reanalyze` → `force_reanalyze`,
`--analyze-inline` → `analyze_inline`. Use `task update <name> [options]` to change any field on an existing task —
only the fields you pass are modified.

Adding a job type means adding a function to the `HANDLERS` registry in
`app/jobs/handlers.py`; `job_type` values are otherwise free-form strings, so
no migration is needed. Note that the CLI and the agent's task tool
currently expose a narrower allowlist than the registry, so not every handler
is reachable through them yet.

In the web UI (`/app/tasks`) a task is edited on `/app/tasks/{id}/edit`.
The `/app/tasks?task_id=<id>` compatibility link redirects there only when the
caller has edit permission. `_edit_payload()` (`app/web/tasks.py`) loads the
workspace-scoped task and its complete source, scenario, target and trigger
configuration before rendering the standalone editor.

The web task form presents source work as one **Анализ соцсетей** task. Saving
it stores the existing `analyze` job type with `collect_before_analyze=true`:
the handler first stages fresh source data without inline model calls, then
analyzes that backlog. Any raw rows the model cannot process remain staged for
a later run. The `collect` job type remains available to CLI/API/chat and for
technical history compatibility, but is not offered as a separate web task.
The source page reports raw waiting/suspended rows, recent analyses and total
analytics cost; only users with `agenttask.view` see task names and schedules.

The operator setup path is **source → scenario → task → results**. A scenario
holds analysis methodology and can be reused; each task selects its scenario,
sources, target filters, date window and schedule. A missing task-level scenario
uses the workspace default. Source and scenario detail pages link directly to
pre-filled task creation when the caller has `agenttask.create`; task links and
usage counts are gated by `agenttask.view`.

Unified web analysis requires an active selected scenario or an active workspace
default before creation or launch. A paused task may retain its inactive
selected scenario; missing methodology does not prevent pausing a task.
The editor requires JavaScript and disables saving when it is unavailable,
preventing partial parameter loss. Its shared cron builder preserves the original
expression until an explicit schedule change; unsupported visual patterns stay
editable as raw cron.

A task's own page is `/app/tasks/{id}`. The queue links to it per row
(`/app/tasks/{id}?from=jobs`), and `?from=jobs` swaps the card's breadcrumb for
«← Очередь заданий» so the reader returns to the row they came from. Only the
literal value `jobs` is honoured — the parameter names a page, never an
arbitrary URL.

The card's linked sources come from `_task_source_ids()`, which reads the
`AgentTask.sources` relationship (`secondary=agent_task_sources`) with a
`prefetch_related`. Reaching for the m2m table directly is what broke the page:
the join column is `agent_task_id`, and a declarative `Table.select()` produces
a core `Select` with no `.fetchall()`. Either mistake 500s the card for every
task, and nothing on the task list noticed — the list never opens a card.

**Content window is mandatory for collect/analyze.** A task of these types
cannot be saved without a `cli_dates.start_date`: without it, a fresh source
has no lower bound and the first run drains the whole history from the first
post. The web form, the agent's `task_add` tool and the CLI's `--start-date`
all enforce/store it the same way (`AgentTaskManager.parse_date` +
`build_dates_payload`). `force_refresh` (collect: overwrite the window on each
run) and `force_reanalyze` (analyze: re-run the model on stored rows) are the
operator's explicit choices. The CLI `task <job_type>` command runs a task
with its stored dates unless flags override — the job payload is built from
the task payload, so a task that saved a window keeps it.

The chat `task_update` tool treats omitted fields as unchanged. A supplied
start or end date replaces only that bound and retains the other bound;
`scenario_id` changes the binding, while `clear_scenario=true` explicitly
removes it. Supplying `source_ids` replaces the full link set, including `[]`
for the existing empty-link meaning (all active workspace sources). Source
and scenario ownership is checked by `AgentTaskManager` under the workspace
admission/task locks. The task row and link replacement commit together, and a
rejected source or scenario leaves both unchanged. Cron edits persist both the
expression and its next occurrence. These edits invalidate pending task runs
through the same manager path used by other task editors.

## Default tasks

`app/tasks/bootstrap.py::ensure_all_default_tasks()` seeds every active
workspace at startup with `hourly-collect`, `hourly-learn` and
`weekly-reflect`. On new tenant creation the caller must call
`ensure_all_default_tasks(tenant_id)` separately (it is **not** automatic). The
bootstrap only inserts, never overwrites — so editing or pausing them in the
database survives restarts.

`learn` and `reflect` drive the chat-learning loop (`app/agent/learning.py`).
`analyze` has no default task — it writes the `bot_actions` ledger, so
it is meant to be added per workspace deliberately via `task add`.
Both the CLI (`task add`) and the agent's `task_add` tool validate `job_type`
against the `HANDLERS` registry dynamically, so every registered handler is
reachable through both interfaces without code changes.

`learn` self-gates on a message-count watermark, and `reflect` is a no-op when
memory is empty — so both are cheap even when there is little to do.

### Job history and one-time runs

`/app/jobs` is available to workspace admins and owners. It shows active and
failed work plus completed runs needing attention by default, with a separate
history view for completed runs without attention markers and an all-jobs view.
Ordinary terminal Jobs are removed automatically after 24 hours based on
`finished_at`; no `daily-prune` schedule or runtime process is required for this
retention. The Job row alone is deleted: one-time `@once` AgentTask cards and
analysis, receipts, and billing evidence remain. Unconfirmed or review-required
outcomes are kept for manual investigation.

The web task form's **Run now** action admits a Job and launches it in the web
process; it does not require `app.runtime`. A one-time AgentTask card is useful
when its settings should remain reusable and visible. Direct service one-offs
can use the existing job admission/enqueue and dispatcher APIs without creating
a cron task. Future scheduled runs still require `app.runtime`.

Admins can add a `prune` task in the Jobs page to set success and failure
retention periods (1–8760 hours). This policy is applied by built-in retention
even when its own schedule does not fire. The most recently updated valid active
policy wins; with no active policy, the 24-hour defaults apply. Existing prune
tasks continue to manage raw content and analysis retention; their legacy
schedule does not define Job-history retention.

Repeating a safe failed source run creates a new Job and preserves the original
row. Its source and selection snapshot is used so later edits to the recurring
task do not broaden the replay. Older runs without a reliable snapshot and runs
with external-action settings cannot be replayed from Jobs; start a new run from
the task after reviewing its current settings.

> **Tenancy note:** agent_tasks and jobs are tenant-owned (`TenantScopedMixin`).
> The runner loops over every active `Tenant` and calls `tick_tenant()` inside
> `tenant_scope(tenant_id)` — one tenant's broken cron won't block others.
> `claim_next()` runs cross-tenant (`bypass`) to pick up any workspace's job,
> but the handler body runs scoped to `job.tenant_id`. See
> [TENANCY.md](./TENANCY.md).

## Tuning

`SCHEDULER_ENABLED`, `SCHEDULER_POLL_SECONDS`, `SCHEDULER_TIMEZONE`,
`JOB_MAX_ATTEMPTS`, `JOB_RETRY_BACKOFF_SECONDS` (see `app/core/config.py`).
`prune` tasks also apply `DATA_RETENTION_MONTHS` (default 1) to analysis results
and unanalysed raw content; see `docs/CONFIGURATION.md#data-retention`. Job
history uses the independent built-in policy described above.

`SCHEDULER_TIMEZONE` is only the **fallback**: a workspace's own
`tenants.timezone` decides when its tasks fire (`app/tasks/cron.py::resolve_tz`
is the single place that resolves it, used by every writer — the web form, the
admin, the CLI, the agent tool, the bootstrap and the runner). The two must
agree, because a schedule is written once at creation and re-advanced after
every fire.

## Why not Celery

One VPS, one operator: Redis and a Celery worker add two moving parts for a
workload measured in jobs per hour. A database queue gives atomic claiming,
retries, and an inspectable history with no extra service. It does **not** give
distributed workers across machines or priority queues — if that is ever
needed, revisit this decision. `app/celery/` is superseded and not started by
any entrypoint.

## Explicit task plans (P8-T29)

Target: [universal tasks](design/universal-tasks.md). New explicit plans distinguish selected, discovery and all-authorized source scope and fixed versus rolling data windows. Legacy tasks retain their existing empty-link semantics. All surfaces share validation; effective conditions and personal payer are fixed at admission. See the task card for delivered checks; this section alone is not acceptance evidence.
