"""Task runner: poll agent tasks, enqueue due jobs, advance next_run_at.

Tenancy: `agent_tasks`/`jobs` rows are tenant-owned, so the tick is run once per
active workspace inside its tenant scope. The queryset guard in `BaseManager`
then makes each pass see exactly one workspace's tasks — there is no
"filter by tenant_id" to forget here.
"""

import asyncio
import logging
from datetime import datetime, timezone

from app.core.permissions import service_permission_scope
from app.core.config import settings
from app.core.tenant_context import tenant_scope
from app.models.managers.agent_task_manager import AgentTaskManager
from app.models.managers.job_manager import JobManager

from .cron import next_run_at, resolve_tz

logger = logging.getLogger(__name__)

tasks = AgentTaskManager()
jobs = JobManager()


async def tick_tenant(tenant_id: int, now: datetime | None = None, tz: str | None = None) -> dict:
    """One pass for a single workspace. Must be called inside its tenant scope.

    `tz` is the workspace's cron timezone; it must be the same one the task was
    scheduled with at creation, or the schedule shifts by the offset between the
    two zones on the first fire. Omit it only where no tenant row is at hand.
    """
    now = now or datetime.now(timezone.utc)
    stats = {"due": 0, "enqueued": 0, "failed": 0}

    due = await tasks.get_due(now)
    stats["due"] = len(due)

    for task in due:
        is_once = task.cron_expr == "@once"
        try:
            if is_once:
                nxt = None
            else:
                nxt = next_run_at(task.cron_expr, tz or settings.SCHEDULER_TIMEZONE, after=now)
        except (ValueError, KeyError) as e:
            logger.error(f"AgentTask {task.name}: invalid cron {task.cron_expr!r}: {e}")
            await tasks.mark_triggered(task.id, now, status="failed", error=str(e))
            stats["failed"] += 1
            continue

        await jobs.enqueue(
            job_type=task.job_type,
            payload=task.payload or {},
            agent_task_id=task.id,
            run_at=now,
        )
        await tasks.mark_triggered(task.id, nxt, status="ok")
        if is_once:
            with service_permission_scope("agenttask", "update"):
                await tasks.update_by_id(task.id, is_active=False)
        stats["enqueued"] += 1
        if is_once:
            logger.info(f"Enqueued one-shot {task.job_type} job for task {task.name!r}")
        else:
            logger.info(f"Enqueued {task.job_type} job for task {task.name!r}, next run {nxt.isoformat()}")

    return stats


async def tick() -> dict:
    """
    One task-runner pass across all workspaces.

    Returns stats: {"tenants": int, "due": int, "enqueued": int, "failed": int}

    A failing workspace is logged and skipped: one tenant's bad cron must not
    stop the others' digests.
    """
    from app.models import Tenant

    # Tenant rows are global (not tenant-scoped), so this read needs no context.
    tenants = await Tenant.objects.filter(is_active=True)
    totals = {"tenants": len(tenants), "due": 0, "enqueued": 0, "failed": 0}

    for tenant in tenants:
        try:
            with tenant_scope(tenant.id):
                stats = await tick_tenant(tenant.id, tz=resolve_tz(tenant))
        except Exception:  # noqa: BLE001
            logger.exception(f"Task tick failed for tenant {tenant.slug} (id={tenant.id})")
            continue
        for key in ("due", "enqueued", "failed"):
            totals[key] += stats[key]

    return totals


async def run_forever(poll_seconds: int | None = None) -> None:
    """Continuously run ticks. Dedupe: tasks are marked immediately, so re-ticks skip them."""
    poll = poll_seconds or settings.SCHEDULER_POLL_SECONDS
    logger.info(
        f"Task runner started (poll every {poll}s, default tz={settings.SCHEDULER_TIMEZONE}; "
        "a workspace with its own timezone uses that)"
    )
    while True:
        try:
            stats = await tick()
            if stats["enqueued"] or stats["failed"]:
                logger.info(f"Task runner tick: {stats}")
        except asyncio.CancelledError:
            logger.info("Task runner stopped")
            break
        except Exception:
            logger.exception("Task runner tick failed")
        await asyncio.sleep(poll)
