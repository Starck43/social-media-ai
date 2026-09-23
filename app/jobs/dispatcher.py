"""Job dispatcher: claim → execute → mark done/failed with retries.

Tenancy: the queue itself is process-wide (any worker may claim any tenant's
job), but every handler runs inside the owning tenant's scope, taken from
`job.tenant_id`. Handlers therefore never receive a tenant and never filter by
it — the queryset guard in `BaseManager` applies it.
"""

import asyncio
import logging
from typing import Any, Callable

from app.core.tenant_context import tenant_scope
from app.jobs.handlers import HANDLERS
from app.models.managers.job_manager import JobManager

logger = logging.getLogger(__name__)

jobs = JobManager()


async def execute_job(job: Any, handler: Callable) -> None:
    """Run a claimed job and record the outcome (done / retry / failed)."""
    # Job context travels in columns, not in payload. Handlers that need it
    # (digest idempotency is keyed on schedule_id) get it merged in here.
    payload = dict(job.payload or {})
    payload.setdefault("schedule_id", job.schedule_id)
    payload.setdefault("job_id", job.id)
    # Everything below — the handler AND the bookkeeping writes — must see the
    # job's workspace, otherwise mark_done() cannot even find the row.
    with tenant_scope(job.tenant_id):
        try:
            result = await handler(payload)
            await jobs.mark_done(job.id, result=result)
            logger.info(f"Job {job.id} ({job.job_type}) done: {result}")
        except Exception as e:
            will_retry = await jobs.mark_failed(job.id, error=str(e))
            if will_retry:
                logger.warning(f"Job {job.id} failed (will retry): {e}")
            else:
                logger.error(f"Job {job.id} failed permanently: {e}", exc_info=True)


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

    handler = HANDLERS.get(job.job_type)
    if not handler:
        with tenant_scope(job.tenant_id):
            await jobs.mark_failed(job.id, error=f"Unknown job type: {job.job_type}")
        return 1

    await execute_job(job, handler)
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
