"""Bootstrap: create default schedules on first run (idempotent)."""

import logging

from app.core.config import settings
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


async def ensure_default_schedules() -> None:
    """Create missing default schedules; compute next_run_at for new ones."""
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
            timezone=settings.SCHEDULER_TIMEZONE,
            job_type=job_type,
            payload=payload,
            is_active=True,
            next_run_at=next_run_at(cron_expr, settings.SCHEDULER_TIMEZONE),
        )
        logger.info(f"Created default schedule {name!r} ({cron_expr}, {job_type})")
