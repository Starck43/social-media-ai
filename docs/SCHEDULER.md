# Scheduler and Job Queue

Cron scheduling and background work are backed by **PostgreSQL**, not by Celery
or Redis. Two processes consume the same tables:

| Process | Entrypoint | Responsibility |
| --- | --- | --- |
| runtime | `python -m app.runtime` | `scheduler/runner.py` tick loop, channel polling, agent chat |
| worker | `python -m app.worker` | claim and execute jobs |

The process split matters: heavy collection runs in `worker`, so a chat reply
is never blocked behind a long collection.

## Tables

- `schedules` — one cron definition: `cron_expr`, `timezone`, `job_type`
  (`collect` | `digest` | `prune`), `payload` (JSON), `is_active`,
  `next_run_at`, `last_run_at`, `last_status`, `last_error`.
- `jobs` — the queue: `job_type`, `payload`, `status`
  (`pending` | `running` | `done` | `failed`), `run_at`, `locked_at`,
  `attempts`, `max_attempts`, `result`, `error`, plus `schedule_id` when the
  job came from a schedule.

## How a run happens

1. `scheduler/runner.py::tick()` selects active schedules with
   `next_run_at <= now`.
2. For each, it inserts a `pending` job and advances `next_run_at` to the next
   cron occurrence. Deduplication is a side effect: advancing the schedule
   means the same occurrence cannot be enqueued twice.
3. `jobs/dispatcher.py::claim_next()` claims the oldest due job with
   `SELECT ... FOR UPDATE SKIP LOCKED` and flips it to `running`. Any number of
   workers can run this concurrently without double-processing.
4. The handler from `jobs/handlers.py` runs, then the job is marked `done` with
   its result, or retried: `attempts++` happened at claim time, and
   `JOB_RETRY_BACKOFF_SECONDS * 2**(attempts-1)` sets the next `run_at`.
5. `reap_stale()` returns jobs stuck in `running` (crashed worker) to `pending`.

Job columns are merged into the handler payload by `execute_job()`, so handlers
receive `schedule_id` and `job_id` alongside their own `payload` keys. Digest
idempotency depends on this.

## Cron expressions

`app/scheduler/cron.py` validates expressions with `croniter` and computes the
next occurrence **in the schedule's own timezone**, returning UTC. `0 9 * * *`
in `Europe/Moscow` therefore means 06:00 UTC.

## Adding a scheduled job

```bash
python -m cli.main schedule add weekly-digest "0 9 * * 1" digest -p '{"period": "week"}'
python -m cli.main schedule list
python -m cli.main schedule pause weekly-digest
python -m cli.main schedule remove weekly-digest
```

Handlers are registered in `HANDLERS` (`app/jobs/handlers.py`). Adding a job
type means adding a function there; `job_type` values are otherwise free-form
strings, so no migration is needed.

## Default schedules

`app/scheduler/bootstrap.py` creates `hourly-collect` and `daily-prune` on the
first runtime start if they are missing. It only inserts, never overwrites — so
editing or pausing them in the database survives restarts.

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
