"""Job dispatcher: claim → execute → mark done/failed with retries.

Tenancy: the queue itself is process-wide (any worker may claim any tenant's
job), but every handler runs inside the owning tenant's scope, taken from
`job.tenant_id`. Handlers therefore never receive a tenant and never filter by
it — the queryset guard in `BaseManager` applies it.

Two entry points, one execution path:
- `run_pending_once()` — the worker loop; claims the globally oldest pending job.
- `run_job_now(job_id)` — the operator path (CLI "run this task"); claims that
  exact job, so it can never pick up a different workspace's queued work.
"""

import asyncio
import logging
from typing import Any, Callable, Optional

from app.core.tenant_context import tenant_scope
from app.jobs.handlers import HANDLERS
from app.models.managers.job_manager import JobManager

logger = logging.getLogger(__name__)

jobs = JobManager()


def _job_label(job_type: str) -> str:
    labels = {
        "collect": "Сбор данных",
        "digest": "Дайджест",
        "prune": "Очистка",
        "analyze": "Анализ",
        "learn": "Обучение",
        "reflect": "Рефлексия",
    }
    return labels.get(job_type, job_type)


def _format_name_list(names: list[str], limit: int = 5) -> str:
    """Human-readable list of source names, truncated with a tail count."""
    if not names:
        return ""
    head = names[:limit]
    tail = f" и ещё {len(names) - limit}" if len(names) > limit else ""
    return ", ".join(head) + tail


def _job_success_message(job: Any, result: dict | None) -> str:
    """User-facing completion message, readable by a non-technical person.

    For the collect job the raw stats dict is replaced with a plain-language
    summary: what was monitored, how many new items landed in the tables and
    which sources failed. Every other job falls back to the old terse form.
    """
    label = _job_label(job.job_type)
    if not isinstance(result, dict):
        return f"Задача «{label}» успешно завершена."

    if job.job_type == "collect":
        collected = result.get("collected", 0)
        error = result.get("error", 0)
        empty = result.get("empty", 0)
        excluded = result.get("excluded", 0)
        items = result.get("items", 0)

        parts = [f"Задача «{label}» успешно завершена."]

        if collected:
            names = _format_name_list(result.get("collected_sources") or [])
            parts.append(f"Отслежено источников: {collected} ({names}).")
            parts.append(f"В таблицы добавлено или обновлено записей: {items}.")
        else:
            parts.append("Новых данных не получено.")

        if error:
            names = _format_name_list(result.get("error_sources") or [])
            parts.append(f"Ошибки при сборе: {error} ({names}).")
        if empty:
            parts.append(f"Без контента: {empty} (источники опрошены, но постов нет).")
        if excluded:
            parts.append(f"Пропущено (исключено из мониторинга): {excluded}.")

        return " ".join(parts)

    detail = ", ".join(f"{k}: {v}" for k, v in list(result.items())[:6])
    return f"Задача «{label}» успешно завершена." + (f" {detail}" if detail else "")


