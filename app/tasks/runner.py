"""Task runner: poll agent tasks, enqueue due jobs, advance next_run_at.

Tenancy: `agent_tasks`/`jobs` rows are tenant-owned, so the tick is run once per
active workspace inside its tenant scope. The queryset guard in `BaseManager`
then makes each pass see exactly one workspace's tasks — there is no
"filter by tenant_id" to forget here.
"""

import asyncio
import logging
from datetime import datetime, timezone

from app.core.config import settings
from app.core.tenant_context import tenant_scope
from app.models.managers.agent_task_manager import AgentTaskManager

from .cron import resolve_tz

logger = logging.getLogger(__name__)

tasks = AgentTaskManager()


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
        receipt = await tasks._admit_task_run(
            task.id, now=now, scheduled=True, expected_next_run_at=task.next_run_at,
            timezone_name=tz or settings.SCHEDULER_TIMEZONE,
        )
        if receipt is None:
            continue
        if receipt["status"] == "invalid_schedule":
            logger.error("scheduled_task_invalid_cron task_id=%s", task.id)
            stats["failed"] += 1
            continue
        stats["enqueued"] += 1
        logger.info("scheduled_task_enqueued task_id=%s job_id=%s", task.id, receipt["job"].id)

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
    """Continuously run ticks; each scheduled insertion/advance is atomically fenced."""
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
