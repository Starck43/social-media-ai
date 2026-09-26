# Agent Tasks and Job Queue

Cron scheduling and background work are backed by **PostgreSQL**, not by Celery
or Redis. Two processes consume the same tables:

| Process | Entrypoint | Responsibility |
| --- | --- | --- |
| runtime | `python -m app.runtime` | `scheduler/runner.py` tick loop, `jobs` worker, and `channels/listener.py` agent chat loop |
| worker | `python -m app.worker` | claim and execute jobs |

The process split matters: heavy collection runs in `worker`, so a chat reply
is never blocked behind a long collection.

## Tables

- `agent_tasks` — one cron definition: `cron_expr`, `timezone`, `job_type`
  (`collect` | `digest` | `prune` | `analyze` | `learn` | `reflect`),
  `payload` (JSON), `is_active`,
  `next_run_at`, `last_run_at`, `last_status`, `last_error`.
- `jobs` — the queue: `job_type`, `payload`, `status`
  (`pending` | `running` | `done` | `failed`), `run_at`, `locked_at`,
  `attempts`, `max_attempts`, `result`, `error`, plus `agent_task_id` when the
  job came from a task.

## How a run happens

1. `scheduler/runner.py::tick()` selects active agent_tasks with
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
5. `reap_stale()` returns jobs stuck in `running` (crashed worker) to `pending`.

Job columns are merged into the handler payload by `execute_job()`, so handlers
receive `agent_task_id` and `job_id` alongside their own `payload` keys. Digest
idempotency depends on this.

## Cron expressions

`app/scheduler/cron.py` validates expressions with `croniter` and computes the
next occurrence **in the task's own timezone**, returning UTC. `0 9 * * *`
in `Europe/Moscow` therefore means 06:00 UTC.

## Adding a task

```bash
python -m cli.main task add weekly-digest "0 9 * * 1" digest -p '{"period": "week"}'
python -m cli.main task list
python -m cli.main task pause weekly-digest
python -m cli.main task remove weekly-digest
```

Adding a job type means adding a function to the `HANDLERS` registry in
`app/jobs/handlers.py`; `job_type` values are otherwise free-form strings, so
no migration is needed. Note that the CLI and the agent's task tool
currently expose a narrower allowlist than the registry, so not every handler
is reachable through them yet.

## Default tasks

`app/scheduler/bootstrap.py::ensure_all_default_tasks()` seeds every active
workspace at startup with `hourly-collect`, `daily-prune`, `hourly-learn` and
`weekly-reflect`. On new tenant creation the caller must call
`ensure_all_default_tasks(tenant_id)` separately (it is **not** automatic). The
bootstrap only inserts, never overwrites — so editing or pausing them in the
database survives restarts.

`learn` and `reflect` drive the chat-learning loop (`app/agent/learning.py`).
`analyze` has **no default task** — it writes the `bot_actions` ledger, so
it is meant to be added per workspace deliberately. Note the current CLI/tool
gap: `task add` and the agent's `task_add` accept only
`collect | digest | prune`, so an `analyze` row has to be inserted into
`agent_tasks` directly until that allowlist is widened.

`learn` self-gates on a message-count watermark, and `reflect` is a no-op when
memory is empty — so both are cheap even when there is little to do.

> **Tenancy note:** agent_tasks and jobs are tenant-owned (`TenantScopedMixin`).
> The runner loops over every active `Tenant` and calls `tick_tenant()` inside
> `tenant_scope(tenant_id)` — one tenant's broken cron won't block others.
> `claim_next()` runs cross-tenant (`bypass`) to pick up any workspace's job,
> but the handler body runs scoped to `job.tenant_id`. See
> [TENANCY.md](./TENANCY.md).

## Tuning

`SCHEDULER_ENABLED`, `SCHEDULER_POLL_SECONDS`, `SCHEDULER_TIMEZONE`,
`JOB_MAX_ATTEMPTS`, `JOB_RETRY_BACKOFF_SECONDS` (see `app/core/config.py`).

## Why not Celery

One VPS, one operator: Redis and a Celery worker add two moving parts for a
workload measured in jobs per hour. A database queue gives atomic claiming,
retries, and an inspectable history with no extra service. It does **not** give
distributed workers across machines or priority queues — if that is ever
needed, revisit this decision. `app/celery/` is superseded and not started by
any entrypoint.
