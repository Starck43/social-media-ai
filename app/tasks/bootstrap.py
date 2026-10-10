"""Bootstrap: create default agent tasks (idempotent).

Agent tasks are tenant-owned, so defaults exist per workspace: the bootstrap
tenant gets them at startup, and every newly onboarded tenant gets its own set
(right after the invite is redeemed).
"""

import logging

from app.core.permissions import service_permission_scope
from app.core.tenant_context import tenant_scope
from app.models import AgentTask
from app.models.managers.agent_task_manager import AgentTaskManager
from app.tasks.cron import next_run_at, resolve_tz

logger = logging.getLogger(__name__)

tasks = AgentTaskManager()

# name -> (cron, job_type, payload)
DEFAULT_TASKS = {
    "hourly-collect": ("0 * * * *", "collect", {}),
    "daily-prune": ("0 4 * * *", "prune", {"days": 7}),
    "daily-analyze": ("0 9 * * *", "analyze", {}),
    # Learning loop: learn fires only after enough new chat turns;
    # reflect keeps memory clean once a week (both cheap when idle).
    "hourly-learn": ("30 * * * *", "learn", {"min_messages": 8}),
    "weekly-reflect": ("0 5 * * 1", "reflect", {}),
}


async def ensure_default_tasks(tenant_id: int) -> int:
    """Create missing default tasks for one workspace. Returns how many were added.

    Must be called inside `tenant_scope(tenant_id)`.
    """
    from app.models import Tenant

    # The workspace zone, not the global default: a default task scheduled in the
    # wrong zone would fire hours off for every non-default workspace, and the
    # runner would then keep re-advancing it in the global zone.
    tenant = await Tenant.objects.get(id=tenant_id)
    tz = resolve_tz(tenant)
    created = 0
    for name, (cron_expr, job_type, payload) in DEFAULT_TASKS.items():
        existing = await AgentTask.objects.get(name=name)
        if existing:
            continue
        if not tasks.validate_cron(cron_expr):
            logger.error(f"Default task {name!r}: invalid cron {cron_expr!r}")
            continue
        with service_permission_scope("agenttask", "create"):
            await tasks.create(
                name=name,
                cron_expr=cron_expr,
                job_type=job_type,
                payload=payload,
                is_active=True,
                next_run_at=next_run_at(cron_expr, tz),
            )
        created += 1
        logger.info(f"Created default task {name!r} ({cron_expr}, {job_type})")
    return created


async def ensure_all_default_tasks() -> dict[str, int]:
    """Run the bootstrap for every active workspace (startup path)."""
    from app.models import Tenant

    result: dict[str, int] = {}
    for tenant in await Tenant.objects.filter(is_active=True):
        with tenant_scope(tenant.id):
            result[tenant.slug] = await ensure_default_tasks(tenant.id)
    return result
