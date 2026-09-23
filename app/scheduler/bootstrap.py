"""Bootstrap: create default schedules (idempotent).

Schedules are tenant-owned, so defaults exist per workspace: the bootstrap
tenant gets them at startup, and every newly onboarded tenant gets its own set
(right after the invite is redeemed).
"""

import logging

from app.core.tenant_context import tenant_scope
from app.models import Schedule
from app.models.managers.schedule_manager import ScheduleManager
from app.scheduler.cron import next_run_at

logger = logging.getLogger(__name__)

schedules = ScheduleManager()

# name -> (cron, job_type, payload)
DEFAULT_SCHEDULES = {
    "hourly-collect": ("0 * * * *", "collect", {}),
    "daily-prune": ("0 4 * * *", "prune", {"days": 7}),
}


async def ensure_default_schedules(tenant_id: int, timezone: str | None = None) -> int:
    """Create missing default schedules for one workspace. Returns how many were added.

    Must be called inside `tenant_scope(tenant_id)`.
    """
    tz = timezone or await _tenant_timezone(tenant_id)
    created = 0
    for name, (cron_expr, job_type, payload) in DEFAULT_SCHEDULES.items():
        existing = await Schedule.objects.get(name=name)
        if existing:
            continue
        if not schedules.validate_cron(cron_expr):
            logger.error(f"Default schedule {name!r}: invalid cron {cron_expr!r}")
            continue
        await schedules.create(
            name=name,
            cron_expr=cron_expr,
            timezone=tz,
            job_type=job_type,
            payload=payload,
            is_active=True,
            next_run_at=next_run_at(cron_expr, tz),
        )
        created += 1
        logger.info(f"Created default schedule {name!r} ({cron_expr}, {job_type})")
    return created


async def _tenant_timezone(tenant_id: int) -> str:
    from app.models import Tenant

    tenant = await Tenant.objects.get(id=tenant_id)
    return (tenant.timezone if tenant else None) or "Europe/Moscow"


async def ensure_all_default_schedules() -> dict[str, int]:
    """Run the bootstrap for every active workspace (startup path)."""
    from app.models import Tenant

    result: dict[str, int] = {}
    for tenant in await Tenant.objects.filter(is_active=True):
        with tenant_scope(tenant.id):
            result[tenant.slug] = await ensure_default_schedules(tenant.id, tenant.timezone)
    return result
