"""Scheduler runner: poll schedules, enqueue due jobs, advance next_run_at.

Tenancy: `schedules`/`jobs` rows are tenant-owned, so the tick is run once per
active workspace inside its tenant scope. The queryset guard in `BaseManager`
then makes each pass see exactly one workspace's schedules — there is no
"filter by tenant_id" to forget here.
"""

import asyncio
import logging
from datetime import datetime, timezone

from app.core.config import settings
from app.core.tenant_context import tenant_scope
from app.models.managers.job_manager import JobManager
from app.models.managers.schedule_manager import ScheduleManager

from .cron import next_run_at

logger = logging.getLogger(__name__)

schedules = ScheduleManager()
jobs = JobManager()


async def tick_tenant(tenant_id: int, now: datetime | None = None) -> dict:
    """One pass for a single workspace. Must be called inside its tenant scope."""
    now = now or datetime.now(timezone.utc)
    stats = {"due": 0, "enqueued": 0, "failed": 0}

    due = await schedules.get_due(now)
    stats["due"] = len(due)

    for schedule in due:
        try:
            nxt = next_run_at(schedule.cron_expr, schedule.timezone, after=now)
        except (ValueError, KeyError) as e:
            logger.error(f"Schedule {schedule.name}: invalid cron {schedule.cron_expr!r}: {e}")
            await schedules.mark_triggered(schedule.id, now, status="failed", error=str(e))
            stats["failed"] += 1
            continue

        await jobs.enqueue(
            job_type=schedule.job_type,
            payload=schedule.payload or {},
            schedule_id=schedule.id,
            run_at=now,
        )
        await schedules.mark_triggered(schedule.id, nxt, status="ok")
        stats["enqueued"] += 1
        logger.info(f"Enqueued {schedule.job_type} job for schedule {schedule.name!r}, next run {nxt.isoformat()}")

    return stats


async def tick() -> dict:
    """
    One scheduler pass across all workspaces.

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
                stats = await tick_tenant(tenant.id)
        except Exception:  # noqa: BLE001
            logger.exception(f"Scheduler tick failed for tenant {tenant.slug} (id={tenant.id})")
            continue
        for key in ("due", "enqueued", "failed"):
            totals[key] += stats[key]

    return totals


async def run_forever(poll_seconds: int | None = None) -> None:
    """Continuously run ticks. Dedupe: schedules are marked immediately, so re-ticks skip them."""
    poll = poll_seconds or settings.SCHEDULER_POLL_SECONDS
    logger.info(f"Scheduler started (poll every {poll}s, tz={settings.SCHEDULER_TIMEZONE})")
    while True:
        try:
            stats = await tick()
            if stats["enqueued"] or stats["failed"]:
                logger.info(f"Scheduler tick: {stats}")
        except asyncio.CancelledError:
            logger.info("Scheduler stopped")
            break
        except Exception:
            logger.exception("Scheduler tick failed")
        await asyncio.sleep(poll)