async def _notify_job_result(job: Any, *, success: bool, error: str | None = None, result: dict | None = None) -> None:
    """Create a tenant-scoped Notification for a finished job.

    User-facing message (понятная пользователю формулировка) goes into the
    Notification row; the technical error stays on the Job (`error`) and on the
    AgentTask (`last_error`) for the admin UI. Runs inside `tenant_scope`.
    """
    from app.services.notifications.service import notify
    from app.types import NotificationType

    label = _job_label(job.job_type)
    if not success:
        try:
            await notify.create(
                title=f"Ошибка задачи «{label}»",
                message=f"Задача «{label}» не выполнена: {error or 'неизвестная ошибка'}",
                ntype=NotificationType.API_ERROR,
                entity_type="task",
                entity_id=job.agent_task_id,
            )
        except Exception:  # noqa: BLE001 — a failing notification must never break the worker
            logger.exception(f"Failed to create error notification for job {job.id}")
        return

    try:
        await notify.create(
            title=f"Задача «{label}» выполнена",
            message=_job_success_message(job, result),
            ntype=NotificationType.REPORT_READY,
            entity_type="task",
            entity_id=job.agent_task_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception(f"Failed to create success notification for job {job.id}")


async def execute_job(job: Any, handler: Callable) -> None:
    """Run a claimed job and record the outcome (done / retry / failed)."""
    # Job context travels in columns, not in payload. Handlers that need it
    # (digest idempotency is keyed on agent_task_id) get it merged in here.
    payload = dict(job.payload or {})
    payload.setdefault("agent_task_id", job.agent_task_id)
    payload.setdefault("job_id", job.id)
    # Everything below — the handler AND the bookkeeping writes — must see the
    # job's workspace, otherwise mark_done() cannot even find the row.
    with tenant_scope(job.tenant_id):
        try:
            result = await handler(payload)
            await jobs.mark_done(job.id, result=result)
            logger.info(f"Job {job.id} ({job.job_type}) done: {result}")
            # A skip is a normal no-op (already sent digest, learn below its
            # message threshold, cost cap, nothing to reflect on). It would fire
            # a notification on every scheduled run, so only real work reports.
            if not (isinstance(result, dict) and result.get("status") == "skipped"):
                await _notify_job_result(job, success=True, result=result)
        except Exception as e:
            will_retry = await jobs.mark_failed(job.id, error=str(e))
            if will_retry:
                logger.warning(f"Job {job.id} failed (will retry): {e}")
            else:
                logger.error(f"Job {job.id} failed permanently: {e}", exc_info=True)
                await _notify_job_result(job, success=False, error=str(e))


async def _execute_claimed(job: Any) -> Optional[dict]:
    """Run a claimed job through its handler and report the outcome.

    Returns None when the job type has no handler (it is marked failed), else
    the job's final `{status, result, error}`.
    """
    handler = HANDLERS.get(job.job_type)
    if not handler:
        with tenant_scope(job.tenant_id):
            await jobs.mark_failed(job.id, error=f"Unknown job type: {job.job_type}")
        return {"status": "failed", "error": f"Unknown job type: {job.job_type}"}

    await execute_job(job, handler)

    with tenant_scope(job.tenant_id):
        finished = await jobs.get(id=job.id)
    if finished is None:  # pragma: no cover - the row cannot disappear mid-run
        return {"status": "unknown", "error": "Job row not found after execution"}
    return {"status": finished.status, "result": finished.result, "error": finished.error}


async def run_job_now(job_id: int) -> Optional[dict]:
    """Execute one specific job now and return its outcome (operator path).

    Used by `cli.main task run` and any "run this exact thing" surface. The
    difference from `run_pending_once` is the claim: it takes *this* job rather
    than the globally oldest pending one, so running a task of one workspace can
    never execute somebody else's queued job by accident.
    """
    job = await jobs.claim_job(job_id)
    if not job:
        return None
    return await _execute_claimed(job)


async def run_pending_once() -> int:
    """Claim and execute one due job. Returns number of jobs processed (0 or 1).

    `claim_next` deliberately runs without a tenant: a worker must be able to
    pick up any workspace's job (it is a raw cross-tenant SELECT ... SKIP
    LOCKED).
    """
    # Queue maintenance is cross-tenant by nature: reap stragglers everywhere.
    with tenant_scope(bypass=True):
        await jobs.reap_stale()

    job = await jobs.claim_next()
    if not job:
        return 0

    await _execute_claimed(job)
    return 1


async def drain(max_jobs: int | None = None) -> int:
    """Process all due jobs (bounded). Used by worker loop and tests."""
    processed = 0
    limit = max_jobs or 100
    while processed < limit:
        n = await run_pending_once()
        if not n:
            break
        processed += n
    return processed


async def worker_forever(poll_seconds: int = 5) -> None:
    """Worker loop: poll the jobs table and process due jobs."""
    logger.info(f"Worker started (poll every {poll_seconds}s)")
    while True:
        try:
            processed = await drain(max_jobs=10)
            await asyncio.sleep(0 if processed else poll_seconds)
        except asyncio.CancelledError:
            logger.info("Worker stopped")
            break
        except Exception:
            logger.exception("Worker iteration failed")
            await asyncio.sleep(poll_seconds)
